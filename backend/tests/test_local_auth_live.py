from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from queue import Queue
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from vinted_monitor.db.models import SearchSource, User, UserSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import LOCAL_CSRF_HEADER_NAME, LOCAL_SESSION_COOKIE_NAME, create_local_user

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]
LIVE_PASSWORD = "live-stack-auth-password-14-12-1"


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes
    headers: object

    def json(self) -> dict:
        return json.loads(self.body)


@pytest.fixture
def live_auth_context():
    base_url = os.getenv("LOCAL_AUTH_LIVE_BASE_URL")
    if not base_url:
        pytest.skip("set LOCAL_AUTH_LIVE_BASE_URL to run against the live API")
    base_url = _loopback_url(base_url, "LOCAL_AUTH_LIVE_BASE_URL")
    origin = _loopback_url(
        os.getenv("LOCAL_AUTH_LIVE_ORIGIN", "http://localhost:5173"),
        "LOCAL_AUTH_LIVE_ORIGIN",
    )
    email = f"pytest-live-auth-{uuid4().hex}@example.local"
    tracked_token_hashes: set[str] = set()
    tracked_source_ids: set[int] = set()
    with SessionLocal() as db:
        user = create_local_user(db, email=email, password=LIVE_PASSWORD)
        user_id = user.id

    def track_cookie(jar: CookieJar) -> str:
        token = _session_token(jar)
        tracked_token_hashes.add(hashlib.sha256(token.encode()).hexdigest())
        return token

    try:
        yield {
            "base_url": base_url,
            "origin": origin,
            "email": email,
            "user_id": user_id,
            "track_cookie": track_cookie,
            "track_source": tracked_source_ids.add,
        }
    finally:
        with SessionLocal() as db:
            if tracked_source_ids:
                db.execute(delete(SearchSource).where(SearchSource.id.in_(tracked_source_ids)))
            db.execute(delete(UserSession).where(UserSession.user_id == user_id))
            if tracked_token_hashes:
                db.execute(delete(UserSession).where(UserSession.token_hash.in_(tracked_token_hashes)))
            user = db.get(User, user_id)
            if user is not None:
                db.delete(user)
            db.commit()


def test_live_http_auth_csrf_logout_and_sse_revocation(live_auth_context) -> None:
    base_url = live_auth_context["base_url"]
    origin = live_auth_context["origin"]
    track_cookie = live_auth_context["track_cookie"]
    jar = CookieJar()
    opener = _local_opener(jar)

    health = _request(opener, base_url, "GET", "/health")
    anonymous = _request(opener, base_url, "GET", "/api/monitors")
    assert health.status == 200
    assert anonymous.status == 401
    assert anonymous.headers.get("Cache-Control") == "no-store"

    bootstrap = _request(opener, base_url, "GET", "/api/auth/session")
    preauth_token = track_cookie(jar)
    assert bootstrap.status == 200
    assert bootstrap.json()["authenticated"] is False
    assert bootstrap.headers.get("Cache-Control") == "no-store"
    cookie_header = "; ".join(bootstrap.headers.get_all("Set-Cookie") or [])
    assert "HttpOnly" in cookie_header
    assert "SameSite=strict" in cookie_header
    assert "Path=/api" in cookie_header
    assert "Domain=" not in cookie_header

    missing_origin = _request(
        opener,
        base_url,
        "POST",
        "/api/auth/login",
        payload={"email": live_auth_context["email"], "password": LIVE_PASSWORD},
        headers={LOCAL_CSRF_HEADER_NAME: bootstrap.json()["csrf_token"]},
    )
    assert missing_origin.status == 403

    login = _request(
        opener,
        base_url,
        "POST",
        "/api/auth/login",
        payload={"email": live_auth_context["email"], "password": LIVE_PASSWORD},
        headers={"Origin": origin, LOCAL_CSRF_HEADER_NAME: bootstrap.json()["csrf_token"]},
    )
    assert login.status == 200
    assert login.json()["authenticated"] is True
    authenticated_token = track_cookie(jar)
    assert authenticated_token != preauth_token
    assert _request_with_token(base_url, preauth_token, "/api/monitors").status == 401
    assert _request(opener, base_url, "GET", "/api/monitors").status == 200

    preflight = _request(
        opener,
        base_url,
        "OPTIONS",
        "/api/monitors",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": LOCAL_CSRF_HEADER_NAME,
        },
    )
    assert preflight.status == 200
    assert preflight.headers.get("Access-Control-Allow-Origin") == origin
    assert preflight.headers.get("Access-Control-Allow-Credentials") == "true"

    with SessionLocal() as db:
        monitor_count = db.scalar(select(func.count()).select_from(SearchSource))
    rejected_mutation = _request(
        opener,
        base_url,
        "POST",
        "/api/monitors",
        payload={"name": "must not persist", "url": "https://www.vinted.es/catalog?search_text=auth-live-reject"},
        headers={"Origin": origin},
    )
    assert rejected_mutation.status == 403
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SearchSource)) == monitor_count

    accepted_mutation = _request(
        opener,
        base_url,
        "POST",
        "/api/monitors",
        payload={
            "name": f"pytest live auth {uuid4().hex}",
            "url": "https://www.vinted.es/catalog?search_text=auth-live-accepted",
        },
        headers={"Origin": origin, LOCAL_CSRF_HEADER_NAME: login.json()["csrf_token"]},
    )
    assert accepted_mutation.status == 201
    source_id = int(accepted_mutation.json()["id"])
    live_auth_context["track_source"](source_id)
    archived = _request(
        opener,
        base_url,
        "DELETE",
        f"/api/monitors/{source_id}",
        headers={"Origin": origin, LOCAL_CSRF_HEADER_NAME: login.json()["csrf_token"]},
    )
    assert archived.status == 204

    stream_ready = Event()
    stream_closed = Event()
    stream_errors: Queue[BaseException] = Queue()
    stream_thread = Thread(
        target=_consume_sse_until_closed,
        args=(opener, f"{base_url}/api/monitors/events/stream", stream_ready, stream_closed, stream_errors),
        daemon=True,
    )
    stream_thread.start()
    assert stream_ready.wait(timeout=5), "live SSE did not emit stream_ready"

    authenticated_hash = hashlib.sha256(authenticated_token.encode()).hexdigest()
    with SessionLocal() as db:
        session = db.scalar(select(UserSession).where(UserSession.token_hash == authenticated_hash))
        assert session is not None
        session.revoked_at = datetime.now(UTC)
        db.commit()

    assert stream_closed.wait(timeout=10), "revoked live SSE did not close"
    assert stream_errors.empty(), list(stream_errors.queue)
    assert _request(opener, base_url, "GET", "/api/monitors").status == 401

    second_bootstrap = _request(opener, base_url, "GET", "/api/auth/session")
    track_cookie(jar)
    assert second_bootstrap.status == 200
    second_login = _request(
        opener,
        base_url,
        "POST",
        "/api/auth/login",
        payload={"email": live_auth_context["email"], "password": LIVE_PASSWORD},
        headers={"Origin": origin, LOCAL_CSRF_HEADER_NAME: second_bootstrap.json()["csrf_token"]},
    )
    assert second_login.status == 200
    logout_token = track_cookie(jar)
    logout = _request(
        opener,
        base_url,
        "POST",
        "/api/auth/logout",
        headers={"Origin": origin, LOCAL_CSRF_HEADER_NAME: second_login.json()["csrf_token"]},
    )
    assert logout.status == 204
    logout_cookie_header = "; ".join(logout.headers.get_all("Set-Cookie") or [])
    assert "Max-Age=0" in logout_cookie_header
    assert "Path=/api" in logout_cookie_header
    assert _request_with_token(base_url, logout_token, "/api/monitors").status == 401


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "http://example.invalid:8000",
        "http://127.0.0.1",
        "http://user:secret@127.0.0.1:8000",
        "http://127.0.0.1:8000/api",
        "http://127.0.0.1:8000?redirect=http://example.invalid",
    ],
)
def test_live_base_url_rejects_non_loopback_or_ambiguous_targets(url: str) -> None:
    with pytest.raises(ValueError, match="HTTP loopback origin"):
        _loopback_url(url, "LOCAL_AUTH_LIVE_BASE_URL")


def _request(
    opener,
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> HttpResult:
    request_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        request_headers["Content-Type"] = "application/json"
    request = Request(f"{base_url}{path}", data=data, headers=request_headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResult(response.status, response.read(), response.headers)
    except HTTPError as exc:
        return HttpResult(exc.code, exc.read(), exc.headers)


def _request_with_token(base_url: str, token: str, path: str) -> HttpResult:
    opener = _local_opener()
    return _request(
        opener,
        base_url,
        "GET",
        path,
        headers={"Cookie": f"{LOCAL_SESSION_COOKIE_NAME}={token}"},
    )


def _session_token(jar: CookieJar) -> str:
    token = next((cookie.value for cookie in jar if cookie.name == LOCAL_SESSION_COOKIE_NAME), None)
    assert token
    return token


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _local_opener(jar: CookieJar | None = None):
    handlers = [ProxyHandler({}), _RejectRedirects()]
    if jar is not None:
        handlers.append(HTTPCookieProcessor(jar))
    return build_opener(*handlers)


def _loopback_url(raw_url: str, variable_name: str) -> str:
    parsed = urlsplit(raw_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{variable_name} must use a valid explicit loopback port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{variable_name} must be an HTTP loopback origin with an explicit port")
    return raw_url.rstrip("/")


def _consume_sse_until_closed(
    opener,
    stream_url: str,
    stream_ready: Event,
    stream_closed: Event,
    stream_errors: Queue[BaseException],
) -> None:
    try:
        request = Request(stream_url, headers={"Accept": "text/event-stream"})
        with opener.open(request, timeout=20) as response:
            event_lines: list[str] = []
            while line := response.readline():
                decoded = line.decode()
                if decoded == "\n":
                    if "event: stream_ready" in "".join(event_lines):
                        stream_ready.set()
                    event_lines = []
                else:
                    event_lines.append(decoded)
    except BaseException as exc:  # pragma: no cover - asserted in the parent thread
        stream_errors.put(exc)
    finally:
        stream_closed.set()
