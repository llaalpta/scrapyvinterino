from __future__ import annotations

import hashlib
from threading import Lock

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from vinted_monitor.api.main import app
from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import User, UserSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import (
    LOCAL_CSRF_HEADER_NAME,
    LOCAL_SESSION_COOKIE_NAME,
    create_local_user,
)

TEST_LOCAL_USER_EMAIL = "pytest-api-user@example.local"
TEST_LOCAL_USER_PASSWORD = "pytest-only-password-14-12-1"
_raw_session_token: str | None = None
_csrf_token: str | None = None
_preauth_token_hash: str | None = None
_state_initialized = False
_initialization_lock = Lock()


def authenticated_test_client() -> TestClient:
    global _csrf_token, _preauth_token_hash, _raw_session_token, _state_initialized

    origin = get_settings().cors_origins[0]
    if _raw_session_token is None or _csrf_token is None:
        with _initialization_lock:
            if _raw_session_token is None or _csrf_token is None:
                _state_initialized = True
                _reset_test_user()
                bootstrap_client = TestClient(app, headers={"Origin": origin})
                bootstrap = bootstrap_client.get("/api/auth/session")
                assert bootstrap.status_code == 200, bootstrap.text
                bootstrap_payload = bootstrap.json()
                bootstrap_token = bootstrap_client.cookies.get(LOCAL_SESSION_COOKIE_NAME)
                assert bootstrap_token
                _preauth_token_hash = hashlib.sha256(bootstrap_token.encode()).hexdigest()
                login = bootstrap_client.post(
                    "/api/auth/login",
                    headers={LOCAL_CSRF_HEADER_NAME: bootstrap_payload["csrf_token"]},
                    json={"email": TEST_LOCAL_USER_EMAIL, "password": TEST_LOCAL_USER_PASSWORD},
                )
                assert login.status_code == 200, login.text
                _raw_session_token = bootstrap_client.cookies.get(LOCAL_SESSION_COOKIE_NAME)
                _csrf_token = login.json()["csrf_token"]
                assert _raw_session_token
                _delete_tracked_preauth_session()
                bootstrap_client.headers[LOCAL_CSRF_HEADER_NAME] = _csrf_token
                return bootstrap_client

    client = TestClient(
        app,
        headers={
            "Origin": origin,
            LOCAL_CSRF_HEADER_NAME: _csrf_token,
        },
    )
    client.cookies.set(LOCAL_SESSION_COOKIE_NAME, _raw_session_token, path="/api")
    return client


def cleanup_authenticated_test_client_state() -> None:
    global _csrf_token, _preauth_token_hash, _raw_session_token, _state_initialized

    if not _state_initialized:
        return

    try:
        with SessionLocal() as db:
            user_id = db.scalar(select(User.id).where(User.email == TEST_LOCAL_USER_EMAIL))
            if user_id is not None:
                db.execute(delete(UserSession).where(UserSession.user_id == user_id))
                user = db.get(User, user_id)
                if user is not None:
                    db.delete(user)
            if _preauth_token_hash is not None:
                db.execute(delete(UserSession).where(UserSession.token_hash == _preauth_token_hash))
            db.commit()
    finally:
        _raw_session_token = None
        _csrf_token = None
        _preauth_token_hash = None
        _state_initialized = False


def _reset_test_user() -> None:
    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).where(User.email == TEST_LOCAL_USER_EMAIL))
        if user_id is not None:
            db.execute(delete(UserSession).where(UserSession.user_id == user_id))
            user = db.get(User, user_id)
            if user is not None:
                db.delete(user)
            db.commit()
        create_local_user(db, email=TEST_LOCAL_USER_EMAIL, password=TEST_LOCAL_USER_PASSWORD)


def _delete_tracked_preauth_session() -> None:
    if _preauth_token_hash is None:
        return
    with SessionLocal() as db:
        db.execute(delete(UserSession).where(UserSession.token_hash == _preauth_token_hash))
        db.commit()
