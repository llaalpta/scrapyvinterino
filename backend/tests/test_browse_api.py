from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from vinted_monitor.api.main import app
from vinted_monitor.db.models import Item, Opportunity, Run, RunEvent, SearchSource
from vinted_monitor.db.session import SessionLocal

PREFIX = "pytest-browser-"
SEED_NOW = datetime(2090, 1, 1, 10, 0, tzinfo=UTC)


def cleanup_browser_data() -> None:
    with SessionLocal() as db:
        item_ids = list(db.scalars(select(Item.id).where(Item.vinted_item_id.like(f"{PREFIX}%"))))
        source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.name.like(f"{PREFIX}%"))))
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id.in_(source_ids)))) if source_ids else []
        if item_ids:
            db.query(Opportunity).filter(Opportunity.item_id.in_(item_ids)).delete(synchronize_session=False)
            db.query(Item).filter(Item.id.in_(item_ids)).delete(synchronize_session=False)
        if run_ids:
            db.query(RunEvent).filter(RunEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
        if source_ids:
            db.query(RunEvent).filter(RunEvent.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(SearchSource).filter(SearchSource.id.in_(source_ids)).delete(synchronize_session=False)
        db.commit()


def seed_browser_data() -> dict[str, int]:
    cleanup_browser_data()
    with SessionLocal() as db:
        source_a = SearchSource(
            name=f"{PREFIX}source-a",
            url="https://www.vinted.es/catalog?search_text=a",
            normalized_query={"search_text": ["a"]},
            scheduler_config={},
        )
        source_b = SearchSource(
            name=f"{PREFIX}source-b",
            url="https://www.vinted.es/catalog?search_text=b",
            normalized_query={"search_text": ["b"]},
            scheduler_config={},
        )
        db.add_all([source_a, source_b])
        db.flush()

        run_a = Run(source_id=source_a.id, status="success", trigger="manual", items_found=1, items_new=1, opportunities_created=1)
        run_b = Run(source_id=source_b.id, status="success", trigger="manual", items_found=1, items_new=1, opportunities_created=1)
        db.add_all([run_a, run_b])
        db.flush()

        item_a = build_item("source-a", "Source A item", Decimal("9.50"))
        item_b = build_item("source-b", "Source B item", Decimal("2.00"))
        db.add_all([item_a, item_b])
        db.flush()

        db.add_all(
            [
                Opportunity(
                    source_id=source_a.id,
                    item_id=item_a.id,
                    status="new",
                    evaluation_status="passed_without_filters",
                    filter_snapshot=[],
                    last_scraped_at=SEED_NOW - timedelta(hours=2),
                    last_run_id=run_a.id,
                ),
                Opportunity(
                    source_id=source_b.id,
                    item_id=item_b.id,
                    status="new",
                    evaluation_status="passed",
                    filter_snapshot=[],
                    last_scraped_at=SEED_NOW,
                    last_run_id=run_b.id,
                ),
            ]
        )
        db.commit()

        return {"source_a": source_a.id, "source_b": source_b.id}


def build_item(suffix: str, title: str, price: Decimal) -> Item:
    return Item(
        vinted_item_id=f"{PREFIX}{suffix}",
        title=title,
        brand="Pytest Brand",
        price_amount=price,
        currency="EUR",
        size="M",
        status="Muy bueno",
        seller_login="pytest_seller",
        seller_country=None,
        favorite_count=1,
        view_count=11,
        url=f"https://www.vinted.es/items/{PREFIX}{suffix}",
        image_url=f"https://images1.vinted.net/{PREFIX}{suffix}/thumb.webp?s=signed",
        description="Detalle publico de prueba",
        shipping_price_amount=Decimal("1.75"),
        buyer_protection_fee_amount=Decimal("0.80"),
        total_price_amount=price + Decimal("0.80"),
        photos=[
            f"https://images1.vinted.net/{PREFIX}{suffix}/f800/1.webp?s=signed-1",
            f"https://images1.vinted.net/{PREFIX}{suffix}/f800/2.webp?s=signed-2",
        ],
        seller_badges=[],
        availability_flags={"state": "reserved", "reason_codes": ["reserved"], "source": "public_snapshot"},
        detail_raw={},
        raw={},
    )


def test_items_api_is_removed() -> None:
    client = TestClient(app)

    response = client.get("/api/items")

    assert response.status_code == 404


def test_opportunities_api_returns_paginated_opportunities() -> None:
    ids = seed_browser_data()
    client = TestClient(app)
    try:
        response = client.get("/api/opportunities?page=1&page_size=25", params={"source_id": ids["source_b"]})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["source_name"] == f"{PREFIX}source-b"
        assert body["items"][0]["item"]["vinted_item_id"] == f"{PREFIX}source-b"
        assert body["items"][0]["item"]["photos"] == [
            f"https://images1.vinted.net/{PREFIX}source-b/f800/1.webp?s=signed-1",
            f"https://images1.vinted.net/{PREFIX}source-b/f800/2.webp?s=signed-2",
        ]
        assert body["items"][0]["item"]["shipping_price_amount"] == "1.75"
        assert body["items"][0]["item"]["buyer_protection_fee_amount"] == "0.80"
        assert body["items"][0]["item"]["view_count"] == 11
        assert body["items"][0]["item"]["availability_flags"]["state"] == "reserved"
        assert body["items"][0]["last_scraped_at"] == SEED_NOW.isoformat().replace("+00:00", "Z")
        assert body["items"][0]["last_run_id"] is not None
    finally:
        cleanup_browser_data()


def test_opportunities_api_filters_by_date_price_and_status() -> None:
    seed_browser_data()
    client = TestClient(app)
    try:
        response = client.get(
            "/api/opportunities",
            params={
                "scraped_from": "2090-01-01T09:00:00+00:00",
                "scraped_to": "2090-01-01T11:00:00+00:00",
                "price_min": "1.50",
                "price_max": "4.50",
                "evaluation_status": "passed",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert [entry["item"]["vinted_item_id"] for entry in body["items"]] == [f"{PREFIX}source-b"]
    finally:
        cleanup_browser_data()


def test_opportunities_api_rejects_invalid_ranges() -> None:
    client = TestClient(app)

    response = client.get("/api/opportunities?price_min=10&price_max=1")

    assert response.status_code == 422
