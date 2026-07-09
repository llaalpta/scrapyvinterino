import json

import pytest

from vinted_monitor.services.task_queue import MonitorTask, TaskQueueError, dequeue_task, enqueue_task


class FakeRedis:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload
        self.pushed: list[tuple[str, str]] = []

    def brpop(self, _queue_key: str, timeout: int = 0):
        assert self.payload is not None
        return (_queue_key, json.dumps(self.payload))

    def lpush(self, queue_key: str, payload: str) -> None:
        self.pushed.append((queue_key, payload))


def test_enqueue_task_serializes_current_payload_without_proxy_secrets() -> None:
    fake_redis = FakeRedis()
    task = MonitorTask(
        source_id=123,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
        scheduler_config={"interval_seconds": 300},
        proxy_profile_id=7,
        task_id="pytest-task",
        enqueued_at="2026-07-05T12:00:00+00:00",
    )

    enqueue_task(fake_redis, task, queue_key="pytest:queue")

    assert len(fake_redis.pushed) == 1
    queue_key, raw_payload = fake_redis.pushed[0]
    payload = json.loads(raw_payload)
    assert queue_key == "pytest:queue"
    assert payload["proxy_profile_id"] == 7
    assert "proxy_url" not in payload
    assert "proxy_url_template" not in payload


def test_dequeue_task_rejects_unknown_fields() -> None:
    with pytest.raises(TaskQueueError, match="unknown fields"):
        dequeue_task(
            FakeRedis(
                {
                    "source_id": 123,
                    "source_url": "https://www.vinted.es/catalog?search_text=nike",
                    "monitor_mode": "window",
                    "trigger": "scheduler",
                    "scheduler_config": {},
                    "proxy_profile_id": 7,
                    "proxy_url_template": "http://user:password@proxy.example:8000",
                    "task_id": "pytest-task",
                    "enqueued_at": "2026-07-05T12:00:00+00:00",
                }
            )
        )


def test_dequeue_task_reads_current_payload() -> None:
    task = dequeue_task(
        FakeRedis(
            {
                "source_id": 123,
                "source_url": "https://www.vinted.es/catalog?search_text=nike",
                "monitor_mode": "window",
                "trigger": "scheduler",
                "scheduler_config": {},
                "proxy_profile_id": 7,
                "task_id": "pytest-task",
                "enqueued_at": "2026-07-05T12:00:00+00:00",
            }
        )
    )

    assert task is not None
    assert task.source_id == 123
    assert task.proxy_profile_id == 7
