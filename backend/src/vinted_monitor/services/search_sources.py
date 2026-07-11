from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import SearchSource
from vinted_monitor.providers.catalog_url import analyze_catalog_url, ensure_catalog_url_filters_supported
from vinted_monitor.services.filters import normalize_filter_definition
from vinted_monitor.services.monitor_sessions import start_monitor_session, stop_active_monitor_session
from vinted_monitor.services.scheduler import normalize_scheduler_config
from vinted_monitor.services.seen_cache import get_seen_cache
from vinted_monitor.services.task_queue import TaskQueueError, cancel_ready_task_for_source
from vinted_monitor.services.vinted_sessions import invalidate_vinted_sessions_for_source

ALLOWED_VINTED_CATALOG_HOSTS = {"www.vinted.es", "vinted.es"}
ALLOWED_VINTED_CATALOG_PATHS = {"/catalog", "/catalog/"}
MONITOR_MODES = {"manual", "continuous", "duration", "window"}


class SearchSourceNotFoundError(ValueError):
    pass


class SearchSourceConfigError(ValueError):
    pass


class SearchSourceActiveError(ValueError):
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
    if parsed.scheme != "https":
        raise ValueError("Search source URL must use https")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Search source URL has an invalid port") from exc
    if parsed.username or parsed.password or port is not None:
        raise ValueError("Search source URL cannot include credentials or an explicit port")

    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if hostname not in ALLOWED_VINTED_CATALOG_HOSTS:
        raise ValueError("Search source URL must point to Vinted Spain")

    if parsed.path not in ALLOWED_VINTED_CATALOG_PATHS:
        raise ValueError("Search source URL must point to a Vinted catalog page")

    ensure_catalog_url_filters_supported(normalized_url)
    return normalized_url


def normalize_vinted_catalog_url(url: str) -> dict[str, list[str]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return {key: values for key, values in sorted(query.items())}


def catalog_filter_compatibility(url: str) -> dict:
    return analyze_catalog_url(url).as_dict()


def create_source(db: Session, name: str, url: str) -> SearchSource:
    validated_name = validate_search_source_name(name)
    validated_url = validate_vinted_catalog_url(url)
    source = SearchSource(
        name=validated_name,
        url=validated_url,
        normalized_query=normalize_vinted_catalog_url(validated_url),
        is_active=False,
        monitor_mode="manual",
        filter_definition={"blacklist_terms": []},
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
    name: str | None = None,
    url: str | None = None,
    scheduler_config: dict | None = None,
    monitor_mode: str | None = None,
    duration_minutes: int | None = None,
    clear_duration_minutes: bool = False,
    filter_definition: dict | None = None,
) -> SearchSource:
    source = _get_live_source(db, source_id)
    if source.is_active:
        raise SearchSourceActiveError(f"Monitor {source_id} is active; stop it before editing configuration")

    if name is not None:
        source.name = validate_search_source_name(name)
    if url is not None:
        validated_url = validate_vinted_catalog_url(url)
        source.url = validated_url
        source.normalized_query = normalize_vinted_catalog_url(validated_url)
    if scheduler_config is not None:
        source.scheduler_config = normalize_scheduler_config(scheduler_config)
    if monitor_mode is not None:
        source.monitor_mode = validate_monitor_mode(monitor_mode)
    if duration_minutes is not None:
        if duration_minutes < 1 or duration_minutes > 1440:
            raise SearchSourceConfigError("duration_minutes must be between 1 and 1440")
        source.duration_minutes = duration_minutes
    elif clear_duration_minutes:
        source.duration_minutes = None
    if filter_definition is not None:
        source.filter_definition = normalize_filter_definition(filter_definition)
    _validate_monitor_runtime_config(source)

    db.commit()
    db.refresh(source)
    return source


def start_source_monitor(db: Session, source_id: int) -> SearchSource:
    source = _get_live_source(db, source_id)
    _validate_monitor_runtime_config(source)
    now = datetime.now(UTC)
    source.monitor_started_at = now
    source.last_run_at = None
    if source.monitor_mode == "duration":
        if source.duration_minutes is None:
            raise SearchSourceConfigError("duration_minutes is required for duration monitor mode")
        from datetime import timedelta

        source.monitor_until = now + timedelta(minutes=source.duration_minutes)
    else:
        source.monitor_until = None
    source.next_run_at = now
    source.is_active = source.monitor_mode != "manual"
    if source.is_active:
        start_monitor_session(db, source, started_at=now)
    db.commit()
    db.refresh(source)
    return source


def stop_source_monitor(db: Session, source_id: int) -> SearchSource:
    source = _get_live_source(db, source_id)
    source.is_active = False
    source.monitor_started_at = None
    source.next_run_at = None
    source.monitor_until = None
    stop_active_monitor_session(db, source.id, reason="stopped")
    _cancel_ready_source_task(source.id)
    db.commit()
    db.refresh(source)
    return source


def archive_source(db: Session, source_id: int) -> None:
    source = db.scalar(
        select(SearchSource)
        .where(SearchSource.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.archived_at is not None:
        return

    now = datetime.now(UTC)
    source.is_active = False
    source.next_run_at = None
    source.monitor_until = None
    source.archived_at = now
    stop_active_monitor_session(db, source.id, stopped_at=now, reason="archived")
    invalidate_vinted_sessions_for_source(db, source.id, reason="Monitor archived")
    _cancel_ready_source_task(source.id)
    db.commit()


def validate_monitor_mode(value: str) -> str:
    if value not in MONITOR_MODES:
        raise SearchSourceConfigError("monitor_mode must be one of manual, continuous, duration, window")
    return value


def _get_live_source(db: Session, source_id: int) -> SearchSource:
    source = db.scalar(
        select(SearchSource)
        .where(SearchSource.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    return source


def _cancel_ready_source_task(source_id: int) -> None:
    settings = get_settings()
    try:
        cancel_ready_task_for_source(
            get_seen_cache(settings).client,
            source_id,
            queue_key=settings.worker_task_queue_key,
        )
    except TaskQueueError:
        pass


def _validate_monitor_runtime_config(source: SearchSource) -> None:
    validate_monitor_mode(source.monitor_mode)
    if source.monitor_mode == "duration" and source.duration_minutes is None:
        raise SearchSourceConfigError("duration_minutes is required for duration monitor mode")
    allowed_windows = (source.scheduler_config or {}).get("allowed_windows", [])
    if source.monitor_mode == "window" and not allowed_windows:
        raise SearchSourceConfigError("allowed_windows is required for window monitor mode")
