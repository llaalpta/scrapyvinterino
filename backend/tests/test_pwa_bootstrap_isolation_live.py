from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Page, Route, expect, sync_playwright
from sqlalchemy import delete, func, select

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import (
    MonitorSession,
    Opportunity,
    Run,
    RunEvent,
    SearchSource,
    User,
    UserSession,
    VintedSession,
)
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import create_local_user
from vinted_monitor.services.search_sources import create_source

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PASSWORD = "pwa-bootstrap-live-password"
FAILED_SURFACES = (
    ("monitores", "/api/monitors"),
    ("oportunidades", "/api/opportunities"),
    ("proxies", "/api/proxy-profiles"),
)


@dataclass(frozen=True)
class Scenario:
    email: str
    source_id: int
    source_name: str
    source_snapshot: dict[str, object]
    source_url: str
    user_id: int


def test_live_pwa_bootstrap_failures_do_not_hide_monitors() -> None:
    api_url = _loopback_origin("PWA_BOOTSTRAP_QA_API_URL")
    pwa_url = _loopback_origin("PWA_BOOTSTRAP_QA_PWA_URL")
    settings = get_settings()
    assert settings.scheduler_enabled is False
    assert settings.vinted_direct_catalog_enabled is False
    assert settings.vinted_datadome_collector_enabled is False
    assert settings.vinted_auth_enabled is False
    assert settings.action_requests_enabled is False
    for endpoint in (settings.vinted_base_url, settings.vinted_datadome_collector_url, settings.egress_diagnostic_url):
        assert urlsplit(str(endpoint)).hostname in LOOPBACK_HOSTS

    scenario = _seed()
    try:
        _exercise_live_stack(scenario, api_url=api_url, pwa_url=pwa_url)
        _assert_source_snapshot(scenario)
        _assert_no_runtime_graph(scenario.source_id)
    finally:
        _cleanup(scenario)


def _seed() -> Scenario:
    token = uuid4().hex
    with SessionLocal() as db:
        user = create_local_user(db, email=f"qa-bootstrap-{token}@example.local", password=PASSWORD)
        source = create_source(
            db,
            f"qa bootstrap monitor {token}",
            f"https://www.vinted.es/catalog?search_text=bootstrap-{token}",
        )
        return Scenario(
            email=user.email,
            source_id=source.id,
            source_name=source.name,
            source_snapshot=_snapshot(source),
            source_url=source.url,
            user_id=user.id,
        )


def _exercise_live_stack(scenario: Scenario, *, api_url: str, pwa_url: str) -> None:
    seen_urls: list[str] = []
    blocked_urls: list[str] = []
    unexpected_mutations: list[tuple[str, str]] = []
    fault_counts = {surface: 0 for surface, _path in FAILED_SURFACES}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("PWA_BOOTSTRAP_QA_BROWSER_CHANNEL", "chrome"),
            headless=True,
            args=["--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"],
        )
        context = browser.new_context(base_url=pwa_url, service_workers="block")
        authenticated = False
        try:
            for surface, failed_path in FAILED_SURFACES:
                page = context.new_page()

                def guard(route: Route, *, expected_surface: str = surface, expected_path: str = failed_path) -> None:
                    if not _guard_local_read(
                        route,
                        seen_urls=seen_urls,
                        blocked_urls=blocked_urls,
                        unexpected_mutations=unexpected_mutations,
                    ):
                        return

                    request = route.request
                    parsed = urlsplit(request.url)
                    if request.method == "GET" and parsed.path == expected_path and parsed.query == "":
                        fault_counts[expected_surface] += 1
                        route.fulfill(
                            status=503,
                            content_type="application/json",
                            body=f'{{"detail":"qa bootstrap {expected_surface} unavailable"}}',
                        )
                        return
                    route.continue_()

                page.route("**/*", guard)
                page.on("websocket", lambda socket: _assert_loopback(socket.url))
                try:
                    page.goto(pwa_url, wait_until="domcontentloaded")
                    if not authenticated:
                        expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
                        page.get_by_label("Email").fill(scenario.email)
                        page.get_by_label("Password").fill(PASSWORD)
                        page.get_by_role("button", name="Entrar").click()
                        authenticated = True

                    expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
                    expected_message = f"Carga inicial incompleta: {surface}. Las demas secciones disponibles siguen operativas."
                    expect(page.get_by_text(expected_message, exact=True)).to_be_visible()
                    assert fault_counts[surface] >= 1
                    _assert_initial_failure_state(page, scenario, surface=surface)
                finally:
                    page.close()

            recovery_fault_count = _exercise_monitor_recovery_after_initial_failure(
                context,
                scenario,
                pwa_url=pwa_url,
                seen_urls=seen_urls,
                blocked_urls=blocked_urls,
                unexpected_mutations=unexpected_mutations,
            )
            refresh_fault_count = _exercise_confirmed_monitor_refresh_failure(
                context,
                scenario,
                pwa_url=pwa_url,
                seen_urls=seen_urls,
                blocked_urls=blocked_urls,
                unexpected_mutations=unexpected_mutations,
            )
            _assert_source_from_api(context, scenario, api_url=api_url, origin=pwa_url)
        finally:
            context.close()
            browser.close()

    assert all(count >= 1 for count in fault_counts.values())
    assert recovery_fault_count >= 1
    assert refresh_fault_count >= 1
    assert not unexpected_mutations
    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


def _assert_initial_failure_state(page: Page, scenario: Scenario, *, surface: str) -> None:
    if surface == "monitores":
        expect(page.locator(".topbar p")).to_have_text("0 oportunidades")
        page.get_by_role("button", name="Filtros", exact=True).click()
        monitor_selector = page.get_by_role("combobox", name="Monitor", exact=True)
        expect(monitor_selector).to_be_disabled()
        expect(monitor_selector.locator("option")).to_have_count(1)
        expect(monitor_selector.locator("option")).to_have_text("Monitores no disponibles")
        expect(page.get_by_label("Precio min", exact=True)).to_be_enabled()
        expect(page.get_by_role("button", name="Aplicar", exact=True)).to_be_enabled()
        page.locator(".filter-panel").get_by_title("Cerrar filtros").click()
        expect(page.locator(".result-table td.empty")).to_have_text("No hay oportunidades para los filtros actuales.")

        page.get_by_role("button", name="Monitores", exact=True).click()
        expect(page.locator(".topbar p")).to_have_text("Monitores no disponibles")
        create_panel = page.get_by_label("Configurar nuevo monitor")
        expect(create_panel.locator(".monitor-section-heading > span")).to_have_text("No disponible")
        expect(create_panel.get_by_role("button", name="Guardar URL", exact=True)).to_be_disabled()
        monitor_table = page.get_by_label("Monitores configurados")
        expect(monitor_table.get_by_role("status")).to_have_text(
            "Monitores no disponibles. Vuelve a entrar en Monitores o recarga la PWA para reintentar."
        )
        expect(monitor_table.get_by_text("No hay monitores configurados.", exact=True)).to_have_count(0)
        return

    if surface == "oportunidades":
        expect(page.locator(".topbar p")).to_have_text("Oportunidades no disponibles")
        expect(page.locator(".results-view").get_by_role("status")).to_have_text(
            "Oportunidades no disponibles. Aplica los filtros para reintentar la carga."
        )
        expect(page.get_by_text("No hay oportunidades para los filtros actuales.", exact=True)).to_have_count(0)
        _assert_confirmed_monitor_filter(page, scenario)
        page.get_by_role("button", name="Monitores", exact=True).click()
        expect(page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True)).to_be_visible()
        return

    if surface == "proxies":
        expect(page.locator(".topbar p")).to_have_text("0 oportunidades")
        page.get_by_role("button", name="Ajustes", exact=True).click()
        expect(page.get_by_role("heading", name="Estado del scheduler", exact=True)).to_be_visible()
        proxy_section = page.locator(".proxy-section")
        expect(proxy_section.locator(".panel-heading > span")).to_have_text("No disponible")
        expect(proxy_section.get_by_role("status")).to_have_text("Proxys no disponibles. Recarga la PWA para reintentar.")
        expect(proxy_section.get_by_text("Sin proxys configurados.", exact=False)).to_have_count(0)
        expect(proxy_section.get_by_role("button", name="Guardar proxy", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Monitores", exact=True).click()
        expect(page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True)).to_be_visible()
        return

    raise AssertionError(f"unsupported initial failure surface: {surface}")


def _assert_confirmed_monitor_filter(page: Page, scenario: Scenario) -> None:
    page.get_by_role("button", name="Filtros", exact=True).click()
    monitor_selector = page.get_by_role("combobox", name="Monitor", exact=True)
    expect(monitor_selector).to_be_enabled()
    expect(monitor_selector.locator(f'option[value="{scenario.source_id}"]')).to_have_text(scenario.source_name)
    page.locator(".filter-panel").get_by_title("Cerrar filtros").click()


def _exercise_monitor_recovery_after_initial_failure(
    context: BrowserContext,
    scenario: Scenario,
    *,
    pwa_url: str,
    seen_urls: list[str],
    blocked_urls: list[str],
    unexpected_mutations: list[tuple[str, str]],
) -> int:
    page = context.new_page()
    fault_enabled = True
    fault_count = 0

    def guard(route: Route) -> None:
        nonlocal fault_count
        if not _guard_local_read(
            route,
            seen_urls=seen_urls,
            blocked_urls=blocked_urls,
            unexpected_mutations=unexpected_mutations,
        ):
            return

        request = route.request
        parsed = urlsplit(request.url)
        if fault_enabled and request.method == "GET" and parsed.path == "/api/monitors" and parsed.query == "":
            fault_count += 1
            route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"qa initial monitor unavailable"}',
            )
            return
        route.continue_()

    page.route("**/*", guard)
    page.on("websocket", lambda socket: _assert_loopback(socket.url))
    try:
        page.goto(pwa_url, wait_until="domcontentloaded")
        expect(
            page.get_by_text(
                "Carga inicial incompleta: monitores. Las demas secciones disponibles siguen operativas.",
                exact=True,
            )
        ).to_be_visible()

        fault_enabled = False
        page.get_by_role("button", name="Monitores", exact=True).click()
        expect(page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True)).to_be_visible()

        name_input = page.get_by_role("textbox", name="Nombre", exact=True)
        url_input = page.get_by_role("textbox", name="URL de catalogo", exact=True)
        mode_select = page.get_by_role("combobox", name="Modo", exact=True)
        expect(name_input).to_be_enabled()
        expect(name_input).to_have_value(scenario.source_name)
        expect(url_input).to_have_value(scenario.source_url)
        expect(mode_select).to_have_value("manual")

        name_input.fill(f"{scenario.source_name} editado")
        expect(url_input).to_have_value(scenario.source_url)
        expect(mode_select).to_have_value("manual")
        expect(page.get_by_role("button", name="Guardar", exact=True)).to_be_enabled()
        return fault_count
    finally:
        page.close()


def _exercise_confirmed_monitor_refresh_failure(
    context: BrowserContext,
    scenario: Scenario,
    *,
    pwa_url: str,
    seen_urls: list[str],
    blocked_urls: list[str],
    unexpected_mutations: list[tuple[str, str]],
) -> int:
    page = context.new_page()
    fault_enabled = False
    fault_count = 0

    def guard(route: Route) -> None:
        nonlocal fault_count
        if not _guard_local_read(
            route,
            seen_urls=seen_urls,
            blocked_urls=blocked_urls,
            unexpected_mutations=unexpected_mutations,
        ):
            return

        request = route.request
        parsed = urlsplit(request.url)
        if fault_enabled and request.method == "GET" and parsed.path == "/api/monitors" and parsed.query == "":
            fault_count += 1
            route.fulfill(
                status=503,
                content_type="application/json",
                body='{"detail":"qa monitor refresh unavailable"}',
            )
            return
        route.continue_()

    page.route("**/*", guard)
    page.on("websocket", lambda socket: _assert_loopback(socket.url))
    try:
        page.goto(pwa_url, wait_until="domcontentloaded")
        expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
        _assert_confirmed_monitor_filter(page, scenario)

        fault_enabled = True
        with page.expect_response(
            lambda response: (
                response.request.method == "GET" and urlsplit(response.url).path == "/api/monitors" and urlsplit(response.url).query == ""
            )
        ) as response_info:
            page.get_by_role("button", name="Monitores", exact=True).click()
        assert response_info.value.status == 503

        expect(page.get_by_text("qa monitor refresh unavailable", exact=True)).to_be_visible()
        expect(page.locator(".topbar p")).to_have_text("1 monitores configurados")
        expect(page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True)).to_be_visible()
        create_panel = page.get_by_label("Configurar nuevo monitor")
        expect(create_panel.locator(".monitor-section-heading > span")).to_have_text("1 configurados")
        expect(create_panel.get_by_role("button", name="Guardar URL", exact=True)).to_be_enabled()
        monitor_table = page.get_by_label("Monitores configurados")
        expect(monitor_table.locator(".monitor-section-heading > span")).to_have_text("1")
        expect(monitor_table.get_by_role("status")).to_have_count(0)
        expect(monitor_table.get_by_text("No hay monitores configurados.", exact=True)).to_have_count(0)
        return fault_count
    finally:
        page.close()


def _guard_local_read(
    route: Route,
    *,
    seen_urls: list[str],
    blocked_urls: list[str],
    unexpected_mutations: list[tuple[str, str]],
) -> bool:
    request = route.request
    seen_urls.append(request.url)
    if not _local_or_non_network(request.url):
        blocked_urls.append(request.url)
        route.abort("blockedbyclient")
        return False

    parsed = urlsplit(request.url)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and parsed.path.startswith("/api/") and parsed.path != "/api/auth/login":
        unexpected_mutations.append((request.method, parsed.path))
        route.abort("blockedbyclient")
        return False
    return True


def _assert_source_from_api(context: BrowserContext, scenario: Scenario, *, api_url: str, origin: str) -> None:
    response = context.request.get(f"{api_url}/api/monitors", headers={"Origin": origin})
    assert response.ok, f"GET /api/monitors returned HTTP {response.status}"
    matches = [source for source in response.json() if int(source["id"]) == scenario.source_id]
    assert len(matches) == 1
    assert matches[0]["name"] == scenario.source_name


def _snapshot(source: SearchSource) -> dict[str, object]:
    return {column.name: deepcopy(getattr(source, column.name)) for column in SearchSource.__table__.columns}


def _assert_source_snapshot(scenario: Scenario) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, scenario.source_id)
        assert source is not None
        assert _snapshot(source) == scenario.source_snapshot


def _assert_no_runtime_graph(source_id: int) -> None:
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Run).where(Run.source_id == source_id)) == 0
        assert db.scalar(select(func.count()).select_from(MonitorSession).where(MonitorSession.source_id == source_id)) == 0
        assert db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.source_id == source_id)) == 0
        assert db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id)) == 0
        assert db.scalar(select(func.count()).select_from(VintedSession).where(VintedSession.source_id == source_id)) == 0


def _cleanup(scenario: Scenario) -> None:
    with SessionLocal() as db:
        db.execute(delete(UserSession).where(UserSession.user_id == scenario.user_id))
        db.execute(delete(SearchSource).where(SearchSource.id == scenario.source_id))
        db.execute(delete(User).where(User.id == scenario.user_id))
        db.commit()


def _loopback_origin(name: str) -> str:
    raw = os.getenv(name)
    if not raw:
        pytest.skip(f"set {name} through the isolated integration runner")
    parsed = urlsplit(raw)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS or parsed.port is None or parsed.path not in {"", "/"}:
        raise ValueError(f"{name} must be an HTTP loopback origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must be an unambiguous HTTP loopback origin")
    return raw.rstrip("/")


def _local_or_non_network(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"data", "blob", "about"} or (
        parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname in LOOPBACK_HOSTS
    )


def _assert_loopback(url: str) -> None:
    parsed = urlsplit(url)
    assert parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname in LOOPBACK_HOSTS
