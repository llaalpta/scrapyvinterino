from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.db.models import AppSetting, SearchSource

SCHEDULER_SETTING_KEY = "scheduler"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 3600
DEFAULT_JITTER_PERCENT = 20
MIN_JITTER_PERCENT = 0
MAX_JITTER_PERCENT = 50
SUPPORTED_SOURCE_CONFIG_KEYS = {"interval_seconds", "jitter_percent", "allowed_windows"}


class SchedulerConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSchedulerConfig:
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    jitter_percent: int = DEFAULT_JITTER_PERCENT
    allowed_windows: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "interval_seconds": self.interval_seconds,
            "jitter_percent": self.jitter_percent,
            "allowed_windows": list(self.allowed_windows),
        }


@dataclass(frozen=True)
class SchedulerState:
    enabled: bool
    runtime_enabled: bool
    effective_enabled: bool
    max_concurrent_runs: int
    per_source_concurrency: int
    poll_interval_seconds: int
    timezone: str
    proxy_enabled: bool
    proxy_configured: bool


def get_scheduler_state(db: Session, settings: Settings) -> SchedulerState:
    enabled = _read_scheduler_enabled(db)
    runtime_enabled = settings.scheduler_enabled
    return SchedulerState(
        enabled=enabled,
        runtime_enabled=runtime_enabled,
        effective_enabled=enabled and runtime_enabled,
        max_concurrent_runs=max(settings.scheduler_max_concurrent_runs, 1),
        per_source_concurrency=max(settings.scheduler_per_source_concurrency, 1),
        poll_interval_seconds=max(settings.scheduler_poll_interval_seconds, 1),
        timezone=settings.scheduler_timezone,
        proxy_enabled=settings.vinted_proxy_enabled,
        proxy_configured=bool(settings.vinted_proxy_url),
    )


def update_scheduler_enabled(db: Session, enabled: bool, settings: Settings | None = None) -> SchedulerState:
    setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
    if setting is None:
        setting = AppSetting(key=SCHEDULER_SETTING_KEY, value={})
        db.add(setting)
    setting.value = {**(setting.value or {}), "enabled": enabled}
    db.commit()
    return get_scheduler_state(db, settings or get_settings())


def normalize_scheduler_config(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = value or {}
    unsupported_keys = sorted(set(raw) - SUPPORTED_SOURCE_CONFIG_KEYS)
    if unsupported_keys:
        raise SchedulerConfigError(f"unsupported scheduler_config fields: {', '.join(unsupported_keys)}")
    interval_seconds = _validate_int(
        raw.get("interval_seconds", DEFAULT_INTERVAL_SECONDS),
        "interval_seconds",
        MIN_INTERVAL_SECONDS,
        MAX_INTERVAL_SECONDS,
    )
    jitter_percent = _validate_int(
        raw.get("jitter_percent", DEFAULT_JITTER_PERCENT),
        "jitter_percent",
        MIN_JITTER_PERCENT,
        MAX_JITTER_PERCENT,
    )
    allowed_windows = _validate_allowed_windows(raw.get("allowed_windows", []))
    return SourceSchedulerConfig(
        interval_seconds=interval_seconds,
        jitter_percent=jitter_percent,
        allowed_windows=tuple(allowed_windows),
    ).as_dict()


def list_schedulable_sources(db: Session) -> list[SearchSource]:
    expire_source_monitors(db)
    return list(
        db.scalars(
            select(SearchSource)
            .where(
                SearchSource.is_active.is_(True),
                SearchSource.archived_at.is_(None),
                SearchSource.monitor_mode != "manual",
            )
            .order_by(SearchSource.id.asc())
        )
    )


def expire_source_monitors(db: Session, now: datetime | None = None) -> int:
    current_time = now or datetime.now(UTC)
    expired_sources = list(
        db.scalars(
            select(SearchSource).where(
                SearchSource.is_active.is_(True),
                SearchSource.monitor_until.is_not(None),
                SearchSource.monitor_until <= current_time,
            )
        )
    )
    if not expired_sources:
        return 0
    for source in expired_sources:
        source.is_active = False
        source.next_run_at = None
    db.commit()
    return len(expired_sources)


def source_config(source: SearchSource) -> SourceSchedulerConfig:
    normalized = normalize_scheduler_config(source.scheduler_config)
    return SourceSchedulerConfig(
        interval_seconds=normalized["interval_seconds"],
        jitter_percent=normalized["jitter_percent"],
        allowed_windows=tuple(normalized["allowed_windows"]),
    )

def is_within_allowed_windows(
    now: datetime,
    allowed_windows: tuple[str, ...],
    timezone: ZoneInfo | None = None,
) -> bool:
    if not allowed_windows:
        return True
    local_now = _to_local(now, timezone)
    current_time = local_now.timetz().replace(tzinfo=None)
    return any(_time_in_window(current_time, *_parse_window(window)) for window in allowed_windows)


def next_run_after(
    now: datetime,
    config: SourceSchedulerConfig,
    rng: random.Random | None = None,
    timezone: ZoneInfo | None = None,
) -> datetime:
    generator = rng or random.Random()
    jitter_span = config.interval_seconds * config.jitter_percent / 100
    jitter_seconds = int(generator.uniform(-jitter_span, jitter_span)) if jitter_span else 0
    candidate = now + timedelta(seconds=max(config.interval_seconds + jitter_seconds, MIN_INTERVAL_SECONDS))
    if is_within_allowed_windows(candidate, config.allowed_windows, timezone):
        return candidate
    return _next_allowed_window_start(candidate, config.allowed_windows, timezone)


def validate_proxy_settings(settings: Settings) -> None:
    if settings.vinted_proxy_enabled and not settings.vinted_proxy_url:
        raise SchedulerConfigError("Vinted proxy is enabled but VINTED_PROXY_URL is not configured")
    get_scheduler_timezone(settings)


def get_scheduler_timezone(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.scheduler_timezone)
    except ZoneInfoNotFoundError as exc:
        raise SchedulerConfigError(f"Invalid scheduler timezone: {settings.scheduler_timezone}") from exc


def _read_scheduler_enabled(db: Session) -> bool:
    setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
    if setting is None:
        return False
    return bool((setting.value or {}).get("enabled", False))


def _validate_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SchedulerConfigError(f"{field} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise SchedulerConfigError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _validate_allowed_windows(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise SchedulerConfigError("allowed_windows must be a list")
    windows: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise SchedulerConfigError("allowed_windows entries must be strings")
        _parse_window(entry)
        windows.append(entry)
    return windows


def _parse_window(value: str) -> tuple[time, time]:
    parts = value.split("-")
    if len(parts) != 2:
        raise SchedulerConfigError("allowed_windows entries must use HH:MM-HH:MM")
    try:
        start = time.fromisoformat(parts[0])
        end = time.fromisoformat(parts[1])
    except ValueError as exc:
        raise SchedulerConfigError("allowed_windows entries must use HH:MM-HH:MM") from exc
    if start == end:
        raise SchedulerConfigError("allowed_windows start and end cannot be equal")
    return start, end


def _time_in_window(current: time, start: time, end: time) -> bool:
    if start < end:
        return start <= current <= end
    return current >= start or current <= end


def _next_allowed_window_start(
    candidate: datetime,
    allowed_windows: tuple[str, ...],
    timezone: ZoneInfo | None = None,
) -> datetime:
    if not allowed_windows:
        return candidate
    local_candidate = _to_local(candidate, timezone)
    starts = sorted(_parse_window(window)[0] for window in allowed_windows)
    current_time = local_candidate.timetz().replace(tzinfo=None)
    for start in starts:
        if current_time <= start:
            return _from_local(
                local_candidate.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0),
                candidate,
            )
    first = starts[0]
    next_local = (local_candidate + timedelta(days=1)).replace(hour=first.hour, minute=first.minute, second=0, microsecond=0)
    return _from_local(next_local, candidate)


def _to_local(value: datetime, timezone: ZoneInfo | None) -> datetime:
    if timezone is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone)


def _from_local(local_value: datetime, original: datetime) -> datetime:
    if original.tzinfo is None:
        return local_value.replace(tzinfo=None)
    return local_value.astimezone(original.tzinfo)
