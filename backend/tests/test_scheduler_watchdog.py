import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import AppSetting, MonitorSession, Run, RunEvent, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.scheduler import SCHEDULER_SETTING_KEY
from vinted_monitor.services.scheduler_liveness import (
    SCHEDULER_WORKER_HEARTBEAT_KEY,
    touch_scheduler_worker_heartbeat,
)
from vinted_monitor.services.seen_cache import SeenCacheUnavailableError
from vinted_monitor.worker import healthcheck
from vinted_monitor.worker import main as worker_main
from vinted_monitor.worker import watchdog as watchdog_module
from vinted_monitor.worker.watchdog import WORKER_UNAVAILABLE_STOP_REASON, SchedulerWatchdog

QA_SOURCE_PREFIX = "pytest watchdog %"


def _settings(**overrides) -> Settings:
    values = {
        "scheduler_enabled": True,
        "vinted_direct_catalog_enabled": True,
        "scheduler_worker_heartbeat_interval_seconds": 5,
        "scheduler_worker_heartbeat_timeout_seconds": 30,
        "scheduler_watchdog_poll_interval_seconds": 5,
        "scheduler_watchdog_startup_grace_seconds": 30,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _cleanup_sources() -> None:
    with SessionLocal() as db:
        source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.name.like(QA_SOURCE_PREFIX))))
        if source_ids:
            run_ids = list(db.scalars(select(Run.id).where(Run.source_id.in_(source_ids))))
            db.execute(delete(RunEvent).where(RunEvent.source_id.in_(source_ids)))
            if run_ids:
                db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
                db.execute(delete(Run).where(Run.id.in_(run_ids)))
            db.execute(delete(MonitorSession).where(MonitorSession.source_id.in_(source_ids)))
            db.execute(delete(SearchSource).where(SearchSource.id.in_(source_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def preserve_scheduler_state():
    keys = (SCHEDULER_SETTING_KEY, SCHEDULER_WORKER_HEARTBEAT_KEY)
    original_values: dict[str, dict] = {}
    _cleanup_sources()
    with SessionLocal() as db:
        for key in keys:
            setting = db.get(AppSetting, key)
            if setting is not None:
                original_values[key] = deepcopy(setting.value)
                db.delete(setting)
        db.commit()
    yield
    _cleanup_sources()
    with SessionLocal() as db:
        for key in keys:
            setting = db.get(AppSetting, key)
            if setting is not None:
                db.delete(setting)
        db.flush()
        for key, value in original_values.items():
            db.add(AppSetting(key=key, value=value))
        db.commit()


def _create_active_source(db, *, suffix: str, mode: str = "continuous") -> SearchSource:
    source = SearchSource(
        name=f"pytest watchdog {suffix}",
        url=f"https://www.vinted.es/catalog?search_text=watchdog-{suffix}",
        normalized_query={"search_text": [f"watchdog-{suffix}"]},
        is_active=True,
        monitor_mode=mode,
        scheduler_config={"interval_seconds": 60, "jitter_percent": 0, "allowed_windows": []},
        monitor_started_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
        monitor_until=datetime(2026, 7, 12, 12, 30, tzinfo=UTC) if mode == "duration" else None,
        next_run_at=datetime(2026, 7, 12, 12, 1, tzinfo=UTC),
    )
    db.add(source)
    db.flush()
    db.add(MonitorSession(source_id=source.id, started_at=source.monitor_started_at))
    db.flush()
    return source


def test_watchdog_stops_only_active_recurring_monitors() -> None:
    settings = _settings()
    now = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
    with SessionLocal() as db:
        continuous = _create_active_source(db, suffix="continuous")
        duration = _create_active_source(db, suffix="duration", mode="duration")
        window = _create_active_source(db, suffix="window", mode="window")
        manual = _create_active_source(db, suffix="manual", mode="manual")
        db.commit()
        source_ids = {
            "continuous": continuous.id,
            "duration": duration.id,
            "window": window.id,
            "manual": manual.id,
        }

    cancelled: list[int] = []
    watchdog = SchedulerWatchdog(
        settings,
        started_at=now - timedelta(seconds=settings.scheduler_watchdog_startup_grace_seconds),
        clock=lambda: now,
    )
    watchdog._cancel_ready_task = cancelled.append

    recurring_ids = {source_ids[name] for name in ("continuous", "duration", "window")}
    assert set(watchdog.run_once(now=now)) == recurring_ids
    assert set(cancelled) == recurring_ids
    assert watchdog.run_once(now=now) == []

    with SessionLocal() as db:
        sources = list(db.scalars(select(SearchSource).where(SearchSource.id.in_(source_ids.values()))))
        by_id = {source.id: source for source in sources}
        for source_id in recurring_ids:
            source = by_id[source_id]
            assert source.is_active is False
            assert source.monitor_started_at is None
            assert source.monitor_until is None
            assert source.next_run_at is None
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            assert session is not None
            assert session.stopped_at == now
            assert session.stop_reason == WORKER_UNAVAILABLE_STOP_REASON
        assert by_id[source_ids["manual"]].is_active is True
        manual_session = db.scalar(
            select(MonitorSession).where(MonitorSession.source_id == source_ids["manual"])
        )
        assert manual_session is not None
        assert manual_session.stopped_at is None
        events = list(
            db.scalars(
                select(RunEvent)
                .where(RunEvent.phase == WORKER_UNAVAILABLE_STOP_REASON)
                .order_by(RunEvent.source_id)
            )
        )
        assert [event.source_id for event in events] == sorted(recurring_ids)


def test_watchdog_respects_startup_grace_and_fresh_heartbeat() -> None:
    settings = _settings()
    started_at = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    before_grace = started_at + timedelta(seconds=settings.scheduler_watchdog_startup_grace_seconds - 1)
    after_grace = started_at + timedelta(seconds=settings.scheduler_watchdog_startup_grace_seconds + 1)
    with SessionLocal() as db:
        source = _create_active_source(db, suffix="grace")
        db.commit()
        source_id = source.id

    cancelled: list[int] = []
    watchdog = SchedulerWatchdog(settings, started_at=started_at, clock=lambda: after_grace)
    watchdog._cancel_ready_task = cancelled.append
    assert watchdog.run_once(now=before_grace) == []

    with SessionLocal() as db:
        touch_scheduler_worker_heartbeat(db, now=after_grace)
        db.commit()
    assert watchdog.run_once(now=after_grace) == []
    assert cancelled == []
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        assert source.is_active is True


def test_watchdog_rechecks_heartbeat_after_source_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    first_check = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
    recovered_at = first_check + timedelta(seconds=1)
    with SessionLocal() as db:
        source = _create_active_source(db, suffix="recovered")
        db.commit()
        source_id = source.id

    original_availability = watchdog_module.scheduler_worker_availability
    first_check_finished = Event()
    checks = 0

    def recover_after_first_check(db, configured_settings, *, now=None):
        nonlocal checks
        checks += 1
        result = original_availability(db, configured_settings, now=now)
        if checks == 1:
            first_check_finished.set()
        return result

    watchdog = SchedulerWatchdog(
        settings,
        started_at=first_check - timedelta(seconds=settings.scheduler_watchdog_startup_grace_seconds),
        clock=lambda: recovered_at,
    )
    watchdog._cancel_ready_task = lambda _source_id: pytest.fail("recovered monitor must remain queued")
    monkeypatch.setattr(watchdog_module, "scheduler_worker_availability", recover_after_first_check)

    with SessionLocal() as lock_db:
        locked_source = lock_db.scalar(
            select(SearchSource).where(SearchSource.id == source_id).with_for_update()
        )
        assert locked_source is not None
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(watchdog.run_once, first_check)
            assert first_check_finished.wait(timeout=5)
            time.sleep(0.1)
            assert future.done() is False
            with SessionLocal() as recovery_db:
                touch_scheduler_worker_heartbeat(recovery_db, now=recovered_at)
                recovery_db.commit()
            lock_db.commit()
            assert future.result(timeout=5) == []
    assert checks == 2
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        assert source.is_active is True


def test_watchdog_keeps_database_stop_when_redis_cleanup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    now = datetime(2026, 7, 12, 12, 5, tzinfo=UTC)
    with SessionLocal() as db:
        source = _create_active_source(db, suffix="redis-failure")
        db.commit()
        source_id = source.id

    warnings: list[tuple[str, dict]] = []

    class CapturingLogger:
        def warning(self, event: str, **details) -> None:
            warnings.append((event, details))

        def critical(self, _event: str, **_details) -> None:
            return None

    watchdog = SchedulerWatchdog(
        settings,
        started_at=now - timedelta(seconds=settings.scheduler_watchdog_startup_grace_seconds),
        clock=lambda: now,
    )
    watchdog.logger = CapturingLogger()
    monkeypatch.setattr(
        watchdog_module,
        "get_seen_cache",
        lambda _settings: (_ for _ in ()).throw(SeenCacheUnavailableError("Redis unavailable")),
    )

    assert watchdog.run_once(now=now) == [source_id]
    assert [event for event, _details in warnings] == [
        "scheduler_watchdog_ready_task_cancel_failed",
        "scheduler_watchdog_stopped_monitors",
    ]
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        assert source.is_active is False
        assert db.scalar(select(RunEvent).where(RunEvent.source_id == source_id)) is not None


def test_watchdog_unexpected_error_terminates_loop() -> None:
    settings = _settings()
    critical_events: list[str] = []

    class CapturingLogger:
        def critical(self, event: str, **_details) -> None:
            critical_events.append(event)

    watchdog = SchedulerWatchdog(settings, sleep=lambda _delay: pytest.fail("must not keep polling"))
    watchdog.logger = CapturingLogger()
    watchdog.run_once = lambda: (_ for _ in ()).throw(RuntimeError("unexpected watchdog failure"))

    with pytest.raises(RuntimeError, match="unexpected watchdog failure"):
        watchdog.run_forever()
    assert critical_events == ["scheduler_watchdog_crashed"]


def test_worker_supervisor_exits_only_after_producer_heartbeat_expires() -> None:
    settings = _settings()
    started_at = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)

    assert worker_main._producer_heartbeat_expired(
        settings,
        started_at=started_at,
        now=started_at + timedelta(seconds=settings.scheduler_worker_heartbeat_timeout_seconds - 1),
    ) is False
    assert worker_main._producer_heartbeat_expired(
        settings,
        started_at=started_at,
        now=started_at + timedelta(seconds=settings.scheduler_worker_heartbeat_timeout_seconds + 1),
    ) is True

    with SessionLocal() as db:
        touch_scheduler_worker_heartbeat(
            db,
            now=started_at + timedelta(seconds=settings.scheduler_worker_heartbeat_timeout_seconds),
        )
        db.commit()
    assert worker_main._producer_heartbeat_expired(
        settings,
        started_at=started_at,
        now=started_at + timedelta(seconds=settings.scheduler_worker_heartbeat_timeout_seconds + 1),
    ) is False


def test_worker_invalid_scheduler_configuration_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(scheduler_timezone="Invalid/Timezone")
    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_logging", lambda _level: None)

    with pytest.raises(SystemExit) as exc_info:
        worker_main.main()
    assert exc_info.value.code == 2


def test_worker_producer_thread_completion_exits_process(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()

    class FakeCache:
        def require_available(self) -> None:
            return None

    class FakeTarget:
        def __init__(self, _settings, consumer_id: int | None = None) -> None:
            self.consumer_id = consumer_id

        def run_forever(self) -> None:
            return None

    class FakeFuture:
        def __init__(self, name: str) -> None:
            self.name = name

        def result(self) -> None:
            return None

    class FakePool:
        def __init__(self, **_kwargs) -> None:
            return None

        def submit(self, _wrapper, target, name: str, _logger) -> FakeFuture:
            assert callable(target)
            return FakeFuture(name)

    class ProcessExit(RuntimeError):
        def __init__(self, code: int) -> None:
            super().__init__(f"process exit {code}")
            self.code = code

    def completed_producer(futures, **_kwargs):
        producer = next(future for future in futures if future.name == "producer")
        return {producer}, set()

    monkeypatch.setattr(worker_main, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_logging", lambda _level: None)
    monkeypatch.setattr(worker_main, "validate_proxy_settings", lambda _settings: None)
    monkeypatch.setattr(worker_main, "get_seen_cache", lambda _settings: FakeCache())
    monkeypatch.setattr(worker_main, "redis_client_from_url", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(worker_main, "recover_inflight_tasks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(worker_main, "SchedulerRunner", FakeTarget)
    monkeypatch.setattr(worker_main, "TaskConsumer", FakeTarget)
    monkeypatch.setattr(worker_main, "ThreadPoolExecutor", FakePool)
    monkeypatch.setattr(worker_main, "wait", completed_producer)
    monkeypatch.setattr(worker_main, "_producer_heartbeat_expired", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(worker_main, "_exit_process", lambda code: (_ for _ in ()).throw(ProcessExit(code)))

    with pytest.raises(ProcessExit) as exc_info:
        worker_main.main()
    assert exc_info.value.code == 1


def test_worker_healthcheck_uses_producer_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    monkeypatch.setattr(healthcheck, "get_settings", lambda: settings)
    with pytest.raises(SystemExit) as exc_info:
        healthcheck.main()
    assert exc_info.value.code == 1

    with SessionLocal() as db:
        touch_scheduler_worker_heartbeat(db)
        db.commit()
    healthcheck.main()


def test_watchdog_settings_reject_unsafe_timing() -> None:
    with pytest.raises(ValidationError, match="must allow the first heartbeat"):
        _settings(scheduler_watchdog_startup_grace_seconds=4)
    with pytest.raises(ValidationError, match="cannot exceed the heartbeat timeout"):
        _settings(scheduler_watchdog_poll_interval_seconds=31)
