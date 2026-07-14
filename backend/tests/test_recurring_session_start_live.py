from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Page, Route, expect, sync_playwright
from sqlalchemy import delete, func, select

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.redis_client import redis_client_from_url
from vinted_monitor.db.models import (
    ActionExecution,
    ActionRequest,
    AppSetting,
    CheckoutSnapshot,
    ErrorLog,
    Item,
    MonitorSession,
    Opportunity,
    ProxyProfile,
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
from vinted_monitor.services.scheduler import update_scheduler_config
from vinted_monitor.services.seen_cache import RedisSeenCache, get_seen_cache
from vinted_monitor.services.task_queue import (
    dead_letter_queue_key,
    pending_payload_key,
    pending_task_key,
    pending_tasks,
    processing_queue_key,
    reserve_task,
)
from vinted_monitor.worker.consumer import TaskConsumer
from vinted_monitor.worker.scheduler import SchedulerRunner

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PASSWORD = "recurring-session-live-password"
REDIS_LEASE_KEY = "qa:isolated-integration:lease"


@dataclass(frozen=True)
class Scenario:
    token: str
    email: str
    source_id: int
    source_name: str
    item_ids: dict[str, str]


def test_live_recurring_session_start_baseline_and_real_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_url = _loopback_origin("RECURRING_SESSION_QA_API_URL")
    pwa_url = _loopback_origin("RECURRING_SESSION_QA_PWA_URL")
    state_path = _state_path()
    settings = get_settings()
    assert settings.scheduler_enabled is True
    assert settings.vinted_direct_catalog_enabled is True
    assert settings.vinted_prepared_session_required is False
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
    queue_client = redis_client_from_url(settings.redis_url, decode_responses=False, socket_timeout=3)
    try:
        scenario = _seed(token)
        first_now = datetime.now(UTC)
        runner = SchedulerRunner(settings, rng=random.Random(34))
        assert runner.run_once(now=first_now) == []

        from manual_session_qa_app import ControlledManualSessionProvider

        monkeypatch.setattr(
            "vinted_monitor.services.runs.CurlCffiVintedCatalogProvider",
            ControlledManualSessionProvider,
        )
        _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABCDE"], delay_ms=500)
        _exercise_live_stack(
            scenario,
            api_url=api_url,
            cache=cache,
            pwa_url=pwa_url,
            queue_client=queue_client,
            runner=runner,
            settings=settings,
            state_path=state_path,
        )
    finally:
        _cleanup(token, cache, settings.worker_task_queue_key)
        assert _redis_keys(cache) == initial_redis_keys
        _assert_isolated_database_empty()


def _exercise_live_stack(
    scenario: Scenario,
    *,
    api_url: str,
    cache: RedisSeenCache,
    pwa_url: str,
    queue_client,
    runner: SchedulerRunner,
    settings,
    state_path: Path,
) -> None:
    seen_urls: list[str] = []
    blocked_urls: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("RECURRING_SESSION_QA_BROWSER_CHANNEL", "chrome"),
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
            csrf_token = _login(page, scenario, pwa_url)
            _select_monitor(page, scenario.source_name, active=False)
            assert page.get_by_role("button", name="Recalibrar listado inicial", exact=True).count() == 0
            assert page.get_by_text("Snapshot inicial", exact=False).count() == 0

            baseline_response = _start_session(page, scenario.source_id)
            _assert_run(baseline_response, trigger="baseline", found=5, new=0, opportunities=0)
            session_id, activated_at, first_due = _assert_started_state(scenario, baseline_response)
            assert 60 <= (first_due - activated_at).total_seconds() <= 66
            _assert_queue_empty(queue_client, settings.worker_task_queue_key, scenario.source_id)
            assert runner.run_once(now=activated_at) == []

            expect(page.get_by_role("button", name=f"{scenario.source_name}, activo", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Detener sesion", exact=True)).to_be_enabled()
            assert page.get_by_role("button", name="Ejecutar ahora", exact=True).count() == 0
            assert page.get_by_role("button", name="Recalibrar listado inicial", exact=True).count() == 0

            monitors = _get_json(context, f"{api_url}/api/monitors", pwa_url)
            monitor = next(entry for entry in monitors if entry["id"] == scenario.source_id)
            assert "baseline_ready" not in monitor and "baseline_policy_hash" not in monitor
            removed_route = context.request.post(
                f"{api_url}/api/monitors/{scenario.source_id}/baseline",
                headers={"Origin": pwa_url, "X-CSRF-Token": csrf_token},
            )
            assert removed_route.status == 404

            same_run = _consume_due(
                scenario,
                expected_due=first_due,
                queue_client=queue_client,
                runner=runner,
                settings=settings,
            )
            _assert_run(same_run, trigger="scheduler", found=5, new=0, opportunities=0)
            assert same_run["monitor_session_id"] == session_id

            _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABCDEF"])
            new_run = _consume_next_due(
                scenario,
                queue_client=queue_client,
                runner=runner,
                settings=settings,
            )
            _assert_run(new_run, trigger="scheduler", found=6, new=1, opportunities=1)
            assert new_run["monitor_session_id"] == session_id
            _assert_one_opportunity(scenario)

            duplicate_run = _consume_next_due(
                scenario,
                queue_client=queue_client,
                runner=runner,
                settings=settings,
            )
            _assert_run(duplicate_run, trigger="scheduler", found=6, new=0, opportunities=0)
            assert duplicate_run["monitor_session_id"] == session_id
            _assert_one_opportunity(scenario)

            stop_payload = _stop_session(page, scenario.source_id)
            assert stop_payload["is_active"] is False and stop_payload["next_run_at"] is None
            _assert_complete_run_graph(scenario, session_id)
            _assert_queue_empty(queue_client, settings.worker_task_queue_key, scenario.source_id)
        finally:
            context.close()
            browser.close()
    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


def _consume_next_due(
    scenario: Scenario,
    *,
    queue_client,
    runner: SchedulerRunner,
    settings,
) -> dict:
    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.source_id)
        assert source is not None and source.next_run_at is not None
        due = source.next_run_at
    return _consume_due(
        scenario,
        expected_due=due,
        queue_client=queue_client,
        runner=runner,
        settings=settings,
    )


def _consume_due(
    scenario: Scenario,
    *,
    expected_due: datetime,
    queue_client,
    runner: SchedulerRunner,
    settings,
) -> dict:
    # Fast-forward only the scheduler clock. The initial real heartbeat remains
    # authoritative and valid under the test-only 600-second timeout.
    runner._last_heartbeat_at = expected_due
    assert runner.run_once(now=expected_due) == [scenario.source_id]
    assert runner.run_once(now=expected_due) == []
    queued = pending_tasks(
        queue_client,
        queue_key=settings.worker_task_queue_key,
        processing_keys=(
            processing_queue_key(settings.worker_task_queue_key),
            processing_queue_key(settings.worker_task_queue_key, 0),
        ),
    )
    assert len(queued) == 1 and queued[0].source_id == scenario.source_id
    task = queued[0]
    reservation = reserve_task(
        queue_client,
        timeout=1,
        queue_key=settings.worker_task_queue_key,
        consumer_id=0,
    )
    assert reservation is not None
    TaskConsumer(settings, consumer_id=0)._consume_reservation(
        get_seen_cache(settings),
        reservation,
        queue_client=queue_client,
    )
    _assert_queue_empty(queue_client, settings.worker_task_queue_key, scenario.source_id, reservation.raw_payload)
    with SessionLocal() as db:
        runs = list(db.scalars(select(Run).where(Run.task_id == task.task_id)))
        assert len(runs) == 1
        return _run_payload(runs[0])


def _seed(token: str) -> Scenario:
    settings = get_settings()
    email = f"qa-recurring-session-{token}@example.local"
    source_name = f"qa recurring session {token}"
    with SessionLocal() as db:
        create_local_user(db, email=email, password=PASSWORD)
        update_scheduler_config(
            db,
            {
                "enabled": True,
                "allow_direct_without_proxy": True,
                "direct_max_concurrent_runs": 1,
                "max_concurrent_runs": 1,
            },
            settings,
        )
        source = SearchSource(
            name=source_name,
            url=f"https://www.vinted.es/catalog?search_text=qa-recurring-{token}",
            normalized_query={"search_text": [f"qa-recurring-{token}"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 60, "jitter_percent": 10, "allowed_windows": []},
            filter_definition={"blacklist_terms": []},
        )
        db.add(source)
        db.commit()
        return Scenario(
            token=token,
            email=email,
            source_id=source.id,
            source_name=source.name,
            item_ids={letter: f"qa-recurring-{token}-{letter}" for letter in "ABCDEF"},
        )


def _login(page: Page, scenario: Scenario, pwa_url: str) -> str:
    page.goto(pwa_url, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
    page.get_by_label("Email").fill(scenario.email)
    page.get_by_label("Password").fill(PASSWORD)
    with page.expect_response(lambda response: urlsplit(response.url).path == "/api/auth/login") as info:
        page.get_by_role("button", name="Entrar").click()
    payload = info.value.json()
    expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
    page.get_by_role("button", name="Monitores", exact=True).click()
    return str(payload["csrf_token"])


def _select_monitor(page: Page, name: str, *, active: bool) -> None:
    status = "activo" if active else "inactivo"
    row = page.get_by_role("button", name=f"{name}, {status}", exact=True)
    expect(row).to_be_visible()
    row.click()


def _start_session(page: Page, source_id: int) -> dict:
    path = f"/api/monitors/{source_id}/start"
    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as info:
        page.get_by_role("button", name="Iniciar sesion", exact=True).click()
        expect(page.get_by_role("button", name="Iniciando...", exact=True)).to_be_disabled()
    assert info.value.ok, f"POST {path} returned HTTP {info.value.status}"
    return info.value.json()


def _stop_session(page: Page, source_id: int) -> dict:
    path = f"/api/monitors/{source_id}/stop"
    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as info:
        page.get_by_role("button", name="Detener sesion", exact=True).click()
    assert info.value.ok, f"POST {path} returned HTTP {info.value.status}"
    return info.value.json()


def _assert_started_state(scenario: Scenario, baseline: dict) -> tuple[int, datetime, datetime]:
    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.source_id)
        sessions = list(db.scalars(select(MonitorSession).where(MonitorSession.source_id == scenario.source_id)))
        runs = list(db.scalars(select(Run).where(Run.source_id == scenario.source_id)))
        run = db.get(Run, baseline["id"])
        assert source is not None and source.is_active is True
        assert source.monitor_started_at is not None and source.next_run_at is not None
        assert run is not None and run.monitor_session_id is None
        assert run.finished_at is not None and source.monitor_started_at > run.finished_at
        assert len(runs) == 1 and runs[0].id == run.id
        assert len(sessions) == 1 and sessions[0].stopped_at is None
        assert db.scalar(select(func.count()).select_from(Item)) == 0
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 0
        return sessions[0].id, source.monitor_started_at, source.next_run_at


def _assert_complete_run_graph(scenario: Scenario, session_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.source_id)
        session = db.get(MonitorSession, session_id)
        runs = list(db.scalars(select(Run).where(Run.source_id == scenario.source_id).order_by(Run.id.asc())))
        assert source is not None and source.is_active is False
        assert session is not None and session.stopped_at is not None and session.stop_reason == "stopped"
        assert [run.trigger for run in runs] == ["baseline", "scheduler", "scheduler", "scheduler"]
        assert all(run.status == "success" for run in runs)
        assert runs[0].monitor_session_id is None
        assert {run.monitor_session_id for run in runs[1:]} == {session_id}


def _assert_one_opportunity(scenario: Scenario) -> None:
    with SessionLocal() as db:
        opportunities = list(db.scalars(select(Opportunity).where(Opportunity.source_id == scenario.source_id)))
        items = list(db.scalars(select(Item).where(Item.vinted_item_id.like(f"qa-recurring-{scenario.token}-%"))))
        assert len(opportunities) == 1 and len(items) == 1
        assert items[0].vinted_item_id == scenario.item_ids["F"]
        assert opportunities[0].item_id == items[0].id


def _assert_run(payload: dict, *, trigger: str, found: int, new: int, opportunities: int) -> None:
    assert payload["trigger"] == trigger, payload
    assert payload["status"] == "success", payload
    assert payload["items_found"] == found, payload
    assert payload["items_new"] == new, payload
    assert payload["opportunities_created"] == opportunities, payload


def _run_payload(run: Run) -> dict:
    return {
        "id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "items_found": run.items_found,
        "items_new": run.items_new,
        "items_filter_passed": run.items_filter_passed,
        "items_discarded_by_filters": run.items_discarded_by_filters,
        "items_filter_pending": run.items_filter_pending,
        "opportunities_created": run.opportunities_created,
        "monitor_session_id": run.monitor_session_id,
    }


def _assert_queue_empty(queue_client, queue_key: str, source_id: int, raw_payload=None) -> None:
    assert queue_client.llen(queue_key) == 0
    assert queue_client.llen(processing_queue_key(queue_key)) == 0
    assert queue_client.llen(processing_queue_key(queue_key, 0)) == 0
    assert queue_client.llen(dead_letter_queue_key(queue_key)) == 0
    assert queue_client.get(pending_task_key(source_id, queue_key)) is None
    if raw_payload is not None:
        assert queue_client.get(pending_payload_key(raw_payload, queue_key)) is None


def _write_state(path: Path, *, ids: list[str], delay_ms: int = 0) -> None:
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"mode": "ok", "ids": ids, "delay_ms": delay_ms}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _cleanup(token: str, cache: RedisSeenCache, queue_key: str) -> None:
    with SessionLocal() as db:
        source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.name.like(f"qa recurring %{token}"))))
    for source_id in source_ids:
        keys = list(cache.client.scan_iter(match=f"*monitor:{source_id}:*"))
        if keys:
            cache.client.delete(*keys)
    queue_keys = list(cache.client.scan_iter(match=f"{queue_key}*"))
    if queue_keys:
        cache.client.delete(*queue_keys)

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
        db.execute(delete(Item).where(Item.vinted_item_id.like(f"qa-recurring-{token}-%")))
        db.execute(delete(UserSession))
        db.execute(delete(User).where(User.email == f"qa-recurring-session-{token}@example.local"))
        db.execute(delete(AppSetting))
        db.commit()


def _assert_isolated_database_empty() -> None:
    models = (
        User,
        UserSession,
        SearchSource,
        AppSetting,
        Item,
        Run,
        MonitorSession,
        ProxyProfile,
        VintedSession,
        Opportunity,
        RunEvent,
        RunEventPublication,
        RunEventOutbox,
        ActionRequest,
        ActionExecution,
        CheckoutSnapshot,
        ErrorLog,
    )
    with SessionLocal() as db:
        assert all(db.scalar(select(func.count()).select_from(model)) == 0 for model in models)


def _get_json(context: BrowserContext, url: str, origin: str):
    _assert_loopback(url)
    response = context.request.get(url, headers={"Origin": origin})
    assert response.ok, f"GET {urlsplit(url).path} returned HTTP {response.status}"
    return response.json()


def _state_path() -> Path:
    raw = os.getenv("SESSION_QA_PROVIDER_STATE")
    if not raw or not Path(raw).is_absolute():
        pytest.skip("set an absolute SESSION_QA_PROVIDER_STATE through the isolated runner")
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


def _redis_keys(cache: RedisSeenCache) -> set[str]:
    return {str(key) for key in cache.client.scan_iter(match="*")}


def _local_or_non_network(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"data", "blob", "about"} or (
        parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname in LOOPBACK_HOSTS
    )


def _assert_loopback(url: str) -> None:
    assert urlsplit(url).hostname in LOOPBACK_HOSTS, f"non-loopback browser traffic: {url}"
