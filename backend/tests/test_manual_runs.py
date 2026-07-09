from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from vinted_monitor.api.main import app, get_manual_run_provider
from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import ErrorLog, Item, MonitorSession, Opportunity, ProxyProfile, Run, RunEvent, SearchSource, VintedSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession
from vinted_monitor.services.monitor_sessions import start_monitor_session
from vinted_monitor.services.monitor_stats import get_monitor_stats
from vinted_monitor.services.proxies import create_proxy_profile
from vinted_monitor.services.run_events import record_run_event
from vinted_monitor.services.runs import (
    DETAIL_PROBE_TRIGGER,
    FAILED,
    SESSION_PREPARE_TRIGGER,
    SUCCESS,
    SearchSourceInactiveError,
    _persist_provider_session_refresh,
    execute_manual_run,
    execute_monitor_baseline,
    execute_monitor_run,
)
from vinted_monitor.services.scheduler import RunEgress, update_scheduler_config, update_scheduler_enabled
from vinted_monitor.services.seen_cache import SeenCacheUnavailableError
from vinted_monitor.services.vinted_sessions import prepared_context_from_session, save_prepared_vinted_session


class FakeSeenCache:
    def __init__(self, *, unavailable: bool = False, initially_seen: set[str] | None = None, baseline_ready: bool = True) -> None:
        self.unavailable = unavailable
        self.seen = set(initially_seen or set())
        self.processing: set[str] = set()
        self.marked_seen: list[str] = []
        self.baseline_ready = baseline_ready
        self.marked_baseline: list[tuple[int, str]] = []

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

    def has_baseline(self, monitor_id: int, policy_hash: str) -> bool:
        self.require_available()
        return self.baseline_ready

    def mark_baseline(self, monitor_id: int, policy_hash: str) -> None:
        self.require_available()
        self.baseline_ready = True
        self.marked_baseline.append((monitor_id, policy_hash))


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

    def fetch_detail(self, candidate: CatalogItemCandidate, *, referer_url: str | None = None) -> CatalogItemDetail:
        self.detail_calls.append(candidate.vinted_item_id)
        return CatalogItemDetail(
            vinted_item_id=candidate.vinted_item_id,
            description=f"Detalle {candidate.vinted_item_id}",
            color="Azul",
            category="Polos",
            photos=[f"https://images.example.test/{candidate.vinted_item_id}-detail.webp"],
        )


class FakeEventingProvider(FakeSuccessProvider):
    event_sink = None

    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        if self.event_sink is not None:
            self.event_sink(
                phase="anonymous_session_bootstrap_start",
                method="GET",
                url=source.url,
                message="Obtaining anonymous public Vinted session",
            )
            self.event_sink(
                phase="anonymous_session_bootstrap_success",
                method="GET",
                url=source.url,
                status_code=200,
                duration_ms=12,
                details={"session_marker_count": 1},
            )
        return super().search(source, page)


class FakeRefreshingProvider(FakeSuccessProvider):
    prepared_session_refreshed = True

    def __init__(self, *, proxy_session_id: str) -> None:
        super().__init__(item_count=1, prefix="pytest-run-item-refreshed")
        self.prepared_session = PreparedCatalogSession(proxy_session_id=proxy_session_id)

    def export_prepared_session(self, *, proxy_session_id: str | None = None) -> PreparedCatalogSession:
        resolved_proxy_session_id = proxy_session_id or self.prepared_session.proxy_session_id
        return PreparedCatalogSession(
            proxy_session_id=resolved_proxy_session_id,
            cookies={
                "access_token_web": "fresh-access-token",
                "v_udt": "fresh-v-udt-token",
                "anon_id": "fresh-anon-id",
            },
            csrf_token="fresh-csrf-token",
            anon_id="fresh-anon-id",
            access_token_web="fresh-access-token",
            datadome=None,
            v_udt="fresh-v-udt-token",
            user_iso_locale="es-ES",
            vinted_screen="catalog",
            egress_ip="203.0.113.20",
            egress_country_code="ES",
        )


class FakeDetailRefreshingProvider(FakeSuccessProvider):
    prepared_session_refreshed = True

    def __init__(self, *, proxy_session_id: str) -> None:
        super().__init__(item_count=1, prefix="pytest-run-item-detail-refreshed")
        self.prepared_session = PreparedCatalogSession(proxy_session_id=proxy_session_id)

    def export_prepared_session(self, *, proxy_session_id: str | None = None) -> PreparedCatalogSession:
        resolved_proxy_session_id = proxy_session_id or self.prepared_session.proxy_session_id
        return PreparedCatalogSession(
            proxy_session_id=resolved_proxy_session_id,
            cookies={
                "__cf_bm": "fresh-cf-bm",
                "_vinted_fr_session": "fresh-vinted-session",
                "access_token_web": "detail-access-token",
                "anon_id": "detail-anon-id",
                "datadome": "detail-datadome-token",
                "v_sid": "detail-v-sid",
                "v_udt": "detail-v-udt-token",
            },
            csrf_token="detail-csrf-token",
            anon_id="detail-anon-id",
            access_token_web="detail-access-token",
            datadome="detail-datadome-token",
            v_udt="detail-v-udt-token",
            user_iso_locale="es-ES",
            vinted_screen="catalog",
            egress_ip="203.0.113.55",
            egress_country_code="ES",
        )


class FakeDiscardingDetailProvider(FakeSuccessProvider):
    def fetch_detail(self, candidate: CatalogItemCandidate, *, referer_url: str | None = None) -> CatalogItemDetail:
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

    def fetch_detail(self, candidate: CatalogItemCandidate, *, referer_url: str | None = None) -> CatalogItemDetail:
        self.detail_calls.append(candidate.vinted_item_id)
        raise RuntimeError("detail boom cookie=session-secret")


class FakeSearchFailingProvider(FakeSuccessProvider):
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        raise RuntimeError("search boom cookie=session-secret")


class FakeSessionPreparingProvider:
    created: list[FakeSessionPreparingProvider] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.event_sink = kwargs.get("event_sink")
        self.closed = False
        self.bootstrap_calls: list[tuple[str, bool]] = []
        self.probe_calls: list[str] = []
        self.detail_probe_calls: list[tuple[str, str | None]] = []
        FakeSessionPreparingProvider.created.append(self)

    def bootstrap_for_session(self, source_url: str, *, collect_datadome: bool = False) -> dict:
        self.bootstrap_calls.append((source_url, collect_datadome))
        if self.event_sink is not None:
            self.event_sink(
                phase="anonymous_session_bootstrap_success",
                method="GET",
                url=source_url,
                status_code=200,
                duration_ms=7,
                details={"anon": True, "v_udt": True},
            )
        return {"bootstrap": "ok", "datadome_cookie": False}

    def probe_catalog_api(self, source_url: str) -> dict:
        self.probe_calls.append(source_url)
        return {
            "outcome": "accepted_json",
            "status_code": 200,
            "duration_ms": 11,
            "missing_required": [],
        }

    def export_prepared_session(self, *, proxy_session_id: str | None = None) -> PreparedCatalogSession:
        return PreparedCatalogSession(
            proxy_session_id=proxy_session_id,
            cookies={
                "access_token_web": "prepared-access-token",
                "v_udt": "prepared-v-udt",
                "anon_id": "prepared-anon",
            },
            csrf_token="prepared-csrf",
            anon_id="prepared-anon",
            access_token_web="prepared-access-token",
            datadome=None,
            v_udt="prepared-v-udt",
            user_iso_locale=self.kwargs["locale"],
            vinted_screen=self.kwargs["screen"],
            egress_ip="203.0.113.42",
            egress_country_code=self.kwargs["expected_country_code"],
        )

    def probe_item_detail_api(self, item_ref: str, *, referer_url: str | None = None) -> dict:
        self.detail_probe_calls.append((item_ref, referer_url))
        if self.event_sink is not None:
            self.event_sink(
                phase="detail_api_probe_success",
                method="GET",
                url=f"https://www.vinted.es/api/v2/items/{item_ref}/details",
                status_code=200,
                duration_ms=13,
                details={
                    "outcome": "accepted_json",
                    "item_id": item_ref,
                    "request_profile": "api_har146",
                    "detail_summary": {"description_present": True, "photo_count": 2},
                },
            )
        return {
            "outcome": "accepted_json",
            "item_id": item_ref,
            "detail_api_url": f"https://www.vinted.es/api/v2/items/{item_ref}/details",
            "status_code": 200,
            "duration_ms": 13,
            "detail_summary": {"description_present": True, "photo_count": 2},
            "missing_required": [],
            "error": None,
        }

    def close(self) -> None:
        self.closed = True


class FakeDataDomeDetailProvider(FakeSessionPreparingProvider):
    def probe_item_detail_api(self, item_ref: str, *, referer_url: str | None = None) -> dict:
        self.detail_probe_calls.append((item_ref, referer_url))
        return {
            "outcome": "datadome_challenge",
            "item_id": item_ref,
            "detail_api_url": f"https://www.vinted.es/api/v2/items/{item_ref}/details",
            "status_code": 403,
            "duration_ms": 17,
            "detail_summary": {},
            "missing_required": [],
            "error": None,
        }


def _test_direct_egress() -> RunEgress:
    return RunEgress(mode="direct")


def _create_ready_vinted_session(
    db,
    source: SearchSource,
    proxy: ProxyProfile,
    *,
    proxy_session_id: str = "pytestsession",
) -> None:
    profile = profile_for_impersonate("chrome146")
    save_prepared_vinted_session(
        db,
        source,
        proxy,
        proxy_session_id=proxy_session_id,
        profile=profile,
        context=PreparedCatalogSession(
            proxy_session_id=proxy_session_id,
            cookies={
                "access_token_web": "access-token",
                "datadome": "datadome-token",
                "v_udt": "v-udt-token",
                "anon_id": "anon-id",
            },
            csrf_token="csrf-token",
            anon_id="anon-id",
            access_token_web="access-token",
            datadome="datadome-token",
            v_udt="v-udt-token",
            user_iso_locale=proxy.locale,
            vinted_screen=proxy.vinted_screen,
            egress_ip="203.0.113.10",
            egress_country_code=proxy.country_code,
        ),
        settings=Settings(),
    )
    db.flush()


def _enable_direct_runtime(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(scheduler_enabled=True, vinted_direct_catalog_enabled=True)
    monkeypatch.setattr("vinted_monitor.api.main.settings", settings)
    monkeypatch.setattr("vinted_monitor.services.runs.get_settings", lambda: settings)
    return settings


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
        item_ids = list(db.scalars(select(Item.id).where(Item.vinted_item_id.like("pytest-run-item%"))))
        if item_ids:
            db.query(Opportunity).filter(Opportunity.item_id.in_(item_ids)).delete(synchronize_session=False)
        if run_ids:
            db.query(RunEvent).filter(RunEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(ErrorLog).filter(ErrorLog.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
        if source_ids:
            db.query(VintedSession).filter(VintedSession.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(MonitorSession).filter(MonitorSession.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(RunEvent).filter(RunEvent.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(ErrorLog).filter(ErrorLog.source_id.in_(source_ids)).delete(synchronize_session=False)
            db.query(Opportunity).filter(Opportunity.source_id.in_(source_ids)).delete(synchronize_session=False)
        proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.name.like("pytest%"))))
        if proxy_ids:
            db.query(VintedSession).filter(VintedSession.proxy_profile_id.in_(proxy_ids)).delete(synchronize_session=False)
            db.query(ProxyProfile).filter(ProxyProfile.id.in_(proxy_ids)).delete(synchronize_session=False)
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
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_direct_egress())
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))
        phases = [event.phase for event in events]

        assert run.status == SUCCESS
        assert run.items_found == 2
        assert run.items_new == 2
        assert run.opportunities_created == 2
        assert item_count == 2
        assert opportunity_count == 2
        assert sorted(cache.marked_seen) == ["pytest-run-item-0", "pytest-run-item-1"]
        assert "run_config_resolved" in phases
        assert "egress_selected" in phases
        assert "catalog_candidates_received" in phases
        assert phases.count("candidate_evaluation_start") == 2
        assert phases.count("candidate_detail_required") == 2
        assert phases.count("detail_fetch_success") == 2
        assert phases.count("candidate_filter_decision") == 2
        assert phases.count("item_persisted") == 2
        assert phases.count("item_detail_persisted") == 2
        assert phases.count("opportunity_created") == 2
        assert sorted(provider.detail_calls) == ["pytest-run-item-0", "pytest-run-item-1"]
        assert "redis_seen_marked" in phases
        assert next(event for event in events if event.phase == "catalog_candidates_received").details["candidate_count"] == 2
        assert next(event for event in events if event.phase == "redis_seen_marked").details["marked_seen_count"] == 2


def test_monitor_run_persists_provider_progress_events(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeEventingProvider(item_count=1),
            seen_cache=FakeSeenCache(),
            egress=_test_direct_egress(),
        )
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))

        phases = [event.phase for event in events]
        assert "anonymous_session_bootstrap_start" in phases
        assert "anonymous_session_bootstrap_success" in phases
        bootstrap_success = next(event for event in events if event.phase == "anonymous_session_bootstrap_success")
        assert bootstrap_success.status_code == 200
        assert bootstrap_success.duration_ms == 12
        assert bootstrap_success.level == "info"
        assert bootstrap_success.details == {"session_marker_count": 1}
        redis_event = next(event for event in events if event.phase == "redis_seen_result")
        assert redis_event.details["seen_miss_count"] == 1
        assert next(event for event in events if event.phase == "run_succeeded").level == "info"


def test_monitor_run_owned_provider_uses_sticky_proxy_and_closes(
    monkeypatch: pytest.MonkeyPatch, source_id: int
) -> None:
    created_providers: list[FakeOwnedProvider] = []

    class FakeOwnedProvider(FakeSuccessProvider):
        def __init__(self, **kwargs) -> None:
            super().__init__(item_count=1)
            self.kwargs = kwargs
            self.closed = False
            created_providers.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", FakeOwnedProvider)
    monkeypatch.setattr(
        "vinted_monitor.services.runs.get_settings",
        lambda: Settings(_env_file=None, proxy_sticky_username_template="{username}-sessid-{session_id}"),
    )

    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest sticky owned provider",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8000,
            username="customer",
            password=None,
        )
        source = db.get(SearchSource, source_id)
        assert source is not None
        _create_ready_vinted_session(db, source, proxy, proxy_session_id="stickytest01")
        run = execute_monitor_run(
            db,
            source_id,
            egress=RunEgress(
                mode="proxy",
                proxy_profile_id=proxy.id,
                proxy_name=proxy.name,
                proxy_kind=proxy.kind,
            ),
            seen_cache=FakeSeenCache(),
        )

        assert run.status == SUCCESS
        assert len(created_providers) == 1
        assert created_providers[0].closed is True
        assert created_providers[0].kwargs["proxy_url"].startswith("http://customer-sessid-stickytest01")
        assert created_providers[0].kwargs["proxy_url"].endswith(":@proxy.example:8000")
        assert run.runtime_metadata["proxy_profile_id"] == proxy.id
        assert run.runtime_metadata["proxy_session_id_prefix"] == "stickyte"
        assert run.runtime_metadata["vinted_session_id"]
        assert run.runtime_metadata["proxy_sticky_session"]["masked"]
        selected = db.scalar(select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "vinted_session_selected"))
        assert selected is not None
        assert selected.details["vinted_session_id"] == run.runtime_metadata["vinted_session_id"]
        assert selected.details["proxy_session"] == run.runtime_metadata["proxy_sticky_session"]
        assert created_providers[0].kwargs["proxy_session_marker"] == run.runtime_metadata["proxy_sticky_session"]


def test_monitor_session_prepare_api_creates_ready_session_without_business_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    FakeSessionPreparingProvider.created = []
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", FakeSessionPreparingProvider)
    monkeypatch.setattr(
        "vinted_monitor.services.runs.get_settings",
        lambda: Settings(
            scheduler_enabled=True,
            proxy_sticky_username_template="{username}-sessid-{session_id}",
            vinted_prepared_session_required=True,
        ),
    )
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest prepare proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8010,
            username="customer",
            password=None,
        )
        source = SearchSource(
            name="pytest prepare monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id
        source_url = source.url
        proxy_id = proxy.id

    try:
        response = client.post(f"/api/monitors/{source_id}/vinted-session/prepare")

        assert response.status_code == 201
        body = response.json()
        assert body["trigger"] == SESSION_PREPARE_TRIGGER
        assert body["status"] == SUCCESS
        assert body["items_found"] == 0
        assert body["opportunities_created"] == 0
        assert body["runtime_metadata"]["vinted_session_id"]
        assert body["runtime_metadata"]["vinted_session_action"] == "prepared"
        assert body["runtime_metadata"]["vinted_session_datadome_present"] is False
        assert len(FakeSessionPreparingProvider.created) == 1
        assert FakeSessionPreparingProvider.created[0].closed is True
        assert FakeSessionPreparingProvider.created[0].bootstrap_calls == [(source_url, True)]
        assert FakeSessionPreparingProvider.created[0].probe_calls == [source_url]

        with SessionLocal() as db:
            session = db.scalar(
                select(VintedSession).where(VintedSession.source_id == source_id, VintedSession.proxy_profile_id == proxy_id)
            )
            assert session is not None
            assert session.status == "ready"
            assert session.request_count == 1
            events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == body["id"]).order_by(RunEvent.id.asc())))
            phases = [event.phase for event in events]
            assert "vinted_session_prepare_start" in phases
            assert "vinted_session_prepare_result" in phases
            assert "catalog_search_start" not in phases
            assert "baseline_snapshot_seeded" not in phases
            assert "redis_check_start" not in phases
            assert db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%"))) == 0
            assert db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id)) == 0
            stats = get_monitor_stats(db, source_id, range_name="all")
            assert stats.historical_summary.runs_count == 0
            assert sum(point.runs_count for point in stats.chart_points) == 0
    finally:
        cleanup_source(source_id)


def test_monitor_item_detail_probe_api_uses_prepared_session_without_business_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    FakeSessionPreparingProvider.created = []
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", FakeSessionPreparingProvider)
    monkeypatch.setattr(
        "vinted_monitor.services.runs.get_settings",
        lambda: Settings(
            scheduler_enabled=True,
            proxy_sticky_username_template="{username}-sessid-{session_id}",
            vinted_prepared_session_required=True,
        ),
    )
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest detail probe proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8011,
            username="customer",
            password=None,
        )
        source = SearchSource(
            name="pytest detail probe monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id
        source_url = source.url
        proxy_id = proxy.id

    try:
        response = client.post(
            f"/api/monitors/{source_id}/items/detail-probe",
            json={"item_ref": "9356705635"},
        )

        assert response.status_code == 201
        body = response.json()
        run = body["run"]
        result = body["result"]
        assert run["trigger"] == DETAIL_PROBE_TRIGGER
        assert run["status"] == SUCCESS
        assert run["items_found"] == 0
        assert run["opportunities_created"] == 0
        assert result["outcome"] == "accepted_json"
        assert result["item_id"] == "9356705635"
        assert result["detail_summary"]["photo_count"] == 2
        assert len(FakeSessionPreparingProvider.created) == 2
        assert FakeSessionPreparingProvider.created[0].closed is True
        assert FakeSessionPreparingProvider.created[1].closed is True
        assert FakeSessionPreparingProvider.created[1].detail_probe_calls == [("9356705635", source_url)]

        with SessionLocal() as db:
            session = db.scalar(
                select(VintedSession).where(VintedSession.source_id == source_id, VintedSession.proxy_profile_id == proxy_id)
            )
            assert session is not None
            assert session.status == "ready"
            events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run["id"]).order_by(RunEvent.id.asc())))
            phases = [event.phase for event in events]
            assert "vinted_session_prepare_start" in phases
            assert "detail_api_probe_success" in phases
            assert "detail_probe_finished" in phases
            assert "run_succeeded" in phases
            assert "catalog_search_start" not in phases
            assert "baseline_snapshot_seeded" not in phases
            assert "redis_check_start" not in phases
            assert db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%"))) == 0
            assert db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id)) == 0
            stats = get_monitor_stats(db, source_id, range_name="all")
            assert stats.historical_summary.runs_count == 0
            assert sum(point.runs_count for point in stats.chart_points) == 0
    finally:
        cleanup_source(source_id)


def test_monitor_item_detail_probe_invalidates_session_on_datadome_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    FakeSessionPreparingProvider.created = []
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", FakeDataDomeDetailProvider)
    monkeypatch.setattr(
        "vinted_monitor.services.runs.get_settings",
        lambda: Settings(
            scheduler_enabled=True,
            proxy_sticky_username_template="{username}-sessid-{session_id}",
            vinted_prepared_session_required=True,
        ),
    )
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest detail datadome proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8012,
            username="customer",
            password=None,
        )
        source = SearchSource(
            name="pytest detail datadome monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        _create_ready_vinted_session(db, source, proxy, proxy_session_id="detaildatadome01")
        db.commit()
        source_id = source.id
        proxy_id = proxy.id

    try:
        response = client.post(
            f"/api/monitors/{source_id}/items/detail-probe",
            json={"item_ref": "9356705635"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["run"]["status"] == SUCCESS
        assert body["result"]["outcome"] == "datadome_challenge"
        assert len(FakeSessionPreparingProvider.created) == 1

        with SessionLocal() as db:
            session = db.scalar(
                select(VintedSession).where(VintedSession.source_id == source_id, VintedSession.proxy_profile_id == proxy_id)
            )
            assert session is not None
            assert session.status == "invalid"
            assert session.failure_count == 1
            assert "DataDome challenge" in (session.last_error or "")
            events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == body["run"]["id"]).order_by(RunEvent.id.asc())))
            phases = [event.phase for event in events]
            assert "detail_probe_finished" in phases
            assert "run_succeeded" in phases
    finally:
        cleanup_source(source_id)


def test_monitor_run_persists_refreshed_prepared_vinted_session_context(source_id: int) -> None:
    proxy_session_id = "refreshpersist01"
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest refresh persist proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8002,
            username="customer",
            password=None,
        )
        source = db.get(SearchSource, source_id)
        assert source is not None
        _create_ready_vinted_session(db, source, proxy, proxy_session_id=proxy_session_id)
        vinted_session = db.scalar(
            select(VintedSession).where(
                VintedSession.source_id == source.id,
                VintedSession.proxy_profile_id == proxy.id,
                VintedSession.proxy_session_id == proxy_session_id,
            )
        )
        assert vinted_session is not None
        original_request_count = vinted_session.request_count

        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeRefreshingProvider(proxy_session_id=proxy_session_id),
            egress=RunEgress(
                mode="proxy",
                proxy_profile_id=proxy.id,
                proxy_name=proxy.name,
                proxy_kind=proxy.kind,
            ),
            seen_cache=FakeSeenCache(),
            runtime_metadata_extra={
                "vinted_session_id": vinted_session.id,
                "proxy_profile_id": proxy.id,
            },
        )

        assert run.status == SUCCESS
        db.refresh(vinted_session)
        refreshed = prepared_context_from_session(vinted_session, Settings())
        assert vinted_session.request_count == original_request_count
        assert vinted_session.status == "ready"
        assert refreshed.proxy_session_id == proxy_session_id
        assert refreshed.access_token_web == "fresh-access-token"
        assert refreshed.csrf_token == "fresh-csrf-token"
        assert refreshed.datadome is None
        assert refreshed.egress_ip == "203.0.113.20"
        event = db.scalar(select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "vinted_session_context_refreshed"))
        assert event is not None
        assert event.details["vinted_session_id"] == vinted_session.id
        assert event.details["vinted_session_status"] == "ready"
        assert event.details["context"]["user_iso_locale"] is True
        assert event.details["context"]["vinted_screen"] is True


def test_run_persists_prepared_vinted_session_context_refreshed_by_detail(source_id: int) -> None:
    proxy_session_id = "detailrefresh01"
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest detail refresh persist proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8003,
            username="customer",
            password=None,
        )
        source = db.get(SearchSource, source_id)
        assert source is not None
        _create_ready_vinted_session(db, source, proxy, proxy_session_id=proxy_session_id)
        vinted_session = db.scalar(
            select(VintedSession).where(
                VintedSession.source_id == source.id,
                VintedSession.proxy_profile_id == proxy.id,
                VintedSession.proxy_session_id == proxy_session_id,
            )
        )
        assert vinted_session is not None
        run = Run(
            source_id=source.id,
            status="running",
            trigger="manual",
            started_at=datetime.now(UTC),
            runtime_metadata={
                "vinted_session_id": vinted_session.id,
                "proxy_profile_id": proxy.id,
            },
        )
        db.add(run)
        db.flush()
        provider = FakeDetailRefreshingProvider(proxy_session_id=proxy_session_id)

        _persist_provider_session_refresh(db, provider, run, source, proxy.id, Settings())

        db.refresh(vinted_session)
        refreshed = prepared_context_from_session(vinted_session, Settings())
        assert provider.prepared_session_refreshed is False
        assert refreshed.proxy_session_id == proxy_session_id
        assert refreshed.datadome == "detail-datadome-token"
        assert refreshed.cookies is not None
        assert refreshed.cookies["datadome"] == "detail-datadome-token"
        assert refreshed.cookies["_vinted_fr_session"] == "fresh-vinted-session"
        assert refreshed.cookies["__cf_bm"] == "fresh-cf-bm"
        event = db.scalar(select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "vinted_session_context_refreshed"))
        assert event is not None
        assert event.details["context"]["datadome"] is True


def test_ready_vinted_session_is_scoped_to_monitor(source_id: int) -> None:
    other_source_id: int | None = None
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        other_source = SearchSource(
            name="pytest other scoped session source",
            url="https://www.vinted.es/catalog?search_text=other",
            normalized_query={"search_text": ["other"]},
            scheduler_config={},
        )
        proxy = create_proxy_profile(
            db,
            name="pytest scoped session proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8001,
            username=None,
            password=None,
        )
        db.add(other_source)
        db.flush()
        other_source_id = other_source.id
        _create_ready_vinted_session(db, source, proxy, proxy_session_id="scopedtest01")
        db.commit()

        from vinted_monitor.services.vinted_sessions import VintedSessionRequiredError, get_ready_vinted_session

        with pytest.raises(VintedSessionRequiredError):
            get_ready_vinted_session(db, other_source, proxy, settings=Settings(), require_datadome=False)

    cleanup_source(other_source_id)


def test_active_monitor_stops_after_vinted_session_use_limit() -> None:
    source_id: int | None = None
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest vinted session use limit",
            url="https://www.vinted.es/catalog?search_text=limit",
            normalized_query={"search_text": ["limit"]},
            is_active=True,
            monitor_mode="continuous",
            scheduler_config={
                "interval_seconds": 300,
                "jitter_percent": 0,
                "allowed_windows": [],
                "stop_after_vinted_session_uses": 1,
            },
        )
        db.add(source)
        db.flush()
        start_monitor_session(db, source)
        db.commit()
        source_id = source.id

        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeSuccessProvider(item_count=1, prefix="pytest-run-item-limit"),
            seen_cache=FakeSeenCache(),
            egress=_test_direct_egress(),
            runtime_metadata_extra={"vinted_session_id": 987654},
        )

        db.refresh(source)
        event = db.scalar(select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "vinted_session_use_limit_reached"))
        assert run.status == SUCCESS
        assert source.is_active is False
        assert source.monitor_mode == "continuous"
        assert event is not None
        assert event.details["vinted_session_use_count"] == 1
        assert event.details["stop_after_vinted_session_uses"] == 1

    cleanup_source(source_id)


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
        run = execute_manual_run(
            db,
            source_id,
            provider=FakeSuccessProvider(item_count=1),
            seen_cache=FakeSeenCache(),
            egress=_test_direct_egress(),
        )
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
            execute_monitor_run(
                db,
                source_id,
                provider=FakeSuccessProvider(item_count=1),
                seen_cache=FakeSeenCache(),
                egress=_test_direct_egress(),
            )


def test_recalibrate_baseline_marks_visible_items_without_opportunities(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = False
        source.monitor_mode = "manual"
        db.commit()

    cache = FakeSeenCache(baseline_ready=False)
    with SessionLocal() as db:
        run = execute_monitor_baseline(
            db,
            source_id,
            provider=FakeSuccessProvider(item_count=2),
            seen_cache=cache,
            egress=_test_direct_egress(),
        )
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))
        phases = [event.phase for event in events]

        assert run.status == SUCCESS
        assert run.trigger == "baseline"
        assert run.items_found == 2
        assert run.items_new == 0
        assert run.opportunities_created == 0
        assert item_count == 0
        assert opportunity_count == 0
        assert sorted(cache.marked_seen) == ["pytest-run-item-0", "pytest-run-item-1"]
        assert cache.baseline_ready is True
        assert cache.marked_baseline
        assert "baseline_snapshot_seeded" in phases
        assert "filter_passed" not in phases
        assert "opportunity_created" not in phases


def test_monitor_run_without_baseline_fails_before_catalog(source_id: int) -> None:
    provider = FakeSuccessProvider(item_count=1)
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=FakeSeenCache(baseline_ready=False),
            egress=_test_direct_egress(),
        )
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))

        assert run.status == FAILED
        assert "Recalibra el listado inicial" in (run.error_message or "")
        assert provider.detail_calls == []
        assert any(event.phase == "baseline_required" for event in events)
        assert all(event.phase != "catalog_search_start" for event in events)


def test_monitor_run_skips_existing_opportunity_before_filters(source_id: int) -> None:
    provider = FakeSuccessProvider(item_count=1)
    cache = FakeSeenCache()
    with SessionLocal() as db:
        first_run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_direct_egress())
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = True
        db.commit()
        second_cache = FakeSeenCache()
        second_run = execute_monitor_run(db, source_id, provider=provider, seen_cache=second_cache, egress=_test_direct_egress())
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == second_run.id).order_by(RunEvent.id.asc())))

        assert first_run.opportunities_created == 1
        assert second_run.items_new == 0
        assert second_run.opportunities_created == 0
        assert any(event.phase == "candidate_existing_opportunity_skipped" for event in events)
        assert all(event.phase != "filter_passed" for event in events)


def test_monitor_run_api_requires_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest api baseline required monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    _enable_direct_runtime(monkeypatch)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache(baseline_ready=False))
    try:
        response = client.post(f"/api/monitors/{source_id}/runs")

        assert response.status_code == 409
        assert "Recalibra el listado inicial" in response.json()["detail"]
    finally:
        cleanup_source(source_id)


def test_monitor_baseline_api_recalibrates_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    cache = FakeSeenCache(baseline_ready=False)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest api baseline monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=2)
    _enable_direct_runtime(monkeypatch)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: cache)
    try:
        response = client.post(f"/api/monitors/{source_id}/baseline")

        assert response.status_code == 201
        body = response.json()
        assert body["trigger"] == "baseline"
        assert body["items_found"] == 2
        assert body["items_new"] == 0
        assert body["opportunities_created"] == 0
        assert cache.baseline_ready is True
        monitors_response = client.get("/api/monitors")
        monitor = next(entry for entry in monitors_response.json() if entry["id"] == source_id)
        assert monitor["baseline_ready"] is True
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_monitor_baseline_api_rejects_existing_monitor_with_unsupported_url_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest unsupported legacy monitor",
            url="https://www.vinted.es/catalog?catalog[]=76&color_ids[]=12",
            normalized_query={"catalog[]": ["76"], "color_ids[]": ["12"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/baseline")

        assert response.status_code == 422
        assert "color_ids" in response.json()["detail"]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Run).where(Run.source_id == source_id)) == 0
    finally:
        cleanup_source(source_id)


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("runs", None),
        ("vinted-session/prepare", None),
        ("items/detail-probe", {"item_ref": "9356705635"}),
    ],
)
def test_monitor_traffic_actions_reject_unsupported_url_filter_before_creating_run(
    endpoint: str,
    payload: dict | None,
) -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name=f"pytest unsupported {endpoint}",
            url="https://www.vinted.es/catalog?catalog[]=76&color_ids[]=12",
            normalized_query={"catalog[]": ["76"], "color_ids[]": ["12"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    try:
        if payload is None:
            response = client.post(f"/api/monitors/{source_id}/{endpoint}")
        else:
            response = client.post(f"/api/monitors/{source_id}/{endpoint}", json=payload)

        assert response.status_code == 422
        assert "color_ids" in response.json()["detail"]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Run).where(Run.source_id == source_id)) == 0
    finally:
        cleanup_source(source_id)


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
        )
        db.add(source)
        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True))
        db.commit()
        source_id = source.id

    _enable_direct_runtime(monkeypatch)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=1)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/runs")

        assert response.status_code == 201
        assert response.json()["status"] == SUCCESS
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            run = db.get(Run, response.json()["id"])
            assert source is not None
            assert source.is_active is False
            assert source.monitor_until is None
            assert source.next_run_at is None
            assert session is not None
            assert session.stopped_at is not None
            assert session.stop_reason == "completed"
            assert run is not None
            assert run.monitor_session_id == session.id
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_monitor_run_api_returns_conflict_when_no_egress_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        active_proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.is_active.is_(True))))
        if active_proxy_ids:
            db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                {ProxyProfile.is_active: False},
                synchronize_session=False,
            )
        update_scheduler_config(db, {"allow_direct_without_proxy": False}, Settings())
        source = SearchSource(
            name="pytest no egress capacity",
            url="https://www.vinted.es/catalog?search_text=no-egress",
            normalized_query={"search_text": ["no-egress"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/runs")

        assert response.status_code == 409
        assert "No proxy is available" in response.json()["detail"]
    finally:
        with SessionLocal() as db:
            update_scheduler_config(db, {"allow_direct_without_proxy": True}, Settings())
            if active_proxy_ids:
                db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                    {ProxyProfile.is_active: True},
                    synchronize_session=False,
                )
                db.commit()
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
        )
        db.add(source)
        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True))
        db.commit()
        source_id = source.id

    _enable_direct_runtime(monkeypatch)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=1)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

        assert response.status_code == 201
        assert response.json()["status"] == SUCCESS
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            run = db.get(Run, response.json()["id"])
            assert source is not None
            assert source.is_active is False
            assert source.monitor_started_at is None
            assert source.monitor_until is None
            assert source.next_run_at is None
            assert session is not None
            assert session.stopped_at is not None
            assert session.stop_reason == "completed"
            assert run is not None
            assert run.monitor_session_id == session.id
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_recurring_monitor_start_creates_session_and_run_uses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest api recurring start monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 300, "jitter_percent": 0, "allowed_windows": []},
        )
        db.add(source)
        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True))
        db.commit()
        source_id = source.id

    _enable_direct_runtime(monkeypatch)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=1)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

        assert response.status_code == 201
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            run = db.get(Run, response.json()["id"])
            assert source is not None
            assert source.is_active is True
            assert session is not None
            assert session.stopped_at is None
            assert run is not None
            assert run.monitor_session_id == session.id
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_monitor_stop_closes_active_session() -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest stop session monitor",
            url="https://www.vinted.es/catalog?search_text=",
            normalized_query={"search_text": [""]},
            is_active=True,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 300, "jitter_percent": 0, "allowed_windows": []},
        )
        db.add(source)
        db.flush()
        session = MonitorSession(source_id=source.id, started_at=datetime.now(UTC) - timedelta(minutes=5))
        db.add(session)
        db.commit()
        source_id = source.id

    try:
        response = client.post(f"/api/monitors/{source_id}/stop")

        assert response.status_code == 200
        with SessionLocal() as db:
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            assert session is not None
            assert session.stopped_at is not None
            assert session.stop_reason == "stopped"
    finally:
        cleanup_source(source_id)


def test_recurring_monitor_failure_below_threshold_keeps_session_active(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest recurring failure stops session",
            url="https://www.vinted.es/catalog?search_text=",
            normalized_query={"search_text": [""]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 300, "jitter_percent": 0, "allowed_windows": []},
        )
        db.add(source)
        update_scheduler_enabled(db, True, Settings(scheduler_enabled=True))
        db.commit()
        source_id = source.id

    _enable_direct_runtime(monkeypatch)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSearchFailingProvider()
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

        assert response.status_code == 201
        assert response.json()["status"] == FAILED
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            run = db.get(Run, response.json()["id"])
            assert source is not None
            assert source.is_active is True
            assert source.monitor_started_at is not None
            assert session is not None
            assert session.stopped_at is None
            assert run is not None
            assert run.monitor_session_id == session.id
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_monitor_stats_aggregates_sessions_and_chart_points() -> None:
    cleanup_source(None)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest stats monitor",
            url="https://www.vinted.es/catalog?search_text=",
            normalized_query={"search_text": [""]},
            is_active=True,
            monitor_mode="continuous",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        session = MonitorSession(source_id=source.id, started_at=datetime(2026, 7, 4, 8, 0, tzinfo=UTC))
        db.add(session)
        db.flush()
        db.add_all(
            [
                Run(
                    source_id=source.id,
                    monitor_session_id=session.id,
                    status=SUCCESS,
                    trigger="manual",
                    started_at=datetime(2026, 7, 4, 8, 15, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 4, 8, 16, tzinfo=UTC),
                    items_found=3,
                    items_new=2,
                    items_filter_passed=2,
                    items_discarded_by_filters=1,
                    items_filter_pending=0,
                    opportunities_created=2,
                    runtime_metadata={},
                ),
                Run(
                    source_id=source.id,
                    monitor_session_id=session.id,
                    status=SUCCESS,
                    trigger="scheduler",
                    started_at=datetime(2026, 7, 4, 9, 5, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 4, 9, 6, tzinfo=UTC),
                    items_found=4,
                    items_new=1,
                    items_filter_passed=1,
                    items_discarded_by_filters=0,
                    items_filter_pending=0,
                    opportunities_created=1,
                    runtime_metadata={},
                ),
            ]
        )
        db.commit()
        source_id = source.id

    try:
        with SessionLocal() as db:
            stats = get_monitor_stats(db, source_id, range_name="days", now=datetime(2026, 7, 4, 10, 0, tzinfo=UTC))

        assert stats.active_session is not None
        assert stats.latest_session is not None
        assert stats.latest_session.id == stats.active_session.id
        assert stats.session_summary.sessions_count == 1
        assert stats.session_summary.runs_count == 2
        assert stats.session_summary.items_found == 7
        assert stats.historical_summary.opportunities_created == 3
        assert stats.bucket_label == "1 h"
        assert stats.bucket_seconds == 3600
        chart_hits = [point for point in stats.chart_points if point.items_found > 0]
        assert [point.items_found for point in chart_hits] == [3, 4]
    finally:
        cleanup_source(source_id)


def test_runs_endpoint_filters_by_source_id() -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source_a = SearchSource(
            name="pytest runs filter a",
            url="https://www.vinted.es/catalog?search_text=runs-a",
            normalized_query={"search_text": ["runs-a"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        source_b = SearchSource(
            name="pytest runs filter b",
            url="https://www.vinted.es/catalog?search_text=runs-b",
            normalized_query={"search_text": ["runs-b"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add_all([source_a, source_b])
        db.flush()
        db.add_all(
            [
                Run(source_id=source_a.id, status=SUCCESS, trigger="manual", started_at=datetime(2026, 7, 4, 9, 0, tzinfo=UTC)),
                Run(source_id=source_b.id, status=SUCCESS, trigger="manual", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC)),
            ]
        )
        db.commit()
        source_a_id = source_a.id
        source_b_id = source_b.id

    try:
        response = client.get(f"/api/runs?source_id={source_a_id}&limit=10")

        assert response.status_code == 200
        runs = response.json()
        assert len(runs) == 1
        assert runs[0]["source_id"] == source_a_id
    finally:
        cleanup_source(source_a_id)
        cleanup_source(source_b_id)


def test_monitor_stats_range_bucket_granularity() -> None:
    cleanup_source(None)
    now = datetime(2026, 7, 4, 12, 34, 56, tzinfo=UTC)
    client = TestClient(app)
    response = client.post(
        "/api/monitors",
        json={"name": "pytest bucket monitor", "url": "https://www.vinted.es/catalog?search_text=bucket"},
    )
    assert response.status_code == 201
    source_id = response.json()["id"]

    try:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            session = MonitorSession(source_id=source.id, started_at=now - timedelta(minutes=30))
            db.add(session)
            db.flush()
            db.add(
                Run(
                    source_id=source.id,
                    monitor_session_id=session.id,
                    status=SUCCESS,
                    trigger="manual",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    items_found=3,
                    items_new=3,
                    items_filter_passed=3,
                    items_discarded_by_filters=0,
                    items_filter_pending=0,
                    opportunities_created=3,
                    runtime_metadata={},
                )
            )
            db.commit()

            expected = {
                "minutes": ("5 s", 5, 12, datetime(2026, 7, 4, 12, 34, tzinfo=UTC), datetime(2026, 7, 4, 12, 35, tzinfo=UTC)),
                "hours": ("5 min", 300, 12, datetime(2026, 7, 4, 12, 0, tzinfo=UTC), datetime(2026, 7, 4, 13, 0, tzinfo=UTC)),
                "days": ("1 h", 3600, 24, datetime(2026, 7, 4, 0, 0, tzinfo=UTC), datetime(2026, 7, 5, 0, 0, tzinfo=UTC)),
                "month": ("1 dia", 86400, 31, datetime(2026, 7, 1, 0, 0, tzinfo=UTC), datetime(2026, 8, 1, 0, 0, tzinfo=UTC)),
            }
            for range_name, (bucket_label, bucket_seconds, point_count, range_start, range_end) in expected.items():
                stats = get_monitor_stats(db, source_id, range_name=range_name, now=now)
                assert stats.bucket_label == bucket_label
                assert stats.bucket_seconds == bucket_seconds
                assert stats.range_start == range_start
                assert stats.range_end == range_end
                assert len(stats.chart_points) == point_count
                assert stats.chart_points[0].bucket_start == range_start
                assert stats.chart_points[-1].bucket_end == range_end
    finally:
        cleanup_source(source_id)


@pytest.mark.parametrize(
    ("age", "bucket_label", "bucket_seconds"),
    (
        (timedelta(minutes=30), "5 min", 300),
        (timedelta(hours=12), "1 h", 3600),
        (timedelta(days=45), "1 dia", 86400),
        (timedelta(days=120), "1 mes", None),
    ),
)
def test_monitor_stats_all_range_chooses_automatic_bucket(
    age: timedelta, bucket_label: str, bucket_seconds: int | None
) -> None:
    cleanup_source(None)
    now = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    client = TestClient(app)
    response = client.post(
        "/api/monitors",
        json={"name": "pytest all bucket monitor", "url": f"https://www.vinted.es/catalog?search_text=all-{age.total_seconds()}"},
    )
    assert response.status_code == 201
    source_id = response.json()["id"]

    try:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            session = MonitorSession(source_id=source.id, started_at=now - age)
            db.add(session)
            db.flush()
            db.add(
                Run(
                    source_id=source.id,
                    monitor_session_id=session.id,
                    status=SUCCESS,
                    trigger="manual",
                    started_at=now - age,
                    finished_at=now - age + timedelta(minutes=1),
                    items_found=1,
                    items_new=1,
                    items_filter_passed=1,
                    items_discarded_by_filters=0,
                    items_filter_pending=0,
                    opportunities_created=1,
                    runtime_metadata={},
                )
            )
            db.commit()

            stats = get_monitor_stats(db, source_id, range_name="all", now=now)

        assert stats.bucket_label == bucket_label
        assert stats.bucket_seconds == bucket_seconds
        assert sum(point.items_found for point in stats.chart_points) == 1
    finally:
        cleanup_source(source_id)


def test_monitor_stats_uses_latest_closed_session_when_inactive() -> None:
    cleanup_source(None)
    client = TestClient(app)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest latest session monitor",
            url="https://www.vinted.es/catalog?search_text=",
            normalized_query={"search_text": [""]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        old_session = MonitorSession(
            source_id=source.id,
            started_at=datetime(2026, 7, 4, 8, 0, tzinfo=UTC),
            stopped_at=datetime(2026, 7, 4, 8, 1, tzinfo=UTC),
            stop_reason="completed",
        )
        latest_session = MonitorSession(
            source_id=source.id,
            started_at=datetime(2026, 7, 4, 9, 0, tzinfo=UTC),
            stopped_at=datetime(2026, 7, 4, 9, 2, tzinfo=UTC),
            stop_reason="completed",
        )
        db.add_all([old_session, latest_session])
        db.flush()
        db.add_all(
            [
                Run(
                    source_id=source.id,
                    monitor_session_id=old_session.id,
                    status=SUCCESS,
                    trigger="manual",
                    started_at=datetime(2026, 7, 4, 8, 0, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 4, 8, 1, tzinfo=UTC),
                    items_found=2,
                    items_new=2,
                    items_filter_passed=2,
                    items_discarded_by_filters=0,
                    items_filter_pending=0,
                    opportunities_created=2,
                    runtime_metadata={},
                ),
                Run(
                    source_id=source.id,
                    monitor_session_id=latest_session.id,
                    status=SUCCESS,
                    trigger="manual",
                    started_at=datetime(2026, 7, 4, 9, 0, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 4, 9, 2, tzinfo=UTC),
                    items_found=5,
                    items_new=1,
                    items_filter_passed=1,
                    items_discarded_by_filters=0,
                    items_filter_pending=0,
                    opportunities_created=1,
                    runtime_metadata={},
                ),
            ]
        )
        db.commit()
        source_id = source.id

    try:
        response = client.get(f"/api/monitors/{source_id}/stats?range=hours")

        assert response.status_code == 200
        body = response.json()
        assert body["range_start"] is not None
        assert body["range_end"] is not None
        assert body["bucket_label"] == "5 min"
        assert body["bucket_seconds"] == 300
        assert body["active_session"] is None
        assert body["latest_session"]["id"] is not None
        assert body["latest_session"]["stop_reason"] == "completed"
        assert body["session_summary"]["sessions_count"] == 1
        assert body["session_summary"]["runs_count"] == 1
        assert body["session_summary"]["items_found"] == 5
        assert body["historical_summary"]["sessions_count"] == 2
        assert body["historical_summary"]["items_found"] == 7
    finally:
        cleanup_source(source_id)


def test_seen_cache_hit_skips_detail_and_database_writes(source_id: int) -> None:
    cache = FakeSeenCache(initially_seen={"pytest-run-item-0"})
    provider = FakeSuccessProvider(item_count=1)

    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_direct_egress())
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))

        assert run.status == SUCCESS
        assert run.items_new == 0
        assert run.opportunities_created == 0
        assert item_count == 0
        assert provider.detail_calls == []


def test_discarded_item_is_not_persisted(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.filter_definition = {"blacklist_terms": ["descarte"]}
        db.commit()

    cache = FakeSeenCache()
    provider = FakeDiscardingDetailProvider(item_count=1)

    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_direct_egress())
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))

        assert run.items_discarded_by_filters == 1
        assert run.opportunities_created == 0
        assert item_count == 0
        assert opportunity_count == 0


def test_detail_failure_skips_opportunity_with_redacted_error(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.filter_definition = {"blacklist_terms": ["nunca"]}
        db.commit()

    with SessionLocal() as db:
        provider = FakeFailingDetailProvider(item_count=1)
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=FakeSeenCache(),
            egress=_test_direct_egress(),
        )
        opportunity = db.scalar(select(Opportunity).where(Opportunity.source_id == source_id))
        item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))
        error_event = db.scalar(
            select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "detail_fetch_error").order_by(RunEvent.id.desc())
        )

        assert run.opportunities_created == 0
        assert run.items_filter_pending == 1
        assert opportunity is None
        assert item is None
        assert provider.detail_calls == ["pytest-run-item-0"]
        assert error_event is not None
        assert "session-secret" not in (error_event.message or "")


def test_redis_unavailable_fails_run_and_pauses_monitor(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeSuccessProvider(item_count=1),
            seen_cache=FakeSeenCache(unavailable=True),
            egress=_test_direct_egress(),
        )
        source = db.get(SearchSource, source_id)
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))

        assert run.status == FAILED
        assert "Redis seen cache is unavailable" in (run.error_message or "")
        assert source is not None
        assert source.is_active is False
        assert opportunity_count == 0
        assert any(event.phase == "redis_check_error" and event.level == "error" for event in events)
        assert any(event.phase == "run_failed" and event.level == "error" for event in events)


def test_same_item_can_create_opportunity_in_different_monitor(source_id: int) -> None:
    with SessionLocal() as db:
        second = SearchSource(
            name="pytest second monitor",
            url="https://www.vinted.es/catalog?search_text=second",
            normalized_query={"search_text": ["second"]},
            is_active=True,
            scheduler_config={},
        )
        db.add(second)
        db.commit()
        second_id = second.id

    try:
        provider = FakeSuccessProvider(item_count=1)
        with SessionLocal() as db:
            first_run = execute_monitor_run(db, source_id, provider=provider, seen_cache=FakeSeenCache(), egress=_test_direct_egress())
            second_run = execute_monitor_run(db, second_id, provider=provider, seen_cache=FakeSeenCache(), egress=_test_direct_egress())
            item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))
            assert item is not None
            opportunity_sources = sorted(db.scalars(select(Opportunity.source_id).where(Opportunity.item_id == item.id)))

            assert first_run.opportunities_created == 1
            assert second_run.opportunities_created == 1
            assert opportunity_sources == sorted([source_id, second_id])
    finally:
        cleanup_source(second_id)


def test_run_event_timestamps_are_assigned_per_event(source_id: int) -> None:
    with SessionLocal() as db:
        first = record_run_event(db, source_id=source_id, phase="pytest_first")
        second = record_run_event(db, source_id=source_id, phase="pytest_second")
        db.commit()

        assert first.created_at is not None
        assert second.created_at is not None
        assert second.created_at >= first.created_at
        assert second.created_at != first.created_at
