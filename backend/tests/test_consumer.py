from types import SimpleNamespace

from vinted_monitor.core.config import Settings
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.services.runs import SUCCESS
from vinted_monitor.services.task_queue import MonitorTask
from vinted_monitor.services.vinted_sessions import VintedSessionRequiredError
from vinted_monitor.worker.consumer import TaskConsumer


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
    )

    consumer._process_with_escalation(task)

    assert attempts == 1
