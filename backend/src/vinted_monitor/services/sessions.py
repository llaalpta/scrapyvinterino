from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.db.models import MonitorSession, ProxyProfile, SearchSource
from vinted_monitor.providers.vinted_catalog import HttpVintedCatalogProvider
from vinted_monitor.services.filters import filter_hash, get_filter_snapshot
from vinted_monitor.services.proxies import proxy_url_for_profile
from vinted_monitor.services.runs import ManualRunProvider, execute_session_run

ACTIVE_SESSION = "active"
STOPPED_SESSION = "stopped"


class MonitorSessionNotFoundError(ValueError):
    pass


class MonitorSessionConflictError(ValueError):
    pass


class MonitorSessionSourceError(ValueError):
    pass


@dataclass(frozen=True)
class MonitorSessionResult:
    session: MonitorSession
    source_name: str
    proxy_name: str | None


def list_monitor_sessions(db: Session) -> list[MonitorSessionResult]:
    rows = db.execute(
        select(MonitorSession, SearchSource.name.label("source_name"), ProxyProfile.name.label("proxy_name"))
        .join(SearchSource, SearchSource.id == MonitorSession.source_id)
        .outerjoin(ProxyProfile, ProxyProfile.id == MonitorSession.proxy_profile_id)
        .order_by(MonitorSession.started_at.desc(), MonitorSession.id.desc())
    ).all()
    return [MonitorSessionResult(session=row.MonitorSession, source_name=row.source_name, proxy_name=row.proxy_name) for row in rows]


def start_monitor_session(
    db: Session,
    *,
    source_id: int,
    filter_rule_ids: list[int],
    proxy_profile_id: int | None = None,
) -> MonitorSession:
    source = db.get(SearchSource, source_id)
    if source is None:
        raise MonitorSessionSourceError(f"Search source {source_id} does not exist")
    if not source.is_active:
        raise MonitorSessionSourceError(f"Search source {source_id} is paused")
    existing_active = db.scalar(
        select(MonitorSession).where(MonitorSession.source_id == source_id, MonitorSession.status == ACTIVE_SESSION).limit(1)
    )
    if existing_active is not None:
        raise MonitorSessionConflictError(f"Search source {source_id} already has an active monitor session")
    proxy = db.get(ProxyProfile, proxy_profile_id) if proxy_profile_id else None
    if proxy_profile_id and proxy is None:
        raise MonitorSessionSourceError(f"Proxy profile {proxy_profile_id} does not exist")
    if proxy is not None and not proxy.is_active:
        raise MonitorSessionSourceError(f"Proxy profile {proxy_profile_id} is inactive")

    snapshot = get_filter_snapshot(db, filter_rule_ids)
    session = MonitorSession(
        source_id=source.id,
        proxy_profile_id=proxy_profile_id,
        status=ACTIVE_SESSION,
        filter_snapshot=snapshot,
        filter_hash=filter_hash(snapshot),
        cadence_snapshot=source.scheduler_config or {},
        runtime_metadata={
            "auth_mode": "public_anonymous",
            "proxy_profile_id": proxy_profile_id,
        },
    )
    db.add(session)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise MonitorSessionConflictError(f"Search source {source_id} already has an active monitor session") from exc
    db.refresh(session)
    return session


def stop_monitor_session(db: Session, session_id: int) -> MonitorSession:
    session = db.get(MonitorSession, session_id)
    if session is None:
        raise MonitorSessionNotFoundError(f"Monitor session {session_id} does not exist")
    session.status = STOPPED_SESSION
    session.stopped_at = datetime.now(UTC)
    db.commit()
    db.refresh(session)
    return session


def run_monitor_session(
    db: Session,
    session_id: int,
    *,
    provider: ManualRunProvider | None = None,
    settings: Settings | None = None,
    trigger: str = "manual",
):
    settings = settings or get_settings()
    session = db.get(MonitorSession, session_id)
    if session is None:
        raise MonitorSessionNotFoundError(f"Monitor session {session_id} does not exist")
    if session.status != ACTIVE_SESSION:
        raise MonitorSessionConflictError(f"Monitor session {session_id} is not active")

    run_provider = provider
    if run_provider is None:
        proxy = db.get(ProxyProfile, session.proxy_profile_id) if session.proxy_profile_id else None
        run_provider = HttpVintedCatalogProvider(settings=settings, proxy_url=proxy_url_for_profile(proxy, settings))
    return execute_session_run(db, session_id, provider=run_provider, trigger=trigger)
