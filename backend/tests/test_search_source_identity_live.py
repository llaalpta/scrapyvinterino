from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
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
PASSWORD = "monitor-identity-live-password"


@dataclass(frozen=True)
class Scenario:
    email: str
    source_id: int
    source_name: str
    user_id: int


def test_live_monitor_identity_editing_contract() -> None:
    api_url = _loopback_origin("MONITOR_IDENTITY_QA_API_URL")
    pwa_url = _loopback_origin("MONITOR_IDENTITY_QA_PWA_URL")
    settings = get_settings()
    assert settings.scheduler_enabled is False
    assert settings.vinted_direct_catalog_enabled is False
    assert settings.vinted_datadome_collector_enabled is False
    for endpoint in (settings.vinted_base_url, settings.vinted_datadome_collector_url, settings.egress_diagnostic_url):
        assert urlsplit(str(endpoint)).hostname in LOOPBACK_HOSTS

    scenario = _seed()
    try:
        _exercise_live_stack(scenario, api_url=api_url, pwa_url=pwa_url)
        _assert_no_runtime_graph(scenario.source_id)
    finally:
        _cleanup(scenario)


def _seed() -> Scenario:
    token = uuid4().hex
    source_name = f"qa identity {token}"
    source_url = f"https://www.vinted.es/catalog?search_text=before-{token}"
    with SessionLocal() as db:
        user = create_local_user(db, email=f"qa-identity-{token}@example.local", password=PASSWORD)
        source = create_source(db, source_name, source_url)
        return Scenario(
            email=user.email,
            source_id=source.id,
            source_name=source.name,
            user_id=user.id,
        )


def _exercise_live_stack(scenario: Scenario, *, api_url: str, pwa_url: str) -> None:
    edited_name = f"qa identity renamed {scenario.source_id}"
    edited_url = f"https://www.vinted.es/catalog?search_text=after-{scenario.source_id}&brand_ids[]=88"
    seen_urls: list[str] = []
    blocked_urls: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("MONITOR_IDENTITY_QA_BROWSER_CHANNEL", "chrome"),
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
            page.goto(pwa_url, wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
            page.get_by_label("Email").fill(scenario.email)
            page.get_by_label("Password").fill(PASSWORD)
            page.get_by_role("button", name="Entrar").click()
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()

            page.get_by_role("button", name="Monitores", exact=True).click()
            page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True).click()
            name_input = page.get_by_label("Nombre", exact=True)
            url_input = page.get_by_label("URL de catalogo", exact=True)
            expect(name_input).to_be_enabled()
            expect(url_input).to_be_enabled()
            name_input.fill(f"  {edited_name}  ")
            url_input.fill(f"  {edited_url}  ")

            patch_path = f"/api/monitors/{scenario.source_id}"
            with page.expect_response(
                lambda response: response.request.method == "PATCH" and urlsplit(response.url).path == patch_path,
                timeout=10_000,
            ) as saved:
                save_button = page.get_by_role("button", name="Guardar", exact=True)
                expect(save_button).to_be_enabled()
                save_button.click()
            assert saved.value.status == 200
            saved_payload = saved.value.json()
            assert saved_payload["id"] == scenario.source_id
            assert saved_payload["name"] == edited_name
            assert saved_payload["url"] == edited_url
            assert saved_payload["normalized_query"] == {
                "brand_ids[]": ["88"],
                "search_text": [f"after-{scenario.source_id}"],
            }
            expect(page.get_by_role("button", name=f"{edited_name}, inactivo", exact=True)).to_be_visible()
            expect(name_input).to_have_value(edited_name)
            expect(url_input).to_have_value(edited_url)
            expect(page.get_by_role("link", name=edited_url, exact=True)).to_be_visible()

            api_source = next(
                source
                for source in _get_json(context, f"{api_url}/api/monitors", pwa_url)
                if source["id"] == scenario.source_id
            )
            assert api_source["name"] == edited_name
            assert api_source["url"] == edited_url
            _assert_persisted_identity(scenario.source_id, edited_name, edited_url)

            overlong_name = "n" * 161
            name_input.fill(overlong_name)
            with page.expect_response(
                lambda response: response.request.method == "PATCH" and urlsplit(response.url).path == patch_path,
                timeout=10_000,
            ) as rejected:
                page.get_by_role("button", name="Guardar", exact=True).click()
            assert rejected.value.status == 422
            expect(page.get_by_text("Search source name cannot exceed 160 characters", exact=False)).to_be_visible()
            expect(name_input).to_have_value(overlong_name)
            _assert_persisted_identity(scenario.source_id, edited_name, edited_url)

            _mark_active(scenario.source_id)
            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
            page.get_by_role("button", name="Monitores", exact=True).click()
            page.get_by_role("button", name=f"{edited_name}, activo", exact=True).click()
            expect(page.get_by_label("Nombre", exact=True)).to_be_disabled()
            expect(page.get_by_label("URL de catalogo", exact=True)).to_be_disabled()
            assert page.get_by_role("button", name="Guardar", exact=True).count() == 0
        finally:
            context.close()
            browser.close()

    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


def _get_json(context: BrowserContext, url: str, origin: str):
    _assert_loopback(url)
    response = context.request.get(url, headers={"Origin": origin})
    assert response.ok, f"GET {urlsplit(url).path} returned HTTP {response.status}"
    return response.json()


def _assert_persisted_identity(source_id: int, name: str, url: str) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        assert source.id == source_id
        assert source.name == name
        assert source.url == url
        assert source.normalized_query == {"brand_ids[]": ["88"], "search_text": [f"after-{source_id}"]}


def _mark_active(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = True
        source.monitor_started_at = datetime.now(UTC)
        db.commit()


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
