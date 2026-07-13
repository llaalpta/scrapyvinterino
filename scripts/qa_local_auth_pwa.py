from __future__ import annotations

import atexit
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Request, Route, sync_playwright
from sqlalchemy import delete, or_

from vinted_monitor.db.models import User, UserSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import LOCAL_SESSION_COOKIE_NAME, create_local_user


def main() -> None:
    email = _required_environment("LOCAL_AUTH_QA_EMAIL")
    password = _required_environment("LOCAL_AUTH_QA_PASSWORD")
    repo_root = Path(_required_environment("LOCAL_AUTH_QA_REPO_ROOT")).resolve()
    if not (repo_root / "docker-compose.yml").is_file():
        raise RuntimeError("LOCAL_AUTH_QA_REPO_ROOT must contain docker-compose.yml")
    pwa_url = _loopback_url(_required_environment("LOCAL_AUTH_QA_PWA_URL"), "LOCAL_AUTH_QA_PWA_URL")
    api_url = _loopback_url(
        _required_environment("LOCAL_AUTH_QA_API_URL"),
        "LOCAL_AUTH_QA_API_URL",
    )
    browser_channel = os.getenv("LOCAL_AUTH_QA_BROWSER_CHANNEL", "chrome")
    _assert_services_stopped(repo_root, {"worker", "scheduler-watchdog"})
    qa_user_id = _create_qa_user(email, password)
    tracked_session_hashes: set[str] = set()
    cleanup = _QaCleanup(qa_user_id, tracked_session_hashes)
    cleanup_callback = cleanup.run
    atexit.register(cleanup_callback)
    previous_excepthook = sys.excepthook

    def cleanup_before_uncaught_exception(exc_type, exc_value, traceback) -> None:
        try:
            cleanup_callback()
        finally:
            previous_excepthook(exc_type, exc_value, traceback)

    sys.excepthook = cleanup_before_uncaught_exception

    api_requests: list[str] = []
    api_request_timeline: list[tuple[float, str]] = []
    stream_requests: list[str] = []
    stream_request_timeline: list[tuple[float, Request]] = []
    active_streams: set[Request] = set()
    blocked_external_hosts: set[str] = set()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=browser_channel, headless=True)
        context = browser.new_context(service_workers="block")
        page = context.new_page()

        def observe_request(request: Request) -> None:
            parsed = urlsplit(request.url)
            if parsed.path.startswith("/api"):
                suffix = f"?{parsed.query}" if parsed.query else ""
                entry = f"{request.method} {parsed.path}{suffix}"
                api_requests.append(entry)
                api_request_timeline.append((time.monotonic(), entry))
            if parsed.path == "/api/monitors/events/stream":
                stream_requests.append(request.url)
                stream_request_timeline.append((time.monotonic(), request))
                active_streams.add(request)

        page.on("request", observe_request)
        page.on("requestfinished", lambda request: active_streams.discard(request))
        page.on("requestfailed", lambda request: active_streams.discard(request))

        def local_only(route: Route) -> None:
            parsed = urlsplit(route.request.url)
            if parsed.scheme in {"http", "https"} and parsed.hostname not in {"localhost", "127.0.0.1"}:
                blocked_external_hosts.add(parsed.hostname or "unknown")
                route.abort("blockedbyclient")
                return
            route.continue_()

        context.route("**/*", local_only)

        try:
            page.goto(pwa_url, wait_until="domcontentloaded")
            page.get_by_role("heading", name="Acceso a Vinted Monitor").wait_for()
            page.wait_for_timeout(500)
            _track_current_session_cookie(context, tracked_session_hashes)
            assert api_requests == ["GET /api/auth/session"]
            assert page.get_by_role("button", name="Salir").count() == 0

            page.get_by_label("Email").fill(email)
            page.get_by_label("Password").fill("definitely-wrong-password")
            page.get_by_role("button", name="Entrar").click()
            page.get_by_role("alert").wait_for()
            assert page.get_by_role("alert").text_content() == "Email o password incorrectos"
            assert not any(_is_business_request(entry) for entry in api_requests)
            assert page.get_by_label("Password").input_value() == ""

            page.get_by_label("Password").fill(password)
            page.get_by_role("button", name="Entrar").click()
            page.get_by_role("button", name="Salir").wait_for()
            page.get_by_text(email, exact=True).wait_for()
            assert any(_is_business_request(entry) for entry in api_requests)
            authenticated_cookie = next(
                (cookie for cookie in context.cookies() if cookie["name"] == "vinted_monitor_session"),
                None,
            )
            assert authenticated_cookie is not None
            tracked_session_hashes.add(hashlib.sha256(authenticated_cookie["value"].encode()).hexdigest())
            assert authenticated_cookie["httpOnly"] is True
            assert authenticated_cookie["sameSite"] == "Strict"
            assert authenticated_cookie["path"] == "/api"

            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="Salir").wait_for()
            assert page.get_by_role("heading", name="Acceso a Vinted Monitor").count() == 0

            page.get_by_role("button", name="Monitores", exact=True).click()
            _wait_until(page, lambda: len(stream_requests) == 1 and len(active_streams) == 1, "first SSE connection")
            page.wait_for_timeout(1000)
            assert len(stream_requests) == 1, "renders or REST refreshes recreated EventSource"

            page.get_by_role("button", name="Ajustes", exact=True).click()
            _wait_until(page, lambda: len(active_streams) == 0, "SSE closure after leaving Monitores")
            page.get_by_role("button", name="Monitores", exact=True).click()
            _wait_until(page, lambda: len(stream_requests) == 2 and len(active_streams) == 1, "cursor SSE resume")
            assert "last_event_id=" in stream_requests[1]

            # A healthy idle connection must survive beyond the PWA liveness
            # deadline because the named backend heartbeat is observable by JS.
            idle_stream_count = len(stream_requests)
            idle_stream = next(iter(active_streams))
            page.wait_for_timeout(24_000)
            assert len(stream_requests) == idle_stream_count
            assert active_streams == {idle_stream}

            api_started_before = _container_started_at(repo_root, "api")
            restart_started_at = time.monotonic()
            streams_before_restart = len(stream_requests)
            old_stream = next(iter(active_streams))
            subprocess.run(
                ["docker", "compose", "restart", "api"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            _wait_until(page, lambda: _url_is_healthy(f"{api_url}/health"), "API health after restart", timeout=30)
            api_started_after = _container_started_at(repo_root, "api")
            assert api_started_after != api_started_before, "Compose API did not actually restart"
            _wait_until(page, lambda: old_stream not in active_streams, "old SSE closure after API restart", timeout=30)

            def authenticated_restart_stream_is_current() -> bool:
                auth_requests = [
                    recorded_at
                    for recorded_at, entry in api_request_timeline
                    if recorded_at >= restart_started_at and entry == "GET /api/auth/session"
                ]
                new_streams = [
                    (recorded_at, request)
                    for recorded_at, request in stream_request_timeline
                    if recorded_at >= restart_started_at
                ]
                return (
                    len(auth_requests) == 1
                    and len(new_streams) == 1
                    and auth_requests[0] < new_streams[0][0]
                    and active_streams == {new_streams[0][1]}
                )

            _wait_until(
                page,
                authenticated_restart_stream_is_current,
                "authenticated SSE recovery",
                timeout=40,
            )
            assert len(stream_requests) == streams_before_restart + 1
            assert page.get_by_role("button", name="Salir").count() == 1

            logout_attempts = 0

            def fail_first_logout(route: Route) -> None:
                nonlocal logout_attempts
                logout_attempts += 1
                if logout_attempts == 1:
                    time.sleep(0.7)
                    route.abort("failed")
                    return
                route.continue_()

            page.route("**/api/auth/logout", fail_first_logout)
            page.evaluate(
                """() => {
                    window.__qaLogoutTimeline = [];
                    window.__qaLogoutTimer = window.setInterval(() => {
                      window.__qaLogoutTimeline.push({
                        closing: document.body.textContent.includes('Cerrando la sesion de forma segura...'),
                        logoutButton: [...document.querySelectorAll('button')].some((button) => button.textContent.includes('Salir'))
                      });
                    }, 25);
                }"""
            )
            page.get_by_role("button", name="Salir").click()
            page.get_by_text("No se pudo confirmar el cierre de sesion. El panel permanece bloqueado.").wait_for()
            logout_timeline = page.evaluate(
                """() => {
                    window.clearInterval(window.__qaLogoutTimer);
                    return window.__qaLogoutTimeline;
                }"""
            )
            assert any(entry["closing"] and not entry["logoutButton"] for entry in logout_timeline)
            page.get_by_role("button", name="Reintentar cierre").click()
            page.get_by_role("heading", name="Acceso a Vinted Monitor").wait_for()
            assert len(active_streams) == 0

            captured_cookie_client = playwright.request.new_context(
                base_url=api_url,
                extra_http_headers={
                    "Cookie": f"vinted_monitor_session={authenticated_cookie['value']}",
                },
            )
            try:
                captured_cookie_response = captured_cookie_client.get("/api/monitors", max_redirects=0)
                assert captured_cookie_response.status == 401
            finally:
                captured_cookie_client.dispose()

            page.reload(wait_until="domcontentloaded")
            page.get_by_role("heading", name="Acceso a Vinted Monitor").wait_for()
            _track_current_session_cookie(context, tracked_session_hashes)
            assert page.get_by_role("button", name="Salir").count() == 0
            assert blocked_external_hosts == set()
            _assert_services_stopped(repo_root, {"worker", "scheduler-watchdog"})

            print(
                json.dumps(
                    {
                        "status": "ok",
                        "api_request_count": len(api_requests),
                        "stream_request_count": len(stream_requests),
                        "blocked_external_host_count": len(blocked_external_hosts),
                    }
                )
            )
        finally:
            try:
                _track_current_session_cookie(context, tracked_session_hashes)
            finally:
                try:
                    context.close()
                    browser.close()
                finally:
                    cleanup_callback()
                    atexit.unregister(cleanup_callback)
                    sys.excepthook = previous_excepthook


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _create_qa_user(email: str, password: str) -> int:
    with SessionLocal() as db:
        return create_local_user(db, email=email, password=password).id


def _track_current_session_cookie(context: BrowserContext, tracked_hashes: set[str]) -> None:
    for cookie in context.cookies():
        if cookie["name"] == LOCAL_SESSION_COOKIE_NAME:
            tracked_hashes.add(hashlib.sha256(cookie["value"].encode()).hexdigest())


def _cleanup_qa_user(user_id: int, tracked_hashes: set[str]) -> None:
    with SessionLocal() as db:
        owned_session = UserSession.user_id == user_id
        if tracked_hashes:
            owned_session = or_(owned_session, UserSession.token_hash.in_(tracked_hashes))
        db.execute(delete(UserSession).where(owned_session))
        user = db.get(User, user_id)
        if user is not None:
            db.delete(user)
        db.commit()


class _QaCleanup:
    def __init__(self, user_id: int, tracked_hashes: set[str]) -> None:
        self._user_id = user_id
        self._tracked_hashes = tracked_hashes
        self._complete = False

    def run(self) -> None:
        if self._complete:
            return
        _cleanup_qa_user(self._user_id, self._tracked_hashes)
        self._complete = True


def _is_business_request(entry: str) -> bool:
    return entry.startswith(
        (
            "GET /api/monitors",
            "GET /api/opportunities",
            "GET /api/runs",
            "GET /api/proxy-profiles",
            "GET /api/scheduler",
        )
    )


def _wait_until(page, predicate, label: str, *, timeout: int = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"Timed out waiting for {label}")


def _url_is_healthy(url: str) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirects())
        with opener.open(url, timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def _assert_services_stopped(repo_root: Path, services: set[str]) -> None:
    result = subprocess.run(
        ["docker", "compose", "ps", "--status", "running", "--services"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    running = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    unexpected = services & running
    if unexpected:
        raise RuntimeError(f"QA requires stopped services: {', '.join(sorted(unexpected))}")


def _container_started_at(repo_root: Path, service: str) -> str:
    container = subprocess.run(
        ["docker", "compose", "ps", "-q", service],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip()
    if not container:
        raise RuntimeError(f"Compose service {service} is not running")
    return subprocess.run(
        ["docker", "inspect", "--format={{.State.StartedAt}}", container],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip()


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


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


if __name__ == "__main__":
    main()
