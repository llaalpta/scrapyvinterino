from uuid import uuid4

import pytest
from api_client import authenticated_test_client

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import ProxyProfile, VintedSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.proxies import (
    ProxyProfileEligibilityError,
    create_proxy_profile,
    proxy_url_with_sticky_session,
    resolve_proxy_context,
    update_proxy_profile,
)


def proxy_profile(username: str | None = "customer-user") -> ProxyProfile:
    return ProxyProfile(
        name="pytest proxy",
        scheme="http",
        kind="residential",
        host="proxy.example",
        port=7777,
        username=username,
        password_encrypted=None,
        max_concurrent_runs=1,
        is_active=True,
    )


def unique_name(label: str) -> str:
    return f"pytest {label} {uuid4()}"


def test_proxy_url_with_sticky_session_uses_configured_username_template() -> None:
    url = proxy_url_with_sticky_session(
        proxy_profile(),
        "session-123",
        Settings(proxy_sticky_username_template="{username}-sessid-{session_id}"),
    )

    assert url == "http://customer-user-sessid-session-123:@proxy.example:7777"


def test_proxy_url_with_sticky_session_rejects_template_without_session_id() -> None:
    with pytest.raises(ValueError, match="must contain exactly"):
        Settings(proxy_sticky_username_template="{username}")


def test_proxy_url_with_sticky_session_rejects_duplicate_template_fields() -> None:
    with pytest.raises(ValueError, match="must contain exactly"):
        Settings(proxy_sticky_username_template="{username}-{username}-{session_id}")


def test_proxy_url_with_sticky_session_keeps_plain_proxy_without_username() -> None:
    url = proxy_url_with_sticky_session(proxy_profile(username=None), "session-123", Settings())

    assert url == "http://proxy.example:7777"


def test_proxy_context_is_resolved_from_country_preset() -> None:
    context = resolve_proxy_context("FR")

    assert context.country_code == "FR"
    assert context.locale == "fr-FR"
    assert context.accept_language == "fr-FR,fr;q=0.9,en;q=0.8"
    assert context.screen == "1920x1080"
    assert context.vinted_screen == "catalog"


def test_spanish_proxy_context_keeps_har146_accept_language() -> None:
    context = resolve_proxy_context("ES")

    assert context.country_code == "ES"
    assert context.locale == "es-ES"
    assert context.accept_language == "en-GB,en;q=0.9"
    assert context.screen == "1920x1080"
    assert context.vinted_screen == "catalog"


def test_create_proxy_profile_resolves_context_from_country() -> None:
    with SessionLocal() as db:
        profile = create_proxy_profile(
            db,
            name=unique_name("geo context proxy create"),
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=7777,
            username="customer",
            password="test-password",
            country_code="FR",
            settings=Settings(vinted_target_country_code="FR"),
        )
        try:
            assert profile.country_code == "FR"
            assert profile.locale == "fr-FR"
            assert profile.accept_language == "fr-FR,fr;q=0.9,en;q=0.8"
            assert profile.screen == "1920x1080"
            assert profile.vinted_screen == "catalog"
        finally:
            db.delete(profile)
            db.commit()


def test_update_proxy_profile_recomputes_context_from_country() -> None:
    with SessionLocal() as db:
        profile = create_proxy_profile(
            db,
            name=unique_name("geo context proxy update"),
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=7778,
            username="customer",
            password="test-password",
        )
        try:
            update_proxy_profile(db, profile.id, is_active=False)
            updated = update_proxy_profile(db, profile.id, country_code="IT")

            assert updated.country_code == "IT"
            assert updated.locale == "it-IT"
            assert updated.accept_language == "it-IT,it;q=0.9,en;q=0.8"
            assert updated.screen == "1920x1080"
            assert updated.vinted_screen == "catalog"
        finally:
            db.delete(profile)
            db.commit()


def test_create_proxy_profile_rejects_unsupported_country() -> None:
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="Unsupported proxy country_code ZZ"):
            create_proxy_profile(
                db,
                name=unique_name("unsupported country proxy"),
                scheme="http",
                kind="residential",
                host="proxy.example",
                port=7779,
                username="customer",
                password="test-password",
                country_code="ZZ",
            )


def test_proxy_profile_api_rejects_manual_context_fields() -> None:
    client = authenticated_test_client()
    response = client.post(
        "/api/proxy-profiles",
        json={
            "name": "pytest legacy manual context proxy",
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7780,
            "username": "customer",
            "password": "test-password",
            "country_code": "ES",
            "locale": "es-ES",
            "accept_language": "es-ES,es;q=0.9,en;q=0.8",
            "screen": "1920x1080",
        },
    )

    assert response.status_code == 422


def test_proxy_profile_api_rejects_manual_context_update_fields() -> None:
    client = authenticated_test_client()
    create_response = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api legacy context update proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7782,
            "username": "customer",
            "password": "test-password",
            "country_code": "ES",
        },
    )
    assert create_response.status_code == 201
    payload = create_response.json()

    try:
        response = client.patch(
            f"/api/proxy-profiles/{payload['id']}",
            json={"locale": "fr-FR", "accept_language": "fr-FR,fr;q=0.9", "screen": "1366x768"},
        )

        assert response.status_code == 422
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, payload["id"])
            assert profile is not None
            assert profile.country_code == "ES"
            assert profile.locale == "es-ES"
            assert profile.accept_language == "en-GB,en;q=0.9"
            assert profile.screen == "1920x1080"
            assert profile.vinted_screen == "catalog"
    finally:
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, payload["id"])
            if profile is not None:
                db.delete(profile)
                db.commit()


def test_proxy_profile_api_resolves_context_from_target_country() -> None:
    client = authenticated_test_client()
    response = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api country context proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7781,
            "username": "customer",
            "password": "test-password",
            "country_code": "ES",
        },
    )
    assert response.status_code == 201
    payload = response.json()

    try:
        assert payload["country_code"] == "ES"
        assert payload["locale"] == "es-ES"
        assert payload["accept_language"] == "en-GB,en;q=0.9"
        assert payload["screen"] == "1920x1080"
        assert payload["vinted_screen"] == "catalog"
    finally:
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, payload["id"])
            if profile is not None:
                db.delete(profile)
                db.commit()


def test_proxy_profile_api_imports_vinted_session_without_returning_raw_secrets() -> None:
    client = authenticated_test_client()
    create_response = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api vinted session import proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7783,
            "username": "customer",
            "password": "test-password",
            "country_code": "ES",
        },
    )
    assert create_response.status_code == 201
    proxy_payload = create_response.json()

    try:
        response = client.post(
            f"/api/proxy-profiles/{proxy_payload['id']}/vinted-session/import",
            json={
                "proxy_session_id": "pytestimport01",
                "cookie_header": "access_token_web=access-secret; datadome=dd-secret; v_udt=udt-secret; anon_id=anon-secret",
                "csrf_token": "csrf-secret",
                "user_iso_locale": "es-ES",
                "vinted_screen": "catalog",
                "egress_ip": "203.0.113.10",
                "egress_country_code": "ES",
            },
        )

        assert response.status_code == 410
        serialized = response.text
        assert "dd-secret" not in serialized
        assert "csrf-secret" not in serialized
        assert "access-secret" not in serialized
    finally:
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, proxy_payload["id"])
            if profile is not None:
                db.query(VintedSession).filter(VintedSession.proxy_profile_id == profile.id).delete(synchronize_session=False)
                db.delete(profile)
                db.commit()


def test_proxy_profile_test_endpoint_and_telemetry_contract_are_removed() -> None:
    client = authenticated_test_client()
    create_response = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api proxy test success"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7784,
            "username": "customer",
            "password": "test-password",
            "country_code": "ES",
        },
    )
    assert create_response.status_code == 201
    proxy_payload = create_response.json()

    try:
        response = client.post(f"/api/proxy-profiles/{proxy_payload['id']}/test")

        assert response.status_code == 404
        assert "/api/proxy-profiles/{profile_id}/test" not in client.get("/openapi.json").json()["paths"]
        assert not {"last_test_status", "last_test_ip", "last_test_error"}.intersection(proxy_payload)
    finally:
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, proxy_payload["id"])
            if profile is not None:
                db.delete(profile)
                db.commit()


def test_proxy_profile_api_requires_complete_target_configuration() -> None:
    client = authenticated_test_client()
    missing_credentials = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api incomplete proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7785,
            "country_code": "ES",
        },
    )
    wrong_country = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api wrong-country proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7785,
            "username": "customer",
            "password": "test-password",
            "country_code": "FR",
        },
    )

    assert missing_credentials.status_code == 422
    assert wrong_country.status_code == 422
    assert "target country ES" in wrong_country.json()["detail"]


def test_proxy_profile_activation_and_active_password_clear_require_credentials() -> None:
    client = authenticated_test_client()
    incomplete_name = unique_name("api inactive incomplete proxy")
    with SessionLocal() as db:
        incomplete = ProxyProfile(
            name=incomplete_name,
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=7786,
            username=None,
            password_encrypted=None,
            country_code="ES",
            max_concurrent_runs=1,
            is_active=False,
        )
        db.add(incomplete)
        db.commit()
        incomplete_id = incomplete.id

    created = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api complete proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7787,
            "username": "customer",
            "password": "test-password",
            "country_code": "ES",
        },
    )
    assert created.status_code == 201
    complete_id = created.json()["id"]

    try:
        activation = client.patch(f"/api/proxy-profiles/{incomplete_id}", json={"is_active": True})
        clear_password = client.patch(f"/api/proxy-profiles/{complete_id}", json={"clear_password": True})

        assert activation.status_code == 422
        assert activation.json()["detail"] == "Proxy username is required"
        assert clear_password.status_code == 422
        assert clear_password.json()["detail"] == "Proxy password is required"
        with SessionLocal() as db:
            incomplete = db.get(ProxyProfile, incomplete_id)
            complete = db.get(ProxyProfile, complete_id)
            assert incomplete is not None and incomplete.is_active is False
            assert complete is not None and complete.password_encrypted is not None
    finally:
        with SessionLocal() as db:
            for profile_id in (incomplete_id, complete_id):
                profile = db.get(ProxyProfile, profile_id)
                if profile is not None:
                    db.delete(profile)
            db.commit()


def test_create_proxy_profile_rejects_invalid_sticky_template() -> None:
    invalid_settings = Settings().model_copy(update={"proxy_sticky_username_template": "{username}"})
    with SessionLocal() as db:
        with pytest.raises(ProxyProfileEligibilityError, match="sticky username template is invalid"):
            create_proxy_profile(
                db,
                name=unique_name("invalid sticky proxy"),
                scheme="http",
                kind="residential",
                host="proxy.example",
                port=7788,
                username="customer",
                password="test-password",
                country_code="ES",
                settings=invalid_settings,
            )


def test_proxy_profile_catalog_api_probe_endpoint_is_removed() -> None:
    client = authenticated_test_client()
    create_response = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api catalog probe proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7786,
            "username": "customer",
            "password": "test-password",
            "country_code": "ES",
        },
    )
    assert create_response.status_code == 201
    proxy_payload = create_response.json()

    try:
        response = client.post(f"/api/proxy-profiles/{proxy_payload['id']}/catalog-api/probe")

        assert response.status_code == 410
        assert "probe real ocurre dentro" in response.json()["detail"]
        assert "proxy.example" not in response.text
    finally:
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, proxy_payload["id"])
            if profile is not None:
                db.delete(profile)
                db.commit()
