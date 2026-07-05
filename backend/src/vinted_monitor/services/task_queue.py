from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime

from redis import Redis
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

TASK_QUEUE_KEY = "vinted:task_queue"


class TaskQueueError(RuntimeError):
    """Raised when a task queue operation fails."""


@dataclass
class MonitorTask:
    """A unit of work enqueued by the producer and consumed by a worker."""

    source_id: int
    source_url: str
    monitor_mode: str
    trigger: str
    filter_rule_ids: list[int] = field(default_factory=list)
    scheduler_config: dict = field(default_factory=dict)
    proxy_profile_id: int | None = None
    enqueued_at: str = ""
    task_id: str = ""

    def __post_init__(self) -> None:
        if not self.task_id:
            self.task_id = str(uuid.uuid4())
        if not self.enqueued_at:
            self.enqueued_at = datetime.now(UTC).isoformat()


MONITOR_TASK_FIELD_NAMES = {task_field.name for task_field in fields(MonitorTask)}


def enqueue_task(client: Redis, task: MonitorTask, queue_key: str = TASK_QUEUE_KEY) -> None:
    """Push a task to the left of the Redis list (LPUSH).

    The consumer pops from the right (BRPOP) to maintain FIFO order.
    """
    try:
        payload = json.dumps(asdict(task), default=str, separators=(",", ":"))
        client.lpush(queue_key, payload)
    except RedisError as exc:
        raise TaskQueueError(f"Failed to enqueue task {task.task_id}: {exc}") from exc


def dequeue_task(client: Redis, timeout: int = 0, queue_key: str = TASK_QUEUE_KEY) -> MonitorTask | None:
    """Block-pop a task from the right of the Redis list (BRPOP).

    Returns ``None`` if the timeout expires without receiving a task.
    """
    try:
        result = client.brpop(queue_key, timeout=timeout)
    except RedisTimeoutError:
        # BRPOP timeout expired; empty queue, not an error
        return None
    except RedisError as exc:
        raise TaskQueueError(f"Failed to dequeue task: {exc}") from exc
    if result is None:
        return None
    _, raw_payload = result
    try:
        data = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TaskQueueError(f"Failed to deserialize task: {exc}") from exc
    if not isinstance(data, dict):
        raise TaskQueueError("Failed to deserialize task: payload must be an object")
    return MonitorTask(**{key: value for key, value in data.items() if key in MONITOR_TASK_FIELD_NAMES})


def queue_length(client: Redis, queue_key: str = TASK_QUEUE_KEY) -> int:
    """Return the current number of tasks in the queue."""
    try:
        return client.llen(queue_key)
    except RedisError:
        return -1
