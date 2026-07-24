from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Page, Route, expect, sync_playwright
from sqlalchemy import delete

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import ProxyProfile, SearchSource, User, UserSession, VintedSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession
from vinted_monitor.services.local_auth import create_local_user
from vinted_monitor.services.proxies import create_proxy_profile
from vinted_monitor.services.search_sources import create_source
from vinted_monitor.services.vinted_sessions import save_prepared_vinted_session

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PASSWORD = "paused-proxy-editing-password"


@dataclass(frozen=True)
class Scenario:
    token: str
    email: str
    user_id: int
    proxy_id: int
    proxy_name: str
    edited_proxy_name: str
    source_id: int
    session_id: int
    password_ciphertext: str
    identity_generation: int
    identity_fingerprint: str
    context_ciphertext: str


def test_live_paused_proxy_editing_contract() -> None:
    api_url = _loopback_origin("PROXY_STICKY_QA_API_URL")
    pwa_url = _loopback_origin("PROXY_STICKY_QA_PWA_URL")
    settings = get_settings()
    assert settings.scheduler_enabled is False
    assert settings.vinted_datadome_collector_enabled is False
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
    settings = get_settings()
    proxy_secret = f"qa-proxy-secret-{token}"
    context_secret = f"qa-context-secret-{token}"
    with SessionLocal() as db:
        user = create_local_user(
            db,
            email=f"qa-paused-proxy-{token}@example.local",
            password=PASSWORD,
        )
        proxy = create_proxy_profile(
            db,
            name=f"qa paused proxy {token}",
            scheme="http",
            kind="residential",
            host="127.0.0.1",
            port=9,
            username=f"qa-{token}",
            password=proxy_secret,
            country_code="ES",
            sticky_username_template="{username};sessid.{session_id}",
            sticky_ttl_minutes=25,
            max_concurrent_runs=1,
            is_active=True,
            settings=settings,
        )
        source = create_source(
            db,
            f"qa paused proxy source {token}",
            f"https://www.vinted.es/catalog?search_text=qa-paused-proxy-{token}",
        )
        browser_profile = profile_for_impersonate(settings.curl_impersonate_browser)
        session = save_prepared_vinted_session(
            db,
            source,
            proxy,
            proxy_session_id=f"qa-sticky-{token}",
            profile=browser_profile,
            context=PreparedCatalogSession(
                proxy_session_id=f"qa-sticky-{token}",
                cookies={
                    name: context_secret
                    for name in ("anon_id", "access_token_web", "datadome", "__cf_bm", "v_udt")
                },
                csrf_token=context_secret,
                anon_id=context_secret,
                access_token_web=context_secret,
                datadome=context_secret,
                cf_bm=context_secret,
                v_udt=context_secret,
                user_iso_locale=proxy.locale,
                vinted_screen=proxy.vinted_screen,
                egress_ip="127.0.0.1",
                egress_country_code=proxy.country_code,
                egress_validated_at=datetime.now(UTC),
            ),
            settings=settings,
        )
        db.commit()
        assert proxy.password_encrypted is not None
        assert proxy.identity_fingerprint is not None
        return Scenario(
            token=token,
            email=user.email,
            user_id=user.id,
            proxy_id=proxy.id,
            proxy_name=proxy.name,
            edited_proxy_name=f"qa edited proxy {token}",
            source_id=source.id,
            session_id=session.id,
            password_ciphertext=proxy.password_encrypted,
            identity_generation=proxy.identity_generation,
            identity_fingerprint=proxy.identity_fingerprint,
            context_ciphertext=session.context_encrypted,
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
            csrf_token = _login(page, scenario, pwa_url)

            page.get_by_role("button", name="Ajustes", exact=True).click()
            active_row = _proxy_row(page, scenario.proxy_name)
            expect(active_row.get_by_text("Activo", exact=True)).to_be_visible()
            expect(active_row.get_by_text("Pausa el proxy para editar su configuracion.")).to_be_visible()
            expect(active_row.get_by_role("button", name="Pausar", exact=True)).to_be_enabled()
            assert active_row.locator("input, select").count() == 0
            assert active_row.get_by_role("button", name="Guardar cambios", exact=True).count() == 0
            assert active_row.get_by_role("button", name="Activar", exact=True).count() == 0

            rejected = context.request.patch(
                f"{api_url}/api/proxy-profiles/{scenario.proxy_id}",
                headers={"Origin": pwa_url, "X-CSRF-Token": csrf_token},
                data={"is_active": False, "host": "127.0.0.2"},
            )
            assert rejected.status == 409
            assert rejected.json()["detail"] == "Pausa el proxy antes de editar su configuracion"
            _assert_rejected_update_did_not_mutate(scenario)

            with page.expect_response(
                lambda response: response.request.method == "PATCH"
                and urlsplit(response.url).path == f"/api/proxy-profiles/{scenario.proxy_id}"
            ) as pause_info:
                active_row.get_by_role("button", name="Pausar", exact=True).click()
            assert pause_info.value.status == 200
            assert pause_info.value.json()["is_active"] is False

            paused_row = _proxy_row(page, scenario.proxy_name)
            expect(paused_row.get_by_text("Pausado", exact=True)).to_be_visible()
            expect(paused_row.get_by_role("button", name="Guardar cambios", exact=True)).to_be_visible()
            expect(paused_row.get_by_role("button", name="Activar", exact=True)).to_be_enabled()
            assert paused_row.locator("input, select").count() == 11

            paused_row.get_by_label("Nombre", exact=True).fill(scenario.edited_proxy_name)
            paused_row.locator("select").nth(0).select_option("datacenter")
            paused_row.locator("select").nth(1).select_option("https")
            paused_row.get_by_label("Host", exact=True).fill("127.0.0.2")
            paused_row.get_by_label("Puerto", exact=True).fill("18080")
            paused_row.get_by_label("Limite local", exact=True).fill("3")
            paused_row.get_by_label("Plantilla sticky", exact=True).fill(
                "{username}-qa-{session_id}"
            )
            paused_row.get_by_label("TTL sticky (min)", exact=True).fill("7")
            paused_row.get_by_label("Pais", exact=True).fill("ES")
            paused_row.get_by_label("Usuario", exact=True).fill(f"qa-edited-{scenario.token}")
            expect(paused_row.get_by_label("Password", exact=True)).to_have_value("")
            expect(paused_row.get_by_role("button", name="Activar", exact=True)).to_be_disabled()

            with page.expect_response(
                lambda response: response.request.method == "PATCH"
                and urlsplit(response.url).path == f"/api/proxy-profiles/{scenario.proxy_id}"
            ) as edit_info:
                paused_row.get_by_role("button", name="Guardar cambios", exact=True).click()
            assert edit_info.value.status == 200
            edited = edit_info.value.json()
            assert edited["name"] == scenario.edited_proxy_name
            assert edited["kind"] == "datacenter"
            assert edited["scheme"] == "https"
            assert edited["host"] == "127.0.0.2"
            assert edited["port"] == 18080
            assert edited["max_concurrent_runs"] == 3
            assert edited["sticky_username_template"] == "{username}-qa-{session_id}"
            assert edited["sticky_ttl_minutes"] == 7
            assert edited["country_code"] == "ES"
            assert edited["username"] == f"qa-edited-{scenario.token}"
            assert edited["has_password"] is True
            assert edited["is_active"] is False
            assert "password" not in edited
            _assert_paused_edit_persisted(scenario)

            edited_row = _proxy_row(page, scenario.edited_proxy_name)
            expect(edited_row.get_by_label("Password", exact=True)).to_have_value("")
            expect(edited_row.get_by_role("button", name="Activar", exact=True)).to_be_enabled()
            with page.expect_response(
                lambda response: response.request.method == "PATCH"
                and urlsplit(response.url).path == f"/api/proxy-profiles/{scenario.proxy_id}"
            ) as activation_info:
                edited_row.get_by_role("button", name="Activar", exact=True).click()
            assert activation_info.value.status == 200
            assert activation_info.value.json()["is_active"] is True

            final_row = _proxy_row(page, scenario.edited_proxy_name)
            expect(final_row.get_by_text("Activo", exact=True)).to_be_visible()
            expect(final_row.get_by_role("button", name="Pausar", exact=True)).to_be_enabled()
            assert final_row.locator("input, select").count() == 0
            _assert_separate_activation_persisted(scenario)

            proxies = _get_json(context, f"{api_url}/api/proxy-profiles", pwa_url)
            current = next(profile for profile in proxies if profile["id"] == scenario.proxy_id)
            assert current["name"] == scenario.edited_proxy_name
            assert current["is_active"] is True
            assert current["has_password"] is True
            assert "password" not in current
        finally:
            context.close()
            browser.close()

    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


def _assert_rejected_update_did_not_mutate(scenario: Scenario) -> None:
    with SessionLocal() as db:
        profile = db.get(ProxyProfile, scenario.proxy_id)
        session = db.get(VintedSession, scenario.session_id)
        assert profile is not None and session is not None
        assert profile.is_active is True
        assert profile.host == "127.0.0.1"
        assert profile.identity_generation == scenario.identity_generation
        assert profile.identity_fingerprint == scenario.identity_fingerprint
        assert hmac.compare_digest(profile.password_encrypted or "", scenario.password_ciphertext)
        assert session.status == "ready"
        assert session.invalidated_at is None
        assert session.context_encrypted == scenario.context_ciphertext


def _assert_paused_edit_persisted(scenario: Scenario) -> None:
    with SessionLocal() as db:
        profile = db.get(ProxyProfile, scenario.proxy_id)
        session = db.get(VintedSession, scenario.session_id)
        assert profile is not None and session is not None
        assert profile.name == scenario.edited_proxy_name
        assert profile.kind == "datacenter"
        assert profile.scheme == "https"
        assert profile.host == "127.0.0.2"
        assert profile.port == 18080
        assert profile.max_concurrent_runs == 3
        assert profile.sticky_username_template == "{username}-qa-{session_id}"
        assert profile.sticky_ttl_minutes == 7
        assert profile.country_code == "ES"
        assert profile.username == f"qa-edited-{scenario.token}"
        assert profile.is_active is False
        assert hmac.compare_digest(profile.password_encrypted or "", scenario.password_ciphertext)
        assert profile.identity_generation == scenario.identity_generation + 1
        assert profile.identity_fingerprint != scenario.identity_fingerprint
        assert session.status == "invalid"
        assert session.invalidated_at is not None
        assert session.context_encrypted != scenario.context_ciphertext


def _assert_separate_activation_persisted(scenario: Scenario) -> None:
    with SessionLocal() as db:
        profile = db.get(ProxyProfile, scenario.proxy_id)
        session = db.get(VintedSession, scenario.session_id)
        assert profile is not None and session is not None
        assert profile.is_active is True
        assert profile.identity_generation == scenario.identity_generation + 1
        assert session.status == "invalid"


def _login(page: Page, scenario: Scenario, pwa_url: str) -> str:
    page.goto(pwa_url, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
    page.get_by_label("Email").fill(scenario.email)
    page.get_by_label("Password").fill(PASSWORD)
    with page.expect_response(
        lambda response: urlsplit(response.url).path == "/api/auth/login"
    ) as info:
        page.get_by_role("button", name="Entrar").click()
    payload = info.value.json()
    expect(page.get_by_role("button", name="Monitores", exact=True)).to_be_visible()
    return str(payload["csrf_token"])


def _proxy_row(page: Page, name: str):
    row = page.locator("article.proxy-row")
    expect(row).to_have_count(1)
    expect(row.get_by_text(name, exact=True)).to_be_visible()
    return row


def _get_json(context: BrowserContext, url: str, origin: str):
    _assert_loopback(url)
    response = context.request.get(url, headers={"Origin": origin})
    assert response.ok, f"GET {urlsplit(url).path} returned HTTP {response.status}"
    return response.json()


def _cleanup(scenario: Scenario) -> None:
    with SessionLocal() as db:
        db.execute(delete(VintedSession).where(VintedSession.source_id == scenario.source_id))
        db.execute(delete(SearchSource).where(SearchSource.id == scenario.source_id))
        db.execute(delete(ProxyProfile).where(ProxyProfile.id == scenario.proxy_id))
        db.execute(delete(UserSession).where(UserSession.user_id == scenario.user_id))
        db.execute(delete(User).where(User.id == scenario.user_id))
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
