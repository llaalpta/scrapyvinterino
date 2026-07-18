from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.db.models import AppSetting, ProxyProfile, Run, SearchSource
from vinted_monitor.services.monitor_sessions import stop_active_monitor_session
from vinted_monitor.services.scheduler_liveness import scheduler_worker_availability

SCHEDULER_SETTING_KEY = "scheduler"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 3600
DEFAULT_JITTER_PERCENT = 20
MIN_JITTER_PERCENT = 0
MAX_JITTER_PERCENT = 50
MIN_STOP_AFTER_VINTED_SESSION_USES = 1
MAX_STOP_AFTER_VINTED_SESSION_USES = 1000
SUPPORTED_SOURCE_CONFIG_KEYS = {
    "interval_seconds",
    "jitter_percent",
    "allowed_windows",
    "stop_after_vinted_session_uses",
}
RUNTIME_CONFIG_KEYS = {
    "max_concurrent_runs",
    "allow_direct_without_proxy",
    "direct_max_concurrent_runs",
    "catalog_per_page",
    "detail_max_candidates_per_run",
    "request_timeout_ms",
    "stop_monitor_after_consecutive_failures",
    "proxy_cooldown_minutes",
}
INITIAL_RUN_ADMISSION_LOCK_ID = 814_208_009


class SchedulerConfigError(ValueError):
    pass


class SchedulerCapacityError(ValueError):
    pass


class SchedulerUnavailableError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSchedulerConfig:
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    jitter_percent: int = DEFAULT_JITTER_PERCENT
    allowed_windows: tuple[str, ...] = ()
    stop_after_vinted_session_uses: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "interval_seconds": self.interval_seconds,
            "jitter_percent": self.jitter_percent,
            "allowed_windows": list(self.allowed_windows),
            "stop_after_vinted_session_uses": self.stop_after_vinted_session_uses,
        }


@dataclass(frozen=True)
class SchedulerRuntimeConfig:
    max_concurrent_runs: int
    allow_direct_without_proxy: bool
    direct_max_concurrent_runs: int
    direct_runtime_enabled: bool
    catalog_per_page: int
    detail_max_candidates_per_run: int
    request_timeout_ms: int
    stop_monitor_after_consecutive_failures: int
    proxy_cooldown_minutes: int


@dataclass(frozen=True)
class SchedulerState:
    runtime_enabled: bool
    effective_enabled: bool
    worker_available: bool
    worker_last_seen_at: datetime | None
    max_concurrent_runs: int
    per_source_concurrency: int
    poll_interval_seconds: int
    timezone: str
    allow_direct_without_proxy: bool
    direct_max_concurrent_runs: int
    active_proxy_count: int
    proxy_capacity: int
    direct_runtime_enabled: bool
    direct_capacity: int
    effective_capacity: int
    active_periodic_monitors: int
    catalog_per_page: int
    detail_max_candidates_per_run: int
    request_timeout_ms: int
    stop_monitor_after_consecutive_failures: int
    proxy_cooldown_minutes: int


@dataclass(frozen=True)
class RunEgress:
    mode: str
    proxy_profile_id: int | None = None
    proxy_name: str | None = None
    proxy_kind: str | None = None
    proxy_url: str | None = None
    proxy_identity_generation: str | None = None


def get_scheduler_state(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> SchedulerState:
    runtime_config = get_scheduler_runtime_config(db, settings)
    runtime_enabled = settings.scheduler_enabled
    target_country_code = settings.vinted_target_country_code.strip().upper()
    active_proxies = _active_proxy_profiles(db, country_code=target_country_code)
    proxy_capacity = sum(max(proxy.max_concurrent_runs, 1) for proxy in active_proxies)
    direct_runtime_enabled = settings.vinted_direct_catalog_enabled
    direct_capacity = (
        runtime_config.direct_max_concurrent_runs
        if runtime_config.allow_direct_without_proxy and direct_runtime_enabled
        else 0
    )
    effective_capacity = min(runtime_config.max_concurrent_runs, proxy_capacity + direct_capacity)
    worker = scheduler_worker_availability(db, settings, now=now)
    return SchedulerState(
        runtime_enabled=runtime_enabled,
        effective_enabled=runtime_enabled and effective_capacity > 0 and worker.available,
        worker_available=worker.available,
        worker_last_seen_at=worker.last_seen_at,
        max_concurrent_runs=runtime_config.max_concurrent_runs,
        per_source_concurrency=max(settings.scheduler_per_source_concurrency, 1),
        poll_interval_seconds=max(settings.scheduler_poll_interval_seconds, 1),
        timezone=settings.scheduler_timezone,
        allow_direct_without_proxy=runtime_config.allow_direct_without_proxy,
        direct_max_concurrent_runs=runtime_config.direct_max_concurrent_runs,
        direct_runtime_enabled=direct_runtime_enabled,
        active_proxy_count=len(active_proxies),
        proxy_capacity=proxy_capacity,
        direct_capacity=direct_capacity,
        effective_capacity=effective_capacity,
        active_periodic_monitors=_active_periodic_monitor_count(db),
        catalog_per_page=runtime_config.catalog_per_page,
        detail_max_candidates_per_run=runtime_config.detail_max_candidates_per_run,
        request_timeout_ms=runtime_config.request_timeout_ms,
        stop_monitor_after_consecutive_failures=runtime_config.stop_monitor_after_consecutive_failures,
        proxy_cooldown_minutes=runtime_config.proxy_cooldown_minutes,
    )


def get_scheduler_runtime_config(db: Session, settings: Settings) -> SchedulerRuntimeConfig:
    return scheduler_runtime_config_from_value(_read_scheduler_value(db), settings)


def scheduler_runtime_config_from_value(value: dict[str, Any], settings: Settings) -> SchedulerRuntimeConfig:
    _validate_scheduler_runtime_keys(value)
    return SchedulerRuntimeConfig(
        max_concurrent_runs=_validate_int(
            value.get("max_concurrent_runs", settings.scheduler_max_concurrent_runs),
            "max_concurrent_runs",
            1,
            20,
        ),
        allow_direct_without_proxy=bool(value.get("allow_direct_without_proxy", True)),
        direct_max_concurrent_runs=_validate_int(value.get("direct_max_concurrent_runs", 1), "direct_max_concurrent_runs", 0, 10),
        direct_runtime_enabled=settings.vinted_direct_catalog_enabled,
        catalog_per_page=_validate_int(value.get("catalog_per_page", settings.vinted_fast_catalog_per_page), "catalog_per_page", 1, 96),
        detail_max_candidates_per_run=_validate_int(
            value.get("detail_max_candidates_per_run", settings.vinted_detail_max_candidates_per_run),
            "detail_max_candidates_per_run",
            0,
            96,
        ),
        request_timeout_ms=_validate_int(
            value.get("request_timeout_ms", settings.vinted_request_timeout_ms),
            "request_timeout_ms",
            1000,
            60000,
        ),
        stop_monitor_after_consecutive_failures=_validate_int(
            value.get("stop_monitor_after_consecutive_failures", 3),
            "stop_monitor_after_consecutive_failures",
            1,
            20,
        ),
        proxy_cooldown_minutes=_validate_int(value.get("proxy_cooldown_minutes", 10), "proxy_cooldown_minutes", 1, 1440),
    )


def update_scheduler_config(db: Session, payload: dict[str, Any], settings: Settings | None = None) -> SchedulerState:
    _validate_scheduler_runtime_keys(payload)
    setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
    if setting is None:
        setting = AppSetting(key=SCHEDULER_SETTING_KEY, value={})
        db.add(setting)
    current = setting.value or {}
    _validate_scheduler_runtime_keys(current)
    candidate = {**current, **payload}
    scheduler_runtime_config_from_value(candidate, settings or get_settings())
    setting.value = candidate
    db.commit()
    return get_scheduler_state(db, settings or get_settings())


def ensure_scheduler_can_activate(db: Session, settings: Settings, *, source_id: int | None = None) -> None:
    state = get_scheduler_state(db, settings)
    if not state.runtime_enabled:
        raise SchedulerCapacityError("Scheduler runtime is disabled")
    if not state.worker_available:
        raise SchedulerUnavailableError("Scheduler worker is unavailable")
    if state.effective_capacity <= 0:
        raise SchedulerCapacityError("No scheduler egress capacity is available")
    active_count = state.active_periodic_monitors
    if source_id is not None:
        source = db.get(SearchSource, source_id)
        if source is not None and source.is_active and source.monitor_mode != "manual":
            active_count -= 1
    if active_count >= state.effective_capacity:
        raise SchedulerCapacityError("Scheduler capacity limit reached")


def acquire_initial_run_admission_lock(db: Session) -> None:
    """Serialize recurring activation until its first running row is committed."""
    db.execute(select(func.pg_advisory_xact_lock(INITIAL_RUN_ADMISSION_LOCK_ID)))


def choose_run_egress(
    db: Session,
    settings: Settings,
    *,
    active_proxy_counts: dict[int, int] | None = None,
    active_direct_count: int = 0,
) -> RunEgress:
    from vinted_monitor.services.proxies import (
        ProxyProfileEligibilityError,
        effective_proxy_identity_generation,
        list_available_proxy_profiles,
        lock_proxy_profile_for_selection,
        mark_proxy_used,
        proxy_url_for_profile,
    )

    runtime = get_scheduler_runtime_config(db, settings)
    proxy_counts, direct_count = (
        (active_proxy_counts, active_direct_count)
        if active_proxy_counts is not None
        else _active_run_egress_counts(db)
    )
    target_country_code = settings.vinted_target_country_code.strip().upper()
    available_proxies = [
        proxy
        for proxy in list_available_proxy_profiles(db, country_code=target_country_code)
        if proxy_counts.get(proxy.id, 0) < max(proxy.max_concurrent_runs, 1)
    ]
    if available_proxies:
        # Acquire at most one candidate fence per transaction. Retaining an
        # advisory lock for a saturated candidate while trying another can
        # deadlock two selectors when mutable telemetry gives them opposite
        # preference orders, especially while template drift needs exclusive
        # identity reconciliation.
        available_proxy = available_proxies[0]
        try:
            proxy = lock_proxy_profile_for_selection(db, available_proxy.id, settings)
        except ProxyProfileEligibilityError as exc:
            raise SchedulerCapacityError(str(exc)) from exc
        proxy_limit = max(proxy.max_concurrent_runs, 1)
        if proxy_counts.get(proxy.id, 0) >= proxy_limit:
            raise SchedulerCapacityError(f"Proxy profile {proxy.id} capacity changed during egress selection")
        mark_proxy_used(db, proxy.id)
        db.flush()
        return RunEgress(
            mode="proxy",
            proxy_profile_id=proxy.id,
            proxy_name=proxy.name,
            proxy_kind=proxy.kind,
            proxy_url=proxy_url_for_profile(proxy, settings),
            proxy_identity_generation=effective_proxy_identity_generation(proxy),
        )
    if runtime.allow_direct_without_proxy and runtime.direct_runtime_enabled and direct_count < runtime.direct_max_concurrent_runs:
        return RunEgress(mode="direct")
    raise SchedulerCapacityError(
        f"No proxy is available for country {target_country_code} and direct Vinted catalog access is disabled or saturated"
    )


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
    stop_after_vinted_session_uses = _validate_optional_int(
        raw.get("stop_after_vinted_session_uses"),
        "stop_after_vinted_session_uses",
        MIN_STOP_AFTER_VINTED_SESSION_USES,
        MAX_STOP_AFTER_VINTED_SESSION_USES,
    )
    return SourceSchedulerConfig(
        interval_seconds=interval_seconds,
        jitter_percent=jitter_percent,
        allowed_windows=tuple(allowed_windows),
        stop_after_vinted_session_uses=stop_after_vinted_session_uses,
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
        source.monitor_started_at = None
        source.next_run_at = None
        stop_active_monitor_session(db, source.id, stopped_at=current_time, reason="expired")
    db.commit()
    return len(expired_sources)


def source_config(source: SearchSource) -> SourceSchedulerConfig:
    normalized = normalize_scheduler_config(source.scheduler_config)
    return SourceSchedulerConfig(
        interval_seconds=normalized["interval_seconds"],
        jitter_percent=normalized["jitter_percent"],
        allowed_windows=tuple(normalized["allowed_windows"]),
        stop_after_vinted_session_uses=normalized.get("stop_after_vinted_session_uses"),
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
    get_scheduler_timezone(settings)


def get_scheduler_timezone(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.scheduler_timezone)
    except ZoneInfoNotFoundError as exc:
        raise SchedulerConfigError(f"Invalid scheduler timezone: {settings.scheduler_timezone}") from exc


def _read_scheduler_value(db: Session) -> dict[str, Any]:
    setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
    if setting is None:
        return {}
    value = setting.value or {}
    _validate_scheduler_runtime_keys(value)
    return value


def _validate_scheduler_runtime_keys(value: dict[str, Any]) -> None:
    unsupported_keys = sorted(set(value) - RUNTIME_CONFIG_KEYS)
    if unsupported_keys:
        raise SchedulerConfigError(f"unsupported scheduler fields: {', '.join(unsupported_keys)}")


def _active_proxy_profiles(db: Session, *, country_code: str | None = None) -> list[ProxyProfile]:
    current_time = datetime.now(UTC)
    statement = select(ProxyProfile).where(
        ProxyProfile.is_active.is_(True),
        (ProxyProfile.cooldown_until.is_(None) | (ProxyProfile.cooldown_until <= current_time)),
    )
    if country_code:
        statement = statement.where(ProxyProfile.country_code == country_code.strip().upper())
    return list(
        db.scalars(statement)
    )


def _active_periodic_monitor_count(db: Session) -> int:
    return len(
        list(
            db.scalars(
                select(SearchSource.id).where(
                    SearchSource.is_active.is_(True),
                    SearchSource.archived_at.is_(None),
                    SearchSource.monitor_mode != "manual",
                )
            )
        )
    )


def _active_run_egress_counts(db: Session) -> tuple[dict[int, int], int]:
    proxy_counts: dict[int, int] = {}
    direct_count = 0
    rows = db.scalars(
        select(Run.runtime_metadata).where(
            Run.status == "running",
            Run.finished_at.is_(None),
        )
    )
    for metadata in rows:
        if not isinstance(metadata, dict):
            continue
        proxy_profile_id = metadata.get("proxy_profile_id")
        if isinstance(proxy_profile_id, int):
            proxy_counts[proxy_profile_id] = proxy_counts.get(proxy_profile_id, 0) + 1
        elif metadata.get("egress_mode") == "direct":
            direct_count += 1
    return proxy_counts, direct_count


def active_run_egress_counts(db: Session) -> tuple[dict[int, int], int]:
    return _active_run_egress_counts(db)


def _validate_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SchedulerConfigError(f"{field} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise SchedulerConfigError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _validate_optional_int(value: Any, field: str, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    return _validate_int(value, field, minimum, maximum)


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
