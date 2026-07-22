from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession
from vinted_monitor.services.local_auth import LOCAL_SESSION_COOKIE_NAME, create_local_user
from vinted_monitor.services.proxies import create_proxy_profile
from vinted_monitor.services.runs import monitor_policy_hash
from vinted_monitor.services.search_sources import create_source
from vinted_monitor.services.seen_cache import RedisSeenCache, get_seen_cache
from vinted_monitor.services.vinted_sessions import save_prepared_vinted_session

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
    proxy_id: int
    incomplete_proxy_id: int
    incomplete_proxy_name: str
    item_ids: dict[str, str]


@dataclass(frozen=True)
class TrafficScenario:
    blocked_source_id: int
    blocked_source_name: str
    token: str
    email: str
    raw_session_token: str
    source_id: int
    source_name: str
    proxy_id: int
    item_ids: dict[str, str]


def test_live_manual_session_start_baseline_lifecycle() -> None:
    api_url = _loopback_origin("MANUAL_SESSION_QA_API_URL")
    pwa_url = _loopback_origin("MANUAL_SESSION_QA_PWA_URL")
    state_path = _state_path()
    settings = get_settings()
    assert settings.scheduler_enabled is False
    assert not hasattr(settings, "vinted_direct_catalog_enabled")
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


def test_live_monitor_and_session_proxy_traffic_summary() -> None:
    api_url = _loopback_origin("MANUAL_SESSION_QA_API_URL")
    pwa_url = _loopback_origin("MANUAL_SESSION_QA_PWA_URL")
    state_path = _state_path()
    settings = get_settings()
    assert settings.scheduler_enabled is False
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
        scenario = _seed_proxy_traffic(token)
        _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABC"])
        _exercise_proxy_traffic_live_stack(
            scenario,
            state_path=state_path,
            api_url=api_url,
            pwa_url=pwa_url,
        )
    finally:
        _cleanup(token, cache)
        assert _redis_keys(cache) == initial_redis_keys
        _assert_isolated_database_empty()


def _exercise_proxy_traffic_live_stack(
    scenario: TrafficScenario,
    *,
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
        context = browser.new_context(
            base_url=pwa_url,
            service_workers="block",
            viewport={"width": 1440, "height": 900},
        )
        try:
            context.add_cookies(
                [
                    {
                        "name": LOCAL_SESSION_COOKIE_NAME,
                        "value": scenario.raw_session_token,
                        "url": f"{pwa_url}/api",
                    }
                ]
            )
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
            page.goto(pwa_url, wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
            page.get_by_role("button", name="Monitores", exact=True).click()
            _select_monitor(page, scenario.source_name, active=False)

            baseline = _start_session(page, scenario.source_id)
            _assert_run(baseline, trigger="baseline", status="success", found=0, opportunities=0)
            _assert_compact_monitor_activity(page, business_activity=False)
            _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABCD"])
            later_run = _run_now(page, scenario.source_id)
            _assert_run(later_run, trigger="manual", status="success", found=1, opportunities=1)

            stats = _get_json(context, f"{api_url}/api/monitors/{scenario.source_id}/stats?range=all", pwa_url)
            expected_traffic = {
                "state": "measured",
                "runs_count": 2,
                "observed_requests": 3,
                "unobserved_attempts": 0,
                "total_observed_bytes": 4000,
            }
            assert stats["historical_proxy_traffic"] == expected_traffic
            assert stats["session_proxy_traffic"] == expected_traffic
            _assert_proxy_traffic_pwa(page, row_label="Acumulado")
            _assert_proxy_traffic_pwa(page, row_label="Sesion activa")
            _assert_compact_monitor_activity(page, business_activity=True)
            page.set_viewport_size({"width": 1366, "height": 768})
            assert page.locator(".monitor-performance-table-wrap").evaluate(
                "node => node.scrollWidth <= node.clientWidth"
            ) is True
            page.set_viewport_size({"width": 1440, "height": 900})
            expect(page.locator('section[aria-label="Tiempo y trafico por ejecucion"]')).to_have_count(0)
            _assert_proxy_traffic_database(scenario, baseline, later_run)

            stop_payload = _stop_session(page, scenario.source_id)
            assert stop_payload["is_active"] is False
            latest = _performance_row(page, "Ultima sesion")
            expect(latest).to_be_visible(timeout=8_000)
            _assert_proxy_traffic_pwa(page, row_label="Ultima sesion")
            stopped_stats = _get_json(
                context,
                f"{api_url}/api/monitors/{scenario.source_id}/stats?range=all",
                pwa_url,
            )
            assert stopped_stats["session_proxy_traffic"] == expected_traffic
            _move_prepared_context_to_near_expiry(scenario.source_id)
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
            page.get_by_role("button", name="Monitores", exact=True).click()
            _select_monitor(page, scenario.source_name, active=False)
            contexts = page.get_by_role("group", name="Contextos HTTP preparados para este monitor")
            expect(contexts.locator("summary")).to_contain_text(re.compile(r"\d+/50 usos · caduca"))
            contexts.locator("summary").click()
            expect(contexts.get_by_text(re.compile(r"Cada uso.*no una peticion HTTP", re.IGNORECASE))).to_be_visible()
            expect(contexts.get_by_text("El contexto ha expirado.", exact=True)).to_be_visible(timeout=12_000)
            expect(contexts.locator("summary")).to_contain_text(re.compile(r"\d+/50 usos · caduco"))
            _mark_proxy_traffic_partial(baseline["id"])
            page.set_viewport_size({"width": 390, "height": 844})
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
            page.get_by_role("button", name="Monitores", exact=True).click()
            _select_monitor(page, scenario.source_name, active=False)
            accumulated = _performance_row(page, "Acumulado")
            latest = _performance_row(page, "Ultima sesion")
            expect(accumulated.locator('td[data-label="Trafico proxy"]')).to_contain_text("parcial")
            expect(latest.locator('td[data-label="Trafico proxy"]')).to_contain_text("parcial")
            expect(accumulated.locator('td[data-label="Peticiones observadas"]')).to_have_text(
                "3 obs. · 1 sin medir (parcial)"
            )
            expect(latest.locator('td[data-label="Peticiones observadas"]')).to_have_text(
                "3 obs. · 1 sin medir (parcial)"
            )
            assert page.locator(".monitor-performance-table-wrap").evaluate(
                "node => node.scrollWidth <= node.clientWidth"
            ) is True
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth") is True

            _select_monitor(page, scenario.blocked_source_name, active=False)
            blocker = page.locator("section.monitor-detail-shell .catalog-filter-status.blocked")
            expect(blocker.get_by_text("Filtros URL no soportados", exact=True)).to_be_visible()
            expect(blocker.get_by_text(re.compile(r"Bloquean:.*color_ids"))).to_be_visible()
            expect(page.locator("section.monitor-detail-shell details.catalog-filter-summary")).to_have_count(0)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth") is True
            page.get_by_role("button", name="Modificar", exact=True).click()
            expect(page.get_by_label(re.compile(r"Detener tras N runs con el mismo contexto"))).to_be_visible()
            expect(
                page.get_by_text("Vacio: continua. Al rotar el contexto, el contador vuelve a empezar.", exact=True)
            ).to_be_visible()
        finally:
            context.close()
            browser.close()
    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


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
            _assert_run(first_baseline, trigger="baseline", status="success", found=0, opportunities=0)
            first_session_id = _assert_first_baseline_state(scenario, first_baseline, cache)
            _assert_active_manual_ui(page, scenario.source_name)

            same_run = _run_now(page, scenario.source_id, assert_busy=True)
            _assert_run(same_run, trigger="manual", status="success", found=0, opportunities=0)
            assert same_run["monitor_session_id"] == first_session_id
            _assert_single_opportunity(scenario, expected_item_id=None)

            _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABCD"])
            new_run = _run_now(page, scenario.source_id)
            _assert_run(new_run, trigger="manual", status="success", found=1, opportunities=1)
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
            _assert_run(duplicate_run, trigger="manual", status="success", found=0, opportunities=0)
            assert duplicate_run["monitor_session_id"] == first_session_id
            _assert_single_opportunity(scenario, expected_item_id=scenario.item_ids["D"])
            _assert_honest_metrics_ui(page, found=1, opportunities=1)

            marker_key = _baseline_key(scenario.source_id)
            assert cache.client.delete(marker_key) == 1
            missing_marker_run = _run_now(page, scenario.source_id)
            _assert_run(missing_marker_run, trigger="manual", status="failed", found=0, opportunities=0)
            assert missing_marker_run["monitor_session_id"] == first_session_id
            assert "La foto inicial ya no esta disponible" in (missing_marker_run["error_message"] or "")
            assert "inicia una nueva sesion" in (missing_marker_run["error_message"] or "")
            expect(page.locator(".notice")).to_contain_text("La foto inicial ya no esta disponible")
            expect(page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True)).to_be_visible()
            _assert_closed_after_missing_marker(scenario, first_session_id)

            _write_state(state_path, ids=[scenario.item_ids[key] for key in "ABCDE"])
            second_baseline = _start_session(page, scenario.source_id)
            _assert_run(second_baseline, trigger="baseline", status="success", found=0, opportunities=0)
            second_session_id = _assert_restart_baseline_state(scenario, first_session_id, cache)
            assert second_baseline["monitor_session_id"] is None
            _assert_active_manual_ui(page, scenario.source_name)

            stop_payload = _stop_session(page, scenario.source_id)
            assert stop_payload["is_active"] is False and stop_payload["next_run_at"] is None
            expect(page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True)).to_be_visible()
            with SessionLocal() as db:
                stopped = db.get(MonitorSession, second_session_id)
                assert stopped is not None and stopped.stopped_at is not None and stopped.stop_reason == "stopped"

            _write_state(state_path, mode="challenge", ids=[], delay_ms=800)
            _select_monitor(page, scenario.failure_source_name, active=False)
            stream_count = _stream_request_count(seen_urls)
            start_count = _start_request_count(seen_urls, scenario.failure_source_id)
            failed_baseline = _start_session_without_controller(page, scenario.failure_source_id)
            _assert_run(failed_baseline, trigger="baseline", status="failed", found=0, opportunities=0)
            assert failed_baseline["monitor_session_id"] is None
            assert "QA controlled Cloudflare challenge" in (failed_baseline["error_message"] or "")
            expect(page.get_by_text(re.compile(r"Cooldown.*proxy|proxy.*cooldown", re.IGNORECASE))).to_be_visible(
                timeout=10_000
            )
            assert _stream_request_count(seen_urls) == stream_count
            assert _start_request_count(seen_urls, scenario.failure_source_id) == start_count + 1
            blocked_start = page.get_by_role("button", name=re.compile(r"^(Iniciar|Reintentar) sesion$"))
            expect(blocked_start).to_be_disabled()
            blocked_start.click(force=True)
            page.wait_for_timeout(300)
            assert _start_request_count(seen_urls, scenario.failure_source_id) == start_count + 1
            expect(
                page.get_by_role("button", name=f"{scenario.failure_source_name}, inactivo", exact=True)
            ).to_be_visible()
            _assert_failed_baseline_state(scenario, failed_baseline)

            page.get_by_role("button", name="Ajustes", exact=True).click()
            expect(page.get_by_role("button", name="Test IP", exact=True)).to_have_count(0)
            expect(page.get_by_text(re.compile(r"Cooldown activo", re.IGNORECASE))).to_be_visible()
            incomplete_row = page.locator("article.proxy-row").filter(has_text=scenario.incomplete_proxy_name)
            incomplete_row.get_by_role("button", name="Activar", exact=True).click()
            expect(page.locator(".notice")).to_contain_text(re.compile(r"username.*required", re.IGNORECASE), timeout=5_000)
            _assert_incomplete_proxy_still_inactive(scenario)

            _move_cooldown_to_near_expiry(scenario.proxy_id)
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="Monitores", exact=True).click()
            _select_monitor(page, scenario.failure_source_name, active=False)
            retry_button = page.get_by_role("button", name="Reintentar sesion", exact=True)
            expect(retry_button).to_be_enabled(timeout=8_000)
            _write_state(state_path, mode="ok", ids=[])
            retried_baseline = _start_session(page, scenario.failure_source_id)
            _assert_run(retried_baseline, trigger="baseline", status="success", found=0, opportunities=0)
            _assert_retry_cleared_cooldown(scenario, failed_baseline, retried_baseline)
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
        proxy = create_proxy_profile(
            db,
            name=f"qa manual proxy {token}",
            scheme="http",
            kind="residential",
            host="proxy.invalid",
            port=8080,
            username="qa-user",
            password="qa-password",
            country_code="ES",
        )
        incomplete_proxy = ProxyProfile(
            name=f"qa manual proxy {token} incomplete",
            scheme="http",
            kind="residential",
            host="proxy.invalid",
            port=8081,
            username=None,
            password_encrypted=None,
            country_code="ES",
            locale="es-ES",
            accept_language="en-GB,en;q=0.9",
            screen="1920x1080",
            vinted_screen="catalog",
            max_concurrent_runs=1,
            is_active=False,
            identity_generation=1,
        )
        db.add(incomplete_proxy)
        db.flush()
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
            proxy_id=proxy.id,
            incomplete_proxy_id=incomplete_proxy.id,
            incomplete_proxy_name=incomplete_proxy.name,
            item_ids={letter: f"qa-manual-{token}-{letter}" for letter in "ABCDE"},
        )


def _seed_proxy_traffic(token: str) -> TrafficScenario:
    email = f"qa-manual-session-{token}@example.local"
    source_name = f"qa manual traffic {token}"
    raw_session_token = secrets.token_urlsafe(48)
    with SessionLocal() as db:
        user = User(
            email=email,
            password_hash="preauthenticated-qa-session-only",
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(
            UserSession(
                token_hash=hashlib.sha256(raw_session_token.encode()).hexdigest(),
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                authenticated_at=datetime.now(UTC),
            )
        )
        proxy = create_proxy_profile(
            db,
            name=f"qa manual proxy {token}",
            scheme="http",
            kind="residential",
            host="proxy.invalid",
            port=8080,
            username="qa-user",
            password="qa-password",
            country_code="ES",
        )
        source = create_source(
            db,
            source_name,
            f"https://www.vinted.es/catalog?search_text=qa-manual-{token}",
        )
        blocked_source = SearchSource(
            name=f"qa manual blocked {token}",
            url="https://www.vinted.es/catalog?catalog[]=76&color_ids[]=12",
            normalized_query={"catalog[]": ["76"], "color_ids[]": ["12"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={
                "interval_seconds": 60,
                "jitter_percent": 0,
                "allowed_windows": [],
            },
        )
        db.add(blocked_source)
        db.flush()
        profile = profile_for_impersonate(get_settings().curl_impersonate_browser)
        prepared = save_prepared_vinted_session(
            db,
            source,
            proxy,
            proxy_session_id=f"qa-{token}",
            profile=profile,
            context=PreparedCatalogSession(
                proxy_session_id=f"qa-{token}",
                cookies={
                    name: token
                    for name in ("anon_id", "access_token_web", "datadome", "__cf_bm", "v_udt")
                },
                csrf_token=token,
                anon_id=token,
                access_token_web=token,
                datadome=token,
                cf_bm=token,
                v_udt=token,
                user_iso_locale=proxy.locale,
                vinted_screen=proxy.vinted_screen,
                egress_ip="127.0.0.1",
                egress_country_code=proxy.country_code,
                egress_validated_at=datetime.now(UTC),
            ),
            settings=get_settings(),
        )
        prepared.request_count = 7
        db.commit()
        return TrafficScenario(
            blocked_source_id=blocked_source.id,
            blocked_source_name=blocked_source.name,
            token=token,
            email=email,
            raw_session_token=raw_session_token,
            source_id=source.id,
            source_name=source.name,
            proxy_id=proxy.id,
            item_ids={letter: f"qa-manual-{token}-{letter}" for letter in "ABCD"},
        )


def _mark_proxy_traffic_partial(run_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        assert run is not None
        metadata = dict(run.runtime_metadata or {})
        estimate = dict(metadata["proxy_traffic_estimate"])
        estimate["unobserved_attempts"] = 1
        metadata["proxy_traffic_estimate"] = estimate
        run.runtime_metadata = metadata
        db.commit()


def _move_prepared_context_to_near_expiry(source_id: int) -> None:
    with SessionLocal() as db:
        prepared = db.scalar(select(VintedSession).where(VintedSession.source_id == source_id))
        assert prepared is not None
        prepared.expires_at = datetime.now(UTC) + timedelta(seconds=4)
        db.commit()


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
    expect(page.locator(".monitor-detail-content").get_by_role("heading", name=name, exact=True)).to_be_visible()


def _start_session(page: Page, source_id: int, *, assert_busy: bool = False) -> dict:
    path = f"/api/monitors/{source_id}/start"
    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as info:
        page.get_by_role("button", name=re.compile(r"^(Iniciar|Reintentar) sesion$")).click()
        if assert_busy:
            expect(page.get_by_role("button", name="Iniciando...", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="Preparar sesion", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="Probar detalle", exact=True)).to_have_count(0)
    return _response_json(info.value, path)


def _start_session_without_controller(page: Page, source_id: int) -> dict:
    path = f"/api/monitors/{source_id}/start"
    result = page.evaluate(
        """async (path) => {
          const session = await fetch('/api/auth/session', { credentials: 'same-origin' }).then((response) => response.json());
          const response = await fetch(path, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRF-Token': session.csrf_token }
          });
          return { status: response.status, body: await response.json() };
        }""",
        path,
    )
    assert result["status"] == 201, f"POST {path} returned HTTP {result['status']}"
    return result["body"]


def _stream_request_count(urls: list[str]) -> int:
    return sum(urlsplit(url).path == "/api/monitors/events/stream" for url in urls)


def _start_request_count(urls: list[str], source_id: int) -> int:
    path = f"/api/monitors/{source_id}/start"
    return sum(urlsplit(url).path == path for url in urls)


def _run_now(page: Page, source_id: int, *, assert_busy: bool = False) -> dict:
    path = f"/api/monitors/{source_id}/runs"
    run_button = page.get_by_role("button", name=re.compile(r"^Ejecut(ar ahora|ando\.\.\.)$"))
    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == path) as info:
        run_button.click()
        if assert_busy:
            expect(run_button).to_be_disabled()
            expect(page.get_by_role("button", name="Detener sesion", exact=True)).to_be_enabled()
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
    opportunities: int,
) -> None:
    assert payload["trigger"] == trigger
    assert payload["status"] == status
    assert payload["items_found"] == found
    assert "items_new" not in payload
    assert payload["opportunities_created"] == opportunities


def _assert_active_manual_ui(page: Page, source_name: str) -> None:
    expect(page.get_by_role("button", name=f"{source_name}, activo", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Ejecutar ahora", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="Detener sesion", exact=True)).to_be_enabled()
    assert page.get_by_role("button", name="Iniciar sesion", exact=True).count() == 0
    assert page.get_by_role("button", name="Recalibrar listado inicial", exact=True).count() == 0
    expect(page.locator(".monitor-config-editor")).to_have_count(0)
    expect(page.get_by_role("button", name="Modificar", exact=True)).to_have_count(0)


def _assert_honest_metrics_ui(page: Page, *, found: int, opportunities: int) -> None:
    table = page.get_by_role("table", name="Comparativa de rendimiento del monitor")
    expect(table).to_be_visible()
    for row_label in ("Acumulado", "Sesion activa"):
        row = _performance_row(page, row_label)
        expect(row.locator('td[data-label="Encontrados"]')).to_have_text(str(found))
        expect(row.locator('td[data-label="Oportunidades"]')).to_have_text(str(opportunities))
        for obsolete_label in ("Nuevos", "Nuevos monitor", "Pasan", "Descartados", "Sin detalle"):
            expect(row.get_by_text(obsolete_label, exact=True)).to_have_count(0)


def _assert_proxy_traffic_pwa(page: Page, *, row_label: str) -> None:
    row = _performance_row(page, row_label)
    expect(row.locator('td[data-label="Trafico proxy"]')).to_have_text("4 kB")
    expect(row.locator('td[data-label="Peticiones observadas"]')).to_have_text("3")
    performance = page.locator("section.monitor-performance")
    expect(performance.get_by_text(re.compile(r"DataImpulse.*facturacion autoritativo", re.IGNORECASE))).to_be_visible()


def _performance_row(page: Page, label: str):
    table = page.get_by_role("table", name="Comparativa de rendimiento del monitor")
    return table.locator("tbody tr").filter(
        has=page.get_by_role("rowheader", name=re.compile(rf"^{re.escape(label)}(?:\s|$)"))
    )


def _assert_compact_monitor_activity(page: Page, *, business_activity: bool) -> None:
    detail = page.locator("section.monitor-detail-shell")
    filters = detail.locator("details.catalog-filter-summary")
    contexts = detail.get_by_role("group", name="Contextos HTTP preparados para este monitor")
    performance = detail.locator("section.monitor-performance")
    logs = detail.locator("details.monitor-logs").filter(has_text="Logs acumulados")

    expect(filters).to_be_visible()
    expect(filters).not_to_have_attribute("open", "")
    expect(filters.locator("summary")).to_contain_text(
        re.compile(r"\d+ filtros? URL · \d+ controlados? · \d+ sin efecto")
    )
    expect(contexts).to_be_visible()
    expect(contexts).not_to_have_attribute("open", "")
    expect(contexts.locator("summary")).to_contain_text(
        re.compile(r"1/1 reutilizable · \d+/50 usos · caduca")
    )
    expect(logs).not_to_have_attribute("open", "")
    table = page.get_by_role("table", name="Comparativa de rendimiento del monitor")
    expect(table).to_be_visible()
    expect(table.get_by_role("columnheader", name="Trafico proxy", exact=True)).to_be_visible()
    expect(table.get_by_role("columnheader", name="Peticiones obs.", exact=True)).to_be_visible()
    table_wrapper = detail.locator(".monitor-performance-table-wrap")
    assert table_wrapper.evaluate("node => node.scrollWidth <= node.clientWidth") is True

    filter_box = filters.bounding_box()
    context_box = contexts.bounding_box()
    performance_box = performance.bounding_box()
    assert filter_box is not None and context_box is not None and performance_box is not None
    assert filter_box["y"] < context_box["y"] < performance_box["y"]

    chart = detail.locator(".monitor-chart")
    empty_chart = detail.get_by_text("Sin ejecuciones de negocio en este rango.", exact=True)
    if business_activity:
        expect(chart).to_be_visible()
        expect(empty_chart).to_have_count(0)
        chart_box = chart.bounding_box()
        assert chart_box is not None and 150 <= chart_box["height"] <= 175
        ticks = chart.locator(".recharts-cartesian-axis-tick-value")
        expect(ticks.first).to_be_visible()
        assert ticks.evaluate_all(
            "nodes => nodes.every(node => getComputedStyle(node).fontSize === '10px')"
        ) is True
        for label in ("Encontrados", re.compile(r"^Tiempo")):
            axis_label = chart.locator("svg text").filter(has_text=label).first
            expect(axis_label).to_be_visible()
            assert axis_label.evaluate("node => getComputedStyle(node).fontSize") == "10px"
        legend = chart.locator(".monitor-chart-legend")
        assert legend.evaluate("node => getComputedStyle(node).fontSize") == "11px"
        session_marker = chart.locator("svg text").filter(has_text="Inicio sesion")
        expect(session_marker).to_be_visible()
        assert session_marker.evaluate("node => getComputedStyle(node).fontSize") == "11px"
        detail.evaluate("node => node.scrollIntoView({block: 'start'})")
        page.wait_for_timeout(100)
        logs_box = logs.locator("summary").bounding_box()
        assert logs_box is not None and logs_box["y"] + logs_box["height"] <= 900
    else:
        expect(chart).to_have_count(0)
        expect(empty_chart).to_be_visible()


def _assert_proxy_traffic_database(scenario: TrafficScenario, baseline: dict, later_run: dict) -> None:
    with SessionLocal() as db:
        baseline_row = db.get(Run, baseline["id"])
        later_row = db.get(Run, later_run["id"])
        session = db.scalar(
            select(MonitorSession).where(MonitorSession.source_id == scenario.source_id, MonitorSession.stopped_at.is_(None))
        )
        assert baseline_row is not None and later_row is not None and session is not None
        assert baseline_row.runtime_metadata["opened_monitor_session_id"] == session.id
        assert baseline_row.runtime_metadata["proxy_traffic_estimate"]["total_observed_bytes"] == 1000
        assert later_row.monitor_session_id == session.id
        assert later_row.runtime_metadata["proxy_traffic_estimate"]["total_observed_bytes"] == 3000


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
        proxy = db.get(ProxyProfile, scenario.proxy_id)
        assert source is not None and source.is_active is False
        assert source.monitor_started_at is None and source.monitor_until is None and source.next_run_at is None
        assert run is not None and run.status == "failed" and run.monitor_session_id is None
        assert run.runtime_metadata["failure_kind"] == "cloudflare_challenge"
        assert proxy is not None and proxy.failure_count == 1
        assert proxy.cooldown_until is not None and proxy.cooldown_until > datetime.now(UTC)
        assert db.scalar(
            select(func.count()).select_from(MonitorSession).where(MonitorSession.source_id == scenario.failure_source_id)
        ) == 0
        assert db.scalar(select(func.count()).select_from(ErrorLog).where(ErrorLog.run_id == run.id)) == 1


def _assert_incomplete_proxy_still_inactive(scenario: Scenario) -> None:
    with SessionLocal() as db:
        profile = db.get(ProxyProfile, scenario.incomplete_proxy_id)
        assert profile is not None and profile.is_active is False


def _move_cooldown_to_near_expiry(proxy_id: int) -> None:
    with SessionLocal() as db:
        profile = db.get(ProxyProfile, proxy_id)
        assert profile is not None and profile.cooldown_until is not None
        profile.cooldown_until = datetime.now(UTC) + timedelta(seconds=3)
        db.commit()


def _assert_retry_cleared_cooldown(scenario: Scenario, failed_run: dict, retried_run: dict) -> None:
    with SessionLocal() as db:
        failed = db.get(Run, failed_run["id"])
        retried = db.get(Run, retried_run["id"])
        proxy = db.get(ProxyProfile, scenario.proxy_id)
        assert failed is not None and retried is not None and proxy is not None
        assert failed.runtime_metadata["proxy_profile_id"] == retried.runtime_metadata["proxy_profile_id"] == proxy.id
        assert proxy.failure_count == 0 and proxy.cooldown_until is None


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
        db.execute(delete(ProxyProfile).where(ProxyProfile.name.like(f"qa manual proxy {token}%")))
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
        ProxyProfile,
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
