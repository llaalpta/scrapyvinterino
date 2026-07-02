from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import SearchSource


def normalize_vinted_catalog_url(url: str) -> dict[str, list[str]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return {key: values for key, values in sorted(query.items())}


def create_source(db: Session, name: str, url: str) -> SearchSource:
    source = SearchSource(name=name, url=url, normalized_query=normalize_vinted_catalog_url(url))
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def list_sources(db: Session) -> list[SearchSource]:
    return list(db.scalars(select(SearchSource).order_by(SearchSource.id.desc())))
