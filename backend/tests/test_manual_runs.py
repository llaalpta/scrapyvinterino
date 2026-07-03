from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from vinted_monitor.api.main import app, get_manual_run_provider
from vinted_monitor.db.models import (
    ErrorLog,
    FilterRule,
    Item,
    MonitorSession,
    Opportunity,
    ProxyProfile,
    Run,
    RunEvent,
    SearchSource,
    SessionItemState,
    SourceSeenItem,
)
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.services.filters import create_filter_rule
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
from vinted_monitor.services.sessions import start_monitor_session


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
    cleanup_source(None)
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


def cleanup_source(source_id: int | None) -> None:
    with SessionLocal() as db:
        source_ids = (
            [source_id]
            if source_id is not None
            else list(db.scalars(select(SearchSource.id).where(SearchSource.name.like("pytest%"))))
        )
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id.in_(source_ids)))) if source_ids else []
        session_ids = list(db.scalars(select(MonitorSession.id).where(MonitorSession.source_id.in_(source_ids)))) if source_ids else []
        pytest_item_ids = list(db.scalars(select(Item.id).where(Item.vinted_item_id.like("pytest-run-item-%"))))
        if session_ids:
            db.query(RunEvent).filter(RunEvent.session_id.in_(session_ids)).delete(synchronize_session=False)
            db.query(SessionItemState).filter(SessionItemState.session_id.in_(session_ids)).delete(synchronize_session=False)
        if pytest_item_ids:
            db.query(SessionItemState).filter(SessionItemState.item_id.in_(pytest_item_ids)).delete(synchronize_session=False)
            db.query(Opportunity).filter(Opportunity.item_id.in_(pytest_item_ids)).delete(synchronize_session=False)
            db.query(SourceSeenItem).filter(SourceSeenItem.item_id.in_(pytest_item_ids)).delete(synchronize_session=False)
        if run_ids:
            db.query(RunEvent).filter(RunEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(ErrorLog).filter(ErrorLog.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
        if source_ids:
            db.query(RunEvent).filter(RunEvent.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(ErrorLog).filter(ErrorLog.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(MonitorSession).filter(MonitorSession.source_id.in_(source_ids)).delete(synchronize_session=False)
        db.query(FilterRule).filter(FilterRule.name.like("pytest%")).delete(synchronize_session=False)
        db.query(ProxyProfile).filter(ProxyProfile.name.like("pytest%")).delete(synchronize_session=False)
        db.query(Item).filter(Item.vinted_item_id.like("pytest-run-item-%")).delete(synchronize_session=False)
        if source_id is not None:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
        elif source_ids:
            db.query(SearchSource).filter(SearchSource.id.in_(source_ids)).delete(synchronize_session=False)
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


def test_session_run_creates_opportunities_without_filters_and_skips_reprocessing(source_id: int) -> None:
    with SessionLocal() as db:
        session = start_monitor_session(db, source_id=source_id, filter_rule_ids=[])
        first_run = execute_source_run(db, source_id, provider=FakeSuccessProvider(item_count=2), session=session)
        second_run = execute_source_run(db, source_id, provider=FakeSuccessProvider(item_count=2), session=session)
        opportunities = list(db.scalars(select(Opportunity).where(Opportunity.session_id == session.id)))
        states = list(db.scalars(select(SessionItemState).where(SessionItemState.session_id == session.id)))

        assert first_run.items_filter_passed == 2
        assert first_run.items_discarded_by_filters == 0
        assert first_run.opportunities_created == 2
        assert second_run.items_filter_passed == 0
        assert second_run.opportunities_created == 0
        assert len(opportunities) == 2
        assert {opportunity.evaluation_status for opportunity in opportunities} == {"passed_without_filters"}
        assert len(states) == 2


def test_known_global_item_can_create_opportunity_in_different_session(source_id: int) -> None:
    with SessionLocal() as db:
        second_source = SearchSource(
            name="pytest second session source",
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
        with SessionLocal() as db:
            first_session = start_monitor_session(db, source_id=source_id, filter_rule_ids=[])
            second_session = start_monitor_session(db, source_id=second_source_id, filter_rule_ids=[])
            first_run = execute_source_run(db, source_id, provider=FakeSuccessProvider(item_count=1), session=first_session)
            second_run = execute_source_run(db, second_source_id, provider=FakeSuccessProvider(item_count=1), session=second_session)
            opportunity_sessions = list(
                db.scalars(
                    select(Opportunity.session_id)
                    .join(Item, Item.id == Opportunity.item_id)
                    .where(Item.vinted_item_id == "pytest-run-item-0")
                    .order_by(Opportunity.session_id)
                )
            )

            assert first_run.items_new == 1
            assert second_run.items_new == 0
            assert opportunity_sessions == sorted([first_session.id, second_session.id])
    finally:
        cleanup_source(second_source_id)


def test_session_run_discards_items_matching_exclusion_filter(source_id: int) -> None:
    with SessionLocal() as db:
        rule = create_filter_rule(db, name="pytest blacklist detail", definition={"blacklist_terms": ["detalle"]})
        session = start_monitor_session(db, source_id=source_id, filter_rule_ids=[rule.id])
        run = execute_source_run(db, source_id, provider=FakeDetailProvider(item_count=1), session=session)
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.session_id == session.id))
        state = db.scalar(select(SessionItemState).where(SessionItemState.session_id == session.id))

        assert run.items_filter_passed == 0
        assert run.items_discarded_by_filters == 1
        assert run.opportunities_created == 0
        assert opportunity_count == 0
        assert state is not None
        assert state.status == "discarded"


def test_session_run_creates_opportunity_when_detail_fetch_fails(source_id: int) -> None:
    with SessionLocal() as db:
        rule = create_filter_rule(db, name="pytest blacklist unused", definition={"blacklist_terms": ["unused"]})
        session = start_monitor_session(db, source_id=source_id, filter_rule_ids=[rule.id])
        run = execute_source_run(db, source_id, provider=FakeFailingDetailProvider(item_count=1), session=session)
        opportunity = db.scalar(select(Opportunity).where(Opportunity.session_id == session.id))
        state = db.scalar(select(SessionItemState).where(SessionItemState.session_id == session.id))

        assert run.items_filter_passed == 1
        assert run.items_filter_pending == 1
        assert run.opportunities_created == 1
        assert opportunity is not None
        assert opportunity.evaluation_status == "detail_error"
        assert state is not None
        assert state.status == "detail_error"


def test_session_run_creates_opportunity_when_detail_limit_is_reached(source_id: int) -> None:
    with SessionLocal() as db:
        rule = create_filter_rule(db, name="pytest blacklist none", definition={"blacklist_terms": ["unused"]})
        session = start_monitor_session(db, source_id=source_id, filter_rule_ids=[rule.id])
        run = execute_source_run(db, source_id, provider=FakeLimitedDetailProvider(item_count=2), session=session)
        statuses = set(db.scalars(select(Opportunity.evaluation_status).where(Opportunity.session_id == session.id)))

        assert run.items_filter_passed == 2
        assert run.items_filter_pending == 1
        assert run.opportunities_created == 2
        assert statuses == {"passed", "passed_without_detail"}


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
        items = items_response.json()["items"]
        persisted_item = next(item for item in items if item["vinted_item_id"] == "pytest-run-item-0")
        assert persisted_item["title"] == "Pytest item 0"
        assert persisted_item["last_seen_at"]
        assert persisted_item["last_scraped_source_name"] == "pytest manual run source"
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


def test_proxy_profile_api_does_not_return_or_store_raw_password() -> None:
    cleanup_source(None)
    client = TestClient(app)

    try:
        response = client.post(
            "/api/proxy-profiles",
            json={
                "name": "pytest proxy profile",
                "scheme": "http",
                "host": "proxy.example.test",
                "port": 8080,
                "username": "pytest-user",
                "password": "pytest-secret-password",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert "pytest-secret-password" not in str(body)
        assert body["has_password"] is True
        assert body["username_masked"] != "pytest-user"

        with SessionLocal() as db:
            profile = db.scalar(select(ProxyProfile).where(ProxyProfile.name == "pytest proxy profile"))
            assert profile is not None
            assert profile.password_encrypted is not None
            assert "pytest-secret-password" not in profile.password_encrypted
    finally:
        cleanup_source(None)
