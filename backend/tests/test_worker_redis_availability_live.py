from __future__ import annotations

import json
import os
import subprocess
import time
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from playwright.sync_api import BrowserContext, Route, expect, sync_playwright
from sqlalchemy import func, select

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import Run, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import create_local_user
from vinted_monitor.services.scheduler import update_scheduler_config

pytestmark = [pytest.mark.real_auth, pytest.mark.live_stack]
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PASSWORD = "worker-redis-live-password"
QA_OWNER_LABEL = "com.scrapyvinterino.qa.owner"


def test_worker_redis_loss_exits_restarts_and_updates_live_pwa() -> None:
    api_url = _loopback_origin("WORKER_REDIS_QA_API_URL")
    pwa_url = _loopback_origin("WORKER_REDIS_QA_PWA_URL")
    redis_container = _required_env("WORKER_REDIS_QA_REDIS_CONTAINER")
    worker_container = _required_env("WORKER_REDIS_QA_WORKER_CONTAINER")
    owner = _required_env("WORKER_REDIS_QA_OWNER_TOKEN")
    _assert_owned_container(redis_container, owner)
    _assert_owned_container(worker_container, owner)

    settings = get_settings()
    assert settings.scheduler_enabled is True
    assert settings.vinted_direct_catalog_enabled is True
    assert settings.vinted_datadome_collector_enabled is False
    assert settings.action_requests_enabled is False
    assert settings.scheduler_worker_heartbeat_interval_seconds == 1
    assert settings.scheduler_worker_heartbeat_timeout_seconds == 5
    assert urlsplit(settings.redis_url).hostname in LOOPBACK_HOSTS
    for endpoint in (
        settings.vinted_base_url,
        settings.vinted_datadome_collector_url,
        settings.egress_diagnostic_url,
    ):
        assert urlsplit(str(endpoint)).hostname in LOOPBACK_HOSTS

    token = uuid4().hex
    email = f"qa-worker-redis-{token}@example.local"
    with SessionLocal() as db:
        create_local_user(db, email=email, password=PASSWORD)
        update_scheduler_config(
            db,
            {"enabled": True, "allow_direct_without_proxy": True},
            settings,
        )
        assert db.scalar(select(func.count()).select_from(SearchSource)) == 0
        assert db.scalar(select(func.count()).select_from(Run)) == 0

    assert _redis_dbsize(redis_container) == 0
    initial_restart_count = _restart_count(worker_container)
    redis_stopped = False
    seen_urls: list[str] = []
    blocked_urls: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.getenv("WORKER_REDIS_QA_BROWSER_CHANNEL", "chrome"),
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
                page.goto(pwa_url, wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="Acceso a Vinted Monitor")).to_be_visible()
                page.get_by_label("Email").fill(email)
                page.get_by_label("Password").fill(PASSWORD)
                page.get_by_role("button", name="Entrar").click()
                expect(page.get_by_role("button", name="Ajustes", exact=True)).to_be_visible()
                page.get_by_role("button", name="Ajustes", exact=True).click()

                available = _wait_scheduler(context, api_url, pwa_url, expected=True, timeout=20)
                scheduler_status = page.get_by_role("heading", name="Ajustes").locator(
                    "xpath=following-sibling::span"
                )
                expect(scheduler_status).to_have_text(
                    "Scheduler activo",
                    timeout=10_000,
                )

                # Docker only enables a restart policy after a container has
                # stayed up long enough to be considered successfully started.
                page.wait_for_timeout(10_500)
                _docker("stop", redis_container)
                redis_stopped = True
                failed_state = _wait_failed_restart(
                    worker_container,
                    greater_than=initial_restart_count,
                    timeout=25,
                )
                assert failed_state["ExitCode"] != 0
                logs = _docker("logs", worker_container)
                assert "worker_redis_healthcheck_failed" in logs
                assert PASSWORD not in logs

                last_after_exit = _scheduler(context, api_url, pwa_url)["worker_last_seen_at"]
                unavailable = _wait_scheduler(context, api_url, pwa_url, expected=False, timeout=20)
                assert unavailable["effective_enabled"] is False
                assert unavailable["worker_last_seen_at"] == last_after_exit
                assert available["worker_last_seen_at"] is not None
                expect(scheduler_status).to_have_text(
                    "Scheduler no disponible",
                    timeout=10_000,
                )

                _docker("start", redis_container)
                redis_stopped = False
                recovered = _wait_scheduler(context, api_url, pwa_url, expected=True, timeout=90)
                assert recovered["effective_enabled"] is True
                assert recovered["worker_last_seen_at"] != unavailable["worker_last_seen_at"]
                expect(scheduler_status).to_have_text(
                    "Scheduler activo",
                    timeout=10_000,
                )
            finally:
                context.close()
                browser.close()
    finally:
        if redis_stopped:
            _assert_owned_container(redis_container, owner)
            _docker("start", redis_container)

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(SearchSource)) == 0
        assert db.scalar(select(func.count()).select_from(Run)) == 0
    assert _redis_dbsize(redis_container) == 0
    assert seen_urls and not blocked_urls
    assert all(_local_or_non_network(url) for url in seen_urls)


def _wait_scheduler(
    context: BrowserContext,
    api_url: str,
    origin: str,
    *,
    expected: bool,
    timeout: float,
) -> dict:
    deadline = time.monotonic() + timeout
    last_payload: dict | None = None
    while time.monotonic() < deadline:
        last_payload = _scheduler(context, api_url, origin)
        if last_payload["worker_available"] is expected:
            return last_payload
        time.sleep(0.25)
    raise AssertionError(
        f"worker_available did not become {expected}; last payload={last_payload}"
    )


def _scheduler(context: BrowserContext, api_url: str, origin: str) -> dict:
    response = context.request.get(
        f"{api_url}/api/scheduler",
        headers={"Origin": origin},
    )
    assert response.ok, f"GET /api/scheduler returned HTTP {response.status}"
    return response.json()


def _wait_failed_restart(container: str, *, greater_than: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = json.loads(_docker("inspect", "--format", "{{json .State}}", container))
        if (
            _restart_count(container) > greater_than
            and state["Status"] == "restarting"
            and state["ExitCode"] != 0
        ):
            return state
        time.sleep(0.25)
    raise AssertionError(f"worker container did not enter a failed restart after Redis loss: {container}")


def _restart_count(container: str) -> int:
    return int(_docker("inspect", "--format", "{{.RestartCount}}", container))


def _redis_dbsize(container: str) -> int:
    return int(_docker("exec", container, "redis-cli", "--raw", "DBSIZE"))


def _assert_owned_container(container: str, owner: str) -> None:
    actual = _docker(
        "inspect",
        "--format",
        f'{{{{ index .Config.Labels "{QA_OWNER_LABEL}" }}}}',
        container,
    )
    assert actual == owner, f"refusing to control container without QA ownership: {container}"


def _docker(*arguments: str) -> str:
    completed = subprocess.run(
        ["docker", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(f"Docker command failed: docker {arguments[0]}")
    return completed.stdout.strip()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"set {name} through the isolated worker Redis QA runner")
    return value


def _loopback_origin(name: str) -> str:
    value = _required_env(name)
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
        raise AssertionError(f"{name} must be an explicit loopback HTTP origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/"):
        raise AssertionError(f"{name} must not contain credentials, path, query or fragment")
    return value.rstrip("/")


def _local_or_non_network(url: str) -> bool:
    if url.startswith(("data:", "blob:", "about:")):
        return True
    return urlsplit(url).hostname in LOOPBACK_HOSTS


def _assert_loopback(url: str) -> None:
    assert urlsplit(url).hostname in LOOPBACK_HOSTS, f"non-loopback browser traffic: {url}"
