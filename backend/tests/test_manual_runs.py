from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from vinted_monitor.api.main import app, get_manual_run_provider
from vinted_monitor.db.models import ErrorLog, Item, Run, SearchSource, SourceSeenItem
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.services.runs import (
    FAILED,
    GLOBAL_KNOWN_ID_CACHE,
    MANUAL_TRIGGER,
    SCHEDULER_TRIGGER,
    SOURCE_SEEN_ID_CACHE,
    SUCCESS,
    SearchSourceInactiveError,
    SearchSourceNotFoundError,
    execute_manual_run,
    execute_source_run,
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


class FakeDetailProvider(FakeSuccessProvider):
    def __init__(self, item_count: int = 2) -> None:
        super().__init__(item_count=item_count)
        self.detail_calls: list[str] = []

    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        self.detail_calls.append(candidate.vinted_item_id)
        return CatalogItemDetail(
            vinted_item_id=candidate.vinted_item_id,
            description=f"Detalle {candidate.vinted_item_id}",
            color="Azul",
            category="Polos",
            photos=[f"https://images.example.test/{candidate.vinted_item_id}-detail.webp"],
        )


class FakeLimitedDetailProvider(FakeDetailProvider):
    settings = SimpleNamespace(vinted_detail_max_candidates_per_run=1, vinted_detail_concurrency=1)


class FakeFailingDetailProvider(FakeSuccessProvider):
    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        raise RuntimeError(f"detail boom {candidate.vinted_item_id}")


class FakeFailingProvider:
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        raise RuntimeError("provider boom")


class FakeSecretFailingProvider:
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        raise RuntimeError("provider boom access_token_web=secret-token Authorization: Bearer bearer-secret csrf_token=csrf-secret")


class FakeSecretFailingDetailProvider(FakeSuccessProvider):
    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        raise RuntimeError("detail boom cookie=session-secret refresh_token=refresh-secret")


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
    SOURCE_SEEN_ID_CACHE.clear()
    GLOBAL_KNOWN_ID_CACHE.clear()
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
        SOURCE_SEEN_ID_CACHE.clear()
        GLOBAL_KNOWN_ID_CACHE.clear()


def cleanup_source(source_id: int) -> None:
    with SessionLocal() as db:
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id == source_id)))
        pytest_item_ids = list(db.scalars(select(Item.id).where(Item.vinted_item_id.like("pytest-run-item-%"))))
        if pytest_item_ids:
            db.query(SourceSeenItem).filter(SourceSeenItem.item_id.in_(pytest_item_ids)).delete(synchronize_session=False)
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
        assert run.trigger == MANUAL_TRIGGER
        assert run.error_message is None

    assert count_items() == before_items + 2

    with SessionLocal() as db:
        item_id = db.scalar(select(Item.id).where(Item.vinted_item_id == "pytest-run-item-0"))
        assert item_id is not None
        assert GLOBAL_KNOWN_ID_CACHE["pytest-run-item-0"] == item_id
        assert SOURCE_SEEN_ID_CACHE[source_id]["pytest-run-item-0"] == item_id
        seen_count = db.scalar(select(func.count()).select_from(SourceSeenItem).where(SourceSeenItem.source_id == source_id))
        assert seen_count == 2


def test_execute_manual_run_updates_existing_items_without_counting_them_new(source_id: int) -> None:
    with SessionLocal() as db:
        first_run = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=2))
        second_run = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=2))

        assert first_run.items_found == 2
        assert first_run.items_new == 2
        assert second_run.items_found == 2
        assert second_run.items_new == 0
        assert db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item-%"))) == 2


def test_execute_manual_run_fetches_detail_only_for_globally_new_items(source_id: int) -> None:
    provider = FakeDetailProvider(item_count=2)

    with SessionLocal() as db:
        first_run = execute_manual_run(db, source_id, provider=provider)
        second_run = execute_manual_run(db, source_id, provider=provider)

        item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))

        assert first_run.items_new == 2
        assert second_run.items_new == 0
        assert provider.detail_calls == ["pytest-run-item-0", "pytest-run-item-1"]
        assert item is not None
        assert item.description == "Detalle pytest-run-item-0"
        assert item.photos == ["https://images.example.test/pytest-run-item-0-detail.webp"]


def test_execute_manual_run_does_not_count_same_item_new_for_different_source(source_id: int) -> None:
    with SessionLocal() as db:
        second_source = SearchSource(
            name="pytest second source",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            scheduler_config={},
        )
        db.add(second_source)
        db.commit()
        db.refresh(second_source)
        second_source_id = second_source.id

    try:
        provider = FakeDetailProvider(item_count=1)
        with SessionLocal() as db:
            first_run = execute_manual_run(db, source_id, provider=provider)
            second_run = execute_manual_run(db, second_source_id, provider=provider)
            seen_sources = list(
                db.scalars(
                    select(SourceSeenItem.source_id)
                    .join(Item, Item.id == SourceSeenItem.item_id)
                    .where(Item.vinted_item_id == "pytest-run-item-0")
                    .order_by(SourceSeenItem.source_id)
                )
            )

            assert first_run.items_new == 1
            assert second_run.items_new == 0
            assert provider.detail_calls == ["pytest-run-item-0"]
            assert seen_sources == sorted([source_id, second_source_id])
    finally:
        cleanup_source(second_source_id)


def test_execute_manual_run_updates_source_seen_without_changing_first_run(source_id: int) -> None:
    with SessionLocal() as db:
        first_run = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=1))
        first_seen = db.scalar(
            select(SourceSeenItem)
            .join(Item, Item.id == SourceSeenItem.item_id)
            .where(SourceSeenItem.source_id == source_id, Item.vinted_item_id == "pytest-run-item-0")
        )
        assert first_seen is not None
        first_seen_at = first_seen.first_seen_at

        second_run = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=1))
        seen_records = list(
            db.scalars(
                select(SourceSeenItem)
                .join(Item, Item.id == SourceSeenItem.item_id)
                .where(SourceSeenItem.source_id == source_id, Item.vinted_item_id == "pytest-run-item-0")
            )
        )

        assert len(seen_records) == 1
        assert seen_records[0].first_run_id == first_run.id
        assert seen_records[0].last_run_id == second_run.id
        assert seen_records[0].first_seen_at == first_seen_at
        assert second_run.items_new == 0


def test_execute_manual_run_detail_fetch_respects_candidate_limit(source_id: int) -> None:
    provider = FakeLimitedDetailProvider(item_count=3)

    with SessionLocal() as db:
        run = execute_manual_run(db, source_id, provider=provider)

        assert run.items_new == 3
        assert provider.detail_calls == ["pytest-run-item-0"]


def test_execute_source_run_records_scheduler_trigger(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_source_run(db, source_id, provider=FakeSuccessProvider(item_count=1), trigger=SCHEDULER_TRIGGER)

        assert run.status == SUCCESS
        assert run.trigger == SCHEDULER_TRIGGER


def test_execute_manual_run_records_detail_failure_without_failing_run(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_manual_run(db, source_id, provider=FakeFailingDetailProvider(item_count=1))

        item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))
        error = db.scalar(select(ErrorLog).where(ErrorLog.run_id == run.id, ErrorLog.kind == "RuntimeError"))

        assert run.status == SUCCESS
        assert run.items_new == 1
        assert item is not None
        assert item.detail_error == "detail boom pytest-run-item-0"
        assert error is not None
        assert error.details["stage"] == "item_detail"


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


def test_execute_manual_run_redacts_secret_values_from_provider_errors(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_manual_run(db, source_id, provider=FakeSecretFailingProvider())
        error = db.scalar(select(ErrorLog).where(ErrorLog.run_id == run.id))

        assert run.status == FAILED
        assert run.error_message is not None
        assert error is not None
        assert "secret-token" not in run.error_message
        assert "bearer-secret" not in run.error_message
        assert "csrf-secret" not in run.error_message
        assert "secret-token" not in error.message
        assert "bearer-secret" not in error.message
        assert "csrf-secret" not in error.message
        assert "<redacted>" in error.message


def test_execute_manual_run_redacts_secret_values_from_detail_errors(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_manual_run(db, source_id, provider=FakeSecretFailingDetailProvider(item_count=1))
        item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))
        error = db.scalar(select(ErrorLog).where(ErrorLog.run_id == run.id, ErrorLog.kind == "RuntimeError"))

        assert run.status == SUCCESS
        assert item is not None
        assert error is not None
        assert item.detail_error is not None
        assert "session-secret" not in item.detail_error
        assert "refresh-secret" not in item.detail_error
        assert "session-secret" not in error.message
        assert "refresh-secret" not in error.message
        assert "<redacted>" in item.detail_error


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
        assert "pytest-run-item-invalid" not in GLOBAL_KNOWN_ID_CACHE
        assert "pytest-run-item-invalid" not in SOURCE_SEEN_ID_CACHE.get(source_id, {})


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
        assert body["trigger"] == MANUAL_TRIGGER
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
