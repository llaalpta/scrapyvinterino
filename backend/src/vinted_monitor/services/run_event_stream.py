from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from vinted_monitor.db.models import RunEvent, RunEventPublication
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.run_events import redact_persisted_run_event_details

EVENT_BATCH_SIZE = 100
HEARTBEAT_INTERVAL_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 2.0
RECONNECT_DELAY_MILLISECONDS = 3000
RUN_EVENT_PUBLICATION_LOCK_ID = 814_208_008


@dataclass(frozen=True)
class PublishedRunEvent:
    cursor: int
    event: RunEvent


def resolve_monitor_event_cursor(
    query_cursor: int | None,
    header_cursor: int | None,
    *,
    latest_cursor: Callable[[], int] | None = None,
) -> int:
    if query_cursor is not None:
        return query_cursor
    if header_cursor is not None:
        return header_cursor
    return (latest_cursor or latest_monitor_event_id)()


def latest_monitor_event_id() -> int:
    publish_committed_monitor_events()
    with SessionLocal() as db:
        return int(db.scalar(select(func.coalesce(func.max(RunEventPublication.position), 0))) or 0)


def publish_committed_monitor_events() -> None:
    with SessionLocal() as db:
        db.execute(select(func.pg_advisory_xact_lock(RUN_EVENT_PUBLICATION_LOCK_ID)))
        candidate_ids = (
            select(RunEvent.id)
            .outerjoin(RunEventPublication, RunEventPublication.event_id == RunEvent.id)
            .where(
                RunEvent.source_id.is_not(None),
                RunEventPublication.event_id.is_(None),
            )
            .order_by(RunEvent.id.asc())
        )
        statement = (
            insert(RunEventPublication)
            .from_select(["event_id"], candidate_ids)
            .on_conflict_do_nothing(index_elements=[RunEventPublication.event_id])
        )
        db.execute(statement)
        db.commit()


def load_monitor_events_after(cursor: int) -> list[PublishedRunEvent]:
    publish_committed_monitor_events()
    with SessionLocal() as db:
        rows = db.execute(
            select(RunEventPublication.position, RunEvent)
                .join(RunEvent, RunEvent.id == RunEventPublication.event_id)
                .where(RunEventPublication.position > cursor)
                .order_by(RunEventPublication.position.asc())
                .limit(EVENT_BATCH_SIZE)
        )
        return [PublishedRunEvent(cursor=int(position), event=event) for position, event in rows]


async def monitor_event_stream(
    initial_cursor: int,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    load_events: Callable[[int], list[PublishedRunEvent]] = load_monitor_events_after,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    current_id = initial_cursor
    last_heartbeat_at = monotonic()
    yield _stream_ready_message(current_id)

    while not await is_disconnected():
        events = await asyncio.to_thread(load_events, current_id)
        if events:
            for published in events:
                current_id = published.cursor
                yield _monitor_event_message(published)
            # Query again immediately after every non-empty batch. This drains a
            # backlog larger than EVENT_BATCH_SIZE without adding poll latency.
            continue

        now = monotonic()
        heartbeat_due_in = heartbeat_interval_seconds - (now - last_heartbeat_at)
        if heartbeat_due_in <= 0:
            yield ": heartbeat\n\n"
            last_heartbeat_at = now
            heartbeat_due_in = heartbeat_interval_seconds
        await sleep(max(min(poll_interval_seconds, heartbeat_due_in), 0))


def _stream_ready_message(cursor: int) -> str:
    payload = json.dumps({"last_event_id": cursor})
    return f"id: {cursor}\nevent: stream_ready\nretry: {RECONNECT_DELAY_MILLISECONDS}\ndata: {payload}\n\n"


def _monitor_event_message(published: PublishedRunEvent) -> str:
    event = published.event
    payload = {
        "id": event.id,
        "source_id": event.source_id,
        "run_id": event.run_id,
        "phase": event.phase,
        "level": event.level,
        "created_at": event.created_at.isoformat(),
        "method": event.method,
        "url": event.url,
        "status_code": event.status_code,
        "duration_ms": event.duration_ms,
        "proxy_profile_id": event.proxy_profile_id,
        "egress_ip": event.egress_ip,
        "user_agent": event.user_agent,
        "auth_mode": event.auth_mode,
        "message": event.message,
        "details": redact_persisted_run_event_details(event.details),
    }
    return f"id: {published.cursor}\nevent: monitor_event\ndata: {json.dumps(payload)}\n\n"
