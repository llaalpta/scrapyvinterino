import json

import pytest
from pydantic import ValidationError

from vinted_monitor.core.config import Settings
from vinted_monitor.services.task_queue import (
    InvalidTaskPayloadError,
    MonitorTask,
    enqueue_task,
    reserve_task,
)


class FakeRedis:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload
        self.pushed: list[tuple[str, str]] = []

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        assert "LPUSH" in script
        self.pushed.append((keys[1], args[1]))
        return 1

    def blmove(
        self,
        _queue_key: str,
        _processing_key: str,
        _timeout: int,
        *,
        src: str,
        dest: str,
    ) -> str:
        assert src == "RIGHT"
        assert dest == "LEFT"
        assert self.payload is not None
        return json.dumps(self.payload)


def test_enqueue_task_serializes_current_payload_without_proxy_secrets() -> None:
    fake_redis = FakeRedis()
    task = MonitorTask(
        source_id=123,
        source_url="https://www.vinted.es/catalog?search_text=nike",
        monitor_mode="window",
        trigger="scheduler",
        scheduler_config={"interval_seconds": 300},
        proxy_profile_id=7,
        proxy_identity_generation="v1:1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        task_id="pytest-task",
        enqueued_at="2026-07-05T12:00:00+00:00",
    )

    assert enqueue_task(fake_redis, task, queue_key="pytest:queue") is True

    assert len(fake_redis.pushed) == 1
    queue_key, raw_payload = fake_redis.pushed[0]
    payload = json.loads(raw_payload)
    assert queue_key == "pytest:queue"
    assert payload["proxy_profile_id"] == 7
    assert payload["proxy_identity_generation"] == "v1:1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert "proxy_url" not in payload
    assert "proxy_url_template" not in payload


def test_reserve_task_rejects_unknown_fields_without_logging_values() -> None:
    secret_field_name = "user-password-secret-field"
    with pytest.raises(InvalidTaskPayloadError, match="unknown fields") as exc_info:
        reserve_task(
            FakeRedis(
                {
                    "source_id": 123,
                    "source_url": "https://www.vinted.es/catalog?search_text=nike",
                    "monitor_mode": "window",
                    "trigger": "scheduler",
                    "scheduler_config": {},
                    "proxy_profile_id": 7,
                    secret_field_name: "http://user:password@proxy.example:8000",
                    "task_id": "pytest-task",
                    "enqueued_at": "2026-07-05T12:00:00+00:00",
                }
            )
        )

    assert "user:password" not in str(exc_info.value)
    assert secret_field_name not in str(exc_info.value)


def test_reserve_task_reads_current_payload() -> None:
    reservation = reserve_task(
        FakeRedis(
            {
                "source_id": 123,
                "source_url": "https://www.vinted.es/catalog?search_text=nike",
                "monitor_mode": "window",
                "trigger": "scheduler",
                "scheduler_config": {},
                "proxy_profile_id": 7,
                "proxy_identity_generation": "v1:1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "task_id": "pytest-task",
                "enqueued_at": "2026-07-05T12:00:00+00:00",
            }
        )
    )

    assert reservation is not None
    assert reservation.task.source_id == 123
    assert reservation.task.proxy_profile_id == 7
    assert reservation.task.proxy_identity_generation == "v1:1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_reserve_task_rejects_proxy_payload_without_identity_generation() -> None:
    with pytest.raises(InvalidTaskPayloadError, match="invalid field values"):
        reserve_task(
            FakeRedis(
                {
                    "source_id": 123,
                    "source_url": "https://www.vinted.es/catalog?search_text=nike",
                    "monitor_mode": "window",
                    "trigger": "scheduler",
                    "scheduler_config": {},
                    "proxy_profile_id": 7,
                    "task_id": "pytest-task-stale-contract",
                    "enqueued_at": "2026-07-05T12:00:00+00:00",
                }
            )
        )


@pytest.mark.parametrize(
    "proxy_fields",
    [
        {},
        {"proxy_profile_id": None, "proxy_identity_generation": None},
    ],
)
def test_reserve_task_rejects_payload_without_proxy_binding(proxy_fields: dict) -> None:
    with pytest.raises(InvalidTaskPayloadError, match="invalid field values"):
        reserve_task(
            FakeRedis(
                {
                    "source_id": 123,
                    "source_url": "https://www.vinted.es/catalog?search_text=nike",
                    "monitor_mode": "window",
                    "trigger": "scheduler",
                    "scheduler_config": {},
                    "task_id": "pytest-task-no-proxy",
                    "enqueued_at": "2026-07-05T12:00:00+00:00",
                    **proxy_fields,
                }
            )
        )


def test_worker_reservation_settings_reject_invalid_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_reserve_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_consumer_count=0)
