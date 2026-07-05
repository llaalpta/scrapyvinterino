import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import AppSetting, MonitorSession, ProxyProfile, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.scheduler import (
    SCHEDULER_SETTING_KEY,
    SchedulerConfigError,
    SourceSchedulerConfig,
    get_scheduler_state,
    is_within_allowed_windows,
    list_schedulable_sources,
    next_run_after,
    normalize_scheduler_config,
    update_scheduler_enabled,
    validate_proxy_settings,
)
from vinted_monitor.worker.scheduler import SchedulerRunner


class FakeRedis:
    def __init__(self) -> None:
        self.values: list[str] = []

    def lpush(self, _key: str, payload: str) -> None:
        self.values.insert(0, payload)


@pytest.fixture(autouse=True)
def cleanup_scheduler_setting():
    with SessionLocal() as db:
        setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
        if setting is not None:
            db.delete(setting)
            db.commit()
    yield
    with SessionLocal() as db:
        setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
        if setting is not None:
            db.delete(setting)
            db.commit()


def test_scheduler_state_combines_ui_and_runtime_gate() -> None:
    settings = Settings(scheduler_enabled=False)

    with SessionLocal() as db:
        state = update_scheduler_enabled(db, True, settings)

        assert state.enabled is True
        assert state.runtime_enabled is False
        assert state.effective_enabled is False

    with SessionLocal() as db:
        state = get_scheduler_state(db, Settings(scheduler_enabled=True))

        assert state.enabled is True
        assert state.effective_enabled is True


def test_normalize_scheduler_config_applies_defaults() -> None:
    assert normalize_scheduler_config({}) == {
        "interval_seconds": 300,
        "jitter_percent": 20,
        "allowed_windows": [],
    }


@pytest.mark.parametrize(
    "config, message",
    [
        ({"interval_seconds": 59}, "interval_seconds must be between 60 and 3600"),
        ({"interval_seconds": 3601}, "interval_seconds must be between 60 and 3600"),
        ({"jitter_percent": 51}, "jitter_percent must be between 0 and 50"),
        ({"allowed_windows": "09:00-12:00"}, "allowed_windows must be a list"),
        ({"allowed_windows": ["bad"]}, "allowed_windows entries must use HH:MM-HH:MM"),
        ({"allowed_windows": ["09:00-09:00"]}, "allowed_windows start and end cannot be equal"),
        ({"pause_windows": ["10:00-11:00"]}, "unsupported scheduler_config fields: pause_windows"),
    ],
)
def test_normalize_scheduler_config_rejects_invalid_values(config: dict, message: str) -> None:
    with pytest.raises(SchedulerConfigError, match=message):
        normalize_scheduler_config(config)


def test_allowed_windows_support_cross_midnight_ranges() -> None:
    assert is_within_allowed_windows(datetime(2026, 7, 3, 23, 30, tzinfo=UTC), ("22:00-02:00",))
    assert is_within_allowed_windows(datetime(2026, 7, 4, 1, 30, tzinfo=UTC), ("22:00-02:00",))
    assert not is_within_allowed_windows(datetime(2026, 7, 4, 12, 0, tzinfo=UTC), ("22:00-02:00",))


def test_next_run_after_moves_to_next_allowed_window() -> None:
    next_run = next_run_after(
        datetime(2026, 7, 3, 8, 0, tzinfo=UTC),
        SourceSchedulerConfig(interval_seconds=300, jitter_percent=0, allowed_windows=("10:00-12:00",)),
    )

    assert next_run == datetime(2026, 7, 3, 10, 0, tzinfo=UTC)


def test_next_run_after_uses_configured_local_timezone_for_allowed_windows() -> None:
    next_run = next_run_after(
        datetime(2026, 7, 3, 6, 0, tzinfo=UTC),
        SourceSchedulerConfig(interval_seconds=300, jitter_percent=0, allowed_windows=("10:00-12:00",)),
        timezone=ZoneInfo("Europe/Madrid"),
    )

    assert next_run == datetime(2026, 7, 3, 8, 0, tzinfo=UTC)


def test_validate_proxy_settings_rejects_invalid_timezone() -> None:
    with pytest.raises(SchedulerConfigError, match="Invalid scheduler timezone"):
        validate_proxy_settings(Settings(scheduler_timezone="Not/AZone"))


def test_scheduler_runner_does_not_enqueue_source_outside_allowed_window(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setattr("vinted_monitor.worker.scheduler.get_seen_cache", lambda: type("Cache", (), {"client": fake_redis})())

    with SessionLocal() as db:
        previously_active_source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.is_active.is_(True))))
        for active_source_id in previously_active_source_ids:
            active_source = db.get(SearchSource, active_source_id)
            if active_source is not None:
                active_source.is_active = False

        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True))
        source = SearchSource(
            name="pytest scheduler window source",
            url="https://www.vinted.es/catalog?search_text=",
            normalized_query={"search_text": [""]},
            is_active=True,
            monitor_mode="window",
            scheduler_config={
                "interval_seconds": 300,
                "jitter_percent": 0,
                "allowed_windows": ["10:00-11:00"],
            },
        )
        db.add(source)
        db.commit()
        source_id = source.id

    try:
        runner = SchedulerRunner(Settings(scheduler_enabled=True))
        submitted_ids = runner.run_once(now=datetime(2026, 7, 3, 6, 0, tzinfo=UTC))

        assert submitted_ids == []
        assert fake_redis.values == []
        assert runner.next_due_by_source_id[source_id] == datetime(2026, 7, 3, 8, 0, tzinfo=UTC)
    finally:
        with SessionLocal() as db:
            for active_source_id in previously_active_source_ids:
                active_source = db.get(SearchSource, active_source_id)
                if active_source is not None:
                    active_source.is_active = True
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
            setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
            if setting is not None:
                db.delete(setting)
            db.commit()


def test_scheduler_runner_enqueues_due_monitor_task(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setattr("vinted_monitor.worker.scheduler.get_seen_cache", lambda: type("Cache", (), {"client": fake_redis})())
    now = datetime(2026, 7, 3, 8, 0, tzinfo=UTC)

    with SessionLocal() as db:
        previously_active_source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.is_active.is_(True))))
        for active_source_id in previously_active_source_ids:
            active_source = db.get(SearchSource, active_source_id)
            if active_source is not None:
                active_source.is_active = False

        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True))
        source = SearchSource(
            name="pytest due producer source",
            url="https://www.vinted.es/catalog?search_text=nike",
            normalized_query={"search_text": ["nike"]},
            is_active=True,
            monitor_mode="window",
            scheduler_config={
                "interval_seconds": 300,
                "jitter_percent": 0,
                "allowed_windows": [],
            },
            next_run_at=now,
        )
        db.add(source)
        db.commit()
        source_id = source.id

    try:
        runner = SchedulerRunner(Settings(scheduler_enabled=True))
        submitted_ids = runner.run_once(now=now)

        assert submitted_ids == [source_id]
        assert len(fake_redis.values) == 1
        payload = json.loads(fake_redis.values[0])
        assert payload["source_id"] == source_id
        assert payload["source_url"] == "https://www.vinted.es/catalog?search_text=nike"
        assert payload["trigger"] == "scheduler"
        assert payload["proxy_profile_id"] is None
        assert "proxy_url_template" not in payload
        with SessionLocal() as db:
            updated = db.get(SearchSource, source_id)
            assert updated is not None
            assert updated.next_run_at == datetime(2026, 7, 3, 8, 5, tzinfo=UTC)
    finally:
        with SessionLocal() as db:
            for active_source_id in previously_active_source_ids:
                active_source = db.get(SearchSource, active_source_id)
                if active_source is not None:
                    active_source.is_active = True
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
            setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
            if setting is not None:
                db.delete(setting)
            db.commit()


def test_scheduler_runner_respects_direct_capacity_for_due_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setattr("vinted_monitor.worker.scheduler.get_seen_cache", lambda: type("Cache", (), {"client": fake_redis})())
    now = datetime(2026, 7, 3, 8, 0, tzinfo=UTC)
    source_ids: list[int] = []

    with SessionLocal() as db:
        previously_active_source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.is_active.is_(True))))
        active_proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.is_active.is_(True))))
        for active_source_id in previously_active_source_ids:
            active_source = db.get(SearchSource, active_source_id)
            if active_source is not None:
                active_source.is_active = False
        if active_proxy_ids:
            db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                {ProxyProfile.is_active: False},
                synchronize_session=False,
            )

        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True))
        for index in range(2):
            source = SearchSource(
                name=f"pytest due capacity source {index}",
                url=f"https://www.vinted.es/catalog?search_text=capacity-{index}",
                normalized_query={"search_text": [f"capacity-{index}"]},
                is_active=True,
                monitor_mode="window",
                scheduler_config={
                    "interval_seconds": 300,
                    "jitter_percent": 0,
                    "allowed_windows": [],
                },
                next_run_at=now,
            )
            db.add(source)
            db.flush()
            source_ids.append(source.id)
        db.commit()

    try:
        runner = SchedulerRunner(Settings(scheduler_enabled=True))
        submitted_ids = runner.run_once(now=now)

        assert submitted_ids == [source_ids[0]]
        assert len(fake_redis.values) == 1
    finally:
        with SessionLocal() as db:
            for active_source_id in previously_active_source_ids:
                active_source = db.get(SearchSource, active_source_id)
                if active_source is not None:
                    active_source.is_active = True
            if active_proxy_ids:
                db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                    {ProxyProfile.is_active: True},
                    synchronize_session=False,
                )
            for source_id in source_ids:
                source = db.get(SearchSource, source_id)
                if source is not None:
                    db.delete(source)
            setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
            if setting is not None:
                db.delete(setting)
            db.commit()


def test_schedulable_sources_expires_timed_monitor_before_scheduling() -> None:
    source_id: int | None = None
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest expired timed monitor source",
            url="https://www.vinted.es/catalog?search_text=",
            normalized_query={"search_text": [""]},
            is_active=True,
            monitor_mode="duration",
            scheduler_config={},
            monitor_until=datetime.now(UTC) - timedelta(seconds=1),
        )
        db.add(source)
        db.flush()
        db.add(MonitorSession(source_id=source.id, started_at=datetime.now(UTC) - timedelta(minutes=5)))
        db.commit()
        source_id = source.id

    try:
        with SessionLocal() as db:
            sources = list_schedulable_sources(db)
            stopped = db.get(SearchSource, source_id)

            assert all(entry.id != source_id for entry in sources)
            assert stopped is not None
            assert stopped.is_active is False
            assert stopped.next_run_at is None
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            assert session is not None
            assert session.stopped_at is not None
            assert session.stop_reason == "expired"
    finally:
        with SessionLocal() as db:
            if source_id is not None:
                db.query(MonitorSession).filter(MonitorSession.source_id == source_id).delete(synchronize_session=False)
                source = db.get(SearchSource, source_id)
                if source is not None:
                    db.delete(source)
            db.commit()
