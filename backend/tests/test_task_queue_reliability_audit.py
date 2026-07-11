from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
import redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.db.models import Base, Run, SearchSource
from vinted_monitor.services import task_queue
from vinted_monitor.services.runs import (
    FAILED,
    FINALIZING,
    RUNNING,
    SUCCESS,
    recover_task_run_before_delivery,
)
from vinted_monitor.services.seen_cache import (
    DetailCandidateStateUpdate,
    serialize_candidate_state_update,
)
from vinted_monitor.services.task_queue import (
    InvalidTaskPayloadError,
    MonitorTask,
    TaskQueueError,
    pending_payload_key,
)
from vinted_monitor.worker import consumer as consumer_module
from vinted_monitor.worker import main as worker_main
from vinted_monitor.worker.consumer import TaskConsumer

QUEUE = "pytest:reliable-task-queue"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs) -> str:
    return "JSON"


class InMemoryRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.strings: dict[str, str] = {}

    def lpush(self, key: str, *values: str) -> int:
        target = self.lists.setdefault(key, [])
        for value in values:
            target.insert(0, value)
        return len(target)

    def rpush(self, key: str, *values: str) -> int:
        target = self.lists.setdefault(key, [])
        target.extend(values)
        return len(target)

    def blmove(
        self,
        first_list: str,
        second_list: str,
        timeout: int,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        return self.lmove(first_list, second_list, src=src, dest=dest)

    def lmove(self, first_list: str, second_list: str, src: str = "LEFT", dest: str = "RIGHT") -> str | None:
        source = self.lists.setdefault(first_list, [])
        if not source:
            return None
        value = source.pop(0 if src.upper() == "LEFT" else -1)
        destination = self.lists.setdefault(second_list, [])
        if dest.upper() == "LEFT":
            destination.insert(0, value)
        else:
            destination.append(value)
        return value

    def lrem(self, key: str, count: int, value: str) -> int:
        target = self.lists.setdefault(key, [])
        removed = 0
        indexes = range(len(target)) if count >= 0 else range(len(target) - 1, -1, -1)
        for index in list(indexes):
            if target[index] != value:
                continue
            target.pop(index)
            removed += 1
            if count and removed >= abs(count):
                break
        return removed

    def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        target = self.lists.get(key, [])
        stop = len(target) if end == -1 else end + 1
        return target[start:stop]

    def lpos(self, key: str, value: str) -> int | None:
        try:
            return self.lists.get(key, []).index(value)
        except ValueError:
            return None

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.strings:
            return False
        self.strings[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def exists(self, key: str) -> int:
        return int(key in self.strings or bool(self.lists.get(key)))

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(key in self.strings or key in self.lists)
            self.strings.pop(key, None)
            self.lists.pop(key, None)
        return removed

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])
        if "LRANGE" in script and "RPUSH" in script:
            payloads = list(self.lists.get(keys[0], []))
            self.rpush(keys[1], *payloads)
            if payloads:
                self.delete(keys[0])
            return len(payloads)
        if "EXISTS" in script and "LPUSH" in script:
            if self.exists(keys[0]):
                return 0
            self.set(keys[0], args[0])
            self.set(keys[2], args[2])
            self.lpush(keys[1], args[1])
            return 1
        if "removed_processing" in script:
            removed_processing = self.lrem(keys[0], 1, args[0])
            removed_ready = self.lrem(keys[1], 1, args[0])
            pending = self.get(keys[2])
            if pending == args[1]:
                self.delete(keys[2])
            self.delete(keys[3])
            return int(bool(removed_processing or removed_ready or pending == args[1] or pending is None))
        if "#KEYS == 4" in script:
            removed = self.lrem(keys[0], 1, args[0])
            if removed:
                self.lpush(keys[1], args[0])
                if len(keys) == 4 and self.get(keys[3]) == args[1]:
                    self.delete(keys[3])
                self.delete(keys[2])
                return 1
            if self.lpos(keys[1], args[0]) is not None:
                if len(keys) == 4 and self.get(keys[3]) == args[1]:
                    self.delete(keys[3])
                self.delete(keys[2])
                return 1
            return 0
        if "LPOS" in script and "LPUSH" in script:
            removed = self.lrem(keys[0], 1, args[0])
            if removed:
                self.lpush(keys[1], args[0])
                return 1
            if self.lpos(keys[1], args[0]) is not None:
                return 1
            if self.get(keys[2]) == args[1]:
                return 1
            return 0
        if "LREM" in script and "KEYS[3]" in script:
            removed = self.lrem(keys[0], 1, args[0])
            if removed:
                if self.get(keys[1]) == args[1]:
                    self.delete(keys[1])
                self.delete(keys[2])
            return removed
        raise AssertionError("unsupported Redis audit script")


class RecoverySeenCache:
    def __init__(self) -> None:
        self.finalized: list[DetailCandidateStateUpdate] = []

    def require_available(self) -> None:
        return None

    def finalize_candidate_states(
        self,
        monitor_id: int,
        policy_hash: str,
        update: DetailCandidateStateUpdate,
    ) -> None:
        self.finalized.append(update)


def _api(name: str) -> Callable:
    value = getattr(task_queue, name, None)
    assert callable(value), f"task queue reliability API is missing: {name}"
    return value


def _task(*, source_id: int = 41, task_id: str = "task-41") -> MonitorTask:
    return MonitorTask(
        source_id=source_id,
        source_url="https://www.vinted.es/catalog?order=newest_first",
        monitor_mode="window",
        trigger="scheduler",
        scheduler_config={"interval_seconds": 30},
        task_id=task_id,
        enqueued_at="2026-07-11T08:00:00+00:00",
    )


def _processing_key(queue_key: str = QUEUE) -> str:
    return f"{queue_key}:processing"


def _dead_letter_key(queue_key: str = QUEUE) -> str:
    return f"{queue_key}:dead-letter"


def _pending_key(source_id: int, queue_key: str = QUEUE) -> str:
    return f"{queue_key}:pending:{source_id}"


def test_two_scheduler_ticks_do_not_enqueue_same_source_twice() -> None:
    client = InMemoryRedis()
    enqueue_task = _api("enqueue_task")

    first_enqueued = enqueue_task(client, _task(task_id="tick-1"), queue_key=QUEUE)
    second_enqueued = enqueue_task(client, _task(task_id="tick-2"), queue_key=QUEUE)

    assert first_enqueued is True
    assert second_enqueued is False
    assert client.llen(QUEUE) == 1
    assert client.get(_pending_key(41)) == "tick-1"


def test_archiving_can_cancel_task_while_it_is_still_ready() -> None:
    client = InMemoryRedis()
    task = _task()
    assert task_queue.enqueue_task(client, task, queue_key=QUEUE) is True
    raw_payload = client.lrange(QUEUE, 0, -1)[0]
    reverse_key = pending_payload_key(raw_payload, QUEUE)

    assert task_queue.cancel_ready_task_for_source(client, task.source_id, queue_key=QUEUE) is True

    assert client.llen(QUEUE) == 0
    assert client.get(_pending_key(task.source_id)) is None
    assert client.get(reverse_key) is None


def test_reservation_moves_ready_task_to_processing_until_exact_ack() -> None:
    client = InMemoryRedis()
    enqueue_task = _api("enqueue_task")
    reserve_task = _api("reserve_task")
    ack_task = _api("ack_task")
    task = _task()
    assert enqueue_task(client, task, queue_key=QUEUE) is True

    reservation = reserve_task(client, timeout=0, queue_key=QUEUE)

    assert reservation is not None
    assert reservation.task.task_id == task.task_id
    assert client.llen(QUEUE) == 0
    assert client.lrange(_processing_key(), 0, -1) == [reservation.raw_payload]
    assert ack_task(client, reservation, queue_key=QUEUE) is True
    assert client.llen(_processing_key()) == 0
    assert client.get(_pending_key(task.source_id)) is None


def test_ack_does_not_clear_newer_pending_marker_for_same_source() -> None:
    client = InMemoryRedis()
    enqueue_task = _api("enqueue_task")
    reserve_task = _api("reserve_task")
    ack_task = _api("ack_task")
    task = _task(task_id="older-task")
    assert enqueue_task(client, task, queue_key=QUEUE) is True
    reservation = reserve_task(client, timeout=0, queue_key=QUEUE)
    assert reservation is not None
    client.set(_pending_key(task.source_id), "newer-task")

    assert ack_task(client, reservation, queue_key=QUEUE) is True

    assert client.get(_pending_key(task.source_id)) == "newer-task"


def test_requeue_is_idempotent_and_keeps_pending_marker() -> None:
    client = InMemoryRedis()
    enqueue_task = _api("enqueue_task")
    reserve_task = _api("reserve_task")
    requeue_task = _api("requeue_task")
    task = _task()
    assert enqueue_task(client, task, queue_key=QUEUE) is True
    reservation = reserve_task(client, timeout=0, queue_key=QUEUE)
    assert reservation is not None

    assert requeue_task(client, reservation, queue_key=QUEUE) is True
    assert requeue_task(client, reservation, queue_key=QUEUE) is True

    assert client.lrange(QUEUE, 0, -1) == [reservation.raw_payload]
    assert client.llen(_processing_key()) == 0
    assert client.get(_pending_key(task.source_id)) == task.task_id


def test_requeue_retry_does_not_duplicate_task_reserved_by_another_consumer() -> None:
    client = InMemoryRedis()
    task = _task()
    assert task_queue.enqueue_task(client, task, queue_key=QUEUE) is True
    first = task_queue.reserve_task(client, timeout=0, queue_key=QUEUE, consumer_id=0)
    assert first is not None
    assert task_queue.requeue_task(client, first, queue_key=QUEUE) is True
    second = task_queue.reserve_task(client, timeout=0, queue_key=QUEUE, consumer_id=1)
    assert second is not None

    assert task_queue.requeue_task(client, first, queue_key=QUEUE) is True

    assert client.llen(QUEUE) == 0
    assert client.llen(f"{QUEUE}:processing:0") == 0
    assert client.lrange(f"{QUEUE}:processing:1", 0, -1) == [second.raw_payload]


def test_startup_recovery_preserves_fifo_and_rebuilds_pending_markers() -> None:
    client = InMemoryRedis()
    enqueue_task = _api("enqueue_task")
    reserve_task = _api("reserve_task")
    recover_inflight_tasks = _api("recover_inflight_tasks")
    first = _task(source_id=41, task_id="first")
    second = _task(source_id=42, task_id="second")
    assert enqueue_task(client, first, queue_key=QUEUE) is True
    assert enqueue_task(client, second, queue_key=QUEUE) is True
    first_reservation = reserve_task(client, timeout=0, queue_key=QUEUE)
    second_reservation = reserve_task(client, timeout=0, queue_key=QUEUE)
    assert first_reservation is not None
    assert second_reservation is not None
    client.delete(_pending_key(first.source_id), _pending_key(second.source_id))

    assert recover_inflight_tasks(client, queue_key=QUEUE) == 2

    assert client.llen(_processing_key()) == 0
    recovered_first = reserve_task(client, timeout=0, queue_key=QUEUE)
    recovered_second = reserve_task(client, timeout=0, queue_key=QUEUE)
    assert recovered_first is not None
    assert recovered_second is not None
    assert [recovered_first.task.task_id, recovered_second.task.task_id] == ["first", "second"]
    assert client.get(_pending_key(first.source_id)) == first.task_id
    assert client.get(_pending_key(second.source_id)) == second.task_id


def test_malformed_processing_payload_moves_to_dead_letter_and_clears_identifiable_marker() -> None:
    client = InMemoryRedis()
    dead_letter_task = _api("dead_letter_task")
    raw_payload = json.dumps(
        {
            "source_id": 41,
            "task_id": "malformed-task",
            "source_url": "https://www.vinted.es/catalog",
            "monitor_mode": "window",
            "trigger": "scheduler",
            "unknown": "field",
        },
        separators=(",", ":"),
    )
    client.lpush(_processing_key(), raw_payload)
    client.set(_pending_key(41), "malformed-task")

    assert dead_letter_task(
        client,
        raw_payload,
        queue_key=QUEUE,
        source_id=41,
        task_id="malformed-task",
    ) is True

    assert client.llen(_processing_key()) == 0
    assert client.lrange(_dead_letter_key(), 0, -1) == [raw_payload]
    assert client.get(_pending_key(41)) is None


def test_unparseable_payload_uses_reverse_marker_to_unblock_source() -> None:
    client = InMemoryRedis()
    raw_payload = '{"source_id":'
    reverse_key = pending_payload_key(raw_payload, QUEUE)
    client.lpush(_processing_key(), raw_payload)
    client.set(_pending_key(41), "unparseable-task")
    client.set(reverse_key, json.dumps({"source_id": 41, "task_id": "unparseable-task"}))

    assert task_queue.dead_letter_task(client, raw_payload, queue_key=QUEUE) is True

    assert client.get(_pending_key(41)) is None
    assert client.get(reverse_key) is None
    assert client.lrange(_dead_letter_key(), 0, -1) == [raw_payload]


def _run_consumer_once(
    monkeypatch: pytest.MonkeyPatch,
    *,
    process: Callable[[MonitorTask], None],
) -> tuple[list, list]:
    assert "reserve_task" in TaskConsumer.run_forever.__code__.co_names
    reservation = SimpleNamespace(task=_task(), raw_payload="raw-task")
    calls = 0
    acknowledged: list = []
    requeued: list = []

    def reserve_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return reservation
        raise SystemExit("stop consumer after one reservation")

    class NullSession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

    monkeypatch.setattr(consumer_module, "get_seen_cache", lambda *args, **kwargs: SimpleNamespace(client=object()))
    monkeypatch.setattr(consumer_module, "recover_inflight_tasks", lambda *args, **kwargs: 0)
    monkeypatch.setattr(consumer_module, "SessionLocal", NullSession)
    monkeypatch.setattr(consumer_module, "recover_task_run_before_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr(consumer_module, "reserve_task", reserve_once, raising=False)
    monkeypatch.setattr(
        consumer_module,
        "ack_task",
        lambda client, value, queue_key: acknowledged.append(value) or True,
        raising=False,
    )
    monkeypatch.setattr(
        consumer_module,
        "requeue_task",
        lambda client, value, queue_key: requeued.append(value) or True,
        raising=False,
    )
    consumer = TaskConsumer(Settings(_env_file=None), consumer_id=0)
    monkeypatch.setattr(consumer, "_process_with_escalation", process)
    with pytest.raises(SystemExit, match="stop consumer"):
        consumer.run_forever()
    return acknowledged, requeued


def test_consumer_acknowledges_task_only_after_terminal_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    acknowledged, requeued = _run_consumer_once(monkeypatch, process=lambda task: None)

    assert len(acknowledged) == 1
    assert requeued == []


def test_consumer_requeues_reservation_after_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(task: MonitorTask) -> None:
        raise RuntimeError("unexpected consumer failure")

    acknowledged, requeued = _run_consumer_once(monkeypatch, process=fail)

    assert acknowledged == []
    assert len(requeued) == 1


def test_consumer_retries_ack_after_transient_redis_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    reservation = SimpleNamespace(task=_task(), raw_payload="raw-task")
    calls = 0
    sleeps: list[int] = []

    class NullSession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

    def flaky_ack(*args, **kwargs) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TaskQueueError("transient ack failure")
        return True

    monkeypatch.setattr(consumer_module, "SessionLocal", NullSession)
    monkeypatch.setattr(consumer_module, "recover_task_run_before_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr(consumer_module, "ack_task", flaky_ack)
    monkeypatch.setattr(consumer_module.time, "sleep", sleeps.append)
    consumer = TaskConsumer(Settings(_env_file=None), consumer_id=0)
    monkeypatch.setattr(consumer, "_process_with_escalation", lambda task: None)

    consumer._consume_reservation(SimpleNamespace(client=object()), reservation)

    assert calls == 2
    assert sleeps == [1]


def test_consumer_retries_requeue_after_transient_redis_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    reservation = SimpleNamespace(task=_task(), raw_payload="raw-task")
    calls = 0
    sleeps: list[int] = []

    class NullSession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

    def flaky_requeue(*args, **kwargs) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TaskQueueError("transient requeue failure")
        return True

    monkeypatch.setattr(consumer_module, "SessionLocal", NullSession)
    monkeypatch.setattr(consumer_module, "recover_task_run_before_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr(consumer_module, "requeue_task", flaky_requeue)
    monkeypatch.setattr(consumer_module.time, "sleep", sleeps.append)
    consumer = TaskConsumer(Settings(_env_file=None), consumer_id=0)
    monkeypatch.setattr(
        consumer,
        "_process_with_escalation",
        lambda task: (_ for _ in ()).throw(RuntimeError("processing failed")),
    )

    consumer._consume_reservation(SimpleNamespace(client=object()), reservation)

    assert calls == 2
    assert sleeps == [1]


def test_consumer_backs_off_after_reserve_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[int] = []
    queue_timeouts: list[tuple[bool, float | None]] = []

    def reserve_after_error(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TaskQueueError("redis unavailable")
        raise SystemExit("stop after backoff")

    def fake_seen_cache(*args, **kwargs):
        return SimpleNamespace(client=object())

    monkeypatch.setattr(consumer_module, "get_seen_cache", fake_seen_cache)
    monkeypatch.setattr(
        consumer_module,
        "redis_client_from_url",
        lambda url, decode_responses, socket_timeout: queue_timeouts.append(
            (decode_responses, socket_timeout)
        )
        or object(),
    )
    monkeypatch.setattr(consumer_module, "reserve_task", reserve_after_error)
    recovered: list[tuple[str, ...]] = []

    def recover_after_outage(client, queue_key, processing_keys):
        recovered.append(processing_keys)
        if len(recovered) == 2:
            raise TaskQueueError("redis still unavailable")
        return int(len(recovered) == 3)

    monkeypatch.setattr(
        consumer_module,
        "recover_inflight_tasks",
        recover_after_outage,
    )
    monkeypatch.setattr(consumer_module.time, "sleep", sleeps.append)

    with pytest.raises(SystemExit, match="stop after backoff"):
        TaskConsumer(Settings(_env_file=None), consumer_id=0).run_forever()

    processing_key = f"{Settings(_env_file=None).worker_task_queue_key}:processing:0"
    assert sleeps == [1, 1]
    assert recovered == [(processing_key,), (processing_key,), (processing_key,)]
    assert queue_timeouts == [(False, 10)]


def test_consumer_dead_letters_reserved_malformed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    moved: list[tuple] = []
    error = InvalidTaskPayloadError(
        "invalid audit payload",
        raw_payload="malformed-raw",
        source_id=41,
        task_id="malformed-task",
    )

    def reserve_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        raise SystemExit("stop consumer after malformed payload")

    monkeypatch.setattr(consumer_module, "get_seen_cache", lambda *args, **kwargs: SimpleNamespace(client=object()))
    monkeypatch.setattr(consumer_module, "reserve_task", reserve_once)
    monkeypatch.setattr(consumer_module, "recover_inflight_tasks", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        consumer_module,
        "dead_letter_task",
        lambda client, raw_payload, queue_key, source_id, task_id, processing_key_override: moved.append(
            (raw_payload, source_id, task_id)
        )
        or True,
    )

    with pytest.raises(SystemExit, match="stop consumer"):
        TaskConsumer(Settings(_env_file=None), consumer_id=0).run_forever()

    assert moved == [("malformed-raw", 41, "malformed-task")]


def test_worker_recovers_inflight_queue_before_constructing_producer(monkeypatch: pytest.MonkeyPatch) -> None:
    recovered: list[str] = []
    settings = Settings(_env_file=None)
    cache = SimpleNamespace(client=object(), require_available=lambda: recovered.append("available"))

    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_logging", lambda level: None)
    monkeypatch.setattr(worker_main, "validate_proxy_settings", lambda value: None)
    monkeypatch.setattr(worker_main, "get_seen_cache", lambda value: cache)
    monkeypatch.setattr(
        worker_main,
        "recover_inflight_tasks",
        lambda client, queue_key: recovered.append("queue") or 0,
    )

    def stop_at_producer(value):
        assert recovered == ["available", "queue"]
        raise SystemExit("stop after startup recovery")

    monkeypatch.setattr(worker_main, "SchedulerRunner", stop_at_producer)

    with pytest.raises(SystemExit, match="startup recovery"):
        worker_main.main()


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


@pytest.mark.parametrize("terminal_status", [SUCCESS, FAILED])
def test_redelivered_terminal_task_skips_second_network_run(
    monkeypatch: pytest.MonkeyPatch,
    audit_session_factory,
    terminal_status: str,
) -> None:
    task = _task(task_id=f"terminal-{terminal_status}")
    with audit_session_factory() as db:
        source = SearchSource(
            name=f"queue audit {terminal_status}",
            url=task.source_url,
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        task.source_id = source.id
        existing = Run(
            source_id=source.id,
            task_id=task.task_id,
            status=terminal_status,
            trigger="scheduler",
            items_found=0,
            items_new=0,
            items_filter_passed=0,
            items_discarded_by_filters=0,
            items_filter_pending=0,
            opportunities_created=0,
            runtime_metadata={"task_id": task.task_id, "attempt": 1},
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)
        existing_id = existing.id

    monkeypatch.setattr(consumer_module, "SessionLocal", audit_session_factory)
    acknowledged: list = []

    def forbidden_network_run(*args, **kwargs) -> None:
        raise AssertionError("redelivery must not execute Vinted traffic twice")

    monkeypatch.setattr(
        consumer_module,
        "ack_task",
        lambda client, reservation, queue_key: acknowledged.append(reservation) or True,
    )
    monkeypatch.setattr(consumer_module, "requeue_task", forbidden_network_run)
    consumer = TaskConsumer(Settings(_env_file=None), consumer_id=0)
    monkeypatch.setattr(consumer, "_process_with_escalation", forbidden_network_run)
    reservation = SimpleNamespace(task=task, raw_payload="terminal-task")
    consumer._consume_reservation(SimpleNamespace(client=object()), reservation)

    assert acknowledged == [reservation]
    with audit_session_factory() as db:
        persisted = db.get(Run, existing_id)
        assert persisted is not None
        assert persisted.status == terminal_status


def test_redelivered_finalizing_task_is_reconciled_without_new_run(audit_session_factory) -> None:
    task = _task(task_id="finalizing-task")
    transition = DetailCandidateStateUpdate()
    with audit_session_factory() as db:
        source = SearchSource(
            name="queue audit finalizing",
            url=task.source_url,
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            monitor_mode="window",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        task.source_id = source.id
        previous = Run(
            source_id=source.id,
            task_id=task.task_id,
            status=FINALIZING,
            trigger="scheduler",
            items_found=1,
            items_new=1,
            items_filter_passed=1,
            items_discarded_by_filters=0,
            items_filter_pending=0,
            opportunities_created=1,
            runtime_metadata={
                "task_id": task.task_id,
                "candidate_state_transition_status": "pending",
                "candidate_state_transition_policy_hash": "audit-policy",
                "candidate_state_transition": serialize_candidate_state_update(transition),
                "candidate_state_close_session_on_finish": False,
            },
        )
        db.add(previous)
        db.commit()
        db.refresh(previous)
        previous_id = previous.id

    cache = RecoverySeenCache()
    with audit_session_factory() as db:
        recovered = recover_task_run_before_delivery(
            db,
            source_id=task.source_id,
            task_id=task.task_id,
            seen_cache=cache,
        )

    assert recovered is not None
    assert recovered.id == previous_id
    assert recovered.status == SUCCESS
    assert cache.finalized == [transition]
    with audit_session_factory() as db:
        assert db.query(Run).filter(Run.source_id == task.source_id).count() == 1


def test_redelivered_running_task_closes_orphan_before_retry(audit_session_factory) -> None:
    task = _task(task_id="running-task")
    with audit_session_factory() as db:
        source = SearchSource(
            name="queue audit running",
            url=task.source_url,
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            monitor_mode="window",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        task.source_id = source.id
        orphan = Run(
            source_id=source.id,
            task_id=task.task_id,
            status=RUNNING,
            trigger="scheduler",
            started_at=datetime.now(UTC),
            items_found=0,
            items_new=0,
            items_filter_passed=0,
            items_discarded_by_filters=0,
            items_filter_pending=0,
            opportunities_created=0,
            runtime_metadata={"task_id": task.task_id, "attempt": 2},
        )
        db.add(orphan)
        db.commit()
        db.refresh(orphan)
        orphan_id = orphan.id

    with audit_session_factory() as db:
        recovered = recover_task_run_before_delivery(
            db,
            source_id=task.source_id,
            task_id=task.task_id,
            seen_cache=RecoverySeenCache(),
        )

    assert recovered is not None
    assert recovered.status == FAILED
    assert recovered.runtime_metadata["failure_kind"] == "worker_task_delivery_interrupted"
    assert TaskConsumer(Settings(_env_file=None))._next_recovery_attempt(recovered) == 3
    with audit_session_factory() as db:
        persisted = db.get(Run, orphan_id)
        assert persisted is not None
        assert persisted.status == FAILED
        assert persisted.finished_at is not None
        assert "interrupted" in (persisted.error_message or "").lower()


def test_interrupted_delivery_at_retry_limit_is_not_run_again() -> None:
    previous = SimpleNamespace(
        status=FAILED,
        runtime_metadata={"failure_kind": "worker_task_delivery_interrupted", "attempt": 3},
    )

    assert TaskConsumer(Settings(_env_file=None))._next_recovery_attempt(previous) is None


def _reachable_real_redis(*, decode_responses: bool = True) -> redis.Redis:
    configured_url = get_settings().redis_url
    parsed = urlsplit(configured_url)
    fallback_url = urlunsplit((parsed.scheme, f"127.0.0.1:{parsed.port or 6379}", parsed.path, parsed.query, parsed.fragment))
    candidates = (fallback_url,) if sys.platform == "win32" and parsed.hostname == "redis" else (configured_url, fallback_url)
    for url in dict.fromkeys(candidates):
        client = redis.Redis.from_url(
            url,
            decode_responses=decode_responses,
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


def test_binary_queue_dead_letters_non_utf8_payload_without_poison_loop() -> None:
    client = _reachable_real_redis(decode_responses=False)
    queue_key = f"pytest:binary-task-queue:{uuid4().hex}"
    processing_key = f"{queue_key}:processing:3"
    dead_letter_key = f"{queue_key}:dead-letter"
    raw_payload = b"\xff\xfeinvalid-task"
    reverse_key = pending_payload_key(raw_payload, queue_key)
    try:
        client.lpush(queue_key, raw_payload)
        with pytest.raises(InvalidTaskPayloadError) as exc_info:
            task_queue.reserve_task(client, timeout=1, queue_key=queue_key, consumer_id=3)

        error = exc_info.value
        assert error.raw_queue_payload == raw_payload
        assert error.processing_key == processing_key
        assert task_queue.dead_letter_task(
            client,
            error.raw_queue_payload,
            queue_key=queue_key,
            processing_key_override=error.processing_key,
        ) is True
        assert client.llen(queue_key) == 0
        assert client.llen(processing_key) == 0
        assert client.lrange(dead_letter_key, 0, -1) == [raw_payload]
    finally:
        client.delete(queue_key, processing_key, dead_letter_key, reverse_key)
        client.close()


def test_real_redis_reservation_recovery_and_ack() -> None:
    client = _reachable_real_redis()
    queue_key = f"pytest:reliable-task-queue:{uuid4().hex}"
    task = _task(source_id=91, task_id=f"task-{uuid4().hex}")
    enqueue_task = _api("enqueue_task")
    reserve_task = _api("reserve_task")
    recover_inflight_tasks = _api("recover_inflight_tasks")
    ack_task = _api("ack_task")
    keys = (
        queue_key,
        _processing_key(queue_key),
        f"{queue_key}:processing:0",
        _dead_letter_key(queue_key),
        _pending_key(task.source_id, queue_key),
        pending_payload_key(task_queue._serialize_task(task), queue_key),
    )
    try:
        assert enqueue_task(client, task, queue_key=queue_key) is True
        assert enqueue_task(client, _task(source_id=task.source_id, task_id="duplicate"), queue_key=queue_key) is False
        first_reservation = reserve_task(client, timeout=0, queue_key=queue_key)
        assert first_reservation is not None
        assert client.llen(queue_key) == 0
        assert client.llen(_processing_key(queue_key)) == 1

        assert recover_inflight_tasks(client, queue_key=queue_key) == 1
        recovered = reserve_task(client, timeout=0, queue_key=queue_key)
        assert recovered is not None
        assert recovered.task.task_id == task.task_id
        assert ack_task(client, recovered, queue_key=queue_key) is True
        assert client.llen(_processing_key(queue_key)) == 0
        assert client.get(_pending_key(task.source_id, queue_key)) is None
    finally:
        client.delete(*keys)
        client.close()
