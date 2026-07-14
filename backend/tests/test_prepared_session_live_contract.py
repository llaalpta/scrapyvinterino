from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Route, expect, sync_playwright
from sqlalchemy import delete, func, select

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import ProxyProfile, Run, RunEvent, SearchSource, User, UserSession, VintedSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession
from vinted_monitor.services.local_auth import create_local_user
from vinted_monitor.services.proxies import create_proxy_profile
from vinted_monitor.services.search_sources import create_source
from vinted_monitor.services.vinted_sessions import get_ready_vinted_session, save_prepared_vinted_session

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PASSWORD = "prepared-session-live-password"


@dataclass(frozen=True)
class Scenario:
    email: str
    user_id: int
    proxy_id: int
    proxy_name: str
    source_id: int
    source_name: str
    canonical_id: int
    compatible_non_lru_id: int
    incompatible_id: int
    expiry_source_id: int
    expiry_source_name: str
    expiry_id: int
    ciphertexts: dict[int, str]
    secret: str


def test_live_prepared_session_read_model_matches_runtime_and_pwa() -> None:
    api_url = _loopback_origin("PREPARED_SESSION_QA_API_URL")
    pwa_url = _loopback_origin("PREPARED_SESSION_QA_PWA_URL")
    settings = get_settings()
    assert settings.scheduler_enabled is False
    assert settings.vinted_direct_catalog_enabled is False
    assert settings.vinted_datadome_collector_enabled is False
    for endpoint in (settings.vinted_base_url, settings.vinted_datadome_collector_url, settings.egress_diagnostic_url):
        assert urlsplit(str(endpoint)).hostname in LOOPBACK_HOSTS

    scenario = _seed()
    try:
        with SessionLocal() as db:
            source = db.get(SearchSource, scenario.source_id)
            proxy = db.get(ProxyProfile, scenario.proxy_id)
            assert source is not None and proxy is not None
            selected, _context = get_ready_vinted_session(db, source, proxy, settings=settings)
            assert selected.id == scenario.canonical_id
            assert selected.id != scenario.compatible_non_lru_id
            db.rollback()

        _exercise_live_stack(scenario, api_url=api_url, pwa_url=pwa_url)
        _assert_read_only(scenario)
    finally:
        _cleanup(scenario)


def _seed() -> Scenario:
    token = uuid4().hex
    secret = f"qa-secret-{token}"
    settings = get_settings()
    now = datetime.now(UTC)
    with SessionLocal() as db:
        user = create_local_user(db, email=f"qa-prepared-{token}@example.local", password=PASSWORD)
        proxy = create_proxy_profile(
            db,
            name=f"qa prepared proxy {token}",
            scheme="http",
            host="127.0.0.1",
            port=9,
            username=f"qa-{token}",
            password=secret,
            country_code="ES",
            settings=settings,
        )
        source = create_source(db, f"qa prepared canonical {token}", f"https://www.vinted.es/catalog?search_text=qa-{token}")
        expiry_source = create_source(db, f"qa prepared expiry {token}", f"https://www.vinted.es/catalog?search_text=qa-exp-{token}")
        profile = profile_for_impersonate(settings.curl_impersonate_browser)
        canonical = _save(db, source, proxy, profile, f"{secret}-canonical", secret)
        canonical.prepared_at = now - timedelta(minutes=5)
        canonical.expires_at = now + timedelta(minutes=20)
        compatible_non_lru = _save(db, source, proxy, profile, f"{secret}-recent", secret)
        compatible_non_lru.prepared_at = now - timedelta(minutes=4)
        compatible_non_lru.last_used_at = now - timedelta(minutes=2)
        compatible_non_lru.expires_at = now + timedelta(minutes=20)
        incompatible = _save(db, source, proxy, profile, f"{secret}-latest", secret)
        incompatible.prepared_at = now - timedelta(minutes=1)
        incompatible.viewport_size = "1366x768"
        incompatible.expires_at = now + timedelta(minutes=20)
        expiry = _save(db, expiry_source, proxy, profile, f"{secret}-expiry", secret)
        expiry.expires_at = now + timedelta(minutes=20)
        db.commit()
        rows = (canonical, compatible_non_lru, incompatible, expiry)
        return Scenario(
            email=user.email,
            user_id=user.id,
            proxy_id=proxy.id,
            proxy_name=proxy.name,
            source_id=source.id,
            source_name=source.name,
            canonical_id=canonical.id,
            compatible_non_lru_id=compatible_non_lru.id,
            incompatible_id=incompatible.id,
            expiry_source_id=expiry_source.id,
            expiry_source_name=expiry_source.name,
            expiry_id=expiry.id,
            ciphertexts={row.id: row.context_encrypted for row in rows},
            secret=secret,
        )


def _save(db, source, proxy, profile, sticky: str, secret: str) -> VintedSession:
    return save_prepared_vinted_session(
        db,
        source,
        proxy,
        proxy_session_id=sticky,
        profile=profile,
        context=PreparedCatalogSession(
            proxy_session_id=sticky,
            cookies={name: secret for name in ("anon_id", "access_token_web", "datadome", "__cf_bm", "v_udt")},
            csrf_token=secret,
            anon_id=secret,
            access_token_web=secret,
            datadome=secret,
            cf_bm=secret,
            v_udt=secret,
            user_iso_locale=proxy.locale,
            vinted_screen=proxy.vinted_screen,
            egress_ip="127.0.0.1",
            egress_country_code=proxy.country_code,
            egress_validated_at=datetime.now(UTC),
        ),
        settings=get_settings(),
    )


def _exercise_live_stack(scenario: Scenario, *, api_url: str, pwa_url: str) -> None:
    seen_urls: list[str] = []
    blocked_urls: list[str] = []
    navigations: list[str] = []
    monitor_reads: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("PREPARED_SESSION_QA_BROWSER_CHANNEL", "chrome"),
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
            page.on(
                "request",
                lambda request: monitor_reads.append(request.url)
                if request.method == "GET" and urlsplit(request.url).path == "/api/monitors"
                else None,
            )
            page.on("framenavigated", lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
            page.on("websocket", lambda socket: _assert_loopback(socket.url))
            page.goto(pwa_url, wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
            page.get_by_label("Email").fill(scenario.email)
            page.get_by_label("Password").fill(PASSWORD)
            page.get_by_role("button", name="Entrar").click()
            expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()

            monitors = _get_json(context, f"{api_url}/api/monitors", pwa_url)
            canonical = _source(monitors, scenario.source_id)["prepared_sessions"]
            assert len(canonical) == 1
            assert canonical[0]["id"] == scenario.canonical_id
            assert canonical[0]["proxy_name"] == scenario.proxy_name
            assert canonical[0]["usable_now"] is True
            assert canonical[0]["unusable_reason"] is None
            assert scenario.incompatible_id > scenario.canonical_id
            proxies = _get_json(context, f"{api_url}/api/proxy-profiles", pwa_url)
            assert len(proxies) == 1 and "vinted_session" not in proxies[0]
            assert scenario.secret not in json.dumps({"monitors": monitors, "proxies": proxies})

            page.get_by_role("button", name="Monitores", exact=True).click()
            page.get_by_role("button", name=f"{scenario.source_name}, inactivo", exact=True).click()
            panel = page.get_by_role("region", name="Sesiones Vinted preparadas para este monitor")
            expect(panel.get_by_text(f"Sesion #{scenario.canonical_id}", exact=False)).to_be_visible()
            expect(panel.get_by_text("Utilizable ahora", exact=True)).to_be_visible()
            assert panel.get_by_text(f"Sesion #{scenario.incompatible_id}", exact=False).count() == 0

            page.get_by_role("button", name="Ajustes", exact=True).click()
            expect(page.get_by_text(scenario.proxy_name, exact=True)).to_be_visible()
            assert page.get_by_text("Ultima sesion Vinted", exact=False).count() == 0
            assert page.get_by_text(f"Sesion #{scenario.canonical_id}", exact=False).count() == 0
            assert scenario.secret not in page.locator("body").inner_text()

            _expire_soon(scenario.expiry_id)
            expiry = _source(_get_json(context, f"{api_url}/api/monitors", pwa_url), scenario.expiry_source_id)["prepared_sessions"][0]
            assert expiry["id"] == scenario.expiry_id and expiry["usable_now"] is True
            with page.expect_response(lambda response: urlsplit(response.url).path == "/api/monitors", timeout=10_000):
                page.get_by_role("button", name="Monitores", exact=True).click()
            page.get_by_role("button", name=f"{scenario.expiry_source_name}, inactivo", exact=True).click()
            panel = page.get_by_role("region", name="Sesiones Vinted preparadas para este monitor")
            expect(panel.get_by_text("Utilizable ahora", exact=True)).to_be_visible()
            page.wait_for_timeout(400)
            reads_before = len(monitor_reads)
            expect(panel.get_by_text("La sesion ha expirado.", exact=True)).to_be_visible(timeout=12_000)
            reads_after = len(monitor_reads)
            assert 1 <= reads_after - reads_before <= 2
            assert len(navigations) == 1
            page.wait_for_timeout(2_000)
            assert len(monitor_reads) == reads_after
            expired = _source(_get_json(context, f"{api_url}/api/monitors", pwa_url), scenario.expiry_source_id)["prepared_sessions"][0]
            assert expired["id"] == scenario.expiry_id
            assert expired["usable_now"] is False and expired["unusable_reason"] == "expired"
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


def _source(payload: list[dict], source_id: int) -> dict:
    return next(source for source in payload if source["id"] == source_id)


def _expire_soon(session_id: int) -> None:
    with SessionLocal() as db:
        session = db.get(VintedSession, session_id)
        assert session is not None
        session.expires_at = datetime.now(UTC) + timedelta(seconds=7)
        db.commit()


def _assert_read_only(scenario: Scenario) -> None:
    source_ids = (scenario.source_id, scenario.expiry_source_id)
    with SessionLocal() as db:
        rows = list(db.scalars(select(VintedSession).where(VintedSession.id.in_(scenario.ciphertexts))))
        assert all(row.request_count == 0 and row.context_encrypted == scenario.ciphertexts[row.id] for row in rows)
        assert db.scalar(select(func.count()).select_from(Run).where(Run.source_id.in_(source_ids))) == 0
        assert db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.source_id.in_(source_ids))) == 0


def _cleanup(scenario: Scenario) -> None:
    with SessionLocal() as db:
        db.execute(delete(UserSession))
        db.execute(delete(VintedSession).where(VintedSession.id.in_(scenario.ciphertexts)))
        db.execute(delete(SearchSource).where(SearchSource.id.in_((scenario.source_id, scenario.expiry_source_id))))
        db.execute(delete(ProxyProfile).where(ProxyProfile.id == scenario.proxy_id))
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
