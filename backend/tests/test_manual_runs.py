from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from vinted_monitor.api.main import app, get_manual_run_provider
from vinted_monitor.db.models import ErrorLog, FilterRule, Item, Opportunity, ProxyProfile, Run, RunEvent, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.services.filters import create_filter_rule
from vinted_monitor.services.runs import FAILED, SUCCESS, SearchSourceInactiveError, execute_manual_run, execute_monitor_run
from vinted_monitor.services.seen_cache import SeenCacheUnavailableError


class FakeSeenCache:
    def __init__(self, *, unavailable: bool = False, initially_seen: set[str] | None = None) -> None:
        self.unavailable = unavailable
        self.seen = set(initially_seen or set())
        self.processing: set[str] = set()
        self.marked_seen: list[str] = []

    def require_available(self) -> None:
        if self.unavailable:
            raise SeenCacheUnavailableError("Redis seen cache is unavailable")

    def claim_unseen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> set[str]:
        self.require_available()
        claimed = {item_id for item_id in vinted_item_ids if item_id not in self.seen and item_id not in self.processing}
        self.processing.update(claimed)
        return claimed

    def mark_seen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.require_available()
        self.seen.update(vinted_item_ids)
        self.marked_seen.extend(vinted_item_ids)
        self.processing.difference_update(vinted_item_ids)

    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.processing.difference_update(vinted_item_ids)


class FakeSuccessProvider:
    def __init__(self, item_count: int = 2, prefix: str = "pytest-run-item") -> None:
        self.item_count = item_count
        self.prefix = prefix
        self.detail_calls: list[str] = []

    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        return CatalogSearchResult(
            items=[
                CatalogItemCandidate(
                    vinted_item_id=f"{self.prefix}-{index}",
                    title=f"Pytest item {index}",
                    brand="Pytest Brand",
                    price_amount=Decimal("3.50"),
                    currency="EUR",
                    size="M",
                    status="Muy bueno",
                    seller_login="pytest_seller",
                    seller_country=None,
                    favorite_count=1,
                    url=f"https://www.vinted.es/items/{self.prefix}-{index}",
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

    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        self.detail_calls.append(candidate.vinted_item_id)
        return CatalogItemDetail(
            vinted_item_id=candidate.vinted_item_id,
            description=f"Detalle {candidate.vinted_item_id}",
            color="Azul",
            category="Polos",
            photos=[f"https://images.example.test/{candidate.vinted_item_id}-detail.webp"],
        )


class FakeDiscardingDetailProvider(FakeSuccessProvider):
    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        self.detail_calls.append(candidate.vinted_item_id)
        return CatalogItemDetail(
            vinted_item_id=candidate.vinted_item_id,
            description="contiene descarte pytest",
            color="Azul",
            category="Polos",
            photos=[],
        )


class FakeFailingDetailProvider(FakeSuccessProvider):
    settings = SimpleNamespace(vinted_detail_max_candidates_per_run=5, vinted_detail_concurrency=1)

    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        self.detail_calls.append(candidate.vinted_item_id)
        raise RuntimeError("detail boom cookie=session-secret")


@pytest.fixture
def source_id() -> int:
    cleanup_source(None)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest manual run source",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            scheduler_config={},
            filter_rule_ids=[],
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        created_id = source.id

    try:
        yield created_id
    finally:
        cleanup_source(created_id)


def cleanup_source(source_id: int | None) -> None:
    with SessionLocal() as db:
        source_ids = (
            [source_id]
            if source_id is not None
            else list(db.scalars(select(SearchSource.id).where(SearchSource.name.like("pytest%"))))
        )
        run_ids = list(db.scalars(select(Run.id).where(Run.source_id.in_(source_ids)))) if source_ids else []
        rule_ids = list(db.scalars(select(FilterRule.id).where(FilterRule.name.like("pytest%"))))
        item_ids = list(db.scalars(select(Item.id).where(Item.vinted_item_id.like("pytest-run-item%"))))
        if item_ids:
            db.query(Opportunity).filter(Opportunity.item_id.in_(item_ids)).delete(synchronize_session=False)
        if rule_ids:
            db.query(Opportunity).filter(Opportunity.rule_id.in_(rule_ids)).delete(synchronize_session=False)
        if run_ids:
            db.query(RunEvent).filter(RunEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(ErrorLog).filter(ErrorLog.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
        if source_ids:
            db.query(RunEvent).filter(RunEvent.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(ErrorLog).filter(ErrorLog.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(Opportunity).filter(Opportunity.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(SearchSource).filter(SearchSource.id.in_(source_ids)).update(
                {SearchSource.proxy_profile_id: None},
                synchronize_session=False,
            )
        db.query(FilterRule).filter(FilterRule.name.like("pytest%")).delete(synchronize_session=False)
        db.query(ProxyProfile).filter(ProxyProfile.name.like("pytest%")).delete(synchronize_session=False)
        db.query(Item).filter(Item.vinted_item_id.like("pytest-run-item%")).delete(synchronize_session=False)
        if source_id is not None:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
        elif source_ids:
            db.query(SearchSource).filter(SearchSource.id.in_(source_ids)).delete(synchronize_session=False)
        db.commit()


def test_monitor_run_creates_opportunities_and_persists_only_opportunity_items(source_id: int) -> None:
    cache = FakeSeenCache()
    provider = FakeSuccessProvider(item_count=2)

    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache)
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))

        assert run.status == SUCCESS
        assert run.items_found == 2
        assert run.items_new == 2
        assert run.opportunities_created == 2
        assert item_count == 2
        assert opportunity_count == 2
        assert sorted(cache.marked_seen) == ["pytest-run-item-0", "pytest-run-item-1"]


def test_punctual_manual_run_executes_inactive_monitor_without_activating_it(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = False
        source.monitor_mode = "manual"
        source.monitor_started_at = datetime(2026, 7, 4, 8, 0, tzinfo=UTC)
        source.monitor_until = datetime(2026, 7, 4, 9, 0, tzinfo=UTC)
        source.next_run_at = datetime(2026, 7, 4, 8, 5, tzinfo=UTC)
        db.commit()

    with SessionLocal() as db:
        run = execute_manual_run(db, source_id, provider=FakeSuccessProvider(item_count=1), seen_cache=FakeSeenCache())
        source = db.get(SearchSource, source_id)

        assert run.status == SUCCESS
        assert source is not None
        assert source.is_active is False
        assert source.monitor_started_at is None
        assert source.monitor_until is None
        assert source.next_run_at is None
        assert source.last_run_at == run.finished_at


def test_scheduler_style_run_still_requires_active_monitor(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = False
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(SearchSourceInactiveError):
            execute_monitor_run(db, source_id, provider=FakeSuccessProvider(item_count=1), seen_cache=FakeSeenCache())


def test_monitor_run_api_executes_inactive_manual_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest api manual monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
            filter_rule_ids=[],
        )
        db.add(source)
        db.commit()
        source_id = source.id

    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=1)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/runs")

        assert response.status_code == 201
        assert response.json()["status"] == SUCCESS
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.is_active is False
            assert source.monitor_until is None
            assert source.next_run_at is None
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_monitor_start_api_in_manual_mode_runs_once_and_stays_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest api manual start monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
            filter_rule_ids=[],
        )
        db.add(source)
        db.commit()
        source_id = source.id

    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=1)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

        assert response.status_code == 201
        assert response.json()["status"] == SUCCESS
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.is_active is False
            assert source.monitor_started_at is None
            assert source.monitor_until is None
            assert source.next_run_at is None
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_seen_cache_hit_skips_detail_and_database_writes(source_id: int) -> None:
    cache = FakeSeenCache(initially_seen={"pytest-run-item-0"})
    provider = FakeSuccessProvider(item_count=1)

    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache)
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))

        assert run.status == SUCCESS
        assert run.items_new == 0
        assert run.opportunities_created == 0
        assert item_count == 0
        assert provider.detail_calls == []


def test_discarded_item_is_not_persisted(source_id: int) -> None:
    with SessionLocal() as db:
        rule = create_filter_rule(
            db,
            name="pytest discard",
            definition={"blacklist_terms": ["descarte"]},
            is_active=True,
        )
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.filter_rule_ids = [rule.id]
        db.commit()

    cache = FakeSeenCache()
    provider = FakeDiscardingDetailProvider(item_count=1)

    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache)
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))

        assert run.items_discarded_by_filters == 1
        assert run.opportunities_created == 0
        assert item_count == 0
        assert opportunity_count == 0


def test_detail_failure_creates_opportunity_with_redacted_error(source_id: int) -> None:
    with SessionLocal() as db:
        rule = create_filter_rule(db, name="pytest filter", definition={"blacklist_terms": ["nunca"]}, is_active=True)
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.filter_rule_ids = [rule.id]
        db.commit()

    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=FakeFailingDetailProvider(item_count=1), seen_cache=FakeSeenCache())
        opportunity = db.scalar(select(Opportunity).where(Opportunity.source_id == source_id))
        item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))

        assert run.opportunities_created == 1
        assert run.items_filter_pending == 1
        assert opportunity is not None
        assert opportunity.evaluation_status == "detail_error"
        assert item is not None
        assert "session-secret" not in (item.detail_error or "")


def test_redis_unavailable_fails_run_and_pauses_monitor(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=FakeSuccessProvider(item_count=1), seen_cache=FakeSeenCache(unavailable=True))
        source = db.get(SearchSource, source_id)
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))

        assert run.status == FAILED
        assert "Redis seen cache is unavailable" in (run.error_message or "")
        assert source is not None
        assert source.is_active is False
        assert opportunity_count == 0


def test_same_item_can_create_opportunity_in_different_monitor(source_id: int) -> None:
    with SessionLocal() as db:
        second = SearchSource(
            name="pytest second monitor",
            url="https://www.vinted.es/catalog?search_text=second",
            normalized_query={"search_text": ["second"]},
            is_active=True,
            scheduler_config={},
            filter_rule_ids=[],
        )
        db.add(second)
        db.commit()
        second_id = second.id

    try:
        provider = FakeSuccessProvider(item_count=1)
        with SessionLocal() as db:
            first_run = execute_monitor_run(db, source_id, provider=provider, seen_cache=FakeSeenCache())
            second_run = execute_monitor_run(db, second_id, provider=provider, seen_cache=FakeSeenCache())
            item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))
            assert item is not None
            opportunity_sources = sorted(db.scalars(select(Opportunity.source_id).where(Opportunity.item_id == item.id)))

            assert first_run.opportunities_created == 1
            assert second_run.opportunities_created == 1
            assert opportunity_sources == sorted([source_id, second_id])
    finally:
        cleanup_source(second_id)
