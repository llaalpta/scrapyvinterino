from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, insert, select
from sqlalchemy.engine import Connection

from vinted_monitor.db.models import (
    RunEvent,
    RunEventOutbox,
    RunEventPublication,
    SearchSource,
)
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services import run_event_stream
from vinted_monitor.services.run_event_stream import (
    PUBLICATION_BATCH_SIZE,
    latest_monitor_event_id,
    load_monitor_events_after,
    publish_committed_monitor_events,
)
from vinted_monitor.services.run_events import record_run_event

QA_SOURCE_PREFIX = "pytest 14.7 outbox "
QA_PHASE_PREFIX = "pytest_14_7_outbox_"


def _create_source() -> int:
    token = uuid4().hex
    with SessionLocal() as db:
        source = SearchSource(
            name=f"{QA_SOURCE_PREFIX}{token}",
            url=f"https://example.invalid/qa-14-7/{token}",
            normalized_query={"qa": [token]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
            filter_definition={"blacklist_terms": []},
        )
        db.add(source)
        db.commit()
        return source.id


def _cleanup_source(source_id: int) -> None:
    with SessionLocal() as db:
        event_ids = select(RunEvent.id).where(RunEvent.source_id == source_id)
        db.execute(delete(RunEventPublication).where(RunEventPublication.event_id.in_(event_ids)))
        db.execute(delete(RunEventOutbox).where(RunEventOutbox.event_id.in_(event_ids)))
        db.execute(delete(RunEvent).where(RunEvent.source_id == source_id))
        db.execute(
            delete(SearchSource).where(
                SearchSource.id == source_id,
                SearchSource.name.like(f"{QA_SOURCE_PREFIX}%"),
            )
        )
        db.commit()


def _record(source_id: int | None, suffix: str) -> int:
    with SessionLocal() as db:
        recorded = record_run_event(
            db,
            source_id=source_id,
            phase=f"{QA_PHASE_PREFIX}{suffix}",
            details={"qa": "transactional-outbox"},
        )
        db.commit()
        return recorded.id


def _bulk_record(source_id: int, count: int, suffix: str) -> list[int]:
    created_at = datetime.now(UTC)
    with SessionLocal() as db:
        rows = db.execute(
            insert(RunEvent)
            .values(
                [
                    {
                        "source_id": source_id,
                        "phase": f"{QA_PHASE_PREFIX}{suffix}",
                        "level": "info",
                        "details": {"qa_index": index},
                        "created_at": created_at,
                    }
                    for index in range(count)
                ]
            )
            .returning(RunEvent.id)
        )
        event_ids = [int(row.id) for row in rows]
        db.execute(
            insert(RunEventOutbox),
            [{"event_id": event_id, "created_at": created_at} for event_id in event_ids],
        )
        db.commit()
        return event_ids


def _drain_committed_outbox() -> None:
    while publish_committed_monitor_events() == PUBLICATION_BATCH_SIZE:
        pass


def test_recorded_monitor_event_commits_event_and_outbox_then_resumes_once() -> None:
    _drain_committed_outbox()
    start_cursor = latest_monitor_event_id()
    source_id = _create_source()
    try:
        event_id = _record(source_id, "commit")

        with SessionLocal() as db:
            assert db.get(RunEvent, event_id) is not None
            assert db.get(RunEventOutbox, event_id) is not None
            assert db.scalar(
                select(func.count()).select_from(RunEventPublication).where(RunEventPublication.event_id == event_id)
            ) == 0

        delivered = [
            published
            for published in load_monitor_events_after(start_cursor)
            if published.event.id == event_id
        ]
        assert len(delivered) == 1
        assert [
            published
            for published in load_monitor_events_after(delivered[0].cursor)
            if published.event.id == event_id
        ] == []

        with SessionLocal() as db:
            assert db.get(RunEventOutbox, event_id) is None
            assert db.scalar(
                select(func.count()).select_from(RunEventPublication).where(RunEventPublication.event_id == event_id)
            ) == 1
    finally:
        _cleanup_source(source_id)


def test_recorded_monitor_event_rollback_leaves_neither_event_nor_outbox() -> None:
    source_id = _create_source()
    event_id: int | None = None
    try:
        with SessionLocal() as db:
            recorded = record_run_event(
                db,
                source_id=source_id,
                phase=f"{QA_PHASE_PREFIX}producer_rollback",
            )
            event_id = recorded.id
            db.rollback()

        with SessionLocal() as db:
            assert db.get(RunEvent, event_id) is None
            assert db.get(RunEventOutbox, event_id) is None
    finally:
        _cleanup_source(source_id)


def test_event_without_source_does_not_enter_monitor_outbox() -> None:
    event_id = _record(None, "without_source")
    try:
        with SessionLocal() as db:
            assert db.get(RunEvent, event_id) is not None
            assert db.get(RunEventOutbox, event_id) is None
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(RunEvent).where(
                    RunEvent.id == event_id,
                    RunEvent.source_id.is_(None),
                    RunEvent.phase == f"{QA_PHASE_PREFIX}without_source",
                )
            )
            db.commit()


def test_publication_delivers_transactions_that_commit_in_inverted_event_id_order() -> None:
    _drain_committed_outbox()
    start_cursor = latest_monitor_event_id()
    source_id = _create_source()
    first_session = SessionLocal()
    second_session = SessionLocal()
    try:
        first_event = record_run_event(
            first_session,
            source_id=source_id,
            phase=f"{QA_PHASE_PREFIX}commit_first",
        )
        second_event = record_run_event(
            second_session,
            source_id=source_id,
            phase=f"{QA_PHASE_PREFIX}commit_second",
        )
        second_session.commit()

        first_batch = [
            published
            for published in load_monitor_events_after(start_cursor)
            if published.event.source_id == source_id
        ]
        assert [published.event.id for published in first_batch] == [second_event.id]

        first_session.commit()
        second_batch = [
            published
            for published in load_monitor_events_after(first_batch[-1].cursor)
            if published.event.source_id == source_id
        ]
        assert [published.event.id for published in second_batch] == [first_event.id]
        assert second_batch[0].cursor > first_batch[0].cursor
    finally:
        first_session.rollback()
        second_session.rollback()
        first_session.close()
        second_session.close()
        _cleanup_source(source_id)


def test_two_concurrent_publishers_publish_one_event_without_duplicates() -> None:
    _drain_committed_outbox()
    source_id = _create_source()
    try:
        event_id = _record(source_id, "concurrent_publishers")
        barrier = Barrier(2)

        def publish() -> int:
            barrier.wait()
            return publish_committed_monitor_events()

        with ThreadPoolExecutor(max_workers=2) as executor:
            published_counts = list(executor.map(lambda _index: publish(), range(2)))

        assert sum(published_counts) == 1
        with SessionLocal() as db:
            assert db.scalar(
                select(func.count()).select_from(RunEventPublication).where(RunEventPublication.event_id == event_id)
            ) == 1
            assert db.get(RunEventOutbox, event_id) is None
    finally:
        _cleanup_source(source_id)


def test_publication_drains_1001_events_in_two_bounded_batches() -> None:
    _drain_committed_outbox()
    source_id = _create_source()
    try:
        event_ids = _bulk_record(source_id, PUBLICATION_BATCH_SIZE + 1, "batch_1001")
        high_water = max(event_ids)

        assert publish_committed_monitor_events(max_event_id=high_water) == PUBLICATION_BATCH_SIZE
        assert publish_committed_monitor_events(max_event_id=high_water) == 1
        assert publish_committed_monitor_events(max_event_id=high_water) == 0

        with SessionLocal() as db:
            assert db.scalar(
                select(func.count())
                .select_from(RunEventPublication)
                .where(RunEventPublication.event_id.in_(event_ids))
            ) == PUBLICATION_BATCH_SIZE + 1
            assert db.scalar(
                select(func.count()).select_from(RunEventOutbox).where(RunEventOutbox.event_id.in_(event_ids))
            ) == 0
    finally:
        _cleanup_source(source_id)


def test_publication_commit_failure_rolls_back_publication_and_outbox_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drain_committed_outbox()
    source_id = _create_source()
    try:
        event_id = _record(source_id, "publisher_rollback")
        failing_session = SessionLocal()

        def fail_commit(_session: object) -> None:
            raise RuntimeError("pytest forced publication commit failure")

        event.listen(failing_session, "before_commit", fail_commit, once=True)
        monkeypatch.setattr(run_event_stream, "SessionLocal", lambda: failing_session)
        with pytest.raises(RuntimeError, match="forced publication commit failure"):
            run_event_stream.publish_committed_monitor_events()

        with SessionLocal() as db:
            assert db.get(RunEventOutbox, event_id) is not None
            assert db.scalar(
                select(func.count()).select_from(RunEventPublication).where(RunEventPublication.event_id == event_id)
            ) == 0

        monkeypatch.setattr(run_event_stream, "SessionLocal", SessionLocal)
        assert publish_committed_monitor_events() == 1
    finally:
        _cleanup_source(source_id)


def test_existing_publication_with_stale_outbox_is_reconciled_without_duplicate() -> None:
    _drain_committed_outbox()
    source_id = _create_source()
    try:
        event_id = _record(source_id, "stale_outbox")
        with SessionLocal() as db:
            db.add(RunEventPublication(event_id=event_id))
            db.commit()

        assert publish_committed_monitor_events() == 1
        with SessionLocal() as db:
            assert db.get(RunEventOutbox, event_id) is None
            assert db.scalar(
                select(func.count()).select_from(RunEventPublication).where(RunEventPublication.event_id == event_id)
            ) == 1
    finally:
        _cleanup_source(source_id)


def test_latest_cursor_snapshot_excludes_lower_id_committed_during_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drain_committed_outbox()
    source_id = _create_source()
    lower_id_session = SessionLocal()
    try:
        lower_event = record_run_event(
            lower_id_session,
            source_id=source_id,
            phase=f"{QA_PHASE_PREFIX}lower_id_late_commit",
        )
        initial_event_ids = _bulk_record(source_id, PUBLICATION_BATCH_SIZE, "snapshot_initial")
        assert lower_event.id < min(initial_event_ids)

        real_publish_batch = run_event_stream._publish_pending_batch
        first_batch_published = Event()
        allow_snapshot_to_finish = Event()

        def publish_and_pause(db: object, *, max_event_id: int | None = None) -> int:
            published = real_publish_batch(db, max_event_id=max_event_id)
            if published == PUBLICATION_BATCH_SIZE and not first_batch_published.is_set():
                first_batch_published.set()
                assert allow_snapshot_to_finish.wait(timeout=5)
            return published

        monkeypatch.setattr(run_event_stream, "_publish_pending_batch", publish_and_pause)
        with ThreadPoolExecutor(max_workers=1) as executor:
            cursor_future = executor.submit(latest_monitor_event_id)
            assert first_batch_published.wait(timeout=5)
            lower_id_session.commit()
            allow_snapshot_to_finish.set()
            cursor = cursor_future.result(timeout=5)

        with SessionLocal() as db:
            assert db.get(RunEventOutbox, lower_event.id) is not None
            assert db.scalar(
                select(func.count())
                .select_from(RunEventPublication)
                .where(RunEventPublication.event_id.in_(initial_event_ids))
            ) == PUBLICATION_BATCH_SIZE
            assert db.scalar(
                select(func.count())
                .select_from(RunEventPublication)
                .where(RunEventPublication.event_id == lower_event.id)
            ) == 0

        delivered = [
            published
            for published in load_monitor_events_after(cursor)
            if published.event.id == lower_event.id
        ]
        assert len(delivered) == 1
        assert delivered[0].cursor > cursor
    finally:
        lower_id_session.rollback()
        lower_id_session.close()
        _cleanup_source(source_id)


def test_latest_cursor_fence_makes_concurrent_publisher_yield_until_cursor_is_fixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drain_committed_outbox()
    source_id = _create_source()
    try:
        initial_event_id = _record(source_id, "snapshot_lock_initial")
        real_publish_batch = run_event_stream._publish_pending_batch
        initial_batch_published = Event()
        allow_cursor_to_finish = Event()

        def publish_and_pause(db: object, *, max_event_id: int | None = None) -> int:
            published = real_publish_batch(db, max_event_id=max_event_id)
            if published == 1 and not initial_batch_published.is_set():
                initial_batch_published.set()
                assert allow_cursor_to_finish.wait(timeout=5)
            return published

        monkeypatch.setattr(run_event_stream, "_publish_pending_batch", publish_and_pause)
        publisher_started = Event()

        def publish_late_event() -> int:
            publisher_started.set()
            return publish_committed_monitor_events()

        with ThreadPoolExecutor(max_workers=2) as executor:
            cursor_future = executor.submit(latest_monitor_event_id)
            assert initial_batch_published.wait(timeout=5)
            late_event_id = _record(source_id, "snapshot_lock_late")
            publisher_future = executor.submit(publish_late_event)
            assert publisher_started.wait(timeout=5)
            assert publisher_future.result(timeout=5) == 0
            allow_cursor_to_finish.set()
            cursor = cursor_future.result(timeout=5)

        assert publish_committed_monitor_events() == 1

        with SessionLocal() as db:
            initial_position = db.scalar(
                select(RunEventPublication.position).where(RunEventPublication.event_id == initial_event_id)
            )
            late_position = db.scalar(
                select(RunEventPublication.position).where(RunEventPublication.event_id == late_event_id)
            )
            assert initial_position == cursor
            assert late_position is not None
            assert late_position > cursor
    finally:
        _cleanup_source(source_id)


def test_latest_cursor_commit_failure_releases_session_lock_for_next_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drain_committed_outbox()
    source_id = _create_source()
    try:
        event_id = _record(source_id, "snapshot_commit_failure")
        real_commit = Connection.commit
        commit_calls = 0

        def fail_first_commit(connection: Connection) -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("pytest forced fence acquisition commit failure")
            real_commit(connection)

        monkeypatch.setattr(Connection, "commit", fail_first_commit)
        with pytest.raises(RuntimeError, match="fence acquisition commit failure"):
            latest_monitor_event_id()
        monkeypatch.setattr(Connection, "commit", real_commit)

        assert publish_committed_monitor_events() == 1
        with SessionLocal() as db:
            assert db.get(RunEventOutbox, event_id) is None
            assert db.scalar(
                select(func.count())
                .select_from(RunEventPublication)
                .where(RunEventPublication.event_id == event_id)
            ) == 1
    finally:
        _cleanup_source(source_id)
