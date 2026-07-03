from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from vinted_monitor.api.main import app
from vinted_monitor.db.models import (
    FilterRule,
    Item,
    MonitorSession,
    Opportunity,
    Run,
    RunEvent,
    SearchSource,
    SessionItemState,
    SourceSeenItem,
)
from vinted_monitor.db.session import SessionLocal

PREFIX = "pytest-browser-"
QA_PREFIX = "qa-browser-"
SEED_NOW = datetime(2090, 1, 1, 10, 0, tzinfo=UTC)
SEED_FROM = "2090-01-01T06:00:00+00:00"
SEED_TO = "2090-01-01T11:00:00+00:00"


def cleanup_browser_data() -> None:
    with SessionLocal() as db:
        item_ids = list(
            db.scalars(
                select(Item.id).where(
                    Item.vinted_item_id.like(f"{PREFIX}%") | Item.vinted_item_id.like(f"{QA_PREFIX}%")
                )
            )
        )
        source_ids = list(
            db.scalars(
                select(SearchSource.id).where(SearchSource.name.like(f"{PREFIX}%") | SearchSource.name.like(f"{QA_PREFIX}%"))
            )
        )
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id.in_(source_ids)))) if source_ids else []
        session_ids = list(db.scalars(select(MonitorSession.id).where(MonitorSession.source_id.in_(source_ids)))) if source_ids else []
        rule_ids = list(db.scalars(select(FilterRule.id).where(FilterRule.source_id.in_(source_ids)))) if source_ids else []

        if session_ids:
            db.query(RunEvent).filter(RunEvent.session_id.in_(session_ids)).delete(synchronize_session=False)
            db.query(SessionItemState).filter(SessionItemState.session_id.in_(session_ids)).delete(synchronize_session=False)
        if item_ids:
            db.query(SessionItemState).filter(SessionItemState.item_id.in_(item_ids)).delete(synchronize_session=False)
            db.query(SourceSeenItem).filter(SourceSeenItem.item_id.in_(item_ids)).delete(synchronize_session=False)
            db.query(Opportunity).filter(Opportunity.item_id.in_(item_ids)).delete(synchronize_session=False)
            db.query(Item).filter(Item.id.in_(item_ids)).delete(synchronize_session=False)
        if rule_ids:
            db.query(Opportunity).filter(Opportunity.rule_id.in_(rule_ids)).delete(synchronize_session=False)
            db.query(FilterRule).filter(FilterRule.id.in_(rule_ids)).delete(synchronize_session=False)
        if run_ids:
            db.query(RunEvent).filter(RunEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
        if session_ids:
            db.query(MonitorSession).filter(MonitorSession.id.in_(session_ids)).delete(synchronize_session=False)
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

        run_a = Run(source_id=source_a.id, status="success", trigger="manual", items_found=2, items_new=2, opportunities_created=0)
        run_b = Run(source_id=source_b.id, status="success", trigger="manual", items_found=2, items_new=0, opportunities_created=0)
        db.add_all([run_a, run_b])
        db.flush()

        item_shared = build_item("shared", "Shared item", Decimal("4.00"))
        item_a = build_item("source-a-only", "Source A item", Decimal("9.50"))
        item_b = build_item("source-b-only", "Source B item", Decimal("2.00"))
        db.add_all([item_shared, item_a, item_b])
        db.flush()

        db.add_all(
            [
                SourceSeenItem(
                    source_id=source_a.id,
                    item_id=item_shared.id,
                    first_run_id=run_a.id,
                    last_run_id=run_a.id,
                    first_seen_at=SEED_NOW - timedelta(hours=3),
                    last_seen_at=SEED_NOW - timedelta(hours=3),
                ),
                SourceSeenItem(
                    source_id=source_b.id,
                    item_id=item_shared.id,
                    first_run_id=run_b.id,
                    last_run_id=run_b.id,
                    first_seen_at=SEED_NOW - timedelta(hours=1),
                    last_seen_at=SEED_NOW - timedelta(hours=1),
                ),
                SourceSeenItem(
                    source_id=source_a.id,
                    item_id=item_a.id,
                    first_run_id=run_a.id,
                    last_run_id=run_a.id,
                    first_seen_at=SEED_NOW - timedelta(hours=2),
                    last_seen_at=SEED_NOW - timedelta(hours=2),
                ),
                SourceSeenItem(
                    source_id=source_b.id,
                    item_id=item_b.id,
                    first_run_id=run_b.id,
                    last_run_id=run_b.id,
                    first_seen_at=SEED_NOW,
                    last_seen_at=SEED_NOW,
                ),
            ]
        )

        rule = FilterRule(source_id=source_b.id, name=f"{PREFIX}rule", definition={}, is_active=True)
        db.add(rule)
        db.flush()
        opportunity = Opportunity(source_id=source_b.id, item_id=item_b.id, rule_id=rule.id, status="new")
        db.add(opportunity)
        db.commit()

        return {
            "source_a": source_a.id,
            "source_b": source_b.id,
        }


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
        url=f"https://www.vinted.es/items/{PREFIX}{suffix}",
        image_url=None,
        photos=[],
        seller_badges=[],
        availability_flags={},
        detail_raw={},
        raw={},
    )


def test_items_api_returns_paginated_global_items_with_latest_source() -> None:
    seed_browser_data()
    client = TestClient(app)
    try:
        response = client.get(
            "/api/items",
            params={"page": 1, "page_size": 2, "scraped_from": SEED_FROM, "scraped_to": SEED_TO},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert body["total_pages"] == 2
        assert [item["vinted_item_id"] for item in body["items"]] == [f"{PREFIX}source-b-only", f"{PREFIX}shared"]
        assert body["items"][1]["last_scraped_source_name"] == f"{PREFIX}source-b"
    finally:
        cleanup_browser_data()


def test_items_api_source_filter_keeps_global_dedupe_but_uses_filtered_source_metadata() -> None:
    ids = seed_browser_data()
    client = TestClient(app)
    try:
        response = client.get(f"/api/items?source_id={ids['source_a']}&page_size=25")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        shared = next(item for item in body["items"] if item["vinted_item_id"] == f"{PREFIX}shared")
        assert shared["last_scraped_source_id"] == ids["source_a"]
        assert shared["last_scraped_source_name"] == f"{PREFIX}source-a"
    finally:
        cleanup_browser_data()


def test_items_api_filters_by_scrape_range_and_price() -> None:
    seed_browser_data()
    client = TestClient(app)
    try:
        response = client.get(
            "/api/items",
            params={
                "scraped_from": "2090-01-01T08:30:00+00:00",
                "scraped_to": "2090-01-01T10:30:00+00:00",
                "price_min": "1.50",
                "price_max": "4.50",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert [item["vinted_item_id"] for item in body["items"]] == [f"{PREFIX}source-b-only", f"{PREFIX}shared"]
    finally:
        cleanup_browser_data()


def test_items_api_rejects_invalid_ranges() -> None:
    client = TestClient(app)

    response = client.get("/api/items?price_min=10&price_max=1")

    assert response.status_code == 422


def test_opportunities_api_returns_paginated_opportunities() -> None:
    seed_browser_data()
    client = TestClient(app)
    try:
        response = client.get("/api/opportunities?page=1&page_size=25")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["source_name"] == f"{PREFIX}source-b"
        assert body["items"][0]["item"]["vinted_item_id"] == f"{PREFIX}source-b-only"
    finally:
        cleanup_browser_data()
