from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from vinted_monitor.db.models import Item
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail


@dataclass(frozen=True)
class ItemPersistenceResult:
    found_count: int
    inserted_count: int
    updated_count: int
    inserted_vinted_item_ids: list[str]


def list_items(db: Session, limit: int = 100) -> list[Item]:
    statement = select(Item).order_by(Item.last_seen_at.desc(), Item.id.desc()).limit(limit)
    return list(db.scalars(statement))


def persist_catalog_items(db: Session, candidates: list[CatalogItemCandidate]) -> ItemPersistenceResult:
    unique_candidates = _deduplicate_candidates(candidates)
    if not unique_candidates:
        return ItemPersistenceResult(found_count=0, inserted_count=0, updated_count=0, inserted_vinted_item_ids=[])

    now = datetime.now(UTC)
    insert_rows = [_item_insert_values(candidate, now) for candidate in unique_candidates.values()]
    insert_statement = (
        pg_insert(Item)
        .values(insert_rows)
        .on_conflict_do_nothing(index_elements=[Item.vinted_item_id])
        .returning(Item.vinted_item_id)
    )
    inserted_vinted_item_ids = list(db.scalars(insert_statement))
    inserted_id_set = set(inserted_vinted_item_ids)

    existing_items = {
        item.vinted_item_id: item
        for item in db.scalars(select(Item).where(Item.vinted_item_id.in_(list(unique_candidates.keys()))))
    }

    for candidate in unique_candidates.values():
        if candidate.vinted_item_id not in inserted_id_set:
            existing = existing_items[candidate.vinted_item_id]
            _update_item(existing, candidate, now)

    db.flush()
    return ItemPersistenceResult(
        found_count=len(candidates),
        inserted_count=len(inserted_vinted_item_ids),
        updated_count=len(unique_candidates) - len(inserted_vinted_item_ids),
        inserted_vinted_item_ids=inserted_vinted_item_ids,
    )


def get_items_by_vinted_ids(db: Session, vinted_item_ids: list[str]) -> dict[str, Item]:
    if not vinted_item_ids:
        return {}
    return {
        item.vinted_item_id: item
        for item in db.scalars(select(Item).where(Item.vinted_item_id.in_(list(dict.fromkeys(vinted_item_ids)))))
    }


def get_or_persist_catalog_item(db: Session, candidate: CatalogItemCandidate) -> Item:
    existing = db.scalar(select(Item).where(Item.vinted_item_id == candidate.vinted_item_id))
    now = datetime.now(UTC)
    if existing is not None:
        _update_item(existing, candidate, now)
        db.flush()
        return existing

    item = Item(**_item_insert_values(candidate, now))
    db.add(item)
    db.flush()
    return item


def build_transient_catalog_item(candidate: CatalogItemCandidate) -> Item:
    return Item(**_item_insert_values(candidate, datetime.now(UTC)))


def apply_item_detail(db: Session, item: Item, detail: CatalogItemDetail) -> None:
    now = datetime.now(UTC)
    apply_item_detail_data(item, detail, now)
    db.flush()


def apply_item_detail_data(item: Item, detail: CatalogItemDetail, now: datetime | None = None) -> None:
    resolved_now = now or datetime.now(UTC)
    item.description = detail.description
    item.color = detail.color
    item.category = detail.category
    item.shipping_price_amount = detail.shipping_price_amount
    item.buyer_protection_fee_amount = detail.buyer_protection_fee_amount
    item.total_price_amount = detail.total_price_amount
    item.photos = detail.photos
    item.seller_rating = detail.seller_rating
    item.seller_badges = detail.seller_badges
    item.availability_flags = detail.availability_flags
    item.detail_raw = detail.raw
    item.detail_last_fetched_at = resolved_now
    item.detail_error = None
    item.last_seen_at = resolved_now


def _deduplicate_candidates(candidates: list[CatalogItemCandidate]) -> dict[str, CatalogItemCandidate]:
    unique_candidates: dict[str, CatalogItemCandidate] = {}
    for candidate in candidates:
        unique_candidates[candidate.vinted_item_id] = candidate
    return unique_candidates


def _item_insert_values(candidate: CatalogItemCandidate, now: datetime) -> dict:
    return {
        "vinted_item_id": candidate.vinted_item_id,
        "title": candidate.title,
        "brand": candidate.brand,
        "price_amount": candidate.price_amount,
        "currency": candidate.currency,
        "size": candidate.size,
        "status": candidate.status,
        "seller_login": candidate.seller_login,
        "seller_country": candidate.seller_country,
        "favorite_count": candidate.favorite_count,
        "url": candidate.url,
        "image_url": candidate.image_url,
        "photos": [],
        "seller_badges": [],
        "availability_flags": {},
        "detail_raw": {},
        "raw": candidate.raw,
        "first_seen_at": now,
        "last_seen_at": now,
    }


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
