from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import MonitorSession, SearchSource
from vinted_monitor.services.scheduler import normalize_scheduler_config

ALLOWED_VINTED_CATALOG_HOSTS = {"www.vinted.es", "vinted.es"}
ALLOWED_VINTED_CATALOG_PATHS = {"/catalog", "/catalog/"}


class SearchSourceNotFoundError(ValueError):
    pass


def validate_search_source_name(name: str) -> str:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Search source name cannot be empty")
    return normalized_name


def validate_vinted_catalog_url(url: str) -> str:
    normalized_url = url.strip()
    if not normalized_url:
        raise ValueError("Search source URL cannot be empty")

    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Search source URL must use http or https")

    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if hostname not in ALLOWED_VINTED_CATALOG_HOSTS:
        raise ValueError("Search source URL must point to Vinted Spain")

    if parsed.path not in ALLOWED_VINTED_CATALOG_PATHS:
        raise ValueError("Search source URL must point to a Vinted catalog page")

    return normalized_url


def normalize_vinted_catalog_url(url: str) -> dict[str, list[str]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return {key: values for key, values in sorted(query.items())}


def create_source(db: Session, name: str, url: str) -> SearchSource:
    validated_name = validate_search_source_name(name)
    validated_url = validate_vinted_catalog_url(url)
    source = SearchSource(
        name=validated_name,
        url=validated_url,
        normalized_query=normalize_vinted_catalog_url(validated_url),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def list_sources(db: Session) -> list[SearchSource]:
    return list(db.scalars(select(SearchSource).where(SearchSource.archived_at.is_(None)).order_by(SearchSource.id.desc())))


def update_source(
    db: Session,
    source_id: int,
    *,
    is_active: bool | None = None,
    scheduler_config: dict | None = None,
) -> SearchSource:
    source = db.get(SearchSource, source_id)
    if source is None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")

    if is_active is not None:
        source.is_active = is_active
    if scheduler_config is not None:
        source.scheduler_config = normalize_scheduler_config(scheduler_config)

    db.commit()
    db.refresh(source)
    return source


def archive_source(db: Session, source_id: int) -> None:
    source = db.get(SearchSource, source_id)
    if source is None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.archived_at is not None:
        return

    now = datetime.now(UTC)
    source.is_active = False
    source.archived_at = now
    active_sessions = db.scalars(
        select(MonitorSession).where(MonitorSession.source_id == source_id, MonitorSession.status == "active")
    )
    for session in active_sessions:
        session.status = "stopped"
        session.stopped_at = now
    db.commit()
