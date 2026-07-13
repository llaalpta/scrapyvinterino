from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.crypto import decrypt_text
from vinted_monitor.core.redis_client import redis_client_from_url
from vinted_monitor.db.models import (
    ErrorLog,
    MonitorSession,
    ProxyProfile,
    Run,
    RunEvent,
    SearchSource,
    VintedSession,
)
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.vinted_catalog import (
    PreparedCatalogSession,
    VintedCatalogChallengeError,
    VintedCatalogRateLimitError,
)
from vinted_monitor.services.proxies import create_proxy_profile, effective_proxy_identity_generation
from vinted_monitor.services.runs import FAILED, monitor_policy_hash
from vinted_monitor.services.seen_cache import get_seen_cache
from vinted_monitor.services.task_queue import (
    MonitorTask,
    dead_letter_queue_key,
    enqueue_task,
    pending_payload_key,
    pending_task_key,
    processing_queue_key,
    reserve_task,
)
from vinted_monitor.services.vinted_sessions import save_prepared_vinted_session
from vinted_monitor.worker.consumer import TaskConsumer


@pytest.fixture(scope="module", autouse=True)
def require_isolated_catalog_failstop_environment() -> None:
    settings = get_settings()
    database_url = make_url(settings.database_url)
    redis_database = int(urlparse(settings.redis_url).path.lstrip("/") or "0")
    http_destinations = (
        urlparse(str(settings.vinted_base_url)),
        urlparse(str(settings.vinted_datadome_collector_url)),
        urlparse(str(settings.egress_diagnostic_url)),
    )
    if (
        settings.app_env.strip().lower() != "test"
        or database_url.get_backend_name() != "postgresql"
        or database_url.database in {None, "vinted_monitor"}
        or redis_database == 0
        or any(destination.hostname not in {"127.0.0.1", "localhost"} for destination in http_destinations)
    ):
        pytest.fail(
            "catalog fail-stop integration requires isolated test PostgreSQL/Redis and loopback-only HTTP destinations"
        )


@dataclass(frozen=True)
class FailStopGraph:
    source_id: int
    proxy_id: int
    session_id: int
    proxy_identity_generation: str
    policy_hash: str


class LocalTerminalResponseProvider:
    constructed = 0
    searches = 0
    closes = 0
    failure_factory: Callable[[], Exception] = lambda: DataDomeChallengeError("local DataDome challenge canary")

    @classmethod
    def reset(cls, failure_factory: Callable[[], Exception]) -> None:
        cls.constructed = 0
        cls.searches = 0
        cls.closes = 0
        cls.failure_factory = failure_factory

    def __init__(self, **kwargs) -> None:
        type(self).constructed += 1
        proxy_url = urlparse(str(kwargs.get("proxy_url") or ""))
        assert proxy_url.hostname == "127.0.0.1"
        assert kwargs.get("prepared_session") is not None
        self.event_sink = kwargs.get("event_sink")
        self.prepared_session = kwargs["prepared_session"]
        self.prepared_session_refreshed = False

    def search(self, _source: SearchSource, page: int | None = None):
        del page
        type(self).searches += 1
        raise type(self).failure_factory()

    def close(self) -> None:
        type(self).closes += 1


@contextmanager
def _failstop_graph():
    settings = get_settings()
    suffix = uuid4().hex
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name=f"qa failstop proxy {suffix}",
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
            name=f"qa failstop monitor {suffix}",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            monitor_mode="window",
            scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
        )
        db.add(source)
        db.flush()
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
        graph = FailStopGraph(
            source_id=source.id,
            proxy_id=proxy.id,
            session_id=session.id,
            proxy_identity_generation=effective_proxy_identity_generation(proxy),
            policy_hash=monitor_policy_hash(source),
        )
        db.commit()

    cache = get_seen_cache(settings)
    cache.mark_baseline(graph.source_id, graph.policy_hash)
    try:
        yield graph
    finally:
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


@pytest.mark.parametrize(
    ("failure_factory", "expected_failure_kind", "expected_proxy_failure_count", "expected_cooldown_minutes"),
    [
        pytest.param(
            lambda: DataDomeChallengeError("local DataDome challenge canary"),
            "datadome_challenge",
            2,
            20,
            id="datadome",
        ),
        pytest.param(
            lambda: VintedCatalogChallengeError("local Cloudflare challenge canary"),
            "cloudflare_challenge",
            1,
            10,
            id="cloudflare",
        ),
        pytest.param(
            lambda: VintedCatalogRateLimitError(
                "local catalog rate limit canary",
                retry_after_seconds=2.0,
                retry_after_source="seconds",
            ),
            "catalog_rate_limited",
            0,
            None,
            id="rate-limit",
        ),
    ],
)
def test_catalog_terminal_response_fails_once_invalidates_session_and_acks(
    monkeypatch: pytest.MonkeyPatch,
    failure_factory: Callable[[], Exception],
    expected_failure_kind: str,
    expected_proxy_failure_count: int,
    expected_cooldown_minutes: int | None,
) -> None:
    LocalTerminalResponseProvider.reset(failure_factory)
    monkeypatch.setattr(
        "vinted_monitor.services.runs.CurlCffiVintedCatalogProvider",
        LocalTerminalResponseProvider,
    )
    base_settings = get_settings()
    queue_key = f"qa:catalog-fail-stop:{uuid4().hex}"
    settings = base_settings.model_copy(
        update={
            "worker_task_queue_key": queue_key,
            "worker_max_retry_attempts": 3,
        }
    )
    queue_client = redis_client_from_url(settings.redis_url, decode_responses=False, socket_timeout=3)

    with _failstop_graph() as graph:
        try:
            task = MonitorTask(
                source_id=graph.source_id,
                source_url="https://www.vinted.es/catalog?search_text=&order=newest_first",
                monitor_mode="window",
                trigger="scheduler",
                scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
                proxy_profile_id=graph.proxy_id,
                proxy_identity_generation=graph.proxy_identity_generation,
            )
            assert enqueue_task(queue_client, task, queue_key=queue_key) is True
            reservation = reserve_task(queue_client, timeout=1, queue_key=queue_key, consumer_id=0)
            assert reservation is not None
            failure_started_at = datetime.now(UTC)

            TaskConsumer(settings, consumer_id=0)._consume_reservation(
                get_seen_cache(settings),
                reservation,
                queue_client=queue_client,
            )

            assert LocalTerminalResponseProvider.constructed == 1
            assert LocalTerminalResponseProvider.searches == 1
            assert LocalTerminalResponseProvider.closes == 1
            with SessionLocal() as db:
                runs = list(db.scalars(select(Run).where(Run.task_id == task.task_id)))
                assert len(runs) == 1
                assert runs[0].status == FAILED
                assert (runs[0].runtime_metadata or {}).get("failure_kind") == expected_failure_kind
                failed_event = db.scalar(
                    select(RunEvent).where(RunEvent.run_id == runs[0].id, RunEvent.phase == "run_failed")
                )
                assert failed_event is not None
                assert (failed_event.details or {}).get("recovery_action") == "invalidate_session_and_end_attempt"
                proxy = db.get(ProxyProfile, graph.proxy_id)
                assert proxy is not None
                assert proxy.failure_count == expected_proxy_failure_count
                if expected_cooldown_minutes is None:
                    assert proxy.cooldown_until is None
                else:
                    assert proxy.cooldown_until is not None
                    assert failure_started_at + timedelta(minutes=expected_cooldown_minutes) <= proxy.cooldown_until
                    assert proxy.cooldown_until <= datetime.now(UTC) + timedelta(minutes=expected_cooldown_minutes)
                session = db.get(VintedSession, graph.session_id)
                assert session is not None
                assert session.status == "invalid"
                assert decrypt_text(session.context_encrypted, settings.app_secret_key) == "{}"

            assert queue_client.llen(queue_key) == 0
            assert queue_client.llen(processing_queue_key(queue_key)) == 0
            assert queue_client.llen(processing_queue_key(queue_key, 0)) == 0
            assert queue_client.llen(dead_letter_queue_key(queue_key)) == 0
            assert queue_client.get(pending_task_key(graph.source_id, queue_key)) is None
            assert queue_client.get(pending_payload_key(reservation.raw_payload, queue_key)) is None
        finally:
            keys = list(queue_client.scan_iter(match=f"{queue_key}*"))
            if keys:
                queue_client.delete(*keys)
