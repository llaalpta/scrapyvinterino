from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import MonitorSession, SearchSource


def start_monitor_session(db: Session, source: SearchSource, *, started_at: datetime | None = None) -> MonitorSession | None:
    if source.monitor_mode == "manual":
        return None
    existing = get_active_monitor_session(db, source.id)
    if existing is not None:
        return existing
    session = MonitorSession(source_id=source.id, started_at=started_at or datetime.now(UTC))
    db.add(session)
    db.flush()
    return session


def stop_active_monitor_session(
    db: Session,
    source_id: int,
    *,
    stopped_at: datetime | None = None,
    reason: str,
) -> MonitorSession | None:
    session = get_active_monitor_session(db, source_id)
    if session is None:
        return None
    session.stopped_at = stopped_at or datetime.now(UTC)
    session.stop_reason = reason
    db.flush()
    return session


def get_active_monitor_session(db: Session, source_id: int) -> MonitorSession | None:
    return db.scalar(
        select(MonitorSession)
        .where(MonitorSession.source_id == source_id, MonitorSession.stopped_at.is_(None))
        .order_by(MonitorSession.started_at.desc(), MonitorSession.id.desc())
        .limit(1)
    )
