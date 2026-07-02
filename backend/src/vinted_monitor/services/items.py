from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import Item
from vinted_monitor.providers.catalog import CatalogItemCandidate


@dataclass(frozen=True)
class ItemPersistenceResult:
    found_count: int
    inserted_count: int
    updated_count: int


def list_items(db: Session, limit: int = 100) -> list[Item]:
    statement = select(Item).order_by(Item.last_seen_at.desc(), Item.id.desc()).limit(limit)
    return list(db.scalars(statement))


def persist_catalog_items(db: Session, candidates: list[CatalogItemCandidate]) -> ItemPersistenceResult:
    unique_candidates = _deduplicate_candidates(candidates)
    if not unique_candidates:
        return ItemPersistenceResult(found_count=0, inserted_count=0, updated_count=0)

    existing_items = {
        item.vinted_item_id: item
        for item in db.scalars(select(Item).where(Item.vinted_item_id.in_(unique_candidates.keys())))
    }
    now = datetime.now(UTC)
    inserted_count = 0
    updated_count = 0

    for candidate in unique_candidates.values():
        existing = existing_items.get(candidate.vinted_item_id)
        if existing is None:
            db.add(_build_item(candidate, now))
            inserted_count += 1
        else:
            _update_item(existing, candidate, now)
            updated_count += 1

    db.flush()
    return ItemPersistenceResult(
        found_count=len(candidates),
        inserted_count=inserted_count,
        updated_count=updated_count,
    )


def _deduplicate_candidates(candidates: list[CatalogItemCandidate]) -> dict[str, CatalogItemCandidate]:
    unique_candidates: dict[str, CatalogItemCandidate] = {}
    for candidate in candidates:
        unique_candidates[candidate.vinted_item_id] = candidate
    return unique_candidates


def _build_item(candidate: CatalogItemCandidate, now: datetime) -> Item:
    return Item(
        vinted_item_id=candidate.vinted_item_id,
        title=candidate.title,
        brand=candidate.brand,
        price_amount=candidate.price_amount,
        currency=candidate.currency,
        size=candidate.size,
        status=candidate.status,
        seller_login=candidate.seller_login,
        seller_country=candidate.seller_country,
        favorite_count=candidate.favorite_count,
        url=candidate.url,
        image_url=candidate.image_url,
        raw=candidate.raw,
        first_seen_at=now,
        last_seen_at=now,
    )


def _update_item(item: Item, candidate: CatalogItemCandidate, now: datetime) -> None:
    item.title = candidate.title
    item.brand = candidate.brand
    item.price_amount = candidate.price_amount
    item.currency = candidate.currency
    item.size = candidate.size
    item.status = candidate.status
    item.seller_login = candidate.seller_login
    item.seller_country = candidate.seller_country
    item.favorite_count = candidate.favorite_count
    item.url = candidate.url
    item.image_url = candidate.image_url
    item.raw = candidate.raw
    item.last_seen_at = now
