from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.redaction import redact_sensitive_text, safe_secret_marker
from vinted_monitor.db.models import ErrorLog, Item, Opportunity, ProxyProfile, Run, SearchSource
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.vinted_catalog import (
    CurlCffiVintedCatalogProvider,
    VintedCatalogRateLimitError,
    VintedCatalogSessionContextError,
    VintedCatalogSessionError,
    extract_vinted_item_id,
    parse_catalog_api_payload,
)
from vinted_monitor.services.filters import (
    evaluate_exclusion_filters,
    filter_snapshot_term_count,
    filter_term_count,
    monitor_filter_snapshot,
)
from vinted_monitor.services.items import (
    apply_item_detail,
    apply_item_detail_data,
    build_transient_catalog_item,
    get_or_persist_catalog_item,
)
from vinted_monitor.services.monitor_sessions import get_active_monitor_session, start_monitor_session, stop_active_monitor_session
from vinted_monitor.services.proxies import mark_proxy_run_failure, mark_proxy_run_success, proxy_url_with_sticky_session
from vinted_monitor.services.run_events import record_run_event
from vinted_monitor.services.scheduler import RunEgress, SchedulerCapacityError, choose_run_egress, get_scheduler_runtime_config
from vinted_monitor.services.search_sources import SearchSourceConfigError, catalog_filter_compatibility, validate_vinted_catalog_url
from vinted_monitor.services.seen_cache import SeenCache, SeenCacheUnavailableError, get_seen_cache
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
SUCCESS = "success"
FAILED = "failed"
MANUAL_TRIGGER = "manual"
SCHEDULER_TRIGGER = "scheduler"
BASELINE_TRIGGER = "baseline"
SESSION_PREPARE_TRIGGER = "session_prepare"
DETAIL_PROBE_TRIGGER = "detail_probe"
SESSION_ITEM_PASSED = "passed"
SESSION_ITEM_DISCARDED = "discarded"
SESSION_ITEM_PASSED_WITHOUT_FILTERS = "passed_without_filters"
SESSION_ITEM_PASSED_WITHOUT_DETAIL = "passed_without_detail"
SESSION_ITEM_DETAIL_ERROR = "detail_error"


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


def execute_manual_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
    seen_cache: SeenCache | None = None,
    egress: RunEgress | None = None,
) -> Run:
    source = db.get(SearchSource, source_id)
    if source is not None and source.is_active:
        raise RunAlreadyActiveError(f"Monitor {source.id} already has an active session")
    return execute_monitor_run(
        db,
        source_id,
        provider=provider,
        trigger=MANUAL_TRIGGER,
        seen_cache=seen_cache,
        require_active=False,
        create_session_for_run=True,
        close_session_on_finish=True,
        egress=egress,
    )


def monitor_policy_hash(source: SearchSource) -> str:
    return _policy_hash(source, monitor_filter_snapshot(source.filter_definition))


def monitor_baseline_ready(source: SearchSource, cache: SeenCache | None = None) -> tuple[bool, str]:
    resolved_cache = cache or get_seen_cache()
    policy_hash = monitor_policy_hash(source)
    try:
        return resolved_cache.has_baseline(source.id, policy_hash), policy_hash
    except SeenCacheUnavailableError:
        return False, policy_hash


def ensure_monitor_baseline_ready(db: Session, source_id: int, seen_cache: SeenCache | None = None) -> str:
    source = db.get(SearchSource, source_id)
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    _validated_catalog_filter_compatibility(source)
    cache = seen_cache or get_seen_cache()
    policy_hash = monitor_policy_hash(source)
    cache.require_available()
    if not cache.has_baseline(source.id, policy_hash):
        raise BaselineRequiredError("Recalibra el listado inicial antes de ejecutar este monitor")
    return policy_hash


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
) -> Run:
    source = db.get(SearchSource, source_id)
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.is_active:
        raise RunAlreadyActiveError("Deten la sesion antes de recalibrar el listado inicial")
    if _active_source_run_exists(db, source_id=source.id):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")
    catalog_filters = _validated_catalog_filter_compatibility(source)

    settings = get_settings()
    runtime_config = get_scheduler_runtime_config(db, settings)
    selected_egress = egress or choose_run_egress(db, settings)
    owned_provider = provider is None
    run_provider: ManualRunProvider | None = provider
    prepared_catalog_result: CatalogSearchResult | None = None

    filter_snapshot = monitor_filter_snapshot(source.filter_definition)
    policy_hash = _policy_hash(source, filter_snapshot)
    run = Run(
        source_id=source.id,
        monitor_session_id=None,
        status=RUNNING,
        trigger=BASELINE_TRIGGER,
        items_found=0,
        items_new=0,
        items_filter_passed=0,
        items_discarded_by_filters=0,
        items_filter_pending=0,
        opportunities_created=0,
        runtime_metadata={
            **_run_runtime_metadata(source, selected_egress, runtime_config),
            "policy_hash": policy_hash,
            "baseline_run": True,
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
            "policy_hash": policy_hash,
            "filter_snapshot": filter_snapshot,
            "catalog_filter_compatibility": catalog_filters,
            "runtime_config": {
                "catalog_per_page": runtime_config.catalog_per_page,
                "request_timeout_ms": runtime_config.request_timeout_ms,
            },
            "baseline_run": True,
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
            "direct_allowed": runtime_config.allow_direct_without_proxy,
            "direct_runtime_enabled": runtime_config.direct_runtime_enabled,
        },
    )

    cache = seen_cache or get_seen_cache()
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
                run_provider, provider_runtime_metadata, prepared_catalog_result = _provider_for_egress(
                    db,
                    source,
                    selected_egress,
                    runtime_config,
                    settings,
                    run=run,
                    include_catalog_payload=True,
                )
                _merge_run_metadata(run, provider_runtime_metadata)
                db.flush()
            proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
            _attach_provider_event_sink(db, run_provider, run, source, proxy_profile_id)
        except Exception as exc:
            failed_run = _record_failed_run(db, run, source, exc, penalize_proxy=not isinstance(exc, SeenCacheUnavailableError))
            if run_provider is not None:
                _close_owned_provider(run_provider, owned_provider=owned_provider)
            return failed_run
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="catalog_search_start",
            method="GET",
            url=source.url,
            proxy_profile_id=proxy_profile_id,
            auth_mode="public_anonymous",
        )
        result = prepared_catalog_result or run_provider.search(source)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="catalog_search_success",
            method="GET",
            url=source.url,
            proxy_profile_id=proxy_profile_id,
            auth_mode="public_anonymous",
            details={"provider": result.provider_metadata},
        )
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
        run.items_found = len(result.items)
        run.items_new = 0
        run.items_filter_passed = 0
        run.items_discarded_by_filters = 0
        run.items_filter_pending = 0
        run.opportunities_created = 0
        run.error_message = None
        source.last_run_at = run.finished_at
        mark_proxy_run_success(db, proxy_profile_id)
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
                "reason": "explicit_recalibration",
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
                "items_found": run.items_found,
                "items_new": run.items_new,
                "items_filter_passed": run.items_filter_passed,
                "items_discarded_by_filters": run.items_discarded_by_filters,
                "items_filter_pending": run.items_filter_pending,
                "opportunities_created": run.opportunities_created,
            },
        )
        db.commit()
        db.refresh(run)
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return run
    except Exception as exc:
        failed_run = _record_failed_run(db, run, source, exc, penalize_proxy=not isinstance(exc, SeenCacheUnavailableError))
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
    if _active_source_run_exists(db, source_id=source.id):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")
    catalog_filters = _validated_catalog_filter_compatibility(source)

    settings = get_settings()
    runtime_config = get_scheduler_runtime_config(db, settings)
    selected_egress = egress or choose_run_egress(db, settings)
    if selected_egress.proxy_profile_id is None:
        raise VintedSessionRequiredError("Configura un proxy activo antes de preparar una sesion Vinted")
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
        items_new=0,
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
            "direct_allowed": runtime_config.allow_direct_without_proxy,
            "direct_runtime_enabled": runtime_config.direct_runtime_enabled,
            "session_prepare_run": True,
        },
    )

    try:
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
            penalize_proxy=not isinstance(exc, VintedSessionRequiredError),
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
    if _active_source_run_exists(db, source_id=source.id):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")
    item_id = extract_vinted_item_id(item_ref)
    if item_id is None:
        raise SearchSourceConfigError("Introduce un ID numerico de Vinted o una URL de item valida")
    catalog_filters = _validated_catalog_filter_compatibility(source)

    settings = get_settings()
    runtime_config = get_scheduler_runtime_config(db, settings)
    selected_egress = egress or choose_run_egress(db, settings)
    if selected_egress.proxy_profile_id is None:
        raise VintedSessionRequiredError("Configura un proxy activo antes de probar el detalle de un item")
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
        items_new=0,
        items_filter_passed=0,
        items_discarded_by_filters=0,
        items_filter_pending=0,
        opportunities_created=0,
        runtime_metadata={
            **_run_runtime_metadata(source, selected_egress, runtime_config),
            "detail_probe_run": True,
            "item_ref": item_ref,
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
            "endpoint": f"/api/v2/items/{item_id}/details",
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
    result: dict[str, Any] = {
        "outcome": "failed",
        "item_id": item_id,
        "error": None,
    }
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
        result = provider.probe_item_detail_api(item_ref, referer_url=source.url)
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
            if result.get("outcome") == "datadome_challenge":
                mark_vinted_session_invalid(db, vinted_session_id, reason="DataDome challenge on item detail API probe")
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
            level="info" if result.get("outcome") == "accepted_json" else "warning",
            details={
                "detail_probe_run": True,
                "item_id": item_id,
                "outcome": result.get("outcome"),
                "status_code": result.get("status_code"),
                "duration_ms": result.get("duration_ms"),
                "endpoint": result.get("detail_api_url"),
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
                "endpoint": result.get("detail_api_url"),
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
) -> Run:
    source = db.get(SearchSource, source_id)
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
    owned_provider = provider is None
    run_provider: ManualRunProvider | None = provider
    run_session = start_monitor_session(db, source, allow_manual=True) if create_session_for_run else None
    active_session = run_session
    if active_session is None and require_active and source.monitor_mode != "manual":
        active_session = get_active_monitor_session(db, source.id)
    run = Run(
        source_id=source.id,
        monitor_session_id=active_session.id if active_session is not None else None,
        status=RUNNING,
        trigger=trigger,
        items_found=0,
        items_new=0,
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
            "policy_hash": policy_hash,
            "filter_snapshot": filter_snapshot,
            "catalog_filter_compatibility": catalog_filters,
            "runtime_config": {
                "catalog_per_page": runtime_config.catalog_per_page,
                "detail_max_candidates_per_run": runtime_config.detail_max_candidates_per_run,
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
            "direct_allowed": runtime_config.allow_direct_without_proxy,
            "direct_runtime_enabled": runtime_config.direct_runtime_enabled,
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
        source.monitor_mode = "manual"
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
        stop_active_monitor_session(db, source.id, reason="redis_unavailable")
        failed_run = _record_failed_run(
            db, run, source, exc, kind="redis_unavailable", penalize_proxy=False, force_stop_monitor=True
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
        if not cache.has_baseline(source.id, policy_hash):
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="baseline_required",
                level="warning",
                proxy_profile_id=proxy_profile_id,
                message="Recalibra el listado inicial antes de ejecutar este monitor",
                details={"policy_hash": policy_hash},
            )
            source.is_active = False
            source.monitor_mode = "manual"
            source.monitor_started_at = None
            source.monitor_until = None
            source.next_run_at = None
            stop_active_monitor_session(db, source.id, reason="baseline_required")
            failed_run = _record_failed_run(
                db,
                run,
                source,
                BaselineRequiredError("Recalibra el listado inicial antes de ejecutar este monitor"),
                kind="baseline_required",
                penalize_proxy=False,
                force_stop_monitor=True,
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
        source.monitor_mode = "manual"
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
        stop_active_monitor_session(db, source.id, reason="redis_unavailable")
        failed_run = _record_failed_run(
            db, run, source, exc, kind="redis_unavailable", penalize_proxy=False, force_stop_monitor=True
        )
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run

    try:
        if run_provider is None:
            run_provider, provider_runtime_metadata, _prepared_catalog_result = _provider_for_egress(
                db,
                source,
                selected_egress,
                runtime_config,
                settings,
                run=run,
            )
            _merge_run_metadata(run, provider_runtime_metadata)
            db.flush()
        proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
        _attach_provider_event_sink(db, run_provider, run, source, proxy_profile_id)
    except Exception as exc:
        failed_run = _record_failed_run(db, run, source, exc, penalize_proxy=not isinstance(exc, SeenCacheUnavailableError))
        if run_provider is not None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run

    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="catalog_search_start",
        method="GET",
        url=source.url,
        proxy_profile_id=proxy_profile_id,
        user_agent=None,
        auth_mode="public_anonymous",
    )
    try:
        result = run_provider.search(source)
        _persist_provider_session_refresh(db, run_provider, run, source, proxy_profile_id, settings)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="catalog_search_success",
            method="GET",
            url=source.url,
            proxy_profile_id=proxy_profile_id,
            user_agent=None,
            auth_mode="public_anonymous",
            details={"provider": result.provider_metadata},
        )
    except DataDomeChallengeError as exc:
        try:
            _record_failed_run(db, run, source, exc, kind="datadome_challenge", penalize_proxy=False)
        finally:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
        raise
    except VintedCatalogRateLimitError as exc:
        failed_run = _record_failed_run(db, run, source, exc, kind="catalog_rate_limited", penalize_proxy=False)
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run
    except Exception as exc:
        failed_run = _record_failed_run(db, run, source, exc, penalize_proxy=True)
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run

    claimed_ids: set[str] = set()
    processed_ids: list[str] = []
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
        claimed_ids = cache.claim_unseen(source.id, policy_hash, [candidate.vinted_item_id for candidate in unique_candidates])
        monitor_new_candidates = [candidate for candidate in unique_candidates if candidate.vinted_item_id in claimed_ids]
        existing_opportunity_ids = _existing_opportunity_item_ids(db, source, monitor_new_candidates)
        if existing_opportunity_ids:
            already_claimed_existing_ids = [
                candidate.vinted_item_id for candidate in monitor_new_candidates if candidate.vinted_item_id in existing_opportunity_ids
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
            cache.mark_seen(source.id, policy_hash, already_claimed_existing_ids)
            claimed_ids.difference_update(already_claimed_existing_ids)
            monitor_new_candidates = [
                candidate for candidate in monitor_new_candidates if candidate.vinted_item_id not in existing_opportunity_ids
            ]
        seen_candidates = [candidate for candidate in unique_candidates if candidate.vinted_item_id not in claimed_ids]
        if seen_candidates:
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="candidate_seen_skipped",
                level="debug",
                proxy_profile_id=proxy_profile_id,
                message="Candidates already seen by this monitor policy were skipped",
                details={
                    "seen_hit_count": len(seen_candidates),
                    "sample_vinted_item_ids": [candidate.vinted_item_id for candidate in seen_candidates[:10]],
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
            monitor_new_candidates,
            filter_snapshot,
        )
        _persist_provider_session_refresh(db, run_provider, run, source, proxy_profile_id, settings)
        processed_ids = [candidate.vinted_item_id for candidate in monitor_new_candidates]
        run.status = SUCCESS
        run.finished_at = datetime.now(UTC)
        run.items_found = len(result.items)
        run.items_new = len(monitor_new_candidates)
        run.items_filter_passed = monitor_result["passed"]
        run.items_discarded_by_filters = monitor_result["discarded"]
        run.items_filter_pending = monitor_result["pending"]
        run.opportunities_created = monitor_result["opportunities_created"]
        run.error_message = None
        source.last_run_at = run.finished_at
        mark_proxy_run_success(db, proxy_profile_id)
        if close_session_on_finish and run.monitor_session_id is not None:
            stop_active_monitor_session(db, source.id, stopped_at=run.finished_at, reason="completed")
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="monitor_session_closed",
                proxy_profile_id=proxy_profile_id,
                message="Monitor session closed after run completion",
                details={"monitor_session_id": run.monitor_session_id, "reason": "completed"},
            )
        elif not close_session_on_finish:
            _stop_monitor_if_vinted_session_use_limit_reached(db, run, source)
        _clear_manual_monitor_runtime(source)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="run_succeeded",
            proxy_profile_id=proxy_profile_id,
            user_agent=None,
            auth_mode="public_anonymous",
            details={
                "items_found": run.items_found,
                "items_new": run.items_new,
                "items_filter_passed": run.items_filter_passed,
                "items_discarded_by_filters": run.items_discarded_by_filters,
                "items_filter_pending": run.items_filter_pending,
                "opportunities_created": run.opportunities_created,
            },
        )
        db.commit()
        cache.mark_seen(source.id, policy_hash, processed_ids)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="redis_seen_marked",
            proxy_profile_id=proxy_profile_id,
            details={
                "marked_seen_count": len(processed_ids),
                "sample_vinted_item_ids": processed_ids[:10],
                "policy_hash": policy_hash,
            },
        )
        db.commit()
        db.refresh(run)
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return run
    except SeenCacheUnavailableError as exc:
        db.rollback()
        run = db.get(Run, run.id)
        source = db.get(SearchSource, source.id) or source
        source.is_active = False
        source.monitor_mode = "manual"
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
        stop_active_monitor_session(db, source.id, reason="redis_unavailable")
        if claimed_ids:
            try:
                cache.release_processing(source.id, policy_hash, list(claimed_ids))
            except SeenCacheUnavailableError:
                pass
        if run is None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
            raise_(exc)
        failed_run = _record_failed_run(
            db, run, source, exc, kind="redis_unavailable", penalize_proxy=False, force_stop_monitor=True
        )
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run
    except DataDomeChallengeError as exc:
        db.rollback()
        run = db.get(Run, run.id)
        if claimed_ids:
            cache.release_processing(source.id, policy_hash, list(claimed_ids))
        if run is None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
            raise
        try:
            _record_failed_run(db, run, source, exc, kind="datadome_challenge", penalize_proxy=False)
        finally:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
        raise
    except Exception as exc:
        db.rollback()
        run = db.get(Run, run.id)
        if claimed_ids:
            cache.release_processing(source.id, policy_hash, list(claimed_ids))
        if run is None:
            _close_owned_provider(run_provider, owned_provider=owned_provider)
            raise
        failed_run = _record_failed_run(db, run, source, exc, penalize_proxy=False)
        _close_owned_provider(run_provider, owned_provider=owned_provider)
        return failed_run


def list_runs(db: Session, limit: int = 50, source_id: int | None = None) -> list[Run]:
    statement = select(Run)
    if source_id is not None:
        statement = statement.where(Run.source_id == source_id)
    statement = statement.order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
    return list(db.scalars(statement))


def _provider_for_egress(
    db: Session,
    source: SearchSource,
    egress: RunEgress,
    runtime_config,
    settings,
    *,
    run: Run | None = None,
    include_catalog_payload: bool = False,
) -> tuple[CurlCffiVintedCatalogProvider, dict[str, Any], CatalogSearchResult | None]:
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
    if egress.proxy_profile_id is not None:
        profile = db.get(ProxyProfile, egress.proxy_profile_id)
        if profile is None:
            raise RuntimeError(f"Proxy profile {egress.proxy_profile_id} no longer exists")
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
    elif settings.vinted_prepared_session_required:
        raise VintedSessionRequiredError("Prepara una sesion Vinted con proxy antes de lanzar trafico de catalogo")

    return CurlCffiVintedCatalogProvider(
        settings=settings,
        proxy_url=proxy_url,
        timeout_ms=runtime_config.request_timeout_ms,
        catalog_per_page=runtime_config.catalog_per_page,
        request_retries=settings.vinted_request_retries,
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


def _prepare_vinted_session_for_run(
    db: Session,
    source: SearchSource,
    proxy_profile: ProxyProfile,
    runtime_config,
    settings,
    *,
    event_sink,
    include_catalog_payload: bool = False,
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
    provider = CurlCffiVintedCatalogProvider(
        settings=settings,
        profile=browser_profile,
        proxy_url=proxy_url,
        timeout_ms=runtime_config.request_timeout_ms,
        catalog_per_page=runtime_config.catalog_per_page,
        request_retries=0,
        human_delay_min=settings.human_delay_min_seconds,
        human_delay_max=settings.human_delay_max_seconds,
        event_sink=event_sink,
        proxy_session_marker=proxy_marker,
        expected_country_code=proxy_profile.country_code,
        locale=proxy_profile.locale,
        accept_language=proxy_profile.accept_language,
        screen=proxy_profile.vinted_screen,
        viewport_size=proxy_profile.screen,
        require_datadome_cookie=True,
    )
    try:
        context_report = provider.bootstrap_for_session(source.url, collect_datadome=True)
        probe = provider.probe_catalog_api(source.url, include_payload=include_catalog_payload)
        prepared = provider.export_prepared_session(proxy_session_id=proxy_session_id)
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
            last_error = "Prepared Vinted session rejected: probe payload unavailable for recalibration"

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
        raise VintedSessionRequiredError(saved.last_error or "Prepared Vinted session is not usable")
    return saved, prepared, {
        "vinted_session_prepare_probe_outcome": probe_outcome,
        "vinted_session_prepare_probe_status_code": probe.get("status_code"),
        "vinted_session_prepare_probe_duration_ms": probe.get("duration_ms"),
    }, catalog_result


def _close_owned_provider(provider: ManualRunProvider, *, owned_provider: bool) -> None:
    if not owned_provider:
        return
    close = getattr(provider, "close", None)
    if callable(close):
        close()


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


def _active_source_run_exists(db: Session, *, source_id: int) -> bool:
    return (
        db.scalar(
            select(Run.id)
            .where(
                Run.source_id == source_id,
                Run.status == RUNNING,
                Run.finished_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def _run_runtime_metadata(source: SearchSource, egress: RunEgress, runtime_config) -> dict:
    return {
        "filter_count": filter_term_count(source.filter_definition),
        "egress_mode": egress.mode,
        "proxy_profile_id": egress.proxy_profile_id,
        "proxy_name": egress.proxy_name,
        "proxy_kind": egress.proxy_kind,
        "auth_mode": "public_anonymous",
        "catalog_per_page": runtime_config.catalog_per_page,
        "detail_max_candidates_per_run": runtime_config.detail_max_candidates_per_run,
        "request_timeout_ms": runtime_config.request_timeout_ms,
        "proxy_cooldown_minutes": runtime_config.proxy_cooldown_minutes,
        "stop_monitor_after_consecutive_failures": runtime_config.stop_monitor_after_consecutive_failures,
    }


def _merge_run_metadata(run: Run, metadata: dict[str, Any]) -> None:
    run.runtime_metadata = {**(run.runtime_metadata or {}), **metadata}


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
            details=details,
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


def _record_failed_run(
    db: Session,
    run: Run,
    source: SearchSource,
    exc: Exception,
    *,
    kind: str | None = None,
    penalize_proxy: bool = False,
    force_stop_monitor: bool = False,
) -> Run:
    message = redact_sensitive_text(str(exc))
    session_failure = _classify_session_failure(exc, kind=kind)
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_failed",
        level="error",
        message=message,
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        user_agent=None,
        auth_mode="public_anonymous",
        details={
            "kind": kind or exc.__class__.__name__,
            "session_end_reason": session_failure["session_end_reason"],
            "recovery_action": session_failure["recovery_action"],
            "vinted_session_id": (run.runtime_metadata or {}).get("vinted_session_id"),
            "vinted_session_use_count": (run.runtime_metadata or {}).get("vinted_session_request_count"),
        },
    )
    run.status = FAILED
    run.finished_at = datetime.now(UTC)
    run.error_message = message
    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
    vinted_session_id = (run.runtime_metadata or {}).get("vinted_session_id")
    if isinstance(exc, DataDomeChallengeError | VintedCatalogSessionError | VintedCatalogSessionContextError):
        mark_vinted_session_invalid(db, vinted_session_id, reason=message)
    cooldown_minutes = int((run.runtime_metadata or {}).get("proxy_cooldown_minutes", 10))
    if penalize_proxy:
        mark_proxy_run_failure(db, proxy_profile_id, cooldown_minutes=cooldown_minutes)
    should_stop_monitor = _should_stop_monitor_after_failure(db, run, source, force_stop_monitor=force_stop_monitor)
    if run.monitor_session_id is not None and should_stop_monitor:
        stop_active_monitor_session(db, source.id, stopped_at=run.finished_at, reason="failed")
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="monitor_session_closed",
            level="warning",
            proxy_profile_id=proxy_profile_id,
            message="Monitor session closed after run failure",
            details={"monitor_session_id": run.monitor_session_id, "reason": "failed"},
        )
        source.is_active = False
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
    elif run.monitor_session_id is not None:
        _stop_monitor_if_vinted_session_use_limit_reached(db, run, source)
    _clear_manual_monitor_runtime(source)
    db.add(
        ErrorLog(
            run_id=run.id,
            source_id=source.id,
            kind=kind or exc.__class__.__name__,
            message=message,
            details={},
        )
    )
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
    if isinstance(exc, DataDomeChallengeError) or kind == "datadome_challenge":
        return {
            "session_end_reason": "datadome_challenge",
            "recovery_action": "invalidate_session_and_rotate_sticky",
        }
    if isinstance(exc, VintedCatalogSessionContextError):
        return {
            "session_end_reason": "catalog_context_incomplete",
            "recovery_action": "invalidate_session_and_prepare_new",
        }
    if isinstance(exc, VintedCatalogSessionError):
        return {
            "session_end_reason": "catalog_session_rejected",
            "recovery_action": "invalidate_session_and_prepare_new",
        }
    if isinstance(exc, VintedCatalogRateLimitError) or kind == "catalog_rate_limited":
        return {
            "session_end_reason": "catalog_rate_limited",
            "recovery_action": "respect_retry_after_and_reduce_rate",
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


def _evaluate_monitor_candidates(
    db: Session,
    provider: ManualRunProvider,
    source: SearchSource,
    run: Run,
    candidates: list[CatalogItemCandidate],
    filters: list[dict],
) -> dict[str, int]:
    if not candidates:
        return {"passed": 0, "discarded": 0, "pending": 0, "opportunities_created": 0}

    passed = 0
    discarded = 0
    pending = 0
    opportunities_created = 0
    provider_settings = getattr(provider, "settings", get_settings())
    runtime_detail_limit = (run.runtime_metadata or {}).get("detail_max_candidates_per_run")
    configured_detail_limit = (
        runtime_detail_limit if runtime_detail_limit is not None else provider_settings.vinted_detail_max_candidates_per_run
    )
    max_detail_candidates = max(int(configured_detail_limit), 0)
    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
    detail_attempts = 0

    for candidate in candidates:
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

        if detail_attempts < max_detail_candidates:
            detail_attempts += 1
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="candidate_detail_required",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                details={
                    "vinted_item_id": candidate.vinted_item_id,
                    "attempt": detail_attempts,
                    "max_detail_candidates": max_detail_candidates,
                    "reason": "filters_configured" if filters else "opportunity_enrichment",
                },
            )
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="detail_fetch_start",
                method="GET",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                user_agent=None,
                auth_mode="public_anonymous",
                details={"vinted_item_id": candidate.vinted_item_id, "attempt": detail_attempts, "referer_url": source.url},
            )
            detail_started_at = time.perf_counter()
            try:
                detail = provider.fetch_detail(candidate, referer_url=source.url)
                apply_item_detail_data(transient_item, detail)
                record_run_event(
                    db,
                    run_id=run.id,
                    source_id=source.id,
                    phase="detail_fetch_success",
                    method="GET",
                    url=candidate.url,
                    duration_ms=_elapsed_ms(detail_started_at),
                    proxy_profile_id=proxy_profile_id,
                    user_agent=None,
                    auth_mode="public_anonymous",
                    details={
                        "vinted_item_id": candidate.vinted_item_id,
                        "attempt": detail_attempts,
                        "has_description": bool(detail.description),
                        "photo_count": len(detail.photos),
                        "has_total_price": detail.total_price_amount is not None,
                    },
                )
            except DataDomeChallengeError as exc:
                record_run_event(
                    db,
                    run_id=run.id,
                    source_id=source.id,
                    phase="detail_fetch_error",
                    method="GET",
                    url=candidate.url,
                    duration_ms=_elapsed_ms(detail_started_at),
                    level="error",
                    proxy_profile_id=proxy_profile_id,
                    user_agent=None,
                    auth_mode="public_anonymous",
                    message=redact_sensitive_text(str(exc)),
                    details={"vinted_item_id": candidate.vinted_item_id, "attempt": detail_attempts, "kind": "datadome_challenge"},
                )
                raise
            except Exception as exc:
                pending += 1
                evaluation_status = SESSION_ITEM_DETAIL_ERROR
                detail_error = redact_sensitive_text(str(exc))
                record_run_event(
                    db,
                    run_id=run.id,
                    source_id=source.id,
                    phase="detail_fetch_error",
                    method="GET",
                    url=candidate.url,
                    duration_ms=_elapsed_ms(detail_started_at),
                    level="error",
                    proxy_profile_id=proxy_profile_id,
                    user_agent=None,
                    auth_mode="public_anonymous",
                    message=detail_error,
                    details={"vinted_item_id": candidate.vinted_item_id, "attempt": detail_attempts, "terminal": "no_opportunity"},
                )
        else:
            pending += 1
            evaluation_status = SESSION_ITEM_PASSED_WITHOUT_DETAIL
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="detail_fetch_skipped",
                level="warning",
                url=candidate.url,
                proxy_profile_id=proxy_profile_id,
                message="Detail fetch limit reached; opportunity will not be created without detail",
                details={
                    "vinted_item_id": candidate.vinted_item_id,
                    "max_detail_candidates": max_detail_candidates,
                    "terminal": "no_opportunity",
                },
            )

        if detail is not None and evaluation_status == SESSION_ITEM_PASSED:
            decision = evaluate_exclusion_filters(transient_item, filters)
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
                "matched_terms": matched_terms,
                "detail_error": detail_error,
            },
        )

        if evaluation_status == SESSION_ITEM_DISCARDED:
            discarded += 1
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

    return {
        "passed": passed,
        "discarded": discarded,
        "pending": pending,
        "opportunities_created": opportunities_created,
    }


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
