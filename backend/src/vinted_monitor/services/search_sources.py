from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import FilterRule, ProxyProfile, SearchSource
from vinted_monitor.services.scheduler import normalize_scheduler_config

ALLOWED_VINTED_CATALOG_HOSTS = {"www.vinted.es", "vinted.es"}
ALLOWED_VINTED_CATALOG_PATHS = {"/catalog", "/catalog/"}
MONITOR_MODES = {"manual", "continuous", "duration", "window"}


class SearchSourceNotFoundError(ValueError):
    pass


class SearchSourceConfigError(ValueError):
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
        is_active=False,
        monitor_mode="manual",
        filter_rule_ids=[],
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
    is_active: bool | None = None,
    scheduler_config: dict | None = None,
    monitor_mode: str | None = None,
    duration_minutes: int | None = None,
    clear_duration_minutes: bool = False,
    filter_rule_ids: list[int] | None = None,
    proxy_profile_id: int | None = None,
    clear_proxy_profile: bool = False,
) -> SearchSource:
    source = db.get(SearchSource, source_id)
    if source is None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")

    if name is not None:
        source.name = validate_search_source_name(name)
    if url is not None:
        validated_url = validate_vinted_catalog_url(url)
        source.url = validated_url
        source.normalized_query = normalize_vinted_catalog_url(validated_url)
    if is_active is not None:
        source.is_active = is_active
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
    if filter_rule_ids is not None:
        source.filter_rule_ids = _validate_filter_rule_ids(db, filter_rule_ids)
    if clear_proxy_profile:
        source.proxy_profile_id = None
    elif proxy_profile_id is not None:
        source.proxy_profile_id = _validate_proxy_profile_id(db, proxy_profile_id)
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
    db.commit()
    db.refresh(source)
    return source


def stop_source_monitor(db: Session, source_id: int) -> SearchSource:
    source = _get_live_source(db, source_id)
    source.is_active = False
    source.next_run_at = None
    source.monitor_until = None
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
    source.next_run_at = None
    source.monitor_until = None
    source.archived_at = now
    db.commit()


def validate_monitor_mode(value: str) -> str:
    if value not in MONITOR_MODES:
        raise SearchSourceConfigError("monitor_mode must be one of manual, continuous, duration, window")
    return value


def _get_live_source(db: Session, source_id: int) -> SearchSource:
    source = db.get(SearchSource, source_id)
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    return source


def _validate_filter_rule_ids(db: Session, filter_rule_ids: list[int]) -> list[int]:
    unique_ids = list(dict.fromkeys(int(rule_id) for rule_id in filter_rule_ids))
    if not unique_ids:
        return []
    found_ids = set(db.scalars(select(FilterRule.id).where(FilterRule.id.in_(unique_ids), FilterRule.is_active.is_(True))))
    missing_ids = sorted(set(unique_ids) - found_ids)
    if missing_ids:
        raise SearchSourceConfigError(f"Filter rules do not exist or are inactive: {', '.join(str(entry) for entry in missing_ids)}")
    return unique_ids


def _validate_proxy_profile_id(db: Session, proxy_profile_id: int) -> int:
    proxy = db.get(ProxyProfile, proxy_profile_id)
    if proxy is None:
        raise SearchSourceConfigError(f"Proxy profile {proxy_profile_id} does not exist")
    if not proxy.is_active:
        raise SearchSourceConfigError(f"Proxy profile {proxy_profile_id} is inactive")
    return proxy_profile_id


def _validate_monitor_runtime_config(source: SearchSource) -> None:
    validate_monitor_mode(source.monitor_mode)
    if source.monitor_mode == "duration" and source.duration_minutes is None:
        raise SearchSourceConfigError("duration_minutes is required for duration monitor mode")
    allowed_windows = (source.scheduler_config or {}).get("allowed_windows", [])
    if source.monitor_mode == "window" and not allowed_windows:
        raise SearchSourceConfigError("allowed_windows is required for window monitor mode")
