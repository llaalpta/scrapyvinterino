import json

import pytest

from vinted_monitor.services.task_queue import TaskQueueError, dequeue_task


class FakeRedis:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def brpop(self, _queue_key: str, timeout: int = 0):
        return (_queue_key, json.dumps(self.payload))


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
