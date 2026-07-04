from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import MonitorSession, Run, SearchSource

STATS_RANGES = {"minutes", "hours", "days", "month", "all"}


@dataclass(frozen=True)
class MonitorChartPoint:
    bucket_start: datetime
    bucket_end: datetime
    items_found: int
    runs_count: int


@dataclass(frozen=True)
class MonitorSummary:
    sessions_count: int
    active_seconds: int
    runs_count: int
    failed_runs: int
    items_found: int
    items_new: int
    items_discarded_by_filters: int
    opportunities_created: int


@dataclass(frozen=True)
class MonitorSessionDetails:
    id: int
    started_at: datetime
    stopped_at: datetime | None
    stop_reason: str | None
    duration_seconds: int


@dataclass(frozen=True)
class MonitorStats:
    range: str
    range_start: datetime
    range_end: datetime
    bucket_label: str
    bucket_seconds: int | None
    active_session: MonitorSessionDetails | None
    latest_session: MonitorSessionDetails | None
    session_summary: MonitorSummary
    historical_summary: MonitorSummary
    chart_points: list[MonitorChartPoint]


@dataclass(frozen=True)
class MonitorChartConfig:
    start: datetime
    end: datetime
    bucket: str
    bucket_label: str
    bucket_seconds: int | None


class MonitorStatsNotFoundError(ValueError):
    pass


class MonitorStatsRangeError(ValueError):
    pass


def get_monitor_stats(db: Session, monitor_id: int, *, range_name: str = "hours", now: datetime | None = None) -> MonitorStats:
    if range_name not in STATS_RANGES:
        raise MonitorStatsRangeError("range must be one of minutes, hours, days, month, all")

    current_time = now or datetime.now(UTC)
    source = db.get(SearchSource, monitor_id)
    if source is None or source.archived_at is not None:
        raise MonitorStatsNotFoundError(f"Monitor {monitor_id} does not exist")

    sessions = list(
        db.scalars(
            select(MonitorSession)
            .where(MonitorSession.source_id == monitor_id)
            .order_by(MonitorSession.started_at.asc(), MonitorSession.id.asc())
        )
    )
    runs = list(db.scalars(select(Run).where(Run.source_id == monitor_id).order_by(Run.started_at.asc(), Run.id.asc())))
    active_session = next((session for session in reversed(sessions) if session.stopped_at is None), None)
    latest_session = active_session or next((session for session in reversed(sessions) if session.stopped_at is not None), None)
    session_runs = [run for run in runs if latest_session is not None and run.monitor_session_id == latest_session.id]
    chart_config = _chart_config(runs, range_name, current_time)

    return MonitorStats(
        range=range_name,
        range_start=chart_config.start,
        range_end=chart_config.end,
        bucket_label=chart_config.bucket_label,
        bucket_seconds=chart_config.bucket_seconds,
        active_session=_session_read(active_session, current_time),
        latest_session=_session_read(latest_session, current_time),
        session_summary=_summary(sessions=[latest_session] if latest_session is not None else [], runs=session_runs, now=current_time),
        historical_summary=_summary(sessions=sessions, runs=runs, now=current_time),
        chart_points=_chart_points(runs, chart_config),
    )


def _summary(*, sessions: list[MonitorSession | None], runs: list[Run], now: datetime) -> MonitorSummary:
    actual_sessions = [session for session in sessions if session is not None]
    return MonitorSummary(
        sessions_count=len(actual_sessions),
        active_seconds=sum(_session_seconds(session, now) for session in actual_sessions),
        runs_count=len(runs),
        failed_runs=sum(1 for run in runs if run.status == "failed"),
        items_found=sum(run.items_found or 0 for run in runs),
        items_new=sum(run.items_new or 0 for run in runs),
        items_discarded_by_filters=sum(run.items_discarded_by_filters or 0 for run in runs),
        opportunities_created=sum(run.opportunities_created or 0 for run in runs),
    )


def _session_read(session: MonitorSession | None, now: datetime) -> MonitorSessionDetails | None:
    if session is None:
        return None
    return MonitorSessionDetails(
        id=session.id,
        started_at=session.started_at,
        stopped_at=session.stopped_at,
        stop_reason=session.stop_reason,
        duration_seconds=_session_seconds(session, now),
    )


def _session_seconds(session: MonitorSession, now: datetime) -> int:
    end = session.stopped_at or now
    return max(round((end - session.started_at).total_seconds()), 0)


def _chart_points(runs: list[Run], config: MonitorChartConfig) -> list[MonitorChartPoint]:
    start = config.start
    end = config.end
    points: list[MonitorChartPoint] = []
    current = start
    while current < end:
        bucket_end = _add_bucket(current, config.bucket)
        points.append(MonitorChartPoint(bucket_start=current, bucket_end=bucket_end, items_found=0, runs_count=0))
        current = bucket_end

    if not points:
        return []

    for run in runs:
        if run.started_at < start or run.started_at >= end:
            continue
        index = _bucket_index(start, run.started_at, config.bucket)
        if index < 0 or index >= len(points):
            continue
        point = points[index]
        points[index] = MonitorChartPoint(
            bucket_start=point.bucket_start,
            bucket_end=point.bucket_end,
            items_found=point.items_found + (run.items_found or 0),
            runs_count=point.runs_count + 1,
        )
    return points


def _chart_config(runs: list[Run], range_name: str, now: datetime) -> MonitorChartConfig:
    if range_name == "minutes":
        start = _floor_minute(now, 1)
        return MonitorChartConfig(
            start=start,
            end=start + timedelta(minutes=1),
            bucket="5s",
            bucket_label="5 s",
            bucket_seconds=5,
        )
    if range_name == "hours":
        start = _floor_hour(now)
        return MonitorChartConfig(
            start=start,
            end=start + timedelta(hours=1),
            bucket="5m",
            bucket_label="5 min",
            bucket_seconds=300,
        )
    if range_name == "days":
        start = _start_of_day(now)
        return MonitorChartConfig(
            start=start,
            end=start + timedelta(days=1),
            bucket="1h",
            bucket_label="1 h",
            bucket_seconds=3600,
        )
    if range_name == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return MonitorChartConfig(
            start=start,
            end=_add_month(start),
            bucket="1d",
            bucket_label="1 dia",
            bucket_seconds=86400,
        )

    first_run = min((run.started_at for run in runs), default=now)
    span = now - first_run
    if span <= timedelta(hours=1):
        start = _floor_minute(first_run, 5)
        end = _floor_minute(now, 5) + timedelta(minutes=5)
        return MonitorChartConfig(start=start, end=end, bucket="5m", bucket_label="5 min", bucket_seconds=300)
    if span <= timedelta(hours=24):
        start = _floor_hour(first_run)
        end = _floor_hour(now) + timedelta(hours=1)
        return MonitorChartConfig(start=start, end=end, bucket="1h", bucket_label="1 h", bucket_seconds=3600)
    if span <= timedelta(days=90):
        start = _start_of_day(first_run)
        end = _start_of_day(now) + timedelta(days=1)
        return MonitorChartConfig(start=start, end=end, bucket="1d", bucket_label="1 dia", bucket_seconds=86400)

    start = first_run.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = _add_month(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    return MonitorChartConfig(start=start, end=end, bucket="1mo", bucket_label="1 mes", bucket_seconds=None)


def _bucket_index(start: datetime, value: datetime, bucket: str) -> int:
    if bucket == "5s":
        return int((value - start).total_seconds() // 5)
    if bucket == "10s":
        return int((value - start).total_seconds() // 10)
    if bucket == "5m":
        return int((value - start).total_seconds() // 300)
    if bucket == "1h":
        return int((value - start).total_seconds() // 3600)
    if bucket == "1d":
        return (value.date() - start.date()).days
    if bucket == "1mo":
        return (value.year - start.year) * 12 + value.month - start.month
    return 0


def _add_bucket(value: datetime, bucket: str) -> datetime:
    if bucket == "5s":
        return value + timedelta(seconds=5)
    if bucket == "10s":
        return value + timedelta(seconds=10)
    if bucket == "5m":
        return value + timedelta(minutes=5)
    if bucket == "1h":
        return value + timedelta(hours=1)
    if bucket == "1d":
        return value + timedelta(days=1)
    return _add_month(value)


def _add_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _start_of_day(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _floor_minute(value: datetime, step: int) -> datetime:
    minute = value.minute - (value.minute % step)
    return value.replace(minute=minute, second=0, microsecond=0)
