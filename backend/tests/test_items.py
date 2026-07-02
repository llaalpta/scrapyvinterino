from decimal import Decimal

from sqlalchemy import select

from vinted_monitor.db.models import Item
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.catalog import CatalogItemCandidate
from vinted_monitor.services.items import persist_catalog_items


def build_candidate(
    vinted_item_id: str = "pytest-item-persistence-1",
    title: str = "Persisted pytest item",
    brand: str | None = "Pytest Brand",
    price_amount: Decimal | None = Decimal("4.25"),
    currency: str | None = "EUR",
    size: str | None = "M",
    status: str | None = "Muy bueno",
    seller_login: str | None = "pytest_seller",
    seller_country: str | None = None,
    favorite_count: int | None = 2,
    image_url: str | None = "https://images.example.test/item.webp",
) -> CatalogItemCandidate:
    return CatalogItemCandidate(
        vinted_item_id=vinted_item_id,
        title=title,
        brand=brand,
        price_amount=price_amount,
        currency=currency,
        size=size,
        status=status,
        seller_login=seller_login,
        seller_country=seller_country,
        favorite_count=favorite_count,
        url=f"https://www.vinted.es/items/{vinted_item_id}",
        image_url=image_url,
        raw={"id": vinted_item_id, "title": title, "safe": True},
    )


def cleanup_items() -> None:
    with SessionLocal() as db:
        db.query(Item).filter(Item.vinted_item_id.like("pytest-item-persistence-%")).delete(synchronize_session=False)
        db.commit()


def test_persist_catalog_items_inserts_new_item() -> None:
    cleanup_items()
    try:
        with SessionLocal() as db:
            result = persist_catalog_items(db, [build_candidate()])
            db.commit()

            item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-item-persistence-1"))

            assert result.found_count == 1
            assert result.inserted_count == 1
            assert result.updated_count == 0
            assert item is not None
            assert item.title == "Persisted pytest item"
            assert item.brand == "Pytest Brand"
            assert item.price_amount == Decimal("4.25")
            assert item.raw == {"id": "pytest-item-persistence-1", "title": "Persisted pytest item", "safe": True}
    finally:
        cleanup_items()


def test_persist_catalog_items_updates_existing_item_without_changing_identity() -> None:
    cleanup_items()
    try:
        with SessionLocal() as db:
            first_result = persist_catalog_items(db, [build_candidate()])
            db.commit()
            original = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-item-persistence-1"))
            assert original is not None
            original_id = original.id
            original_first_seen_at = original.first_seen_at
            original_last_seen_at = original.last_seen_at

            second_result = persist_catalog_items(
                db,
                [
                    build_candidate(
                        title="Updated pytest item",
                        price_amount=Decimal("3.99"),
                        favorite_count=5,
                    )
                ],
            )
            db.commit()
            updated = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-item-persistence-1"))

            assert first_result.inserted_count == 1
            assert second_result.inserted_count == 0
            assert second_result.updated_count == 1
            assert updated is not None
            assert updated.id == original_id
            assert updated.first_seen_at == original_first_seen_at
            assert updated.last_seen_at >= original_last_seen_at
            assert updated.title == "Updated pytest item"
            assert updated.price_amount == Decimal("3.99")
            assert updated.favorite_count == 5
    finally:
        cleanup_items()


def test_persist_catalog_items_allows_missing_optional_fields() -> None:
    cleanup_items()
    try:
        with SessionLocal() as db:
            result = persist_catalog_items(
                db,
                [
                    build_candidate(
                        vinted_item_id="pytest-item-persistence-missing",
                        brand=None,
                        price_amount=None,
                        currency=None,
                        size=None,
                        status=None,
                        seller_login=None,
                        seller_country=None,
                        favorite_count=None,
                        image_url=None,
                    )
                ],
            )
            db.commit()
            item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-item-persistence-missing"))

            assert result.inserted_count == 1
            assert item is not None
            assert item.brand is None
            assert item.price_amount is None
            assert item.image_url is None
    finally:
        cleanup_items()


def test_persist_catalog_items_deduplicates_candidates_in_one_batch() -> None:
    cleanup_items()
    try:
        with SessionLocal() as db:
            result = persist_catalog_items(
                db,
                [
                    build_candidate(vinted_item_id="pytest-item-persistence-duplicate", title="First title"),
                    build_candidate(vinted_item_id="pytest-item-persistence-duplicate", title="Last title"),
                ],
            )
            db.commit()
            items = list(db.scalars(select(Item).where(Item.vinted_item_id == "pytest-item-persistence-duplicate")))

            assert result.found_count == 2
            assert result.inserted_count == 1
            assert result.updated_count == 0
            assert len(items) == 1
            assert items[0].title == "Last title"
    finally:
        cleanup_items()
