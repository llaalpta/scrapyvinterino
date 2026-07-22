from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

TASK_QUEUE_KEY = "vinted:task_queue"


class TaskQueueError(RuntimeError):
    """Raised when a task queue operation fails."""


class InvalidTaskPayloadError(TaskQueueError):
    """Raised after an invalid payload has already been reserved for processing."""

    def __init__(
        self,
        message: str,
        *,
        raw_payload: str,
        source_id: int | None = None,
        task_id: str | None = None,
        processing_key: str | None = None,
        raw_queue_payload: str | bytes | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_payload = raw_payload
        self.source_id = source_id
        self.task_id = task_id
        self.processing_key = processing_key
        self.raw_queue_payload = raw_queue_payload if raw_queue_payload is not None else raw_payload


@dataclass
class MonitorTask:
    """A unit of work enqueued by the producer and consumed by a worker."""

    source_id: int
    source_url: str
    monitor_mode: str
    trigger: str
    proxy_profile_id: int
    proxy_identity_generation: str
    scheduler_config: dict = field(default_factory=dict)
    enqueued_at: str = ""
    task_id: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.proxy_profile_id, int)
            or isinstance(self.proxy_profile_id, bool)
            or self.proxy_profile_id <= 0
            or not isinstance(self.proxy_identity_generation, str)
            or not re.fullmatch(r"v1:[1-9]\d*:[0-9a-f]{64}", self.proxy_identity_generation)
        ):
            raise ValueError("MonitorTask requires a valid proxy identity binding")
        if not self.task_id:
            self.task_id = str(uuid.uuid4())
        if not self.enqueued_at:
            self.enqueued_at = datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class TaskReservation:
    task: MonitorTask
    raw_payload: str
    processing_key: str = ""


MONITOR_TASK_FIELD_NAMES = {task_field.name for task_field in fields(MonitorTask)}


def processing_queue_key(queue_key: str = TASK_QUEUE_KEY, consumer_id: int | None = None) -> str:
    suffix = "processing" if consumer_id is None else f"processing:{consumer_id}"
    return f"{queue_key}:{suffix}"


def dead_letter_queue_key(queue_key: str = TASK_QUEUE_KEY) -> str:
    return f"{queue_key}:dead-letter"


def pending_task_key(source_id: int, queue_key: str = TASK_QUEUE_KEY) -> str:
    return f"{queue_key}:pending:{source_id}"


def pending_payload_key(raw_payload: str | bytes, queue_key: str = TASK_QUEUE_KEY) -> str:
    encoded_payload = raw_payload if isinstance(raw_payload, bytes) else raw_payload.encode("utf-8")
    digest = hashlib.sha256(encoded_payload).hexdigest()
    return f"{queue_key}:pending-payload:{digest}"


def enqueue_task(client: Redis, task: MonitorTask, queue_key: str = TASK_QUEUE_KEY) -> bool:
    """Atomically enqueue one FIFO task while its monitor has no pending work."""
    payload = _serialize_task(task)
    script = """
    if redis.call('EXISTS', KEYS[1]) == 1 then
        return 0
    end
    redis.call('SET', KEYS[1], ARGV[1])
    redis.call('SET', KEYS[3], ARGV[3])
    redis.call('LPUSH', KEYS[2], ARGV[2])
    return 1
    """
    try:
        return bool(
            client.eval(
                script,
                3,
                pending_task_key(task.source_id, queue_key),
                queue_key,
                pending_payload_key(payload, queue_key),
                task.task_id,
                payload,
                _serialize_task_identity(task.source_id, task.task_id),
            )
        )
    except RedisError as exc:
        raise TaskQueueError(f"Failed to enqueue task {task.task_id}: {exc}") from exc


def reserve_task(
    client: Redis,
    timeout: int = 0,
    queue_key: str = TASK_QUEUE_KEY,
    consumer_id: int | None = None,
) -> TaskReservation | None:
    """Atomically reserve the oldest task in the processing list."""
    destination_key = processing_queue_key(queue_key, consumer_id)
    try:
        raw_payload = client.blmove(
            queue_key,
            destination_key,
            timeout,
            src="RIGHT",
            dest="LEFT",
        )
    except RedisTimeoutError as exc:
        raise TaskQueueError("Timed out while reserving task") from exc
    except UnicodeError as exc:
        raise TaskQueueError("Failed to decode reserved task payload") from exc
    except RedisError as exc:
        raise TaskQueueError(f"Failed to reserve task: {exc}") from exc
    if raw_payload is None:
        return None
    try:
        normalized_payload = _normalize_raw_payload(raw_payload)
        task = _deserialize_task(normalized_payload)
    except InvalidTaskPayloadError as exc:
        exc.processing_key = destination_key
        exc.raw_queue_payload = raw_payload
        raise
    return TaskReservation(task=task, raw_payload=normalized_payload, processing_key=destination_key)


def ack_task(client: Redis, reservation: TaskReservation, queue_key: str = TASK_QUEUE_KEY) -> bool:
    """Idempotently remove one completed delivery and release its monitor marker."""
    script = """
    local removed_processing = redis.call('LREM', KEYS[1], 1, ARGV[1])
    local removed_ready = redis.call('LREM', KEYS[2], 1, ARGV[1])
    local pending = redis.call('GET', KEYS[3])
    if pending == ARGV[2] then
        redis.call('DEL', KEYS[3])
    end
    redis.call('DEL', KEYS[4])
    if removed_processing == 1 or removed_ready == 1 or pending == ARGV[2] or not pending then
        return 1
    end
    return 0
    """
    try:
        return bool(
            client.eval(
                script,
                4,
                reservation.processing_key or processing_queue_key(queue_key),
                queue_key,
                pending_task_key(reservation.task.source_id, queue_key),
                pending_payload_key(reservation.raw_payload, queue_key),
                reservation.raw_payload,
                reservation.task.task_id,
            )
        )
    except RedisError as exc:
        raise TaskQueueError(f"Failed to acknowledge task {reservation.task.task_id}: {exc}") from exc


def requeue_task(client: Redis, reservation: TaskReservation, queue_key: str = TASK_QUEUE_KEY) -> bool:
    """Idempotently restore one exact processing payload to the ready queue."""
    script = """
    local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
    if removed == 1 then
        redis.call('LPUSH', KEYS[2], ARGV[1])
        return 1
    end
    if redis.call('LPOS', KEYS[2], ARGV[1]) then
        return 1
    end
    if redis.call('GET', KEYS[3]) == ARGV[2] then
        return 1
    end
    return 0
    """
    try:
        return bool(
            client.eval(
                script,
                3,
                reservation.processing_key or processing_queue_key(queue_key),
                queue_key,
                pending_task_key(reservation.task.source_id, queue_key),
                reservation.raw_payload,
                reservation.task.task_id,
            )
        )
    except RedisError as exc:
        raise TaskQueueError(f"Failed to requeue task {reservation.task.task_id}: {exc}") from exc


def dead_letter_task(
    client: Redis,
    raw_payload: str | bytes,
    queue_key: str = TASK_QUEUE_KEY,
    *,
    source_id: int | None = None,
    task_id: str | None = None,
    processing_key_override: str | None = None,
) -> bool:
    """Move one malformed processing payload to dead-letter and release a known marker."""
    reverse_key = pending_payload_key(raw_payload, queue_key)
    resolved_source_id, resolved_task_id = _resolve_pending_identity(
        client,
        reverse_key,
        source_id=source_id,
        task_id=task_id,
    )
    keys = [
        processing_key_override or processing_queue_key(queue_key),
        dead_letter_queue_key(queue_key),
        reverse_key,
    ]
    script = """
    local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
    if removed == 1 then
        redis.call('LPUSH', KEYS[2], ARGV[1])
        if #KEYS == 4 and redis.call('GET', KEYS[4]) == ARGV[2] then
            redis.call('DEL', KEYS[4])
        end
        redis.call('DEL', KEYS[3])
        return 1
    end
    if redis.call('LPOS', KEYS[2], ARGV[1]) then
        if #KEYS == 4 and redis.call('GET', KEYS[4]) == ARGV[2] then
            redis.call('DEL', KEYS[4])
        end
        redis.call('DEL', KEYS[3])
        return 1
    end
    return 0
    """
    args = [raw_payload]
    if resolved_source_id is not None and resolved_task_id:
        keys.append(pending_task_key(resolved_source_id, queue_key))
        args.append(resolved_task_id)
    else:
        args.append("")
    try:
        return bool(client.eval(script, len(keys), *keys, *args))
    except RedisError as exc:
        raise TaskQueueError("Failed to dead-letter invalid task payload") from exc


def recover_inflight_tasks(
    client: Redis,
    queue_key: str = TASK_QUEUE_KEY,
    *,
    processing_keys: tuple[str, ...] | None = None,
) -> int:
    """Return every unacknowledged processing payload to FIFO before workers start."""
    script = """
    local payloads = redis.call('LRANGE', KEYS[1], 0, -1)
    for index = 1, #payloads do
        redis.call('RPUSH', KEYS[2], payloads[index])
    end
    if #payloads > 0 then
        redis.call('DEL', KEYS[1])
    end
    return #payloads
    """
    try:
        resolved_processing_keys = processing_keys or _discover_processing_queue_keys(client, queue_key)
        recovered = sum(
            int(client.eval(script, 2, resolved_processing_key, queue_key))
            for resolved_processing_key in resolved_processing_keys
        )
        _restore_pending_markers(client, queue_key)
        return recovered
    except (RedisError, UnicodeError) as exc:
        raise TaskQueueError(f"Failed to recover in-flight tasks: {exc}") from exc


def _discover_processing_queue_keys(client: Redis, queue_key: str) -> tuple[str, ...]:
    legacy_key = processing_queue_key(queue_key)
    scan_iter = getattr(client, "scan_iter", None)
    if not callable(scan_iter):
        return (legacy_key,)
    discovered = {legacy_key}
    for raw_key in scan_iter(match=f"{legacy_key}:*"):
        discovered.add(_normalize_raw_payload(raw_key))
    return tuple(sorted(discovered))


def queue_length(client: Redis, queue_key: str = TASK_QUEUE_KEY) -> int:
    """Return the current number of ready tasks."""
    try:
        return client.llen(queue_key)
    except RedisError:
        return -1


def pending_tasks(
    client: Redis,
    queue_key: str = TASK_QUEUE_KEY,
    *,
    processing_keys: tuple[str, ...] | None = None,
) -> list[MonitorTask]:
    """Return a deduplicated snapshot of ready and in-flight tasks."""
    try:
        queue_keys = (queue_key, *(processing_keys or _discover_processing_queue_keys(client, queue_key)))
        pipeline_method = getattr(client, "pipeline", None)
        if callable(pipeline_method):
            pipe = pipeline_method(transaction=True)
            for key in queue_keys:
                pipe.lrange(key, 0, -1)
            raw_payloads = [raw_payload for payloads in pipe.execute() for raw_payload in payloads]
        else:
            raw_payloads = [raw_payload for key in queue_keys for raw_payload in client.lrange(key, 0, -1)]
    except (RedisError, UnicodeError) as exc:
        raise TaskQueueError("Failed to inspect pending tasks") from exc
    tasks_by_id: dict[str, MonitorTask] = {}
    for raw_payload in raw_payloads:
        try:
            task = _deserialize_task(_normalize_raw_payload(raw_payload))
        except InvalidTaskPayloadError:
            continue
        tasks_by_id.setdefault(task.task_id, task)
    return list(tasks_by_id.values())


def cancel_ready_task_for_source(
    client: Redis,
    source_id: int,
    queue_key: str = TASK_QUEUE_KEY,
) -> bool:
    """Cancel a source task only while it is still in the ready list."""
    pending_key = pending_task_key(source_id, queue_key)
    script = """
    local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
    if removed == 1 then
        if redis.call('GET', KEYS[2]) == ARGV[2] then
            redis.call('DEL', KEYS[2])
        end
        redis.call('DEL', KEYS[3])
    end
    return removed
    """
    try:
        raw_pending_id = client.get(pending_key)
        if raw_pending_id is None:
            return False
        pending_id = _normalize_raw_payload(raw_pending_id)
        for raw_payload in client.lrange(queue_key, 0, -1):
            try:
                normalized_payload = _normalize_raw_payload(raw_payload)
                task = _deserialize_task(normalized_payload)
            except InvalidTaskPayloadError:
                continue
            if task.source_id != source_id or task.task_id != pending_id:
                continue
            return bool(
                client.eval(
                    script,
                    3,
                    queue_key,
                    pending_key,
                    pending_payload_key(raw_payload, queue_key),
                    raw_payload,
                    task.task_id,
                )
            )
        return False
    except (RedisError, UnicodeError) as exc:
        raise TaskQueueError(f"Failed to cancel queued task for source {source_id}") from exc


def _restore_pending_markers(client: Redis, queue_key: str) -> None:
    for raw_payload in reversed(client.lrange(queue_key, 0, -1)):
        try:
            task = _deserialize_task(_normalize_raw_payload(raw_payload))
        except InvalidTaskPayloadError:
            continue
        payload = _normalize_raw_payload(raw_payload)
        client.set(pending_task_key(task.source_id, queue_key), task.task_id, nx=True)
        client.set(
            pending_payload_key(payload, queue_key),
            _serialize_task_identity(task.source_id, task.task_id),
        )


def _serialize_task(task: MonitorTask) -> str:
    return json.dumps(asdict(task), ensure_ascii=True, default=str, separators=(",", ":"))


def _serialize_task_identity(source_id: int, task_id: str) -> str:
    return json.dumps({"source_id": source_id, "task_id": task_id}, separators=(",", ":"))


def _resolve_pending_identity(
    client: Redis,
    reverse_key: str,
    *,
    source_id: int | None,
    task_id: str | None,
) -> tuple[int | None, str | None]:
    if source_id is not None and task_id:
        return source_id, task_id
    try:
        raw_identity = client.get(reverse_key)
    except RedisError as exc:
        raise TaskQueueError("Failed to resolve pending task identity") from exc
    if isinstance(raw_identity, bytes):
        try:
            raw_identity = raw_identity.decode("utf-8")
        except UnicodeDecodeError:
            return source_id, task_id
    if not isinstance(raw_identity, str):
        return source_id, task_id
    try:
        identity = json.loads(raw_identity)
    except json.JSONDecodeError:
        return source_id, task_id
    resolved_source_id, resolved_task_id = _task_identity(identity)
    return source_id or resolved_source_id, task_id or resolved_task_id


def _normalize_raw_payload(raw_payload: Any) -> str:
    if isinstance(raw_payload, bytes):
        try:
            return raw_payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidTaskPayloadError(
                "Failed to deserialize task: payload is not UTF-8",
                raw_payload=raw_payload.decode("utf-8", errors="replace"),
            ) from exc
    if isinstance(raw_payload, str):
        return raw_payload
    raise InvalidTaskPayloadError(
        "Failed to deserialize task: payload must be text",
        raw_payload=str(raw_payload),
    )


def _deserialize_task(raw_payload: str) -> MonitorTask:
    try:
        data = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: invalid JSON",
            raw_payload=raw_payload,
        ) from exc
    source_id, task_id = _task_identity(data)
    if not isinstance(data, dict):
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: payload must be an object",
            raw_payload=raw_payload,
        )
    unknown_fields = sorted(set(data) - MONITOR_TASK_FIELD_NAMES)
    if unknown_fields:
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: unknown fields",
            raw_payload=raw_payload,
            source_id=source_id,
            task_id=task_id,
        )
    if not isinstance(data.get("task_id"), str) or not data["task_id"]:
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: invalid task_id",
            raw_payload=raw_payload,
            source_id=source_id,
            task_id=task_id,
        )
    if not isinstance(data.get("enqueued_at"), str) or not data["enqueued_at"]:
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: invalid enqueued_at",
            raw_payload=raw_payload,
            source_id=source_id,
            task_id=task_id,
        )
    try:
        task = MonitorTask(**data)
    except (TypeError, ValueError) as exc:
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: invalid field values",
            raw_payload=raw_payload,
            source_id=source_id,
            task_id=task_id,
        ) from exc
    if not isinstance(task.source_id, int) or isinstance(task.source_id, bool) or task.source_id <= 0:
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: invalid source_id",
            raw_payload=raw_payload,
            source_id=source_id,
            task_id=task_id,
        )
    if not isinstance(task.task_id, str) or not task.task_id or len(task.task_id) > 64:
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: invalid task_id",
            raw_payload=raw_payload,
            source_id=source_id,
            task_id=task_id,
        )
    if (
        not isinstance(task.source_url, str)
        or not task.source_url
        or len(task.source_url) > 2048
        or not isinstance(task.monitor_mode, str)
        or not task.monitor_mode
        or len(task.monitor_mode) > 32
        or not isinstance(task.trigger, str)
        or not task.trigger
        or len(task.trigger) > 32
        or not isinstance(task.scheduler_config, dict)
        or not isinstance(task.enqueued_at, str)
        or len(task.enqueued_at) > 80
    ):
        raise InvalidTaskPayloadError(
            "Failed to deserialize task: invalid field values",
            raw_payload=raw_payload,
            source_id=source_id,
            task_id=task_id,
        )
    return task


def _task_identity(data: Any) -> tuple[int | None, str | None]:
    if not isinstance(data, dict):
        return None, None
    raw_source_id = data.get("source_id")
    source_id = raw_source_id if isinstance(raw_source_id, int) and not isinstance(raw_source_id, bool) else None
    raw_task_id = data.get("task_id")
    task_id = str(raw_task_id) if isinstance(raw_task_id, str) and raw_task_id else None
    return source_id, task_id
