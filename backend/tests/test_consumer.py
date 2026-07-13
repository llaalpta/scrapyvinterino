from types import SimpleNamespace

from vinted_monitor.core.config import Settings
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.services.runs import FAILED, FINALIZING, SUCCESS
from vinted_monitor.services.task_queue import MonitorTask, TaskReservation
from vinted_monitor.services.vinted_sessions import VintedSessionRequiredError
from vinted_monitor.worker import consumer as consumer_module
from vinted_monitor.worker.consumer import TaskConsumer


class NullSession:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def test_consumer_retries_datadome_challenge_without_creating_proxy_sessions(monkeypatch) -> None:
    attempts = 0

    def fake_execute(self, task, attempt):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DataDomeChallengeError("challenge")
        return SimpleNamespace(
            id=42,
            status=SUCCESS,
            items_found=0,
            items_new=0,
        )

    monkeypatch.setattr(TaskConsumer, "_execute_run", fake_execute)

    consumer = TaskConsumer(Settings(worker_max_retry_attempts=2), consumer_id=0)
    task = MonitorTask(
        source_id=1,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
    )

    consumer._process_with_escalation(task)

    assert attempts == 2


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
        proxy_profile_id=123,
        proxy_identity_generation="v1:1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    consumer._process_with_escalation(task)

    assert attempts == 1


def test_consumer_requeues_finalizing_run_without_ack(monkeypatch) -> None:
    task = MonitorTask(
        source_id=1,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
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


def test_consumer_resumes_remaining_challenge_attempt_after_crash(monkeypatch) -> None:
    task = MonitorTask(
        source_id=1,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
    )
    reservation = TaskReservation(task=task, raw_payload="challenge-payload")
    previous_run = SimpleNamespace(
        id=98,
        status=FAILED,
        runtime_metadata={"failure_kind": "datadome_challenge", "attempt": 1},
    )
    resumed_attempts: list[int] = []
    acknowledged: list[TaskReservation] = []
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
    consumer = TaskConsumer(Settings(_env_file=None, worker_max_retry_attempts=3), consumer_id=0)
    monkeypatch.setattr(
        consumer,
        "_process_with_escalation",
        lambda value, first_attempt: resumed_attempts.append(first_attempt),
    )

    consumer._consume_reservation(SimpleNamespace(client=object()), reservation)

    assert resumed_attempts == [2]
    assert acknowledged == [reservation]
