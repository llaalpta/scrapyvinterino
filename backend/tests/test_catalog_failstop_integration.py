from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

import vinted_monitor.services.runs as runs_module
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
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogSearchResult
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.vinted_catalog import (
    EgressContext,
    PreparedCatalogSession,
    ProxyEgressProbeResult,
    VintedCatalogChallengeError,
    VintedCatalogRateLimitError,
    VintedCatalogTransportError,
    VintedEgressDiagnosticError,
)
from vinted_monitor.services.proxies import create_proxy_profile, effective_proxy_identity_generation
from vinted_monitor.services.runs import FAILED, SUCCESS, monitor_policy_hash
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
        or any(
            destination.hostname is None
            or (
                destination.hostname != "localhost"
                and not ip_address(destination.hostname).is_loopback
            )
            for destination in http_destinations
        )
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


class LocalSameProfileRecoveryProvider:
    constructed = 0
    searches = 0
    closes = 0
    preparations = 0
    detail_calls = 0
    catalog_items: list[CatalogItemCandidate] = []
    detail_error: Exception | None = None
    search_outcomes: list[Exception | None] = []

    @classmethod
    def reset(
        cls,
        *search_outcomes: Exception | None,
        catalog_items: list[CatalogItemCandidate] | None = None,
        detail_error: Exception | None = None,
    ) -> None:
        cls.constructed = 0
        cls.searches = 0
        cls.closes = 0
        cls.preparations = 0
        cls.detail_calls = 0
        cls.catalog_items = list(catalog_items or [])
        cls.detail_error = detail_error
        cls.search_outcomes = list(search_outcomes)

    def __init__(self, **kwargs: Any) -> None:
        type(self).constructed += 1
        proxy_url = urlparse(str(kwargs.get("proxy_url") or ""))
        assert proxy_url.hostname == "127.0.0.1"
        self.settings = kwargs["settings"]
        self.event_sink = kwargs.get("event_sink")
        self.prepared_session = kwargs.get("prepared_session")
        self.prevalidated_egress = kwargs.get("prevalidated_egress")
        self.prepared_session_refreshed = False
        self.egress_ip = (
            self.prepared_session.egress_ip
            if self.prepared_session is not None
            else self.prevalidated_egress.context.ip
            if self.prevalidated_egress is not None
            else None
        )
        if self.prepared_session is None:
            type(self).preparations += 1

    def search(self, _source: SearchSource, page: int | None = None):
        del page
        type(self).searches += 1
        if not type(self).search_outcomes:
            raise AssertionError("Unexpected catalog search")
        outcome = type(self).search_outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return CatalogSearchResult(
            items=list(type(self).catalog_items),
            page=1,
            total_pages=1,
            total_entries=0,
            per_page=5,
            next_page=None,
            provider_metadata={"provider": "local_same_profile_recovery"},
        )

    def fetch_detail(
        self,
        _candidate: CatalogItemCandidate,
        *,
        referer_url: str | None = None,
        early_filter_terms: tuple[str, ...] = (),
    ):
        del referer_url, early_filter_terms
        type(self).detail_calls += 1
        if type(self).detail_error is not None:
            raise type(self).detail_error
        raise AssertionError("Unexpected successful detail request")

    def bootstrap_for_session(self, _source_url: str, *, collect_datadome: bool = False) -> dict[str, Any]:
        assert collect_datadome is True
        assert self.prevalidated_egress is not None
        return {"bootstrap": "ok", "datadome_cookie": True, "cf_bm_cookie": True}

    def probe_catalog_api(self, _source_url: str, *, include_payload: bool = False) -> dict[str, Any]:
        assert include_payload is False
        return {
            "outcome": "accepted_json",
            "status_code": 200,
            "duration_ms": 1,
            "missing_required": [],
        }

    def export_prepared_session(self, *, proxy_session_id: str | None = None) -> PreparedCatalogSession:
        assert proxy_session_id
        assert self.egress_ip
        return _complete_context(proxy_session_id, egress_ip=self.egress_ip)

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


def _complete_context(proxy_session_id: str, *, egress_ip: str = "192.0.2.10") -> PreparedCatalogSession:
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
        egress_ip=egress_ip,
        egress_country_code="ES",
        egress_validated_at=datetime.now(UTC),
    )


@contextmanager
def _isolated_queue():
    base_settings = get_settings()
    queue_key = f"qa:catalog-recovery:{uuid4().hex}"
    settings = base_settings.model_copy(
        update={
            "worker_task_queue_key": queue_key,
            "worker_max_retry_attempts": 3,
        }
    )
    queue_client = redis_client_from_url(settings.redis_url, decode_responses=False, socket_timeout=3)
    try:
        yield settings, queue_client
    finally:
        keys = list(queue_client.scan_iter(match=f"{queue_key}*"))
        if keys:
            queue_client.delete(*keys)


def _consume_one(
    graph: FailStopGraph,
    settings,
    queue_client,
) -> tuple[MonitorTask, Any]:
    task = MonitorTask(
        source_id=graph.source_id,
        source_url="https://www.vinted.es/catalog?search_text=&order=newest_first",
        monitor_mode="window",
        trigger="scheduler",
        scheduler_config={"interval_seconds": 60, "jitter_percent": 0},
        proxy_profile_id=graph.proxy_id,
        proxy_identity_generation=graph.proxy_identity_generation,
    )
    assert enqueue_task(queue_client, task, queue_key=settings.worker_task_queue_key) is True
    reservation = reserve_task(
        queue_client,
        timeout=1,
        queue_key=settings.worker_task_queue_key,
        consumer_id=0,
    )
    assert reservation is not None
    TaskConsumer(settings, consumer_id=0)._consume_reservation(
        get_seen_cache(settings),
        reservation,
        queue_client=queue_client,
    )
    return task, reservation


def _assert_queue_acked(settings, queue_client, graph: FailStopGraph, reservation: Any) -> None:
    queue_key = settings.worker_task_queue_key
    assert queue_client.llen(queue_key) == 0
    assert queue_client.llen(processing_queue_key(queue_key)) == 0
    assert queue_client.llen(processing_queue_key(queue_key, 0)) == 0
    assert queue_client.llen(dead_letter_queue_key(queue_key)) == 0
    assert queue_client.get(pending_task_key(graph.source_id, queue_key)) is None
    assert queue_client.get(pending_payload_key(reservation.raw_payload, queue_key)) is None


def _probe_result(ip: str) -> ProxyEgressProbeResult:
    return ProxyEgressProbeResult(
        context=EgressContext(ip=ip, country="Spain", country_code="ES"),
        validated_at=datetime.now(UTC),
    )


def test_catalog_challenge_recovers_once_with_fresh_sticky_and_single_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LocalSameProfileRecoveryProvider.reset(
        VintedCatalogChallengeError("local initial Cloudflare challenge canary"),
        None,
    )
    monkeypatch.setattr(
        "vinted_monitor.services.runs.CurlCffiVintedCatalogProvider",
        LocalSameProfileRecoveryProvider,
    )
    probe_calls: list[str] = []

    def probe(**_kwargs: Any) -> ProxyEgressProbeResult:
        probe_calls.append("forced")
        return _probe_result("192.0.2.20")

    monkeypatch.setattr(runs_module, "probe_proxy_egress", probe)

    with _failstop_graph() as graph, _isolated_queue() as (settings, queue_client):
        with SessionLocal() as db:
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert profile is not None
            profile.failure_count = 1
            db.commit()

        task, reservation = _consume_one(graph, settings, queue_client)

        assert probe_calls == ["forced"]
        assert LocalSameProfileRecoveryProvider.constructed == 3
        assert LocalSameProfileRecoveryProvider.preparations == 1
        assert LocalSameProfileRecoveryProvider.searches == 2
        assert LocalSameProfileRecoveryProvider.closes == 3
        with SessionLocal() as db:
            runs = list(db.scalars(select(Run).where(Run.task_id == task.task_id)))
            assert len(runs) == 1
            run = runs[0]
            assert run.status == SUCCESS
            assert (run.runtime_metadata or {}).get("session_acquisition_attempts") == 2
            assert (run.runtime_metadata or {}).get("session_acquisition_egress_changed") is True
            phases = list(db.scalars(select(RunEvent.phase).where(RunEvent.run_id == run.id)))
            assert phases.count("session_acquisition_attempt_started") == 2
            assert phases.count("session_acquisition_attempt_failed") == 1
            assert phases.count("session_acquisition_attempt_succeeded") == 1
            assert phases.count("run_succeeded") == 1
            assert "run_failed" not in phases
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert profile is not None
            assert profile.failure_count == 0
            assert profile.cooldown_until is None
            sessions = list(
                db.scalars(
                    select(VintedSession)
                    .where(VintedSession.source_id == graph.source_id)
                    .order_by(VintedSession.id.asc())
                )
            )
            assert len(sessions) == 2
            assert sessions[0].id == graph.session_id and sessions[0].status == "invalid"
            assert sessions[1].status == "ready"
            assert sessions[1].egress_ip == "192.0.2.20"

        _assert_queue_acked(settings, queue_client, graph, reservation)


def test_both_catalog_attempts_fail_with_one_profile_penalty_and_one_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LocalSameProfileRecoveryProvider.reset(
        DataDomeChallengeError("local initial DataDome challenge canary"),
        VintedCatalogChallengeError("local replacement Cloudflare challenge canary"),
    )
    monkeypatch.setattr(
        "vinted_monitor.services.runs.CurlCffiVintedCatalogProvider",
        LocalSameProfileRecoveryProvider,
    )
    monkeypatch.setattr(runs_module, "probe_proxy_egress", lambda **_kwargs: _probe_result("192.0.2.20"))
    penalty_calls: list[int | None] = []
    original_penalty = runs_module.mark_proxy_challenge_detected

    def record_penalty(db, profile_id: int | None, **kwargs: Any) -> None:
        penalty_calls.append(profile_id)
        original_penalty(db, profile_id, **kwargs)

    monkeypatch.setattr(runs_module, "mark_proxy_challenge_detected", record_penalty)

    with _failstop_graph() as graph, _isolated_queue() as (settings, queue_client):
        failure_started_at = datetime.now(UTC)
        task, reservation = _consume_one(graph, settings, queue_client)

        assert penalty_calls == [graph.proxy_id]
        assert LocalSameProfileRecoveryProvider.constructed == 3
        assert LocalSameProfileRecoveryProvider.searches == 2
        assert LocalSameProfileRecoveryProvider.closes == 3
        with SessionLocal() as db:
            runs = list(db.scalars(select(Run).where(Run.task_id == task.task_id)))
            assert len(runs) == 1
            run = runs[0]
            assert run.status == FAILED
            assert (run.runtime_metadata or {}).get("failure_kind") == "profile_session_acquisition_exhausted"
            assert (run.runtime_metadata or {}).get("session_acquisition_attempts") == 2
            assert (run.runtime_metadata or {}).get("session_acquisition_last_reason") == "cloudflare_challenge"
            failed_events = list(
                db.scalars(
                    select(RunEvent).where(
                        RunEvent.run_id == run.id,
                        RunEvent.phase == "run_failed",
                    )
                )
            )
            assert len(failed_events) == 1
            assert (failed_events[0].details or {}).get("recovery_action") == (
                "wait_for_proxy_cooldown_or_retry_manually"
            )
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert profile is not None
            assert profile.failure_count == 2
            assert profile.cooldown_until is not None
            assert failure_started_at + timedelta(minutes=20) <= profile.cooldown_until
            assert profile.cooldown_until <= datetime.now(UTC) + timedelta(minutes=20)
            sessions = list(db.scalars(select(VintedSession).where(VintedSession.source_id == graph.source_id)))
            assert len(sessions) == 2
            assert all(session.status == "invalid" for session in sessions)
            assert all(decrypt_text(session.context_encrypted, settings.app_secret_key) == "{}" for session in sessions)

        _assert_queue_acked(settings, queue_client, graph, reservation)


def test_catalog_rate_limit_does_not_retry_or_penalize_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LocalSameProfileRecoveryProvider.reset(
        VintedCatalogRateLimitError(
            "local catalog rate limit canary",
            retry_after_seconds=2.0,
            retry_after_source="seconds",
        )
    )
    monkeypatch.setattr(
        "vinted_monitor.services.runs.CurlCffiVintedCatalogProvider",
        LocalSameProfileRecoveryProvider,
    )

    def unexpected_probe(**_kwargs: Any) -> ProxyEgressProbeResult:
        raise AssertionError("429 must not request a replacement sticky")

    monkeypatch.setattr(runs_module, "probe_proxy_egress", unexpected_probe)

    with _failstop_graph() as graph, _isolated_queue() as (settings, queue_client):
        task, reservation = _consume_one(graph, settings, queue_client)

        assert LocalSameProfileRecoveryProvider.constructed == 1
        assert LocalSameProfileRecoveryProvider.preparations == 0
        assert LocalSameProfileRecoveryProvider.searches == 1
        assert LocalSameProfileRecoveryProvider.closes == 1
        with SessionLocal() as db:
            runs = list(db.scalars(select(Run).where(Run.task_id == task.task_id)))
            assert len(runs) == 1
            run = runs[0]
            assert run.status == FAILED
            assert (run.runtime_metadata or {}).get("failure_kind") == "catalog_rate_limited"
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert profile is not None
            assert profile.failure_count == 0
            assert profile.cooldown_until is None
            session = db.get(VintedSession, graph.session_id)
            assert session is not None
            assert session.status == "invalid"
            assert decrypt_text(session.context_encrypted, settings.app_secret_key) == "{}"

        _assert_queue_acked(settings, queue_client, graph, reservation)


def test_transport_then_egress_diagnostic_failure_exhausts_before_second_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LocalSameProfileRecoveryProvider.reset(
        VintedCatalogTransportError("local initial proxy transport canary")
    )
    monkeypatch.setattr(
        "vinted_monitor.services.runs.CurlCffiVintedCatalogProvider",
        LocalSameProfileRecoveryProvider,
    )
    probe_calls: list[str] = []

    def failed_probe(**_kwargs: Any) -> ProxyEgressProbeResult:
        probe_calls.append("forced")
        return ProxyEgressProbeResult(
            context=EgressContext(),
            validated_at=None,
            error=VintedEgressDiagnosticError("local egress diagnostic canary"),
        )

    monkeypatch.setattr(runs_module, "probe_proxy_egress", failed_probe)

    with _failstop_graph() as graph, _isolated_queue() as (settings, queue_client):
        task, reservation = _consume_one(graph, settings, queue_client)

        assert probe_calls == ["forced"]
        assert LocalSameProfileRecoveryProvider.constructed == 1
        assert LocalSameProfileRecoveryProvider.preparations == 0
        assert LocalSameProfileRecoveryProvider.searches == 1
        assert LocalSameProfileRecoveryProvider.closes == 1
        with SessionLocal() as db:
            runs = list(db.scalars(select(Run).where(Run.task_id == task.task_id)))
            assert len(runs) == 1
            run = runs[0]
            assert run.status == FAILED
            metadata = run.runtime_metadata or {}
            assert metadata["failure_kind"] == "profile_session_acquisition_exhausted"
            assert metadata["session_acquisition_attempts"] == 2
            assert metadata["session_acquisition_last_reason"] == "egress_diagnostic_failed"
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert profile is not None
            assert profile.failure_count == 1
            assert profile.cooldown_until is not None

        _assert_queue_acked(settings, queue_client, graph, reservation)


def test_post_candidate_challenge_invalidates_context_without_replaying_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = CatalogItemCandidate(
        vinted_item_id="9900000001",
        title="Local post-candidate canary",
        brand=None,
        price_amount=None,
        currency="EUR",
        size=None,
        status=None,
        seller_login=None,
        seller_country=None,
        favorite_count=None,
        url="https://www.vinted.es/items/9900000001-local-post-candidate-canary",
        image_url=None,
    )
    LocalSameProfileRecoveryProvider.reset(
        None,
        catalog_items=[candidate],
        detail_error=DataDomeChallengeError("local post-candidate DataDome challenge canary"),
    )
    monkeypatch.setattr(
        "vinted_monitor.services.runs.CurlCffiVintedCatalogProvider",
        LocalSameProfileRecoveryProvider,
    )

    def unexpected_probe(**_kwargs: Any) -> ProxyEgressProbeResult:
        raise AssertionError("Post-candidate work must not request a replacement sticky")

    monkeypatch.setattr(runs_module, "probe_proxy_egress", unexpected_probe)

    with _failstop_graph() as graph, _isolated_queue() as (settings, queue_client):
        task, reservation = _consume_one(graph, settings, queue_client)

        assert LocalSameProfileRecoveryProvider.constructed == 1
        assert LocalSameProfileRecoveryProvider.searches == 1
        assert LocalSameProfileRecoveryProvider.detail_calls == 1
        assert LocalSameProfileRecoveryProvider.closes == 1
        with SessionLocal() as db:
            runs = list(db.scalars(select(Run).where(Run.task_id == task.task_id)))
            assert len(runs) == 1
            run = runs[0]
            assert run.status == FAILED
            assert (run.runtime_metadata or {}).get("failure_kind") == "datadome_challenge"
            assert (run.runtime_metadata or {}).get("session_acquisition_attempts") == 1
            phases = list(db.scalars(select(RunEvent.phase).where(RunEvent.run_id == run.id)))
            assert phases.count("session_acquisition_attempt_started") == 1
            assert phases.count("session_acquisition_attempt_succeeded") == 1
            assert phases.count("run_failed") == 1
            assert (run.runtime_metadata or {}).get("vinted_session_id") == graph.session_id
            profile = db.get(ProxyProfile, graph.proxy_id)
            assert profile is not None
            assert profile.failure_count == 2
            assert profile.cooldown_until is not None
            session = db.get(VintedSession, graph.session_id)
            assert session is not None and session.status == "invalid"

        _assert_queue_acked(settings, queue_client, graph, reservation)
