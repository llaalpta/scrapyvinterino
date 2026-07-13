from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from vinted_monitor.api.local_auth import _set_local_session_cookie, auth_router
from vinted_monitor.api.main import app, business_router
from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.db.models import SearchSource, User, UserSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import (
    LOCAL_CSRF_HEADER_NAME,
    LOCAL_SESSION_COOKIE_NAME,
    LocalSessionGrant,
    create_local_user,
)

TEST_PASSWORD = "local-auth-test-password-14-12-1"


@pytest.fixture
def local_user():
    email = f"pytest-auth-{uuid4().hex}@example.local"
    tracked_token_hashes: set[str] = set()
    with SessionLocal() as db:
        user = create_local_user(db, email=email, password=TEST_PASSWORD)
        user_id = user.id
        password_hash = user.password_hash

    def track_client_token(client: TestClient) -> str:
        raw_token = client.cookies.get(LOCAL_SESSION_COOKIE_NAME)
        assert raw_token
        tracked_token_hashes.add(hashlib.sha256(raw_token.encode()).hexdigest())
        return raw_token

    try:
        yield {
            "id": user_id,
            "email": email,
            "password": TEST_PASSWORD,
            "password_hash": password_hash,
            "track_client_token": track_client_token,
        }
    finally:
        with SessionLocal() as db:
            owned_session = UserSession.user_id == user_id
            if tracked_token_hashes:
                owned_session = or_(owned_session, UserSession.token_hash.in_(tracked_token_hashes))
            db.execute(delete(UserSession).where(owned_session))
            user = db.get(User, user_id)
            if user is not None:
                db.delete(user)
            db.commit()


def test_local_user_password_is_argon2_and_bootstrap_cookie_is_opaque(local_user) -> None:
    assert local_user["password_hash"].startswith("$argon2id$")
    client = TestClient(app)

    response = client.get("/api/auth/session")
    local_user["track_client_token"](client)

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.headers["cache-control"] == "no-store"
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/api" in set_cookie
    assert "Domain=" not in set_cookie
    if get_settings().app_env.strip().lower() in {"development", "test"}:
        assert "Secure" not in set_cookie

    raw_token = client.cookies.get(LOCAL_SESSION_COOKIE_NAME)
    assert raw_token
    with SessionLocal() as db:
        session = db.scalar(select(UserSession).where(UserSession.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()))
        assert session is not None
        assert session.user_id is None
        assert session.token_hash != raw_token


@pytest.mark.real_auth
def test_every_registered_business_route_rejects_anonymous_request_before_validation() -> None:
    client = TestClient(app)
    public_paths = {"/api/auth/session", "/api/auth/login"}
    checked: list[tuple[str, str]] = []

    # FastAPI 0.139 keeps included routers lazy in ``app.routes``. Inspect the
    # owning routers so this test still proves every registered business route
    # reaches the authentication dependency before request validation.
    for route in [*business_router.routes, *auth_router.routes]:
        path = getattr(route, "path", "")
        if not path.startswith("/api") or path in public_paths:
            continue
        request_path = re.sub(r"{[^}]+}", "999", path)
        methods = sorted(method for method in (getattr(route, "methods", set()) or set()) if method not in {"OPTIONS"})
        for method in methods:
            response = client.request(method, request_path)
            assert response.status_code == 401, (method, path, response.status_code, response.text)
            checked.append((method, path))

    assert ("GET", "/api/monitors/events/stream") in checked
    assert ("POST", "/api/actions") in checked
    assert ("POST", "/api/proxy-profiles/{profile_id}/vinted-session/preflight") in checked
    assert ("POST", "/api/auth/logout") in checked


@pytest.mark.real_auth
def test_login_rotates_preauth_and_authenticated_sessions(local_user) -> None:
    client = TestClient(app)
    first_bootstrap = client.get("/api/auth/session").json()
    token_a = local_user["track_client_token"](client)

    first_login = client.post(
        "/api/auth/login",
        headers={"Origin": _origin(), LOCAL_CSRF_HEADER_NAME: first_bootstrap["csrf_token"]},
        json={"email": local_user["email"], "password": local_user["password"]},
    )

    assert first_login.status_code == 200
    assert first_login.json()["authenticated"] is True
    token_b = local_user["track_client_token"](client)
    assert token_a and token_b and token_b != token_a
    assert TestClient(app, cookies={LOCAL_SESSION_COOKIE_NAME: token_a}).get("/api/monitors").status_code == 401

    second_login = client.post(
        "/api/auth/login",
        headers={"Origin": _origin(), LOCAL_CSRF_HEADER_NAME: first_login.json()["csrf_token"]},
        json={"email": local_user["email"], "password": local_user["password"]},
    )
    assert second_login.status_code == 200
    token_c = local_user["track_client_token"](client)
    assert token_c and token_c not in {token_a, token_b}
    assert TestClient(app, cookies={LOCAL_SESSION_COOKIE_NAME: token_b}).get("/api/monitors").status_code == 401
    assert client.get("/api/monitors").status_code == 200

    with SessionLocal() as db:
        rows = list(db.scalars(select(UserSession).order_by(UserSession.created_at.asc(), UserSession.id.asc())))
        by_hash = {row.token_hash: row for row in rows}
        assert by_hash[hashlib.sha256(token_a.encode()).hexdigest()].revoked_at is not None
        assert by_hash[hashlib.sha256(token_b.encode()).hexdigest()].revoked_at is not None
        assert by_hash[hashlib.sha256(token_c.encode()).hexdigest()].revoked_at is None


@pytest.mark.real_auth
def test_invalid_login_is_generic_and_does_not_create_authenticated_session(local_user) -> None:
    client = TestClient(app)
    bootstrap = client.get("/api/auth/session").json()
    local_user["track_client_token"](client)

    response = client.post(
        "/api/auth/login",
        headers={"Origin": _origin(), LOCAL_CSRF_HEADER_NAME: bootstrap["csrf_token"]},
        json={"email": local_user["email"], "password": "incorrect-password-value"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == local_user["id"])) == 0


@pytest.mark.real_auth
def test_login_boundary_precedes_body_validation_and_never_reflects_password(local_user) -> None:
    client = TestClient(app)
    bootstrap = client.get("/api/auth/session").json()
    local_user["track_client_token"](client)
    raw_password = "raw-password-canary-" + ("x" * 140)

    missing_origin = client.post(
        "/api/auth/login",
        headers={LOCAL_CSRF_HEADER_NAME: bootstrap["csrf_token"]},
        json={"email": local_user["email"], "password": {"secret": raw_password}},
    )
    invalid_length = client.post(
        "/api/auth/login",
        headers={"Origin": _origin(), LOCAL_CSRF_HEADER_NAME: bootstrap["csrf_token"]},
        json={"email": local_user["email"], "password": raw_password},
    )

    assert missing_origin.status_code == 403
    assert invalid_length.status_code == 401
    assert invalid_length.json()["detail"] == "Invalid email or password"
    assert raw_password not in missing_origin.text
    assert raw_password not in invalid_length.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == local_user["id"])) == 0


@pytest.mark.real_auth
def test_csrf_and_origin_rejections_are_mutation_free(local_user) -> None:
    client, auth = _login(local_user)
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(SearchSource))
    payload = {"name": "must not exist", "url": "https://www.vinted.es/catalog?search_text=auth-reject"}

    missing_csrf = client.post("/api/monitors", headers={"Origin": _origin()}, json=payload)
    invalid_csrf = client.post(
        "/api/monitors",
        headers={"Origin": _origin(), LOCAL_CSRF_HEADER_NAME: "invalid"},
        json=payload,
    )
    hostile_origin = client.post(
        "/api/monitors",
        headers={"Origin": f"{_origin()}.evil.invalid", LOCAL_CSRF_HEADER_NAME: auth["csrf_token"]},
        json=payload,
    )

    assert missing_csrf.status_code == 403
    assert invalid_csrf.status_code == 403
    assert hostile_origin.status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SearchSource)) == before


@pytest.mark.real_auth
def test_logout_revokes_server_state_and_clears_exact_cookie_scope(local_user) -> None:
    client, auth = _login(local_user)
    captured = client.cookies.get(LOCAL_SESSION_COOKIE_NAME)

    response = client.post(
        "/api/auth/logout",
        headers={"Origin": _origin(), LOCAL_CSRF_HEADER_NAME: auth["csrf_token"]},
    )

    assert response.status_code == 204
    set_cookie = response.headers["set-cookie"]
    assert "Max-Age=0" in set_cookie
    assert "Path=/api" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    copied_client = TestClient(app, cookies={LOCAL_SESSION_COOKIE_NAME: captured})
    assert copied_client.get("/api/monitors").status_code == 401
    with SessionLocal() as db:
        session = db.scalar(
            select(UserSession).where(UserSession.token_hash == hashlib.sha256(captured.encode()).hexdigest())
        )
        assert session is not None
        assert session.revoked_at is not None


@pytest.mark.real_auth
def test_expiry_and_inactive_user_fail_closed(local_user) -> None:
    client, _auth = _login(local_user)
    token = client.cookies.get(LOCAL_SESSION_COOKIE_NAME)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with SessionLocal() as db:
        session = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/monitors").status_code == 401

    second_client, _second_auth = _login(local_user)
    with SessionLocal() as db:
        user = db.get(User, local_user["id"])
        user.is_active = False
        db.commit()
    assert second_client.get("/api/monitors").status_code == 401


@pytest.mark.real_auth
def test_authentication_database_failure_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable_session():
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr("vinted_monitor.api.local_auth.SessionLocal", unavailable_session)

    response = TestClient(app).get("/api/monitors")

    assert response.status_code == 503
    assert response.json()["detail"] == "Authentication service unavailable"


def test_production_cookie_is_secure_and_host_only() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        app_secret_key="x" * 32,
        backend_cors_origins="https://monitor.example.test",
    )
    now = datetime.now(UTC)
    grant = LocalSessionGrant(
        raw_token="opaque-test-token",
        expires_at=now + timedelta(hours=1),
        principal=None,
        issued=True,
    )
    response = Response()

    _set_local_session_cookie(response, grant, settings)

    set_cookie = response.headers["set-cookie"]
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/api" in set_cookie
    assert "Domain=" not in set_cookie


def _login(local_user) -> tuple[TestClient, dict]:
    client = TestClient(app)
    bootstrap = client.get("/api/auth/session").json()
    local_user["track_client_token"](client)
    response = client.post(
        "/api/auth/login",
        headers={"Origin": _origin(), LOCAL_CSRF_HEADER_NAME: bootstrap["csrf_token"]},
        json={"email": local_user["email"], "password": local_user["password"]},
    )
    if response.status_code == 200:
        local_user["track_client_token"](client)
    assert response.status_code == 200, response.text
    return client, response.json()


def _origin() -> str:
    return get_settings().cors_origins[0]
