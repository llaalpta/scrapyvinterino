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
PASSWORD = "monitor-command-live-password"
CREATE_REFRESH_MESSAGE = "El monitor se creo, pero no se pudieron cargar sus estadisticas; recarga Monitores"
ARCHIVE_REFRESH_MESSAGE = (
    "El monitor se archivo, pero no se pudieron actualizar por completo los datos derivados; recarga la PWA"
)


@dataclass(frozen=True)
class Scenario:
    created_name: str
    created_url: str
    email: str
    seed_source_id: int
    seed_source_name: str
    user_id: int


def test_live_pwa_monitor_command_state_contract() -> None:
    api_url = _loopback_origin("PWA_MONITOR_COMMAND_QA_API_URL")
    pwa_url = _loopback_origin("PWA_MONITOR_COMMAND_QA_PWA_URL")
    settings = get_settings()
    assert settings.scheduler_enabled is False
    assert settings.vinted_direct_catalog_enabled is False
    assert settings.vinted_datadome_collector_enabled is False
    assert settings.vinted_auth_enabled is False
    assert settings.action_requests_enabled is False
    for endpoint in (settings.vinted_base_url, settings.vinted_datadome_collector_url, settings.egress_diagnostic_url):
        assert urlsplit(str(endpoint)).hostname in LOOPBACK_HOSTS

    scenario = _seed()
    created_source_id: int | None = None
    try:
        created_source_id = _exercise_live_stack(scenario, api_url=api_url, pwa_url=pwa_url)
        _assert_no_runtime_graph((scenario.seed_source_id, created_source_id))
    finally:
        _cleanup(scenario, created_source_id)


def _seed() -> Scenario:
    token = uuid4().hex
    created_name = f"qa command created {token}"
    created_url = f"https://www.vinted.es/catalog?search_text=created-{token}"
    with SessionLocal() as db:
        user = create_local_user(db, email=f"qa-command-{token}@example.local", password=PASSWORD)
        seed = create_source(
            db,
            f"qa command seed {token}",
            f"https://www.vinted.es/catalog?search_text=seed-{token}",
        )
        return Scenario(
            created_name=created_name,
            created_url=created_url,
            email=user.email,
            seed_source_id=seed.id,
            seed_source_name=seed.name,
            user_id=user.id,
        )


def _exercise_live_stack(scenario: Scenario, *, api_url: str, pwa_url: str) -> int:
    seen_urls: list[str] = []
    blocked_urls: list[str] = []
    monitor_posts: list[str] = []
    monitor_deletes: list[str] = []
    faults: dict[str, int | bool | None] = {
        "stats_source_id": None,
        "stats_aborted": 0,
        "abort_runs": False,
        "runs_aborted": 0,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("PWA_MONITOR_COMMAND_QA_BROWSER_CHANNEL", "chrome"),
            headless=True,
            args=["--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"],
        )
        context = browser.new_context(base_url=pwa_url, service_workers="block")
        try:
            page = context.new_page()
            page.add_init_script(_response_delivery_gates())

            def guard(route: Route) -> None:
                request = route.request
                seen_urls.append(request.url)
                if not _local_or_non_network(request.url):
                    blocked_urls.append(request.url)
                    route.abort("blockedbyclient")
                    return

                parsed = urlsplit(request.url)
                if request.method == "POST" and parsed.path == "/api/monitors":
                    monitor_posts.append(request.url)
                if request.method == "DELETE" and parsed.path.startswith("/api/monitors/"):
                    monitor_deletes.append(request.url)
                stats_source_id = faults["stats_source_id"]
                if (
                    request.method == "GET"
                    and stats_source_id is not None
                    and parsed.path == f"/api/monitors/{stats_source_id}/stats"
                    and faults["stats_aborted"] == 0
                ):
                    faults["stats_aborted"] = 1
                    route.abort("failed")
                    return
                if (
                    request.method == "GET"
                    and faults["abort_runs"] is True
                    and parsed.path == "/api/runs"
                    and parsed.query == ""
                    and faults["runs_aborted"] == 0
                ):
                    faults["runs_aborted"] = 1
                    route.abort("failed")
                    return
                route.continue_()

            page.route("**/*", guard)
            page.on("websocket", lambda socket: _assert_loopback(socket.url))
            page.goto(pwa_url, wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
            page.get_by_label("Email").fill(scenario.email)
            page.get_by_label("Password").fill(PASSWORD)
            page.get_by_role("button", name="Entrar").click()
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()

            page.get_by_role("button", name="Monitores", exact=True).click()
            page.get_by_role("button", name=f"{scenario.seed_source_name}, inactivo", exact=True).click()
            archive_button = page.get_by_role("button", name="Archivar monitor", exact=True)
            start_button = page.get_by_role("button", name="Iniciar sesion", exact=True)
            expect(archive_button).to_be_enabled()
            expect(start_button).to_be_enabled()
            expect(page.get_by_role("button", name="Preparar sesion", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="Probar detalle", exact=True)).to_have_count(0)
            expect(page.get_by_label("ID o URL de item para probar detalle", exact=True)).to_have_count(0)

            create_name = page.get_by_placeholder("Nombre del monitor", exact=True)
            create_url = page.get_by_placeholder("URL de catalogo Vinted", exact=True)
            create_name.fill(scenario.created_name)
            create_url.fill(scenario.created_url)
            create_path = "/api/monitors"
            with page.expect_response(
                lambda response: response.request.method == "POST" and urlsplit(response.url).path == create_path,
                timeout=10_000,
            ) as created:
                page.locator("form.source-form").evaluate(
                    "form => { form.requestSubmit(); form.requestSubmit(); }"
                )
            assert created.value.status == 201
            page.wait_for_function("window.__qaMonitorCreatedId !== null")
            created_source_id = int(page.evaluate("window.__qaMonitorCreatedId"))

            page.wait_for_timeout(250)
            assert len(monitor_posts) == 1
            expect(page.get_by_role("button", name="Guardando...", exact=True)).to_be_disabled()
            expect(create_name).to_be_disabled()
            expect(create_url).to_be_disabled()
            expect(page.get_by_label("Nombre", exact=True)).to_be_disabled()
            expect(page.get_by_label("URL de catalogo", exact=True)).to_be_disabled()
            expect(archive_button).to_be_disabled()
            expect(start_button).to_be_disabled()
            _assert_created_row(scenario, created_source_id)

            faults["stats_source_id"] = created_source_id
            page.evaluate("window.__qaReleaseMonitorCreateResponse()")
            expect(page.get_by_text(CREATE_REFRESH_MESSAGE, exact=True)).to_be_visible()
            assert faults["stats_aborted"] == 1
            expect(create_name).to_have_value("")
            expect(create_url).to_have_value("")
            created_row = page.get_by_role("button", name=f"{scenario.created_name}, inactivo", exact=True)
            expect(created_row).to_be_visible()
            assert _source_ids_from_api(context, api_url, pwa_url).count(created_source_id) == 1

            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
            page.evaluate("window.__qaHoldNextMonitorSources = true")
            page.get_by_role("button", name="Monitores", exact=True).click()
            page.wait_for_function("window.__qaMonitorSourcesHeld === true")
            created_row = page.get_by_role("button", name=f"{scenario.created_name}, inactivo", exact=True)
            expect(created_row).to_be_visible()
            created_row.click()
            archive_button = page.get_by_role("button", name="Archivar monitor", exact=True)
            expect(archive_button).to_be_enabled()

            archive_button.click()
            dialog = page.get_by_role("dialog", name="Archivar monitor")
            expect(dialog).to_be_visible()
            delete_path = f"/api/monitors/{created_source_id}"
            with page.expect_response(
                lambda response: response.request.method == "DELETE" and urlsplit(response.url).path == delete_path,
                timeout=10_000,
            ) as archived:
                dialog.get_by_role("button", name="Archivar monitor", exact=True).click()
            assert archived.value.status == 204
            assert len(monitor_deletes) == 1
            _assert_archived_row(created_source_id)
            expect(created_row).to_be_visible()
            expect(page.get_by_label("Nombre", exact=True)).to_be_disabled()

            faults["abort_runs"] = True
            page.evaluate("window.__qaReleaseMonitorArchiveResponse()")
            expect(page.get_by_text(ARCHIVE_REFRESH_MESSAGE, exact=True)).to_be_visible()
            assert faults["runs_aborted"] == 1
            expect(created_row).to_have_count(0)
            assert created_source_id not in _source_ids_from_api(context, api_url, pwa_url)

            page.evaluate("window.__qaReleaseMonitorSourcesResponse()")
            page.wait_for_timeout(250)
            expect(created_row).to_have_count(0)

            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
            page.get_by_role("button", name="Monitores", exact=True).click()
            expect(page.get_by_role("button", name=f"{scenario.seed_source_name}, inactivo", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name=f"{scenario.created_name}, inactivo", exact=True)).to_have_count(0)
            assert created_source_id not in _source_ids_from_api(context, api_url, pwa_url)
            _assert_archived_row(created_source_id)
        finally:
            context.close()
            browser.close()

    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)
    return created_source_id


def _response_delivery_gates() -> str:
    return """
        (() => {
          const originalFetch = window.fetch.bind(window);
          let releaseCreate;
          let releaseArchive;
          let releaseSources;
          const createGate = new Promise((resolve) => { releaseCreate = resolve; });
          const archiveGate = new Promise((resolve) => { releaseArchive = resolve; });
          const sourcesGate = new Promise((resolve) => { releaseSources = resolve; });
          window.__qaMonitorCreatedId = null;
          window.__qaHoldNextMonitorSources = false;
          window.__qaMonitorSourcesHeld = false;
          window.__qaReleaseMonitorCreateResponse = () => releaseCreate();
          window.__qaReleaseMonitorArchiveResponse = () => releaseArchive();
          window.__qaReleaseMonitorSourcesResponse = () => releaseSources();
          window.fetch = async (...args) => {
            const input = args[0];
            const init = args[1] ?? {};
            const method = String(init.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
            const rawUrl = input instanceof Request ? input.url : String(input);
            const path = new URL(rawUrl, window.location.href).pathname;
            const response = await originalFetch(...args);
            if (method === 'GET' && path === '/api/monitors' && window.__qaHoldNextMonitorSources) {
              window.__qaHoldNextMonitorSources = false;
              window.__qaMonitorSourcesHeld = true;
              await sourcesGate;
            }
            if (method === 'POST' && path === '/api/monitors') {
              const payload = await response.clone().json();
              window.__qaMonitorCreatedId = payload.id;
              await createGate;
            }
            if (method === 'DELETE' && path.startsWith('/api/monitors/')) {
              await archiveGate;
            }
            return response;
          };
        })();
    """


def _source_ids_from_api(context: BrowserContext, api_url: str, origin: str) -> list[int]:
    response = context.request.get(f"{api_url}/api/monitors", headers={"Origin": origin})
    assert response.ok, f"GET /api/monitors returned HTTP {response.status}"
    return [int(source["id"]) for source in response.json()]


def _assert_created_row(scenario: Scenario, source_id: int) -> None:
    with SessionLocal() as db:
        assert db.scalar(
            select(func.count()).select_from(SearchSource).where(SearchSource.name == scenario.created_name)
        ) == 1
        source = db.get(SearchSource, source_id)
        assert source is not None
        assert source.name == scenario.created_name
        assert source.url == scenario.created_url
        assert source.archived_at is None
        assert source.is_active is False


def _assert_archived_row(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        assert source.archived_at is not None
        assert source.is_active is False


def _assert_no_runtime_graph(source_ids: tuple[int, ...]) -> None:
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Run).where(Run.source_id.in_(source_ids))) == 0
        assert db.scalar(
            select(func.count()).select_from(MonitorSession).where(MonitorSession.source_id.in_(source_ids))
        ) == 0
        assert db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.source_id.in_(source_ids))) == 0
        assert db.scalar(
            select(func.count()).select_from(Opportunity).where(Opportunity.source_id.in_(source_ids))
        ) == 0
        assert db.scalar(
            select(func.count()).select_from(VintedSession).where(VintedSession.source_id.in_(source_ids))
        ) == 0


def _cleanup(scenario: Scenario, created_source_id: int | None) -> None:
    with SessionLocal() as db:
        source_ids = {scenario.seed_source_id}
        if created_source_id is not None:
            source_ids.add(created_source_id)
        source_ids.update(db.scalars(select(SearchSource.id).where(SearchSource.name == scenario.created_name)).all())
        db.execute(delete(UserSession).where(UserSession.user_id == scenario.user_id))
        db.execute(delete(SearchSource).where(SearchSource.id.in_(source_ids)))
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
