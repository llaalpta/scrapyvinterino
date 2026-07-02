from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import SearchSource

ALLOWED_VINTED_CATALOG_HOSTS = {"www.vinted.es", "vinted.es"}
ALLOWED_VINTED_CATALOG_PATHS = {"/catalog", "/catalog/"}


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
    return list(db.scalars(select(SearchSource).order_by(SearchSource.id.desc())))
