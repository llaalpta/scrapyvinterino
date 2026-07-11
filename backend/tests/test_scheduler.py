import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from vinted_monitor.api.main import app
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
    update_scheduler_config,
    update_scheduler_enabled,
    validate_proxy_settings,
)
from vinted_monitor.services.search_sources import archive_source
from vinted_monitor.worker.scheduler import SchedulerRunner


class FakeRedis:
    def __init__(self) -> None:
        self.values: list[str] = []
        self.pending: dict[str, str] = {}

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        if "EXISTS" in script and "LPUSH" in script:
            if keys[0] in self.pending:
                return 0
            self.pending[keys[0]] = args[0]
            self.pending[keys[2]] = args[2]
            self.values.insert(0, args[1])
            return 1
        if "LREM" in script:
            try:
                self.values.remove(args[0])
            except ValueError:
                return 0
            if self.pending.get(keys[1]) == args[1]:
                self.pending.pop(keys[1], None)
            self.pending.pop(keys[2], None)
            return 1
        raise AssertionError("unsupported Redis script")

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        if key.endswith(":processing"):
            return []
        stop = len(self.values) if end == -1 else end + 1
        return self.values[start:stop]

    def get(self, key: str) -> str | None:
        return self.pending.get(key)


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
        state = get_scheduler_state(db, Settings(scheduler_enabled=True, vinted_direct_catalog_enabled=True))

        assert state.enabled is True
        assert state.effective_enabled is True


def test_scheduler_api_does_not_expose_removed_runtime_fields() -> None:
    client = TestClient(app)

    response = client.get("/api/scheduler")

    assert response.status_code == 200
    payload = response.json()
    assert "max_runs_per_proxy" not in payload
    assert "request_retries" not in payload


def test_scheduler_api_rejects_removed_runtime_fields() -> None:
    client = TestClient(app)

    response = client.patch("/api/scheduler", json={"max_runs_per_proxy": 2, "request_retries": 2})

    assert response.status_code == 422


def test_scheduler_config_rejects_unknown_persisted_runtime_fields() -> None:
    with SessionLocal() as db:
        setting = AppSetting(
            key=SCHEDULER_SETTING_KEY,
            value={
                "enabled": True,
                "max_concurrent_runs": 4,
                "max_runs_per_proxy": 1,
                "request_retries": 5,
            },
        )
        db.add(setting)
        db.commit()

        with pytest.raises(SchedulerConfigError, match="unsupported scheduler fields: max_runs_per_proxy, request_retries"):
            update_scheduler_config(db, {"direct_max_concurrent_runs": 2}, Settings(scheduler_enabled=True))


def test_scheduler_proxy_capacity_uses_proxy_profile_limits() -> None:
    proxy_ids: list[int] = []
    active_proxy_ids: list[int] = []
    with SessionLocal() as db:
        active_proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.is_active.is_(True))))
        if active_proxy_ids:
            db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                {ProxyProfile.is_active: False},
                synchronize_session=False,
            )
        update_scheduler_config(
            db,
            {"enabled": True, "max_concurrent_runs": 20, "allow_direct_without_proxy": False},
            Settings(scheduler_enabled=True),
        )
        for index, limit in enumerate((3, 2)):
            proxy = ProxyProfile(
                name=f"pytest capacity proxy {index}",
                scheme="http",
                kind="residential",
                host=f"proxy-{index}.example",
                port=7000 + index,
                max_concurrent_runs=limit,
                is_active=True,
            )
            db.add(proxy)
            db.flush()
            proxy_ids.append(proxy.id)
        db.commit()

    try:
        with SessionLocal() as db:
            state = get_scheduler_state(db, Settings(scheduler_enabled=True))

            assert state.proxy_capacity == 5
            assert state.direct_capacity == 0
            assert state.effective_capacity == 5
    finally:
        with SessionLocal() as db:
            for proxy_id in proxy_ids:
                proxy = db.get(ProxyProfile, proxy_id)
                if proxy is not None:
                    db.delete(proxy)
            if active_proxy_ids:
                db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                    {ProxyProfile.is_active: True},
                    synchronize_session=False,
                )
            setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
            if setting is not None:
                db.delete(setting)
            db.commit()


def test_normalize_scheduler_config_applies_defaults() -> None:
    assert normalize_scheduler_config({}) == {
        "interval_seconds": 300,
        "jitter_percent": 20,
        "allowed_windows": [],
        "stop_after_vinted_session_uses": None,
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
        ({"stop_after_vinted_session_uses": 0}, "stop_after_vinted_session_uses must be between 1 and 1000"),
        ({"stop_after_vinted_session_uses": 1001}, "stop_after_vinted_session_uses must be between 1 and 1000"),
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

        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True, vinted_direct_catalog_enabled=True))
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
        runner = SchedulerRunner(Settings(scheduler_enabled=True, vinted_direct_catalog_enabled=True))
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
    active_proxy_ids: list[int] = []

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

        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True, vinted_direct_catalog_enabled=True))
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
        runner = SchedulerRunner(Settings(scheduler_enabled=True, vinted_direct_catalog_enabled=True))
        submitted_ids = runner.run_once(now=now)

        assert submitted_ids == [source_id]
        assert len(fake_redis.values) == 1
        payload = json.loads(fake_redis.values[0])
        assert payload["source_id"] == source_id
        assert payload["source_url"] == "https://www.vinted.es/catalog?search_text=nike"
        assert payload["trigger"] == "scheduler"
        assert payload["proxy_profile_id"] is None
        assert "proxy_url_template" not in payload

        coalesced_ids = runner.run_once(now=now + timedelta(minutes=5))

        assert coalesced_ids == []
        assert len(fake_redis.values) == 1
        with SessionLocal() as db:
            updated = db.get(SearchSource, source_id)
            assert updated is not None
            assert updated.next_run_at == datetime(2026, 7, 3, 8, 10, tzinfo=UTC)
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

        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True, vinted_direct_catalog_enabled=True))
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
        runner = SchedulerRunner(Settings(scheduler_enabled=True, vinted_direct_catalog_enabled=True))
        submitted_ids = runner.run_once(now=now)

        assert submitted_ids == [source_ids[0]]
        assert len(fake_redis.values) == 1

        submitted_while_first_is_pending = runner.run_once(now=now + timedelta(minutes=1))

        assert submitted_while_first_is_pending == []
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


def test_archive_cancels_scheduler_task_that_is_still_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = FakeRedis()
    cache = type("Cache", (), {"client": fake_redis})()
    settings = Settings(
        scheduler_enabled=True,
        vinted_direct_catalog_enabled=True,
        worker_task_queue_key="pytest:archive-scheduler-queue",
    )
    monkeypatch.setattr("vinted_monitor.worker.scheduler.get_seen_cache", lambda: cache)
    monkeypatch.setattr("vinted_monitor.services.search_sources.get_seen_cache", lambda value: cache)
    monkeypatch.setattr("vinted_monitor.services.search_sources.get_settings", lambda: settings)
    now = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)

    with SessionLocal() as db:
        previously_active_source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.is_active.is_(True))))
        active_proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.is_active.is_(True))))
        if previously_active_source_ids:
            db.query(SearchSource).filter(SearchSource.id.in_(previously_active_source_ids)).update(
                {SearchSource.is_active: False},
                synchronize_session=False,
            )
        if active_proxy_ids:
            db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                {ProxyProfile.is_active: False},
                synchronize_session=False,
            )
        update_scheduler_enabled(db, True, settings)
        source = SearchSource(
            name="pytest archive queued source",
            url="https://www.vinted.es/catalog?search_text=archive-queued",
            normalized_query={"search_text": ["archive-queued"]},
            is_active=True,
            monitor_mode="window",
            scheduler_config={"interval_seconds": 300, "jitter_percent": 0, "allowed_windows": []},
            next_run_at=now,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

    try:
        assert SchedulerRunner(settings).run_once(now=now) == [source_id]
        assert len(fake_redis.values) == 1

        with SessionLocal() as db:
            archive_source(db, source_id)

        assert fake_redis.values == []
        with SessionLocal() as db:
            archived = db.get(SearchSource, source_id)
            assert archived is not None
            assert archived.archived_at is not None
            assert archived.is_active is False
            assert archived.next_run_at is None
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
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
            setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
            if setting is not None:
                db.delete(setting)
            db.commit()


def test_scheduler_revalidates_source_after_archive_commits_during_initial_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = FakeRedis()
    cache = type("Cache", (), {"client": fake_redis})()
    settings = Settings(
        scheduler_enabled=True,
        vinted_direct_catalog_enabled=True,
        worker_task_queue_key="pytest:archive-snapshot-queue",
    )
    snapshot_taken = Event()
    resume_scheduler = Event()

    def pause_after_initial_snapshot(*args, **kwargs) -> list:
        snapshot_taken.set()
        assert resume_scheduler.wait(timeout=5)
        return []

    monkeypatch.setattr("vinted_monitor.worker.scheduler.get_seen_cache", lambda: cache)
    monkeypatch.setattr("vinted_monitor.worker.scheduler.pending_tasks", pause_after_initial_snapshot)
    monkeypatch.setattr("vinted_monitor.services.search_sources.get_seen_cache", lambda value: cache)
    monkeypatch.setattr("vinted_monitor.services.search_sources.get_settings", lambda: settings)
    now = datetime(2026, 7, 3, 9, 30, tzinfo=UTC)

    with SessionLocal() as db:
        previously_active_source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.is_active.is_(True))))
        active_proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.is_active.is_(True))))
        if previously_active_source_ids:
            db.query(SearchSource).filter(SearchSource.id.in_(previously_active_source_ids)).update(
                {SearchSource.is_active: False},
                synchronize_session=False,
            )
        if active_proxy_ids:
            db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                {ProxyProfile.is_active: False},
                synchronize_session=False,
            )
        update_scheduler_enabled(db, True, settings)
        source = SearchSource(
            name="pytest archive snapshot source",
            url="https://www.vinted.es/catalog?search_text=archive-snapshot",
            normalized_query={"search_text": ["archive-snapshot"]},
            is_active=True,
            monitor_mode="window",
            scheduler_config={"interval_seconds": 300, "jitter_percent": 0, "allowed_windows": []},
            next_run_at=now,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

    try:
        runner = SchedulerRunner(settings)
        with ThreadPoolExecutor(max_workers=1) as executor:
            scheduler_future = executor.submit(runner.run_once, now)
            assert snapshot_taken.wait(timeout=5)
            with SessionLocal() as db:
                archive_source(db, source_id)
            resume_scheduler.set()
            assert scheduler_future.result(timeout=5) == []

        assert fake_redis.values == []
        with SessionLocal() as db:
            archived = db.get(SearchSource, source_id)
            assert archived is not None
            assert archived.archived_at is not None
            assert archived.is_active is False
            assert archived.next_run_at is None
    finally:
        resume_scheduler.set()
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
