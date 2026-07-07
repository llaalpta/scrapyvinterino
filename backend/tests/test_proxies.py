from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from vinted_monitor.api.main import app
from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import ProxyProfile
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.proxies import create_proxy_profile, proxy_url_with_sticky_session, resolve_proxy_context, update_proxy_profile


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
    with pytest.raises(ValueError, match="must include"):
        proxy_url_with_sticky_session(
            proxy_profile(),
            "session-123",
            Settings(proxy_sticky_username_template="{username}"),
        )


def test_proxy_url_with_sticky_session_keeps_plain_proxy_without_username() -> None:
    url = proxy_url_with_sticky_session(proxy_profile(username=None), "session-123", Settings())

    assert url == "http://proxy.example:7777"


def test_proxy_context_is_resolved_from_country_preset() -> None:
    context = resolve_proxy_context("FR")

    assert context.country_code == "FR"
    assert context.locale == "fr-FR"
    assert context.accept_language == "fr-FR,fr;q=0.9,en;q=0.8"
    assert context.screen == "1920x1080"


def test_create_proxy_profile_resolves_context_from_country() -> None:
    with SessionLocal() as db:
        profile = create_proxy_profile(
            db,
            name=unique_name("geo context proxy create"),
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=7777,
            username=None,
            password=None,
            country_code="FR",
        )
        try:
            assert profile.country_code == "FR"
            assert profile.locale == "fr-FR"
            assert profile.accept_language == "fr-FR,fr;q=0.9,en;q=0.8"
            assert profile.screen == "1920x1080"
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
            username=None,
            password=None,
        )
        try:
            updated = update_proxy_profile(db, profile.id, country_code="IT")

            assert updated.country_code == "IT"
            assert updated.locale == "it-IT"
            assert updated.accept_language == "it-IT,it;q=0.9,en;q=0.8"
            assert updated.screen == "1920x1080"
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
                username=None,
                password=None,
                country_code="ZZ",
            )


def test_proxy_profile_api_rejects_manual_context_fields() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/proxy-profiles",
        json={
            "name": "pytest legacy manual context proxy",
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7780,
            "country_code": "ES",
            "locale": "es-ES",
            "accept_language": "es-ES,es;q=0.9,en;q=0.8",
            "screen": "1920x1080",
        },
    )

    assert response.status_code == 422


def test_proxy_profile_api_rejects_manual_context_update_fields() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api legacy context update proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7782,
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
            assert profile.accept_language == "es-ES,es;q=0.9,en;q=0.8"
            assert profile.screen == "1920x1080"
    finally:
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, payload["id"])
            if profile is not None:
                db.delete(profile)
                db.commit()


def test_proxy_profile_api_resolves_context_from_country_only() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/proxy-profiles",
        json={
            "name": unique_name("api country context proxy"),
            "scheme": "http",
            "kind": "residential",
            "host": "proxy.example",
            "port": 7781,
            "country_code": "PT",
        },
    )
    assert response.status_code == 201
    payload = response.json()

    try:
        assert payload["country_code"] == "PT"
        assert payload["locale"] == "pt-PT"
        assert payload["accept_language"] == "pt-PT,pt;q=0.9,en;q=0.8"
        assert payload["screen"] == "1920x1080"
    finally:
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, payload["id"])
            if profile is not None:
                db.delete(profile)
                db.commit()
