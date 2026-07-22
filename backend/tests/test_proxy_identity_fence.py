from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from textwrap import dedent
from threading import Barrier, Event, Lock
from time import monotonic, sleep
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from api_client import authenticated_test_client
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.redis_client import redis_client_from_url
from vinted_monitor.db.models import AppSetting, ErrorLog, MonitorSession, ProxyProfile, Run, RunEvent, SearchSource, VintedSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.catalog import CatalogSearchResult
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession
from vinted_monitor.services.proxies import (
    create_proxy_profile,
    effective_proxy_identity_generation,
    lock_proxy_profile_for_selection,
)
from vinted_monitor.services.runs import FAILED, SUCCESS, monitor_policy_hash
from vinted_monitor.services.scheduler import (
    SCHEDULER_SETTING_KEY,
    SchedulerCapacityError,
    choose_run_egress,
    update_scheduler_config,
)
from vinted_monitor.services.scheduler_liveness import SCHEDULER_WORKER_HEARTBEAT_KEY
from vinted_monitor.services.seen_cache import get_seen_cache
from vinted_monitor.services.task_queue import (
    MonitorTask,
    dead_letter_queue_key,
    enqueue_task,
    pending_payload_key,
    pending_task_key,
    pending_tasks,
    processing_queue_key,
    reserve_task,
)
from vinted_monitor.services.vinted_sessions import (
    VintedSessionRequiredError,
    get_ready_vinted_session,
    prepared_context_flags,
    prepared_context_from_session,
    save_prepared_vinted_session,
)
from vinted_monitor.worker.consumer import TaskConsumer
from vinted_monitor.worker.scheduler import SchedulerRunner

IDENTITY_MUTATIONS = (
    "scheme",
    "host",
    "port",
    "username",
    "clear_username",
    "password",
    "clear_password",
    "country",
    "inactive",
    "cooldown",
    "preset",
)
IDENTITY_CHANGES = frozenset(IDENTITY_MUTATIONS) - {"inactive", "cooldown"}


@pytest.fixture(scope="module", autouse=True)
def require_isolated_identity_fence_environment() -> None:
    settings = get_settings()
    database_url = make_url(settings.database_url)
    database_name = database_url.database
    redis_database = int(urlparse(settings.redis_url).path.lstrip("/") or "0")
    if (
        settings.app_env.strip().lower() != "test"
        or database_url.get_backend_name() != "postgresql"
        or database_name in {None, "vinted_monitor"}
        or redis_database == 0
    ):
        pytest.fail("proxy identity integration tests require isolated test PostgreSQL and non-default Redis databases")


@dataclass(frozen=True)
class IdentityGraph:
    source_id: int
    proxy_id: int
    session_id: int
    policy_hash: str


class StopConsumerLoop(RuntimeError):
    pass


class ProviderConstructionTrap:
    constructed = 0
    _lock = Lock()

    def __init__(self, **_kwargs) -> None:
        with self._lock:
            type(self).constructed += 1
        raise AssertionError("provider construction crossed the proxy identity fence")


class LocalAcceptedProvider:
    constructed = 0
    bootstrap_calls = 0
    probe_calls = 0
    search_calls = 0
    close_calls = 0
    proxy_hosts: list[str | None] = []
    sticky_templates: list[str] = []

    @classmethod
    def reset(cls) -> None:
        cls.constructed = 0
        cls.bootstrap_calls = 0
        cls.probe_calls = 0
        cls.search_calls = 0
        cls.close_calls = 0
        cls.proxy_hosts = []
        cls.sticky_templates = []

    def __init__(self, **kwargs) -> None:
        type(self).constructed += 1
        proxy_url = kwargs.get("proxy_url")
        type(self).proxy_hosts.append(urlparse(proxy_url).hostname if isinstance(proxy_url, str) else None)
        type(self).sticky_templates.append(kwargs["settings"].proxy_sticky_username_template)
        self.settings = kwargs["settings"]
        self.event_sink = kwargs.get("event_sink")
        self.prepared_session = kwargs.get("prepared_session")
        self.prepared_session_refreshed = False

    def bootstrap_for_session(self, _source_url: str, *, collect_datadome: bool = True) -> dict:
        type(self).bootstrap_calls += 1
        assert collect_datadome is True
        return {"outcome": "local_accepted"}

    def probe_catalog_api(self, _source_url: str, *, include_payload: bool = False) -> dict:
        type(self).probe_calls += 1
        assert include_payload is False
        return {
            "outcome": "accepted_json",
            "status_code": 200,
            "duration_ms": 1,
            "missing_required": [],
        }

    def export_prepared_session(self, *, proxy_session_id: str | None = None) -> PreparedCatalogSession:
        return _complete_context(proxy_session_id or "local-prepared-session")

    def search(self, _source: SearchSource, page: int | None = None) -> CatalogSearchResult:
        type(self).search_calls += 1
        return CatalogSearchResult(
            items=[],
            page=page or 1,
            total_pages=1,
            total_entries=0,
            per_page=1,
            next_page=None,
            provider_metadata={"transport": "local"},
        )

    def close(self) -> None:
        type(self).close_calls += 1
        return None


@contextmanager
def _identity_graph(*, active: bool = False, manual_active: bool = False, recurring_inactive: bool = False):
    assert sum((active, manual_active, recurring_inactive)) <= 1
    settings = get_settings()
    suffix = uuid4().hex
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name=f"qa identity proxy {suffix}",
            scheme="http",
            kind="residential",
            host="127.0.0.1",
            port=18080,
            username=f"qa-user-{suffix}",
            password=f"qa-password-{suffix}",
            country_code="ES",
            settings=settings,
        )
        source = SearchSource(
            name=f"qa identity monitor {suffix}",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=active or manual_active,
            monitor_mode="window" if active or recurring_inactive else "manual",
            monitor_started_at=datetime.now(UTC) if manual_active else None,
            scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
        )
        db.add(source)
        db.flush()
        if active or manual_active:
            db.add(MonitorSession(source_id=source.id))
        session = save_prepared_vinted_session(
            db,
            source,
            proxy,
            proxy_session_id=f"qa{suffix[:24]}",
            profile=profile_for_impersonate(settings.curl_impersonate_browser),
            context=_complete_context(f"qa{suffix[:24]}"),
            settings=settings,
        )
        policy_hash = monitor_policy_hash(source)
        db.commit()
        graph = IdentityGraph(
            source_id=source.id,
            proxy_id=proxy.id,
            session_id=session.id,
            policy_hash=policy_hash,
        )

    cache = get_seen_cache()
    cache.mark_baseline(graph.source_id, graph.policy_hash)
    try:
        yield graph
    finally:
        _cleanup_identity_graph(graph)


@contextmanager
def _enabled_scheduler_runtime(settings):
    keys = (SCHEDULER_SETTING_KEY, SCHEDULER_WORKER_HEARTBEAT_KEY)
    with SessionLocal() as db:
        snapshots: dict[str, tuple[bool, dict]] = {}
        for key in keys:
            setting = db.get(AppSetting, key)
            snapshots[key] = (setting is not None, deepcopy(setting.value or {}) if setting is not None else {})
        update_scheduler_config(
            db,
            {"max_concurrent_runs": 1},
            settings,
        )
    try:
        yield
    finally:
        with SessionLocal() as db:
            for key, (existed, value) in snapshots.items():
                setting = db.get(AppSetting, key)
                if not existed:
                    if setting is not None:
                        db.delete(setting)
                elif setting is None:
                    db.add(AppSetting(key=key, value=deepcopy(value)))
                else:
                    setting.value = deepcopy(value)
            db.commit()


def _complete_context(proxy_session_id: str) -> PreparedCatalogSession:
    return PreparedCatalogSession(
        proxy_session_id=proxy_session_id,
        cookies={
            "access_token_web": "qa-access-token",
            "datadome": "qa-datadome-token",
            "__cf_bm": "qa-cf-token",
            "v_udt": "qa-v-udt",
            "anon_id": "qa-anon-id",
        },
        csrf_token="qa-csrf-token",
        anon_id="qa-anon-id",
        access_token_web="qa-access-token",
        datadome="qa-datadome-token",
        cf_bm="qa-cf-token",
        v_udt="qa-v-udt",
        user_iso_locale="es-ES",
        vinted_screen="catalog",
        egress_ip="192.0.2.10",
        egress_country_code="ES",
        egress_validated_at=datetime.now(UTC),
    )


def _apply_mutation(case: str, client, proxy_id: int) -> str | None:
    secret_canary: str | None = None
    if case == "scheme":
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"scheme": "https"})
    elif case == "host":
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"host": "127.0.0.2"})
    elif case == "port":
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"port": 18081})
    elif case == "username":
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"username": f"qa-next-{uuid4().hex}"})
    elif case == "clear_username":
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"username": ""})
    elif case == "password":
        secret_canary = f"qa-next-password-{uuid4().hex}"
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"password": secret_canary})
    elif case == "clear_password":
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"clear_password": True})
    elif case == "country":
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"country_code": "FR"})
    elif case == "inactive":
        response = client.patch(f"/api/proxy-profiles/{proxy_id}", json={"is_active": False})
    elif case == "cooldown":
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, proxy_id)
            assert profile is not None
            profile.cooldown_until = datetime.now(UTC) + timedelta(minutes=5)
            db.commit()
        return None
    elif case == "preset":
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, proxy_id)
            assert profile is not None
            profile.locale = "fr-FR"
            db.commit()
        return None
    else:  # pragma: no cover - parametrization owns the closed set
        raise AssertionError(case)
    assert response.status_code == 200, response.text
    if secret_canary is not None:
        assert secret_canary not in response.text
    return secret_canary


def _install_fence_barrier(
    monkeypatch: pytest.MonkeyPatch,
    *,
    wait_on_call: int = 1,
) -> tuple[Event, Event]:
    import vinted_monitor.services.runs as runs_module

    captured = Event()
    release = Event()
    real_fence = runs_module.lock_and_revalidate_proxy_selection
    call_count = 0
    entered_lock = Lock()

    def blocked_fence(*args, **kwargs):
        nonlocal call_count
        with entered_lock:
            call_count += 1
            should_wait = call_count == wait_on_call
        if should_wait:
            captured.set()
            assert release.wait(timeout=10), "identity fence test barrier timed out"
        return real_fence(*args, **kwargs)

    monkeypatch.setattr(runs_module, "lock_and_revalidate_proxy_selection", blocked_fence)
    return captured, release


@pytest.mark.parametrize("mutation", IDENTITY_MUTATIONS)
def test_manual_api_stale_proxy_selection_never_constructs_provider(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    captured, release = _install_fence_barrier(monkeypatch)
    command_client = authenticated_test_client()
    mutation_client = authenticated_test_client()

    with _identity_graph(manual_active=True) as graph, ThreadPoolExecutor(max_workers=1) as pool:
        with SessionLocal() as db:
            original_profile = db.get(ProxyProfile, graph.proxy_id)
            original_session = db.get(VintedSession, graph.session_id)
            assert original_profile is not None and original_session is not None
            original_generation = effective_proxy_identity_generation(original_profile)
            original_context_encrypted = original_session.context_encrypted
        future = pool.submit(command_client.post, f"/api/monitors/{graph.source_id}/runs")
        assert captured.wait(timeout=10), "manual command did not reach the identity fence"
        secret_canary = _apply_mutation(mutation, mutation_client, graph.proxy_id)
        release.set()
        response = future.result(timeout=15)

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["status"] == FAILED
        assert ProviderConstructionTrap.constructed == 0
        if secret_canary is not None:
            assert secret_canary not in response.text

        with SessionLocal() as db:
            run = db.get(Run, payload["id"])
            assert run is not None
            assert (run.runtime_metadata or {}).get("failure_kind") == "ProxyProfileEligibilityError"
            _assert_proxy_fence_failure(db, run)
            if secret_canary is not None:
                _assert_secret_absent_from_runtime_artifacts(db, run, secret_canary)
            sessions = list(db.scalars(select(VintedSession).where(VintedSession.source_id == graph.source_id)))
            assert len(sessions) <= 1
            profile = db.get(ProxyProfile, graph.proxy_id)
            if profile is not None:
                assert profile.failure_count == 0, (payload.get("runtime_metadata") or {}).get("failure_kind")
                current_generation = effective_proxy_identity_generation(profile)
                if mutation in IDENTITY_CHANGES:
                    original_parts = original_generation.split(":")
                    current_parts = current_generation.split(":")
                    assert int(current_parts[1]) == int(original_parts[1]) + 1
                    assert current_parts[2] != original_parts[2]
                else:
                    assert current_generation == original_generation
            if mutation in {"inactive", "cooldown"}:
                assert sessions and sessions[0].status == "ready"
                assert sessions[0].context_encrypted == original_context_encrypted
            else:
                assert sessions and sessions[0].status == "invalid"
                assert not any(prepared_context_flags(prepared_context_from_session(sessions[0], get_settings())).values())


@pytest.mark.parametrize(
    ("endpoint", "payload", "nested_run"),
    (
        ("vinted-session/prepare", None, False),
        ("items/detail-probe", {"item_ref": "9356705635"}, True),
    ),
)
def test_auxiliary_api_traffic_actions_share_the_proxy_identity_fence(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    payload: dict[str, str] | None,
    nested_run: bool,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    captured, release = _install_fence_barrier(monkeypatch)
    command_client = authenticated_test_client()
    mutation_client = authenticated_test_client()

    with _identity_graph() as graph, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            command_client.post,
            f"/api/monitors/{graph.source_id}/{endpoint}",
            json=payload,
        )
        assert captured.wait(timeout=10), f"{endpoint} did not reach the identity fence"
        _apply_mutation("host", mutation_client, graph.proxy_id)
        release.set()
        response = future.result(timeout=15)

        assert response.status_code == 201, response.text
        response_payload = response.json()
        run_payload = response_payload["run"] if nested_run else response_payload
        assert run_payload["status"] == FAILED
        assert ProviderConstructionTrap.constructed == 0
        with SessionLocal() as db:
            run = db.get(Run, run_payload["id"])
            assert run is not None
            _assert_proxy_fence_failure(db, run)


def test_session_prepare_revalidates_cooldown_immediately_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    captured, release = _install_fence_barrier(monkeypatch, wait_on_call=2)
    command_client = authenticated_test_client()

    with _identity_graph() as graph, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(command_client.post, f"/api/monitors/{graph.source_id}/vinted-session/prepare")
        assert captured.wait(timeout=10), "session preparation did not reach its immediate pre-provider fence"
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert profile is not None
            profile.cooldown_until = datetime.now(UTC) + timedelta(minutes=5)
            db.commit()
        release.set()
        response = future.result(timeout=15)

        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["status"] == FAILED
        assert ProviderConstructionTrap.constructed == 0
        with SessionLocal() as db:
            run = db.get(Run, payload["id"])
            assert run is not None
            _assert_proxy_fence_failure(db, run, run_started_expected=True)


@pytest.mark.parametrize("mutation", IDENTITY_MUTATIONS)
def test_redis_consumer_stale_proxy_selection_is_terminal_and_acknowledged(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    captured, release = _install_fence_barrier(monkeypatch)
    mutation_client = authenticated_test_client()
    settings = get_settings()
    queue_key = f"qa:identity:{uuid4().hex}"
    queue_client = redis_client_from_url(settings.redis_url, decode_responses=False, socket_timeout=3)

    with _identity_graph(active=True) as graph:
        try:
            with SessionLocal() as db:
                egress = choose_run_egress(db, settings)
                assert egress.proxy_profile_id == graph.proxy_id
                task = MonitorTask(
                    source_id=graph.source_id,
                    source_url="https://www.vinted.es/catalog?search_text=&order=newest_first",
                    monitor_mode="window",
                    trigger="scheduler",
                    scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
                    proxy_profile_id=egress.proxy_profile_id,
                    proxy_identity_generation=egress.proxy_identity_generation,
                )
                assert enqueue_task(queue_client, task, queue_key=queue_key) is True
                db.commit()
            reservation = reserve_task(queue_client, timeout=1, queue_key=queue_key, consumer_id=0)
            assert reservation is not None
            consumer_settings = settings.model_copy(
                update={
                    "worker_task_queue_key": queue_key,
                    "worker_max_retry_attempts": 3,
                }
            )
            consumer = TaskConsumer(consumer_settings, consumer_id=0)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    consumer._consume_reservation,
                    SimpleNamespace(client=queue_client),
                    reservation,
                )
                assert captured.wait(timeout=10), "queued command did not reach the identity fence"
                secret_canary = _apply_mutation(mutation, mutation_client, graph.proxy_id)
                release.set()
                future.result(timeout=15)

            assert ProviderConstructionTrap.constructed == 0
            _assert_task_acknowledged(queue_client, queue_key, graph.source_id, reservation.raw_payload)
            with SessionLocal() as db:
                run = db.scalar(select(Run).where(Run.task_id == task.task_id))
                assert run is not None
                assert (run.runtime_metadata or {}).get("failure_kind") == "ProxyProfileEligibilityError"
                _assert_proxy_fence_failure(db, run)
                profile = db.get(ProxyProfile, graph.proxy_id)
                if profile is not None:
                    assert profile.failure_count == 0
                if secret_canary is not None:
                    raw_payload_text = (
                        reservation.raw_payload.decode(errors="replace")
                        if isinstance(reservation.raw_payload, bytes)
                        else reservation.raw_payload
                    )
                    assert secret_canary not in raw_payload_text
                    _assert_secret_absent_from_runtime_artifacts(db, run, secret_canary)
        finally:
            _delete_queue_keys(queue_client, queue_key)


def test_real_scheduler_producer_and_consumer_loop_preserve_stale_identity_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    base_settings = get_settings()
    queue_key = f"qa:identity:{uuid4().hex}"
    settings = base_settings.model_copy(
        update={
            "scheduler_enabled": True,
            "worker_task_queue_key": queue_key,
            "worker_consumer_count": 1,
            "worker_reserve_timeout_seconds": 1,
            "worker_max_retry_attempts": 3,
        }
    )
    queue_client = redis_client_from_url(settings.redis_url, decode_responses=False, socket_timeout=3)
    mutation_client = authenticated_test_client()

    with _enabled_scheduler_runtime(settings), _identity_graph(active=True) as graph:
        try:
            now = datetime.now(UTC)
            with SessionLocal() as db:
                source = db.get(SearchSource, graph.source_id)
                assert source is not None
                source.next_run_at = now
                db.commit()

            assert SchedulerRunner(settings).run_once(now=now) == [graph.source_id]
            queued = pending_tasks(
                queue_client,
                queue_key=queue_key,
                processing_keys=(processing_queue_key(queue_key, 0),),
            )
            assert len(queued) == 1
            task = queued[0]
            assert task.proxy_profile_id == graph.proxy_id
            with SessionLocal() as db:
                profile = db.get(ProxyProfile, graph.proxy_id)
                assert profile is not None
                assert task.proxy_identity_generation == effective_proxy_identity_generation(profile)

            mutation = mutation_client.patch(f"/api/proxy-profiles/{graph.proxy_id}", json={"host": "127.0.0.2"})
            assert mutation.status_code == 200, mutation.text

            consumer = TaskConsumer(settings, consumer_id=0)
            real_consume = consumer._consume_reservation
            consumed_reservations = []

            def consume_once(cache, reservation, *, queue_client=None):
                consumed_reservations.append(reservation)
                real_consume(cache, reservation, queue_client=queue_client)
                raise StopConsumerLoop

            monkeypatch.setattr(consumer, "_consume_reservation", consume_once)
            with pytest.raises(StopConsumerLoop):
                consumer.run_forever()

            assert len(consumed_reservations) == 1
            reservation = consumed_reservations[0]
            assert ProviderConstructionTrap.constructed == 0
            _assert_task_acknowledged(queue_client, queue_key, graph.source_id, reservation.raw_payload)
            with SessionLocal() as db:
                runs = list(db.scalars(select(Run).where(Run.task_id == task.task_id)))
                assert len(runs) == 1
                _assert_proxy_fence_failure(db, runs[0])
        finally:
            _delete_queue_keys(queue_client, queue_key)


def test_manual_api_missing_proxy_fails_before_first_event_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    captured, release = _install_fence_barrier(monkeypatch)
    command_client = authenticated_test_client()

    with _identity_graph(manual_active=True) as graph, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(command_client.post, f"/api/monitors/{graph.source_id}/runs")
        assert captured.wait(timeout=10), "manual command did not reach the identity fence"
        with SessionLocal() as db:
            db.execute(delete(VintedSession).where(VintedSession.id == graph.session_id))
            db.execute(delete(ProxyProfile).where(ProxyProfile.id == graph.proxy_id))
            db.commit()
        release.set()
        response = future.result(timeout=15)

        assert response.status_code == 201, response.text
        payload = response.json()
        assert ProviderConstructionTrap.constructed == 0
        with SessionLocal() as db:
            run = db.get(Run, payload["id"])
            assert run is not None
            _assert_proxy_fence_failure(db, run)
            failed_event = db.scalar(
                select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "run_failed")
            )
            assert failed_event is not None and failed_event.proxy_profile_id is None


def test_scheduler_proxy_selection_and_identity_edit_use_one_lock_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vinted_monitor.services.proxies as proxies_module
    import vinted_monitor.worker.scheduler as scheduler_module

    base_settings = get_settings()
    queue_key = f"qa:identity:{uuid4().hex}"
    settings = base_settings.model_copy(
        update={
            "scheduler_enabled": True,
            "worker_task_queue_key": queue_key,
            "worker_consumer_count": 1,
        }
    )
    queue_client = redis_client_from_url(settings.redis_url, decode_responses=False, socket_timeout=3)
    edit_client = authenticated_test_client()
    scheduler_before_choose = Event()
    release_scheduler_choose = Event()
    edit_has_exclusive_advisory = Event()
    release_edit = Event()
    real_choose = scheduler_module.choose_run_egress
    real_identity_lock = proxies_module._acquire_proxy_identity_lock

    def blocked_choose(*args, **kwargs):
        scheduler_before_choose.set()
        assert release_scheduler_choose.wait(timeout=10)
        return real_choose(*args, **kwargs)

    def observed_identity_lock(db, profile_id: int, *, exclusive: bool):
        real_identity_lock(db, profile_id, exclusive=exclusive)
        if exclusive:
            edit_has_exclusive_advisory.set()
            assert release_edit.wait(timeout=10)

    monkeypatch.setattr(scheduler_module, "choose_run_egress", blocked_choose)
    monkeypatch.setattr(proxies_module, "_acquire_proxy_identity_lock", observed_identity_lock)

    with _enabled_scheduler_runtime(settings), _identity_graph(active=True) as graph:
        try:
            now = datetime.now(UTC)
            with SessionLocal() as db:
                source = db.get(SearchSource, graph.source_id)
                assert source is not None
                source.next_run_at = now
                db.commit()
            runner = SchedulerRunner(settings)

            with ThreadPoolExecutor(max_workers=2) as pool:
                scheduler_future = pool.submit(runner.run_once, now)
                assert scheduler_before_choose.wait(timeout=10), "scheduler did not reach proxy selection"
                edit_future = pool.submit(
                    edit_client.patch,
                    f"/api/proxy-profiles/{graph.proxy_id}",
                    json={"host": "127.0.0.2"},
                )
                assert edit_has_exclusive_advisory.wait(timeout=10), "proxy edit did not acquire exclusive ownership"
                release_scheduler_choose.set()
                release_edit.set()
                edit_response = edit_future.result(timeout=15)
                first_submitted = scheduler_future.result(timeout=15)

            assert edit_response.status_code == 200, edit_response.text
            assert first_submitted == [graph.source_id]
            queued = pending_tasks(
                queue_client,
                queue_key=queue_key,
                processing_keys=(processing_queue_key(queue_key, 0),),
            )
            assert len(queued) == 1
            with SessionLocal() as db:
                profile = db.get(ProxyProfile, graph.proxy_id)
                assert profile is not None
                assert queued[0].proxy_identity_generation == effective_proxy_identity_generation(profile)
        finally:
            release_scheduler_choose.set()
            release_edit.set()
            _delete_queue_keys(queue_client, queue_key)


def test_saturated_proxy_selection_does_not_accumulate_identity_fences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vinted_monitor.services.proxies as proxies_module

    base_settings = get_settings()
    replacement_template = (
        "{username}-identity-{session_id}"
        if base_settings.proxy_sticky_username_template != "{username}-identity-{session_id}"
        else "{username}-session-{session_id}"
    )
    settings = base_settings.model_copy(
        update={
            "scheduler_enabled": True,
            "proxy_sticky_username_template": replacement_template,
        }
    )
    selector_barrier = Barrier(2)
    selector_index = 0
    selector_index_lock = Lock()
    identity_lock_calls = 0
    identity_lock_calls_guard = Lock()
    real_selection_lock = proxies_module.lock_proxy_profile_for_selection

    with (
        _enabled_scheduler_runtime(settings),
        _identity_graph(active=True) as first_graph,
        _identity_graph(active=True) as second_graph,
    ):
        profile_ids = (first_graph.proxy_id, second_graph.proxy_id)

        def opposite_candidate_orders(db, *, now=None, country_code=None):
            del now, country_code
            nonlocal selector_index
            with selector_index_lock:
                current_index = selector_index
                selector_index += 1
            assert selector_barrier.wait(timeout=5) in (0, 1)
            ordered_ids = profile_ids if current_index == 0 else tuple(reversed(profile_ids))
            profiles = [db.get(ProxyProfile, profile_id) for profile_id in ordered_ids]
            assert all(profile is not None for profile in profiles)
            return profiles

        def counted_selection_lock(*args, **kwargs):
            nonlocal identity_lock_calls
            with identity_lock_calls_guard:
                identity_lock_calls += 1
            return real_selection_lock(*args, **kwargs)

        monkeypatch.setattr(proxies_module, "list_available_proxy_profiles", opposite_candidate_orders)
        monkeypatch.setattr(proxies_module, "lock_proxy_profile_for_selection", counted_selection_lock)
        active_counts = {first_graph.proxy_id: 1, second_graph.proxy_id: 1}

        def select_saturated_egress() -> None:
            with SessionLocal() as db:
                db.execute(text("SET LOCAL lock_timeout = '1s'"))
                with pytest.raises(SchedulerCapacityError):
                    choose_run_egress(
                        db,
                        settings,
                        active_proxy_counts=active_counts,
                    )
                db.rollback()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(select_saturated_egress) for _ in range(2)]
            for future in futures:
                future.result(timeout=5)
        assert identity_lock_calls == 0


def test_egress_selection_fences_only_one_usable_candidate_and_fails_without_proxy_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vinted_monitor.services.proxies as proxies_module

    settings = get_settings().model_copy(update={"scheduler_enabled": True})
    real_selection_lock = proxies_module.lock_proxy_profile_for_selection
    lock_calls: list[int] = []
    lower_selected_capacity = False

    with (
        _enabled_scheduler_runtime(settings),
        _identity_graph(active=True) as first_graph,
        _identity_graph(active=True) as second_graph,
    ):
        with SessionLocal() as db:
            first_profile = db.get(ProxyProfile, first_graph.proxy_id)
            assert first_profile is not None
            first_profile.max_concurrent_runs = 2
            update_scheduler_config(
                db,
                {"max_concurrent_runs": 3},
                settings,
            )

        def stable_candidate_order(db, *, now=None, country_code=None):
            del now, country_code
            profiles = [db.get(ProxyProfile, first_graph.proxy_id), db.get(ProxyProfile, second_graph.proxy_id)]
            assert all(profile is not None for profile in profiles)
            return profiles

        def observed_selection_lock(db, profile_id: int, selection_settings):
            lock_calls.append(profile_id)
            profile = real_selection_lock(db, profile_id, selection_settings)
            if lower_selected_capacity:
                profile.max_concurrent_runs = 1
            return profile

        monkeypatch.setattr(proxies_module, "list_available_proxy_profiles", stable_candidate_order)
        monkeypatch.setattr(proxies_module, "lock_proxy_profile_for_selection", observed_selection_lock)

        with SessionLocal() as db:
            selected = choose_run_egress(
                db,
                settings,
                active_proxy_counts={first_graph.proxy_id: 2, second_graph.proxy_id: 0},
            )
            assert selected.proxy_profile_id == second_graph.proxy_id
            assert lock_calls == [second_graph.proxy_id]
            db.rollback()

        lock_calls.clear()
        with SessionLocal() as db:
            with pytest.raises(SchedulerCapacityError, match="No proxy is available"):
                choose_run_egress(
                    db,
                    settings,
                    active_proxy_counts={first_graph.proxy_id: 2, second_graph.proxy_id: 1},
                )
            assert lock_calls == []
            db.rollback()

        lock_calls.clear()
        lower_selected_capacity = True
        with SessionLocal() as db:
            with pytest.raises(SchedulerCapacityError, match="capacity changed"):
                choose_run_egress(
                    db,
                    settings,
                    active_proxy_counts={first_graph.proxy_id: 1, second_graph.proxy_id: 1},
                )
            assert lock_calls == [first_graph.proxy_id]
            db.rollback()


def test_redis_consumer_missing_proxy_is_terminal_without_constructing_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    settings = get_settings()
    queue_key = f"qa:identity:{uuid4().hex}"
    queue_client = redis_client_from_url(settings.redis_url, decode_responses=False, socket_timeout=3)

    with _identity_graph(active=True) as graph:
        try:
            with SessionLocal() as db:
                egress = choose_run_egress(db, settings)
                assert egress.proxy_profile_id == graph.proxy_id
                task = MonitorTask(
                    source_id=graph.source_id,
                    source_url="https://www.vinted.es/catalog?search_text=&order=newest_first",
                    monitor_mode="window",
                    trigger="scheduler",
                    scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
                    proxy_profile_id=egress.proxy_profile_id,
                    proxy_identity_generation=egress.proxy_identity_generation,
                )
                assert enqueue_task(queue_client, task, queue_key=queue_key) is True
                db.commit()
            with SessionLocal() as db:
                db.execute(delete(VintedSession).where(VintedSession.id == graph.session_id))
                db.execute(delete(ProxyProfile).where(ProxyProfile.id == graph.proxy_id))
                db.commit()

            reservation = reserve_task(queue_client, timeout=1, queue_key=queue_key, consumer_id=0)
            assert reservation is not None
            consumer = TaskConsumer(
                settings.model_copy(
                    update={
                        "worker_task_queue_key": queue_key,
                        "worker_max_retry_attempts": 3,
                    }
                ),
                consumer_id=0,
            )

            consumer._consume_reservation(SimpleNamespace(client=queue_client), reservation)

            assert ProviderConstructionTrap.constructed == 0
            _assert_task_acknowledged(queue_client, queue_key, graph.source_id, reservation.raw_payload)
            with SessionLocal() as db:
                run = db.scalar(select(Run).where(Run.task_id == task.task_id))
                assert run is not None
                _assert_proxy_fence_failure(db, run)
                failed_event = db.scalar(
                    select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "run_failed")
                )
                assert failed_event is not None
                assert failed_event.proxy_profile_id is None
                assert (run.runtime_metadata or {}).get("failure_kind") == "ProxyProfileEligibilityError"
        finally:
            _delete_queue_keys(queue_client, queue_key)


def test_worker_config_change_rejects_task_captured_with_old_sticky_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    old_settings = get_settings()
    replacement_template = (
        "{username}-identity-{session_id}"
        if old_settings.proxy_sticky_username_template != "{username}-identity-{session_id}"
        else "{username}-session-{session_id}"
    )
    new_settings = old_settings.model_copy(update={"proxy_sticky_username_template": replacement_template})
    queue_key = f"qa:identity:{uuid4().hex}"
    queue_client = redis_client_from_url(old_settings.redis_url, decode_responses=False, socket_timeout=3)

    with _identity_graph(active=True) as graph:
        try:
            with SessionLocal() as db:
                egress = choose_run_egress(db, old_settings)
                assert egress.proxy_profile_id == graph.proxy_id
                task = MonitorTask(
                    source_id=graph.source_id,
                    source_url="https://www.vinted.es/catalog?search_text=&order=newest_first",
                    monitor_mode="window",
                    trigger="scheduler",
                    scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
                    proxy_profile_id=egress.proxy_profile_id,
                    proxy_identity_generation=egress.proxy_identity_generation,
                )
                assert enqueue_task(queue_client, task, queue_key=queue_key) is True
                db.commit()

            raw_payload = queue_client.lindex(queue_key, 0)
            assert isinstance(raw_payload, bytes)
            child_env = os.environ.copy()
            child_env.update(
                {
                    "PROXY_STICKY_USERNAME_TEMPLATE": replacement_template,
                    "WORKER_TASK_QUEUE_KEY": queue_key,
                    "WORKER_MAX_RETRY_ATTEMPTS": "3",
                    "WORKER_RESERVE_TIMEOUT_SECONDS": "1",
                }
            )
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    dedent(
                        """
                        import os
                        from types import SimpleNamespace

                        import vinted_monitor.services.runs as runs_module
                        from vinted_monitor.core.config import get_settings
                        from vinted_monitor.core.redis_client import redis_client_from_url
                        from vinted_monitor.services.task_queue import reserve_task
                        from vinted_monitor.worker.consumer import TaskConsumer


                        class ProviderConstructionTrap:
                            def __init__(self, **_kwargs):
                                raise SystemExit(86)


                        runs_module.CurlCffiVintedCatalogProvider = ProviderConstructionTrap
                        settings = get_settings()
                        assert settings.proxy_sticky_username_template == os.environ[
                            "PROXY_STICKY_USERNAME_TEMPLATE"
                        ]
                        queue_client = redis_client_from_url(
                            settings.redis_url,
                            decode_responses=False,
                            socket_timeout=3,
                        )
                        reservation = reserve_task(
                            queue_client,
                            timeout=1,
                            queue_key=settings.worker_task_queue_key,
                            consumer_id=0,
                        )
                        if reservation is None:
                            raise SystemExit(87)
                        TaskConsumer(settings, consumer_id=0)._consume_reservation(
                            SimpleNamespace(client=queue_client),
                            reservation,
                        )
                        """
                    ),
                ],
                check=False,
                capture_output=True,
                env=child_env,
                text=True,
                timeout=20,
            )

            assert child.returncode == 0, f"fresh worker process exited with {child.returncode}"
            assert ProviderConstructionTrap.constructed == 0
            _assert_task_acknowledged(queue_client, queue_key, graph.source_id, raw_payload)
            with SessionLocal() as db:
                run = db.scalar(select(Run).where(Run.task_id == task.task_id))
                profile = db.get(ProxyProfile, graph.proxy_id)
                session = db.get(VintedSession, graph.session_id)
                assert run is not None
                _assert_proxy_fence_failure(db, run)
                assert profile is not None
                assert effective_proxy_identity_generation(profile) != egress.proxy_identity_generation
                assert session is not None and session.status == "invalid"
                assert not any(prepared_context_flags(prepared_context_from_session(session, new_settings)).values())

            monkeypatch.setattr("vinted_monitor.services.runs.get_settings", lambda: new_settings)
            LocalAcceptedProvider.reset()
            monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", LocalAcceptedProvider)
            with SessionLocal() as db:
                fresh_egress = choose_run_egress(db, new_settings)
                assert fresh_egress.proxy_profile_id == graph.proxy_id
                assert fresh_egress.proxy_identity_generation != egress.proxy_identity_generation
                fresh_task = MonitorTask(
                    source_id=graph.source_id,
                    source_url="https://www.vinted.es/catalog?search_text=&order=newest_first",
                    monitor_mode="window",
                    trigger="scheduler",
                    scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
                    proxy_profile_id=fresh_egress.proxy_profile_id,
                    proxy_identity_generation=fresh_egress.proxy_identity_generation,
                )
                assert enqueue_task(queue_client, fresh_task, queue_key=queue_key) is True
                db.commit()
            fresh_reservation = reserve_task(queue_client, timeout=1, queue_key=queue_key, consumer_id=0)
            assert fresh_reservation is not None
            consumer = TaskConsumer(
                new_settings.model_copy(
                    update={
                        "worker_task_queue_key": queue_key,
                        "worker_max_retry_attempts": 3,
                    }
                ),
                consumer_id=0,
            )
            consumer._consume_reservation(SimpleNamespace(client=queue_client), fresh_reservation)

            _assert_task_acknowledged(queue_client, queue_key, graph.source_id, fresh_reservation.raw_payload)
            assert LocalAcceptedProvider.constructed == 2
            assert LocalAcceptedProvider.bootstrap_calls == 1
            assert LocalAcceptedProvider.probe_calls == 1
            assert LocalAcceptedProvider.search_calls == 1
            assert LocalAcceptedProvider.close_calls == 2
            assert LocalAcceptedProvider.sticky_templates == [replacement_template, replacement_template]
            with SessionLocal() as db:
                fresh_run = db.scalar(select(Run).where(Run.task_id == fresh_task.task_id))
                assert fresh_run is not None and fresh_run.status == SUCCESS
                current_sessions = list(
                    db.scalars(
                        select(VintedSession)
                        .where(VintedSession.source_id == graph.source_id)
                        .order_by(VintedSession.id.asc())
                    )
                )
                assert [session.status for session in current_sessions] == ["invalid", "ready"]
                assert current_sessions[-1].proxy_identity_generation == fresh_egress.proxy_identity_generation
        finally:
            _delete_queue_keys(queue_client, queue_key)


def test_identity_generation_prevents_stale_task_reuse_after_aba_revert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ProviderConstructionTrap.constructed = 0
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", ProviderConstructionTrap)
    settings = get_settings()
    mutation_client = authenticated_test_client()
    queue_key = f"qa:identity:{uuid4().hex}"
    queue_client = redis_client_from_url(settings.redis_url, decode_responses=False, socket_timeout=3)

    with _identity_graph(active=True) as graph:
        try:
            with SessionLocal() as db:
                egress = choose_run_egress(db, settings)
                assert egress.proxy_profile_id == graph.proxy_id
                task = MonitorTask(
                    source_id=graph.source_id,
                    source_url="https://www.vinted.es/catalog?search_text=&order=newest_first",
                    monitor_mode="window",
                    trigger="scheduler",
                    scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
                    proxy_profile_id=egress.proxy_profile_id,
                    proxy_identity_generation=egress.proxy_identity_generation,
                )
                assert enqueue_task(queue_client, task, queue_key=queue_key) is True
                db.commit()

            first_edit = mutation_client.patch(f"/api/proxy-profiles/{graph.proxy_id}", json={"host": "127.0.0.2"})
            revert = mutation_client.patch(f"/api/proxy-profiles/{graph.proxy_id}", json={"host": "127.0.0.1"})
            assert first_edit.status_code == 200, first_edit.text
            assert revert.status_code == 200, revert.text

            reservation = reserve_task(queue_client, timeout=1, queue_key=queue_key, consumer_id=0)
            assert reservation is not None
            consumer = TaskConsumer(
                settings.model_copy(
                    update={
                        "worker_task_queue_key": queue_key,
                        "worker_max_retry_attempts": 3,
                    }
                ),
                consumer_id=0,
            )
            consumer._consume_reservation(SimpleNamespace(client=queue_client), reservation)

            assert ProviderConstructionTrap.constructed == 0
            _assert_task_acknowledged(queue_client, queue_key, graph.source_id, reservation.raw_payload)
            with SessionLocal() as db:
                run = db.scalar(select(Run).where(Run.task_id == task.task_id))
                profile = db.get(ProxyProfile, graph.proxy_id)
                assert run is not None
                _assert_proxy_fence_failure(db, run)
                assert profile is not None
                current_generation = effective_proxy_identity_generation(profile)
                captured_parts = str(egress.proxy_identity_generation).split(":")
                current_parts = current_generation.split(":")
                assert int(current_parts[1]) == int(captured_parts[1]) + 2
                assert current_parts[2] == captured_parts[2]
        finally:
            _delete_queue_keys(queue_client, queue_key)


def test_fresh_command_after_identity_change_prepares_only_current_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LocalAcceptedProvider.reset()
    client = authenticated_test_client()
    with _identity_graph(manual_active=True) as graph:
        response = client.patch(f"/api/proxy-profiles/{graph.proxy_id}", json={"host": "127.0.0.2"})
        assert response.status_code == 200, response.text
        monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", LocalAcceptedProvider)

        run_response = client.post(f"/api/monitors/{graph.source_id}/runs")

        assert run_response.status_code == 201, run_response.text
        assert run_response.json()["status"] == SUCCESS
        assert LocalAcceptedProvider.constructed == 2
        assert LocalAcceptedProvider.bootstrap_calls == 1
        assert LocalAcceptedProvider.probe_calls == 1
        assert LocalAcceptedProvider.search_calls == 1
        assert LocalAcceptedProvider.close_calls == 2
        assert LocalAcceptedProvider.proxy_hosts == ["127.0.0.2", "127.0.0.2"]
        assert LocalAcceptedProvider.sticky_templates == [
            get_settings().proxy_sticky_username_template,
            get_settings().proxy_sticky_username_template,
        ]
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert profile is not None
            generation = effective_proxy_identity_generation(profile)
            sessions = list(
                db.scalars(
                    select(VintedSession)
                    .where(VintedSession.source_id == graph.source_id)
                    .order_by(VintedSession.id.asc())
                )
            )
            assert [session.status for session in sessions] == ["invalid", "ready"]
            assert sessions[-1].proxy_identity_generation == generation


def test_already_invalid_stale_session_is_not_rewritten_by_later_selection() -> None:
    settings = get_settings()
    client = authenticated_test_client()
    with _identity_graph() as graph:
        response = client.patch(f"/api/proxy-profiles/{graph.proxy_id}", json={"host": "127.0.0.2"})
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            stale_session = db.get(VintedSession, graph.session_id)
            assert stale_session is not None and stale_session.status == "invalid"
            original_invalidated_at = stale_session.invalidated_at
            original_context_encrypted = stale_session.context_encrypted

        second_response = client.patch(f"/api/proxy-profiles/{graph.proxy_id}", json={"host": "127.0.0.3"})
        assert second_response.status_code == 200, second_response.text
        with SessionLocal() as db:
            stale_session = db.get(VintedSession, graph.session_id)
            source = db.get(SearchSource, graph.source_id)
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert stale_session is not None and stale_session.status == "invalid"
            assert source is not None and profile is not None
            assert stale_session.invalidated_at == original_invalidated_at
            assert stale_session.context_encrypted == original_context_encrypted
            with pytest.raises(VintedSessionRequiredError):
                get_ready_vinted_session(db, source, profile, settings=settings)
            db.commit()

        with SessionLocal() as db:
            persisted = db.get(VintedSession, graph.session_id)
            assert persisted is not None
            assert persisted.invalidated_at == original_invalidated_at
            assert persisted.context_encrypted == original_context_encrypted


def test_nonidentity_profile_edits_preserve_generation_and_ready_context() -> None:
    client = authenticated_test_client()
    with _identity_graph() as graph:
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, graph.proxy_id)
            session = db.get(VintedSession, graph.session_id)
            assert profile is not None and session is not None
            original_generation = effective_proxy_identity_generation(profile)
            original_context_encrypted = session.context_encrypted

        response = client.patch(
            f"/api/proxy-profiles/{graph.proxy_id}",
            json={
                "name": f"qa renamed proxy {uuid4().hex}",
                "kind": "datacenter",
                "max_concurrent_runs": 3,
            },
        )
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, graph.proxy_id)
            session = db.get(VintedSession, graph.session_id)
            assert profile is not None and session is not None
            profile.last_test_status = "qa-local"
            profile.last_test_ip = "192.0.2.20"
            profile.failure_count = 2
            db.commit()
            db.refresh(profile)
            db.refresh(session)
            profile = lock_proxy_profile_for_selection(db, graph.proxy_id, get_settings())
            assert effective_proxy_identity_generation(profile) == original_generation
            assert session.status == "ready"
            assert session.context_encrypted == original_context_encrypted


def test_proxy_identity_shared_fences_allow_parallel_runs_and_block_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vinted_monitor.services.proxies as proxies_module

    settings = get_settings()
    client = authenticated_test_client()
    edit_lock_attempted = Event()
    edit_backend_pid: list[int] = []
    real_identity_lock = proxies_module._acquire_proxy_identity_lock

    def observed_identity_lock(db, profile_id: int, *, exclusive: bool):
        if exclusive:
            backend_pid = db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(backend_pid, int)
            edit_backend_pid.append(backend_pid)
            edit_lock_attempted.set()
        return real_identity_lock(db, profile_id, exclusive=exclusive)

    monkeypatch.setattr(proxies_module, "_acquire_proxy_identity_lock", observed_identity_lock)
    with _identity_graph() as graph, SessionLocal() as first_db:
        first_profile = first_db.get(ProxyProfile, graph.proxy_id)
        assert first_profile is not None
        captured_generation = effective_proxy_identity_generation(first_profile)

        from vinted_monitor.services.proxies import lock_and_revalidate_proxy_selection

        lock_and_revalidate_proxy_selection(first_db, graph.proxy_id, captured_generation, settings)
        second_acquired = Event()
        release_second = Event()

        def hold_second_shared_fence() -> None:
            with SessionLocal() as second_db:
                lock_and_revalidate_proxy_selection(second_db, graph.proxy_id, captured_generation, settings)
                second_acquired.set()
                assert release_second.wait(timeout=10)
                second_db.rollback()

        with ThreadPoolExecutor(max_workers=2) as pool:
            second_future = pool.submit(hold_second_shared_fence)
            assert second_acquired.wait(timeout=3), "two shared run fences serialized unexpectedly"
            edit_future = pool.submit(
                client.patch,
                f"/api/proxy-profiles/{graph.proxy_id}",
                json={"host": "127.0.0.3"},
            )
            assert edit_lock_attempted.wait(timeout=3), "proxy edit did not attempt the exclusive fence"
            _assert_advisory_lock_waiting(edit_backend_pid[-1])
            assert not edit_future.done(), "proxy edit crossed an admitted shared run fence"
            first_db.rollback()
            assert not edit_future.done(), "proxy edit crossed the second admitted shared run fence"
            release_second.set()
            second_future.result(timeout=10)
            edit_response = edit_future.result(timeout=10)
            assert edit_response.status_code == 200, edit_response.text


def test_admitted_run_keeps_identity_fence_through_final_provider_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vinted_monitor.services.proxies as proxies_module

    LocalAcceptedProvider.reset()
    run_client = authenticated_test_client()
    edit_client = authenticated_test_client()
    search_entered = Event()
    release_search = Event()
    edit_lock_attempted = Event()
    edit_backend_pid: list[int] = []
    real_search = LocalAcceptedProvider.search
    real_identity_lock = proxies_module._acquire_proxy_identity_lock

    def blocked_search(self, source: SearchSource, page: int | None = None) -> CatalogSearchResult:
        search_entered.set()
        assert release_search.wait(timeout=10)
        return real_search(self, source, page)

    def observed_identity_lock(db, profile_id: int, *, exclusive: bool):
        if exclusive:
            backend_pid = db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(backend_pid, int)
            edit_backend_pid.append(backend_pid)
            edit_lock_attempted.set()
        return real_identity_lock(db, profile_id, exclusive=exclusive)

    monkeypatch.setattr(LocalAcceptedProvider, "search", blocked_search)
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", LocalAcceptedProvider)
    monkeypatch.setattr(proxies_module, "_acquire_proxy_identity_lock", observed_identity_lock)

    with _identity_graph(manual_active=True) as graph:
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                run_future = pool.submit(run_client.post, f"/api/monitors/{graph.source_id}/runs")
                assert search_entered.wait(timeout=10), "run did not reach the final provider call"
                edit_future = pool.submit(
                    edit_client.patch,
                    f"/api/proxy-profiles/{graph.proxy_id}",
                    json={"host": "127.0.0.4"},
                )
                assert edit_lock_attempted.wait(timeout=3), "proxy edit did not attempt the exclusive fence"
                _assert_advisory_lock_waiting(edit_backend_pid[-1])
                assert not edit_future.done(), "proxy edit crossed the run's final provider fence"
                release_search.set()
                run_response = run_future.result(timeout=15)
                edit_response = edit_future.result(timeout=15)

            assert run_response.status_code == 201, run_response.text
            assert run_response.json()["status"] == SUCCESS
            assert edit_response.status_code == 200, edit_response.text
            assert LocalAcceptedProvider.search_calls == 1
            assert LocalAcceptedProvider.proxy_hosts == ["127.0.0.1"]
            with SessionLocal() as db:
                profile = db.get(ProxyProfile, graph.proxy_id)
                session = db.get(VintedSession, graph.session_id)
                assert profile is not None and profile.host == "127.0.0.4"
                assert session is not None and session.status == "invalid"
        finally:
            release_search.set()


def _assert_advisory_lock_waiting(backend_pid: int, *, timeout: float = 3) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        with SessionLocal() as db:
            waiting = db.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_locks "
                    "WHERE pid = :backend_pid AND locktype = 'advisory' AND granted IS FALSE"
                    ")"
                ),
                {"backend_pid": backend_pid},
            )
        if waiting:
            return
        sleep(0.01)
    pytest.fail(f"PostgreSQL backend {backend_pid} never waited on the advisory identity fence")


def _assert_proxy_fence_failure(db, run: Run, *, run_started_expected: bool = False) -> None:
    assert run.status == FAILED
    run_started_id = db.scalar(select(RunEvent.id).where(RunEvent.run_id == run.id, RunEvent.phase == "run_started"))
    assert (run_started_id is not None) is run_started_expected
    failed_event = db.scalar(select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "run_failed"))
    assert failed_event is not None
    assert (failed_event.details or {}).get("session_end_reason") == "proxy_selection_stale_or_ineligible"
    assert (failed_event.details or {}).get("recovery_action") == "issue_fresh_command_after_proxy_review"


def _assert_secret_absent_from_runtime_artifacts(db, run: Run, secret_canary: str) -> None:
    events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id)))
    errors = list(
        db.scalars(
            select(ErrorLog).where(
                (ErrorLog.run_id == run.id) | (ErrorLog.source_id == run.source_id)
            )
        )
    )
    artifacts = {
        "run": {
            "error_message": run.error_message,
            "runtime_metadata": run.runtime_metadata,
        },
        "events": [
            {
                "phase": event.phase,
                "method": event.method,
                "url": event.url,
                "user_agent": event.user_agent,
                "auth_mode": event.auth_mode,
                "message": event.message,
                "details": event.details,
            }
            for event in events
        ],
        "errors": [
            {
                "kind": error.kind,
                "message": error.message,
                "details": error.details,
            }
            for error in errors
        ],
    }
    assert secret_canary not in json.dumps(artifacts, default=str, sort_keys=True)


def _assert_task_acknowledged(queue_client, queue_key: str, source_id: int, raw_payload: bytes) -> None:
    assert queue_client.llen(queue_key) == 0
    assert queue_client.llen(processing_queue_key(queue_key)) == 0
    assert queue_client.llen(processing_queue_key(queue_key, 0)) == 0
    assert queue_client.llen(dead_letter_queue_key(queue_key)) == 0
    assert queue_client.get(pending_task_key(source_id, queue_key)) is None
    assert queue_client.get(pending_payload_key(raw_payload, queue_key)) is None


def _cleanup_identity_graph(graph: IdentityGraph) -> None:
    cache = get_seen_cache()
    keys = list(cache.client.scan_iter(match=f"*monitor:{graph.source_id}:*"))
    if keys:
        cache.client.delete(*keys)
    with SessionLocal() as db:
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id == graph.source_id)))
        if run_ids:
            db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
            db.execute(delete(ErrorLog).where(ErrorLog.run_id.in_(run_ids)))
            db.execute(delete(Run).where(Run.id.in_(run_ids)))
        db.execute(delete(RunEvent).where(RunEvent.source_id == graph.source_id))
        db.execute(delete(ErrorLog).where(ErrorLog.source_id == graph.source_id))
        db.execute(delete(VintedSession).where(VintedSession.source_id == graph.source_id))
        db.execute(delete(MonitorSession).where(MonitorSession.source_id == graph.source_id))
        db.execute(delete(SearchSource).where(SearchSource.id == graph.source_id))
        db.execute(delete(ProxyProfile).where(ProxyProfile.id == graph.proxy_id))
        db.commit()


def _delete_queue_keys(queue_client, queue_key: str) -> None:
    keys = list(queue_client.scan_iter(match=f"{queue_key}*"))
    if keys:
        queue_client.delete(*keys)
