from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import AppSetting

SCHEDULER_WORKER_HEARTBEAT_KEY = "scheduler_worker_heartbeat"


@dataclass(frozen=True)
class SchedulerWorkerAvailability:
    available: bool
    last_seen_at: datetime | None


def touch_scheduler_worker_heartbeat(db: Session, *, now: datetime | None = None) -> datetime:
    current_time = now or datetime.now(UTC)
    setting = db.get(AppSetting, SCHEDULER_WORKER_HEARTBEAT_KEY)
    if setting is None:
        setting = AppSetting(key=SCHEDULER_WORKER_HEARTBEAT_KEY, value={})
        db.add(setting)
    setting.value = {"last_seen_at": current_time.isoformat()}
    return current_time


def scheduler_worker_availability(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> SchedulerWorkerAvailability:
    setting = db.get(AppSetting, SCHEDULER_WORKER_HEARTBEAT_KEY)
    last_seen_at = _parse_last_seen_at(setting.value if setting is not None else None)
    current_time = now or datetime.now(UTC)
    if last_seen_at is not None and last_seen_at > current_time:
        last_seen_at = None
    available = last_seen_at is not None and last_seen_at >= current_time - timedelta(
        seconds=settings.scheduler_worker_heartbeat_timeout_seconds
    )
    return SchedulerWorkerAvailability(available=available, last_seen_at=last_seen_at)


def _parse_last_seen_at(value: object) -> datetime | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("last_seen_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
