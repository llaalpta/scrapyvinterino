from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from api_client import authenticated_test_client
from pydantic import ValidationError
from sqlalchemy import delete, select

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import AppSetting, MonitorSession, Run, RunEvent, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.scheduler import SCHEDULER_SETTING_KEY, get_scheduler_state, update_scheduler_config
from vinted_monitor.services.scheduler_liveness import (
    SCHEDULER_WORKER_HEARTBEAT_KEY,
    scheduler_worker_availability,
    touch_scheduler_worker_heartbeat,
)
from vinted_monitor.worker.scheduler import SchedulerRunner

QA_SOURCE_PREFIX = "pytest scheduler availability %"


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        scheduler_enabled=True,
        vinted_direct_catalog_enabled=True,
        **overrides,
    )


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
def preserve_scheduler_app_settings():
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


def test_scheduler_state_requires_fresh_producer_heartbeat() -> None:
    settings = _settings()
    now = datetime.now(UTC)
    with SessionLocal() as db:
        update_scheduler_config(db, {"allow_direct_without_proxy": True}, settings)

        missing = get_scheduler_state(db, settings, now=now)
        assert missing.worker_available is False
        assert missing.worker_last_seen_at is None
        assert missing.effective_enabled is False

        touch_scheduler_worker_heartbeat(db, now=now)
        db.commit()
        fresh = get_scheduler_state(db, settings, now=now)
        assert fresh.worker_available is True
        assert fresh.worker_last_seen_at == now
        assert fresh.effective_enabled is True

        heartbeat = db.get(AppSetting, SCHEDULER_WORKER_HEARTBEAT_KEY)
        assert heartbeat is not None
        heartbeat.value = {
            "last_seen_at": (now - timedelta(seconds=settings.scheduler_worker_heartbeat_timeout_seconds + 1)).isoformat()
        }
        db.commit()
        assert scheduler_worker_availability(db, settings, now=now).available is False

        for invalid_value in (
            ["invalid"],
            {"last_seen_at": "not-a-date"},
            {"last_seen_at": now.replace(tzinfo=None).isoformat()},
            {"last_seen_at": (now + timedelta(microseconds=1)).isoformat()},
        ):
            heartbeat.value = invalid_value
            db.commit()
            unavailable = scheduler_worker_availability(db, settings, now=now)
            assert unavailable.available is False
            assert unavailable.last_seen_at is None


def test_scheduler_runner_writes_heartbeat_while_deployment_gate_is_disabled() -> None:
    settings = Settings(
        _env_file=None,
        scheduler_enabled=False,
        vinted_direct_catalog_enabled=True,
    )
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)

    runner = SchedulerRunner(settings)
    assert runner.run_once(now=now) == []

    with SessionLocal() as db:
        availability = scheduler_worker_availability(db, settings, now=now)
        assert availability.available is True
        assert availability.last_seen_at == now


def test_scheduler_runner_keeps_heartbeat_fresh_during_long_idle_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        scheduler_poll_interval_seconds=60,
        scheduler_worker_heartbeat_interval_seconds=5,
        scheduler_worker_heartbeat_timeout_seconds=30,
    )
    current_time = [datetime(2026, 7, 12, 12, 0, tzinfo=UTC)]
    touched_at: list[datetime] = []

    class NullSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def commit(self):
            return None

    def sleep(delay: float) -> None:
        current_time[0] += timedelta(seconds=delay)

    monkeypatch.setattr("vinted_monitor.worker.scheduler.SessionLocal", lambda: NullSession())
    monkeypatch.setattr(
        "vinted_monitor.worker.scheduler.touch_scheduler_worker_heartbeat",
        lambda _db, *, now=None: touched_at.append(now),
    )
    runner = SchedulerRunner(settings, clock=lambda: current_time[0], sleep=sleep)

    runner._write_heartbeat_if_due(current_time[0])
    runner._sleep_until_next_poll()

    assert touched_at == [
        datetime(2026, 7, 12, 12, 0, tzinfo=UTC) + timedelta(seconds=second)
        for second in range(0, 61, 5)
    ]


def test_recurring_start_returns_503_without_producer_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    monkeypatch.setattr("vinted_monitor.api.main.settings", settings)
    monkeypatch.setattr(
        "vinted_monitor.api.main.choose_run_egress",
        lambda *_args, **_kwargs: pytest.fail("egress selection must not run without a producer heartbeat"),
    )
    with SessionLocal() as db:
        update_scheduler_config(db, {"allow_direct_without_proxy": True}, settings)
        source = SearchSource(
            name="pytest scheduler availability unavailable",
            url="https://www.vinted.es/catalog?search_text=scheduler-availability",
            normalized_query={"search_text": ["scheduler-availability"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 60, "jitter_percent": 0, "allowed_windows": []},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    response = authenticated_test_client().post(f"/api/monitors/{source_id}/start")

    assert response.status_code == 503
    assert response.json()["detail"] == "Scheduler worker is unavailable"
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        assert source.is_active is False
        assert source.monitor_started_at is None
        assert source.next_run_at is None
        assert db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id)) is None
        assert db.scalar(select(Run).where(Run.source_id == source_id)) is None


def test_scheduler_liveness_settings_require_two_heartbeat_intervals() -> None:
    with pytest.raises(ValidationError, match="must allow at least two heartbeats"):
        _settings(
            scheduler_worker_heartbeat_interval_seconds=10,
            scheduler_worker_heartbeat_timeout_seconds=19,
        )
