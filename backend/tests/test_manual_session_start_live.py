from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Page, Route, expect, sync_playwright
from sqlalchemy import delete, func, select

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import (
    ErrorLog,
    Item,
    MonitorSession,
    Opportunity,
    Run,
    RunEvent,
    RunEventOutbox,
    RunEventPublication,
    SearchSource,
    User,
    UserSession,
    VintedSession,
)
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import create_local_user
from vinted_monitor.services.runs import monitor_policy_hash
from vinted_monitor.services.search_sources import create_source
from vinted_monitor.services.seen_cache import RedisSeenCache, get_seen_cache

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PASSWORD = "manual-session-live-password"
REDIS_LEASE_KEY = "qa:isolated-integration:lease"


@dataclass(frozen=True)
class Scenario:
    token: str
    email: str
    source_id: int
    source_name: str
    failure_source_id: int
    failure_source_name: str
    item_ids: dict[str, str]


def test_live_manual_session_start_baseline_lifecycle() -> None:
    api_url = _loopback_origin("MANUAL_SESSION_QA_API_URL")
    pwa_url = _loopback_origin("MANUAL_SESSION_QA_PWA_URL")
    state_path = _state_path()
    settings = get_settings()
    assert settings.scheduler_enabled is False
    assert settings.vinted_direct_catalog_enabled is True
    assert settings.vinted_datadome_collector_enabled is False
    assert settings.action_requests_enabled is False
    for endpoint in (
        settings.vinted_base_url,
        settings.vinted_datadome_collector_url,
        settings.egress_diagnostic_url,
    ):
        assert urlsplit(str(endpoint)).hostname in LOOPBACK_HOSTS

    cache = get_seen_cache(settings)
    initial_redis_keys = _redis_keys(cache)
    assert initial_redis_keys == {REDIS_LEASE_KEY}
    _assert_isolated_database_empty()
    token = uuid4().hex
    try:
        scenario = _seed(token)
        _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABC"], delay_ms=800)
        _exercise_live_stack(
            scenario,
            cache=cache,
            state_path=state_path,
            api_url=api_url,
            pwa_url=pwa_url,
        )
    finally:
        _cleanup(token, cache)
        assert _redis_keys(cache) == initial_redis_keys
        _assert_isolated_database_empty()


def _exercise_live_stack(
    scenario: Scenario,
    *,
    cache: RedisSeenCache,
    state_path: Path,
    api_url: str,
    pwa_url: str,
) -> None:
    seen_urls: list[str] = []
    blocked_urls: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("MANUAL_SESSION_QA_BROWSER_CHANNEL", "chrome"),
            headless=True,
            args=["--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"],
        )
        context = browser.new_context(base_url=pwa_url, service_workers="block")
        try:
            page = context.new_page()

            def guard(route: Route) -> None:
                seen_urls.append(route.request.url)
                if _local_or_non_network(route.request.url):
                    route.continue_()
                else:
                    blocked_urls.append(route.request.url)
                    route.abort("blockedbyclient")

            page.route("**/*", guard)
            page.on("websocket", lambda socket: _assert_loopback(socket.url))
            _login(page, scenario, pwa_url)
            _select_monitor(page, scenario.source_name, active=False)
            assert page.get_by_role("button", name="Recalibrar listado inicial", exact=True).count() == 0

            first_baseline = _start_session(page, scenario.source_id, assert_busy=True)
            _assert_run(first_baseline, trigger="baseline", status="success", found=3, new=0, opportunities=0)
            first_session_id = _assert_first_baseline_state(scenario, first_baseline, cache)
            _assert_active_manual_ui(page, scenario.source_name)

            same_run = _run_now(page, scenario.source_id, assert_busy=True)
            _assert_run(same_run, trigger="manual", status="success", found=3, new=0, opportunities=0)
            assert same_run["monitor_session_id"] == first_session_id
            _assert_single_opportunity(scenario, expected_item_id=None)

            _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABCD"])
            new_run = _run_now(page, scenario.source_id)
            _assert_run(new_run, trigger="manual", status="success", found=4, new=1, opportunities=1)
            assert new_run["monitor_session_id"] == first_session_id
            _assert_single_opportunity(scenario, expected_item_id=scenario.item_ids["D"])
            opportunity_page = _get_json(
                context,
                f"{api_url}/api/opportunities?source_id={scenario.source_id}",
                pwa_url,
            )
            assert opportunity_page["total"] == 1
            assert opportunity_page["items"][0]["item"]["vinted_item_id"] == scenario.item_ids["D"]

            duplicate_run = _run_now(page, scenario.source_id)
            _assert_run(duplicate_run, trigger="manual", status="success", found=4, new=0, opportunities=0)
            assert duplicate_run["monitor_session_id"] == first_session_id
            _assert_single_opportunity(scenario, expected_item_id=scenario.item_ids["D"])

            marker_key = _baseline_key(scenario.source_id)
            assert cache.client.delete(marker_key) == 1
            missing_marker_run = _run_now(page, scenario.source_id)
            _assert_run(missing_marker_run, trigger="manual", status="failed", found=0, new=0, opportunities=0)
            assert missing_marker_run["monitor_session_id"] == first_session_id
            assert "La foto inicial ya no esta disponible" in (missing_marker_run["error_message"] or "")
            assert "inicia una nueva sesion" in (missing_marker_run["error_message"] or "")
            expect(page.locator(".notice")).to_contain_text("La foto inicial ya no esta disponible")
            expect(page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True)).to_be_visible()
            _assert_closed_after_missing_marker(scenario, first_session_id)

            _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABCDE"])
            second_baseline = _start_session(page, scenario.source_id)
            _assert_run(second_baseline, trigger="baseline", status="success", found=5, new=0, opportunities=0)
            second_session_id = _assert_restart_baseline_state(scenario, first_session_id, cache)
            assert second_baseline["monitor_session_id"] is None
            _assert_active_manual_ui(page, scenario.source_name)

            stop_payload = _stop_session(page, scenario.source_id)
            assert stop_payload["is_active"] is False and stop_payload["next_run_at"] is None
            expect(page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True)).to_be_visible()
            with SessionLocal() as db:
                stopped = db.get(MonitorSession, second_session_id)
                assert stopped is not None and stopped.stopped_at is not None and stopped.stop_reason == "stopped"

            _write_state(state_path, mode="fail", ids=[], delay_ms=800)
            _select_monitor(page, scenario.failure_source_name, active=False)
            failed_baseline = _start_session(page, scenario.failure_source_id, assert_busy=True)
            _assert_run(failed_baseline, trigger="baseline", status="failed", found=0, new=0, opportunities=0)
            assert failed_baseline["monitor_session_id"] is None
            assert "QA catalog provider forced failure" in (failed_baseline["error_message"] or "")
            expect(page.locator(".notice")).to_contain_text("QA catalog provider forced failure")
            expect(
                page.get_by_role("button", name=f"{scenario.failure_source_name}, inactivo", exact=True)
            ).to_be_visible()
            _assert_failed_baseline_state(scenario, failed_baseline)
        finally:
            context.close()
            browser.close()
    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


def _seed(token: str) -> Scenario:
    email = f"qa-manual-session-{token}@example.local"
    source_name = f"qa manual session {token}"
    failure_source_name = f"qa manual failure {token}"
    with SessionLocal() as db:
        create_local_user(db, email=email, password=PASSWORD)
        source = create_source(
            db,
            source_name,
            f"https://www.vinted.es/catalog?search_text=qa-manual-{token}",
        )
        failure_source = create_source(
            db,
            failure_source_name,
            f"https://www.vinted.es/catalog?search_text=qa-manual-failure-{token}",
        )
        return Scenario(
            token=token,
            email=email,
            source_id=source.id,
            source_name=source.name,
            failure_source_id=failure_source.id,
            failure_source_name=failure_source.name,
            item_ids={letter: f"qa-manual-{token}-{letter}" for letter in "ABCDE"},
        )


def _login(page: Page, scenario: Scenario, pwa_url: str) -> None:
    page.goto(pwa_url, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
    page.get_by_label("Email").fill(scenario.email)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
    page.get_by_role("button", name="Monitores", exact=True).click()


def _select_monitor(page: Page, name: str, *, active: bool) -> None:
    status = "activo" if active else "inactivo"
    row = page.get_by_role("button", name=f"{name}, {status}", exact=True)
    expect(row).to_be_visible()
    row.click()
    expect(page.locator(".monitor-detail-content").get_by_text(name, exact=True)).to_be_visible()


def _start_session(page: Page, source_id: int, *, assert_busy: bool = False) -> dict:
    path = f"/api/monitors/{source_id}/start"
    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as info:
        page.get_by_role("button", name="Iniciar sesion", exact=True).click()
        if assert_busy:
            expect(page.get_by_role("button", name="Iniciando...", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="Preparar sesion", exact=True)).to_be_disabled()
    return _response_json(info.value, path)


def _run_now(page: Page, source_id: int, *, assert_busy: bool = False) -> dict:
    path = f"/api/monitors/{source_id}/runs"
    run_button = page.get_by_role("button", name=re.compile(r"^Ejecut(ar ahora|ando\.\.\.)$"))
    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as info:
        run_button.click()
        if assert_busy:
            expect(run_button).to_be_disabled()
            expect(page.get_by_role("button", name="Detener sesion", exact=True)).to_be_disabled()
    return _response_json(info.value, path)


def _stop_session(page: Page, source_id: int) -> dict:
    path = f"/api/monitors/{source_id}/stop"
    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as info:
        page.get_by_role("button", name="Detener sesion", exact=True).click()
    return _response_json(info.value, path)


def _response_json(response, path: str) -> dict:
    assert response.ok, f"POST {path} returned HTTP {response.status}"
    return response.json()


def _assert_run(
    payload: dict,
    *,
    trigger: str,
    status: str,
    found: int,
    new: int,
    opportunities: int,
) -> None:
    assert payload["trigger"] == trigger
    assert payload["status"] == status
    assert payload["items_found"] == found
    assert payload["items_new"] == new
    assert payload["opportunities_created"] == opportunities


def _assert_active_manual_ui(page: Page, source_name: str) -> None:
    expect(page.get_by_role("button", name=f"{source_name}, activo", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Ejecutar ahora", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="Detener sesion", exact=True)).to_be_enabled()
    assert page.get_by_role("button", name="Iniciar sesion", exact=True).count() == 0
    assert page.get_by_role("button", name="Recalibrar listado inicial", exact=True).count() == 0
    config_controls = page.locator(".monitor-config-editor input, .monitor-config-editor select, .monitor-config-editor textarea")
    assert config_controls.count() > 0
    assert all(config_controls.nth(index).is_disabled() for index in range(config_controls.count()))


def _assert_first_baseline_state(scenario: Scenario, run_payload: dict, cache: RedisSeenCache) -> int:
    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.source_id)
        sessions = list(db.scalars(select(MonitorSession).where(MonitorSession.source_id == scenario.source_id)))
        run = db.get(Run, run_payload["id"])
        assert source is not None and source.is_active is True
        assert source.monitor_started_at is not None and source.next_run_at is None
        assert run is not None and run.monitor_session_id is None
        assert len(sessions) == 1 and sessions[0].stopped_at is None
        assert db.scalar(select(func.count()).select_from(Item)) == 0
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 0
        session_id = sessions[0].id
    assert cache.client.exists(_baseline_key(scenario.source_id)) == 1
    for item_id in (scenario.item_ids[key] for key in "ABC"):
        assert cache.client.exists(_seen_key(scenario.source_id, item_id)) == 1
    return session_id


def _assert_single_opportunity(scenario: Scenario, *, expected_item_id: str | None) -> None:
    with SessionLocal() as db:
        opportunities = list(db.scalars(select(Opportunity).where(Opportunity.source_id == scenario.source_id)))
        items = list(db.scalars(select(Item).where(Item.vinted_item_id.like(f"qa-manual-{scenario.token}-%"))))
        if expected_item_id is None:
            assert opportunities == [] and items == []
        else:
            assert len(opportunities) == 1 and len(items) == 1
            assert items[0].vinted_item_id == expected_item_id
            assert opportunities[0].item_id == items[0].id


def _assert_closed_after_missing_marker(scenario: Scenario, session_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.source_id)
        session = db.get(MonitorSession, session_id)
        assert source is not None and source.is_active is False
        assert source.monitor_started_at is None and source.next_run_at is None
        assert session is not None and session.stopped_at is not None
        assert session.stop_reason == "baseline_required"


def _assert_restart_baseline_state(scenario: Scenario, first_session_id: int, cache: RedisSeenCache) -> int:
    with SessionLocal() as db:
        sessions = list(
            db.scalars(
                select(MonitorSession)
                .where(MonitorSession.source_id == scenario.source_id)
                .order_by(MonitorSession.id.asc())
            )
        )
        source = db.get(SearchSource, scenario.source_id)
        assert source is not None and source.is_active is True and source.next_run_at is None
        assert len(sessions) == 2 and sessions[0].id == first_session_id
        assert sessions[0].stopped_at is not None and sessions[1].stopped_at is None
        assert db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == scenario.source_id)) == 1
        assert db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id == scenario.item_ids["E"])) == 0
        second_session_id = sessions[1].id
    assert cache.client.exists(_baseline_key(scenario.source_id)) == 1
    assert cache.client.exists(_seen_key(scenario.source_id, scenario.item_ids["E"])) == 1
    return second_session_id


def _assert_failed_baseline_state(scenario: Scenario, run_payload: dict) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.failure_source_id)
        run = db.get(Run, run_payload["id"])
        assert source is not None and source.is_active is False
        assert source.monitor_started_at is None and source.monitor_until is None and source.next_run_at is None
        assert run is not None and run.status == "failed" and run.monitor_session_id is None
        assert db.scalar(
            select(func.count()).select_from(MonitorSession).where(MonitorSession.source_id == scenario.failure_source_id)
        ) == 0
        assert db.scalar(select(func.count()).select_from(ErrorLog).where(ErrorLog.run_id == run.id)) == 1


def _baseline_key(source_id: int) -> str:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        policy_hash = monitor_policy_hash(source)
    return f"baseline:monitor:{source_id}:policy:{policy_hash}"


def _seen_key(source_id: int, item_id: str) -> str:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        policy_hash = monitor_policy_hash(source)
    return f"seen:monitor:{source_id}:policy:{policy_hash}:item:{item_id}"


def _write_state(path: Path, *, ids: list[str], mode: str = "ok", delay_ms: int = 0) -> None:
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"mode": mode, "ids": ids, "delay_ms": delay_ms}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _cleanup(token: str, cache: RedisSeenCache) -> None:
    with SessionLocal() as db:
        source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.name.like(f"qa manual %{token}"))))
    for source_id in source_ids:
        keys = list(cache.client.scan_iter(match=f"*monitor:{source_id}:*"))
        if keys:
            cache.client.delete(*keys)

    with SessionLocal() as db:
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id.in_(source_ids)))) if source_ids else []
        event_ids = list(db.scalars(select(RunEvent.id).where(RunEvent.source_id.in_(source_ids)))) if source_ids else []
        if event_ids:
            db.execute(delete(RunEventPublication).where(RunEventPublication.event_id.in_(event_ids)))
            db.execute(delete(RunEventOutbox).where(RunEventOutbox.event_id.in_(event_ids)))
            db.execute(delete(RunEvent).where(RunEvent.id.in_(event_ids)))
        if run_ids:
            db.execute(delete(ErrorLog).where(ErrorLog.run_id.in_(run_ids)))
        if source_ids:
            db.execute(delete(ErrorLog).where(ErrorLog.source_id.in_(source_ids)))
            db.execute(delete(Opportunity).where(Opportunity.source_id.in_(source_ids)))
            db.execute(delete(Run).where(Run.id.in_(run_ids)))
            db.execute(delete(VintedSession).where(VintedSession.source_id.in_(source_ids)))
            db.execute(delete(MonitorSession).where(MonitorSession.source_id.in_(source_ids)))
            db.execute(delete(SearchSource).where(SearchSource.id.in_(source_ids)))
        db.execute(delete(Item).where(Item.vinted_item_id.like(f"qa-manual-{token}-%")))
        user_id = db.scalar(select(User.id).where(User.email == f"qa-manual-session-{token}@example.local"))
        db.execute(delete(UserSession))
        if user_id is not None:
            db.execute(delete(User).where(User.id == user_id))
        db.commit()


def _assert_isolated_database_empty() -> None:
    models = (
        User,
        UserSession,
        SearchSource,
        Item,
        Run,
        MonitorSession,
        Opportunity,
        RunEvent,
        RunEventPublication,
        RunEventOutbox,
        ErrorLog,
        VintedSession,
    )
    with SessionLocal() as db:
        assert all(db.scalar(select(func.count()).select_from(model)) == 0 for model in models)


def _redis_keys(cache: RedisSeenCache) -> set[str]:
    return {str(key) for key in cache.client.scan_iter(match="*")}


def _get_json(context: BrowserContext, url: str, origin: str):
    _assert_loopback(url)
    response = context.request.get(url, headers={"Origin": origin})
    assert response.ok, f"GET {urlsplit(url).path} returned HTTP {response.status}"
    return response.json()


def _state_path() -> Path:
    raw = os.getenv("MANUAL_SESSION_QA_PROVIDER_STATE")
    if not raw or not Path(raw).is_absolute():
        pytest.skip("set an absolute MANUAL_SESSION_QA_PROVIDER_STATE through the isolated runner")
    return Path(raw).resolve()


def _loopback_origin(name: str) -> str:
    raw = os.getenv(name)
    if not raw:
        pytest.skip(f"set {name} through the isolated integration runner")
    parsed = urlsplit(raw)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS or parsed.port is None:
        raise ValueError(f"{name} must be an explicit HTTP loopback origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/"):
        raise ValueError(f"{name} must not contain credentials, path, query or fragment")
    return raw.rstrip("/")


def _local_or_non_network(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"data", "blob", "about"} or (
        parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname in LOOPBACK_HOSTS
    )


def _assert_loopback(url: str) -> None:
    assert urlsplit(url).hostname in LOOPBACK_HOSTS, f"non-loopback browser traffic: {url}"
