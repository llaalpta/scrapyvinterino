import hashlib
from types import SimpleNamespace

import pytest

from vinted_monitor.core.config import Settings
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.vinted_catalog import (
    VintedCatalogChallengeError,
    VintedCatalogRateLimitError,
    VintedCatalogSessionContextError,
    VintedCatalogSessionError,
)
from vinted_monitor.services.runs import FAILED, FINALIZING
from vinted_monitor.services.task_queue import InvalidTaskPayloadError, MonitorTask, TaskReservation
from vinted_monitor.services.vinted_sessions import VintedSessionRequiredError
from vinted_monitor.worker import consumer as consumer_module
from vinted_monitor.worker.consumer import TaskConsumer

PROXY_ID = 123
PROXY_IDENTITY = "v1:1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class NullSession:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def error(self, event: str, **details) -> None:
        self.events.append((event, details))


def test_consumer_dead_letters_exact_payload_and_logs_no_payload_content(monkeypatch) -> None:
    secret_canary = "proxy-password-canary"
    raw_payload = f'{{"proxy_profile_id":null,"password":"{secret_canary}"}}'
    error = InvalidTaskPayloadError(
        "Failed to deserialize task: invalid field values",
        raw_payload=raw_payload,
        source_id=17,
        task_id=secret_canary,
        processing_key="queue:processing:3",
        raw_queue_payload=raw_payload.encode(),
    )
    calls: list[tuple[bytes, dict]] = []
    monkeypatch.setattr(
        consumer_module,
        "dead_letter_task",
        lambda _client, payload, **kwargs: calls.append((payload, kwargs)) or True,
    )
    consumer = TaskConsumer(Settings(_env_file=None, worker_task_queue_key="queue"), consumer_id=3)
    logger = RecordingLogger()
    consumer.logger = logger

    consumer._dead_letter_invalid_task(object(), error)

    assert calls == [
        (
            raw_payload.encode(),
            {
                "queue_key": "queue",
                "source_id": 17,
                "task_id": secret_canary,
                "processing_key_override": "queue:processing:3",
            },
        )
    ]
    assert logger.events == [
        (
            "consumer_invalid_task_dead_lettered",
            {
                "error": "Failed to deserialize task: invalid field values",
                "moved": True,
                "source_id": 17,
                "task_id_fingerprint": hashlib.sha256(secret_canary.encode()).hexdigest()[:16],
            },
        )
    ]
    assert secret_canary not in repr(logger.events)


@pytest.mark.parametrize(
    "challenge",
    [
        DataDomeChallengeError("challenge"),
        VintedCatalogChallengeError("challenge"),
        VintedCatalogSessionContextError("contradictory session context"),
        VintedCatalogSessionError("session rejected"),
        VintedCatalogRateLimitError(
            "rate limited",
            retry_after_seconds=2.0,
            retry_after_source="seconds",
        ),
    ],
)
def test_consumer_does_not_escalate_direct_terminal_catalog_response(monkeypatch, challenge: Exception) -> None:
    attempts = 0

    def fake_execute(self, task, attempt):
        nonlocal attempts
        attempts += 1
        raise challenge

    monkeypatch.setattr(TaskConsumer, "_execute_run", fake_execute)

    consumer = TaskConsumer(Settings(worker_max_retry_attempts=2), consumer_id=0)
    task = MonitorTask(
        source_id=1,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
        proxy_profile_id=PROXY_ID,
        proxy_identity_generation=PROXY_IDENTITY,
    )

    consumer._process_with_escalation(task)

    assert attempts == 1


def test_consumer_does_not_retry_when_prepared_vinted_session_is_missing(monkeypatch) -> None:
    attempts = 0

    def fake_execute(self, task, attempt):
        nonlocal attempts
        attempts += 1
        raise VintedSessionRequiredError("prepare session first")

    monkeypatch.setattr(TaskConsumer, "_execute_run", fake_execute)

    consumer = TaskConsumer(Settings(worker_max_retry_attempts=3), consumer_id=0)
    task = MonitorTask(
        source_id=1,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
        proxy_profile_id=PROXY_ID,
        proxy_identity_generation=PROXY_IDENTITY,
    )

    consumer._process_with_escalation(task)

    assert attempts == 1


def test_consumer_requeues_finalizing_run_without_ack(monkeypatch) -> None:
    task = MonitorTask(
        source_id=1,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
        proxy_profile_id=PROXY_ID,
        proxy_identity_generation=PROXY_IDENTITY,
    )
    reservation = TaskReservation(task=task, raw_payload="finalizing-payload")
    acknowledged: list[TaskReservation] = []
    requeued: list[TaskReservation] = []
    monkeypatch.setattr(consumer_module, "SessionLocal", NullSession)
    monkeypatch.setattr(consumer_module, "recover_task_run_before_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        consumer_module,
        "ack_task",
        lambda client, value, queue_key: acknowledged.append(value) or True,
    )
    monkeypatch.setattr(
        consumer_module,
        "requeue_task",
        lambda client, value, queue_key: requeued.append(value) or True,
    )
    consumer = TaskConsumer(Settings(_env_file=None), consumer_id=0)
    monkeypatch.setattr(
        consumer,
        "_execute_run",
        lambda value, attempt: SimpleNamespace(id=99, status=FINALIZING),
    )

    consumer._consume_reservation(SimpleNamespace(client=object()), reservation)

    assert acknowledged == []
    assert requeued == [reservation]


def test_consumer_acks_terminal_challenge_run_without_resume_or_requeue(monkeypatch) -> None:
    task = MonitorTask(
        source_id=1,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
        proxy_profile_id=PROXY_ID,
        proxy_identity_generation=PROXY_IDENTITY,
    )
    reservation = TaskReservation(task=task, raw_payload="challenge-payload")
    previous_run = SimpleNamespace(
        id=98,
        status=FAILED,
        runtime_metadata={"failure_kind": "datadome_challenge", "attempt": 1},
    )
    resumed_attempts: list[int] = []
    acknowledged: list[TaskReservation] = []
    requeued: list[TaskReservation] = []
    monkeypatch.setattr(consumer_module, "SessionLocal", NullSession)
    monkeypatch.setattr(
        consumer_module,
        "recover_task_run_before_delivery",
        lambda *args, **kwargs: previous_run,
    )
    monkeypatch.setattr(
        consumer_module,
        "ack_task",
        lambda client, value, queue_key: acknowledged.append(value) or True,
    )
    monkeypatch.setattr(
        consumer_module,
        "requeue_task",
        lambda client, value, queue_key: requeued.append(value) or True,
    )
    consumer = TaskConsumer(Settings(_env_file=None, worker_max_retry_attempts=3), consumer_id=0)
    monkeypatch.setattr(
        consumer,
        "_process_with_escalation",
        lambda value, first_attempt: resumed_attempts.append(first_attempt),
    )

    consumer._consume_reservation(SimpleNamespace(client=object()), reservation)

    assert resumed_attempts == []
    assert acknowledged == [reservation]
    assert requeued == []
