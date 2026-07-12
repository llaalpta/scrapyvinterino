import json
from datetime import UTC, datetime

import pytest

from vinted_monitor.api.main import stream_monitor_events
from vinted_monitor.db.models import RunEvent
from vinted_monitor.services.run_event_stream import (
    EVENT_BATCH_SIZE,
    monitor_event_stream,
    resolve_monitor_event_cursor,
)


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


def make_event(event_id: int, *, details: dict | None = None) -> RunEvent:
    return RunEvent(
        id=event_id,
        source_id=7,
        run_id=11,
        phase="run_succeeded",
        level="info",
        created_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
        details=details or {},
    )


def event_data(message: str) -> dict:
    data_line = next(line for line in message.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


def test_cursor_query_takes_precedence_then_header_then_tail() -> None:
    assert resolve_monitor_event_cursor(12, 11, latest_cursor=lambda: 99) == 12
    assert resolve_monitor_event_cursor(None, 11, latest_cursor=lambda: 99) == 11
    assert resolve_monitor_event_cursor(None, None, latest_cursor=lambda: 99) == 99


@pytest.mark.asyncio
async def test_endpoint_tail_mode_announces_cursor_and_stream_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vinted_monitor.services.run_event_stream.latest_monitor_event_id", lambda: 42)
    response = await stream_monitor_events(ConnectedRequest(), None, None)

    first_message = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert event_data(first_message)["last_event_id"] == 42
    assert "retry: 3000" in first_message
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_endpoint_query_cursor_precedes_last_event_id_header() -> None:
    response = await stream_monitor_events(ConnectedRequest(), 9, 8)

    first_message = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert event_data(first_message)["last_event_id"] == 9


@pytest.mark.asyncio
async def test_stream_resumes_after_cursor_without_duplicates() -> None:
    events = [make_event(event_id) for event_id in range(98, 103)]

    def load_events(cursor: int) -> list[RunEvent]:
        return [event for event in events if event.id > cursor][:EVENT_BATCH_SIZE]

    stream = monitor_event_stream(100, is_disconnected=ConnectedRequest().is_disconnected, load_events=load_events)
    messages = [await anext(stream) for _ in range(3)]
    await stream.aclose()

    assert [event_data(message).get("id") for message in messages[1:]] == [101, 102]


@pytest.mark.asyncio
async def test_stream_drains_backlog_larger_than_batch_without_sleeping() -> None:
    events = [make_event(event_id) for event_id in range(1, 206)]
    cursors: list[int] = []
    sleeps: list[float] = []

    def load_events(cursor: int) -> list[RunEvent]:
        cursors.append(cursor)
        return [event for event in events if event.id > cursor][:EVENT_BATCH_SIZE]

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    stream = monitor_event_stream(
        0,
        is_disconnected=ConnectedRequest().is_disconnected,
        load_events=load_events,
        sleep=sleep,
    )
    messages = [await anext(stream) for _ in range(206)]
    await stream.aclose()

    assert cursors == [0, 100, 200]
    assert sleeps == []
    assert event_data(messages[-1])["id"] == 205


@pytest.mark.asyncio
async def test_stream_emits_idle_heartbeat() -> None:
    stream = monitor_event_stream(
        0,
        is_disconnected=ConnectedRequest().is_disconnected,
        load_events=lambda _cursor: [],
        heartbeat_interval_seconds=0,
    )

    assert "event: stream_ready" in await anext(stream)
    assert await anext(stream) == ": heartbeat\n\n"
    await stream.aclose()


@pytest.mark.asyncio
async def test_stream_stops_after_disconnect() -> None:
    async def disconnected() -> bool:
        return True

    stream = monitor_event_stream(0, is_disconnected=disconnected, load_events=lambda _cursor: [])

    assert "event: stream_ready" in await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_stream_redacts_event_details() -> None:
    secret = "raw-cookie-secret"
    stream = monitor_event_stream(
        0,
        is_disconnected=ConnectedRequest().is_disconnected,
        load_events=lambda cursor: [make_event(1, details={"cookie": secret})] if cursor == 0 else [],
    )

    await anext(stream)
    event_message = await anext(stream)
    await stream.aclose()

    assert secret not in event_message
    assert event_data(event_message)["details"]["cookie"] == "<redacted>"
