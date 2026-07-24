from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import Route, expect, sync_playwright
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
from vinted_monitor.services.proxies import create_proxy_profile
from vinted_monitor.services.runs import monitor_policy_hash
from vinted_monitor.services.scheduler_liveness import touch_scheduler_worker_heartbeat
from vinted_monitor.services.search_sources import create_source
from vinted_monitor.services.seen_cache import get_seen_cache

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]
PASSWORD = "same-profile-recovery-password"


@dataclass(frozen=True)
class Scenario:
    token: str
    email: str
    user_id: int
    proxy_id: int
    proxy_name: str
    fallback_proxy_id: int
    fallback_proxy_name: str
    success_source_id: int
    success_source_name: str
    repeated_source_id: int
    repeated_source_name: str


def test_live_pwa_same_profile_recovery_and_repeated_egress_rejection() -> None:
    api_url = _loopback_origin("SAME_PROFILE_QA_API_URL")
    pwa_url = _loopback_origin("SAME_PROFILE_QA_PWA_URL")
    state_path = _state_path()
    settings = get_settings()
    assert settings.scheduler_enabled is True
    assert settings.vinted_datadome_collector_enabled is False
    assert settings.vinted_auth_enabled is False
    assert settings.action_requests_enabled is False
    for endpoint in (
        settings.vinted_base_url,
        settings.vinted_datadome_collector_url,
        settings.egress_diagnostic_url,
    ):
        _assert_loopback(str(endpoint))

    with _loopback_proxy(state_path) as proxy_port:
        scenario = _seed(proxy_port)
        try:
            _exercise_live_stack(
                scenario,
                api_url=api_url,
                pwa_url=pwa_url,
                state_path=state_path,
            )
        finally:
            _cleanup(scenario)


def _seed(proxy_port: int) -> Scenario:
    token = uuid4().hex
    settings = get_settings()
    with SessionLocal() as db:
        user = create_local_user(
            db,
            email=f"qa-same-profile-{token}@example.local",
            password=PASSWORD,
        )
        proxy = create_proxy_profile(
            db,
            name=f"qa same-profile proxy {token}",
            scheme="http",
            kind="residential",
            host="127.0.0.1",
            port=proxy_port,
            username=f"qa-user-{token}",
            password=f"qa-password-{token}",
            country_code="ES",
            settings=settings,
        )
        fallback_proxy = create_proxy_profile(
            db,
            name=f"qa same-profile fallback {token}",
            scheme="http",
            kind="residential",
            host="127.0.0.1",
            port=proxy_port,
            username=f"qa-fallback-user-{token}",
            password=f"qa-fallback-password-{token}",
            country_code="ES",
            settings=settings,
        )
        success_source = create_source(
            db,
            f"qa same-profile success {token}",
            f"https://www.vinted.es/catalog?search_text=qa-recovery-{token}&order=newest_first",
        )
        success_source.monitor_mode = "manual"
        success_source.scheduler_config = {}
        repeated_source = create_source(
            db,
            f"qa same-profile repeated {token}",
            f"https://www.vinted.es/catalog?search_text=qa-repeated-{token}&order=newest_first",
        )
        repeated_source.monitor_mode = "continuous"
        repeated_source.scheduler_config = {
            "interval_seconds": 60,
            "jitter_percent": 0,
            "allowed_windows": [],
        }
        touch_scheduler_worker_heartbeat(db)
        db.commit()
        return Scenario(
            token=token,
            email=user.email,
            user_id=user.id,
            proxy_id=proxy.id,
            proxy_name=proxy.name,
            fallback_proxy_id=fallback_proxy.id,
            fallback_proxy_name=fallback_proxy.name,
            success_source_id=success_source.id,
            success_source_name=success_source.name,
            repeated_source_id=repeated_source.id,
            repeated_source_name=repeated_source.name,
        )


def _exercise_live_stack(
    scenario: Scenario,
    *,
    api_url: str,
    pwa_url: str,
    state_path: Path,
) -> None:
    seen_urls: list[str] = []
    blocked_urls: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("SAME_PROFILE_QA_BROWSER_CHANNEL", "chrome"),
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
            page.get_by_role("button", name="Monitores", exact=True).click()

            _reset_state(state_path, mode="different_ip", item_id=9900000101)
            _select_monitor(page, scenario.success_source_name, active=False)
            success = _post_monitor_action(
                page,
                path=f"/api/monitors/{scenario.success_source_id}/start",
                button_name="Iniciar sesion",
            )
            assert success["status"] == "success"
            expect(page.locator(".monitor-detail-content").get_by_text("Activo", exact=True)).to_be_visible()
            _assert_success_state(scenario, state_path)

            stopped = _post_monitor_action(
                page,
                path=f"/api/monitors/{scenario.success_source_id}/stop",
                button_name="Detener sesion",
            )
            assert stopped["is_active"] is False

            _reset_state(state_path, mode="same_ip", item_id=9900000102)
            _select_monitor(page, scenario.repeated_source_name, active=False)
            failed = _post_monitor_action(
                page,
                path=f"/api/monitors/{scenario.repeated_source_id}/start",
                button_name="Iniciar sesion",
            )
            assert failed["status"] == "failed"
            assert (failed["runtime_metadata"] or {})["failure_kind"] == (
                "session_acquisition_exhausted"
            )
            expect(page.locator(".notice")).to_contain_text(
                "No eligible proxy profile could obtain a usable Vinted session"
            )
            expect(page.get_by_text("Cooldown de proxy activo.", exact=False)).to_be_visible()
            _assert_repeated_egress_state(scenario, state_path)
            retry_selector = page.get_by_label("Perfil proxy para el reintento")
            expect(retry_selector).to_be_visible()
            expect(retry_selector.locator("option")).to_have_count(2)
            expect(page.get_by_role("button", name="Iniciar sesion", exact=True)).to_be_disabled()
            scheduler_response = context.request.get(f"{api_url}/api/scheduler")
            assert scheduler_response.ok
            assert scheduler_response.json()["runtime_enabled"] is True
            assert scheduler_response.json()["worker_available"] is True
            assert scheduler_response.json()["effective_enabled"] is False

            profiles_response = context.request.get(
                f"{api_url}/api/proxy-profiles",
                headers={"Origin": pwa_url},
            )
            assert profiles_response.ok
            profile = next(
                row for row in profiles_response.json() if row["id"] == scenario.proxy_id
            )
            assert profile["failure_count"] == 1
            assert profile["cooldown_until"] is not None
            assert "password" not in profile
            fallback_profile = next(
                row for row in profiles_response.json() if row["id"] == scenario.fallback_proxy_id
            )
            assert fallback_profile["failure_count"] == 1
            assert fallback_profile["cooldown_until"] is not None

            state_before_rejection = _read_state(state_path)
            auth_session = context.request.get(
                f"{api_url}/api/auth/session",
                headers={"Origin": pwa_url},
            )
            assert auth_session.ok
            malformed_retry = context.request.post(
                f"{api_url}/api/monitors/{scenario.repeated_source_id}/vinted-session/retry",
                data={"proxy_profile_id": True},
                headers={
                    "Origin": pwa_url,
                    "X-CSRF-Token": auth_session.json()["csrf_token"],
                },
            )
            assert malformed_retry.status == 422
            invalid_retry = context.request.post(
                f"{api_url}/api/monitors/{scenario.repeated_source_id}/vinted-session/retry",
                data={"proxy_profile_id": 999_999_999},
                headers={
                    "Origin": pwa_url,
                    "X-CSRF-Token": auth_session.json()["csrf_token"],
                },
            )
            assert invalid_retry.status == 409
            assert _read_state(state_path) == state_before_rejection
            _assert_invalid_retry_unchanged(scenario)

            _reset_state(state_path, mode="different_ip", item_id=9900000103)
            retry_selector.select_option(str(scenario.proxy_id))
            retried = _post_monitor_action(
                page,
                path=f"/api/monitors/{scenario.repeated_source_id}/vinted-session/retry",
                button_name="Reintentar sesion",
            )
            assert retried["status"] == "success"
            expect(page.locator(".monitor-detail-content").get_by_text("Activo", exact=True)).to_be_visible()
            _assert_explicit_retry_success_state(scenario, failed, retried, state_path)
        finally:
            context.close()
            browser.close()

    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


def _assert_success_state(scenario: Scenario, state_path: Path) -> None:
    state = _read_state(state_path)
    assert state["egress_probe_calls"] == 1
    assert state["proxy_errors"] == []
    assert state["constructions"] == [
        "initial_preparation",
        "forced_preparation",
        "execution",
    ]
    assert state["bootstrap_calls"] == 2
    assert state["catalog_probe_calls"] == 1

    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.success_source_id)
        runs = list(db.scalars(select(Run).where(Run.source_id == scenario.success_source_id)))
        sessions = list(
            db.scalars(
                select(VintedSession).where(
                    VintedSession.source_id == scenario.success_source_id
                )
            )
        )
        monitor_sessions = list(
            db.scalars(
                select(MonitorSession).where(
                    MonitorSession.source_id == scenario.success_source_id
                )
            )
        )
        assert source is not None and source.is_active is True
        assert len(runs) == 1 and runs[0].status == "success"
        assert (runs[0].runtime_metadata or {})["session_acquisition_attempts"] == 2
        assert (runs[0].runtime_metadata or {})["session_acquisition_egress_changed"] is True
        assert len(sessions) == 1
        assert sessions[0].status == "ready" and sessions[0].egress_ip == "192.0.2.20"
        assert len(monitor_sessions) == 1 and monitor_sessions[0].stopped_at is None
        phases = list(
            db.scalars(select(RunEvent.phase).where(RunEvent.run_id == runs[0].id))
        )
        assert phases.count("session_acquisition_attempt_started") == 2
        assert phases.count("session_acquisition_attempt_failed") == 1
        assert phases.count("session_acquisition_attempt_succeeded") == 1
        assert phases.count("egress_diagnostic_success") == 1
        attempt_details = list(
            db.scalars(
                select(RunEvent.details).where(
                    RunEvent.run_id == runs[0].id,
                    RunEvent.phase.like("session_acquisition_attempt_%"),
                )
            )
        )
        serialized = json.dumps(attempt_details)
        assert "192.0.2." not in serialized
        assert "qa-password" not in serialized
        assert "sessid" not in serialized
        policy_hash = monitor_policy_hash(source)

    cache = get_seen_cache()
    assert cache.has_baseline(scenario.success_source_id, policy_hash) is True


def _assert_repeated_egress_state(scenario: Scenario, state_path: Path) -> None:
    state = _read_state(state_path)
    assert state["egress_probe_calls"] == 2
    assert state["proxy_errors"] == []
    assert state["constructions"] == ["initial_preparation", "initial_preparation"]
    assert state["bootstrap_calls"] == 2
    assert state["catalog_probe_calls"] == 0

    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.repeated_source_id)
        runs = list(db.scalars(select(Run).where(Run.source_id == scenario.repeated_source_id)))
        profile = db.get(ProxyProfile, scenario.proxy_id)
        fallback_profile = db.get(ProxyProfile, scenario.fallback_proxy_id)
        assert source is not None and source.is_active is False
        assert len(runs) == 1 and runs[0].status == "failed"
        metadata = runs[0].runtime_metadata or {}
        assert metadata["failure_kind"] == "session_acquisition_exhausted"
        assert metadata["session_acquisition_attempts"] == 2
        assert metadata["session_acquisition_last_reason"] == "egress_not_rotated"
        assert metadata["session_acquisition_egress_changed"] is False
        assert set(metadata["session_acquisition_profile_ids"]) == {
            scenario.proxy_id,
            scenario.fallback_proxy_id,
        }
        assert set(metadata["session_acquisition_rejected_egress_fingerprints"]) == {
            str(scenario.proxy_id),
            str(scenario.fallback_proxy_id),
        }
        assert "192.0.2." not in json.dumps(metadata)
        assert profile is not None and fallback_profile is not None
        assert profile.failure_count == 1 and profile.cooldown_until is not None
        assert fallback_profile.failure_count == 1 and fallback_profile.cooldown_until is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(VintedSession)
                .where(VintedSession.source_id == scenario.repeated_source_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(MonitorSession)
                .where(MonitorSession.source_id == scenario.repeated_source_id)
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(ErrorLog)
                .where(ErrorLog.run_id == runs[0].id)
            )
            == 1
        )
        phases = list(
            db.scalars(select(RunEvent.phase).where(RunEvent.run_id == runs[0].id))
        )
        assert phases.count("session_acquisition_attempt_started") == 4
        assert phases.count("session_acquisition_attempt_failed") == 4
        assert phases.count("proxy_profile_handoff_committed") == 1
        assert phases.count("run_failed") == 1
        assert "catalog_search_start" not in phases

    cache = get_seen_cache()
    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.repeated_source_id)
        assert source is not None
        policy_hash = monitor_policy_hash(source)
    assert cache.has_baseline(scenario.repeated_source_id, policy_hash) is False


def _assert_invalid_retry_unchanged(scenario: Scenario) -> None:
    with SessionLocal() as db:
        runs = list(db.scalars(select(Run).where(Run.source_id == scenario.repeated_source_id)))
        profiles = {
            profile.id: profile
            for profile in db.scalars(
                select(ProxyProfile).where(
                    ProxyProfile.id.in_((scenario.proxy_id, scenario.fallback_proxy_id))
                )
            )
        }
        assert len(runs) == 1
        assert all(profile.failure_count == 1 for profile in profiles.values())
        assert all(profile.cooldown_until is not None for profile in profiles.values())


def _assert_explicit_retry_success_state(
    scenario: Scenario,
    failed_run: dict[str, Any],
    retried_run: dict[str, Any],
    state_path: Path,
) -> None:
    state = _read_state(state_path)
    assert state["egress_probe_calls"] == 1
    assert state["proxy_errors"] == []
    assert state["constructions"] == ["forced_preparation", "execution"]
    assert state["bootstrap_calls"] == 1
    assert state["catalog_probe_calls"] == 1
    assert state["closes"] == 2

    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.repeated_source_id)
        retry = db.get(Run, retried_run["id"])
        selected_profile = db.get(ProxyProfile, scenario.proxy_id)
        fallback_profile = db.get(ProxyProfile, scenario.fallback_proxy_id)
        sessions = list(
            db.scalars(
                select(VintedSession).where(
                    VintedSession.source_id == scenario.repeated_source_id
                )
            )
        )
        monitor_sessions = list(
            db.scalars(
                select(MonitorSession).where(
                    MonitorSession.source_id == scenario.repeated_source_id
                )
            )
        )
        assert source is not None and source.is_active is True
        assert source.monitor_mode == "continuous"
        assert source.next_run_at is not None
        assert retry is not None and retry.status == "success"
        metadata = retry.runtime_metadata or {}
        assert metadata["explicit_cooldown_retry"] is True
        assert metadata["explicit_retry_origin_run_id"] == failed_run["id"]
        assert metadata["proxy_profile_id"] == scenario.proxy_id
        assert metadata["session_acquisition_attempts"] == 1
        assert metadata["session_acquisition_egress_changed"] is True
        assert metadata["session_acquisition_profile_ids"] == [scenario.proxy_id]
        assert "192.0.2." not in json.dumps(metadata)
        assert selected_profile is not None
        assert selected_profile.failure_count == 0 and selected_profile.cooldown_until is None
        assert fallback_profile is not None
        assert fallback_profile.failure_count == 1 and fallback_profile.cooldown_until is not None
        assert len(sessions) == 1
        assert sessions[0].proxy_profile_id == scenario.proxy_id
        assert sessions[0].status == "ready"
        assert len(monitor_sessions) == 1 and monitor_sessions[0].stopped_at is None
        phases = list(
            db.scalars(select(RunEvent.phase).where(RunEvent.run_id == retry.id))
        )
        assert phases.count("session_acquisition_attempt_started") == 1
        assert phases.count("session_acquisition_attempt_succeeded") == 1
        assert phases.count("session_acquisition_attempt_failed") == 0
        assert phases.count("egress_diagnostic_success") == 1
        assert phases.count("run_succeeded") == 1
        policy_hash = monitor_policy_hash(source)

    assert get_seen_cache().has_baseline(scenario.repeated_source_id, policy_hash) is True


@contextmanager
def _loopback_proxy(state_path: Path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            try:
                parsed = urlsplit(self.path)
                if parsed.hostname is None or not ip_address(parsed.hostname).is_loopback:
                    raise AssertionError("Diagnostic proxy received a non-loopback target")
                state = _read_state(state_path)
                observed_ip = "192.0.2.20" if state["mode"] == "different_ip" else "192.0.2.10"
                _increment_state(state_path, "egress_probe_calls")
                body = json.dumps(
                    {
                        "ip": observed_ip,
                        "country": "Spain",
                        "country_code": "ES",
                        "connection": {"asn": 64500, "org": "QA loopback proxy"},
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                _append_state(state_path, "proxy_errors", exc.__class__.__name__)
                self.send_error(502)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="qa-egress-proxy", daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _reset_state(state_path: Path, *, mode: str, item_id: int) -> None:
    _write_state(
        state_path,
        {
            "mode": mode,
            "item_id": item_id,
            "constructions": [],
            "bootstrap_calls": 0,
            "catalog_probe_calls": 0,
            "closes": 0,
            "egress_probe_calls": 0,
            "proxy_errors": [],
        },
    )


def _increment_state(state_path: Path, name: str) -> None:
    state = _read_state(state_path)
    state[name] = int(state.get(name, 0)) + 1
    _write_state(state_path, state)


def _append_state(state_path: Path, name: str, value: str) -> None:
    state = _read_state(state_path)
    entries = list(state.get(name) or [])
    entries.append(value)
    state[name] = entries
    _write_state(state_path, state)


def _read_state(state_path: Path) -> dict[str, Any]:
    return json.loads(state_path.read_text(encoding="utf-8"))


def _write_state(state_path: Path, state: dict[str, Any]) -> None:
    temporary = state_path.with_suffix(f"{state_path.suffix}.test.tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, state_path)


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
    expect(
        page.locator(".monitor-detail-content").get_by_role(
            "heading",
            name=name,
            exact=True,
        )
    ).to_be_visible()


def _post_monitor_action(page, *, path: str, button_name: str) -> dict[str, Any]:
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlsplit(response.url).path == path,
        timeout=15_000,
    ) as info:
        page.get_by_role("button", name=button_name, exact=True).click()
    assert info.value.ok, (
        f"POST {path} returned HTTP {info.value.status}: {info.value.text()}"
    )
    return info.value.json()


def _cleanup(scenario: Scenario) -> None:
    cache = get_seen_cache()
    for source_id in (scenario.success_source_id, scenario.repeated_source_id):
        keys = list(cache.client.scan_iter(match=f"*monitor:{source_id}:*"))
        if keys:
            cache.client.delete(*keys)

    source_ids = (scenario.success_source_id, scenario.repeated_source_id)
    with SessionLocal() as db:
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id.in_(source_ids))))
        event_ids = list(
            db.scalars(select(RunEvent.id).where(RunEvent.source_id.in_(source_ids)))
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
        db.execute(delete(ErrorLog).where(ErrorLog.source_id.in_(source_ids)))
        db.execute(delete(Run).where(Run.source_id.in_(source_ids)))
        db.execute(delete(VintedSession).where(VintedSession.source_id.in_(source_ids)))
        db.execute(delete(MonitorSession).where(MonitorSession.source_id.in_(source_ids)))
        db.execute(delete(SearchSource).where(SearchSource.id.in_(source_ids)))
        db.execute(
            delete(ProxyProfile).where(
                ProxyProfile.id.in_((scenario.proxy_id, scenario.fallback_proxy_id))
            )
        )
        db.execute(delete(UserSession).where(UserSession.user_id == scenario.user_id))
        db.execute(delete(User).where(User.id == scenario.user_id))
        db.commit()


def _state_path() -> Path:
    raw = os.getenv("SAME_PROFILE_QA_STATE")
    if not raw or not Path(raw).is_absolute():
        pytest.skip("set SAME_PROFILE_QA_STATE through the isolated runner")
    return Path(raw).resolve()


def _loopback_origin(name: str) -> str:
    value = os.getenv(name, "").rstrip("/")
    if not value:
        pytest.skip(f"set {name} through the isolated runner")
    _assert_loopback(value)
    return value


def _assert_loopback(url: str) -> None:
    parsed = urlsplit(url)
    assert parsed.scheme in {"http", "https", "ws", "wss"}
    assert parsed.hostname is not None and ip_address(parsed.hostname).is_loopback


def _local_or_non_network(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme in {"data", "blob"}:
        return True
    try:
        return parsed.hostname is not None and ip_address(parsed.hostname).is_loopback
    except ValueError:
        return parsed.hostname == "localhost"
