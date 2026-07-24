from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.redaction import redact_sensitive_text, safe_secret_marker
from vinted_monitor.db.models import (
    ErrorLog,
    Item,
    Opportunity,
    ProxyProfile,
    Run,
    RunEvent,
    SearchSource,
    VintedSession,
)
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.transfer_metrics import (
    PROXY_TRAFFIC_METADATA_KEY,
    PROXY_TRANSFER_DETAIL_KEY,
    aggregate_proxy_traffic_estimate,
)
from vinted_monitor.providers.vinted_catalog import (
    DETAIL_BATCH_TELEMETRY_ATTR,
    CurlCffiVintedCatalogProvider,
    DetailFetchOutcome,
    ProxyEgressProbeResult,
    VintedCatalogChallengeError,
    VintedCatalogRateLimitError,
    VintedCatalogSessionContextError,
    VintedCatalogSessionError,
    VintedCatalogTransportError,
    VintedDetailDeferred,
    VintedEgressDiagnosticError,
    VintedEgressRotationError,
    VintedItemEarlyDiscard,
    build_item_detail_navigation_url,
    extract_vinted_item_id,
    parse_catalog_api_payload,
    probe_proxy_egress,
)
from vinted_monitor.services.filters import (
    evaluate_exclusion_filters,
    filter_snapshot_term_count,
    filter_snapshot_terms,
    filter_term_count,
    monitor_filter_snapshot,
)
from vinted_monitor.services.items import (
    apply_item_detail,
    apply_item_detail_data,
    build_transient_catalog_item,
    get_or_persist_catalog_item,
)
from vinted_monitor.services.monitor_sessions import (
    OPENED_MONITOR_SESSION_ID_KEY,
    get_active_monitor_session,
    start_monitor_session,
    stop_active_monitor_session,
)
from vinted_monitor.services.proxies import (
    ProxyProfileEligibilityError,
    effective_proxy_identity_generation,
    list_available_proxy_profiles,
    lock_and_revalidate_proxy_selection,
    lock_proxy_profile_for_selection,
    mark_proxy_challenge_detected,
    mark_proxy_run_failure,
    mark_proxy_run_success,
    mark_proxy_used,
    proxy_url_for_profile,
    proxy_url_with_sticky_session,
)
from vinted_monitor.services.run_events import record_run_event
from vinted_monitor.services.scheduler import (
    RunEgress,
    SchedulerCapacityError,
    SchedulerUnavailableError,
    acquire_initial_run_admission_lock,
    acquire_run_egress_admission_lock,
    choose_run_egress,
    ensure_scheduler_can_activate,
    get_scheduler_runtime_config,
    run_egress_admission_snapshot,
)
from vinted_monitor.services.search_sources import (
    SearchSourceConfigError,
    catalog_filter_compatibility,
    start_source_monitor,
    validate_vinted_catalog_url,
)
from vinted_monitor.services.seen_cache import (
    DetailCandidateStateUpdate,
    SeenCache,
    SeenCacheUnavailableError,
    deserialize_candidate_state_update,
    get_seen_cache,
    serialize_candidate_state_update,
)
from vinted_monitor.services.task_queue import TaskQueueError, pending_tasks
from vinted_monitor.services.vinted_sessions import (
    INCOMPLETE,
    READY,
    VintedSessionRequiredError,
    generate_proxy_session_id,
    get_ready_vinted_session,
    mark_vinted_session_invalid,
    mark_vinted_session_used,
    missing_prepared_context,
    prepared_context_flags,
    save_prepared_vinted_session,
    update_vinted_session_context,
)

RUNNING = "running"
FINALIZING = "finalizing"
SUCCESS = "success"
FAILED = "failed"
MANUAL_TRIGGER = "manual"
SCHEDULER_TRIGGER = "scheduler"
BASELINE_TRIGGER = "baseline"
SESSION_PREPARE_TRIGGER = "session_prepare"
DETAIL_PROBE_TRIGGER = "detail_probe"
EVALUATION_CONTRACT_VERSION = "description_only_v2"
SESSION_ITEM_PASSED = "passed"
SESSION_ITEM_DISCARDED = "discarded"
SESSION_ITEM_PASSED_WITHOUT_FILTERS = "passed_without_filters"
SESSION_ITEM_PASSED_WITHOUT_DETAIL = "passed_without_detail"
SESSION_ITEM_DETAIL_ERROR = "detail_error"
DEFAULT_DETAIL_REQUIRED_FIELDS = frozenset(
    {"title", "description", "brand", "size", "status", "price_amount", "currency", "photos"}
)
STALE_RUN_AFTER = timedelta(minutes=30)
DETAIL_RETRY_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class DetailWorkItem:
    candidate: CatalogItemCandidate


@dataclass(frozen=True)
class MonitorEvaluationResult:
    passed: int
    discarded: int
    pending: int
    opportunities_created: int
    terminal_ids: tuple[str, ...]


class ManualRunProvider(Protocol):
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        """Return public catalog candidates for a monitor run."""

    def fetch_detail(self, candidate: CatalogItemCandidate, *, referer_url: str | None = None) -> CatalogItemDetail:
        """Return public detail data for a candidate."""


class SearchSourceNotFoundError(ValueError):
    pass


class SearchSourceInactiveError(ValueError):
    pass


class RunAlreadyActiveError(ValueError):
    pass


class BaselineRequiredError(ValueError):
    pass


class ProfileSessionAcquisitionExhaustedError(RuntimeError):
    failure_kind = "profile_session_acquisition_exhausted"

    def __init__(self, *, datadome_penalty: bool) -> None:
        super().__init__("Selected proxy profile could not obtain a usable Vinted session after two attempts")
        self.datadome_penalty = datadome_penalty


class SessionAcquisitionExhaustedError(RuntimeError):
    failure_kind = "session_acquisition_exhausted"

    def __init__(self, exhausted_profile_count: int) -> None:
        super().__init__("No eligible proxy profile could obtain a usable Vinted session")
        self.exhausted_profile_count = exhausted_profile_count


class ExplicitSessionRetryError(RuntimeError):
    failure_kind = "explicit_session_retry_failed"


class ExplicitSessionRetryUnavailableError(ValueError):
    pass


def execute_manual_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
    seen_cache: SeenCache | None = None,
    egress: RunEgress | None = None,
) -> Run:
    source = db.scalar(
        select(SearchSource)
        .where(SearchSource.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.monitor_mode != "manual":
        raise SearchSourceConfigError("Ejecutar ahora solo esta disponible para una sesion manual activa")
    if not source.is_active or get_active_monitor_session(db, source.id) is None:
        raise SearchSourceInactiveError("Inicia la sesion manual antes de ejecutar este monitor")
    if _active_source_run_exists(db, source_id=source.id, include_finalizing=True):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")
    return execute_monitor_run(
        db,
        source_id,
        provider=provider,
        trigger=MANUAL_TRIGGER,
        seen_cache=seen_cache,
        require_active=True,
        create_session_for_run=False,
        close_session_on_finish=False,
        egress=egress,
    )


def monitor_policy_hash(source: SearchSource) -> str:
    return _policy_hash(source, monitor_filter_snapshot(source.filter_definition))


def execute_monitor_session_retry(
    db: Session,
    source_id: int,
    *,
    proxy_profile_id: int,
) -> Run:
    source = db.get(SearchSource, source_id)
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.is_active:
        raise RunAlreadyActiveError("Deten la sesion antes de iniciar una nueva")
    settings = get_settings()
    if source.monitor_mode != "manual":
        acquire_initial_run_admission_lock(db)
        ensure_scheduler_can_activate(
            db,
            settings,
            source_id=source.id,
            cooldown_bypass_profile_id=proxy_profile_id,
        )
    _validated_catalog_filter_compatibility(source)
    runtime_config = get_scheduler_runtime_config(db, settings)
    cache = get_seen_cache()
    _resolve_explicit_retry_origin(db, source, proxy_profile_id)
    # Proxy/admission ownership must precede the source row lock acquired by
    # execute_monitor_baseline; identity edits use that same lock order.
    egress = _choose_explicit_retry_egress(
        db,
        proxy_profile_id,
        runtime_config,
        settings,
        cache,
    )
    return execute_monitor_baseline(
        db,
        source_id,
        seen_cache=cache,
        egress=egress,
        activate_session=True,
        explicit_retry_profile_id=proxy_profile_id,
    )


def _validated_catalog_filter_compatibility(source: SearchSource) -> dict[str, Any]:
    try:
        validate_vinted_catalog_url(source.url)
    except ValueError as exc:
        raise SearchSourceConfigError(str(exc)) from exc
    compatibility = catalog_filter_compatibility(source.url)
    if not compatibility.get("compatible", False):
        unsupported = compatibility.get("unsupported") or {}
        unsupported_filters = ", ".join(sorted(str(key) for key in unsupported)) or "desconocidos"
        raise SearchSourceConfigError(
            f"Filtros de URL no soportados por el catalogo rapido: {unsupported_filters}"
        )
    return compatibility


def execute_monitor_baseline(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
    seen_cache: SeenCache | None = None,
    egress: RunEgress | None = None,
    activate_session: bool = False,
    explicit_retry_profile_id: int | None = None,
) -> Run:
    source = db.scalar(
        select(SearchSource)
        .where(SearchSource.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.is_active:
        raise RunAlreadyActiveError("Deten la sesion antes de iniciar una nueva")
    if _active_source_run_exists(db, source_id=source.id, include_finalizing=True):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")
    catalog_filters = _validated_catalog_filter_compatibility(source)

    settings = get_settings()
    runtime_config = get_scheduler_runtime_config(db, settings)
    cache = seen_cache or get_seen_cache()
    explicit_retry_origin_run_id: int | None = None
    rejected_egress_fingerprint: str | None = None
    if explicit_retry_profile_id is not None:
        if (
            provider is not None
            or egress is None
            or egress.proxy_profile_id != explicit_retry_profile_id
            or not activate_session
        ):
            raise ValueError("Explicit session retry requires its admitted egress and activation")
        explicit_retry_origin_run_id, rejected_egress_fingerprint = _resolve_explicit_retry_origin(
            db,
            source,
            explicit_retry_profile_id,
        )
        selected_egress = egress
    else:
        selected_egress = egress or choose_run_egress(db, settings)
    _require_proxy_egress(selected_egress)
    owned_provider = provider is None
    run_provider: ManualRunProvider | None = provider

    filter_snapshot = monitor_filter_snapshot(source.filter_definition)
    policy_hash = _policy_hash(source, filter_snapshot)
    baseline_reason = "session_start" if activate_session else "internal_snapshot"
    run = Run(
        source_id=source.id,
        monitor_session_id=None,
        status=RUNNING,
        trigger=BASELINE_TRIGGER,
        items_found=0,
        items_filter_passed=0,
        items_discarded_by_filters=0,
        items_filter_pending=0,
        opportunities_created=0,
        runtime_metadata={
            **_run_runtime_metadata(source, selected_egress, runtime_config),
            "policy_hash": policy_hash,
            "baseline_run": True,
            "baseline_reason": baseline_reason,
            "explicit_cooldown_retry": explicit_retry_profile_id is not None,
            **(
                {
                    "explicit_retry_origin_run_id": explicit_retry_origin_run_id,
                    "session_acquisition_profile_ids": [explicit_retry_profile_id],
                    "session_acquisition_rejected_egress_fingerprints": {
                        str(explicit_retry_profile_id): rejected_egress_fingerprint
                    }
                    if rejected_egress_fingerprint
                    else {},
                }
                if explicit_retry_profile_id is not None
                else {}
            ),
        },
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if run_provider is not None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run") from exc
    db.refresh(run)
    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")

    if run_provider is None and selected_egress.proxy_profile_id is not None:
        try:
            if explicit_retry_profile_id is not None:
                lock_and_revalidate_proxy_selection(
                    db,
                    selected_egress.proxy_profile_id,
                    selected_egress.proxy_identity_generation,
                    settings,
                    bypass_cooldown=True,
                )
            else:
                lock_and_revalidate_proxy_selection(
                    db,
                    selected_egress.proxy_profile_id,
                    selected_egress.proxy_identity_generation,
                    settings,
                )
        except Exception as exc:
            return _record_failed_run(
                db,
                run,
                source,
                exc,
                penalize_proxy=not isinstance(exc, ProxyProfileEligibilityError),
            )

    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_started",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "trigger": BASELINE_TRIGGER,
            "monitor_mode": source.monitor_mode,
            "filter_count": filter_term_count(source.filter_definition),
            "egress_mode": (run.runtime_metadata or {}).get("egress_mode"),
            "proxy_profile_id": proxy_profile_id,
            "proxy_kind": (run.runtime_metadata or {}).get("proxy_kind"),
            "target_country_code": (run.runtime_metadata or {}).get("target_country_code"),
            "proxy_country_code": (run.runtime_metadata or {}).get("proxy_country_code"),
            "locale": (run.runtime_metadata or {}).get("locale"),
            "screen": (run.runtime_metadata or {}).get("screen"),
            "vinted_screen": (run.runtime_metadata or {}).get("vinted_screen"),
            "browser_profile": (run.runtime_metadata or {}).get("browser_profile"),
            "vinted_session_id": (run.runtime_metadata or {}).get("vinted_session_id"),
            "proxy_sticky_session": (run.runtime_metadata or {}).get("proxy_sticky_session"),
            "baseline_run": True,
            "baseline_reason": baseline_reason,
            "explicit_retry_origin_run_id": explicit_retry_origin_run_id,
        },
    )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_config_resolved",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "monitor_id": source.id,
            "trigger": BASELINE_TRIGGER,
            "monitor_mode": source.monitor_mode,
            "evaluation_contract": EVALUATION_CONTRACT_VERSION,
            "filter_scope": "description",
            "policy_hash": policy_hash,
            "filter_snapshot": filter_snapshot,
            "catalog_filter_compatibility": catalog_filters,
            "runtime_config": {
                "catalog_per_page": runtime_config.catalog_per_page,
                "request_timeout_ms": runtime_config.request_timeout_ms,
            },
            "baseline_run": True,
            "baseline_reason": baseline_reason,
        },
    )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="egress_selected",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "egress_mode": (run.runtime_metadata or {}).get("egress_mode"),
            "proxy_profile_id": proxy_profile_id,
            "proxy_name": (run.runtime_metadata or {}).get("proxy_name"),
            "proxy_kind": (run.runtime_metadata or {}).get("proxy_kind"),
            "target_country_code": (run.runtime_metadata or {}).get("target_country_code"),
            "proxy_country_code": (run.runtime_metadata or {}).get("proxy_country_code"),
            "locale": (run.runtime_metadata or {}).get("locale"),
            "accept_language": (run.runtime_metadata or {}).get("accept_language"),
            "screen": (run.runtime_metadata or {}).get("screen"),
            "vinted_screen": (run.runtime_metadata or {}).get("vinted_screen"),
            "vinted_session_id": (run.runtime_metadata or {}).get("vinted_session_id"),
            "proxy_sticky_session": (run.runtime_metadata or {}).get("proxy_sticky_session"),
        },
    )

    try:
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="redis_check_start",
            proxy_profile_id=proxy_profile_id,
            message="Checking Redis seen cache availability",
            details={"policy_hash": policy_hash, "baseline_run": True},
        )
        cache.require_available()
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="redis_check_success",
            proxy_profile_id=proxy_profile_id,
            message="Redis seen cache is available",
            details={"policy_hash": policy_hash, "baseline_run": True},
        )
        try:
            if run_provider is None:
                if explicit_retry_profile_id is not None:
                    run_provider, result = _search_catalog_with_same_profile_recovery(
                        db,
                        run,
                        source,
                        selected_egress,
                        runtime_config,
                        settings,
                        include_catalog_payload=True,
                        explicit_retry=True,
                        rejected_egress_fingerprint=rejected_egress_fingerprint,
                    )
                else:
                    run_provider, result = _search_catalog_with_profile_pool_recovery(
                        db,
                        run,
                        source,
                        selected_egress,
                        runtime_config,
                        settings,
                        cache,
                        queue_key=settings.worker_task_queue_key,
                        include_catalog_payload=True,
                    )
            else:
                proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
                _attach_provider_event_sink(db, run_provider, run, source, proxy_profile_id)
                result = _search_catalog_once(
                    db,
                    run,
                    source,
                    run_provider,
                    proxy_profile_id,
                    settings,
                    prepared_result=None,
                    attempt=None,
                )
        except (
            DataDomeChallengeError,
            VintedCatalogChallengeError,
            VintedCatalogRateLimitError,
            VintedCatalogSessionContextError,
            VintedCatalogSessionError,
        ) as exc:
            failed_run = _record_failed_run(
                db,
                run,
                source,
                exc,
                kind=_catalog_terminal_failure_kind(exc),
                penalize_proxy=False,
            )
            if run_provider is not None:
                _close_owned_provider(run_provider, owned_provider=owned_provider)
            return failed_run
        except Exception as exc:
            failed_run = _record_failed_run(
                db,
                run,
                source,
                exc,
                penalize_proxy=False,
            )
            if run_provider is not None:
                _close_owned_provider(run_provider, owned_provider=owned_provider)
            return failed_run
        proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
        unique_candidates = _deduplicate_candidates(result.items)
        candidate_ids = [candidate.vinted_item_id for candidate in unique_candidates]
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="catalog_candidates_received",
            proxy_profile_id=proxy_profile_id,
            details={
                "candidate_count": len(result.items),
                "unique_candidate_count": len(unique_candidates),
                "duplicate_count": max(len(result.items) - len(unique_candidates), 0),
                "page": result.page,
                "per_page": result.per_page,
                "total_pages": result.total_pages,
                "total_entries": result.total_entries,
                "baseline_run": True,
            },
        )
        cache.mark_seen(source.id, policy_hash, candidate_ids)
        cache.mark_baseline(source.id, policy_hash)
        run.status = SUCCESS
        run.finished_at = datetime.now(UTC)
        run.items_found = 0
        run.items_filter_passed = 0
        run.items_discarded_by_filters = 0
        run.items_filter_pending = 0
        run.opportunities_created = 0
        run.error_message = None
        source.last_run_at = run.finished_at
        mark_proxy_run_success(db, proxy_profile_id)
        db.flush()
        activation_error: SchedulerCapacityError | SchedulerUnavailableError | None = None
        if activate_session:
            if source.monitor_mode != "manual":
                try:
                    acquire_initial_run_admission_lock(db)
                    ensure_scheduler_can_activate(db, settings, source_id=source.id)
                except (SchedulerCapacityError, SchedulerUnavailableError) as exc:
                    activation_error = exc
            if activation_error is None:
                start_source_monitor(
                    db,
                    source.id,
                    commit=False,
                )
                opened_session = get_active_monitor_session(db, source.id)
                if opened_session is None:
                    raise RuntimeError("Monitor activation did not open a session")
                _merge_run_metadata(run, {OPENED_MONITOR_SESSION_ID_KEY: opened_session.id})
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="baseline_snapshot_seeded",
            proxy_profile_id=proxy_profile_id,
            message="Foto inicial guardada",
            details={
                "candidate_count": len(result.items),
                "unique_candidate_count": len(unique_candidates),
                "marked_seen_count": len(candidate_ids),
                "sample_vinted_item_ids": candidate_ids[:10],
                "policy_hash": policy_hash,
                "reason": baseline_reason,
            },
        )
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="run_succeeded",
            proxy_profile_id=proxy_profile_id,
            auth_mode="public_anonymous",
            details={
                "baseline_run": True,
                "baseline_reason": baseline_reason,
                "items_found": run.items_found,
                "items_filter_passed": run.items_filter_passed,
                "items_discarded_by_filters": run.items_discarded_by_filters,
                "items_filter_pending": run.items_filter_pending,
                "opportunities_created": run.opportunities_created,
            },
        )
        db.commit()
        db.refresh(run)
        if activation_error is not None:
            raise activation_error
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return run
    except (SchedulerCapacityError, SchedulerUnavailableError):
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        raise
    except (
        DataDomeChallengeError,
        VintedCatalogChallengeError,
        VintedCatalogRateLimitError,
        VintedCatalogSessionContextError,
        VintedCatalogSessionError,
    ) as exc:
        failed_run = _record_failed_run(
            db,
            run,
            source,
            exc,
            kind=_catalog_terminal_failure_kind(exc),
            penalize_proxy=False,
        )
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run
    except Exception as exc:
        failed_run = _record_failed_run(
            db,
            run,
            source,
            exc,
            penalize_proxy=not isinstance(exc, SeenCacheUnavailableError | ProxyProfileEligibilityError),
        )
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run


def execute_monitor_session_prepare(
    db: Session,
    source_id: int,
    egress: RunEgress | None = None,
) -> Run:
    source = db.get(SearchSource, source_id)
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.is_active:
        raise RunAlreadyActiveError("Deten la sesion antes de preparar la sesion Vinted")
    if _active_source_run_exists(db, source_id=source.id, include_finalizing=True):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")
    catalog_filters = _validated_catalog_filter_compatibility(source)

    settings = get_settings()
    runtime_config = get_scheduler_runtime_config(db, settings)
    selected_egress = egress or choose_run_egress(db, settings)
    _require_proxy_egress(selected_egress)
    proxy_profile = db.get(ProxyProfile, selected_egress.proxy_profile_id)
    if proxy_profile is None:
        raise SchedulerCapacityError(f"Proxy profile {selected_egress.proxy_profile_id} no longer exists")

    browser_profile = profile_for_impersonate(settings.curl_impersonate_browser)
    run = Run(
        source_id=source.id,
        monitor_session_id=None,
        status=RUNNING,
        trigger=SESSION_PREPARE_TRIGGER,
        items_found=0,
        items_filter_passed=0,
        items_discarded_by_filters=0,
        items_filter_pending=0,
        opportunities_created=0,
        runtime_metadata={
            **_run_runtime_metadata(source, selected_egress, runtime_config),
            "session_prepare_run": True,
            "target_country_code": settings.vinted_target_country_code.strip().upper(),
            "proxy_country_code": proxy_profile.country_code,
            "locale": proxy_profile.locale,
            "accept_language": proxy_profile.accept_language,
            "screen": proxy_profile.screen,
            "vinted_screen": proxy_profile.vinted_screen,
            "browser_profile": browser_profile.name,
            "impersonate": browser_profile.impersonate,
        },
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run") from exc
    db.refresh(run)

    try:
        proxy_profile = lock_and_revalidate_proxy_selection(
            db,
            selected_egress.proxy_profile_id,
            selected_egress.proxy_identity_generation,
            settings,
        )
    except Exception as exc:
        return _record_failed_run(
            db,
            run,
            source,
            exc,
            kind="vinted_session_prepare",
            penalize_proxy=not isinstance(exc, ProxyProfileEligibilityError),
        )

    proxy_profile_id = proxy_profile.id
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_started",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "trigger": SESSION_PREPARE_TRIGGER,
            "monitor_mode": source.monitor_mode,
            "egress_mode": selected_egress.mode,
            "proxy_profile_id": proxy_profile_id,
            "proxy_kind": proxy_profile.kind,
            "target_country_code": (run.runtime_metadata or {}).get("target_country_code"),
            "proxy_country_code": proxy_profile.country_code,
            "locale": proxy_profile.locale,
            "screen": proxy_profile.screen,
            "vinted_screen": proxy_profile.vinted_screen,
            "browser_profile": browser_profile.name,
            "session_prepare_run": True,
        },
    )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_config_resolved",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "monitor_id": source.id,
            "trigger": SESSION_PREPARE_TRIGGER,
            "monitor_mode": source.monitor_mode,
            "catalog_filter_compatibility": catalog_filters,
            "runtime_config": {
                "catalog_per_page": runtime_config.catalog_per_page,
                "request_timeout_ms": runtime_config.request_timeout_ms,
            },
            "session_prepare_run": True,
        },
    )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="egress_selected",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "egress_mode": selected_egress.mode,
            "proxy_profile_id": proxy_profile_id,
            "proxy_name": proxy_profile.name,
            "proxy_kind": proxy_profile.kind,
            "target_country_code": (run.runtime_metadata or {}).get("target_country_code"),
            "proxy_country_code": proxy_profile.country_code,
            "locale": proxy_profile.locale,
            "accept_language": proxy_profile.accept_language,
            "screen": proxy_profile.screen,
            "vinted_screen": proxy_profile.vinted_screen,
            "session_prepare_run": True,
        },
    )

    try:
        proxy_profile = lock_and_revalidate_proxy_selection(
            db,
            selected_egress.proxy_profile_id,
            selected_egress.proxy_identity_generation,
            settings,
        )
        event_sink = _build_provider_event_sink(db, run, source, proxy_profile_id)
        vinted_session, prepared_session, provider_metadata, _prepared_catalog_result = _prepare_vinted_session_for_run(
            db,
            source,
            proxy_profile,
            runtime_config,
            settings,
            event_sink=event_sink,
        )
        proxy_marker = safe_secret_marker("proxy_sticky_session_id", vinted_session.proxy_session_id, kind="proxy_session")
        _merge_run_metadata(
            run,
            {
                **provider_metadata,
                "vinted_session_id": vinted_session.id,
                "vinted_session_status": vinted_session.status,
                "vinted_session_request_count": vinted_session.request_count,
                "vinted_session_max_requests": vinted_session.max_requests,
                "vinted_session_action": "prepared",
                "vinted_session_datadome_present": bool(
                    prepared_session.datadome or (prepared_session.cookies or {}).get("datadome")
                ),
                "vinted_session_cf_bm_present": bool(
                    prepared_session.cf_bm or (prepared_session.cookies or {}).get("__cf_bm")
                ),
                "proxy_session_id_prefix": vinted_session.proxy_session_id[:8],
                "proxy_sticky_session": proxy_marker,
            },
        )
        run.status = SUCCESS
        run.finished_at = datetime.now(UTC)
        run.error_message = None
        mark_proxy_run_success(db, proxy_profile_id)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="run_succeeded",
            proxy_profile_id=proxy_profile_id,
            auth_mode="public_anonymous",
            details={
                "session_prepare_run": True,
                "vinted_session_id": vinted_session.id,
                "vinted_session_status": vinted_session.status,
                "vinted_session_use_count": vinted_session.request_count,
                "vinted_session_max_requests": vinted_session.max_requests,
                "context": prepared_context_flags(prepared_session),
                "datadome_required": True,
                "proxy_session": proxy_marker,
            },
        )
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        return _record_failed_run(
            db,
            run,
            source,
            exc,
            kind="vinted_session_prepare",
            penalize_proxy=not isinstance(exc, VintedSessionRequiredError | ProxyProfileEligibilityError),
        )


def execute_monitor_item_detail_probe(
    db: Session,
    source_id: int,
    *,
    item_ref: str,
    egress: RunEgress | None = None,
) -> tuple[Run, dict[str, Any]]:
    source = db.get(SearchSource, source_id)
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.is_active:
        raise RunAlreadyActiveError("Deten la sesion antes de probar el detalle de un item")
    if _active_source_run_exists(db, source_id=source.id, include_finalizing=True):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")
    normalized_item_ref = str(item_ref or "").strip()
    item_id = extract_vinted_item_id(normalized_item_ref)
    if item_id is None:
        raise SearchSourceConfigError("Introduce un ID numerico de Vinted o una URL de item valida")
    if not normalized_item_ref.isdigit():
        try:
            build_item_detail_navigation_url(normalized_item_ref)
        except ValueError as exc:
            raise SearchSourceConfigError("Introduce un ID numerico de Vinted o una URL de item valida") from exc
    catalog_filters = _validated_catalog_filter_compatibility(source)

    settings = get_settings()
    runtime_config = get_scheduler_runtime_config(db, settings)
    selected_egress = egress or choose_run_egress(db, settings)
    _require_proxy_egress(selected_egress)
    proxy_profile = db.get(ProxyProfile, selected_egress.proxy_profile_id)
    if proxy_profile is None:
        raise SchedulerCapacityError(f"Proxy profile {selected_egress.proxy_profile_id} no longer exists")

    browser_profile = profile_for_impersonate(settings.curl_impersonate_browser)
    run = Run(
        source_id=source.id,
        monitor_session_id=None,
        status=RUNNING,
        trigger=DETAIL_PROBE_TRIGGER,
        items_found=0,
        items_filter_passed=0,
        items_discarded_by_filters=0,
        items_filter_pending=0,
        opportunities_created=0,
        runtime_metadata={
            **_run_runtime_metadata(source, selected_egress, runtime_config),
            "detail_probe_run": True,
            "item_id": item_id,
            "target_country_code": settings.vinted_target_country_code.strip().upper(),
            "proxy_country_code": proxy_profile.country_code,
            "locale": proxy_profile.locale,
            "accept_language": proxy_profile.accept_language,
            "screen": proxy_profile.screen,
            "vinted_screen": proxy_profile.vinted_screen,
            "browser_profile": browser_profile.name,
            "impersonate": browser_profile.impersonate,
        },
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run") from exc
    db.refresh(run)

    result: dict[str, Any] = {
        "outcome": "failed",
        "item_id": item_id,
        "error": None,
    }
    try:
        proxy_profile = lock_and_revalidate_proxy_selection(
            db,
            selected_egress.proxy_profile_id,
            selected_egress.proxy_identity_generation,
            settings,
        )
    except Exception as exc:
        failed_run = _record_failed_run(
            db,
            run,
            source,
            exc,
            kind="detail_probe",
            penalize_proxy=False,
        )
        result["error"] = redact_sensitive_text(str(exc))
        return failed_run, result

    proxy_profile_id = proxy_profile.id
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_started",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "trigger": DETAIL_PROBE_TRIGGER,
            "monitor_mode": source.monitor_mode,
            "egress_mode": selected_egress.mode,
            "proxy_profile_id": proxy_profile_id,
            "proxy_kind": proxy_profile.kind,
            "target_country_code": (run.runtime_metadata or {}).get("target_country_code"),
            "proxy_country_code": proxy_profile.country_code,
            "locale": proxy_profile.locale,
            "screen": proxy_profile.screen,
            "vinted_screen": proxy_profile.vinted_screen,
            "browser_profile": browser_profile.name,
            "detail_probe_run": True,
            "item_id": item_id,
        },
    )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_config_resolved",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "monitor_id": source.id,
            "trigger": DETAIL_PROBE_TRIGGER,
            "monitor_mode": source.monitor_mode,
            "catalog_filter_compatibility": catalog_filters,
            "runtime_config": {
                "request_timeout_ms": runtime_config.request_timeout_ms,
            },
            "detail_probe_run": True,
            "item_id": item_id,
            "endpoint": f"/items/{item_id}?referrer=catalog",
        },
    )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="egress_selected",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "egress_mode": selected_egress.mode,
            "proxy_profile_id": proxy_profile_id,
            "proxy_name": proxy_profile.name,
            "proxy_kind": proxy_profile.kind,
            "target_country_code": (run.runtime_metadata or {}).get("target_country_code"),
            "proxy_country_code": proxy_profile.country_code,
            "locale": proxy_profile.locale,
            "accept_language": proxy_profile.accept_language,
            "screen": proxy_profile.screen,
            "vinted_screen": proxy_profile.vinted_screen,
            "detail_probe_run": True,
        },
    )

    provider: CurlCffiVintedCatalogProvider | None = None
    try:
        provider, provider_metadata, _prepared_catalog_result = _provider_for_egress(
            db,
            source,
            selected_egress,
            runtime_config,
            settings,
            run=run,
        )
        _merge_run_metadata(run, {**provider_metadata, "detail_probe_run": True, "item_id": item_id})
        result = provider.probe_item_detail_document(
            f"{str(settings.vinted_base_url).rstrip('/')}/items/{item_id}",
            referer_url=source.url,
        )
        vinted_session_id = (run.runtime_metadata or {}).get("vinted_session_id")
        if isinstance(vinted_session_id, int):
            provider_prepared_session = getattr(provider, "prepared_session", None)
            refreshed_context = provider.export_prepared_session(
                proxy_session_id=(provider_prepared_session.proxy_session_id if provider_prepared_session else None)
            )
            refreshed_context.session_id = vinted_session_id
            update_vinted_session_context(
                db,
                vinted_session_id,
                context=refreshed_context,
                settings=settings,
            )
        _merge_run_metadata(
            run,
            {
                "detail_probe_outcome": result.get("outcome"),
                "detail_probe_status_code": result.get("status_code"),
                "detail_probe_duration_ms": result.get("duration_ms"),
                "detail_probe_item_id": item_id,
            },
        )
        run.status = SUCCESS
        run.finished_at = datetime.now(UTC)
        run.error_message = None
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="detail_probe_finished",
            proxy_profile_id=proxy_profile_id,
            auth_mode="public_anonymous",
            status_code=result.get("status_code") if isinstance(result.get("status_code"), int) else None,
            duration_ms=result.get("duration_ms") if isinstance(result.get("duration_ms"), int) else None,
            level="info" if result.get("outcome") == "accepted_html" else "warning",
            details={
                "detail_probe_run": True,
                "item_id": item_id,
                "outcome": result.get("outcome"),
                "status_code": result.get("status_code"),
                "duration_ms": result.get("duration_ms"),
                "endpoint": result.get("detail_document_url"),
                "detail_summary": result.get("detail_summary") or {},
                "error": result.get("error"),
            },
        )
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="run_succeeded",
            proxy_profile_id=proxy_profile_id,
            auth_mode="public_anonymous",
            status_code=result.get("status_code") if isinstance(result.get("status_code"), int) else None,
            duration_ms=result.get("duration_ms") if isinstance(result.get("duration_ms"), int) else None,
            details={
                "detail_probe_run": True,
                "item_id": item_id,
                "outcome": result.get("outcome"),
                "status_code": result.get("status_code"),
                "duration_ms": result.get("duration_ms"),
                "endpoint": result.get("detail_document_url"),
                "detail_summary": result.get("detail_summary") or {},
                "error": result.get("error"),
            },
        )
        db.commit()
        db.refresh(run)
        return run, result
    except Exception as exc:
        failed_run = _record_failed_run(
            db,
            run,
            source,
            exc,
            kind="detail_probe",
            penalize_proxy=False,
        )
        result["error"] = redact_sensitive_text(str(exc))
        return failed_run, result
    finally:
        if provider is not None:
            provider.close()


def execute_monitor_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
    trigger: str = MANUAL_TRIGGER,
    seen_cache: SeenCache | None = None,
    require_active: bool = True,
    create_session_for_run: bool = False,
    close_session_on_finish: bool = False,
    egress: RunEgress | None = None,
    runtime_metadata_extra: dict[str, Any] | None = None,
    task_queue_key: str | None = None,
) -> Run:
    source = _lock_source_for_run_transition(db, source_id)
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if require_active and not source.is_active:
        raise SearchSourceInactiveError(f"Search source {source_id} is inactive")
    if _active_source_run_exists(db, source_id=source.id):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")
    catalog_filters = _validated_catalog_filter_compatibility(source)

    settings = get_settings()
    runtime_config = get_scheduler_runtime_config(db, settings)
    selected_egress = egress or choose_run_egress(db, settings)
    _require_proxy_egress(selected_egress)
    owned_provider = provider is None
    run_provider: ManualRunProvider | None = provider
    run_session = start_monitor_session(db, source, allow_manual=True) if create_session_for_run else None
    active_session = run_session
    if active_session is None and require_active:
        active_session = get_active_monitor_session(db, source.id)
    if require_active and active_session is None:
        if source.monitor_mode == "manual":
            raise SearchSourceInactiveError("Inicia la sesion manual antes de ejecutar este monitor")
        raise SearchSourceInactiveError(f"Search source {source_id} has no active monitor session")
    run = Run(
        source_id=source.id,
        monitor_session_id=active_session.id if active_session is not None else None,
        task_id=_runtime_task_id(runtime_metadata_extra),
        status=RUNNING,
        trigger=trigger,
        items_found=0,
        items_filter_passed=0,
        items_discarded_by_filters=0,
        items_filter_pending=0,
        opportunities_created=0,
        runtime_metadata={
            **_run_runtime_metadata(source, selected_egress, runtime_config),
            **(runtime_metadata_extra or {}),
        },
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if run_provider is not None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run") from exc
    db.refresh(run)
    if run_provider is None and selected_egress.proxy_profile_id is not None:
        try:
            lock_and_revalidate_proxy_selection(
                db,
                selected_egress.proxy_profile_id,
                selected_egress.proxy_identity_generation,
                settings,
            )
        except Exception as exc:
            return _record_failed_run(
                db,
                run,
                source,
                exc,
                penalize_proxy=not isinstance(exc, ProxyProfileEligibilityError),
            )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_started",
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        user_agent=None,
        auth_mode="public_anonymous",
        details={
            "trigger": trigger,
            "monitor_mode": source.monitor_mode,
            "filter_count": filter_term_count(source.filter_definition),
            "egress_mode": (run.runtime_metadata or {}).get("egress_mode"),
            "proxy_profile_id": (run.runtime_metadata or {}).get("proxy_profile_id"),
            "proxy_kind": (run.runtime_metadata or {}).get("proxy_kind"),
            "target_country_code": (run.runtime_metadata or {}).get("target_country_code"),
            "proxy_country_code": (run.runtime_metadata or {}).get("proxy_country_code"),
            "locale": (run.runtime_metadata or {}).get("locale"),
            "screen": (run.runtime_metadata or {}).get("screen"),
            "vinted_screen": (run.runtime_metadata or {}).get("vinted_screen"),
            "browser_profile": (run.runtime_metadata or {}).get("browser_profile"),
            "vinted_session_id": (run.runtime_metadata or {}).get("vinted_session_id"),
            "proxy_session_id_prefix": (run.runtime_metadata or {}).get("proxy_session_id_prefix"),
            "proxy_sticky_session": (run.runtime_metadata or {}).get("proxy_sticky_session"),
            "task_id": (run.runtime_metadata or {}).get("task_id"),
        },
    )
    cache = seen_cache or get_seen_cache()
    filter_snapshot = monitor_filter_snapshot(source.filter_definition)
    policy_hash = _policy_hash(source, filter_snapshot)
    run.runtime_metadata = {**(run.runtime_metadata or {}), "policy_hash": policy_hash}
    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
    _attach_provider_event_sink(db, run_provider, run, source, proxy_profile_id)
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_config_resolved",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "monitor_id": source.id,
            "trigger": trigger,
            "monitor_mode": source.monitor_mode,
            "evaluation_contract": EVALUATION_CONTRACT_VERSION,
            "filter_scope": "description",
            "policy_hash": policy_hash,
            "filter_snapshot": filter_snapshot,
            "catalog_filter_compatibility": catalog_filters,
            "runtime_config": {
                "catalog_per_page": runtime_config.catalog_per_page,
                "detail_max_candidates_per_run": runtime_config.detail_max_candidates_per_run,
                "detail_fetch_mode": settings.vinted_detail_fetch_mode,
                "detail_concurrency": settings.vinted_detail_concurrency,
                "detail_early_filter_mode": settings.vinted_detail_early_filter_mode,
                "detail_head_max_bytes": settings.vinted_detail_head_max_bytes,
                "request_timeout_ms": runtime_config.request_timeout_ms,
                "stop_monitor_after_consecutive_failures": runtime_config.stop_monitor_after_consecutive_failures,
            },
        },
    )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="egress_selected",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "egress_mode": (run.runtime_metadata or {}).get("egress_mode"),
            "proxy_profile_id": proxy_profile_id,
            "proxy_name": (run.runtime_metadata or {}).get("proxy_name"),
            "proxy_kind": (run.runtime_metadata or {}).get("proxy_kind"),
            "target_country_code": (run.runtime_metadata or {}).get("target_country_code"),
            "proxy_country_code": (run.runtime_metadata or {}).get("proxy_country_code"),
            "locale": (run.runtime_metadata or {}).get("locale"),
            "accept_language": (run.runtime_metadata or {}).get("accept_language"),
            "screen": (run.runtime_metadata or {}).get("screen"),
            "vinted_screen": (run.runtime_metadata or {}).get("vinted_screen"),
            "vinted_session_id": (run.runtime_metadata or {}).get("vinted_session_id"),
            "proxy_sticky_session": (run.runtime_metadata or {}).get("proxy_sticky_session"),
        },
    )

    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="redis_check_start",
        proxy_profile_id=proxy_profile_id,
        message="Checking Redis seen cache availability",
        details={"policy_hash": policy_hash},
    )
    try:
        cache.require_available()
    except SeenCacheUnavailableError as exc:
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="redis_check_error",
            level="error",
            proxy_profile_id=proxy_profile_id,
            message=str(exc),
            details={"policy_hash": policy_hash},
        )
        source.is_active = False
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
        failed_run = _record_failed_run(
            db,
            run,
            source,
            exc,
            kind="redis_unavailable",
            penalize_proxy=False,
            force_stop_monitor=True,
            monitor_stop_reason="redis_unavailable",
        )
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="redis_check_success",
        proxy_profile_id=proxy_profile_id,
        message="Redis seen cache is available",
        details={"policy_hash": policy_hash},
    )
    try:
        _reconcile_finalizing_runs(db, source, cache, exclude_run_id=run.id)
        if not cache.has_baseline(source.id, policy_hash):
            baseline_required_message = "La foto inicial ya no esta disponible; inicia una nueva sesion"
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="baseline_required",
                level="warning",
                proxy_profile_id=proxy_profile_id,
                message=baseline_required_message,
                details={"policy_hash": policy_hash},
            )
            source.is_active = False
            source.monitor_started_at = None
            source.monitor_until = None
            source.next_run_at = None
            failed_run = _record_failed_run(
                db,
                run,
                source,
                BaselineRequiredError(baseline_required_message),
                kind="baseline_required",
                penalize_proxy=False,
                force_stop_monitor=True,
                monitor_stop_reason="baseline_required",
            )
            _close_owned_provider(run_provider, owned_provider=owned_provider)
            return failed_run
    except SeenCacheUnavailableError as exc:
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="redis_check_error",
            level="error",
            proxy_profile_id=proxy_profile_id,
            message=str(exc),
            details={"policy_hash": policy_hash},
        )
        source.is_active = False
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
        failed_run = _record_failed_run(
            db,
            run,
            source,
            exc,
            kind="redis_unavailable",
            penalize_proxy=False,
            force_stop_monitor=True,
            monitor_stop_reason="redis_unavailable",
        )
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run

    try:
        if run_provider is None:
            run_provider, result = _search_catalog_with_profile_pool_recovery(
                db,
                run,
                source,
                selected_egress,
                runtime_config,
                settings,
                cache,
                queue_key=task_queue_key or settings.worker_task_queue_key,
                include_catalog_payload=False,
            )
        else:
            proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
            _attach_provider_event_sink(db, run_provider, run, source, proxy_profile_id)
            result = _search_catalog_once(
                db,
                run,
                source,
                run_provider,
                proxy_profile_id,
                settings,
                prepared_result=None,
                attempt=None,
            )
    except (
        DataDomeChallengeError,
        VintedCatalogChallengeError,
        VintedCatalogRateLimitError,
        VintedCatalogSessionContextError,
        VintedCatalogSessionError,
    ) as exc:
        failed_run = _record_failed_run(
            db,
            run,
            source,
            exc,
            kind=_catalog_terminal_failure_kind(exc),
            penalize_proxy=False,
        )
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run
    except Exception as exc:
        failed_run = _record_failed_run(db, run, source, exc, penalize_proxy=False)
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run

    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
    acquisition_events = _snapshot_session_acquisition_events(db, run.id)
    claimed_ids: set[str] = set()
    claimed_work_items: list[DetailWorkItem] = []
    found_count = 0
    try:
        unique_candidates = _deduplicate_candidates(result.items)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="catalog_candidates_received",
            proxy_profile_id=proxy_profile_id,
            details={
                "candidate_count": len(result.items),
                "unique_candidate_count": len(unique_candidates),
                "duplicate_count": max(len(result.items) - len(unique_candidates), 0),
                "page": result.page,
                "per_page": result.per_page,
                "total_pages": result.total_pages,
                "total_entries": result.total_entries,
            },
        )
        max_detail_candidates = _detail_candidate_limit(run, run_provider)
        catalog_claimed_ids = cache.claim_unseen(
            source.id,
            policy_hash,
            [candidate.vinted_item_id for candidate in unique_candidates],
        )
        monitor_new_candidates = [
            candidate for candidate in unique_candidates if candidate.vinted_item_id in catalog_claimed_ids
        ]
        claimed_work_items = [DetailWorkItem(candidate=candidate) for candidate in monitor_new_candidates]
        claimed_ids = {work_item.candidate.vinted_item_id for work_item in claimed_work_items}
        if claimed_work_items:
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="detail_candidates_claimed",
                proxy_profile_id=proxy_profile_id,
                details={
                    "candidate_count": len(claimed_work_items),
                    "sample_vinted_item_ids": [
                        work_item.candidate.vinted_item_id for work_item in claimed_work_items[:10]
                    ],
                    "policy_hash": policy_hash,
                },
            )
        existing_opportunity_ids = _existing_opportunity_item_ids(
            db,
            source,
            [work_item.candidate for work_item in claimed_work_items],
        )
        found_count = sum(
            candidate.vinted_item_id not in existing_opportunity_ids for candidate in monitor_new_candidates
        )
        run.items_found = found_count
        if existing_opportunity_ids:
            already_claimed_existing_ids = [
                work_item.candidate.vinted_item_id
                for work_item in claimed_work_items
                if work_item.candidate.vinted_item_id in existing_opportunity_ids
            ]
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="candidate_existing_opportunity_skipped",
                level="debug",
                proxy_profile_id=proxy_profile_id,
                message="Candidates already have an opportunity for this monitor and were skipped",
                details={
                    "existing_opportunity_count": len(already_claimed_existing_ids),
                    "sample_vinted_item_ids": already_claimed_existing_ids[:10],
                    "policy_hash": policy_hash,
                },
            )
            cache.finalize_candidate_states(
                source.id,
                policy_hash,
                DetailCandidateStateUpdate(terminal_ids=tuple(already_claimed_existing_ids)),
            )
            claimed_ids.difference_update(already_claimed_existing_ids)
            catalog_claimed_ids.difference_update(already_claimed_existing_ids)
            monitor_new_candidates = [
                candidate for candidate in monitor_new_candidates if candidate.vinted_item_id not in existing_opportunity_ids
            ]
            claimed_work_items = [
                work_item
                for work_item in claimed_work_items
                if work_item.candidate.vinted_item_id not in existing_opportunity_ids
            ]
        unavailable_catalog_candidates = [
            candidate for candidate in unique_candidates if candidate.vinted_item_id not in catalog_claimed_ids
        ]
        if unavailable_catalog_candidates:
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="candidate_seen_skipped",
                level="debug",
                proxy_profile_id=proxy_profile_id,
                message="Catalog candidates already seen or processing were skipped",
                details={
                    "seen_or_pending_count": len(unavailable_catalog_candidates),
                    "sample_vinted_item_ids": [
                        candidate.vinted_item_id for candidate in unavailable_catalog_candidates[:10]
                    ],
                    "policy_hash": policy_hash,
                },
            )
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="redis_seen_result",
            proxy_profile_id=proxy_profile_id,
            message="Monitor seen cache evaluated catalog candidates",
            details={
                "candidate_count": len(result.items),
                "unique_candidate_count": len(unique_candidates),
                "seen_hit_count": len(unique_candidates) - len(monitor_new_candidates),
                "seen_miss_count": len(monitor_new_candidates),
                "policy_hash": policy_hash,
            },
        )
        monitor_result = _evaluate_monitor_candidates(
            db,
            run_provider,
            source,
            run,
            claimed_work_items,
            filter_snapshot,
            max_detail_candidates=max_detail_candidates,
        )
        _persist_provider_session_refresh(db, run_provider, run, source, proxy_profile_id, settings)
        candidate_state_update = DetailCandidateStateUpdate(terminal_ids=monitor_result.terminal_ids)
        run.status = FINALIZING
        run.finished_at = None
        run.items_found = found_count
        run.items_filter_passed = monitor_result.passed
        run.items_discarded_by_filters = monitor_result.discarded
        run.items_filter_pending = monitor_result.pending
        run.opportunities_created = monitor_result.opportunities_created
        run.error_message = None
        _merge_run_metadata(
            run,
            {
                "candidate_state_transition_status": "pending",
                "candidate_state_transition_policy_hash": policy_hash,
                "candidate_state_transition": serialize_candidate_state_update(candidate_state_update),
                "candidate_state_close_session_on_finish": close_session_on_finish,
            },
        )
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="redis_candidate_state_pending",
            proxy_profile_id=proxy_profile_id,
            details={
                "marked_seen_count": len(monitor_result.terminal_ids),
                "policy_hash": policy_hash,
            },
        )
        db.commit()
        cache.finalize_candidate_states(
            source.id,
            policy_hash,
            candidate_state_update,
        )
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="redis_candidate_state_updated",
            proxy_profile_id=proxy_profile_id,
            details={
                "marked_seen_count": len(monitor_result.terminal_ids),
                "sample_seen_vinted_item_ids": list(monitor_result.terminal_ids[:10]),
                "policy_hash": policy_hash,
            },
        )
        _complete_finalizing_run(
            db,
            run,
            source,
            close_session_on_finish=close_session_on_finish,
            reconciled=False,
        )
        db.commit()
        db.refresh(run)
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return run
    except SeenCacheUnavailableError as exc:
        observability_metadata = _run_observability_metadata(run)
        db.rollback()
        run = db.get(Run, run.id)
        source = db.get(SearchSource, source.id) or source
        if run is not None:
            _merge_run_metadata(run, observability_metadata)
            _restore_session_acquisition_events(db, run, source, acquisition_events)
        if run is not None and run.status == FINALIZING:
            try:
                _apply_pending_candidate_state_transition(db, run, source, cache, reconciled=True)
                db.commit()
                db.refresh(run)
                _close_owned_provider(run_provider, owned_provider=owned_provider)
                return run
            except SeenCacheUnavailableError as retry_exc:
                record_run_event(
                    db,
                    run_id=run.id,
                    source_id=source.id,
                    phase="redis_candidate_state_pending_error",
                    level="error",
                    proxy_profile_id=proxy_profile_id,
                    message=str(retry_exc),
                    details={"recovery_pending": True, "policy_hash": policy_hash},
                )
                source.is_active = False
                source.monitor_started_at = None
                source.monitor_until = None
                source.next_run_at = None
                stop_active_monitor_session(db, source.id, reason="redis_unavailable")
                db.commit()
                db.refresh(run)
                _close_owned_provider(run_provider, owned_provider=owned_provider)
                return run
        source.is_active = False
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
        if claimed_ids:
            try:
                cache.release_processing(source.id, policy_hash, list(claimed_ids))
            except SeenCacheUnavailableError:
                pass
        if run is None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
            raise_(exc)
        run.items_found = found_count
        failed_run = _record_failed_run(
            db,
            run,
            source,
            exc,
            kind="redis_unavailable",
            penalize_proxy=False,
            force_stop_monitor=True,
            monitor_stop_reason="redis_unavailable",
        )
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run
    except (
        DataDomeChallengeError,
        VintedCatalogChallengeError,
        VintedCatalogRateLimitError,
        VintedCatalogSessionContextError,
        VintedCatalogSessionError,
    ) as exc:
        observability_metadata = _run_observability_metadata(run)
        db.rollback()
        run = db.get(Run, run.id)
        if run is None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
            raise
        _merge_run_metadata(run, observability_metadata)
        _restore_session_acquisition_events(db, run, source, acquisition_events)
        try:
            run.items_found = found_count
            failure_kind = _catalog_terminal_failure_kind(exc)
            failed_run = _record_failed_run(db, run, source, exc, kind=failure_kind, penalize_proxy=False)
            release_error: SeenCacheUnavailableError | None = None
            if claimed_ids:
                for _ in range(2):
                    try:
                        cache.release_processing(source.id, policy_hash, list(claimed_ids))
                        release_error = None
                        break
                    except SeenCacheUnavailableError as retry_exc:
                        release_error = retry_exc
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="detail_candidate_batch_closed" if release_error is None else "detail_candidate_lock_expiry_pending",
                level="warning" if release_error is None else "error",
                proxy_profile_id=proxy_profile_id,
                message=(
                    "Claimed candidate work was discarded after the terminal provider failure stopped the session"
                    if release_error is None
                    else "Claimed candidate locks will expire after the terminal provider failure stopped the session"
                ),
                details={
                    "failure_kind": failure_kind,
                    "discarded_candidate_count": len(claimed_ids),
                    "lock_expiry_pending": release_error is not None,
                },
            )
            db.commit()
            db.refresh(failed_run)
        finally:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run
    except Exception as exc:
        observability_metadata = _run_observability_metadata(run)
        db.rollback()
        run = db.get(Run, run.id)
        if run is not None:
            _merge_run_metadata(run, observability_metadata)
            _restore_session_acquisition_events(db, run, source, acquisition_events)
        if run is not None and run.status == FINALIZING:
            try:
                _apply_pending_candidate_state_transition(db, run, source, cache, reconciled=True)
                db.commit()
                db.refresh(run)
            except Exception:
                db.rollback()
                run = db.get(Run, run.id)
            _close_owned_provider(run_provider, owned_provider=owned_provider)
            if run is None:
                raise
            return run
        if claimed_ids:
            for _ in range(2):
                try:
                    cache.release_processing(source.id, policy_hash, list(claimed_ids))
                    break
                except SeenCacheUnavailableError:
                    continue
        if run is None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
            raise
        run.items_found = found_count
        failed_run = _record_failed_run(db, run, source, exc, penalize_proxy=False)
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run


def list_runs(db: Session, limit: int = 50, source_id: int | None = None) -> list[Run]:
    statement = select(Run)
    if source_id is not None:
        statement = statement.where(Run.source_id == source_id)
    statement = statement.order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
    return list(db.scalars(statement))


def _resolve_explicit_retry_origin(
    db: Session,
    source: SearchSource,
    proxy_profile_id: int,
) -> tuple[int, str | None]:
    latest_run = db.scalar(
        select(Run)
        .where(Run.source_id == source.id)
        .order_by(Run.started_at.desc(), Run.id.desc())
        .limit(1)
    )
    if latest_run is None or latest_run.trigger != BASELINE_TRIGGER or latest_run.status != FAILED:
        raise ExplicitSessionRetryUnavailableError(
            "El reintento explicito solo esta disponible tras el ultimo inicio de sesion fallido"
        )
    metadata = latest_run.runtime_metadata or {}
    attempted_profile_ids = metadata.get("session_acquisition_profile_ids")
    if (
        not isinstance(attempted_profile_ids, list)
        or proxy_profile_id not in attempted_profile_ids
    ):
        raise ExplicitSessionRetryUnavailableError(
            "El perfil seleccionado no participo en el ultimo inicio de sesion fallido"
        )
    fingerprints = metadata.get("session_acquisition_rejected_egress_fingerprints")
    rejected_fingerprint = (
        fingerprints.get(str(proxy_profile_id))
        if isinstance(fingerprints, dict)
        else None
    )
    if not isinstance(rejected_fingerprint, str) or not re.fullmatch(
        r"v1:[0-9a-f]{64}",
        rejected_fingerprint,
    ):
        rejected_fingerprint = None
    return latest_run.id, rejected_fingerprint


def _choose_explicit_retry_egress(
    db: Session,
    proxy_profile_id: int,
    runtime_config,
    settings,
    seen_cache: SeenCache,
) -> RunEgress:
    acquire_run_egress_admission_lock(db)
    try:
        seen_cache.require_available()
        queued_tasks = _pending_tasks_for_admission(
            seen_cache,
            settings,
            queue_key=settings.worker_task_queue_key,
        )
    except (SeenCacheUnavailableError, TaskQueueError) as exc:
        raise SeenCacheUnavailableError("Redis is unavailable for explicit retry admission") from exc
    admission_counts, total_count = run_egress_admission_snapshot(db, queued_tasks)
    if total_count >= runtime_config.max_concurrent_runs:
        raise SchedulerCapacityError("Global run capacity is unavailable for explicit retry")
    try:
        profile = lock_proxy_profile_for_selection(
            db,
            proxy_profile_id,
            settings,
            bypass_cooldown=True,
        )
    except ProxyProfileEligibilityError as exc:
        raise ExplicitSessionRetryUnavailableError(str(exc)) from exc
    if profile.cooldown_until is None or profile.cooldown_until <= datetime.now(UTC):
        raise ExplicitSessionRetryUnavailableError(
            f"Proxy profile {profile.id} is not cooling down"
        )
    if admission_counts.get(profile.id, 0) >= max(profile.max_concurrent_runs, 1):
        raise SchedulerCapacityError(f"Proxy profile {profile.id} has no explicit retry capacity")
    mark_proxy_used(db, profile.id)
    db.flush()
    return RunEgress(
        mode="proxy",
        proxy_profile_id=profile.id,
        proxy_name=profile.name,
        proxy_kind=profile.kind,
        proxy_url=proxy_url_for_profile(profile, settings),
        proxy_identity_generation=effective_proxy_identity_generation(profile),
    )


def _search_catalog_with_profile_pool_recovery(
    db: Session,
    run: Run,
    source: SearchSource,
    egress: RunEgress,
    runtime_config,
    settings,
    seen_cache: SeenCache,
    *,
    queue_key: str,
    include_catalog_payload: bool,
) -> tuple[ManualRunProvider, CatalogSearchResult]:
    current_egress = egress
    exhausted_profile_ids: set[int] = set()
    remaining_profile_ids: list[int] | None = None
    fallback_binding = False

    while True:
        if fallback_binding:
            try:
                _lock_and_revalidate_fallback_egress_capacity(
                    db,
                    run,
                    current_egress,
                    settings,
                    seen_cache,
                    queue_key=queue_key,
                )
            except (ProxyProfileEligibilityError, SchedulerCapacityError) as exc:
                candidate_profile_id = current_egress.proxy_profile_id
                db.rollback()
                _record_profile_handoff_rejection(
                    db,
                    run,
                    source,
                    candidate_profile_id,
                    exc,
                    stage="pre_provider",
                )
                current_egress = _bind_next_profile_for_run(
                    db,
                    run,
                    source,
                    remaining_profile_ids or [],
                    settings,
                    seen_cache,
                    queue_key=queue_key,
                )
                if current_egress is None:
                    raise SessionAcquisitionExhaustedError(len(exhausted_profile_ids)) from exc
                continue

        try:
            return _search_catalog_with_same_profile_recovery(
                db,
                run,
                source,
                current_egress,
                runtime_config,
                settings,
                include_catalog_payload=include_catalog_payload,
            )
        except ProfileSessionAcquisitionExhaustedError as exc:
            exhausted_profile_id = current_egress.proxy_profile_id
            if not isinstance(exhausted_profile_id, int):
                raise
            _commit_exhausted_profile_penalty(
                db,
                run,
                source,
                exhausted_profile_id,
                exc,
                settings,
            )
            exhausted_profile_ids.add(exhausted_profile_id)
            if remaining_profile_ids is None:
                target_country_code = settings.vinted_target_country_code.strip().upper()
                remaining_profile_ids = [
                    profile.id
                    for profile in list_available_proxy_profiles(
                        db,
                        country_code=target_country_code,
                    )
                    if profile.id not in exhausted_profile_ids
                ]
            current_egress = _bind_next_profile_for_run(
                db,
                run,
                source,
                remaining_profile_ids,
                settings,
                seen_cache,
                queue_key=queue_key,
            )
            if current_egress is None:
                raise SessionAcquisitionExhaustedError(len(exhausted_profile_ids)) from exc
            fallback_binding = True


def _bind_next_profile_for_run(
    db: Session,
    run: Run,
    source: SearchSource,
    remaining_profile_ids: list[int],
    settings,
    seen_cache: SeenCache,
    *,
    queue_key: str,
) -> RunEgress | None:
    while remaining_profile_ids:
        candidate_profile_id = remaining_profile_ids.pop(0)
        try:
            return _bind_run_to_fallback_profile(
                db,
                run,
                source,
                candidate_profile_id,
                settings,
                seen_cache,
                queue_key=queue_key,
            )
        except (ProxyProfileEligibilityError, SchedulerCapacityError) as exc:
            db.rollback()
            _record_profile_handoff_rejection(
                db,
                run,
                source,
                candidate_profile_id,
                exc,
                stage="admission",
            )
    return None


def _bind_run_to_fallback_profile(
    db: Session,
    run: Run,
    source: SearchSource,
    candidate_profile_id: int,
    settings,
    seen_cache: SeenCache,
    *,
    queue_key: str,
) -> RunEgress:
    acquire_run_egress_admission_lock(db)
    queued_tasks = _pending_tasks_for_admission(seen_cache, settings, queue_key=queue_key)
    admission_counts, _ = run_egress_admission_snapshot(
        db,
        queued_tasks,
        exclude_run_id=run.id,
    )
    profile = lock_proxy_profile_for_selection(db, candidate_profile_id, settings)
    if admission_counts.get(profile.id, 0) >= max(profile.max_concurrent_runs, 1):
        raise SchedulerCapacityError(f"Proxy profile {profile.id} has no handoff capacity")

    current_source = _lock_source_for_run_transition(db, source.id)
    if current_source is None:
        raise SearchSourceNotFoundError(f"Search source {source.id} does not exist")
    current_run = db.scalar(
        select(Run)
        .where(
            Run.id == run.id,
            Run.source_id == source.id,
            Run.status == RUNNING,
            Run.finished_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current_run is None:
        raise RuntimeError(f"Run {run.id} is no longer available for proxy handoff")

    previous_profile_id = (current_run.runtime_metadata or {}).get("proxy_profile_id")
    generation = effective_proxy_identity_generation(profile)
    metadata = dict(current_run.runtime_metadata or {})
    for key in tuple(metadata):
        if key.startswith("vinted_session_") or key in _PROFILE_SESSION_BINDING_METADATA_KEYS:
            metadata.pop(key, None)
    handoff_count = int(metadata.get("proxy_handoff_count") or 0) + 1
    metadata.update(
        {
            "proxy_profile_id": profile.id,
            "proxy_identity_generation": generation,
            "proxy_name": profile.name,
            "proxy_kind": profile.kind,
            "proxy_country_code": profile.country_code,
            "locale": profile.locale,
            "accept_language": profile.accept_language,
            "screen": profile.screen,
            "vinted_screen": profile.vinted_screen,
            "proxy_handoff_count": handoff_count,
        }
    )
    current_run.runtime_metadata = metadata
    mark_proxy_used(db, profile.id)
    record_run_event(
        db,
        run_id=current_run.id,
        source_id=current_source.id,
        phase="proxy_profile_handoff_committed",
        level="warning",
        proxy_profile_id=profile.id,
        auth_mode="public_anonymous",
        message="Run reassigned to another eligible proxy profile",
        details={
            "from_proxy_profile_id": previous_profile_id,
            "to_proxy_profile_id": profile.id,
            "handoff_count": handoff_count,
        },
    )
    db.commit()
    return RunEgress(
        mode="proxy",
        proxy_profile_id=profile.id,
        proxy_identity_generation=generation,
    )


def _lock_and_revalidate_fallback_egress_capacity(
    db: Session,
    run: Run,
    egress: RunEgress,
    settings,
    seen_cache: SeenCache,
    *,
    queue_key: str,
) -> None:
    _require_proxy_egress(egress)
    profile = lock_and_revalidate_proxy_selection(
        db,
        egress.proxy_profile_id,
        egress.proxy_identity_generation,
        settings,
    )
    current_run = db.get(Run, run.id)
    metadata = current_run.runtime_metadata if current_run is not None else {}
    if (
        current_run is None
        or current_run.status != RUNNING
        or current_run.finished_at is not None
        or metadata.get("proxy_profile_id") != profile.id
        or metadata.get("proxy_identity_generation") != egress.proxy_identity_generation
    ):
        raise ProxyProfileEligibilityError("Durable run egress binding changed before provider construction")
    queued_tasks = _pending_tasks_for_admission(seen_cache, settings, queue_key=queue_key)
    admission_counts, _ = run_egress_admission_snapshot(db, queued_tasks)
    if admission_counts.get(profile.id, 0) > max(profile.max_concurrent_runs, 1):
        raise SchedulerCapacityError(f"Proxy profile {profile.id} became saturated before provider construction")


def _pending_tasks_for_admission(
    seen_cache: SeenCache,
    settings,
    *,
    queue_key: str,
) -> list[Any]:
    queue_client = getattr(seen_cache, "client", None)
    queue_client = queue_client or get_seen_cache(settings).client
    return pending_tasks(queue_client, queue_key=queue_key)


def _commit_exhausted_profile_penalty(
    db: Session,
    run: Run,
    source: SearchSource,
    proxy_profile_id: int,
    exc: ProfileSessionAcquisitionExhaustedError,
    settings,
) -> None:
    cooldown_minutes = int((run.runtime_metadata or {}).get("proxy_cooldown_minutes", 10))
    if exc.datadome_penalty:
        mark_proxy_challenge_detected(
            db,
            proxy_profile_id,
            penalty_multiplier=settings.datadome_challenge_penalty_multiplier,
            cooldown_minutes=cooldown_minutes,
        )
    else:
        mark_proxy_run_failure(
            db,
            proxy_profile_id,
            cooldown_minutes=cooldown_minutes,
        )
    exhausted_count = int(
        (run.runtime_metadata or {}).get("session_acquisition_exhausted_profile_count") or 0
    ) + 1
    _merge_run_metadata(
        run,
        {"session_acquisition_exhausted_profile_count": exhausted_count},
    )
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="proxy_profile_session_exhausted",
        level="warning",
        proxy_profile_id=proxy_profile_id,
        details={
            "exhausted_profile_count": exhausted_count,
            "datadome_penalty": exc.datadome_penalty,
        },
    )
    db.commit()


def _record_profile_handoff_rejection(
    db: Session,
    run: Run,
    source: SearchSource,
    candidate_profile_id: int,
    exc: Exception,
    *,
    stage: str,
) -> None:
    event_profile_id = db.scalar(
        select(ProxyProfile.id).where(ProxyProfile.id == candidate_profile_id)
    )
    reason = "capacity_changed" if isinstance(exc, SchedulerCapacityError) else "identity_or_eligibility_changed"
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="proxy_profile_handoff_rejected",
        level="warning",
        proxy_profile_id=event_profile_id,
        auth_mode="public_anonymous",
        message="Proxy profile handoff rejected before provider construction",
        details={
            "candidate_proxy_profile_id": candidate_profile_id,
            "stage": stage,
            "reason": reason,
        },
    )
    db.commit()


_PROFILE_SESSION_BINDING_METADATA_KEYS = (
    "proxy_session_id_prefix",
    "proxy_sticky_session",
    "session_acquisition_attempts",
    "session_acquisition_last_reason",
    "session_acquisition_egress_changed",
)


def _search_catalog_with_same_profile_recovery(
    db: Session,
    run: Run,
    source: SearchSource,
    egress: RunEgress,
    runtime_config,
    settings,
    *,
    include_catalog_payload: bool,
    explicit_retry: bool = False,
    rejected_egress_fingerprint: str | None = None,
) -> tuple[ManualRunProvider, CatalogSearchResult]:
    previous_egress_ip: str | None = None
    previous_reason = "explicit_cooldown_retry" if explicit_retry else None
    datadome_penalty = False
    proxy_profile_id = egress.proxy_profile_id
    attempt_limit = 1 if explicit_retry else 2

    for attempt in range(1, attempt_limit + 1):
        previous_egress_known = bool(
            rejected_egress_fingerprint if explicit_retry else previous_egress_ip
        )
        _record_session_acquisition_attempt_event(
            db,
            run,
            source,
            phase="session_acquisition_attempt_started",
            attempt=attempt,
            attempt_limit=attempt_limit,
            reason=previous_reason or "initial_context",
            previous_egress_known=previous_egress_known,
            egress_changed=False,
        )
        attempt_provider: ManualRunProvider | None = None
        attempt_metadata: dict[str, Any] = {}
        try:
            attempt_provider, attempt_metadata, prepared_result = _provider_for_egress(
                db,
                source,
                egress,
                runtime_config,
                settings,
                run=run,
                include_catalog_payload=include_catalog_payload,
                force_new_session=explicit_retry or attempt == 2,
                rejected_egress_ip=previous_egress_ip,
                rejected_egress_fingerprint=rejected_egress_fingerprint,
                bypass_cooldown=explicit_retry,
            )
            _merge_run_metadata(run, attempt_metadata)
            db.flush()
            _attach_provider_event_sink(db, attempt_provider, run, source, proxy_profile_id)
            result = _search_catalog_once(
                db,
                run,
                source,
                attempt_provider,
                proxy_profile_id,
                settings,
                prepared_result=prepared_result,
                attempt=attempt,
            )
            observed_egress_ip = _session_acquisition_egress_ip(
                db,
                attempt_provider,
                attempt_metadata,
                None,
            )
            egress_changed = _session_acquisition_egress_changed(
                observed_egress_ip,
                settings,
                previous_egress_ip=previous_egress_ip,
                rejected_egress_fingerprint=rejected_egress_fingerprint,
            )
            _merge_run_metadata(
                run,
                {
                    "session_acquisition_attempts": attempt,
                    "session_acquisition_last_reason": "accepted",
                    "session_acquisition_egress_changed": egress_changed,
                },
            )
            _record_session_acquisition_attempt_event(
                db,
                run,
                source,
                phase="session_acquisition_attempt_succeeded",
                attempt=attempt,
                attempt_limit=attempt_limit,
                reason=(
                    "explicit_fresh_sticky_accepted"
                    if explicit_retry
                    else "fresh_sticky_accepted"
                    if attempt == 2
                    else "initial_context_accepted"
                ),
                previous_egress_known=previous_egress_known,
                egress_changed=egress_changed,
            )
            return attempt_provider, result
        except Exception as exc:
            observed_egress_ip = _session_acquisition_egress_ip(
                db,
                attempt_provider,
                attempt_metadata,
                exc,
            )
            reason = _session_acquisition_failure_reason(exc)
            _remember_rejected_egress(
                run,
                proxy_profile_id,
                observed_egress_ip,
                settings,
            )
            egress_changed = _session_acquisition_egress_changed(
                observed_egress_ip,
                settings,
                previous_egress_ip=previous_egress_ip,
                rejected_egress_fingerprint=rejected_egress_fingerprint,
            )
            _record_session_acquisition_attempt_event(
                db,
                run,
                source,
                phase="session_acquisition_attempt_failed",
                attempt=attempt,
                attempt_limit=attempt_limit,
                reason=reason,
                previous_egress_known=previous_egress_known,
                egress_changed=egress_changed,
            )
            _close_owned_provider(attempt_provider, owned_provider=True)
            _merge_run_metadata(
                run,
                {
                    "session_acquisition_attempts": attempt,
                    "session_acquisition_last_reason": reason,
                    "session_acquisition_egress_changed": egress_changed,
                },
            )
            db.flush()
            if not _is_recoverable_session_acquisition_error(exc):
                raise

            session_id = _session_acquisition_session_id(attempt_metadata, exc)
            if session_id is not None:
                mark_vinted_session_invalid(
                    db,
                    session_id,
                    reason=f"Session acquisition attempt failed: {reason}",
                    settings=settings,
                )
            datadome_penalty = datadome_penalty or isinstance(exc, DataDomeChallengeError)
            if attempt < attempt_limit:
                previous_egress_ip = observed_egress_ip
                previous_reason = reason
                continue
            if explicit_retry:
                cooldown_minutes = int((run.runtime_metadata or {}).get("proxy_cooldown_minutes", 10))
                if datadome_penalty:
                    mark_proxy_challenge_detected(
                        db,
                        proxy_profile_id,
                        penalty_multiplier=settings.datadome_challenge_penalty_multiplier,
                        cooldown_minutes=cooldown_minutes,
                    )
                else:
                    mark_proxy_run_failure(
                        db,
                        proxy_profile_id,
                        cooldown_minutes=cooldown_minutes,
                    )
                record_run_event(
                    db,
                    run_id=run.id,
                    source_id=source.id,
                    phase="explicit_session_retry_failed",
                    level="warning",
                    proxy_profile_id=proxy_profile_id,
                    auth_mode="public_anonymous",
                    details={"reason": reason, "attempt_limit": 1},
                )
                raise ExplicitSessionRetryError(str(exc)) from exc
            raise ProfileSessionAcquisitionExhaustedError(
                datadome_penalty=datadome_penalty,
            ) from exc

    raise RuntimeError("Session acquisition attempt loop exited unexpectedly")


def _session_acquisition_egress_changed(
    observed_egress_ip: str | None,
    settings,
    *,
    previous_egress_ip: str | None,
    rejected_egress_fingerprint: str | None,
) -> bool:
    if not observed_egress_ip:
        return False
    if rejected_egress_fingerprint:
        return not hmac.compare_digest(
            rejected_egress_fingerprint,
            _egress_identity_fingerprint(observed_egress_ip, settings),
        )
    return bool(previous_egress_ip and observed_egress_ip != previous_egress_ip)


def _search_catalog_once(
    db: Session,
    run: Run,
    source: SearchSource,
    provider: ManualRunProvider,
    proxy_profile_id: int | None,
    settings,
    *,
    prepared_result: CatalogSearchResult | None,
    attempt: int | None,
) -> CatalogSearchResult:
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="catalog_search_start",
        method="GET",
        url=source.url,
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={"session_acquisition_attempt": attempt} if attempt is not None else None,
    )
    result = prepared_result or provider.search(source)
    _persist_provider_session_refresh(db, provider, run, source, proxy_profile_id, settings)
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="catalog_search_success",
        method="GET",
        url=source.url,
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        details={
            "provider": result.provider_metadata,
            **({"session_acquisition_attempt": attempt} if attempt is not None else {}),
        },
    )
    return result


def _record_session_acquisition_attempt_event(
    db: Session,
    run: Run,
    source: SearchSource,
    *,
    phase: str,
    attempt: int,
    attempt_limit: int = 2,
    reason: str,
    previous_egress_known: bool,
    egress_changed: bool,
) -> None:
    metadata = run.runtime_metadata or {}
    profile_id = metadata.get("proxy_profile_id")
    attempted_profile_ids = [
        value
        for value in metadata.get("session_acquisition_profile_ids", [])
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if isinstance(profile_id, int) and not isinstance(profile_id, bool) and profile_id not in attempted_profile_ids:
        attempted_profile_ids.append(profile_id)
        _merge_run_metadata(run, {"session_acquisition_profile_ids": attempted_profile_ids})
        metadata = run.runtime_metadata or {}
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase=phase,
        level="warning" if phase.endswith("_failed") else "info",
        proxy_profile_id=metadata.get("proxy_profile_id"),
        auth_mode="public_anonymous",
        details={
            "proxy_profile_id": metadata.get("proxy_profile_id"),
            "proxy_name": metadata.get("proxy_name"),
            "attempt": attempt,
            "attempt_limit": attempt_limit,
            "reason": reason,
            "previous_egress_known": previous_egress_known,
            "egress_changed": egress_changed,
        },
    )


def _remember_rejected_egress(
    run: Run,
    proxy_profile_id: int | None,
    egress_ip: str | None,
    settings,
) -> None:
    if (
        not isinstance(proxy_profile_id, int)
        or isinstance(proxy_profile_id, bool)
        or not isinstance(egress_ip, str)
        or not egress_ip.strip()
    ):
        return
    current = (run.runtime_metadata or {}).get("session_acquisition_rejected_egress_fingerprints")
    fingerprints = dict(current) if isinstance(current, dict) else {}
    fingerprints[str(proxy_profile_id)] = _egress_identity_fingerprint(egress_ip, settings)
    _merge_run_metadata(
        run,
        {"session_acquisition_rejected_egress_fingerprints": fingerprints},
    )


def _egress_identity_fingerprint(egress_ip: str, settings) -> str:
    digest = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        egress_ip.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v1:{digest}"


def _is_recoverable_session_acquisition_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            DataDomeChallengeError,
            VintedCatalogChallengeError,
            VintedCatalogSessionContextError,
            VintedCatalogSessionError,
            VintedCatalogTransportError,
            VintedEgressDiagnosticError,
            VintedSessionRequiredError,
        ),
    ) and not isinstance(exc, VintedCatalogRateLimitError)


def _session_acquisition_failure_reason(exc: Exception) -> str:
    if isinstance(exc, VintedEgressRotationError):
        return "egress_not_rotated"
    if isinstance(exc, VintedEgressDiagnosticError):
        return "egress_diagnostic_failed"
    if isinstance(exc, DataDomeChallengeError):
        return "datadome_challenge"
    if isinstance(exc, VintedCatalogChallengeError):
        return "cloudflare_challenge"
    if isinstance(exc, VintedCatalogTransportError):
        return "proxy_transport_error"
    if isinstance(exc, VintedCatalogSessionContextError):
        return "catalog_session_context_invalid"
    if isinstance(exc, VintedCatalogSessionError):
        return "catalog_session_rejected"
    if isinstance(exc, VintedSessionRequiredError):
        return "prepared_context_unusable"
    if isinstance(exc, VintedCatalogRateLimitError):
        return "catalog_rate_limited"
    if isinstance(exc, ProxyProfileEligibilityError):
        return "proxy_identity_or_eligibility_changed"
    return "internal_error"


def _session_acquisition_session_id(
    metadata: Mapping[str, Any],
    exc: Exception,
) -> int | None:
    candidate = getattr(exc, "vinted_session_id", None)
    if not isinstance(candidate, int) or isinstance(candidate, bool):
        candidate = metadata.get("vinted_session_id")
    return candidate if isinstance(candidate, int) and not isinstance(candidate, bool) else None


def _session_acquisition_egress_ip(
    db: Session,
    provider: ManualRunProvider | None,
    metadata: Mapping[str, Any],
    exc: Exception | None,
) -> str | None:
    candidates = [
        getattr(exc, "egress_ip", None) if exc is not None else None,
        getattr(provider, "egress_ip", None) if provider is not None else None,
        getattr(getattr(provider, "prepared_session", None), "egress_ip", None)
        if provider is not None
        else None,
    ]
    session_id = (
        _session_acquisition_session_id(metadata, exc)
        if exc is not None
        else metadata.get("vinted_session_id")
    )
    if not isinstance(session_id, int) or isinstance(session_id, bool):
        session_id = None
    if session_id is not None:
        session = db.get(VintedSession, session_id)
        candidates.append(session.egress_ip if session is not None else None)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _provider_for_egress(
    db: Session,
    source: SearchSource,
    egress: RunEgress,
    runtime_config,
    settings,
    *,
    run: Run | None = None,
    include_catalog_payload: bool = False,
    force_new_session: bool = False,
    rejected_egress_ip: str | None = None,
    rejected_egress_fingerprint: str | None = None,
    bypass_cooldown: bool = False,
) -> tuple[CurlCffiVintedCatalogProvider, dict[str, Any], CatalogSearchResult | None]:
    _require_proxy_egress(egress)
    proxy_url = egress.proxy_url
    metadata: dict[str, Any] = {
        "target_country_code": settings.vinted_target_country_code.strip().upper(),
        "locale": settings.vinted_target_locale,
        "accept_language": settings.vinted_target_accept_language,
        "screen": settings.vinted_target_screen,
        "vinted_screen": settings.vinted_target_vinted_screen,
    }
    provider_country_code = settings.vinted_target_country_code.strip().upper()
    provider_locale = settings.vinted_target_locale
    provider_accept_language = settings.vinted_target_accept_language
    provider_viewport_size = settings.vinted_target_screen
    provider_vinted_screen = settings.vinted_target_vinted_screen
    prepared_session = None
    prepared_catalog_result: CatalogSearchResult | None = None
    proxy_marker: dict[str, Any] | None = None
    event_sink = _build_provider_event_sink(db, run, source, egress.proxy_profile_id) if run is not None else None
    profile = (
        lock_and_revalidate_proxy_selection(
            db,
            egress.proxy_profile_id,
            egress.proxy_identity_generation,
            settings,
            bypass_cooldown=True,
        )
        if bypass_cooldown
        else lock_and_revalidate_proxy_selection(
            db,
            egress.proxy_profile_id,
            egress.proxy_identity_generation,
            settings,
        )
    )
    if force_new_session:
        vinted_session, prepared_session, prepared_metadata, prepared_catalog_result = _prepare_vinted_session_for_run(
            db,
            source,
            profile,
            runtime_config,
            settings,
            event_sink=event_sink,
            include_catalog_payload=include_catalog_payload,
            force_egress_diagnostic=True,
            rejected_egress_ip=rejected_egress_ip,
            rejected_egress_fingerprint=rejected_egress_fingerprint,
            egress_diagnostic_attempt=1 if bypass_cooldown else 2,
        )
        metadata.update(prepared_metadata)
        session_action = "prepared"
    else:
        try:
            vinted_session, prepared_session = get_ready_vinted_session(
                db,
                source,
                profile,
                settings=settings,
            )
            session_action = "reused"
        except VintedSessionRequiredError:
            vinted_session, prepared_session, prepared_metadata, prepared_catalog_result = _prepare_vinted_session_for_run(
                db,
                source,
                profile,
                runtime_config,
                settings,
                event_sink=event_sink,
                include_catalog_payload=include_catalog_payload,
            )
            metadata.update(prepared_metadata)
            session_action = "prepared"
    proxy_session_id = vinted_session.proxy_session_id
    proxy_url = proxy_url_with_sticky_session(profile, proxy_session_id, settings)
    proxy_marker = safe_secret_marker("proxy_sticky_session_id", proxy_session_id, kind="proxy_session")
    provider_country_code = profile.country_code
    provider_locale = profile.locale
    provider_accept_language = profile.accept_language
    provider_viewport_size = profile.screen
    provider_vinted_screen = profile.vinted_screen
    metadata["proxy_country_code"] = profile.country_code
    metadata["locale"] = profile.locale
    metadata["accept_language"] = profile.accept_language
    metadata["screen"] = profile.screen
    metadata["vinted_screen"] = profile.vinted_screen
    metadata["vinted_session_id"] = vinted_session.id
    metadata["vinted_session_status"] = vinted_session.status
    metadata["vinted_session_request_count"] = vinted_session.request_count
    metadata["vinted_session_max_requests"] = vinted_session.max_requests
    metadata["vinted_session_action"] = session_action
    metadata["vinted_session_datadome_present"] = bool(prepared_session.datadome or (prepared_session.cookies or {}).get("datadome"))
    metadata["vinted_session_cf_bm_present"] = bool(prepared_session.cf_bm or (prepared_session.cookies or {}).get("__cf_bm"))
    metadata["proxy_session_id_prefix"] = proxy_session_id[:8]
    metadata["proxy_sticky_session"] = proxy_marker
    if run is not None:
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="vinted_session_selected",
            proxy_profile_id=profile.id,
            auth_mode="public_anonymous",
            details={
                "action": session_action,
                "vinted_session_id": vinted_session.id,
                "vinted_session_status": vinted_session.status,
                "vinted_session_use_count": vinted_session.request_count,
                "vinted_session_max_requests": vinted_session.max_requests,
                "context": prepared_context_flags(prepared_session),
                "datadome_required": True,
                "proxy_session": proxy_marker,
            },
        )

    return CurlCffiVintedCatalogProvider(
        settings=settings,
        proxy_url=proxy_url,
        timeout_ms=runtime_config.request_timeout_ms,
        catalog_per_page=runtime_config.catalog_per_page,
        human_delay_min=settings.human_delay_min_seconds,
        human_delay_max=settings.human_delay_max_seconds,
        event_sink=event_sink,
        proxy_session_marker=metadata.get("proxy_sticky_session"),
        expected_country_code=provider_country_code,
        locale=provider_locale,
        accept_language=provider_accept_language,
        screen=provider_vinted_screen,
        viewport_size=provider_viewport_size,
        prepared_session=prepared_session,
        require_datadome_cookie=True,
    ), metadata, prepared_catalog_result


def _require_proxy_egress(egress: RunEgress) -> None:
    if (
        egress.mode != "proxy"
        or not isinstance(egress.proxy_profile_id, int)
        or isinstance(egress.proxy_profile_id, bool)
        or egress.proxy_profile_id <= 0
        or not isinstance(egress.proxy_identity_generation, str)
        or not re.fullmatch(r"v1:[1-9]\d*:[0-9a-f]{64}", egress.proxy_identity_generation)
    ):
        raise SchedulerCapacityError("Catalog execution requires an eligible proxy")


def _prepare_vinted_session_for_run(
    db: Session,
    source: SearchSource,
    proxy_profile: ProxyProfile,
    runtime_config,
    settings,
    *,
    event_sink,
    include_catalog_payload: bool = False,
    force_egress_diagnostic: bool = False,
    rejected_egress_ip: str | None = None,
    rejected_egress_fingerprint: str | None = None,
    egress_diagnostic_attempt: int = 2,
) -> tuple[Any, Any, dict[str, Any], CatalogSearchResult | None]:
    browser_profile = profile_for_impersonate(settings.curl_impersonate_browser)
    proxy_session_id = generate_proxy_session_id()
    proxy_url = proxy_url_with_sticky_session(proxy_profile, proxy_session_id, settings)
    proxy_marker = safe_secret_marker("proxy_sticky_session_id", proxy_session_id, kind="proxy_session")
    if event_sink is not None:
        event_sink(
            phase="vinted_session_prepare_start",
            message="Preparing monitor-owned Vinted session",
            details={
                "monitor_id": source.id,
                "proxy_profile_id": proxy_profile.id,
                "browser_profile": browser_profile.name,
                "impersonate": browser_profile.impersonate,
                "proxy_session": proxy_marker,
                "datadome_required": True,
                "source_url": source.url,
            },
        )
    prevalidated_egress: ProxyEgressProbeResult | None = None
    if force_egress_diagnostic:
        prevalidated_egress = probe_proxy_egress(
            settings=settings,
            profile=browser_profile,
            proxy_url=proxy_url,
            timeout_ms=runtime_config.request_timeout_ms,
            proxy_session_marker=proxy_marker,
            expected_country_code=proxy_profile.country_code,
            event_sink=event_sink,
            attempt=egress_diagnostic_attempt,
        )
        if prevalidated_egress.error is not None:
            raise prevalidated_egress.error
        observed_egress_ip = prevalidated_egress.context.ip
        rejected_egress_matches = bool(
            rejected_egress_ip
            and observed_egress_ip == rejected_egress_ip
        ) or bool(
            rejected_egress_fingerprint
            and hmac.compare_digest(
                rejected_egress_fingerprint,
                _egress_identity_fingerprint(observed_egress_ip, settings),
            )
        )
        if rejected_egress_matches:
            raise VintedEgressRotationError(
                "Fresh proxy sticky resolved to the previously rejected egress",
                egress_ip=observed_egress_ip,
            )

    provider = CurlCffiVintedCatalogProvider(
        settings=settings,
        profile=browser_profile,
        proxy_url=proxy_url,
        timeout_ms=runtime_config.request_timeout_ms,
        catalog_per_page=runtime_config.catalog_per_page,
        human_delay_min=settings.human_delay_min_seconds,
        human_delay_max=settings.human_delay_max_seconds,
        event_sink=event_sink,
        proxy_session_marker=proxy_marker,
        expected_country_code=proxy_profile.country_code,
        locale=proxy_profile.locale,
        accept_language=proxy_profile.accept_language,
        screen=proxy_profile.vinted_screen,
        viewport_size=proxy_profile.screen,
        prevalidated_egress=prevalidated_egress,
        require_datadome_cookie=True,
    )
    try:
        try:
            context_report = provider.bootstrap_for_session(source.url, collect_datadome=True)
            probe = provider.probe_catalog_api(source.url, include_payload=include_catalog_payload)
            prepared = provider.export_prepared_session(proxy_session_id=proxy_session_id)
        except Exception as exc:
            if not getattr(exc, "egress_ip", None):
                egress_ip = getattr(provider, "egress_ip", None)
                if isinstance(egress_ip, str) and egress_ip:
                    exc.egress_ip = egress_ip
            raise
    finally:
        provider.close()

    missing_context = list(probe.get("missing_required") or [])
    missing_prepared = missing_prepared_context(prepared)
    probe_outcome = str(probe.get("outcome") or "unknown")
    usable = probe_outcome == "accepted_json" and not missing_context and not missing_prepared
    if not usable:
        reasons = []
        if probe_outcome != "accepted_json":
            reasons.append(f"probe={probe_outcome}")
        if missing_context:
            reasons.append(f"context={','.join(missing_context)}")
        if missing_prepared:
            reasons.append(f"prepared={','.join(missing_prepared)}")
        last_error = "Prepared Vinted session rejected: " + "; ".join(reasons or ["unknown"])
    else:
        last_error = None
    catalog_result: CatalogSearchResult | None = None
    if usable and include_catalog_payload:
        payload = probe.get("payload")
        if isinstance(payload, dict):
            catalog_result = parse_catalog_api_payload(payload, base_url=str(settings.vinted_base_url))
            catalog_result = replace(
                catalog_result,
                provider_metadata={
                    **catalog_result.provider_metadata,
                    "source": "catalog_api_probe_json",
                    "reused_prepare_probe": True,
                    "probe_status_code": probe.get("status_code"),
                    "probe_duration_ms": probe.get("duration_ms"),
                },
            )
        else:
            usable = False
            last_error = "Prepared Vinted session rejected: probe payload unavailable for baseline"

    saved = save_prepared_vinted_session(
        db,
        source,
        proxy_profile,
        proxy_session_id=proxy_session_id,
        profile=browser_profile,
        context=prepared,
        status=READY if usable else INCOMPLETE,
        settings=settings,
        last_error=last_error,
    )
    if usable:
        mark_vinted_session_used(db, saved)
        prepared.session_id = saved.id
    if event_sink is not None:
        event_sink(
            phase="vinted_session_prepare_result",
            level="info" if usable else "error",
            message="Prepared Vinted session is usable" if usable else "Prepared Vinted session is not usable",
            details={
                "vinted_session_id": saved.id,
                "status": saved.status,
                "probe_outcome": probe_outcome,
                "probe_status_code": probe.get("status_code"),
                "probe_duration_ms": probe.get("duration_ms"),
                "context": prepared_context_flags(prepared),
                "context_report": context_report,
                "missing_required": missing_context,
                "missing_prepared": missing_prepared,
                "datadome_required": True,
                "proxy_session": proxy_marker,
                "vinted_session_use_count": saved.request_count,
                "vinted_session_max_requests": saved.max_requests,
                "last_error": saved.last_error,
            },
        )
    if not usable:
        error = VintedSessionRequiredError(saved.last_error or "Prepared Vinted session is not usable")
        error.vinted_session_id = saved.id
        error.egress_ip = prepared.egress_ip
        raise error
    return saved, prepared, {
        "vinted_session_prepare_probe_outcome": probe_outcome,
        "vinted_session_prepare_probe_status_code": probe.get("status_code"),
        "vinted_session_prepare_probe_duration_ms": probe.get("duration_ms"),
        "session_acquisition_egress_changed": bool(
            force_egress_diagnostic
            and (rejected_egress_ip or rejected_egress_fingerprint)
            and prepared.egress_ip
            and not (
                prepared.egress_ip == rejected_egress_ip
                or (
                    rejected_egress_fingerprint
                    and hmac.compare_digest(
                        rejected_egress_fingerprint,
                        _egress_identity_fingerprint(prepared.egress_ip, settings),
                    )
                )
            )
        ),
    }, catalog_result


def _close_owned_provider(provider: ManualRunProvider, *, owned_provider: bool) -> None:
    if not owned_provider:
        return
    close = getattr(provider, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Cleanup must never replace the run/challenge exception that triggered it.
            pass


def _persist_provider_session_refresh(
    db: Session,
    provider: ManualRunProvider,
    run: Run,
    source: SearchSource,
    proxy_profile_id: int | None,
    settings,
) -> None:
    if not bool(getattr(provider, "prepared_session_refreshed", False)):
        return
    vinted_session_id = (run.runtime_metadata or {}).get("vinted_session_id")
    if not isinstance(vinted_session_id, int):
        return
    export_prepared_session = getattr(provider, "export_prepared_session", None)
    if not callable(export_prepared_session):
        return
    prepared_session = getattr(provider, "prepared_session", None)
    proxy_session_id = getattr(prepared_session, "proxy_session_id", None)
    try:
        refreshed_context = export_prepared_session(proxy_session_id=proxy_session_id)
    except Exception as exc:
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="vinted_session_context_refresh_persist_failed",
            level="warning",
            proxy_profile_id=proxy_profile_id,
            auth_mode="public_anonymous",
            message=redact_sensitive_text(str(exc)),
            details={"vinted_session_id": vinted_session_id},
        )
        return
    refreshed_context.session_id = vinted_session_id
    updated = update_vinted_session_context(
        db,
        vinted_session_id,
        context=refreshed_context,
        settings=settings,
    )
    if updated is None:
        return
    try:
        provider.prepared_session_refreshed = False
    except Exception:
        pass
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="vinted_session_context_refreshed",
        proxy_profile_id=proxy_profile_id,
        auth_mode="public_anonymous",
        message="Prepared Vinted session context updated after silent refresh",
        details={
            "vinted_session_id": updated.id,
            "vinted_session_status": updated.status,
            "context": prepared_context_flags(refreshed_context),
        },
    )


def _active_source_run_exists(
    db: Session,
    *,
    source_id: int,
    include_finalizing: bool = False,
) -> bool:
    stale_cutoff = datetime.now(UTC) - STALE_RUN_AFTER
    stale_runs = list(
        db.scalars(
            select(Run).where(
                Run.source_id == source_id,
                Run.status == RUNNING,
                Run.finished_at.is_(None),
                Run.started_at < stale_cutoff,
            )
        )
    )
    for stale_run in stale_runs:
        message = "Stale running run interrupted during worker crash recovery"
        stale_run.status = FAILED
        stale_run.finished_at = datetime.now(UTC)
        stale_run.error_message = message
        record_run_event(
            db,
            run_id=stale_run.id,
            source_id=source_id,
            phase="stale_run_recovered",
            level="error",
            message=message,
            details={"stale_after_seconds": int(STALE_RUN_AFTER.total_seconds())},
        )
        db.add(
            ErrorLog(
                run_id=stale_run.id,
                source_id=source_id,
                kind="stale_run_recovered",
                message=message,
                details={},
            )
        )
    if stale_runs:
        db.flush()
    active_statuses = [RUNNING, FINALIZING] if include_finalizing else [RUNNING]
    return (
        db.scalar(
            select(Run.id)
            .where(
                Run.source_id == source_id,
                Run.status.in_(active_statuses),
                Run.finished_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def _lock_source_for_run_transition(db: Session, source_id: int) -> SearchSource | None:
    return db.scalar(
        select(SearchSource)
        .where(SearchSource.id == source_id)
        # Admission, stop and terminal writers mutate no source key. NO KEY
        # UPDATE preserves their mutual exclusion without waiting on FK
        # key-share locks from events emitted during the provider request.
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )


def _run_runtime_metadata(source: SearchSource, egress: RunEgress, runtime_config) -> dict:
    return {
        "evaluation_contract": EVALUATION_CONTRACT_VERSION,
        "filter_count": filter_term_count(source.filter_definition),
        "egress_mode": egress.mode,
        "proxy_profile_id": egress.proxy_profile_id,
        "proxy_identity_generation": egress.proxy_identity_generation,
        "proxy_name": egress.proxy_name,
        "proxy_kind": egress.proxy_kind,
        "auth_mode": "public_anonymous",
        "catalog_per_page": runtime_config.catalog_per_page,
        "detail_max_candidates_per_run": runtime_config.detail_max_candidates_per_run,
        "request_timeout_ms": runtime_config.request_timeout_ms,
        "proxy_cooldown_minutes": runtime_config.proxy_cooldown_minutes,
        "stop_monitor_after_consecutive_failures": runtime_config.stop_monitor_after_consecutive_failures,
    }


def _runtime_task_id(runtime_metadata_extra: dict[str, Any] | None) -> str | None:
    raw_task_id = (runtime_metadata_extra or {}).get("task_id")
    if raw_task_id is None:
        return None
    task_id = str(raw_task_id)
    if not task_id or len(task_id) > 64:
        raise ValueError("task_id must contain between 1 and 64 characters")
    return task_id


def _merge_run_metadata(run: Run, metadata: dict[str, Any]) -> None:
    run.runtime_metadata = {**(run.runtime_metadata or {}), **metadata}


_RUN_OBSERVABILITY_METADATA_KEYS = (
    PROXY_TRAFFIC_METADATA_KEY,
    "detail_fetch_mode",
    "detail_fetch_elapsed_ms",
    "detail_fetch_request_duration_total_ms",
    "detail_fetch_attempts",
    "filter_duration_total_ms",
    "persistence_duration_total_ms",
    "vinted_session_id",
    "vinted_session_request_count",
    "session_acquisition_attempts",
    "session_acquisition_last_reason",
    "session_acquisition_egress_changed",
    "session_acquisition_profile_ids",
    "session_acquisition_rejected_egress_fingerprints",
)


def _run_observability_metadata(run: Run) -> dict[str, Any]:
    metadata = run.runtime_metadata or {}
    return {key: metadata[key] for key in _RUN_OBSERVABILITY_METADATA_KEYS if key in metadata}


_SESSION_ACQUISITION_EVENT_PHASES = (
    "session_acquisition_attempt_started",
    "session_acquisition_attempt_failed",
    "session_acquisition_attempt_succeeded",
)


def _snapshot_session_acquisition_events(
    db: Session,
    run_id: int,
) -> list[dict[str, Any]]:
    events = db.scalars(
        select(RunEvent)
        .where(
            RunEvent.run_id == run_id,
            RunEvent.phase.in_(_SESSION_ACQUISITION_EVENT_PHASES),
        )
        .order_by(RunEvent.id.asc())
    )
    return [
        {
            "phase": event.phase,
            "level": event.level,
            "proxy_profile_id": event.proxy_profile_id,
            "auth_mode": event.auth_mode,
            "message": event.message,
            "details": dict(event.details or {}),
        }
        for event in events
    ]


def _restore_session_acquisition_events(
    db: Session,
    run: Run,
    source: SearchSource,
    snapshots: list[dict[str, Any]],
) -> None:
    if not snapshots:
        return
    existing = db.scalar(
        select(func.count())
        .select_from(RunEvent)
        .where(
            RunEvent.run_id == run.id,
            RunEvent.phase.in_(_SESSION_ACQUISITION_EVENT_PHASES),
        )
    )
    if existing:
        return
    for snapshot in snapshots:
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase=snapshot["phase"],
            level=snapshot["level"],
            proxy_profile_id=snapshot["proxy_profile_id"],
            auth_mode=snapshot["auth_mode"],
            message=snapshot["message"],
            details=snapshot["details"],
        )


def _build_provider_event_sink(
    db: Session,
    run: Run | None,
    source: SearchSource,
    proxy_profile_id: int | None,
):
    if run is None:
        return None

    def sink(
        *,
        phase: str,
        method: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        level: str | None = None,
        message: str | None = None,
        details: dict | None = None,
    ) -> None:
        event_details = details or {}
        transfer_observation = event_details.get(PROXY_TRANSFER_DETAIL_KEY)
        if (run.runtime_metadata or {}).get("egress_mode") == "proxy" and isinstance(
            transfer_observation, Mapping
        ):
            current_estimate = (run.runtime_metadata or {}).get(PROXY_TRAFFIC_METADATA_KEY)
            _merge_run_metadata(
                run,
                {
                    PROXY_TRAFFIC_METADATA_KEY: aggregate_proxy_traffic_estimate(
                        current_estimate if isinstance(current_estimate, Mapping) else None,
                        transfer_observation,
                    )
                },
            )
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase=phase,
            method=method,
            url=url,
            status_code=status_code,
            duration_ms=duration_ms,
            level=level,
            proxy_profile_id=proxy_profile_id,
            user_agent=None,
            auth_mode="public_anonymous",
            message=message,
            details=event_details,
        )

    return sink


def _attach_provider_event_sink(
    db: Session,
    provider: ManualRunProvider,
    run: Run,
    source: SearchSource,
    proxy_profile_id: int | None,
) -> None:
    if not hasattr(provider, "event_sink"):
        return
    provider.event_sink = _build_provider_event_sink(db, run, source, proxy_profile_id)


def _reconcile_finalizing_runs(
    db: Session,
    source: SearchSource,
    cache: SeenCache,
    *,
    exclude_run_id: int,
) -> None:
    pending_runs = db.scalars(
        select(Run)
        .where(
            Run.source_id == source.id,
            Run.status == FINALIZING,
            Run.id != exclude_run_id,
        )
        .order_by(Run.id.asc())
    )
    for pending_run in pending_runs:
        _apply_pending_candidate_state_transition(db, pending_run, source, cache, reconciled=True)
        db.commit()


def recover_task_run_before_delivery(
    db: Session,
    *,
    source_id: int,
    task_id: str,
    seen_cache: SeenCache | None = None,
) -> Run | None:
    """Converge a prior delivery of the same task before any new Vinted traffic."""
    previous_run = db.scalar(
        select(Run)
        .where(Run.source_id == source_id, Run.task_id == task_id)
        .order_by(Run.id.desc())
        .limit(1)
    )
    if previous_run is None:
        return None
    if previous_run.status in {SUCCESS, FAILED}:
        return previous_run

    source = _lock_source_for_run_transition(db, source_id)
    if source is None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if previous_run.status == FINALIZING:
        cache = seen_cache or get_seen_cache()
        cache.require_available()
        _apply_pending_candidate_state_transition(db, previous_run, source, cache, reconciled=True)
        db.commit()
        db.refresh(previous_run)
        return previous_run
    if previous_run.status == RUNNING:
        message = "Running task delivery interrupted before queue acknowledgement"
        previous_run.status = FAILED
        previous_run.finished_at = datetime.now(UTC)
        previous_run.error_message = message
        metadata = dict(previous_run.runtime_metadata or {})
        metadata["failure_kind"] = "worker_task_delivery_interrupted"
        previous_run.runtime_metadata = metadata
        record_run_event(
            db,
            run_id=previous_run.id,
            source_id=source_id,
            phase="worker_task_delivery_recovered",
            level="error",
            message=message,
            details={"task_id": task_id, "recovery_action": "close_orphan_and_redeliver"},
        )
        db.add(
            ErrorLog(
                run_id=previous_run.id,
                source_id=source_id,
                kind="worker_task_delivery_recovered",
                message=message,
                details={"task_id": task_id},
            )
        )
        _close_draining_monitor_session(db, previous_run, source, level="warning")
        db.commit()
        db.refresh(previous_run)
        return previous_run
    raise ValueError(f"Run {previous_run.id} has unsupported task recovery status {previous_run.status!r}")


def _apply_pending_candidate_state_transition(
    db: Session,
    run: Run,
    source: SearchSource,
    cache: SeenCache,
    *,
    reconciled: bool,
) -> None:
    metadata = dict(run.runtime_metadata or {})
    policy_hash = metadata.get("candidate_state_transition_policy_hash")
    payload = metadata.get("candidate_state_transition")
    if not isinstance(policy_hash, str) or not policy_hash or payload is None:
        raise ValueError(f"Run {run.id} has no recoverable candidate state transition")
    update = deserialize_candidate_state_update(payload)
    cache.finalize_candidate_states(source.id, policy_hash, update)
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="redis_candidate_state_reconciled" if reconciled else "redis_candidate_state_updated",
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        details={
            "marked_seen_count": len(update.terminal_ids),
            "policy_hash": policy_hash,
            "reconciled": reconciled,
        },
    )
    _complete_finalizing_run(
        db,
        run,
        source,
        close_session_on_finish=bool(metadata.get("candidate_state_close_session_on_finish")),
        reconciled=reconciled,
    )


def _close_draining_monitor_session(
    db: Session,
    run: Run,
    source: SearchSource,
    *,
    level: str | None = None,
) -> bool:
    if not _is_draining_monitor_session(db, run, source):
        return False
    db.flush()
    other_non_terminal_run_id = db.scalar(
        select(Run.id)
        .where(
            Run.id != run.id,
            Run.source_id == source.id,
            Run.monitor_session_id == run.monitor_session_id,
            Run.status.in_((RUNNING, FINALIZING)),
            Run.finished_at.is_(None),
        )
        .limit(1)
    )
    if other_non_terminal_run_id is not None:
        return False
    closed_session = stop_active_monitor_session(
        db,
        source.id,
        stopped_at=run.finished_at or datetime.now(UTC),
        reason="stopped",
    )
    if closed_session is None:
        return False
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="monitor_session_closed",
        level=level,
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        message="Monitor session closed after stop request",
        details={"monitor_session_id": run.monitor_session_id, "reason": "stopped"},
    )
    return True


def _is_draining_monitor_session(db: Session, run: Run, source: SearchSource) -> bool:
    if source.is_active or run.monitor_session_id is None:
        return False
    active_session = get_active_monitor_session(db, source.id)
    return active_session is not None and active_session.id == run.monitor_session_id


def _complete_finalizing_run(
    db: Session,
    run: Run,
    source: SearchSource,
    *,
    close_session_on_finish: bool,
    reconciled: bool,
) -> None:
    mark_proxy_run_success(db, (run.runtime_metadata or {}).get("proxy_profile_id"))
    db.flush()
    current_source = _lock_source_for_run_transition(db, source.id)
    if current_source is None:
        raise SearchSourceNotFoundError(f"Search source {source.id} does not exist")
    source = current_source
    run.status = SUCCESS
    run.finished_at = datetime.now(UTC)
    run.error_message = None
    source.last_run_at = run.finished_at
    drain_requested = _is_draining_monitor_session(db, run, source)
    if drain_requested:
        _close_draining_monitor_session(db, run, source)
    if not drain_requested and close_session_on_finish and run.monitor_session_id is not None:
        stop_active_monitor_session(db, source.id, stopped_at=run.finished_at, reason="completed")
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="monitor_session_closed",
            proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
            message="Monitor session closed after run completion",
            details={"monitor_session_id": run.monitor_session_id, "reason": "completed"},
        )
    elif not drain_requested and not close_session_on_finish:
        _stop_monitor_if_vinted_session_use_limit_reached(db, run, source)
    if close_session_on_finish:
        _clear_manual_monitor_runtime(source)
    metadata = dict(run.runtime_metadata or {})
    metadata.pop("candidate_state_transition", None)
    metadata["candidate_state_transition_status"] = "applied"
    run.runtime_metadata = metadata
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_succeeded",
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        auth_mode="public_anonymous",
        details={
            "items_found": run.items_found,
            "items_filter_passed": run.items_filter_passed,
            "items_discarded_by_filters": run.items_discarded_by_filters,
            "items_filter_pending": run.items_filter_pending,
            "opportunities_created": run.opportunities_created,
            "candidate_state_reconciled": reconciled,
        },
    )


def _catalog_terminal_failure_kind(exc: Exception) -> str:
    if isinstance(exc, VintedCatalogChallengeError):
        return "cloudflare_challenge"
    if isinstance(exc, DataDomeChallengeError):
        return "datadome_challenge"
    if isinstance(exc, VintedCatalogRateLimitError):
        return "catalog_rate_limited"
    if isinstance(exc, VintedCatalogSessionContextError):
        return "catalog_session_context_invalid"
    return "catalog_session_rejected"


def _record_failed_run(
    db: Session,
    run: Run,
    source: SearchSource,
    exc: Exception,
    *,
    kind: str | None = None,
    penalize_proxy: bool = False,
    force_stop_monitor: bool = False,
    monitor_stop_reason: str = "failed",
) -> Run:
    message = redact_sensitive_text(str(exc))
    failure_kind = kind or getattr(exc, "failure_kind", exc.__class__.__name__)
    _merge_run_metadata(run, {"failure_kind": failure_kind})
    session_failure = _classify_session_failure(exc, kind=kind)
    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
    event_proxy_profile_id = proxy_profile_id
    if isinstance(exc, ProxyProfileEligibilityError) and isinstance(proxy_profile_id, int):
        existing_proxy_id = db.scalar(select(ProxyProfile.id).where(ProxyProfile.id == proxy_profile_id))
        if existing_proxy_id is None:
            event_proxy_profile_id = None
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_failed",
        level="error",
        message=message,
        proxy_profile_id=event_proxy_profile_id,
        user_agent=None,
        auth_mode="public_anonymous",
        details={
            "kind": failure_kind,
            "session_end_reason": session_failure["session_end_reason"],
            "recovery_action": session_failure["recovery_action"],
            "vinted_session_id": (run.runtime_metadata or {}).get("vinted_session_id"),
            "vinted_session_use_count": (run.runtime_metadata or {}).get("vinted_session_request_count"),
            "session_acquisition_attempts": (run.runtime_metadata or {}).get("session_acquisition_attempts"),
            "session_acquisition_last_reason": (run.runtime_metadata or {}).get(
                "session_acquisition_last_reason"
            ),
            "session_acquisition_egress_changed": (run.runtime_metadata or {}).get(
                "session_acquisition_egress_changed"
            ),
        },
    )
    vinted_session_id = (run.runtime_metadata or {}).get("vinted_session_id")
    if isinstance(
        exc,
        (DataDomeChallengeError, VintedCatalogRateLimitError, VintedCatalogSessionError, VintedCatalogSessionContextError),
    ):
        mark_vinted_session_invalid(db, vinted_session_id, reason=message)
    cooldown_minutes = int((run.runtime_metadata or {}).get("proxy_cooldown_minutes", 10))
    if isinstance(exc, ProfileSessionAcquisitionExhaustedError):
        if exc.datadome_penalty:
            mark_proxy_challenge_detected(
                db,
                proxy_profile_id,
                penalty_multiplier=get_settings().datadome_challenge_penalty_multiplier,
                cooldown_minutes=cooldown_minutes,
            )
        else:
            mark_proxy_run_failure(db, proxy_profile_id, cooldown_minutes=cooldown_minutes)
    elif isinstance(exc, DataDomeChallengeError):
        mark_proxy_challenge_detected(
            db,
            proxy_profile_id,
            penalty_multiplier=get_settings().datadome_challenge_penalty_multiplier,
            cooldown_minutes=cooldown_minutes,
        )
    elif isinstance(exc, VintedCatalogChallengeError):
        mark_proxy_run_failure(db, proxy_profile_id, cooldown_minutes=cooldown_minutes)
    elif penalize_proxy:
        mark_proxy_run_failure(db, proxy_profile_id, cooldown_minutes=cooldown_minutes)
    db.flush()
    current_source = _lock_source_for_run_transition(db, source.id)
    if current_source is None:
        raise SearchSourceNotFoundError(f"Search source {source.id} does not exist")
    source = current_source
    run.status = FAILED
    run.finished_at = datetime.now(UTC)
    run.error_message = message
    drain_requested = not force_stop_monitor and _is_draining_monitor_session(db, run, source)
    stopped_after_request = drain_requested and _close_draining_monitor_session(db, run, source, level="warning")
    should_stop_monitor = stopped_after_request or (
        not drain_requested
        and _should_stop_monitor_after_failure(
            db,
            run,
            source,
            force_stop_monitor=force_stop_monitor,
        )
    )
    if run.monitor_session_id is not None and should_stop_monitor:
        if not stopped_after_request:
            closed_session = stop_active_monitor_session(
                db,
                source.id,
                stopped_at=run.finished_at,
                reason=monitor_stop_reason,
            )
            if closed_session is not None:
                record_run_event(
                    db,
                    run_id=run.id,
                    source_id=source.id,
                    phase="monitor_session_closed",
                    level="warning",
                    proxy_profile_id=event_proxy_profile_id,
                    message="Monitor session closed after run failure",
                    details={"monitor_session_id": run.monitor_session_id, "reason": closed_session.stop_reason},
                )
        source.is_active = False
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
    elif run.monitor_session_id is not None and not drain_requested:
        _stop_monitor_if_vinted_session_use_limit_reached(db, run, source)
    _clear_manual_monitor_runtime(source)
    db.add(
        ErrorLog(
            run_id=run.id,
            source_id=source.id,
            kind=failure_kind,
            message=message,
            details={},
        )
    )
    db.commit()
    if session_failure["recovery_action"] in {
        "invalidate_session_and_end_attempt",
    } and isinstance(vinted_session_id, int):
        persisted_session = db.get(VintedSession, vinted_session_id)
        if persisted_session is not None and persisted_session.status != "invalid":
            mark_vinted_session_invalid(db, vinted_session_id, reason=message)
            db.commit()
    db.refresh(run)
    return run


def _stop_monitor_if_vinted_session_use_limit_reached(db: Session, run: Run, source: SearchSource) -> bool:
    if source.monitor_mode == "manual" or run.monitor_session_id is None:
        return False
    limit = _stop_after_vinted_session_uses(source)
    if limit is None:
        return False
    vinted_session_id = (run.runtime_metadata or {}).get("vinted_session_id")
    if not isinstance(vinted_session_id, int):
        return False
    use_count = _completed_run_count_for_vinted_session(db, run, vinted_session_id)
    if use_count < limit:
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="vinted_session_use_count_checked",
            proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
            auth_mode="public_anonymous",
            details={
                "vinted_session_id": vinted_session_id,
                "vinted_session_use_count": use_count,
                "stop_after_vinted_session_uses": limit,
                "limit_reached": False,
            },
        )
        return False

    stop_active_monitor_session(db, source.id, stopped_at=run.finished_at or datetime.now(UTC), reason="vinted_session_use_limit_reached")
    source.is_active = False
    source.monitor_started_at = None
    source.monitor_until = None
    source.next_run_at = None
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="vinted_session_use_limit_reached",
        level="warning",
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        auth_mode="public_anonymous",
        message="Monitor stopped after configured Vinted session use limit",
        details={
            "monitor_session_id": run.monitor_session_id,
            "vinted_session_id": vinted_session_id,
            "vinted_session_use_count": use_count,
            "stop_after_vinted_session_uses": limit,
            "session_end_reason": "vinted_session_use_limit_reached",
            "recovery_action": "manual_review_or_relaunch",
        },
    )
    return True


def _stop_after_vinted_session_uses(source: SearchSource) -> int | None:
    value = (source.scheduler_config or {}).get("stop_after_vinted_session_uses")
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _completed_run_count_for_vinted_session(db: Session, run: Run, vinted_session_id: int) -> int:
    if run.monitor_session_id is None:
        return 0
    count = 1 if run.status in {SUCCESS, FAILED} else 0
    previous_runs = db.scalars(
        select(Run).where(
            Run.source_id == run.source_id,
            Run.monitor_session_id == run.monitor_session_id,
            Run.id != run.id,
            Run.status.in_([SUCCESS, FAILED]),
        )
    )
    for previous in previous_runs:
        if (previous.runtime_metadata or {}).get("vinted_session_id") == vinted_session_id:
            count += 1
    return count


def _classify_session_failure(exc: Exception, *, kind: str | None = None) -> dict[str, str]:
    text = str(exc).lower()
    if isinstance(exc, ExplicitSessionRetryError):
        return {
            "session_end_reason": "explicit_session_retry_failed",
            "recovery_action": "retry_manually_or_wait_for_cooldown",
        }
    if isinstance(exc, SessionAcquisitionExhaustedError):
        return {
            "session_end_reason": "session_acquisition_exhausted",
            "recovery_action": "wait_for_eligible_proxy_or_retry_manually",
        }
    if isinstance(exc, ProfileSessionAcquisitionExhaustedError):
        return {
            "session_end_reason": "profile_session_acquisition_exhausted",
            "recovery_action": "wait_for_proxy_cooldown_or_retry_manually",
        }
    if isinstance(exc, BaselineRequiredError):
        return {
            "session_end_reason": "baseline_required",
            "recovery_action": "start_new_session",
        }
    if isinstance(exc, ProxyProfileEligibilityError):
        return {
            "session_end_reason": "proxy_selection_stale_or_ineligible",
            "recovery_action": "issue_fresh_command_after_proxy_review",
        }
    if isinstance(exc, VintedCatalogChallengeError) or kind == "cloudflare_challenge":
        return {
            "session_end_reason": "cloudflare_challenge",
            "recovery_action": "invalidate_session_and_end_attempt",
        }
    if isinstance(exc, DataDomeChallengeError) or kind == "datadome_challenge":
        return {
            "session_end_reason": "datadome_challenge",
            "recovery_action": "invalidate_session_and_end_attempt",
        }
    if isinstance(exc, VintedCatalogSessionContextError):
        return {
            "session_end_reason": "catalog_context_incomplete",
            "recovery_action": "invalidate_session_and_end_attempt",
        }
    if isinstance(exc, VintedCatalogSessionError):
        return {
            "session_end_reason": "catalog_session_rejected",
            "recovery_action": "invalidate_session_and_end_attempt",
        }
    if isinstance(exc, VintedCatalogRateLimitError) or kind == "catalog_rate_limited":
        return {
            "session_end_reason": "catalog_rate_limited",
            "recovery_action": "invalidate_session_and_end_attempt",
        }
    if isinstance(exc, VintedSessionRequiredError):
        return {
            "session_end_reason": "session_preparation_unusable",
            "recovery_action": "prepare_new_sticky_session",
        }
    if "timeout" in text or "timed out" in text or "operation timed out" in text:
        return {
            "session_end_reason": "proxy_or_network_timeout",
            "recovery_action": "retry_with_new_sticky_if_repeated",
        }
    if "proxy" in text:
        return {
            "session_end_reason": "proxy_transport_error",
            "recovery_action": "cooldown_proxy_on_repeated_failure",
        }
    return {
        "session_end_reason": "run_error",
        "recovery_action": "inspect_run_log",
    }


def _should_stop_monitor_after_failure(db: Session, run: Run, source: SearchSource, *, force_stop_monitor: bool) -> bool:
    if force_stop_monitor or source.monitor_mode == "manual":
        return True
    threshold = int((run.runtime_metadata or {}).get("stop_monitor_after_consecutive_failures", 1))
    if threshold <= 1:
        return True
    previous_statuses = list(
        db.scalars(
            select(Run.status)
            .where(
                Run.source_id == source.id,
                Run.id != run.id,
            )
            .order_by(Run.started_at.desc(), Run.id.desc())
            .limit(threshold - 1)
        )
    )
    consecutive_failures = 1
    for status in previous_statuses:
        if status != FAILED:
            break
        consecutive_failures += 1
    return consecutive_failures >= threshold


def _clear_manual_monitor_runtime(source: SearchSource) -> None:
    if source.monitor_mode != "manual":
        return
    source.is_active = False
    source.monitor_started_at = None
    source.monitor_until = None
    source.next_run_at = None


def _detail_candidate_limit(run: Run, provider: ManualRunProvider) -> int:
    settings = get_settings()
    provider_settings = getattr(provider, "settings", settings)
    runtime_limit = (run.runtime_metadata or {}).get("detail_max_candidates_per_run")
    configured_limit = (
        runtime_limit
        if runtime_limit is not None
        else getattr(provider_settings, "vinted_detail_max_candidates_per_run", settings.vinted_detail_max_candidates_per_run)
    )
    return max(int(configured_limit), 0)


def _detail_required_fields(provider_settings: Any, default_settings: Any) -> frozenset[str]:
    configured = getattr(
        provider_settings,
        "vinted_detail_required_fields",
        getattr(default_settings, "vinted_detail_required_fields", DEFAULT_DETAIL_REQUIRED_FIELDS),
    )
    if isinstance(configured, str):
        fields = {field.strip() for field in configured.split(",") if field.strip()}
    else:
        fields = {str(field).strip() for field in configured if str(field).strip()}
    return frozenset(fields or DEFAULT_DETAIL_REQUIRED_FIELDS)


def _missing_required_detail_fields(item: Item, required_fields: frozenset[str]) -> list[str]:
    missing: list[str] = []
    for field_name in sorted(required_fields):
        value = getattr(item, field_name, None)
        if field_name == "description":
            is_missing = value is None
        elif field_name == "photos":
            is_missing = not isinstance(value, list) or not value
        elif isinstance(value, str):
            is_missing = not value.strip()
        else:
            is_missing = value is None
        if is_missing:
            missing.append(field_name)
    return missing


def _detail_failure_kind(exc: Exception) -> str:
    explicit_kind = getattr(exc, "failure_kind", None)
    if isinstance(explicit_kind, str) and explicit_kind:
        return explicit_kind
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return f"detail_http_{status_code}"
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "detail_timeout"
    if "parse" in text or "detail data" in text or "html" in text:
        return "detail_invalid_document"
    return "detail_transport_or_parser_error"


def _fetch_monitor_candidate_detail(
    provider: ManualRunProvider,
    candidate: CatalogItemCandidate,
    *,
    referer_url: str,
    early_filter_terms: tuple[str, ...],
    prefetched_outcome: DetailFetchOutcome | None,
) -> CatalogItemDetail:
    if prefetched_outcome is not None:
        if prefetched_outcome.error is not None:
            raise prefetched_outcome.error
        if prefetched_outcome.detail is None:
            raise ValueError("Prefetched detail outcome omitted both detail and error")
        return prefetched_outcome.detail
    if isinstance(provider, CurlCffiVintedCatalogProvider):
        return provider.fetch_detail(
            candidate,
            referer_url=referer_url,
            early_filter_terms=early_filter_terms,
        )
    return provider.fetch_detail(candidate, referer_url=referer_url)


def _evaluate_monitor_candidates(
    db: Session,
    provider: ManualRunProvider,
    source: SearchSource,
    run: Run,
    work_items: list[DetailWorkItem],
    filters: list[dict],
    *,
    max_detail_candidates: int | None = None,
) -> MonitorEvaluationResult:
    if not work_items:
        return MonitorEvaluationResult(0, 0, 0, 0, ())

    passed = 0
    discarded = 0
    pending = 0
    opportunities_created = 0
    terminal_ids: list[str] = []
    detail_fetch_elapsed_ms = 0
    detail_fetch_request_duration_total_ms = 0
    detail_fetch_attempts = 0
    filter_duration_total_ms = 0.0
    persistence_duration_total_ms = 0.0
    resolved_detail_limit = (
        _detail_candidate_limit(run, provider) if max_detail_candidates is None else max(max_detail_candidates, 0)
    )
    settings = get_settings()
    provider_settings = getattr(provider, "settings", settings)
    required_fields = _detail_required_fields(provider_settings, settings)
    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
    detail_attempts = 0
    early_filter_terms = filter_snapshot_terms(filters)
    prefetched_outcomes: dict[str, DetailFetchOutcome] = {}
    detail_fetch_mode = str(getattr(provider_settings, "vinted_detail_fetch_mode", "serial"))
    if (
        isinstance(provider, CurlCffiVintedCatalogProvider)
        and detail_fetch_mode in {"canary", "parallel"}
        and resolved_detail_limit > 0
    ):
        batch_candidates = [work_item.candidate for work_item in work_items[:resolved_detail_limit]]
        try:
            batch_result = provider.fetch_detail_batch(
                batch_candidates,
                referer_url=source.url,
                early_filter_terms=early_filter_terms,
                concurrency=int(getattr(provider_settings, "vinted_detail_concurrency", 1)),
                canary=detail_fetch_mode == "canary",
            )
        except Exception as exc:
            batch_telemetry = getattr(exc, DETAIL_BATCH_TELEMETRY_ATTR, None)
            if isinstance(batch_telemetry, Mapping):
                timing_metadata = {
                    key: value
                    for key in (
                        "detail_fetch_elapsed_ms",
                        "detail_fetch_request_duration_total_ms",
                        "detail_fetch_attempts",
                    )
                    if isinstance((value := batch_telemetry.get(key)), int)
                    and not isinstance(value, bool)
                    and value >= 0
                }
                _merge_run_metadata(run, {"detail_fetch_mode": detail_fetch_mode, **timing_metadata})
            raise
        prefetched_outcomes = {
            outcome.candidate.vinted_item_id: outcome for outcome in batch_result.outcomes
        }
        detail_fetch_elapsed_ms = batch_result.makespan_ms
        detail_fetch_request_duration_total_ms = batch_result.summed_duration_ms
        detail_fetch_attempts = sum(
            1 for outcome in batch_result.outcomes if not isinstance(outcome.error, VintedDetailDeferred)
        )
        _merge_run_metadata(
            run,
            {
                "detail_fetch_mode": detail_fetch_mode,
                "detail_concurrency_configured": batch_result.configured_concurrency,
                "detail_concurrency_effective": batch_result.effective_concurrency,
                "detail_batch_makespan_ms": batch_result.makespan_ms,
                "detail_batch_summed_duration_ms": batch_result.summed_duration_ms,
                "detail_cookie_divergence_names": list(batch_result.divergent_cookie_names),
            },
        )
        db.flush()

    for work_item in work_items:
        candidate = work_item.candidate
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="candidate_evaluation_start",
            url=candidate.url,
            proxy_profile_id=proxy_profile_id,
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "title": candidate.title,
                "price_amount": str(candidate.price_amount),
                "currency": candidate.currency,
                "view_count": candidate.view_count,
                "brand": candidate.brand,
                "size": candidate.size,
                "filter_count": filter_snapshot_term_count(filters),
            },
        )
        transient_item = build_transient_catalog_item(candidate)
        evaluation_status = SESSION_ITEM_PASSED_WITHOUT_FILTERS if not filters else SESSION_ITEM_PASSED
        matched_terms: list[str] = []
        detail: CatalogItemDetail | None = None
        detail_error: str | None = None

        if detail_attempts < resolved_detail_limit:
            detail_attempts += 1
            prefetched_outcome = prefetched_outcomes.get(candidate.vinted_item_id)
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="candidate_detail_required",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                details={
                    "vinted_item_id": candidate.vinted_item_id,
                    "request_position": detail_attempts,
                    "max_detail_candidates": resolved_detail_limit,
                    "reason": "filters_configured" if filters else "opportunity_enrichment",
                },
            )
            for attempt_number in (1, 2):
                attempt_prefetched_outcome = prefetched_outcome if attempt_number == 1 else None
                record_run_event(
                    db,
                    run_id=run.id,
                    source_id=source.id,
                    phase="detail_fetch_joined" if attempt_prefetched_outcome is not None else "detail_fetch_start",
                    method="GET",
                    url=candidate.url,
                    proxy_profile_id=proxy_profile_id,
                    user_agent=None,
                    auth_mode="public_anonymous",
                    details={
                        "vinted_item_id": candidate.vinted_item_id,
                        "attempt": attempt_number,
                        "referer_url": source.url,
                        "prefetched": attempt_prefetched_outcome is not None,
                    },
                )
                detail_started_at = time.perf_counter()
                detail_duration_ms = (
                    attempt_prefetched_outcome.duration_ms if attempt_prefetched_outcome is not None else None
                )
                try:
                    try:
                        detail = _fetch_monitor_candidate_detail(
                            provider,
                            candidate,
                            referer_url=source.url,
                            early_filter_terms=early_filter_terms,
                            prefetched_outcome=attempt_prefetched_outcome,
                        )
                    except Exception:
                        if attempt_prefetched_outcome is None:
                            measured_duration_ms = _elapsed_ms(detail_started_at)
                            detail_fetch_elapsed_ms += measured_duration_ms
                            detail_fetch_request_duration_total_ms += measured_duration_ms
                            detail_fetch_attempts += 1
                        raise
                    else:
                        if attempt_prefetched_outcome is None:
                            measured_duration_ms = _elapsed_ms(detail_started_at)
                            detail_fetch_elapsed_ms += measured_duration_ms
                            detail_fetch_request_duration_total_ms += measured_duration_ms
                            detail_fetch_attempts += 1
                    if detail.vinted_item_id != candidate.vinted_item_id:
                        raise ValueError(
                            f"Detail item id {detail.vinted_item_id} does not match requested item {candidate.vinted_item_id}"
                        )
                    detail_error = None
                    apply_item_detail_data(transient_item, detail)
                    missing_required = _missing_required_detail_fields(transient_item, required_fields)
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_fetch_success",
                        method="GET",
                        url=candidate.url,
                        duration_ms=(
                            detail_duration_ms if detail_duration_ms is not None else _elapsed_ms(detail_started_at)
                        ),
                        proxy_profile_id=proxy_profile_id,
                        user_agent=None,
                        auth_mode="public_anonymous",
                        details={
                            "vinted_item_id": candidate.vinted_item_id,
                            "attempt": attempt_number,
                            "description_observed": detail.description is not None
                            or "description" in detail.observed_fields,
                            "photo_count": len(detail.photos),
                            "has_total_price": detail.total_price_amount is not None,
                            "availability_state": detail.availability_flags.get("state"),
                            "missing_required": missing_required,
                            "field_sources": detail.field_sources,
                        },
                    )
                    if missing_required:
                        pending += 1
                        evaluation_status = SESSION_ITEM_DETAIL_ERROR
                        terminal_ids.append(candidate.vinted_item_id)
                        record_run_event(
                            db,
                            run_id=run.id,
                            source_id=source.id,
                            phase="detail_incomplete",
                            level="warning",
                            url=candidate.url,
                            proxy_profile_id=proxy_profile_id,
                            message="Valid item document is missing configured required detail fields",
                            details={
                                "vinted_item_id": candidate.vinted_item_id,
                                "missing_required": missing_required,
                                "required_fields": sorted(required_fields),
                                "terminal": True,
                            },
                        )
                    break
                except VintedDetailDeferred as exc:
                    pending += 1
                    evaluation_status = SESSION_ITEM_PASSED_WITHOUT_DETAIL
                    detail_error = str(exc)
                    terminal_ids.append(candidate.vinted_item_id)
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_fetch_skipped",
                        level="warning",
                        url=candidate.url,
                        proxy_profile_id=proxy_profile_id,
                        message="Detail skipped after a concurrent wave was rate limited",
                        details={
                            "vinted_item_id": candidate.vinted_item_id,
                            "attempt": attempt_number,
                            "terminal": True,
                            "reason": str(exc),
                        },
                    )
                    break
                except VintedItemEarlyDiscard as exc:
                    evaluation_status = SESSION_ITEM_DISCARDED
                    matched_terms = exc.matched_terms
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_fetch_early_discard",
                        method="GET",
                        url=candidate.url,
                        duration_ms=(
                            detail_duration_ms if detail_duration_ms is not None else _elapsed_ms(detail_started_at)
                        ),
                        proxy_profile_id=proxy_profile_id,
                        auth_mode="public_anonymous",
                        details={
                            "vinted_item_id": candidate.vinted_item_id,
                            "attempt": attempt_number,
                            "filter_scope": "description",
                            "match_count": len(matched_terms),
                        },
                    )
                    break
                except (
                    DataDomeChallengeError,
                    VintedCatalogChallengeError,
                    VintedCatalogRateLimitError,
                    VintedCatalogSessionContextError,
                    VintedCatalogSessionError,
                ) as exc:
                    _merge_run_metadata(
                        run,
                        {
                            "detail_fetch_mode": detail_fetch_mode,
                            "detail_fetch_elapsed_ms": detail_fetch_elapsed_ms,
                            "detail_fetch_request_duration_total_ms": detail_fetch_request_duration_total_ms,
                            "detail_fetch_attempts": detail_fetch_attempts,
                            "filter_duration_total_ms": round(filter_duration_total_ms, 3),
                            "persistence_duration_total_ms": round(persistence_duration_total_ms, 3),
                        },
                    )
                    exc.detail_candidate_id = candidate.vinted_item_id
                    failure_kind = _catalog_terminal_failure_kind(exc)
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_fetch_error",
                        method="GET",
                        url=candidate.url,
                        duration_ms=(
                            detail_duration_ms if detail_duration_ms is not None else _elapsed_ms(detail_started_at)
                        ),
                        level="error",
                        proxy_profile_id=proxy_profile_id,
                        user_agent=None,
                        auth_mode="public_anonymous",
                        message=redact_sensitive_text(str(exc)),
                        details={
                            "vinted_item_id": candidate.vinted_item_id,
                            "attempt": attempt_number,
                            "kind": failure_kind,
                        },
                    )
                    raise
                except Exception as exc:
                    detail = None
                    detail_error = redact_sensitive_text(str(exc))
                    status_code = getattr(exc, "status_code", None)
                    terminal_http_error = status_code in {404, 410}
                    retry_exhausted = attempt_number == 2
                    failure_kind = _detail_failure_kind(exc)
                    terminal = terminal_http_error or retry_exhausted
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_fetch_error",
                        method="GET",
                        url=candidate.url,
                        duration_ms=(
                            detail_duration_ms if detail_duration_ms is not None else _elapsed_ms(detail_started_at)
                        ),
                        level="error",
                        proxy_profile_id=proxy_profile_id,
                        user_agent=None,
                        auth_mode="public_anonymous",
                        message=detail_error,
                        details={
                            "vinted_item_id": candidate.vinted_item_id,
                            "attempt": attempt_number,
                            "kind": failure_kind,
                            "status_code": status_code,
                            "terminal": terminal,
                            "retry_exhausted": retry_exhausted,
                        },
                    )
                    if terminal:
                        pending += 1
                        evaluation_status = SESSION_ITEM_DETAIL_ERROR
                        terminal_ids.append(candidate.vinted_item_id)
                        if retry_exhausted:
                            record_run_event(
                                db,
                                run_id=run.id,
                                source_id=source.id,
                                phase="detail_retry_exhausted",
                                level="error",
                                url=candidate.url,
                                proxy_profile_id=proxy_profile_id,
                                message="Immediate detail retry exhausted; candidate will be marked seen",
                                details={
                                    "vinted_item_id": candidate.vinted_item_id,
                                    "attempt_count": attempt_number,
                                    "failure_kind": failure_kind,
                                },
                            )
                        break
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_retry_scheduled",
                        level="warning",
                        url=candidate.url,
                        proxy_profile_id=proxy_profile_id,
                        message="Recoverable detail failure will retry once in the current run",
                        details={
                            "vinted_item_id": candidate.vinted_item_id,
                            "attempt_count": attempt_number,
                            "delay_seconds": DETAIL_RETRY_DELAY_SECONDS,
                            "failure_kind": failure_kind,
                        },
                    )
                    detail_fetch_elapsed_ms += round(DETAIL_RETRY_DELAY_SECONDS * 1000)
                    time.sleep(DETAIL_RETRY_DELAY_SECONDS)
        else:
            pending += 1
            evaluation_status = SESSION_ITEM_PASSED_WITHOUT_DETAIL
            terminal_ids.append(candidate.vinted_item_id)
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="detail_fetch_skipped",
                level="warning",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                message="Detail fetch limit reached; candidate closed without deferred work",
                details={
                    "vinted_item_id": candidate.vinted_item_id,
                    "max_detail_candidates": resolved_detail_limit,
                    "terminal": True,
                },
            )

        filter_duration_ms = 0.0
        if detail is not None and candidate.vinted_item_id not in terminal_ids and evaluation_status == SESSION_ITEM_PASSED:
            filter_started_at = time.perf_counter_ns()
            decision = evaluate_exclusion_filters(transient_item, filters)
            filter_duration_ms = round((time.perf_counter_ns() - filter_started_at) / 1_000_000, 3)
            filter_duration_total_ms += filter_duration_ms
            evaluation_status = decision.status
            matched_terms = decision.matched_terms

        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="candidate_filter_decision",
            level="warning" if evaluation_status == SESSION_ITEM_DISCARDED else None,
            url=candidate.url,
            proxy_profile_id=proxy_profile_id,
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "evaluation_status": evaluation_status,
                "filter_scope": "description",
                "match_count": len(matched_terms),
                "matched_terms": matched_terms,
                "detail_error": detail_error,
                "filter_duration_ms": filter_duration_ms,
            },
        )

        if evaluation_status == SESSION_ITEM_DISCARDED:
            discarded += 1
            terminal_ids.append(candidate.vinted_item_id)
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="item_discarded",
                level="warning",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                message=f"Matched blacklist terms: {', '.join(matched_terms)}",
                details={"vinted_item_id": candidate.vinted_item_id, "matched_terms": matched_terms},
            )
            continue

        if detail is None:
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="opportunity_skipped_missing_detail",
                level="warning",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                message="Opportunity skipped because item detail was not available",
                details={
                    "vinted_item_id": candidate.vinted_item_id,
                    "evaluation_status": evaluation_status,
                    "detail_error": detail_error,
                },
            )
            continue

        if candidate.vinted_item_id in terminal_ids:
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="opportunity_skipped_incomplete_detail",
                level="warning",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                message="Opportunity skipped because required item detail was incomplete",
                details={"vinted_item_id": candidate.vinted_item_id},
            )
            continue

        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="filter_passed",
            url=candidate.url,
            proxy_profile_id=proxy_profile_id,
            message="Candidate passed monitor filters",
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "evaluation_status": evaluation_status,
                "filter_count": filter_snapshot_term_count(filters),
            },
        )

        persistence_started_at = time.perf_counter_ns()
        existing_item_id = db.scalar(select(Item.id).where(Item.vinted_item_id == candidate.vinted_item_id))
        item = get_or_persist_catalog_item(db, candidate)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="item_reused" if existing_item_id else "item_persisted",
            url=candidate.url,
            proxy_profile_id=proxy_profile_id,
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "item_id": item.id,
            },
        )
        if detail is not None:
            apply_item_detail(db, item, detail)
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="item_detail_persisted",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                details={
                    "vinted_item_id": candidate.vinted_item_id,
                    "item_id": item.id,
                    "photo_count": len(detail.photos),
                    "has_description": bool(detail.description),
                    "has_total_price": detail.total_price_amount is not None,
                },
            )
        _, created = _get_or_create_monitor_opportunity(db, source, run, item, evaluation_status, filters)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="opportunity_created" if created else "opportunity_skipped",
            level="info" if created else "warning",
            url=candidate.url,
            proxy_profile_id=proxy_profile_id,
            message="Opportunity created" if created else "Opportunity already existed for this monitor item",
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "item_id": item.id,
                "evaluation_status": evaluation_status,
            },
        )
        opportunities_created += 1 if created else 0
        passed += 1
        terminal_ids.append(candidate.vinted_item_id)
        persistence_duration_ms = round((time.perf_counter_ns() - persistence_started_at) / 1_000_000, 3)
        persistence_duration_total_ms += persistence_duration_ms
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="candidate_persistence_finished",
            url=candidate.url,
            proxy_profile_id=proxy_profile_id,
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "duration_ms": persistence_duration_ms,
                "opportunity_created": created,
            },
        )

    _merge_run_metadata(
        run,
        {
            "detail_fetch_mode": detail_fetch_mode,
            "detail_fetch_elapsed_ms": detail_fetch_elapsed_ms,
            "detail_fetch_request_duration_total_ms": detail_fetch_request_duration_total_ms,
            "detail_fetch_attempts": detail_fetch_attempts,
            "filter_duration_total_ms": round(filter_duration_total_ms, 3),
            "persistence_duration_total_ms": round(persistence_duration_total_ms, 3),
        },
    )
    return MonitorEvaluationResult(
        passed=passed,
        discarded=discarded,
        pending=pending,
        opportunities_created=opportunities_created,
        terminal_ids=tuple(dict.fromkeys(terminal_ids)),
    )


def _get_or_create_monitor_opportunity(
    db: Session,
    source: SearchSource,
    run: Run,
    item: Item,
    evaluation_status: str,
    filters: list[dict],
) -> tuple[Opportunity, bool]:
    existing = db.scalar(
        select(Opportunity).where(
            Opportunity.source_id == source.id,
            Opportunity.item_id == item.id,
        )
    )
    if existing is not None:
        existing.last_scraped_at = run.finished_at or datetime.now(UTC)
        existing.last_run_id = run.id
        return existing, False
    opportunity = Opportunity(
        source_id=source.id,
        item_id=item.id,
        status="new",
        evaluation_status=evaluation_status,
        filter_snapshot=filters,
        last_scraped_at=run.finished_at or datetime.now(UTC),
        last_run_id=run.id,
    )
    db.add(opportunity)
    db.flush()
    return opportunity, True


def _existing_opportunity_item_ids(db: Session, source: SearchSource, candidates: list[CatalogItemCandidate]) -> set[str]:
    candidate_ids = [candidate.vinted_item_id for candidate in candidates]
    if not candidate_ids:
        return set()
    return set(
        db.scalars(
            select(Item.vinted_item_id)
            .join(Opportunity, Opportunity.item_id == Item.id)
            .where(
                Opportunity.source_id == source.id,
                Item.vinted_item_id.in_(candidate_ids),
            )
        )
    )


def _deduplicate_candidates(candidates: list[CatalogItemCandidate]) -> list[CatalogItemCandidate]:
    unique_candidates: dict[str, CatalogItemCandidate] = {}
    for candidate in candidates:
        unique_candidates[candidate.vinted_item_id] = candidate
    return list(unique_candidates.values())


def _policy_hash(source: SearchSource, filters: list[dict]) -> str:
    payload = {
        "evaluation_contract": EVALUATION_CONTRACT_VERSION,
        "url": source.url,
        "normalized_query": source.normalized_query or {},
        "filters": filters,
    }
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _elapsed_ms(started_at: float) -> int:
    return max(round((time.perf_counter() - started_at) * 1000), 0)


def raise_(exc: Exception):
    raise exc
