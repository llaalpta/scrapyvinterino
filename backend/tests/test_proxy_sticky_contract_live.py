from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Route, expect, sync_playwright
from sqlalchemy import delete, func, select

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import (
    ErrorLog,
    MonitorSession,
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
from vinted_monitor.services.proxies import effective_proxy_identity_generation
from vinted_monitor.services.search_sources import create_source
from vinted_monitor.services.seen_cache import get_seen_cache

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PASSWORD = "proxy-sticky-contract-password"


@dataclass(frozen=True)
class Scenario:
    token: str
    email: str
    proxy_name: str
    source_id: int
    source_name: str


def test_live_proxy_sticky_contract_edit_invalidates_and_rotates_context() -> None:
    api_url = _loopback_origin("PROXY_STICKY_QA_API_URL")
    pwa_url = _loopback_origin("PROXY_STICKY_QA_PWA_URL")
    settings = get_settings()
    assert settings.scheduler_enabled is False
    for endpoint in (
        settings.vinted_base_url,
        settings.vinted_datadome_collector_url,
        settings.egress_diagnostic_url,
    ):
        assert urlsplit(str(endpoint)).hostname in LOOPBACK_HOSTS

    scenario = _seed()
    try:
        _exercise_live_stack(scenario, api_url=api_url, pwa_url=pwa_url)
    finally:
        _cleanup(scenario)


def _seed() -> Scenario:
    token = uuid4().hex
    email = f"qa-proxy-sticky-{token}@example.local"
    source_name = f"qa proxy sticky monitor {token}"
    with SessionLocal() as db:
        create_local_user(db, email=email, password=PASSWORD)
        source = create_source(
            db,
            source_name,
            f"https://www.vinted.es/catalog?search_text=qa-sticky-{token}",
        )
        source.monitor_mode = "manual"
        source.scheduler_config = {}
        db.commit()
        return Scenario(
            token=token,
            email=email,
            proxy_name=f"qa proxy sticky {token}",
            source_id=source.id,
            source_name=source.name,
        )


def _exercise_live_stack(
    scenario: Scenario,
    *,
    api_url: str,
    pwa_url: str,
) -> None:
    seen_urls: list[str] = []
    blocked_urls: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("PROXY_STICKY_QA_BROWSER_CHANNEL", "chrome"),
            headless=True,
            args=[
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--no-first-run",
            ],
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

            page.get_by_role("button", name="Ajustes", exact=True).click()
            create_path = "/api/proxy-profiles"
            page.get_by_label("Nombre", exact=True).fill(scenario.proxy_name)
            page.locator(".proxy-form select").first.select_option("residential")
            page.get_by_label("Host", exact=True).fill("proxy.invalid")
            page.get_by_label("Puerto", exact=True).fill("8080")
            page.get_by_label("Usuario", exact=True).fill(f"qa-{scenario.token}")
            page.get_by_label("Password", exact=True).fill("qa-proxy-password")
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and urlsplit(response.url).path == create_path
            ) as create_info:
                page.get_by_role("button", name="Guardar proxy", exact=True).click()
            assert create_info.value.status == 201
            proxy_payload = create_info.value.json()
            assert proxy_payload["sticky_username_template"] == "{username};sessid.{session_id}"
            assert proxy_payload["sticky_ttl_minutes"] == 25
            proxy_id = proxy_payload["id"]
            expect(page.get_by_text(scenario.proxy_name, exact=True)).to_be_visible()

            page.get_by_role("button", name="Monitores", exact=True).click()
            _select_monitor(page, scenario.source_name, active=False)
            first_run = _post_monitor_action(
                page,
                path=f"/api/monitors/{scenario.source_id}/start",
                button_name="Iniciar sesion",
            )
            assert first_run["status"] == "success"
            original = _assert_first_context(scenario, proxy_id)

            page.get_by_role("button", name="Ajustes", exact=True).click()
            proxy_row = page.locator("article.proxy-row").filter(has_text=scenario.proxy_name)
            proxy_row.get_by_label(f"Plantilla sticky de {scenario.proxy_name}", exact=True).fill(
                "{username}-qa-{session_id}"
            )
            proxy_row.get_by_label(f"TTL sticky de {scenario.proxy_name}", exact=True).fill("7")
            with page.expect_response(
                lambda response: response.request.method == "PATCH"
                and urlsplit(response.url).path == f"/api/proxy-profiles/{proxy_id}"
            ) as edit_info:
                proxy_row.get_by_role("button", name="Guardar sticky", exact=True).click()
            assert edit_info.value.status == 200
            edited = edit_info.value.json()
            assert edited["sticky_username_template"] == "{username}-qa-{session_id}"
            assert edited["sticky_ttl_minutes"] == 7
            _assert_context_invalidated(scenario, proxy_id, original)

            page.get_by_role("button", name="Monitores", exact=True).click()
            _select_monitor(page, scenario.source_name, active=True)
            second_run = _post_monitor_action(
                page,
                path=f"/api/monitors/{scenario.source_id}/runs",
                button_name="Ejecutar ahora",
            )
            assert second_run["status"] == "success"
            _assert_replacement_context(scenario, proxy_id, original, second_run["id"])

            before_rejection = _sticky_state(scenario, proxy_id)
            page.get_by_role("button", name="Ajustes", exact=True).click()
            proxy_row = page.locator("article.proxy-row").filter(has_text=scenario.proxy_name)
            proxy_row.get_by_label(f"Plantilla sticky de {scenario.proxy_name}", exact=True).fill(
                "{username}"
            )
            with page.expect_response(
                lambda response: response.request.method == "PATCH"
                and urlsplit(response.url).path == f"/api/proxy-profiles/{proxy_id}"
            ) as rejection_info:
                proxy_row.get_by_role("button", name="Guardar sticky", exact=True).click()
            assert rejection_info.value.status == 422
            expect(page.locator(".notice")).to_contain_text("must contain exactly")
            assert _sticky_state(scenario, proxy_id) == before_rejection

            page.get_by_role("button", name="Monitores", exact=True).click()
            _select_monitor(page, scenario.source_name, active=True)
            stopped = _post_monitor_action(
                page,
                path=f"/api/monitors/{scenario.source_id}/stop",
                button_name="Detener sesion",
            )
            assert stopped["is_active"] is False

            proxies = _get_json(context, f"{api_url}/api/proxy-profiles", pwa_url)
            current = next(profile for profile in proxies if profile["id"] == proxy_id)
            assert current["sticky_username_template"] == "{username}-qa-{session_id}"
            assert current["sticky_ttl_minutes"] == 7
            assert "password" not in current
        finally:
            context.close()
            browser.close()

    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


def _assert_first_context(
    scenario: Scenario,
    proxy_id: int,
) -> tuple[int, int, str, int]:
    with SessionLocal() as db:
        profile = db.get(ProxyProfile, proxy_id)
        source = db.get(SearchSource, scenario.source_id)
        session = db.scalar(
            select(VintedSession).where(
                VintedSession.source_id == scenario.source_id,
                VintedSession.proxy_profile_id == proxy_id,
            )
        )
        monitor_session = db.scalar(
            select(MonitorSession).where(
                MonitorSession.source_id == scenario.source_id,
                MonitorSession.stopped_at.is_(None),
            )
        )
        assert profile is not None and source is not None
        assert session is not None and monitor_session is not None
        assert source.is_active is True and session.status == "ready"
        assert session.expires_at is not None
        assert (session.expires_at - session.prepared_at).total_seconds() == pytest.approx(
            25 * 60,
            abs=1,
        )
        return (
            session.id,
            profile.identity_generation,
            effective_proxy_identity_generation(profile),
            monitor_session.id,
        )


def _assert_context_invalidated(
    scenario: Scenario,
    proxy_id: int,
    original: tuple[int, int, str, int],
) -> None:
    session_id, generation, identity, monitor_session_id = original
    with SessionLocal() as db:
        profile = db.get(ProxyProfile, proxy_id)
        session = db.get(VintedSession, session_id)
        monitor_session = db.get(MonitorSession, monitor_session_id)
        source = db.get(SearchSource, scenario.source_id)
        assert profile is not None and session is not None
        assert profile.identity_generation == generation + 1
        assert effective_proxy_identity_generation(profile) != identity
        assert profile.sticky_username_template == "{username}-qa-{session_id}"
        assert profile.sticky_ttl_minutes == 7
        assert session.status == "invalid" and session.invalidated_at is not None
        assert monitor_session is not None and monitor_session.stopped_at is None
        assert source is not None and source.is_active is True


def _assert_replacement_context(
    scenario: Scenario,
    proxy_id: int,
    original: tuple[int, int, str, int],
    run_id: int,
) -> None:
    original_session_id, _generation, _identity, monitor_session_id = original
    with SessionLocal() as db:
        sessions = list(
            db.scalars(
                select(VintedSession)
                .where(
                    VintedSession.source_id == scenario.source_id,
                    VintedSession.proxy_profile_id == proxy_id,
                )
                .order_by(VintedSession.id.asc())
            )
        )
        run = db.get(Run, run_id)
        monitor_session = db.get(MonitorSession, monitor_session_id)
        assert len(sessions) == 2
        assert sessions[0].id == original_session_id and sessions[0].status == "invalid"
        assert sessions[1].status == "ready" and sessions[1].request_count == 1
        assert sessions[1].expires_at is not None
        assert (sessions[1].expires_at - sessions[1].prepared_at).total_seconds() == pytest.approx(
            7 * 60,
            abs=1,
        )
        assert run is not None and run.monitor_session_id == monitor_session_id
        assert run.runtime_metadata["vinted_session_action"] == "prepared"
        assert monitor_session is not None and monitor_session.stopped_at is None


def _sticky_state(scenario: Scenario, proxy_id: int) -> tuple[object, ...]:
    with SessionLocal() as db:
        profile = db.get(ProxyProfile, proxy_id)
        assert profile is not None
        return (
            profile.sticky_username_template,
            profile.sticky_ttl_minutes,
            profile.identity_generation,
            profile.identity_fingerprint,
            db.scalar(
                select(func.count())
                .select_from(VintedSession)
                .where(VintedSession.source_id == scenario.source_id)
            ),
            db.scalar(
                select(func.count()).select_from(Run).where(Run.source_id == scenario.source_id)
            ),
        )


def _login(page, scenario: Scenario, pwa_url: str) -> None:
    page.goto(pwa_url, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
    page.get_by_label("Email").fill(scenario.email)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()


def _select_monitor(page, name: str, *, active: bool) -> None:
    status = "activo" if active else "inactivo"
    page.get_by_role("button", name=f"{name}, {status}", exact=True).click()
    expect(page.locator(".monitor-detail-content").get_by_role("heading", name=name, exact=True)).to_be_visible()


def _post_monitor_action(page, *, path: str, button_name: str) -> dict:
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlsplit(response.url).path == path
    ) as info:
        page.get_by_role("button", name=button_name, exact=True).click()
    assert info.value.ok, f"POST {path} returned HTTP {info.value.status}"
    return info.value.json()


def _get_json(context: BrowserContext, url: str, origin: str):
    _assert_loopback(url)
    response = context.request.get(url, headers={"Origin": origin})
    assert response.ok, f"GET {urlsplit(url).path} returned HTTP {response.status}"
    return response.json()


def _cleanup(scenario: Scenario) -> None:
    cache = get_seen_cache()
    keys = list(cache.client.scan_iter(match=f"*monitor:{scenario.source_id}:*"))
    if keys:
        cache.client.delete(*keys)

    with SessionLocal() as db:
        run_ids = list(
            db.scalars(select(Run.id).where(Run.source_id == scenario.source_id))
        )
        event_ids = list(
            db.scalars(select(RunEvent.id).where(RunEvent.source_id == scenario.source_id))
        )
        if event_ids:
            db.execute(
                delete(RunEventPublication).where(
                    RunEventPublication.event_id.in_(event_ids)
                )
            )
            db.execute(
                delete(RunEventOutbox).where(RunEventOutbox.event_id.in_(event_ids))
            )
            db.execute(delete(RunEvent).where(RunEvent.id.in_(event_ids)))
        if run_ids:
            db.execute(delete(ErrorLog).where(ErrorLog.run_id.in_(run_ids)))
        db.execute(delete(ErrorLog).where(ErrorLog.source_id == scenario.source_id))
        db.execute(delete(Run).where(Run.source_id == scenario.source_id))
        db.execute(
            delete(VintedSession).where(VintedSession.source_id == scenario.source_id)
        )
        db.execute(
            delete(MonitorSession).where(MonitorSession.source_id == scenario.source_id)
        )
        db.execute(delete(SearchSource).where(SearchSource.id == scenario.source_id))
        db.execute(delete(ProxyProfile).where(ProxyProfile.name == scenario.proxy_name))
        user_id = db.scalar(select(User.id).where(User.email == scenario.email))
        if user_id is not None:
            db.execute(delete(UserSession).where(UserSession.user_id == user_id))
            db.execute(delete(User).where(User.id == user_id))
        db.commit()


def _loopback_origin(name: str) -> str:
    value = os.getenv(name, "").rstrip("/")
    if not value:
        pytest.skip(f"set {name} through the isolated runner")
    _assert_loopback(value)
    return value


def _assert_loopback(url: str) -> None:
    parsed = urlsplit(url)
    assert parsed.scheme in {"http", "https", "ws", "wss"}
    assert parsed.hostname in LOOPBACK_HOSTS


def _local_or_non_network(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"data", "blob"} or parsed.hostname in LOOPBACK_HOSTS
