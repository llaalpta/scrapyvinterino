from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.redaction import redact_sensitive_text
from vinted_monitor.db.models import ErrorLog, Item, Opportunity, ProxyProfile, Run, SearchSource
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.providers.vinted_catalog import HttpVintedCatalogProvider
from vinted_monitor.services.filters import evaluate_exclusion_filters, get_filter_snapshot
from vinted_monitor.services.items import (
    apply_item_detail,
    apply_item_detail_data,
    build_transient_catalog_item,
    get_or_persist_catalog_item,
    record_item_detail_error,
)
from vinted_monitor.services.monitor_sessions import get_active_monitor_session, start_monitor_session, stop_active_monitor_session
from vinted_monitor.services.proxies import proxy_url_for_profile
from vinted_monitor.services.run_events import record_run_event
from vinted_monitor.services.seen_cache import SeenCache, SeenCacheUnavailableError, get_seen_cache

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
MANUAL_TRIGGER = "manual"
SCHEDULER_TRIGGER = "scheduler"
SESSION_ITEM_PASSED = "passed"
SESSION_ITEM_DISCARDED = "discarded"
SESSION_ITEM_PASSED_WITHOUT_FILTERS = "passed_without_filters"
SESSION_ITEM_PASSED_WITHOUT_DETAIL = "passed_without_detail"
SESSION_ITEM_DETAIL_ERROR = "detail_error"


class ManualRunProvider(Protocol):
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        """Return public catalog candidates for a monitor run."""

    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        """Return public detail data for a candidate."""


class SearchSourceNotFoundError(ValueError):
    pass


class SearchSourceInactiveError(ValueError):
    pass


class RunAlreadyActiveError(ValueError):
    pass


def execute_manual_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
    seen_cache: SeenCache | None = None,
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
    )


def execute_monitor_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
    trigger: str = MANUAL_TRIGGER,
    seen_cache: SeenCache | None = None,
    require_active: bool = True,
    create_session_for_run: bool = False,
    close_session_on_finish: bool = False,
) -> Run:
    source = db.get(SearchSource, source_id)
    if source is None or source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if require_active and not source.is_active:
        raise SearchSourceInactiveError(f"Search source {source_id} is inactive")
    if _active_source_run_exists(db, source_id=source.id):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")

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
        runtime_metadata=_run_runtime_metadata(db, source),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run") from exc
    db.refresh(run)
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_started",
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        user_agent=get_settings().vinted_user_agent,
        auth_mode="public_anonymous",
        details={
            "trigger": trigger,
            "monitor_mode": source.monitor_mode,
            "filter_count": len(source.filter_rule_ids or []),
        },
    )

    run_provider = provider or _provider_for_source(db, source)
    cache = seen_cache or get_seen_cache()
    policy_hash = _policy_hash(source, get_filter_snapshot(db, source.filter_rule_ids or []))
    run.runtime_metadata = {**(run.runtime_metadata or {}), "policy_hash": policy_hash}
    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
    _attach_provider_event_sink(db, run_provider, run, source, proxy_profile_id)

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
        return _record_failed_run(db, run, source, exc, kind="redis_unavailable")
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="redis_check_success",
        proxy_profile_id=proxy_profile_id,
        message="Redis seen cache is available",
        details={"policy_hash": policy_hash},
    )

    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="catalog_search_start",
        method="GET",
        url=source.url,
        proxy_profile_id=proxy_profile_id,
        user_agent=get_settings().vinted_user_agent,
        auth_mode="public_anonymous",
    )
    try:
        result = run_provider.search(source)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="catalog_search_success",
            method="GET",
            url=source.url,
            proxy_profile_id=proxy_profile_id,
            user_agent=get_settings().vinted_user_agent,
            auth_mode="public_anonymous",
            details={"provider": result.provider_metadata},
        )
    except Exception as exc:
        return _record_failed_run(db, run, source, exc)

    claimed_ids: set[str] = set()
    processed_ids: list[str] = []
    try:
        unique_candidates = _deduplicate_candidates(result.items)
        claimed_ids = cache.claim_unseen(source.id, policy_hash, [candidate.vinted_item_id for candidate in unique_candidates])
        monitor_new_candidates = [candidate for candidate in unique_candidates if candidate.vinted_item_id in claimed_ids]
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
            get_filter_snapshot(db, source.filter_rule_ids or []),
        )
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
        if close_session_on_finish and run.monitor_session_id is not None:
            stop_active_monitor_session(db, source.id, stopped_at=run.finished_at, reason="completed")
        _clear_manual_monitor_runtime(source)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="run_succeeded",
            proxy_profile_id=proxy_profile_id,
            user_agent=get_settings().vinted_user_agent,
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
        db.refresh(run)
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
        return _record_failed_run(db, run, source, exc, kind="redis_unavailable") if run is not None else raise_(exc)
    except Exception as exc:
        db.rollback()
        run = db.get(Run, run.id)
        if claimed_ids:
            cache.release_processing(source.id, policy_hash, list(claimed_ids))
        if run is None:
            raise
        return _record_failed_run(db, run, source, exc)


def list_runs(db: Session, limit: int = 50, source_id: int | None = None) -> list[Run]:
    statement = select(Run)
    if source_id is not None:
        statement = statement.where(Run.source_id == source_id)
    statement = statement.order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
    return list(db.scalars(statement))


def _provider_for_source(db: Session, source: SearchSource) -> HttpVintedCatalogProvider:
    proxy = db.get(ProxyProfile, source.proxy_profile_id) if source.proxy_profile_id else None
    return HttpVintedCatalogProvider(proxy_url=proxy_url_for_profile(proxy, get_settings()))


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


def _run_runtime_metadata(db: Session, source: SearchSource) -> dict:
    proxy = db.get(ProxyProfile, source.proxy_profile_id) if source.proxy_profile_id else None
    return {
        "filter_count": len(source.filter_rule_ids or []),
        "filter_rule_ids": source.filter_rule_ids or [],
        "proxy_profile_id": source.proxy_profile_id,
        "proxy_name": proxy.name if proxy is not None else None,
        "auth_mode": "public_anonymous",
    }


def _attach_provider_event_sink(
    db: Session,
    provider: ManualRunProvider,
    run: Run,
    source: SearchSource,
    proxy_profile_id: int | None,
) -> None:
    if not hasattr(provider, "event_sink"):
        return

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
            user_agent=get_settings().vinted_user_agent,
            auth_mode="public_anonymous",
            message=message,
            details=details,
        )

    provider.event_sink = sink


def _record_failed_run(
    db: Session,
    run: Run,
    source: SearchSource,
    exc: Exception,
    *,
    kind: str | None = None,
) -> Run:
    message = redact_sensitive_text(str(exc))
    record_run_event(
        db,
        run_id=run.id,
        source_id=source.id,
        phase="run_failed",
        level="error",
        message=message,
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        user_agent=get_settings().vinted_user_agent,
        auth_mode="public_anonymous",
        details={"kind": kind or exc.__class__.__name__},
    )
    run.status = FAILED
    run.finished_at = datetime.now(UTC)
    run.error_message = message
    if run.monitor_session_id is not None:
        stop_active_monitor_session(db, source.id, stopped_at=run.finished_at, reason="failed")
        source.is_active = False
        source.monitor_started_at = None
        source.monitor_until = None
        source.next_run_at = None
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
    max_detail_candidates = max(provider_settings.vinted_detail_max_candidates_per_run, 0)
    detail_attempts = 0

    for candidate in candidates:
        transient_item = build_transient_catalog_item(candidate)
        evaluation_status = SESSION_ITEM_PASSED_WITHOUT_FILTERS if not filters else SESSION_ITEM_PASSED
        matched_terms: list[str] = []
        detail: CatalogItemDetail | None = None
        detail_error: str | None = None

        if filters:
            if detail_attempts < max_detail_candidates:
                detail_attempts += 1
                record_run_event(
                    db,
                    run_id=run.id,
                    source_id=source.id,
                    phase="detail_fetch_start",
                    method="GET",
                    url=candidate.url,
                    proxy_profile_id=source.proxy_profile_id,
                    user_agent=get_settings().vinted_user_agent,
                    auth_mode="public_anonymous",
                    details={"vinted_item_id": candidate.vinted_item_id, "attempt": detail_attempts},
                )
                detail_started_at = time.perf_counter()
                try:
                    detail = provider.fetch_detail(candidate)
                    apply_item_detail_data(transient_item, detail)
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_fetch_success",
                        method="GET",
                        url=candidate.url,
                        duration_ms=_elapsed_ms(detail_started_at),
                        proxy_profile_id=source.proxy_profile_id,
                        user_agent=get_settings().vinted_user_agent,
                        auth_mode="public_anonymous",
                        details={"vinted_item_id": candidate.vinted_item_id, "attempt": detail_attempts},
                    )
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
                        proxy_profile_id=source.proxy_profile_id,
                        user_agent=get_settings().vinted_user_agent,
                        auth_mode="public_anonymous",
                        message=detail_error,
                        details={"vinted_item_id": candidate.vinted_item_id, "attempt": detail_attempts},
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
                    proxy_profile_id=source.proxy_profile_id,
                    message="Detail fetch limit reached; opportunity can still be created without detail filters",
                    details={
                        "vinted_item_id": candidate.vinted_item_id,
                        "max_detail_candidates": max_detail_candidates,
                    },
                )

            if evaluation_status == SESSION_ITEM_PASSED:
                decision = evaluate_exclusion_filters(transient_item, filters)
                evaluation_status = decision.status
                matched_terms = decision.matched_terms

        if evaluation_status == SESSION_ITEM_DISCARDED:
            discarded += 1
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="item_discarded",
                level="warning",
                url=candidate.url,
                proxy_profile_id=source.proxy_profile_id,
                message=f"Matched blacklist terms: {', '.join(matched_terms)}",
                details={"vinted_item_id": candidate.vinted_item_id, "matched_terms": matched_terms},
            )
            continue

        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="filter_passed",
            url=candidate.url,
            proxy_profile_id=source.proxy_profile_id,
            message="Candidate passed monitor filters",
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "evaluation_status": evaluation_status,
                "filter_count": len(filters),
            },
        )

        item = get_or_persist_catalog_item(db, candidate)
        if detail is not None:
            apply_item_detail(db, item, detail)
        if detail_error is not None:
            record_item_detail_error(db, item, detail_error)
        _, created = _get_or_create_monitor_opportunity(db, source, run, item, evaluation_status, filters)
        record_run_event(
            db,
            run_id=run.id,
            source_id=source.id,
            phase="opportunity_created" if created else "opportunity_skipped",
            level="info" if created else "warning",
            url=candidate.url,
            proxy_profile_id=source.proxy_profile_id,
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
        rule_id=None,
        status="new",
        evaluation_status=evaluation_status,
        filter_snapshot=filters,
        last_scraped_at=run.finished_at or datetime.now(UTC),
        last_run_id=run.id,
    )
    db.add(opportunity)
    db.flush()
    return opportunity, True


def _deduplicate_candidates(candidates: list[CatalogItemCandidate]) -> list[CatalogItemCandidate]:
    unique_candidates: dict[str, CatalogItemCandidate] = {}
    for candidate in candidates:
        unique_candidates[candidate.vinted_item_id] = candidate
    return list(unique_candidates.values())


def _policy_hash(source: SearchSource, filters: list[dict]) -> str:
    payload = {
        "url": source.url,
        "normalized_query": source.normalized_query or {},
        "filter_rule_ids": source.filter_rule_ids or [],
        "filters": filters,
    }
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _elapsed_ms(started_at: float) -> int:
    return max(round((time.perf_counter() - started_at) * 1000), 0)


def raise_(exc: Exception):
    raise exc
