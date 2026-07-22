from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event
from types import SimpleNamespace

import pytest
from api_client import authenticated_test_client
from sqlalchemy import func, select

from vinted_monitor.api.main import app, get_manual_run_provider
from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import (
    AppSetting,
    ErrorLog,
    Item,
    MonitorSession,
    Opportunity,
    ProxyProfile,
    Run,
    RunEvent,
    SearchSource,
    VintedSession,
)
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.vinted_catalog import (
    DETAIL_BATCH_TELEMETRY_ATTR,
    CurlCffiVintedCatalogProvider,
    DetailBatchResult,
    DetailFetchOutcome,
    PreparedCatalogSession,
    VintedCatalogSessionContextError,
    VintedItemDetailHTTPError,
    extract_vinted_item_id,
)
from vinted_monitor.services.monitor_sessions import start_monitor_session
from vinted_monitor.services.monitor_stats import get_monitor_stats
from vinted_monitor.services.proxies import (
    create_proxy_profile,
    effective_proxy_identity_generation,
    synchronize_proxy_identity,
)
from vinted_monitor.services.run_events import record_run_event
from vinted_monitor.services.runs import (
    DETAIL_PROBE_TRIGGER,
    FAILED,
    SESSION_PREPARE_TRIGGER,
    SUCCESS,
    SearchSourceInactiveError,
    _complete_finalizing_run,
    _persist_provider_session_refresh,
    _record_failed_run,
    execute_manual_run,
    execute_monitor_baseline,
    execute_monitor_run,
)
from vinted_monitor.services.scheduler import (
    SCHEDULER_SETTING_KEY,
    RunEgress,
    SchedulerCapacityError,
    SchedulerUnavailableError,
    update_scheduler_config,
)
from vinted_monitor.services.scheduler import (
    choose_run_egress as choose_scheduler_run_egress,
)
from vinted_monitor.services.scheduler_liveness import SchedulerWorkerAvailability
from vinted_monitor.services.search_sources import archive_source
from vinted_monitor.services.seen_cache import (
    DetailCandidateStateUpdate,
    SeenCacheUnavailableError,
)
from vinted_monitor.services.task_queue import TaskQueueError
from vinted_monitor.services.vinted_sessions import (
    VintedSessionRequiredError,
    get_ready_vinted_session,
    prepared_context_flags,
    prepared_context_from_session,
    save_prepared_vinted_session,
    update_vinted_session_context,
)


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
        claimed = {
            item_id
            for item_id in vinted_item_ids
            if item_id not in self.seen and item_id not in self.processing
        }
        self.processing.update(claimed)
        return claimed

    def mark_seen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.require_available()
        self.seen.update(vinted_item_ids)
        self.marked_seen.extend(vinted_item_ids)
        self.processing.difference_update(vinted_item_ids)

    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.processing.difference_update(vinted_item_ids)

    def finalize_candidate_states(
        self,
        monitor_id: int,
        policy_hash: str,
        update: DetailCandidateStateUpdate,
    ) -> None:
        self.require_available()
        self.mark_seen(monitor_id, policy_hash, list(update.terminal_ids))

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
                details={
                    "session_marker_count": 1,
                    "proxy_transfer": {
                        "category": "session_setup",
                        "observed_requests": 1,
                        "unobserved_attempts": 0,
                        "request_size_bytes": 100,
                        "upload_size_bytes": 20,
                        "header_size_bytes": 40,
                        "download_size_bytes": 840,
                        "total_observed_bytes": 1000,
                    },
                },
            )
        return super().search(source, page)


class FakeTerminalDetailEventingProvider(FakeSuccessProvider):
    event_sink = None
    transfer_observation = {
        "category": "detail",
        "observed_requests": 0,
        "unobserved_attempts": 1,
        "request_size_bytes": 0,
        "upload_size_bytes": 0,
        "header_size_bytes": 0,
        "download_size_bytes": 0,
        "total_observed_bytes": 0,
    }

    def fetch_detail(self, candidate: CatalogItemCandidate, *, referer_url: str | None = None) -> CatalogItemDetail:
        if self.event_sink is not None:
            self.event_sink(
                phase="detail_http_request_error",
                method="GET",
                url=candidate.url,
                level="error",
                details={"proxy_transfer": self.transfer_observation},
            )
        raise DataDomeChallengeError("terminal detail challenge")


class FakeObservedTerminalDetailEventingProvider(FakeTerminalDetailEventingProvider):
    transfer_observation = {
        "category": "detail",
        "observed_requests": 1,
        "unobserved_attempts": 0,
        "request_size_bytes": 100,
        "upload_size_bytes": 0,
        "header_size_bytes": 50,
        "download_size_bytes": 350,
        "total_observed_bytes": 500,
    }


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
                "datadome": "fresh-datadome-token",
                "__cf_bm": "fresh-cf-bm-token",
            },
            csrf_token="fresh-csrf-token",
            anon_id="fresh-anon-id",
            access_token_web="fresh-access-token",
            datadome="fresh-datadome-token",
            cf_bm="fresh-cf-bm-token",
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
            photos=[f"https://images.example.test/{candidate.vinted_item_id}-detail.webp"],
        )


class FakeConcurrentProvider(CurlCffiVintedCatalogProvider):
    prepared_session_refreshed = False

    def __init__(self) -> None:
        self.settings = Settings(
            _env_file=None,
            vinted_detail_fetch_mode="parallel",
            vinted_detail_concurrency=2,
        )
        self.event_sink = None
        self.batch_calls: list[list[str]] = []

    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        return FakeSuccessProvider(item_count=2, prefix="pytest-run-item-concurrent").search(source, page)

    def fetch_detail(self, candidate: CatalogItemCandidate, *, referer_url: str | None = None) -> CatalogItemDetail:
        raise AssertionError("parallel run must consume prefetched detail outcomes")

    def fetch_detail_batch(
        self,
        candidates: list[CatalogItemCandidate],
        **_kwargs,
    ) -> DetailBatchResult:
        self.batch_calls.append([candidate.vinted_item_id for candidate in candidates])
        outcomes = tuple(
            DetailFetchOutcome(
                position=position,
                candidate=candidate,
                detail=CatalogItemDetail(
                    vinted_item_id=candidate.vinted_item_id,
                    description=f"Detalle {candidate.vinted_item_id}",
                    color="Azul",
                    category="Polos",
                    photos=[f"https://images.example.test/{candidate.vinted_item_id}-detail.webp"],
                ),
                error=None,
                duration_ms=20,
            )
            for position, candidate in enumerate(candidates)
        )
        return DetailBatchResult(
            outcomes=outcomes,
            configured_concurrency=2,
            effective_concurrency=2,
            makespan_ms=25,
            summed_duration_ms=40,
            divergent_cookie_names=("_vinted_fr_session",),
        )


class FakeFailingConcurrentProvider(FakeConcurrentProvider):
    def fetch_detail_batch(self, candidates: list[CatalogItemCandidate], **_kwargs) -> DetailBatchResult:
        error = DataDomeChallengeError("parallel detail challenge")
        setattr(
            error,
            DETAIL_BATCH_TELEMETRY_ATTR,
            {
                "detail_fetch_elapsed_ms": 25,
                "detail_fetch_request_duration_total_ms": 40,
                "detail_fetch_attempts": len(candidates),
            },
        )
        raise error


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
        return {"bootstrap": "ok", "datadome_cookie": True, "cf_bm_cookie": True}

    def probe_catalog_api(self, source_url: str, *, include_payload: bool = False) -> dict:
        self.probe_calls.append(source_url)
        result = {
            "outcome": "accepted_json",
            "status_code": 200,
            "duration_ms": 11,
            "missing_required": [],
        }
        if include_payload:
            result["payload"] = {
                "items": [
                    {
                        "id": 9100000001,
                        "title": "Prepared probe item 1",
                        "brand_title": "Prepared",
                        "price": {"amount": "3.00", "currency_code": "EUR"},
                        "path": "/items/9100000001-prepared-probe-item-1",
                        "size_title": "M",
                        "status": "Muy bueno",
                        "favourite_count": 0,
                        "photo": {"url": "https://images.example/1.jpg"},
                        "user": {"login": "prepared_seller"},
                    },
                    {
                        "id": 9100000002,
                        "title": "Prepared probe item 2",
                        "brand_title": "Prepared",
                        "price": {"amount": "4.00", "currency_code": "EUR"},
                        "path": "/items/9100000002-prepared-probe-item-2",
                        "size_title": "L",
                        "status": "Bueno",
                        "favourite_count": 1,
                        "photo": {"url": "https://images.example/2.jpg"},
                        "user": {"login": "prepared_seller"},
                    },
                ],
                "pagination": {"current_page": 1, "total_pages": 1, "total_entries": 2, "per_page": 5},
            }
        return result

    def export_prepared_session(self, *, proxy_session_id: str | None = None) -> PreparedCatalogSession:
        return PreparedCatalogSession(
            proxy_session_id=proxy_session_id,
            cookies={
                "access_token_web": "prepared-access-token",
                "v_udt": "prepared-v-udt",
                "anon_id": "prepared-anon",
                "datadome": "prepared-datadome",
                "__cf_bm": "prepared-cf-bm",
            },
            csrf_token="prepared-csrf",
            anon_id="prepared-anon",
            access_token_web="prepared-access-token",
            datadome="prepared-datadome",
            cf_bm="prepared-cf-bm",
            v_udt="prepared-v-udt",
            user_iso_locale=self.kwargs["locale"],
            vinted_screen=self.kwargs["screen"],
            egress_ip="203.0.113.42",
            egress_country_code=self.kwargs["expected_country_code"],
        )

    def probe_item_detail_document(self, item_ref: str, *, referer_url: str | None = None) -> dict:
        self.detail_probe_calls.append((item_ref, referer_url))
        item_id = extract_vinted_item_id(item_ref) or item_ref
        if self.event_sink is not None:
            self.event_sink(
                phase="detail_document_probe_success",
                method="GET",
                url=f"https://www.vinted.es/items/{item_id}?referrer=catalog",
                status_code=200,
                duration_ms=13,
                details={
                    "outcome": "accepted_html",
                    "item_id": item_id,
                    "detail_summary": {"parser_version": "next_flight_v2", "photo_count": 2},
                },
            )
        return {
            "outcome": "accepted_html",
            "item_id": item_id,
            "detail_document_url": f"https://www.vinted.es/items/{item_id}?referrer=catalog",
            "status_code": 200,
            "duration_ms": 13,
            "detail_summary": {"parser_version": "next_flight_v2", "photo_count": 2},
            "missing_required": [],
            "error": None,
        }

    def close(self) -> None:
        self.closed = True


class FakeDataDomeDetailProvider(FakeSessionPreparingProvider):
    def probe_item_detail_document(self, item_ref: str, *, referer_url: str | None = None) -> dict:
        self.detail_probe_calls.append((item_ref, referer_url))
        raise DataDomeChallengeError("DataDome challenge on item detail document probe")


class FakeSessionPreparingProviderWithoutDataDome(FakeSessionPreparingProvider):
    def bootstrap_for_session(self, source_url: str, *, collect_datadome: bool = False) -> dict:
        super().bootstrap_for_session(source_url, collect_datadome=collect_datadome)
        return {"bootstrap": "ok", "datadome_cookie": False, "cf_bm_cookie": True}

    def probe_catalog_api(self, source_url: str, *, include_payload: bool = False) -> dict:
        self.probe_calls.append(source_url)
        return {
            "outcome": "accepted_json",
            "status_code": 200,
            "duration_ms": 11,
            "missing_required": ["datadome"],
        }

    def export_prepared_session(self, *, proxy_session_id: str | None = None) -> PreparedCatalogSession:
        prepared = super().export_prepared_session(proxy_session_id=proxy_session_id)
        return PreparedCatalogSession(
            proxy_session_id=prepared.proxy_session_id,
            cookies={key: value for key, value in prepared.cookies.items() if key != "datadome"},
            csrf_token=prepared.csrf_token,
            anon_id=prepared.anon_id,
            access_token_web=prepared.access_token_web,
            datadome=None,
            cf_bm=prepared.cf_bm,
            v_udt=prepared.v_udt,
            user_iso_locale=prepared.user_iso_locale,
            vinted_screen=prepared.vinted_screen,
            egress_ip=prepared.egress_ip,
            egress_country_code=prepared.egress_country_code,
        )


def _test_proxy_egress(db) -> RunEgress:
    proxy = db.scalar(select(ProxyProfile).where(ProxyProfile.name == "pytest injected provider proxy"))
    if proxy is None:
        proxy = ProxyProfile(
            name="pytest injected provider proxy",
            scheme="http",
            kind="residential",
            host="proxy.invalid",
            port=8080,
            country_code="ES",
            max_concurrent_runs=1,
            is_active=True,
        )
        db.add(proxy)
        db.flush()
        synchronize_proxy_identity(db, proxy, Settings())
    return RunEgress(
        mode="proxy",
        proxy_profile_id=proxy.id,
        proxy_name=proxy.name,
        proxy_kind=proxy.kind,
        proxy_identity_generation=effective_proxy_identity_generation(proxy),
    )


def _create_ready_vinted_session(
    db,
    source: SearchSource,
    proxy: ProxyProfile,
    *,
    proxy_session_id: str = "pytestsession",
    settings: Settings | None = None,
) -> VintedSession:
    profile = profile_for_impersonate("chrome146")
    session = save_prepared_vinted_session(
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
                "__cf_bm": "cf-bm-token",
                "v_udt": "v-udt-token",
                "anon_id": "anon-id",
            },
            csrf_token="csrf-token",
            anon_id="anon-id",
            access_token_web="access-token",
            datadome="datadome-token",
            cf_bm="cf-bm-token",
            v_udt="v-udt-token",
            user_iso_locale=proxy.locale,
            vinted_screen=proxy.vinted_screen,
            egress_ip="203.0.113.10",
            egress_country_code=proxy.country_code,
            egress_validated_at=datetime.now(UTC),
        ),
        settings=settings or Settings(),
    )
    db.flush()
    return session


def _enable_test_proxy_runtime(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(scheduler_enabled=True)
    monkeypatch.setattr("vinted_monitor.api.main.settings", settings)
    monkeypatch.setattr("vinted_monitor.services.runs.get_settings", lambda: settings)
    monkeypatch.setattr(
        "vinted_monitor.services.runs.choose_run_egress",
        lambda db, *_args, **_kwargs: _test_proxy_egress(db),
    )
    monkeypatch.setattr(
        "vinted_monitor.api.main.choose_run_egress",
        lambda db, *_args, **_kwargs: _test_proxy_egress(db),
    )
    monkeypatch.setattr(
        "vinted_monitor.services.scheduler._active_proxy_profiles",
        lambda *_args, **_kwargs: [SimpleNamespace(id=999_999, max_concurrent_runs=1)],
    )
    monkeypatch.setattr(
        "vinted_monitor.services.scheduler.scheduler_worker_availability",
        lambda *_args, **_kwargs: SchedulerWorkerAvailability(available=True, last_seen_at=datetime.now(UTC)),
    )
    return settings


@pytest.fixture
def source_id() -> int:
    cleanup_source(None)
    with SessionLocal() as db:
        started_at = datetime.now(UTC)
        source = SearchSource(
            name="pytest manual run source",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            monitor_started_at=started_at,
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        start_monitor_session(db, source, started_at=started_at, allow_manual=True)
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
            db.query(RunEvent).filter(RunEvent.proxy_profile_id.in_(proxy_ids)).delete(synchronize_session=False)
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
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_proxy_egress(db))
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))
        phases = [event.phase for event in events]

        assert run.status == SUCCESS
        assert run.items_found == 2
        assert run.opportunities_created == 2
        assert item_count == 2
        assert opportunity_count == 2
        terminal_event = next(event for event in events if event.phase == "run_succeeded")
        assert terminal_event.details["items_found"] == 2
        assert "items_new" not in terminal_event.details
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
        assert "redis_candidate_state_updated" in phases
        assert next(event for event in events if event.phase == "catalog_candidates_received").details["candidate_count"] == 2
        assert next(event for event in events if event.phase == "redis_candidate_state_updated").details["marked_seen_count"] == 2


def test_monitor_run_parallel_mode_consumes_ordered_prefetched_details(source_id: int) -> None:
    cache = FakeSeenCache()
    provider = FakeConcurrentProvider()

    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )
        opportunity_count = db.scalar(
            select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id)
        )

        assert run.status == SUCCESS
        assert run.opportunities_created == 2
        assert opportunity_count == 2
        assert provider.batch_calls == [
            ["pytest-run-item-concurrent-0", "pytest-run-item-concurrent-1"]
        ]
        assert run.runtime_metadata["detail_fetch_mode"] == "parallel"
        assert run.runtime_metadata["detail_concurrency_effective"] == 2
        assert run.runtime_metadata["detail_batch_makespan_ms"] == 25
        assert run.runtime_metadata["detail_fetch_elapsed_ms"] == 25
        assert run.runtime_metadata["detail_fetch_request_duration_total_ms"] == 40
        assert run.runtime_metadata["detail_fetch_attempts"] == 2
        assert sorted(cache.marked_seen) == [
            "pytest-run-item-concurrent-0",
            "pytest-run-item-concurrent-1",
        ]


def test_monitor_run_preserves_failed_parallel_detail_timings(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeFailingConcurrentProvider(),
            seen_cache=FakeSeenCache(),
            egress=_test_proxy_egress(db),
        )

        assert run.status == FAILED
        assert run.runtime_metadata["detail_fetch_mode"] == "parallel"
        assert run.runtime_metadata["detail_fetch_elapsed_ms"] == 25
        assert run.runtime_metadata["detail_fetch_request_duration_total_ms"] == 40
        assert run.runtime_metadata["detail_fetch_attempts"] == 2


def test_monitor_run_persists_provider_progress_events(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeEventingProvider(item_count=1),
            seen_cache=FakeSeenCache(),
            egress=_test_proxy_egress(db),
        )
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))

        phases = [event.phase for event in events]
        assert "anonymous_session_bootstrap_start" in phases
        assert "anonymous_session_bootstrap_success" in phases
        bootstrap_success = next(event for event in events if event.phase == "anonymous_session_bootstrap_success")
        assert bootstrap_success.status_code == 200
        assert bootstrap_success.duration_ms == 12
        assert bootstrap_success.level == "info"
        assert bootstrap_success.details["session_marker_count"] == 1
        assert bootstrap_success.details["proxy_transfer"]["total_observed_bytes"] == 1000
        assert run.runtime_metadata["proxy_traffic_estimate"]["total_observed_bytes"] == 1000
        redis_event = next(event for event in events if event.phase == "redis_seen_result")
        assert redis_event.details["seen_miss_count"] == 1
        assert next(event for event in events if event.phase == "run_succeeded").level == "info"


def test_monitor_run_aggregates_proxy_transfer_observations_only_for_proxy_egress(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeEventingProvider(item_count=1),
            seen_cache=FakeSeenCache(),
            egress=_test_proxy_egress(db),
        )

        assert run.runtime_metadata["proxy_traffic_estimate"] == {
            "version": 1,
            "observed_requests": 1,
            "unobserved_attempts": 0,
            "request_size_bytes": 100,
            "upload_size_bytes": 20,
            "header_size_bytes": 40,
            "download_size_bytes": 840,
            "total_observed_bytes": 1000,
            "by_category": {
                "session_setup": {
                    "observed_requests": 1,
                    "unobserved_attempts": 0,
                    "total_observed_bytes": 1000,
                }
            },
        }


def test_monitor_run_preserves_partial_proxy_observation_across_failed_detail_rollback(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeTerminalDetailEventingProvider(item_count=1),
            seen_cache=FakeSeenCache(),
            egress=_test_proxy_egress(db),
        )

        assert run.status == "failed"
        assert run.runtime_metadata["proxy_traffic_estimate"]["observed_requests"] == 0
        assert run.runtime_metadata["proxy_traffic_estimate"]["unobserved_attempts"] == 1
        assert run.runtime_metadata["detail_fetch_attempts"] == 1


def test_monitor_run_preserves_observed_proxy_bytes_across_failed_detail_rollback(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeObservedTerminalDetailEventingProvider(item_count=1),
            seen_cache=FakeSeenCache(),
            egress=_test_proxy_egress(db),
        )

        assert run.status == "failed"
        assert run.runtime_metadata["proxy_traffic_estimate"]["observed_requests"] == 1
        assert run.runtime_metadata["proxy_traffic_estimate"]["unobserved_attempts"] == 0
        assert run.runtime_metadata["proxy_traffic_estimate"]["total_observed_bytes"] == 500


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
    runtime_settings = Settings(_env_file=None, proxy_sticky_username_template="{username}-sessid-{session_id}")
    monkeypatch.setattr(
        "vinted_monitor.services.runs.get_settings",
        lambda: runtime_settings,
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
            settings=runtime_settings,
        )
        source = db.get(SearchSource, source_id)
        assert source is not None
        _create_ready_vinted_session(
            db,
            source,
            proxy,
            proxy_session_id="stickytest01",
            settings=runtime_settings,
        )
        run = execute_monitor_run(
            db,
            source_id,
            egress=RunEgress(
                mode="proxy",
                proxy_profile_id=proxy.id,
                proxy_name=proxy.name,
                proxy_kind=proxy.kind,
                proxy_identity_generation=effective_proxy_identity_generation(proxy),
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
    client = authenticated_test_client()
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
        assert body["runtime_metadata"]["vinted_session_datadome_present"] is True
        assert body["runtime_metadata"]["vinted_session_cf_bm_present"] is True
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


def test_monitor_session_prepare_api_rejects_session_without_datadome(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    FakeSessionPreparingProvider.created = []
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", FakeSessionPreparingProviderWithoutDataDome)
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
            name="pytest prepare no datadome proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8012,
            username="customer",
            password=None,
        )
        source = SearchSource(
            name="pytest prepare no datadome monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id
        proxy_id = proxy.id

    try:
        response = client.post(f"/api/monitors/{source_id}/vinted-session/prepare")

        assert response.status_code == 201
        body = response.json()
        assert body["trigger"] == SESSION_PREPARE_TRIGGER
        assert body["status"] == FAILED
        assert "datadome" in body["error_message"]
        with SessionLocal() as db:
            session = db.scalar(
                select(VintedSession).where(VintedSession.source_id == source_id, VintedSession.proxy_profile_id == proxy_id)
            )
            assert session is not None
            assert session.status == "incomplete"
            assert session.request_count == 0
            assert session.last_error is not None
            assert "datadome" in session.last_error
            events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == body["id"]).order_by(RunEvent.id.asc())))
            result_event = next(event for event in events if event.phase == "vinted_session_prepare_result")
            assert result_event.level == "error"
            assert result_event.details["datadome_required"] is True
            assert "datadome" in result_event.details["missing_required"]
    finally:
        cleanup_source(source_id)


def test_monitor_item_detail_probe_api_uses_prepared_session_without_business_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
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
        assert result["outcome"] == "accepted_html"
        assert result["item_id"] == "9356705635"
        assert result["detail_summary"]["photo_count"] == 2
        assert len(FakeSessionPreparingProvider.created) == 2
        assert FakeSessionPreparingProvider.created[0].closed is True
        assert FakeSessionPreparingProvider.created[1].closed is True
        expected_item_url = f"{str(Settings().vinted_base_url).rstrip('/')}/items/9356705635"
        assert FakeSessionPreparingProvider.created[1].detail_probe_calls == [(expected_item_url, source_url)]

        with SessionLocal() as db:
            session = db.scalar(
                select(VintedSession).where(VintedSession.source_id == source_id, VintedSession.proxy_profile_id == proxy_id)
            )
            assert session is not None
            assert session.status == "ready"
            events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run["id"]).order_by(RunEvent.id.asc())))
            phases = [event.phase for event in events]
            assert "vinted_session_prepare_start" in phases
            assert "detail_document_probe_success" in phases
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
    client = authenticated_test_client()
    FakeSessionPreparingProvider.created = []
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", FakeDataDomeDetailProvider)
    runtime_settings = Settings(
        scheduler_enabled=True,
        proxy_sticky_username_template="{username}-sessid-{session_id}",
        vinted_prepared_session_required=True,
    )
    monkeypatch.setattr(
        "vinted_monitor.services.runs.get_settings",
        lambda: runtime_settings,
    )
    monkeypatch.setattr("vinted_monitor.api.main.settings", runtime_settings)
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
            settings=runtime_settings,
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
        _create_ready_vinted_session(
            db,
            source,
            proxy,
            proxy_session_id="detaildatadome01",
            settings=runtime_settings,
        )
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
        assert body["run"]["status"] == FAILED
        assert body["result"]["outcome"] == "failed"
        assert len(FakeSessionPreparingProvider.created) == 1

        with SessionLocal() as db:
            session = db.scalar(
                select(VintedSession).where(VintedSession.source_id == source_id, VintedSession.proxy_profile_id == proxy_id)
            )
            assert session is not None
            assert session.status == "invalid"
            assert session.failure_count == 1
            assert "DataDome challenge" in (session.last_error or "")
            assert not any(prepared_context_flags(prepared_context_from_session(session, Settings())).values())
            events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == body["run"]["id"]).order_by(RunEvent.id.asc())))
            phases = [event.phase for event in events]
            assert "run_failed" in phases
            assert "detail_probe_finished" not in phases
    finally:
        cleanup_source(source_id)


def test_archiving_monitor_invalidates_and_purges_prepared_sessions_when_queue_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_source(None)
    client = authenticated_test_client()

    def fail_ready_task_cancellation(*args, **kwargs) -> None:
        raise TaskQueueError("pytest queue unavailable")

    monkeypatch.setattr(
        "vinted_monitor.services.search_sources.cancel_ready_task_for_source",
        fail_ready_task_cancellation,
    )
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest archive session proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8013,
            username="customer",
            password=None,
        )
        source = SearchSource(
            name="pytest archive session monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        session = _create_ready_vinted_session(db, source, proxy, proxy_session_id="archivesession01")
        db.commit()
        source_id = source.id
        session_id = session.id

    try:
        response = client.delete(f"/api/monitors/{source_id}")

        assert response.status_code == 204
        with SessionLocal() as db:
            persisted = db.get(VintedSession, session_id)
            assert persisted is not None
            assert persisted.status == "invalid"
            assert persisted.invalidated_at is not None
            assert persisted.egress_validated_at is None
            assert persisted.failure_count == 1
            assert persisted.last_error == "Monitor archived"
            assert not any(prepared_context_flags(prepared_context_from_session(persisted, Settings())).values())
    finally:
        cleanup_source(source_id)


def test_archived_monitor_rejects_stale_session_context_refresh() -> None:
    cleanup_source(None)
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest stale archive session proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8014,
            username="customer",
            password=None,
        )
        source = SearchSource(
            name="pytest stale archive session monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        session = _create_ready_vinted_session(db, source, proxy, proxy_session_id="stalearchive01")
        db.commit()
        source_id = source.id
        proxy_id = proxy.id
        session_id = session.id

    stale_db = SessionLocal()
    try:
        assert stale_db.get(VintedSession, session_id) is not None
        with SessionLocal() as archive_db:
            archive_source(archive_db, source_id)

        with pytest.raises(VintedSessionRequiredError, match="archived"):
            update_vinted_session_context(
                stale_db,
                session_id,
                context=PreparedCatalogSession(
                    proxy_session_id="stalearchive01",
                    cookies={"datadome": "fresh-secret"},
                    datadome="fresh-secret",
                ),
                settings=Settings(),
            )
        stale_db.rollback()

        with SessionLocal() as create_db:
            archived_source = create_db.get(SearchSource, source_id)
            persisted_proxy = create_db.get(ProxyProfile, proxy_id)
            assert archived_source is not None
            assert persisted_proxy is not None
            with pytest.raises(VintedSessionRequiredError, match="archived"):
                save_prepared_vinted_session(
                    create_db,
                    archived_source,
                    persisted_proxy,
                    proxy_session_id="stalearchive02",
                    profile=profile_for_impersonate("chrome146"),
                    context=PreparedCatalogSession(
                        proxy_session_id="stalearchive02",
                        cookies={"datadome": "new-secret"},
                        datadome="new-secret",
                    ),
                    settings=Settings(),
                )
            create_db.rollback()

        with SessionLocal() as db:
            persisted = db.get(VintedSession, session_id)
            assert persisted is not None
            assert persisted.status == "invalid"
            assert db.scalar(select(func.count()).select_from(VintedSession).where(VintedSession.source_id == source_id)) == 1
            assert not any(prepared_context_flags(prepared_context_from_session(persisted, Settings())).values())
    finally:
        stale_db.close()
        cleanup_source(source_id)


def test_archive_waits_for_inflight_session_refresh_then_purges_it() -> None:
    cleanup_source(None)
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest concurrent archive session proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8015,
            username="customer",
            password=None,
        )
        source = SearchSource(
            name="pytest concurrent archive session monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        session = _create_ready_vinted_session(db, source, proxy, proxy_session_id="concurrentarchive01")
        db.commit()
        source_id = source.id
        proxy_id = proxy.id
        session_id = session.id

    refresh_locked = Event()
    allow_refresh_commit = Event()
    archive_started = Event()

    def refresh_context() -> None:
        with SessionLocal() as db:
            live_source = db.get(SearchSource, source_id)
            live_proxy = db.get(ProxyProfile, proxy_id)
            assert live_source is not None
            assert live_proxy is not None
            selected, _prepared = get_ready_vinted_session(
                db,
                live_source,
                live_proxy,
                settings=Settings(),
            )
            assert selected.id == session_id
            refresh_locked.set()
            assert allow_refresh_commit.wait(timeout=5)
            updated = update_vinted_session_context(
                db,
                session_id,
                context=PreparedCatalogSession(
                    proxy_session_id="concurrentarchive01",
                    cookies={"datadome": "fresh-secret"},
                    datadome="fresh-secret",
                ),
                settings=Settings(),
            )
            assert updated is not None
            db.commit()

    def archive_monitor() -> None:
        assert refresh_locked.wait(timeout=5)
        archive_started.set()
        with SessionLocal() as db:
            archive_source(db, source_id)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            refresh_future = executor.submit(refresh_context)
            assert refresh_locked.wait(timeout=5)
            archive_future = executor.submit(archive_monitor)
            assert archive_started.wait(timeout=5)
            with pytest.raises(FutureTimeoutError):
                archive_future.result(timeout=1)
            allow_refresh_commit.set()
            refresh_future.result(timeout=5)
            archive_future.result(timeout=5)

        with SessionLocal() as db:
            persisted_source = db.get(SearchSource, source_id)
            persisted_session = db.get(VintedSession, session_id)
            assert persisted_source is not None
            assert persisted_source.archived_at is not None
            assert persisted_session is not None
            assert persisted_session.status == "invalid"
            assert not any(prepared_context_flags(prepared_context_from_session(persisted_session, Settings())).values())
    finally:
        allow_refresh_commit.set()
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
                proxy_identity_generation=effective_proxy_identity_generation(proxy),
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
        assert refreshed.datadome == "fresh-datadome-token"
        assert refreshed.cf_bm == "fresh-cf-bm-token"
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
            get_ready_vinted_session(db, other_source, proxy, settings=Settings())

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
            egress=_test_proxy_egress(db),
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


def test_manual_run_reuses_active_session_without_closing_it(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.monitor_mode = "manual"
        source.monitor_started_at = datetime(2026, 7, 4, 8, 0, tzinfo=UTC)
        session = db.scalar(
            select(MonitorSession).where(
                MonitorSession.source_id == source_id,
                MonitorSession.stopped_at.is_(None),
            )
        )
        assert session is not None
        session_id = session.id
        db.commit()

    with SessionLocal() as db:
        run = execute_manual_run(
            db,
            source_id,
            provider=FakeSuccessProvider(item_count=1),
            seen_cache=FakeSeenCache(),
            egress=_test_proxy_egress(db),
        )
        source = db.get(SearchSource, source_id)
        session = db.get(MonitorSession, session_id)

        assert run.status == SUCCESS
        assert source is not None
        assert source.is_active is True
        assert source.monitor_started_at == datetime(2026, 7, 4, 8, 0, tzinfo=UTC)
        assert source.monitor_until is None
        assert source.next_run_at is None
        assert source.last_run_at == run.finished_at
        assert session is not None
        assert session.stopped_at is None
        assert run.monitor_session_id == session.id


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
                egress=_test_proxy_egress(db),
            )


def test_internal_baseline_marks_visible_items_without_opportunities(source_id: int) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = False
        source.monitor_mode = "continuous"
        db.query(MonitorSession).filter(MonitorSession.source_id == source_id).delete(synchronize_session=False)
        db.commit()

    cache = FakeSeenCache(baseline_ready=False)
    with SessionLocal() as db:
        run = execute_monitor_baseline(
            db,
            source_id,
            provider=FakeSuccessProvider(item_count=2),
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))
        phases = [event.phase for event in events]

        assert run.status == SUCCESS
        assert run.trigger == "baseline"
        assert run.runtime_metadata["baseline_reason"] == "internal_snapshot"
        assert run.items_found == 0
        assert run.opportunities_created == 0
        assert item_count == 0
        assert opportunity_count == 0
        assert sorted(cache.marked_seen) == ["pytest-run-item-0", "pytest-run-item-1"]
        assert cache.baseline_ready is True
        assert cache.marked_baseline
        assert "baseline_snapshot_seeded" in phases
        assert "filter_passed" not in phases
        assert "opportunity_created" not in phases


def test_internal_baseline_reuses_prepare_probe_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    FakeSessionPreparingProvider.created = []
    monkeypatch.setattr("vinted_monitor.services.runs.CurlCffiVintedCatalogProvider", FakeSessionPreparingProvider)
    runtime_settings = Settings(
        scheduler_enabled=True,
        proxy_sticky_username_template="{username}-sessid-{session_id}",
        vinted_prepared_session_required=True,
    )
    monkeypatch.setattr(
        "vinted_monitor.services.runs.get_settings",
        lambda: runtime_settings,
    )
    cache = FakeSeenCache(baseline_ready=False)
    with SessionLocal() as db:
        proxy = create_proxy_profile(
            db,
            name="pytest baseline prepare proxy",
            scheme="http",
            kind="residential",
            host="proxy.example",
            port=8013,
            username="customer",
            password=None,
            settings=runtime_settings,
        )
        source = SearchSource(
            name="pytest baseline prepare monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id
        proxy_id = proxy.id

    try:
        with SessionLocal() as db:
            run = execute_monitor_baseline(
                db,
                source_id,
                seen_cache=cache,
                egress=RunEgress(
                    mode="proxy",
                    proxy_profile_id=proxy_id,
                    proxy_name="pytest baseline prepare proxy",
                    proxy_kind="residential",
                    proxy_identity_generation=effective_proxy_identity_generation(db.get(ProxyProfile, proxy_id)),
                ),
            )
            events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))
            success_event = next(event for event in events if event.phase == "catalog_search_success")
            session = db.scalar(
                select(VintedSession).where(
                    VintedSession.source_id == source_id,
                    VintedSession.proxy_profile_id == proxy_id,
                )
            )

            assert run.status == SUCCESS
            assert run.items_found == 0
            assert sorted(cache.marked_seen) == ["9100000001", "9100000002"]
            assert success_event.details["provider"]["reused_prepare_probe"] is True
            assert session is not None
            assert session.status == "ready"
            assert session.request_count == 1
            assert len(FakeSessionPreparingProvider.created) == 2
            assert FakeSessionPreparingProvider.created[0].probe_calls == [
                "https://www.vinted.es/catalog?search_text=&order=newest_first"
            ]
    finally:
        cleanup_source(source_id)


def test_recurring_monitor_run_without_baseline_fails_before_catalog_and_preserves_mode(source_id: int) -> None:
    provider = FakeSuccessProvider(item_count=1)
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.monitor_mode = "continuous"
        db.commit()
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=FakeSeenCache(baseline_ready=False),
            egress=_test_proxy_egress(db),
        )
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))
        source = db.get(SearchSource, source_id)
        session = db.get(MonitorSession, run.monitor_session_id)

        assert run.status == FAILED
        assert "inicia una nueva sesion" in (run.error_message or "")
        assert provider.detail_calls == []
        assert any(event.phase == "baseline_required" for event in events)
        assert all(event.phase != "catalog_search_start" for event in events)
        assert next(event for event in events if event.phase == "run_failed").details["recovery_action"] == "start_new_session"
        closure_events = [event for event in events if event.phase == "monitor_session_closed"]
        assert len(closure_events) == 1
        assert closure_events[0].details["reason"] == "baseline_required"
        assert source is not None
        assert source.is_active is False
        assert source.monitor_mode == "continuous"
        assert session is not None
        assert session.stopped_at is not None
        assert session.stop_reason == "baseline_required"


def test_monitor_run_skips_existing_opportunity_before_filters(source_id: int) -> None:
    provider = FakeSuccessProvider(item_count=1)
    cache = FakeSeenCache()
    with SessionLocal() as db:
        first_run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_proxy_egress(db))
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = True
        db.commit()
        second_cache = FakeSeenCache()
        second_run = execute_monitor_run(db, source_id, provider=provider, seen_cache=second_cache, egress=_test_proxy_egress(db))
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == second_run.id).order_by(RunEvent.id.asc())))

        assert first_run.opportunities_created == 1
        assert second_run.items_found == 0
        assert second_run.opportunities_created == 0
        assert any(event.phase == "candidate_existing_opportunity_skipped" for event in events)
        assert all(event.phase != "filter_passed" for event in events)


def test_monitor_run_api_requires_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        started_at = datetime.now(UTC)
        source = SearchSource(
            name="pytest api baseline required monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            monitor_mode="manual",
            monitor_started_at=started_at,
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        session = start_monitor_session(db, source, started_at=started_at, allow_manual=True)
        assert session is not None
        session_id = session.id
        db.commit()
        source_id = source.id

    _enable_test_proxy_runtime(monkeypatch)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache(baseline_ready=False))
    try:
        response = client.post(f"/api/monitors/{source_id}/runs")

        assert response.status_code == 201
        assert response.json()["status"] == FAILED
        assert "inicia una nueva sesion" in response.json()["error_message"]
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            session = db.get(MonitorSession, session_id)
            run = db.get(Run, response.json()["id"])
            assert source is not None
            assert source.is_active is False
            assert session is not None
            assert session.stopped_at is not None
            assert session.stop_reason == "baseline_required"
            assert run is not None
            assert run.monitor_session_id == session.id
    finally:
        cleanup_source(source_id)


def test_standalone_baseline_contract_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest api baseline monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    try:
        response = client.post(f"/api/monitors/{source_id}/baseline")

        assert response.status_code == 404
        assert "/api/monitors/{monitor_id}/baseline" not in client.get("/openapi.json").json()["paths"]
        monitors_response = client.get("/api/monitors")
        monitor = next(entry for entry in monitors_response.json() if entry["id"] == source_id)
        assert "baseline_ready" not in monitor
        assert "baseline_policy_hash" not in monitor
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Run).where(Run.source_id == source_id)) == 0
    finally:
        cleanup_source(source_id)


def test_monitor_start_rejects_existing_monitor_with_unsupported_url_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest unsupported legacy monitor",
            url="https://www.vinted.es/catalog?catalog[]=76&color_ids[]=12",
            normalized_query={"catalog[]": ["76"], "color_ids[]": ["12"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    _enable_test_proxy_runtime(monkeypatch)
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

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
    client = authenticated_test_client()
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
        db.flush()
        if endpoint == "runs":
            started_at = datetime.now(UTC)
            source.is_active = True
            source.monitor_started_at = started_at
            start_monitor_session(db, source, started_at=started_at, allow_manual=True)
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


def test_monitor_run_api_reuses_active_manual_session(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        started_at = datetime.now(UTC)
        source = SearchSource(
            name="pytest api manual monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=True,
            monitor_mode="manual",
            monitor_started_at=started_at,
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        session = start_monitor_session(db, source, started_at=started_at, allow_manual=True)
        assert session is not None
        session_id = session.id
        db.commit()
        source_id = source.id

    _enable_test_proxy_runtime(monkeypatch)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=1)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/runs")

        assert response.status_code == 201
        assert response.json()["status"] == SUCCESS
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            session = db.get(MonitorSession, session_id)
            run = db.get(Run, response.json()["id"])
            assert source is not None
            assert source.is_active is True
            assert source.monitor_until is None
            assert source.next_run_at is None
            assert session is not None
            assert session.stopped_at is None
            assert session.stop_reason is None
            assert run is not None
            assert run.monitor_session_id == session.id
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_monitor_run_api_rejects_inactive_manual_monitor() -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest api inactive manual monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    try:
        response = client.post(f"/api/monitors/{source_id}/runs")

        assert response.status_code == 409
        assert "sesion manual" in response.json()["detail"]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Run).where(Run.source_id == source_id)) == 0
            assert db.scalar(select(func.count()).select_from(MonitorSession).where(MonitorSession.source_id == source_id)) == 0
    finally:
        cleanup_source(source_id)


def test_monitor_run_api_returns_conflict_when_no_egress_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        active_proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.is_active.is_(True))))
        if active_proxy_ids:
            db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                {ProxyProfile.is_active: False},
                synchronize_session=False,
            )
        started_at = datetime.now(UTC)
        source = SearchSource(
            name="pytest no egress capacity",
            url="https://www.vinted.es/catalog?search_text=no-egress",
            normalized_query={"search_text": ["no-egress"]},
            is_active=True,
            monitor_mode="manual",
            monitor_started_at=started_at,
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        start_monitor_session(db, source, started_at=started_at, allow_manual=True)
        db.commit()
        source_id = source.id

    monkeypatch.setattr(
        "vinted_monitor.services.runs._provider_for_egress",
        lambda *_args, **_kwargs: pytest.fail("provider construction must not run without a proxy"),
    )
    try:
        response = client.post(f"/api/monitors/{source_id}/runs")

        assert response.status_code == 409
        assert "No proxy is available" in response.json()["detail"]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Run).where(Run.source_id == source_id)) == 0
            assert db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.source_id == source_id)) == 0
            assert db.scalar(select(func.count()).select_from(VintedSession).where(VintedSession.source_id == source_id)) == 0
    finally:
        with SessionLocal() as db:
            if active_proxy_ids:
                db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                    {ProxyProfile.is_active: True},
                    synchronize_session=False,
                )
                db.commit()
        cleanup_source(source_id)


def test_monitor_start_api_in_manual_mode_baselines_once_and_opens_session(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
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
        db.commit()
        source_id = source.id

    _enable_test_proxy_runtime(monkeypatch)
    cache = FakeSeenCache(baseline_ready=False)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=1)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: cache)
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == SUCCESS
        assert body["trigger"] == "baseline"
        assert body["items_found"] == 0
        assert "items_new" not in body
        assert body["opportunities_created"] == 0
        assert body["monitor_session_id"] is None
        second_response = client.post(f"/api/monitors/{source_id}/start")
        assert second_response.status_code == 409
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            sessions = list(db.scalars(select(MonitorSession).where(MonitorSession.source_id == source_id)))
            runs = list(db.scalars(select(Run).where(Run.source_id == source_id)))
            item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
            opportunity_count = db.scalar(
                select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id)
            )
            assert source is not None
            assert source.is_active is True
            assert source.monitor_started_at is not None
            assert source.monitor_until is None
            assert source.next_run_at is None
            assert len(sessions) == 1
            assert sessions[0].stopped_at is None
            assert len(runs) == 1
            assert runs[0].id == body["id"]
            assert runs[0].monitor_session_id is None
            assert runs[0].runtime_metadata["baseline_reason"] == "session_start"
            assert cache.marked_seen == ["pytest-run-item-0"]
            assert cache.baseline_ready is True
            assert item_count == 0
            assert opportunity_count == 0
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_monitor_start_api_baseline_failure_leaves_manual_monitor_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest api manual baseline failure",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    _enable_test_proxy_runtime(monkeypatch)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSearchFailingProvider()
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache(baseline_ready=False))
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

        assert response.status_code == 201
        assert response.json()["status"] == FAILED
        assert response.json()["trigger"] == "baseline"
        assert "session-secret" not in (response.json()["error_message"] or "")
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            run = db.get(Run, response.json()["id"])
            assert source is not None
            assert source.is_active is False
            assert source.monitor_started_at is None
            assert source.monitor_until is None
            assert source.next_run_at is None
            assert run is not None
            assert run.monitor_session_id is None
            assert run.runtime_metadata["baseline_reason"] == "session_start"
            assert db.scalar(select(func.count()).select_from(MonitorSession).where(MonitorSession.source_id == source_id)) == 0
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


@pytest.mark.parametrize(
    ("monitor_mode", "duration_minutes", "allowed_windows"),
    [
        ("continuous", None, []),
        ("duration", 5, []),
        ("window", None, ["00:00-12:00", "12:00-00:00"]),
    ],
)
def test_recurring_monitor_start_baselines_then_opens_session_with_future_deadline(
    monkeypatch: pytest.MonkeyPatch,
    monitor_mode: str,
    duration_minutes: int | None,
    allowed_windows: list[str],
) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        source = SearchSource(
            name=f"pytest api {monitor_mode} start monitor",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=False,
            monitor_mode=monitor_mode,
            duration_minutes=duration_minutes,
            scheduler_config={"interval_seconds": 60, "jitter_percent": 0, "allowed_windows": allowed_windows},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    _enable_test_proxy_runtime(monkeypatch)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=1)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

        assert response.status_code == 201
        second_response = client.post(f"/api/monitors/{source_id}/start")
        assert second_response.status_code == 409
        assert "already active" in second_response.json()["detail"]
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            run = db.get(Run, response.json()["id"])
            source_runs = list(db.scalars(select(Run).where(Run.source_id == source_id)))
            assert source is not None
            assert source.is_active is True
            assert source.monitor_started_at is not None
            assert source.next_run_at == source.monitor_started_at + timedelta(seconds=60)
            assert source.monitor_until == (
                source.monitor_started_at + timedelta(minutes=duration_minutes)
                if duration_minutes is not None
                else None
            )
            assert session is not None
            assert session.stopped_at is None
            assert run is not None
            assert run.trigger == "baseline"
            assert run.status == SUCCESS
            assert run.finished_at is not None
            assert source.monitor_started_at > run.finished_at
            assert run.opportunities_created == 0
            assert run.monitor_session_id is None
            assert [entry.id for entry in source_runs] == [run.id]
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_concurrent_recurring_activation_does_not_exceed_initial_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    settings = _enable_test_proxy_runtime(monkeypatch)
    source_ids: list[int] = []

    first_baseline_reached_catalog = Event()
    release_first_baseline = Event()

    with SessionLocal() as db:
        scheduler_setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
        scheduler_setting_existed = scheduler_setting is not None
        original_scheduler_value = deepcopy(scheduler_setting.value) if scheduler_setting is not None else None
        active_proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.is_active.is_(True))))

    try:
        with SessionLocal() as db:
            if active_proxy_ids:
                db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                    {ProxyProfile.is_active: False},
                    synchronize_session=False,
                )
            update_scheduler_config(
                db,
                {"max_concurrent_runs": 1},
                settings,
            )
            for index in range(2):
                source = SearchSource(
                    name=f"pytest concurrent initial capacity {index}",
                    url=f"https://www.vinted.es/catalog?search_text=initial-capacity-{index}",
                    normalized_query={"search_text": [f"initial-capacity-{index}"]},
                    is_active=False,
                    monitor_mode="continuous",
                    scheduler_config={"interval_seconds": 60, "jitter_percent": 0, "allowed_windows": []},
                )
                db.add(source)
                db.flush()
                source_ids.append(source.id)
            # Persist the shared fake egress before either concurrent request
            # enters its admission transaction. Creating it lazily in the
            # first request would make the second wait on the uncommitted
            # unique proxy row instead of exercising the scheduler lock.
            _test_proxy_egress(db)
            db.commit()

        # Use the production selector for this concurrency assertion: the
        # already committed baseline run must consume the only proxy slot.
        monkeypatch.setattr("vinted_monitor.api.main.choose_run_egress", choose_scheduler_run_egress)
        monkeypatch.setattr("vinted_monitor.services.runs.choose_run_egress", choose_scheduler_run_egress)

        class BlockingBaselineProvider(FakeSuccessProvider):
            def search(self, source, page=None):
                first_baseline_reached_catalog.set()
                assert release_first_baseline.wait(timeout=5)
                return super().search(source, page)

        app.dependency_overrides[get_manual_run_provider] = lambda: BlockingBaselineProvider(item_count=0)
        monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: FakeSeenCache())

        # Keep password hashing and session bootstrap outside the scheduler
        # concurrency window. The assertion below is about admission locking,
        # not first-use authentication latency or test ordering.
        with authenticated_test_client() as client:
            assert client.get("/api/monitors").status_code == 200

        def activate(source_id: int) -> tuple[int, str | None]:
            with authenticated_test_client() as client:
                response = client.post(f"/api/monitors/{source_id}/start")
                detail = response.json().get("detail") if response.status_code != 201 else None
                return response.status_code, detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(activate, source_ids[0])
            if not first_baseline_reached_catalog.wait(timeout=5):
                assert first_future.done(), "first activation did not return or reach baseline catalog search"
                pytest.fail(f"first activation returned before baseline catalog search: {first_future.result()}")
            second_future = executor.submit(activate, source_ids[1])
            second_response = second_future.result(timeout=5)
            assert second_response == (409, "No proxy is available for country ES")
            release_first_baseline.set()
            responses = [first_future.result(timeout=5), second_future.result(timeout=5)]

        assert sorted(status for status, _detail in responses) == [201, 409]
        assert [detail for status, detail in responses if status == 409] == [
            "No proxy is available for country ES"
        ]
        with SessionLocal() as db:
            sources = list(db.scalars(select(SearchSource).where(SearchSource.id.in_(source_ids))))
            runs = list(db.scalars(select(Run).where(Run.source_id.in_(source_ids))))
            sessions = list(db.scalars(select(MonitorSession).where(MonitorSession.source_id.in_(source_ids))))
            active_sources = [source for source in sources if source.is_active]
            inactive_sources = [source for source in sources if not source.is_active]
            assert len(active_sources) == 1
            assert len(inactive_sources) == 1
            assert active_sources[0].next_run_at is not None
            assert inactive_sources[0].monitor_started_at is None
            assert inactive_sources[0].next_run_at is None
            assert len(runs) == 1
            assert len(sessions) == 1
            assert runs[0].source_id == active_sources[0].id
            assert sessions[0].source_id == active_sources[0].id
            assert runs[0].trigger == "baseline"
            assert runs[0].monitor_session_id is None
    finally:
        release_first_baseline.set()
        app.dependency_overrides.clear()
        try:
            for source_id in source_ids:
                cleanup_source(source_id)
        finally:
            with SessionLocal() as db:
                scheduler_setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
                if scheduler_setting_existed:
                    if scheduler_setting is None:
                        scheduler_setting = AppSetting(key=SCHEDULER_SETTING_KEY, value={})
                        db.add(scheduler_setting)
                    scheduler_setting.value = original_scheduler_value or {}
                elif scheduler_setting is not None:
                    db.delete(scheduler_setting)
                if active_proxy_ids:
                    db.query(ProxyProfile).filter(ProxyProfile.id.in_(active_proxy_ids)).update(
                        {ProxyProfile.is_active: True},
                        synchronize_session=False,
                    )
                db.commit()


@pytest.mark.parametrize(
    ("admission_error", "expected_status"),
    [
        (SchedulerUnavailableError("synthetic post-baseline worker loss"), 503),
        (SchedulerCapacityError("synthetic post-baseline capacity loss"), 409),
    ],
)
def test_recurring_monitor_start_preserves_successful_baseline_when_postflight_fails(
    monkeypatch: pytest.MonkeyPatch,
    admission_error: Exception,
    expected_status: int,
) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest postflight admission loss",
            url="https://www.vinted.es/catalog?search_text=postflight-admission-loss",
            normalized_query={"search_text": ["postflight-admission-loss"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 60, "jitter_percent": 0, "allowed_windows": []},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    _enable_test_proxy_runtime(monkeypatch)
    cache = FakeSeenCache(baseline_ready=False)
    monkeypatch.setattr("vinted_monitor.services.runs.get_seen_cache", lambda: cache)
    app.dependency_overrides[get_manual_run_provider] = lambda: FakeSuccessProvider(item_count=2)
    monkeypatch.setattr(
        "vinted_monitor.services.runs.ensure_scheduler_can_activate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(admission_error),
    )
    try:
        response = client.post(f"/api/monitors/{source_id}/start")

        assert response.status_code == expected_status
        assert response.json()["detail"] == str(admission_error)
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            sessions = list(db.scalars(select(MonitorSession).where(MonitorSession.source_id == source_id)))
            runs = list(db.scalars(select(Run).where(Run.source_id == source_id)))
            assert source is not None
            assert source.is_active is False
            assert source.monitor_started_at is None
            assert source.monitor_until is None
            assert source.next_run_at is None
            assert sessions == []
            assert len(runs) == 1
            assert runs[0].trigger == "baseline"
            assert runs[0].status == SUCCESS
            assert runs[0].items_found == 0
            assert runs[0].opportunities_created == 0
            assert runs[0].monitor_session_id is None
            assert cache.baseline_ready is True
    finally:
        app.dependency_overrides.clear()
        cleanup_source(source_id)


def test_monitor_stop_commits_inactive_session_before_ready_task_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
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

    cleanup_observations: list[int] = []

    def observe_committed_source(ready_source_id: int) -> None:
        with SessionLocal() as verification_db:
            persisted_source = verification_db.get(SearchSource, ready_source_id)
            persisted_session = verification_db.scalar(
                select(MonitorSession).where(MonitorSession.source_id == ready_source_id)
            )
            assert persisted_source is not None
            assert persisted_source.is_active is False
            assert persisted_session is not None
            assert persisted_session.stopped_at is not None
            assert persisted_session.stop_reason == "stopped"
        cleanup_observations.append(ready_source_id)

    monkeypatch.setattr(
        "vinted_monitor.services.search_sources._cancel_ready_source_task",
        observe_committed_source,
    )
    try:
        response = client.post(f"/api/monitors/{source_id}/stop")

        assert response.status_code == 200
        assert cleanup_observations == [source_id]
        with SessionLocal() as db:
            session = db.scalar(select(MonitorSession).where(MonitorSession.source_id == source_id))
            assert session is not None
            assert session.stopped_at is not None
            assert session.stop_reason == "stopped"
    finally:
        cleanup_source(source_id)


@pytest.mark.parametrize("run_status", ["running", "finalizing"])
def test_monitor_stop_drains_non_terminal_session_run(run_status: str) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    started_at = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    with SessionLocal() as db:
        source = SearchSource(
            name=f"pytest stop blocked {run_status}",
            url="https://www.vinted.es/catalog?search_text=stop-blocked",
            normalized_query={"search_text": ["stop-blocked"]},
            is_active=True,
            monitor_mode="manual",
            monitor_started_at=started_at,
            monitor_until=started_at + timedelta(minutes=5),
            next_run_at=started_at + timedelta(minutes=1),
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        session = start_monitor_session(db, source, started_at=started_at, allow_manual=True)
        assert session is not None
        run = Run(
            source_id=source.id,
            monitor_session_id=session.id,
            status=run_status,
            trigger="manual",
            runtime_metadata={},
        )
        db.add(run)
        db.commit()
        source_id = source.id
        session_id = session.id
        run_id = run.id

    try:
        response = client.post(f"/api/monitors/{source_id}/stop")

        assert response.status_code == 200
        assert response.json()["is_active"] is False
        assert response.json()["monitor_started_at"] is None
        assert response.json()["monitor_until"] is None
        assert response.json()["next_run_at"] is None
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            session = db.get(MonitorSession, session_id)
            run = db.get(Run, run_id)
            assert source is not None
            assert source.is_active is False
            assert source.monitor_started_at is None
            assert source.monitor_until is None
            assert source.next_run_at is None
            assert session is not None
            assert session.stopped_at is None
            assert session.stop_reason is None
            assert run is not None
            assert run.status == run_status
            assert run.finished_at is None
    finally:
        cleanup_source(source_id)


def test_monitor_stop_still_rejects_sessionless_baseline_run() -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest stop blocked baseline",
            url="https://www.vinted.es/catalog?search_text=stop-blocked-baseline",
            normalized_query={"search_text": ["stop-blocked-baseline"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.flush()
        run = Run(
            source_id=source.id,
            monitor_session_id=None,
            status="running",
            trigger="baseline",
            runtime_metadata={},
        )
        db.add(run)
        db.commit()
        source_id = source.id
        run_id = run.id

    try:
        response = client.post(f"/api/monitors/{source_id}/stop")

        assert response.status_code == 409
        assert "ejecucion en curso" in response.json()["detail"]
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            run = db.get(Run, run_id)
            assert source is not None
            assert source.is_active is False
            assert run is not None
            assert run.status == "running"
            assert run.finished_at is None
    finally:
        cleanup_source(source_id)


def test_failed_run_preserves_failure_and_drain_waits_for_finalizing_sibling() -> None:
    cleanup_source(None)
    client = authenticated_test_client()
    started_at = datetime.now(UTC) - timedelta(minutes=1)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest failed stop drain",
            url="https://www.vinted.es/catalog?search_text=failed-stop-drain",
            normalized_query={"search_text": ["failed-stop-drain"]},
            is_active=True,
            monitor_mode="continuous",
            monitor_started_at=started_at,
            next_run_at=datetime.now(UTC) + timedelta(minutes=1),
            scheduler_config={"interval_seconds": 60, "jitter_percent": 0, "allowed_windows": []},
        )
        db.add(source)
        db.flush()
        session = start_monitor_session(db, source, started_at=started_at, allow_manual=True)
        assert session is not None
        finalizing_run = Run(
            source_id=source.id,
            monitor_session_id=session.id,
            status="finalizing",
            trigger="scheduler",
            runtime_metadata={},
        )
        db.add(finalizing_run)
        db.flush()
        run = Run(
            source_id=source.id,
            monitor_session_id=session.id,
            status="running",
            trigger="scheduler",
            runtime_metadata={},
        )
        db.add(run)
        db.commit()
        source_id = source.id
        session_id = session.id
        finalizing_run_id = finalizing_run.id
        run_id = run.id

    try:
        response = client.post(f"/api/monitors/{source_id}/stop")
        assert response.status_code == 200
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            run = db.get(Run, run_id)
            assert source is not None
            assert run is not None
            _record_failed_run(db, run, source, RuntimeError("synthetic terminal failure"))

        with SessionLocal() as db:
            session = db.get(MonitorSession, session_id)
            run = db.get(Run, run_id)
            assert session is not None
            assert run is not None
            assert run.status == FAILED
            assert run.error_message == "synthetic terminal failure"
            assert run.finished_at is not None
            assert session.stopped_at is None
            assert session.stop_reason is None

            source = db.get(SearchSource, source_id)
            finalizing_run = db.get(Run, finalizing_run_id)
            assert source is not None
            assert finalizing_run is not None
            _complete_finalizing_run(
                db,
                finalizing_run,
                source,
                close_session_on_finish=False,
                reconciled=True,
            )
            db.commit()

        with SessionLocal() as db:
            session = db.get(MonitorSession, session_id)
            finalizing_run = db.get(Run, finalizing_run_id)
            assert session is not None
            assert finalizing_run is not None
            assert finalizing_run.status == SUCCESS
            assert finalizing_run.finished_at is not None
            assert session.stopped_at == finalizing_run.finished_at
            assert session.stop_reason == "stopped"
    finally:
        cleanup_source(source_id)


def test_recurring_monitor_baseline_failure_leaves_session_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    cleanup_source(None)
    client = authenticated_test_client()
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
        db.commit()
        source_id = source.id

    _enable_test_proxy_runtime(monkeypatch)
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
            assert source.is_active is False
            assert source.monitor_started_at is None
            assert source.next_run_at is None
            assert session is None
            assert run is not None
            assert run.trigger == "baseline"
            assert run.monitor_session_id is None
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
                    monitor_session_id=None,
                    status=SUCCESS,
                    trigger="baseline",
                    started_at=datetime(2026, 7, 4, 8, 5, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 4, 8, 6, tzinfo=UTC),
                    items_found=0,
                    items_filter_passed=0,
                    items_discarded_by_filters=0,
                    items_filter_pending=0,
                    opportunities_created=0,
                    runtime_metadata={"baseline_run": True},
                ),
                Run(
                    source_id=source.id,
                    monitor_session_id=session.id,
                    status=SUCCESS,
                    trigger="manual",
                    started_at=datetime(2026, 7, 4, 8, 15, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 4, 8, 16, tzinfo=UTC),
                    items_found=2,
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
                    items_found=1,
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
        assert stats.session_summary.items_found == 3
        assert stats.historical_summary.runs_count == 2
        assert stats.historical_summary.items_found == 3
        assert stats.historical_summary.opportunities_created == 3
        assert stats.bucket_label == "1 h"
        assert stats.bucket_seconds == 3600
        chart_hits = [point for point in stats.chart_points if point.items_found > 0]
        assert [point.items_found for point in chart_hits] == [2, 1]
    finally:
        cleanup_source(source_id)


def test_runs_endpoint_filters_by_source_id() -> None:
    cleanup_source(None)
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
                    items_found=1,
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
        assert body["session_summary"]["items_found"] == 1
        assert "items_new" not in body["session_summary"]
        assert body["historical_summary"]["sessions_count"] == 2
        assert body["historical_summary"]["items_found"] == 3
    finally:
        cleanup_source(source_id)


def test_seen_cache_hit_skips_detail_and_database_writes(source_id: int) -> None:
    cache = FakeSeenCache(initially_seen={"pytest-run-item-0"})
    provider = FakeSuccessProvider(item_count=1)

    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_proxy_egress(db))
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))

        assert run.status == SUCCESS
        assert run.items_found == 0
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
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_proxy_egress(db))
        item_count = db.scalar(select(func.count()).select_from(Item).where(Item.vinted_item_id.like("pytest-run-item%")))
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))

        assert run.items_found == 1
        assert run.items_discarded_by_filters == 1
        assert run.opportunities_created == 0
        assert item_count == 0
        assert opportunity_count == 0


def test_only_description_filters_and_optional_decision_data_do_not_block_opportunity(source_id: int) -> None:
    class TitleOnlyMatchProvider(FakeSuccessProvider):
        def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
            result = super().search(source, page)
            return replace(
                result,
                items=[replace(result.items[0], title="descarte solo en titulo", view_count=0)],
            )

        def fetch_detail(
            self,
            candidate: CatalogItemCandidate,
            *,
            referer_url: str | None = None,
        ) -> CatalogItemDetail:
            self.detail_calls.append(candidate.vinted_item_id)
            return CatalogItemDetail(
                vinted_item_id=candidate.vinted_item_id,
                description="Descripcion permitida",
                photos=[f"https://images.example.test/{candidate.vinted_item_id}.webp"],
                availability_flags={
                    "state": "reserved",
                    "reason_codes": ["reserved"],
                    "source": "public_snapshot",
                },
            )

    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.filter_definition = {"blacklist_terms": ["descarte"]}
        db.commit()

    cache = FakeSeenCache()
    provider = TitleOnlyMatchProvider(item_count=1)
    with SessionLocal() as db:
        run = execute_monitor_run(db, source_id, provider=provider, seen_cache=cache, egress=_test_proxy_egress(db))
        opportunity = db.scalar(select(Opportunity).where(Opportunity.source_id == source_id))
        item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id.asc())))
        config_event = next(event for event in events if event.phase == "run_config_resolved")
        filter_event = next(event for event in events if event.phase == "candidate_filter_decision")

        assert run.status == SUCCESS
        assert run.runtime_metadata["evaluation_contract"] == "description_only_v2"
        assert run.items_discarded_by_filters == 0
        assert run.opportunities_created == 1
        assert opportunity is not None
        assert item is not None
        assert item.view_count == 0
        assert item.shipping_price_amount is None
        assert item.buyer_protection_fee_amount is None
        assert item.total_price_amount is None
        assert item.availability_flags["state"] == "reserved"
        assert config_event.details["evaluation_contract"] == "description_only_v2"
        assert config_event.details["filter_scope"] == "description"
        assert config_event.details["runtime_config"]["detail_early_filter_mode"] == "enforced"
        assert config_event.details["runtime_config"]["detail_head_max_bytes"] == 131_072
        assert filter_event.details["filter_scope"] == "description"
        assert filter_event.details["match_count"] == 0


def test_detail_failure_retries_once_in_run_then_closes_candidate(
    source_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.filter_definition = {"blacklist_terms": ["nunca"]}
        db.commit()

    cache = FakeSeenCache()
    sleep_calls: list[float] = []
    monkeypatch.setattr("vinted_monitor.services.runs.time.sleep", sleep_calls.append)
    with SessionLocal() as db:
        provider = FakeFailingDetailProvider(item_count=1)
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )
        opportunity = db.scalar(select(Opportunity).where(Opportunity.source_id == source_id))
        item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))
        error_event = db.scalar(
            select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "detail_fetch_error").order_by(RunEvent.id.desc())
        )

        assert run.items_found == 1
        assert run.opportunities_created == 0
        assert run.items_filter_pending == 1
        assert opportunity is None
        assert item is None
        assert provider.detail_calls == ["pytest-run-item-0", "pytest-run-item-0"]
        assert sleep_calls == [2.0]
        assert error_event is not None
        assert "session-secret" not in (error_event.message or "")
        assert error_event.details["attempt"] == 2
        assert error_event.details["retry_exhausted"] is True
        assert "pytest-run-item-0" in cache.seen
        assert cache.processing == set()


def test_detail_retry_can_create_opportunity_in_the_same_run(
    source_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailOnceProvider(FakeSuccessProvider):
        def fetch_detail(
            self,
            candidate: CatalogItemCandidate,
            *,
            referer_url: str | None = None,
        ) -> CatalogItemDetail:
            if not self.detail_calls:
                self.detail_calls.append(candidate.vinted_item_id)
                raise RuntimeError("temporary detail failure")
            return super().fetch_detail(candidate, referer_url=referer_url)

    cache = FakeSeenCache()
    provider = FailOnceProvider(item_count=1)
    sleep_calls: list[float] = []
    monkeypatch.setattr("vinted_monitor.services.runs.time.sleep", sleep_calls.append)

    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )
        opportunity = db.scalar(select(Opportunity).where(Opportunity.source_id == source_id))
        retry_event = db.scalar(
            select(RunEvent).where(RunEvent.run_id == run.id, RunEvent.phase == "detail_retry_scheduled")
        )

        assert run.items_filter_pending == 0
        assert run.opportunities_created == 1
        assert opportunity is not None
        assert provider.detail_calls == ["pytest-run-item-0", "pytest-run-item-0"]
        assert sleep_calls == [2.0]
        assert retry_event is not None
        assert retry_event.details["delay_seconds"] == 2.0
        assert "pytest-run-item-0" in cache.seen


def test_detail_budget_closes_candidate_without_deferred_work(
    source_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vinted_monitor.services.scheduler import get_scheduler_runtime_config as real_runtime_config

    monkeypatch.setattr(
        "vinted_monitor.services.runs.get_scheduler_runtime_config",
        lambda db, settings: replace(
            real_runtime_config(db, settings),
            detail_max_candidates_per_run=1,
        ),
    )
    cache = FakeSeenCache()
    provider = FakeSuccessProvider(item_count=2)

    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )

    assert run.opportunities_created == 1
    assert run.items_filter_pending == 1
    assert provider.detail_calls == ["pytest-run-item-0"]
    assert "pytest-run-item-0" in cache.seen
    assert "pytest-run-item-1" in cache.seen
    assert cache.processing == set()


def test_datadome_mid_batch_rolls_back_and_discards_claimed_work(source_id: int) -> None:
    class MidBatchChallengeProvider(FakeSuccessProvider):
        def fetch_detail(
            self,
            candidate: CatalogItemCandidate,
            *,
            referer_url: str | None = None,
        ) -> CatalogItemDetail:
            self.detail_calls.append(candidate.vinted_item_id)
            if candidate.vinted_item_id.endswith("-1"):
                raise DataDomeChallengeError("DataDome challenge in detail batch")
            return CatalogItemDetail(
                vinted_item_id=candidate.vinted_item_id,
                description="Detalle valido",
                photos=[f"https://images.example.test/{candidate.vinted_item_id}.webp"],
            )

    cache = FakeSeenCache()
    provider = MidBatchChallengeProvider(item_count=3)
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )

    with SessionLocal() as db:
        opportunity_count = db.scalar(select(func.count()).select_from(Opportunity).where(Opportunity.source_id == source_id))

    assert opportunity_count == 0
    assert run.status == FAILED
    assert run.items_found == 3
    assert (run.runtime_metadata or {}).get("failure_kind") == "datadome_challenge"
    assert provider.detail_calls == ["pytest-run-item-0", "pytest-run-item-1"]
    assert cache.seen == set()
    assert cache.processing == set()


def test_detail_session_context_failure_is_fail_stop_without_retry(
    source_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidSessionProvider(FakeSuccessProvider):
        def fetch_detail(
            self,
            candidate: CatalogItemCandidate,
            *,
            referer_url: str | None = None,
        ) -> CatalogItemDetail:
            self.detail_calls.append(candidate.vinted_item_id)
            raise VintedCatalogSessionContextError("prepared detail context became invalid")

    cache = FakeSeenCache()
    provider = InvalidSessionProvider(item_count=1)
    sleep_calls: list[float] = []
    monkeypatch.setattr("vinted_monitor.services.runs.time.sleep", sleep_calls.append)

    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )

    assert run.status == FAILED
    assert run.items_found == 1
    assert (run.runtime_metadata or {}).get("failure_kind") == "catalog_session_context_invalid"
    assert provider.detail_calls == ["pytest-run-item-0"]
    assert sleep_calls == []
    assert cache.seen == set()
    assert cache.processing == set()


def test_observed_empty_description_is_valid_detail(source_id: int) -> None:
    class EmptyDescriptionProvider(FakeSuccessProvider):
        def fetch_detail(
            self,
            candidate: CatalogItemCandidate,
            *,
            referer_url: str | None = None,
        ) -> CatalogItemDetail:
            self.detail_calls.append(candidate.vinted_item_id)
            return CatalogItemDetail(
                vinted_item_id=candidate.vinted_item_id,
                description="",
                photos=[f"https://images.example.test/{candidate.vinted_item_id}.webp"],
                observed_fields=frozenset({"description", "photos"}),
            )

    cache = FakeSeenCache()
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=EmptyDescriptionProvider(item_count=1),
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )
        item = db.scalar(select(Item).where(Item.vinted_item_id == "pytest-run-item-0"))

        assert run.opportunities_created == 1
        assert item is not None
        assert item.description == ""
        assert "pytest-run-item-0" in cache.seen


def test_missing_required_detail_is_terminal_without_opportunity(source_id: int) -> None:
    class MissingDescriptionProvider(FakeSuccessProvider):
        def fetch_detail(
            self,
            candidate: CatalogItemCandidate,
            *,
            referer_url: str | None = None,
        ) -> CatalogItemDetail:
            self.detail_calls.append(candidate.vinted_item_id)
            return CatalogItemDetail(
                vinted_item_id=candidate.vinted_item_id,
                description=None,
                photos=[f"https://images.example.test/{candidate.vinted_item_id}.webp"],
            )

    cache = FakeSeenCache()
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=MissingDescriptionProvider(item_count=1),
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )
        incomplete_event = db.scalar(
            select(RunEvent).where(
                RunEvent.run_id == run.id,
                RunEvent.phase == "detail_incomplete",
            )
        )

        assert run.items_filter_pending == 1
        assert run.opportunities_created == 0
        assert incomplete_event is not None
        assert incomplete_event.details["missing_required"] == ["description"]
        assert "pytest-run-item-0" in cache.seen


@pytest.mark.parametrize("status_code", [404, 410])
def test_gone_detail_is_terminal_without_retry(
    source_id: int,
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GoneDetailProvider(FakeSuccessProvider):
        def fetch_detail(
            self,
            candidate: CatalogItemCandidate,
            *,
            referer_url: str | None = None,
        ) -> CatalogItemDetail:
            self.detail_calls.append(candidate.vinted_item_id)
            raise VintedItemDetailHTTPError(candidate.vinted_item_id, status_code)

    cache = FakeSeenCache()
    provider = GoneDetailProvider(item_count=1)
    sleep_calls: list[float] = []
    monkeypatch.setattr("vinted_monitor.services.runs.time.sleep", sleep_calls.append)
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=provider,
            seen_cache=cache,
            egress=_test_proxy_egress(db),
        )
        retry_event = db.scalar(
            select(RunEvent).where(
                RunEvent.run_id == run.id,
                RunEvent.phase == "detail_retry_scheduled",
            )
        )

        assert run.items_filter_pending == 1
        assert run.opportunities_created == 0
        assert provider.detail_calls == ["pytest-run-item-0"]
        assert sleep_calls == []
        assert retry_event is None
        assert "pytest-run-item-0" in cache.seen


def test_redis_unavailable_fails_run_and_pauses_monitor(source_id: int) -> None:
    with SessionLocal() as db:
        run = execute_monitor_run(
            db,
            source_id,
            provider=FakeSuccessProvider(item_count=1),
            seen_cache=FakeSeenCache(unavailable=True),
            egress=_test_proxy_egress(db),
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
        started_at = datetime.now(UTC)
        second = SearchSource(
            name="pytest second monitor",
            url="https://www.vinted.es/catalog?search_text=second",
            normalized_query={"search_text": ["second"]},
            is_active=True,
            monitor_started_at=started_at,
            scheduler_config={},
        )
        db.add(second)
        db.flush()
        start_monitor_session(db, second, started_at=started_at, allow_manual=True)
        db.commit()
        second_id = second.id

    try:
        provider = FakeSuccessProvider(item_count=1)
        with SessionLocal() as db:
            first_run = execute_monitor_run(db, source_id, provider=provider, seen_cache=FakeSeenCache(), egress=_test_proxy_egress(db))
            second_run = execute_monitor_run(db, second_id, provider=provider, seen_cache=FakeSeenCache(), egress=_test_proxy_egress(db))
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
