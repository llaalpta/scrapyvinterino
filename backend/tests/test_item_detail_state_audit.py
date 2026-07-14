from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
import redis
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.retry import Retry
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import Base, ErrorLog, Item, Opportunity, Run, RunEvent, SearchSource
from vinted_monitor.db.session import SessionLocal, engine
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.services.items import get_or_persist_catalog_item
from vinted_monitor.services.monitor_sessions import start_monitor_session
from vinted_monitor.services.runs import (
    FAILED,
    FINALIZING,
    SUCCESS,
    RunAlreadyActiveError,
    _close_owned_provider,
    execute_monitor_baseline,
    execute_monitor_run,
    execute_monitor_session_prepare,
)
from vinted_monitor.services.scheduler import RunEgress
from vinted_monitor.services.seen_cache import (
    DetailCandidateStateUpdate,
    DetailRetryRecord,
    RedisSeenCache,
    SeenCacheOwnershipError,
    SeenCacheUnavailableError,
)

PREFIX = "pytest-state-audit"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs) -> str:
    return "JSON"


class AuditSeenCache:
    def __init__(
        self,
        *,
        due_retries: list[DetailRetryRecord] | None = None,
        finalize_failures: int = 0,
        release_failures: int = 0,
    ) -> None:
        self.processing: set[str] = set()
        self.seen: set[str] = set()
        self.detail_retries = {
            retry.candidate.vinted_item_id: retry for retry in (due_retries or [])
        }
        self.finalize_failures = finalize_failures
        self.release_failures = release_failures
        self.finalize_calls: list[DetailCandidateStateUpdate] = []

    def require_available(self) -> None:
        return None

    def has_baseline(self, monitor_id: int, policy_hash: str) -> bool:
        return True

    def mark_baseline(self, monitor_id: int, policy_hash: str) -> None:
        return None

    def claim_unseen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> set[str]:
        pending_ids = set(self.detail_retries)
        claimed = {
            item_id
            for item_id in vinted_item_ids
            if item_id not in self.seen and item_id not in self.processing and item_id not in pending_ids
        }
        self.processing.update(claimed)
        return claimed

    def claim_unseen_with_recovery(
        self,
        monitor_id: int,
        policy_hash: str,
        candidates: list[CatalogItemCandidate],
    ) -> set[str]:
        claimed = self.claim_unseen(
            monitor_id,
            policy_hash,
            [candidate.vinted_item_id for candidate in candidates],
        )
        now = datetime.now(UTC)
        self.stage_candidate_retries(
            monitor_id,
            policy_hash,
            tuple(
                DetailRetryRecord(candidate, 0, now, "detail_claim_recovery")
                for candidate in candidates
                if candidate.vinted_item_id in claimed
            ),
        )
        return claimed

    def claim_due_detail_retries(
        self,
        monitor_id: int,
        policy_hash: str,
        *,
        due_at: datetime,
        limit: int,
    ) -> list[DetailRetryRecord]:
        due = [
            retry
            for retry in self.detail_retries.values()
            if retry.next_attempt_at <= due_at and retry.candidate.vinted_item_id not in self.processing
        ][:limit]
        self.processing.update(retry.candidate.vinted_item_id for retry in due)
        return due

    def mark_seen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.finalize_candidate_states(
            monitor_id,
            policy_hash,
            DetailCandidateStateUpdate(terminal_ids=tuple(vinted_item_ids)),
        )

    def stage_candidate_retries(
        self,
        monitor_id: int,
        policy_hash: str,
        retries: tuple[DetailRetryRecord, ...],
    ) -> None:
        for retry in retries:
            self.detail_retries[retry.candidate.vinted_item_id] = retry

    def finalize_candidate_states(
        self,
        monitor_id: int,
        policy_hash: str,
        update: DetailCandidateStateUpdate,
    ) -> None:
        self.finalize_calls.append(update)
        if self.finalize_failures:
            self.finalize_failures -= 1
            raise SeenCacheUnavailableError("transient finalize failure")
        for item_id in update.terminal_ids:
            self.seen.add(item_id)
            self.detail_retries.pop(item_id, None)
            self.processing.discard(item_id)
        for retry in update.retries:
            item_id = retry.candidate.vinted_item_id
            self.detail_retries[item_id] = retry
            self.processing.discard(item_id)

    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        if self.release_failures:
            self.release_failures -= 1
            raise SeenCacheUnavailableError("transient release failure")
        self.processing.difference_update(vinted_item_ids)


class RedisFailureAfterPing:
    def ping(self) -> bool:
        return True

    def get(self, key: str) -> None:
        return None

    def delete(self, *keys: str) -> int:
        raise RedisConnectionError("connection dropped during delete")

    def time(self) -> tuple[int, int]:
        return 1_000, 0

    def pipeline(self, *, transaction: bool = True):
        return RedisPipelineFailure()


class RedisPipelineFailure:
    def __getattr__(self, name: str):
        def enqueue(*args, **kwargs):
            return self

        return enqueue

    def execute(self) -> list:
        raise RedisConnectionError("connection dropped during exec")


class LockRedis:
    def __init__(self) -> None:
        self.strings: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def exists(self, key: str) -> int:
        return int(key in self.strings)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.strings:
            return False
        self.strings[key] = value
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(key in self.strings)
            self.strings.pop(key, None)
        return removed

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        keys = keys_and_args[:numkeys]
        owner_token = keys_and_args[numkeys]
        for key in keys:
            if self.strings.get(key) == owner_token:
                self.strings.pop(key, None)
        return 1


class AuditProvider:
    settings = SimpleNamespace(
        vinted_detail_max_candidates_per_run=5,
        vinted_detail_max_attempts=3,
        vinted_detail_retry_backoffs_seconds=(30, 120),
    )

    def __init__(self, *, item_count: int = 1, challenge_on: str | None = None) -> None:
        self.item_count = item_count
        self.challenge_on = challenge_on
        self.detail_calls: list[str] = []

    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        items = [_candidate(str(index)) for index in range(self.item_count)]
        return CatalogSearchResult(
            items=items,
            page=1,
            total_pages=1,
            total_entries=len(items),
            per_page=5,
            next_page=None,
            provider_metadata={"provider": "state-audit"},
        )

    def fetch_detail(
        self,
        candidate: CatalogItemCandidate,
        *,
        referer_url: str | None = None,
    ) -> CatalogItemDetail:
        self.detail_calls.append(candidate.vinted_item_id)
        if candidate.vinted_item_id == self.challenge_on:
            raise DataDomeChallengeError("audit DataDome challenge")
        return CatalogItemDetail(
            vinted_item_id=candidate.vinted_item_id,
            description="Audit detail",
            photos=[f"https://images.example.test/{candidate.vinted_item_id}.webp"],
        )


def _candidate(suffix: str) -> CatalogItemCandidate:
    item_id = f"{PREFIX}-{suffix}"
    return CatalogItemCandidate(
        vinted_item_id=item_id,
        title=f"Audit item {suffix}",
        brand="Audit brand",
        price_amount=Decimal("5.00"),
        currency="EUR",
        size="M",
        status="Muy bueno",
        seller_login="audit-seller",
        seller_country=None,
        favorite_count=0,
        url=f"https://www.vinted.es/items/{item_id}",
        image_url=None,
    )


def _direct_egress() -> RunEgress:
    return RunEgress(mode="direct")


def _redis_cache_with_post_ping_failure() -> RedisSeenCache:
    return RedisSeenCache(
        client=RedisFailureAfterPing(),
        seen_ttl_seconds=86_400,
        processing_ttl_seconds=120,
        max_per_monitor=10_000,
    )


def _reachable_real_redis() -> redis.Redis:
    configured_url = get_settings().redis_url
    parsed = urlsplit(configured_url)
    fallback_url = urlunsplit((parsed.scheme, f"127.0.0.1:{parsed.port or 6379}", parsed.path, parsed.query, parsed.fragment))
    if sys.platform == "win32" and parsed.hostname == "redis":
        candidates = (fallback_url,)
    else:
        candidates = (configured_url, fallback_url)
    for url in dict.fromkeys(candidates):
        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            protocol=2,
            retry=Retry(NoBackoff(), 0),
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        try:
            client.ping()
            return client
        except RedisError:
            client.close()
    pytest.skip("Redis is not reachable")


def _cleanup(session_factory=SessionLocal) -> None:
    with session_factory() as db:
        source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.name.like(f"{PREFIX}%"))))
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id.in_(source_ids)))) if source_ids else []
        item_ids = list(db.scalars(select(Item.id).where(Item.vinted_item_id.like(f"{PREFIX}%"))))
        if source_ids:
            db.query(Opportunity).filter(Opportunity.source_id.in_(source_ids)).delete(synchronize_session=False)
        if item_ids:
            db.query(Opportunity).filter(Opportunity.item_id.in_(item_ids)).delete(synchronize_session=False)
        if run_ids:
            db.query(RunEvent).filter(RunEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(ErrorLog).filter(ErrorLog.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
        if source_ids:
            db.query(RunEvent).filter(RunEvent.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(ErrorLog).filter(ErrorLog.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(SearchSource).filter(SearchSource.id.in_(source_ids)).delete(synchronize_session=False)
        if item_ids:
            db.query(Item).filter(Item.id.in_(item_ids)).delete(synchronize_session=False)
        db.commit()


@pytest.fixture
def audit_session_factory():
    audit_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(audit_engine)
    factory = sessionmaker(bind=audit_engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        audit_engine.dispose()


@pytest.fixture
def source_id(audit_session_factory) -> int:
    with audit_session_factory() as db:
        started_at = datetime.now(UTC)
        source = SearchSource(
            name=f"{PREFIX}-monitor",
            url="https://www.vinted.es/catalog?order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            monitor_started_at=started_at,
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        start_monitor_session(db, source, started_at=started_at, allow_manual=True)
        db.commit()
        db.refresh(source)
        return source.id


def test_redis_release_normalizes_connection_drop_after_successful_ping() -> None:
    cache = _redis_cache_with_post_ping_failure()

    with pytest.raises(SeenCacheUnavailableError, match="Redis seen cache is unavailable"):
        cache.release_processing(1, "policy", [f"{PREFIX}-release"])


def test_redis_finalize_normalizes_connection_drop_during_transaction() -> None:
    cache = _redis_cache_with_post_ping_failure()

    with pytest.raises(SeenCacheUnavailableError, match="Redis seen cache is unavailable"):
        cache.finalize_candidate_states(
            1,
            "policy",
            DetailCandidateStateUpdate(terminal_ids=(f"{PREFIX}-finalize",)),
        )


def test_stale_worker_cannot_release_reacquired_processing_lock() -> None:
    client = LockRedis()
    first_worker = RedisSeenCache(client, 86_400, 120, 10_000)
    second_worker = RedisSeenCache(client, 86_400, 120, 10_000)
    item_id = f"{PREFIX}-lock-owner"
    processing_key = first_worker._processing_key(1, "policy", item_id)

    assert first_worker.claim_unseen(1, "policy", [item_id]) == {item_id}
    client.strings.pop(processing_key)
    assert second_worker.claim_unseen(1, "policy", [item_id]) == {item_id}

    first_worker.release_processing(1, "policy", [item_id])

    assert client.exists(processing_key) == 1


def test_real_redis_owner_transition_survives_stale_release() -> None:
    client = _reachable_real_redis()
    monitor_id = 2_000_000_000
    policy_hash = f"audit-{uuid4().hex}"
    candidate = _candidate(uuid4().hex)
    item_id = candidate.vinted_item_id
    first_worker = RedisSeenCache(client, 300, 30, 100)
    second_worker = RedisSeenCache(client, 300, 30, 100)
    processing_key = first_worker._processing_key(monitor_id, policy_hash, item_id)
    seen_key = first_worker._seen_key(monitor_id, policy_hash, item_id)
    keys_to_clean = (
        processing_key,
        seen_key,
        first_worker._seen_index_key(monitor_id, policy_hash),
        first_worker._detail_retry_key(monitor_id, policy_hash, item_id),
        first_worker._detail_retry_index_key(monitor_id, policy_hash),
    )

    try:
        assert first_worker.claim_unseen_with_recovery(monitor_id, policy_hash, [candidate]) == {item_id}
        assert client.exists(first_worker._detail_retry_key(monitor_id, policy_hash, item_id)) == 1
        client.delete(processing_key)
        claimed_retries = second_worker.claim_due_detail_retries(
            monitor_id,
            policy_hash,
            due_at=datetime.now(UTC),
            limit=1,
        )
        assert [retry.candidate.vinted_item_id for retry in claimed_retries] == [item_id]

        first_worker.release_processing(monitor_id, policy_hash, [item_id])

        assert client.get(processing_key) == second_worker.owner_token
        assert client.exists(second_worker._detail_retry_key(monitor_id, policy_hash, item_id)) == 1
        second_worker.finalize_candidate_states(
            monitor_id,
            policy_hash,
            DetailCandidateStateUpdate(terminal_ids=(item_id,)),
        )
        assert client.exists(seen_key) == 1
        assert client.exists(processing_key) == 0
    finally:
        client.delete(*keys_to_clean)
        client.close()


def test_real_redis_stale_worker_cannot_finalize_another_workers_claim() -> None:
    client = _reachable_real_redis()
    monitor_id = 2_000_000_001
    policy_hash = f"audit-{uuid4().hex}"
    candidate = _candidate(uuid4().hex)
    item_id = candidate.vinted_item_id
    first_worker = RedisSeenCache(client, 300, 30, 100)
    second_worker = RedisSeenCache(client, 300, 30, 100)
    processing_key = first_worker._processing_key(monitor_id, policy_hash, item_id)
    seen_key = first_worker._seen_key(monitor_id, policy_hash, item_id)
    retry_key = first_worker._detail_retry_key(monitor_id, policy_hash, item_id)
    retry_index_key = first_worker._detail_retry_index_key(monitor_id, policy_hash)
    keys_to_clean = (
        processing_key,
        seen_key,
        first_worker._seen_index_key(monitor_id, policy_hash),
        retry_key,
        retry_index_key,
    )

    try:
        assert first_worker.claim_unseen_with_recovery(monitor_id, policy_hash, [candidate]) == {item_id}
        client.delete(processing_key)
        claimed = second_worker.claim_due_detail_retries(
            monitor_id,
            policy_hash,
            due_at=datetime.now(UTC),
            limit=1,
        )
        assert [retry.candidate.vinted_item_id for retry in claimed] == [item_id]

        with pytest.raises(SeenCacheOwnershipError, match="owned by another worker"):
            first_worker.finalize_candidate_states(
                monitor_id,
                policy_hash,
                DetailCandidateStateUpdate(terminal_ids=(item_id,)),
            )

        assert client.exists(seen_key) == 0
        assert client.exists(retry_key) == 1
        assert client.get(processing_key) == second_worker.owner_token
        assert item_id in client.zrange(retry_index_key, 0, -1)
    finally:
        client.delete(*keys_to_clean)
        client.close()


def test_real_redis_due_retry_claim_is_atomic_with_terminal_transition() -> None:
    client = _reachable_real_redis()
    monitor_id = 2_000_000_002
    first_worker = RedisSeenCache(client, 300, 30, 100)
    second_worker = RedisSeenCache(client, 300, 30, 100)

    try:
        for _ in range(20):
            policy_hash = f"audit-{uuid4().hex}"
            candidate = _candidate(uuid4().hex)
            item_id = candidate.vinted_item_id
            retry = DetailRetryRecord(
                candidate=candidate,
                attempt_count=1,
                next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
                failure_kind="detail_timeout",
            )
            first_worker.stage_candidate_retries(monitor_id, policy_hash, (retry,))
            barrier = Barrier(2)

            def finalize_terminal(
                race_barrier: Barrier = barrier,
                race_policy_hash: str = policy_hash,
                race_item_id: str = item_id,
            ) -> str:
                race_barrier.wait(timeout=5)
                try:
                    first_worker.finalize_candidate_states(
                        monitor_id,
                        race_policy_hash,
                        DetailCandidateStateUpdate(terminal_ids=(race_item_id,)),
                    )
                    return "terminal"
                except SeenCacheOwnershipError:
                    return "owned"

            def claim_retry(
                race_barrier: Barrier = barrier,
                race_policy_hash: str = policy_hash,
            ) -> list[DetailRetryRecord]:
                race_barrier.wait(timeout=5)
                return second_worker.claim_due_detail_retries(
                    monitor_id,
                    race_policy_hash,
                    due_at=datetime.now(UTC),
                    limit=1,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                terminal_future = executor.submit(finalize_terminal)
                claim_future = executor.submit(claim_retry)
                terminal_outcome = terminal_future.result(timeout=5)
                claimed = claim_future.result(timeout=5)

            seen_key = first_worker._seen_key(monitor_id, policy_hash, item_id)
            retry_key = first_worker._detail_retry_key(monitor_id, policy_hash, item_id)
            processing_key = first_worker._processing_key(monitor_id, policy_hash, item_id)
            if claimed:
                assert terminal_outcome == "owned"
                assert client.exists(seen_key) == 0
                assert client.exists(retry_key) == 1
                assert client.get(processing_key) == second_worker.owner_token
            else:
                assert terminal_outcome == "terminal"
                assert client.exists(seen_key) == 1
                assert client.exists(retry_key) == 0
                assert client.exists(processing_key) == 0
            client.delete(
                seen_key,
                retry_key,
                processing_key,
                first_worker._seen_index_key(monitor_id, policy_hash),
                first_worker._detail_retry_index_key(monitor_id, policy_hash),
            )
    finally:
        client.close()


def test_finalize_failure_keeps_sql_and_terminal_status_consistent(source_id: int, audit_session_factory) -> None:
    cache = AuditSeenCache(finalize_failures=1)

    with audit_session_factory() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=AuditProvider(),
            seen_cache=cache,
            egress=_direct_egress(),
        )

    with audit_session_factory() as db:
        persisted = db.get(Run, run.id)
        opportunity_count = db.scalar(
            select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id)
        )
        phases = list(
            db.scalars(select(RunEvent.phase).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc()))
        )

    assert persisted is not None
    assert persisted.status in {SUCCESS, FAILED}
    if persisted.status == SUCCESS:
        assert opportunity_count == 1
        assert phases.count("run_succeeded") == 1
        assert "run_failed" not in phases
    else:
        assert opportunity_count == 0
        assert phases.count("run_failed") == 1
        assert "run_succeeded" not in phases
    assert cache.processing == set()


def test_sql_commit_failure_after_redis_transition_never_marks_run_failed(
    source_id: int,
    audit_session_factory,
) -> None:
    cache = AuditSeenCache()

    with audit_session_factory() as db:
        original_commit = db.commit
        commit_count = 0

        def fail_final_status_commits() -> None:
            nonlocal commit_count
            commit_count += 1
            if commit_count in {3, 4}:
                raise RuntimeError("simulated final SQL commit failure")
            original_commit()

        db.commit = fail_final_status_commits
        pending_run = execute_monitor_run(
            db,
            source_id,
            provider=AuditProvider(),
            seen_cache=cache,
            egress=_direct_egress(),
        )

    with audit_session_factory() as db:
        persisted = db.get(Run, pending_run.id)
        phases = list(db.scalars(select(RunEvent.phase).where(RunEvent.run_id == pending_run.id)))

    assert persisted is not None
    assert persisted.status == FINALIZING
    assert "run_failed" not in phases
    assert "run_succeeded" not in phases
    assert f"{PREFIX}-0" in cache.seen

    with audit_session_factory() as db:
        reconciled = execute_monitor_run(
            db,
            source_id,
            provider=AuditProvider(item_count=0),
            seen_cache=cache,
            require_active=False,
            egress=_direct_egress(),
        )

    with audit_session_factory() as db:
        completed = db.get(Run, pending_run.id)
        completed_phases = list(db.scalars(select(RunEvent.phase).where(RunEvent.run_id == pending_run.id)))

    assert reconciled.status == SUCCESS
    assert completed is not None
    assert completed.status == SUCCESS
    assert completed_phases.count("run_succeeded") == 1
    assert "run_failed" not in completed_phases


def test_persistent_finalize_failure_converges_before_next_catalog_run(
    source_id: int,
    audit_session_factory,
) -> None:
    item_id = f"{PREFIX}-0"
    cache = AuditSeenCache(finalize_failures=2)

    with audit_session_factory() as db:
        pending_run = execute_monitor_run(
            db,
            source_id,
            provider=AuditProvider(),
            seen_cache=cache,
            egress=_direct_egress(),
        )

    with audit_session_factory() as db:
        persisted_pending = db.get(Run, pending_run.id)
        pending_phases = list(db.scalars(select(RunEvent.phase).where(RunEvent.run_id == pending_run.id)))
        opportunity_count = db.scalar(
            select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id)
        )

    assert persisted_pending is not None
    assert persisted_pending.status == "finalizing"
    assert opportunity_count == 1
    assert "run_succeeded" not in pending_phases
    assert "run_failed" not in pending_phases
    assert item_id in cache.detail_retries

    with audit_session_factory() as db:
        next_run = execute_monitor_run(
            db,
            source_id,
            provider=AuditProvider(item_count=0),
            seen_cache=cache,
            require_active=False,
            egress=_direct_egress(),
        )

    with audit_session_factory() as db:
        reconciled_run = db.get(Run, pending_run.id)
        reconciled_phases = list(db.scalars(select(RunEvent.phase).where(RunEvent.run_id == pending_run.id)))
        final_opportunity_count = db.scalar(
            select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id)
        )

    assert next_run.status == SUCCESS
    assert reconciled_run is not None
    assert reconciled_run.status == SUCCESS
    assert reconciled_phases.count("run_succeeded") == 1
    assert "run_failed" not in reconciled_phases
    assert "redis_candidate_state_reconciled" in reconciled_phases
    assert final_opportunity_count == 1
    assert item_id in cache.seen
    assert item_id not in cache.detail_retries
    assert cache.processing == set()


def test_transient_failure_while_preserving_challenge_keeps_terminal_run_and_retry(
    source_id: int,
    audit_session_factory,
) -> None:
    item_id = f"{PREFIX}-0"
    cache = AuditSeenCache(finalize_failures=1)
    provider = AuditProvider(challenge_on=item_id)

    with audit_session_factory() as db:
        returned_run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_direct_egress(),
        )

    with audit_session_factory() as db:
        run = db.scalar(select(Run).where(Run.source_id == source_id).order_by(Run.id.desc()).limit(1))
        phases = list(db.scalars(select(RunEvent.phase).where(RunEvent.run_id == run.id))) if run else []

    assert run is not None
    assert returned_run.id == run.id
    assert returned_run.status == FAILED
    assert run.status == FAILED
    assert phases.count("run_failed") == 1
    assert item_id in cache.detail_retries
    assert cache.detail_retries[item_id].attempt_count == 1
    assert cache.detail_retries[item_id].failure_kind == "detail_antibot_challenge"
    assert cache.processing == set()


def test_release_failure_does_not_mask_primary_run_error(
    source_id: int,
    audit_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = AuditSeenCache(release_failures=1)

    def fail_after_claim(*args, **kwargs):
        raise RuntimeError("primary evaluation failure")

    monkeypatch.setattr("vinted_monitor.services.runs._evaluate_monitor_candidates", fail_after_claim)

    with audit_session_factory() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=AuditProvider(),
            seen_cache=cache,
            egress=_direct_egress(),
        )

    with audit_session_factory() as db:
        persisted = db.get(Run, run.id)
        phases = list(db.scalars(select(RunEvent.phase).where(RunEvent.run_id == run.id)))

    assert persisted is not None
    assert persisted.status == FAILED
    assert persisted.error_message == "primary evaluation failure"
    assert phases.count("run_failed") == 1
    assert cache.processing == set()


def test_owned_provider_close_failure_is_best_effort() -> None:
    provider = SimpleNamespace(close=lambda: (_ for _ in ()).throw(RuntimeError("close failed")))

    _close_owned_provider(provider, owned_provider=True)


def test_process_crash_immediately_after_claim_has_durable_candidate_recovery(
    source_id: int,
    audit_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_id = f"{PREFIX}-0"
    cache = AuditSeenCache()

    def crash_before_database_lookup(*args, **kwargs):
        raise SystemExit("simulated worker death after Redis claim")

    monkeypatch.setattr(
        "vinted_monitor.services.runs._existing_opportunity_item_ids",
        crash_before_database_lookup,
    )

    with audit_session_factory() as db, pytest.raises(SystemExit, match="simulated worker death"):
        execute_monitor_run(
            db,
            source_id,
            provider=AuditProvider(),
            seen_cache=cache,
            egress=_direct_egress(),
        )

    assert item_id in cache.detail_retries
    assert cache.detail_retries[item_id].attempt_count == 0
    assert cache.detail_retries[item_id].failure_kind == "detail_claim_recovery"


def test_challenge_attempt_counter_only_advances_for_failing_candidate(source_id: int, audit_session_factory) -> None:
    first = _candidate("retry-first")
    failing = _candidate("retry-failing")
    due_at = datetime.now(UTC) - timedelta(seconds=1)
    cache = AuditSeenCache(
        due_retries=[
            DetailRetryRecord(first, attempt_count=1, next_attempt_at=due_at, failure_kind="detail_timeout"),
            DetailRetryRecord(failing, attempt_count=1, next_attempt_at=due_at, failure_kind="detail_timeout"),
        ]
    )
    provider = AuditProvider(item_count=0, challenge_on=failing.vinted_item_id)

    with audit_session_factory() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_direct_egress(),
        )

    assert run.status == FAILED
    assert provider.detail_calls == [first.vinted_item_id, failing.vinted_item_id]
    assert cache.detail_retries[first.vinted_item_id].attempt_count == 1
    assert cache.detail_retries[first.vinted_item_id].failure_kind == "detail_run_aborted_before_commit"
    assert cache.detail_retries[failing.vinted_item_id].attempt_count == 2
    assert cache.detail_retries[failing.vinted_item_id].failure_kind == "detail_antibot_challenge"
    assert cache.processing == set()


def test_stale_running_run_is_closed_before_monitor_continues(source_id: int, audit_session_factory) -> None:
    with audit_session_factory() as db:
        stale_run = Run(
            source_id=source_id,
            status="running",
            trigger="scheduled",
            started_at=datetime.now(UTC) - timedelta(hours=24),
            finished_at=None,
            items_found=0,
            items_new=0,
            items_filter_passed=0,
            items_discarded_by_filters=0,
            items_filter_pending=0,
            opportunities_created=0,
            runtime_metadata={},
        )
        db.add(stale_run)
        db.commit()
        db.refresh(stale_run)
        stale_run_id = stale_run.id

    with audit_session_factory() as db:
        resumed_run = execute_monitor_run(
            db,
            source_id,
            provider=AuditProvider(),
            seen_cache=AuditSeenCache(),
            egress=_direct_egress(),
        )

    with audit_session_factory() as db:
        persisted_stale = db.get(Run, stale_run_id)
        persisted_resumed = db.get(Run, resumed_run.id)

    assert persisted_stale is not None
    assert persisted_stale.status == FAILED
    assert persisted_stale.finished_at is not None
    assert "stale" in (persisted_stale.error_message or "").lower()
    assert persisted_resumed is not None
    assert persisted_resumed.status == SUCCESS


@pytest.mark.parametrize("action", [execute_monitor_baseline, execute_monitor_session_prepare])
def test_non_reconciling_actions_reject_finalizing_run(
    action,
    source_id: int,
    audit_session_factory,
) -> None:
    with audit_session_factory() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = False
        db.add(
            Run(
                source_id=source_id,
                status=FINALIZING,
                trigger="scheduler",
                items_found=1,
                items_new=1,
                items_filter_passed=1,
                items_discarded_by_filters=0,
                items_filter_pending=0,
                opportunities_created=1,
                runtime_metadata={},
            )
        )
        db.commit()

    with audit_session_factory() as db, pytest.raises(RunAlreadyActiveError, match="running run"):
        action(db, source_id)


def test_get_or_persist_catalog_item_is_safe_under_concurrent_insert() -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("the item insertion race requires PostgreSQL")
    probe_engine = create_engine(engine.url, connect_args={"connect_timeout": 3})
    try:
        with probe_engine.connect() as connection:
            connection.execute(select(1))
    except OperationalError:
        pytest.skip("PostgreSQL is not reachable")
    finally:
        probe_engine.dispose()

    candidate = _candidate("concurrent")
    select_barrier = Barrier(2)

    def synchronize_item_lookup(conn, cursor, statement, parameters, context, executemany) -> None:
        if "FROM items" in statement and candidate.vinted_item_id in str(parameters):
            select_barrier.wait(timeout=5)

    def persist_in_thread() -> tuple[str, int | str]:
        with SessionLocal() as db:
            try:
                item = get_or_persist_catalog_item(db, candidate)
                db.commit()
                return "ok", item.id
            except Exception as exc:
                db.rollback()
                return "error", exc.__class__.__name__

    _cleanup()
    event.listen(engine, "after_cursor_execute", synchronize_item_lookup)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: persist_in_thread(), range(2)))
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_item_lookup)

    errors = [value for outcome, value in results if outcome == "error"]
    item_ids = [value for outcome, value in results if outcome == "ok"]
    with SessionLocal() as db:
        row_count = db.scalar(
            select(func.count()).select_from(Item).where(Item.vinted_item_id == candidate.vinted_item_id)
        )

    try:
        assert errors == []
        assert len(item_ids) == 2
        assert len(set(item_ids)) == 1
        assert row_count == 1
    finally:
        _cleanup()
