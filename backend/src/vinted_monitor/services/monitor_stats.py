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
    active_session: MonitorSessionDetails | None
    latest_session: MonitorSessionDetails | None
    session_summary: MonitorSummary
    historical_summary: MonitorSummary
    chart_points: list[MonitorChartPoint]


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

    return MonitorStats(
        range=range_name,
        active_session=_session_read(active_session, current_time),
        latest_session=_session_read(latest_session, current_time),
        session_summary=_summary(sessions=[latest_session] if latest_session is not None else [], runs=session_runs, now=current_time),
        historical_summary=_summary(sessions=sessions, runs=runs, now=current_time),
        chart_points=_chart_points(runs, range_name, current_time),
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


def _chart_points(runs: list[Run], range_name: str, now: datetime) -> list[MonitorChartPoint]:
    start, bucket = _range_start_and_bucket(runs, range_name, now)
    end = _range_end(range_name, now)
    points: list[MonitorChartPoint] = []
    current = start
    while current < end:
        bucket_end = _add_bucket(current, bucket)
        points.append(MonitorChartPoint(bucket_start=current, bucket_end=bucket_end, items_found=0, runs_count=0))
        current = bucket_end

    if not points:
        return []

    for run in runs:
        if run.started_at < start or run.started_at >= end:
            continue
        index = _bucket_index(start, run.started_at, bucket)
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


def _range_start_and_bucket(runs: list[Run], range_name: str, now: datetime) -> tuple[datetime, str]:
    if range_name == "minutes":
        return _floor_minute(now - timedelta(hours=2), 5), "5m"
    if range_name == "hours":
        return now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23), "1h"
    if range_name == "days":
        return _start_of_day(now) - timedelta(days=13), "1d"
    if range_name == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), "1d"
    first_run = min((run.started_at for run in runs), default=now)
    return first_run.replace(day=1, hour=0, minute=0, second=0, microsecond=0), "1mo"


def _range_end(range_name: str, now: datetime) -> datetime:
    if range_name == "minutes":
        return _floor_minute(now, 5) + timedelta(minutes=5)
    if range_name == "hours":
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if range_name == "days":
        return _start_of_day(now) + timedelta(days=1)
    if range_name == "month":
        return _add_month(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    return _add_month(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))


def _bucket_index(start: datetime, value: datetime, bucket: str) -> int:
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


def _floor_minute(value: datetime, step: int) -> datetime:
    minute = value.minute - (value.minute % step)
    return value.replace(minute=minute, second=0, microsecond=0)
