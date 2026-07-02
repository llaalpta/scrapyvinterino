from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from vinted_monitor.api.main import app, get_manual_run_provider
from vinted_monitor.db.models import ErrorLog, Item, Run, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogSearchResult, CatalogSource
from vinted_monitor.services.runs import (
    FAILED,
    SUCCESS,
    SearchSourceInactiveError,
    SearchSourceNotFoundError,
    execute_manual_run,
    list_runs,
)


class FakeSuccessProvider:
    def __init__(self, item_count: int = 2) -> None:
        self.item_count = item_count

    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        return CatalogSearchResult(
            items=[
                CatalogItemCandidate(
                    vinted_item_id=f"pytest-run-item-{index}",
                    title=f"Pytest item {index}",
                    brand="Pytest Brand",
                    price_amount=Decimal("3.50"),
                    currency="EUR",
                    size="M",
                    status="Muy bueno",
                    seller_login="pytest_seller",
                    seller_country=None,
                    favorite_count=1,
                    url=f"https://www.vinted.es/items/pytest-run-item-{index}",
                    image_url=None,
                    raw={"source_url": source.url, "page": page},
                )
                for index in range(self.item_count)
            ],
            page=1,
            total_pages=1,
            total_entries=self.item_count,
            per_page=self.item_count,
            next_page=None,
            provider_metadata={"provider": "fake"},
        )


class FakeFailingProvider:
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        raise RuntimeError("provider boom")


class FakeInvalidItemProvider:
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        return CatalogSearchResult(
            items=[
                CatalogItemCandidate(
                    vinted_item_id="pytest-run-item-invalid",
                    title=None,  # type: ignore[arg-type]
                    brand=None,
                    price_amount=None,
                    currency=None,
                    size=None,
                    status=None,
                    seller_login=None,
                    seller_country=None,
                    favorite_count=None,
                    url="https://www.vinted.es/items/pytest-run-item-invalid",
                    image_url=None,
                    raw={"id": "pytest-run-item-invalid"},
                )
            ],
            page=1,
            total_pages=1,
            total_entries=1,
            per_page=1,
            next_page=None,
            provider_metadata={"provider": "fake-invalid"},
        )


@pytest.fixture
def source_id() -> int:
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest manual run source",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        created_id = source.id

    try:
        yield created_id
    finally:
        cleanup_source(created_id)


def cleanup_source(source_id: int) -> None:
    with SessionLocal() as db:
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id == source_id)))
        if run_ids:
            db.query(ErrorLog).filter(ErrorLog.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
        db.query(ErrorLog).filter(ErrorLog.source_id == source_id).delete(synchronize_session=False)
        db.query(Item).filter(Item.vinted_item_id.like("pytest-run-item-%")).delete(synchronize_session=False)
        source = db.get(SearchSource, source_id)
        if source is not None:
            db.delete(source)
        db.commit()


def count_items() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Item)) or 0


def test_execute_manual_run_records_success_and_persists_items(source_id: int) -> None:
    before_items = count_items()

    with SessionLocal() as db:
        run = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=2))

        assert run.status == SUCCESS
        assert run.finished_at is not None
        assert run.items_found == 2
        assert run.items_new == 2
        assert run.opportunities_created == 0
        assert run.error_message is None

    assert count_items() == before_items + 2


def test_execute_manual_run_updates_existing_items_without_counting_them_new(source_id: int) -> None:
    with SessionLocal() as db:
        first_run = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=2))
        second_run = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=2))

        assert first_run.items_found == 2
        assert first_run.items_new == 2
        assert second_run.items_found == 2
        assert second_run.items_new == 0
        assert db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item-%"))) == 2


def test_execute_manual_run_records_provider_failure(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_manual_run(db, source_id, provider=FakeFailingProvider())

        assert run.status == FAILED
        assert run.finished_at is not None
        assert run.items_found == 0
        assert run.error_message == "provider boom"

        error = db.scalar(select(ErrorLog).where(ErrorLog.run_id == run.id))
        assert error is not None
        assert error.source_id == source_id
        assert error.kind == "RuntimeError"
        assert error.message == "provider boom"


def test_execute_manual_run_records_persistence_failure_without_partial_items(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_manual_run(db, source_id, provider=FakeInvalidItemProvider())

        assert run.status == FAILED
        assert run.finished_at is not None
        assert run.error_message
        assert db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id == "pytest-run-item-invalid")) == 0

        error = db.scalar(select(ErrorLog).where(ErrorLog.run_id == run.id))
        assert error is not None
        assert error.kind == "IntegrityError"


def test_execute_manual_run_rejects_missing_source() -> None:
    with SessionLocal() as db:
        with pytest.raises(SearchSourceNotFoundError):
            execute_manual_run(db, 999_999_999, provider=FakeSuccessProvider())


def test_execute_manual_run_rejects_inactive_source(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = False
        db.commit()

        with pytest.raises(SearchSourceInactiveError):
            execute_manual_run(db, source_id, provider=FakeSuccessProvider())


def test_list_runs_returns_recent_runs_first(source_id: int) -> None:
    with SessionLocal() as db:
        first = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=1))
        second = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=2))

        runs = list_runs(db, limit=2)

        assert [run.id for run in runs] == [second.id, first.id]


def test_manual_run_api_creates_run_with_injected_provider(source_id: int) -> None:
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=3)
    client = TestClient(app)

    try:
        response = client.post(f"/api/sources/{source_id}/runs")

        assert response.status_code == 201
        body = response.json()
        assert body["source_id"] == source_id
        assert body["status"] == SUCCESS
        assert body["items_found"] == 3
        assert body["items_new"] == 3

        list_response = client.get("/api/runs?limit=1")
        assert list_response.status_code == 200
        assert list_response.json()[0]["id"] == body["id"]

        items_response = client.get("/api/items")
        assert items_response.status_code == 200
        items = items_response.json()
        persisted_item = next(item for item in items if item["vinted_item_id"] == "pytest-run-item-0")
        assert persisted_item["title"] == "Pytest item 0"
        assert persisted_item["last_seen_at"]
    finally:
        app.dependency_overrides.clear()


def test_manual_run_api_returns_400_for_inactive_source(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = False
        db.commit()

    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider()
    client = TestClient(app)

    try:
        response = client.post(f"/api/sources/{source_id}/runs")

        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()
