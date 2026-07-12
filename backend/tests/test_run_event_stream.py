import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from vinted_monitor.api.main import stream_monitor_events
from vinted_monitor.core.redaction import safe_secret_marker
from vinted_monitor.db.models import RunEvent, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.run_event_stream import (
    EVENT_BATCH_SIZE,
    PublishedRunEvent,
    latest_monitor_event_id,
    load_monitor_events_after,
    monitor_event_stream,
    resolve_monitor_event_cursor,
)
from vinted_monitor.services.run_events import redact_persisted_run_event_details, redact_run_event_details


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


def make_published(event_id: int, *, cursor: int | None = None, details: dict | None = None) -> PublishedRunEvent:
    return PublishedRunEvent(cursor=cursor or event_id, event=make_event(event_id, details=details))


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
    assert "id: 42" in first_message
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
    events = [make_published(event_id) for event_id in range(98, 103)]

    def load_events(cursor: int) -> list[PublishedRunEvent]:
        return [published for published in events if published.cursor > cursor][:EVENT_BATCH_SIZE]

    stream = monitor_event_stream(100, is_disconnected=ConnectedRequest().is_disconnected, load_events=load_events)
    messages = [await anext(stream) for _ in range(3)]
    await stream.aclose()

    assert [event_data(message).get("id") for message in messages[1:]] == [101, 102]


@pytest.mark.asyncio
async def test_stream_drains_backlog_larger_than_batch_without_sleeping() -> None:
    events = [make_published(event_id) for event_id in range(1, 206)]
    cursors: list[int] = []
    sleeps: list[float] = []

    def load_events(cursor: int) -> list[PublishedRunEvent]:
        cursors.append(cursor)
        return [published for published in events if published.cursor > cursor][:EVENT_BATCH_SIZE]

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
async def test_stream_emits_default_heartbeat_after_fifteen_idle_seconds() -> None:
    clock = [0.0]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    stream = monitor_event_stream(
        0,
        is_disconnected=ConnectedRequest().is_disconnected,
        load_events=lambda _cursor: [],
        sleep=sleep,
        monotonic=lambda: clock[0],
    )

    await anext(stream)
    assert await anext(stream) == ": heartbeat\n\n"
    assert sum(sleeps) == 15
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
async def test_stream_detects_disconnect_after_an_idle_poll() -> None:
    connected = True

    async def is_disconnected() -> bool:
        return not connected

    async def sleep(_delay: float) -> None:
        nonlocal connected
        connected = False

    stream = monitor_event_stream(0, is_disconnected=is_disconnected, load_events=lambda _cursor: [], sleep=sleep)

    await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_stream_redacts_event_details() -> None:
    secret = "raw-cookie-secret"
    stream = monitor_event_stream(
        0,
        is_disconnected=ConnectedRequest().is_disconnected,
        load_events=lambda cursor: [make_published(1, details={"cookie": secret})] if cursor == 0 else [],
    )

    await anext(stream)
    event_message = await anext(stream)
    await stream.aclose()

    assert secret not in event_message
    assert event_data(event_message)["details"]["cookie"] == "<redacted>"


def test_persisted_safe_markers_survive_json_roundtrip_without_accepting_pre_persistence_forgery() -> None:
    marker = safe_secret_marker("http_session", "audit-safe-marker-value", kind="session")
    persisted = json.loads(json.dumps({"http_session": marker, "session_markers": [marker]}))

    restored = redact_persisted_run_event_details(persisted)

    assert restored["http_session"]["masked"] == marker["masked"]
    assert restored["session_markers"][0]["fingerprint"] == marker["fingerprint"]
    forged = {"http_session": dict(marker)}
    assert redact_run_event_details(forged)["http_session"] == "<redacted>"


def test_publication_cursor_delivers_transactions_that_commit_out_of_event_id_order() -> None:
    start_cursor = latest_monitor_event_id()
    source_id: int | None = None
    first_session = SessionLocal()
    second_session = SessionLocal()
    try:
        with SessionLocal() as db:
            source = SearchSource(
                name="pytest inverted event commit",
                url="https://www.vinted.es/catalog?search_text=inverted-event-commit",
                normalized_query={"search_text": ["inverted-event-commit"]},
                is_active=False,
                monitor_mode="manual",
                scheduler_config={},
                filter_definition={"blacklist_terms": []},
            )
            db.add(source)
            db.commit()
            source_id = source.id

        first_event = RunEvent(
            source_id=source_id,
            phase="pytest_commit_first",
            level="info",
            created_at=datetime.now(UTC),
            details={},
        )
        first_session.add(first_event)
        first_session.flush()

        second_event = RunEvent(
            source_id=source_id,
            phase="pytest_commit_second",
            level="info",
            created_at=datetime.now(UTC),
            details={},
        )
        second_session.add(second_event)
        second_session.flush()
        second_session.commit()

        first_batch = [published for published in load_monitor_events_after(start_cursor) if published.event.source_id == source_id]
        assert [published.event.id for published in first_batch] == [second_event.id]

        first_session.commit()
        second_batch = [
            published for published in load_monitor_events_after(first_batch[-1].cursor) if published.event.source_id == source_id
        ]
        assert [published.event.id for published in second_batch] == [first_event.id]
        assert second_batch[0].cursor > first_batch[0].cursor
    finally:
        first_session.rollback()
        second_session.rollback()
        first_session.close()
        second_session.close()
        if source_id is not None:
            with SessionLocal() as db:
                db.execute(delete(RunEvent).where(RunEvent.source_id == source_id))
                source = db.get(SearchSource, source_id)
                if source is not None:
                    db.delete(source)
                db.commit()
