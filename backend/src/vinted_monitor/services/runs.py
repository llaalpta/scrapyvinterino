from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.redaction import redact_sensitive_text
from vinted_monitor.db.models import (
    ErrorLog,
    Item,
    MonitorSession,
    Opportunity,
    ProxyProfile,
    Run,
    SearchSource,
    SessionItemState,
    SourceSeenItem,
)
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.providers.vinted_catalog import HttpVintedCatalogProvider
from vinted_monitor.services.filters import evaluate_exclusion_filters, get_filter_snapshot
from vinted_monitor.services.items import apply_item_detail, get_items_by_vinted_ids, persist_catalog_items, record_item_detail_error
from vinted_monitor.services.proxies import proxy_url_for_profile
from vinted_monitor.services.run_events import record_run_event

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
MANUAL_TRIGGER = "manual"
SCHEDULER_TRIGGER = "scheduler"
SESSION_STATUS_ACTIVE = "active"
SESSION_ITEM_PASSED = "passed"
SESSION_ITEM_DISCARDED = "discarded"
SESSION_ITEM_PASSED_WITHOUT_FILTERS = "passed_without_filters"
SESSION_ITEM_PASSED_WITHOUT_DETAIL = "passed_without_detail"
SESSION_ITEM_DETAIL_ERROR = "detail_error"
SOURCE_SEEN_ID_CACHE_LIMIT = 10_000
GLOBAL_KNOWN_ID_CACHE_LIMIT = 50_000
# These caches are hints only. Database writes remain the source of truth for
# newness, item updates, and source traceability.
SOURCE_SEEN_ID_CACHE: dict[int, OrderedDict[str, int]] = {}
GLOBAL_KNOWN_ID_CACHE: OrderedDict[str, int] = OrderedDict()
_CACHE_LOCK = RLock()


class ManualRunProvider(Protocol):
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        """Return public catalog candidates for a manual run."""

    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        """Return public detail data for a candidate."""


class SearchSourceNotFoundError(ValueError):
    pass


class SearchSourceInactiveError(ValueError):
    pass


class RunAlreadyActiveError(ValueError):
    pass


class MonitorSessionRunError(ValueError):
    pass


def execute_manual_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
) -> Run:
    return execute_source_run(db, source_id, provider=provider, trigger=MANUAL_TRIGGER)


def execute_monitor_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
    trigger: str = MANUAL_TRIGGER,
) -> Run:
    source = db.get(SearchSource, source_id)
    if source is None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if source.archived_at is not None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    run_provider = provider
    if run_provider is None:
        proxy = db.get(ProxyProfile, source.proxy_profile_id) if source.proxy_profile_id else None
        run_provider = HttpVintedCatalogProvider(proxy_url=proxy_url_for_profile(proxy, get_settings()))
    return execute_source_run(db, source_id, provider=run_provider, trigger=trigger, monitor_flow=True)


def execute_session_run(
    db: Session,
    session_id: int,
    provider: ManualRunProvider | None = None,
    trigger: str = MANUAL_TRIGGER,
) -> Run:
    session = db.get(MonitorSession, session_id)
    if session is None:
        raise MonitorSessionRunError(f"Monitor session {session_id} does not exist")
    if session.status != SESSION_STATUS_ACTIVE:
        raise MonitorSessionRunError(f"Monitor session {session_id} is not active")
    return execute_source_run(db, session.source_id, provider=provider, trigger=trigger, session=session)


def execute_source_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
    trigger: str = MANUAL_TRIGGER,
    session: MonitorSession | None = None,
    monitor_flow: bool = False,
) -> Run:
    source = db.get(SearchSource, source_id)
    if source is None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if not source.is_active and not monitor_flow:
        raise SearchSourceInactiveError(f"Search source {source_id} is inactive")
    if session is not None and _active_run_exists(db, session_id=session.id):
        raise RunAlreadyActiveError(f"Monitor session {session.id} already has a running run")
    if monitor_flow and session is None and _active_source_run_exists(db, source_id=source.id):
        raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run")

    run = Run(
        source_id=source.id,
        session_id=session.id if session else None,
        status=RUNNING,
        trigger=trigger,
        items_found=0,
        items_new=0,
        items_filter_passed=0,
        items_discarded_by_filters=0,
        items_filter_pending=0,
        opportunities_created=0,
        runtime_metadata=_run_runtime_metadata(db, source, session),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if session is not None:
            raise RunAlreadyActiveError(f"Monitor session {session.id} already has a running run") from exc
        if monitor_flow:
            raise RunAlreadyActiveError(f"Monitor {source.id} already has a running run") from exc
        raise
    db.refresh(run)

    run_provider = provider or HttpVintedCatalogProvider()
    proxy_profile_id = (run.runtime_metadata or {}).get("proxy_profile_id")
    record_run_event(
        db,
        run_id=run.id,
        session_id=session.id if session else None,
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
            session_id=session.id if session else None,
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

    try:
        persistence_result = persist_catalog_items(db, result.items)
        unique_candidates = _deduplicate_candidates(result.items)
        cached_item_ids_by_vinted_id = _get_cached_known_item_ids(
            [candidate.vinted_item_id for candidate in unique_candidates]
        )
        inserted_vinted_item_ids = set(persistence_result.inserted_vinted_item_ids)
        lookup_vinted_item_ids = sorted({candidate.vinted_item_id for candidate in unique_candidates})
        items_by_vinted_id = get_items_by_vinted_ids(db, lookup_vinted_item_ids)
        item_ids_by_vinted_id = {
            **cached_item_ids_by_vinted_id,
            **{vinted_item_id: item.id for vinted_item_id, item in items_by_vinted_id.items()},
        }
        global_new_candidates = [
            candidate for candidate in unique_candidates if candidate.vinted_item_id in inserted_vinted_item_ids
        ]
        monitor_new_item_ids = _track_source_seen_items(db, source.id, run.id, unique_candidates, item_ids_by_vinted_id)
        monitor_new_candidates = [
            candidate
            for candidate in unique_candidates
            if item_ids_by_vinted_id.get(candidate.vinted_item_id) in monitor_new_item_ids
        ]
        session_result = _evaluate_session_candidates(
            db,
            run_provider,
            source,
            run,
            session,
            unique_candidates,
        ) if session is not None else None
        monitor_result = _evaluate_monitor_candidates(
            db,
            run_provider,
            source,
            run,
            monitor_new_candidates,
        ) if monitor_flow and session is None else None
        if session is None and monitor_result is None:
            _fetch_and_persist_details(db, run_provider, source, run, monitor_new_candidates or global_new_candidates, items_by_vinted_id)

        run.status = SUCCESS
        run.finished_at = datetime.now(UTC)
        run.items_found = persistence_result.found_count
        run.items_new = len(monitor_new_item_ids)
        if session_result is not None:
            run.items_filter_passed = session_result["passed"]
            run.items_discarded_by_filters = session_result["discarded"]
            run.items_filter_pending = session_result["pending"]
            run.opportunities_created = session_result["opportunities_created"]
        elif monitor_result is not None:
            run.items_filter_passed = monitor_result["passed"]
            run.items_discarded_by_filters = monitor_result["discarded"]
            run.items_filter_pending = monitor_result["pending"]
            run.opportunities_created = monitor_result["opportunities_created"]
        else:
            run.opportunities_created = 0
        source.last_run_at = run.finished_at
        run.error_message = None
        db.commit()
        committed_item_ids_by_vinted_id = {
            candidate.vinted_item_id: item_ids_by_vinted_id[candidate.vinted_item_id]
            for candidate in unique_candidates
            if candidate.vinted_item_id in item_ids_by_vinted_id
        }
        _remember_known_items(committed_item_ids_by_vinted_id)
        _remember_source_seen_items(source.id, committed_item_ids_by_vinted_id)
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        run = db.get(Run, run.id)
        if run is None:
            raise
        return _record_failed_run(db, run, source, exc)


def list_runs(db: Session, limit: int = 50) -> list[Run]:
    statement = select(Run).order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
    return list(db.scalars(statement))


def _active_run_exists(db: Session, *, session_id: int) -> bool:
    return (
        db.scalar(
            select(Run.id)
            .where(Run.session_id == session_id, Run.status == RUNNING, Run.finished_at.is_(None))
            .limit(1)
        )
        is not None
    )


def _active_source_run_exists(db: Session, *, source_id: int) -> bool:
    return (
        db.scalar(
            select(Run.id)
            .where(
                Run.source_id == source_id,
                Run.session_id.is_(None),
                Run.status == RUNNING,
                Run.finished_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def _run_runtime_metadata(db: Session, source: SearchSource, session: MonitorSession | None) -> dict:
    if session is None:
        proxy = db.get(ProxyProfile, source.proxy_profile_id) if source.proxy_profile_id else None
        return {
            "filter_count": len(source.filter_rule_ids or []),
            "filter_rule_ids": source.filter_rule_ids or [],
            "proxy_profile_id": source.proxy_profile_id,
            "proxy_name": proxy.name if proxy is not None else None,
            "auth_mode": "public_anonymous",
        }
    return {
        "session_id": session.id,
        "filter_hash": session.filter_hash,
        "filter_count": len(session.filter_snapshot or []),
        "proxy_profile_id": session.proxy_profile_id,
        "auth_mode": "public_anonymous",
    }


def _record_failed_run(db: Session, run: Run, source: SearchSource, exc: Exception) -> Run:
    message = redact_sensitive_text(str(exc))
    record_run_event(
        db,
        run_id=run.id,
        session_id=run.session_id,
        source_id=source.id,
        phase="run_failed",
        message=message,
        proxy_profile_id=(run.runtime_metadata or {}).get("proxy_profile_id"),
        user_agent=get_settings().vinted_user_agent,
        auth_mode="public_anonymous",
    )
    run.status = FAILED
    run.finished_at = datetime.now(UTC)
    run.error_message = message
    db.add(
        ErrorLog(
            run_id=run.id,
            source_id=source.id,
            kind=exc.__class__.__name__,
            message=message,
            details={},
        )
    )
    db.commit()
    db.refresh(run)
    return run


def _evaluate_session_candidates(
    db: Session,
    provider: ManualRunProvider,
    source: SearchSource,
    run: Run,
    session: MonitorSession,
    candidates: list[CatalogItemCandidate],
) -> dict[str, int]:
    if not candidates:
        return {"passed": 0, "discarded": 0, "pending": 0, "opportunities_created": 0}

    items_by_vinted_id = get_items_by_vinted_ids(db, [candidate.vinted_item_id for candidate in candidates])
    item_ids = [item.id for item in items_by_vinted_id.values()]
    existing_states = {
        state.item_id: state
        for state in db.scalars(
            select(SessionItemState).where(
                SessionItemState.session_id == session.id,
                SessionItemState.item_id.in_(item_ids),
                SessionItemState.filter_hash == session.filter_hash,
            )
        )
    } if item_ids else {}

    passed = 0
    discarded = 0
    pending = 0
    opportunities_created = 0
    provider_settings = getattr(provider, "settings", get_settings())
    max_detail_candidates = max(provider_settings.vinted_detail_max_candidates_per_run, 0)
    detail_attempts = 0

    for candidate in candidates:
        item = items_by_vinted_id.get(candidate.vinted_item_id)
        if item is None or item.id in existing_states:
            continue

        evaluation_status = SESSION_ITEM_PASSED
        matched_terms: list[str] = []
        filters = session.filter_snapshot or []
        if not filters:
            evaluation_status = SESSION_ITEM_PASSED_WITHOUT_FILTERS
        else:
            if item.detail_last_fetched_at is None and detail_attempts < max_detail_candidates:
                detail_attempts += 1
                try:
                    detail = provider.fetch_detail(candidate)
                    apply_item_detail(db, item, detail)
                    record_run_event(
                        db,
                        run_id=run.id,
                        session_id=session.id,
                        source_id=source.id,
                        phase="detail_fetch_success",
                        method="GET",
                        url=candidate.url,
                        proxy_profile_id=session.proxy_profile_id,
                        user_agent=get_settings().vinted_user_agent,
                        auth_mode="public_anonymous",
                    )
                except Exception as exc:
                    pending += 1
                    evaluation_status = SESSION_ITEM_DETAIL_ERROR
                    record_item_detail_error(db, item, str(exc))
                    record_run_event(
                        db,
                        run_id=run.id,
                        session_id=session.id,
                        source_id=source.id,
                        phase="detail_fetch_error",
                        method="GET",
                        url=candidate.url,
                        proxy_profile_id=session.proxy_profile_id,
                        user_agent=get_settings().vinted_user_agent,
                        auth_mode="public_anonymous",
                        message=str(exc),
                    )
            elif item.detail_last_fetched_at is None:
                pending += 1
                evaluation_status = SESSION_ITEM_PASSED_WITHOUT_DETAIL

            if evaluation_status == SESSION_ITEM_PASSED:
                decision = evaluate_exclusion_filters(item, filters)
                evaluation_status = decision.status
                matched_terms = decision.matched_terms

        if evaluation_status == SESSION_ITEM_DISCARDED:
            discarded += 1
            _record_session_item_state(db, session, item, evaluation_status, None)
            record_run_event(
                db,
                run_id=run.id,
                session_id=session.id,
                source_id=source.id,
                phase="item_discarded",
                url=item.url,
                proxy_profile_id=session.proxy_profile_id,
                message=f"Matched blacklist terms: {', '.join(matched_terms)}",
            )
            continue

        opportunity, created = _get_or_create_session_opportunity(db, session, source, item, evaluation_status)
        opportunities_created += 1 if created else 0
        passed += 1
        _record_session_item_state(db, session, item, evaluation_status, opportunity.id)

    return {
        "passed": passed,
        "discarded": discarded,
        "pending": pending,
        "opportunities_created": opportunities_created,
    }


def _evaluate_monitor_candidates(
    db: Session,
    provider: ManualRunProvider,
    source: SearchSource,
    run: Run,
    candidates: list[CatalogItemCandidate],
) -> dict[str, int]:
    if not candidates:
        return {"passed": 0, "discarded": 0, "pending": 0, "opportunities_created": 0}

    items_by_vinted_id = get_items_by_vinted_ids(db, [candidate.vinted_item_id for candidate in candidates])
    passed = 0
    discarded = 0
    pending = 0
    opportunities_created = 0
    filters = get_filter_snapshot(db, source.filter_rule_ids or [])
    provider_settings = getattr(provider, "settings", get_settings())
    max_detail_candidates = max(provider_settings.vinted_detail_max_candidates_per_run, 0)
    detail_attempts = 0

    for candidate in candidates:
        item = items_by_vinted_id.get(candidate.vinted_item_id)
        if item is None:
            continue

        evaluation_status = SESSION_ITEM_PASSED_WITHOUT_FILTERS if not filters else SESSION_ITEM_PASSED
        matched_terms: list[str] = []
        if filters:
            if item.detail_last_fetched_at is None and detail_attempts < max_detail_candidates:
                detail_attempts += 1
                try:
                    detail = provider.fetch_detail(candidate)
                    apply_item_detail(db, item, detail)
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_fetch_success",
                        method="GET",
                        url=candidate.url,
                        proxy_profile_id=source.proxy_profile_id,
                        user_agent=get_settings().vinted_user_agent,
                        auth_mode="public_anonymous",
                    )
                except Exception as exc:
                    pending += 1
                    evaluation_status = SESSION_ITEM_DETAIL_ERROR
                    record_item_detail_error(db, item, str(exc))
                    record_run_event(
                        db,
                        run_id=run.id,
                        source_id=source.id,
                        phase="detail_fetch_error",
                        method="GET",
                        url=candidate.url,
                        proxy_profile_id=source.proxy_profile_id,
                        user_agent=get_settings().vinted_user_agent,
                        auth_mode="public_anonymous",
                        message=str(exc),
                    )
            elif item.detail_last_fetched_at is None:
                pending += 1
                evaluation_status = SESSION_ITEM_PASSED_WITHOUT_DETAIL

            if evaluation_status == SESSION_ITEM_PASSED:
                decision = evaluate_exclusion_filters(item, filters)
                evaluation_status = decision.status
                matched_terms = decision.matched_terms

        if evaluation_status == SESSION_ITEM_DISCARDED:
            discarded += 1
            record_run_event(
                db,
                run_id=run.id,
                source_id=source.id,
                phase="item_discarded",
                url=item.url,
                proxy_profile_id=source.proxy_profile_id,
                message=f"Matched blacklist terms: {', '.join(matched_terms)}",
            )
            continue

        _, created = _get_or_create_monitor_opportunity(db, source, item, evaluation_status, filters)
        opportunities_created += 1 if created else 0
        passed += 1

    return {
        "passed": passed,
        "discarded": discarded,
        "pending": pending,
        "opportunities_created": opportunities_created,
    }


def _get_or_create_session_opportunity(
    db: Session,
    session: MonitorSession,
    source: SearchSource,
    item: Item,
    evaluation_status: str,
) -> tuple[Opportunity, bool]:
    existing = db.scalar(select(Opportunity).where(Opportunity.session_id == session.id, Opportunity.item_id == item.id))
    if existing is not None:
        return existing, False
    opportunity = Opportunity(
        source_id=source.id,
        session_id=session.id,
        item_id=item.id,
        rule_id=None,
        status="new",
        evaluation_status=evaluation_status,
        filter_snapshot=session.filter_snapshot or [],
    )
    db.add(opportunity)
    db.flush()
    return opportunity, True


def _get_or_create_monitor_opportunity(
    db: Session,
    source: SearchSource,
    item: Item,
    evaluation_status: str,
    filters: list[dict],
) -> tuple[Opportunity, bool]:
    existing = db.scalar(
        select(Opportunity).where(
            Opportunity.source_id == source.id,
            Opportunity.item_id == item.id,
            Opportunity.session_id.is_(None),
        )
    )
    if existing is not None:
        return existing, False
    opportunity = Opportunity(
        source_id=source.id,
        session_id=None,
        item_id=item.id,
        rule_id=None,
        status="new",
        evaluation_status=evaluation_status,
        filter_snapshot=filters,
    )
    db.add(opportunity)
    db.flush()
    return opportunity, True


def _record_session_item_state(
    db: Session,
    session: MonitorSession,
    item: Item,
    status: str,
    opportunity_id: int | None,
) -> None:
    state = SessionItemState(
        session_id=session.id,
        item_id=item.id,
        filter_hash=session.filter_hash,
        status=status,
        opportunity_id=opportunity_id,
    )
    db.add(state)
    db.flush()


def _deduplicate_candidates(candidates: list[CatalogItemCandidate]) -> list[CatalogItemCandidate]:
    unique_candidates: dict[str, CatalogItemCandidate] = {}
    for candidate in candidates:
        unique_candidates[candidate.vinted_item_id] = candidate
    return list(unique_candidates.values())


def _track_source_seen_items(
    db: Session,
    source_id: int,
    run_id: int,
    candidates: list[CatalogItemCandidate],
    item_ids_by_vinted_id: dict[str, int],
) -> set[int]:
    if not candidates:
        return set()

    return _upsert_source_seen_item_ids(
        db,
        source_id,
        run_id,
        [
            item_ids_by_vinted_id[candidate.vinted_item_id]
            for candidate in candidates
            if candidate.vinted_item_id in item_ids_by_vinted_id
        ],
    )


def _upsert_source_seen_items(db: Session, source_id: int, run_id: int, items: list[Item]) -> None:
    _upsert_source_seen_item_ids(db, source_id, run_id, [item.id for item in items])


def _upsert_source_seen_item_ids(db: Session, source_id: int, run_id: int, item_ids: list[int]) -> set[int]:
    if not item_ids:
        return set()

    unique_item_ids = list(dict.fromkeys(item_ids))
    existing_item_ids = set(
        db.scalars(select(SourceSeenItem.item_id).where(SourceSeenItem.source_id == source_id, SourceSeenItem.item_id.in_(unique_item_ids)))
    )
    new_item_ids = set(unique_item_ids) - existing_item_ids
    now = datetime.now(UTC)
    rows = [
        {
            "source_id": source_id,
            "item_id": item_id,
            "first_run_id": run_id,
            "last_run_id": run_id,
            "first_seen_at": now,
            "last_seen_at": now,
        }
        for item_id in unique_item_ids
    ]
    statement = pg_insert(SourceSeenItem).values(rows)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[SourceSeenItem.source_id, SourceSeenItem.item_id],
            set_={
                "last_run_id": run_id,
                "last_seen_at": now,
            },
        )
    )
    db.flush()
    return new_item_ids


def _get_cached_known_item_ids(vinted_item_ids: list[str]) -> dict[str, int]:
    with _CACHE_LOCK:
        return {
            vinted_item_id: GLOBAL_KNOWN_ID_CACHE[vinted_item_id]
            for vinted_item_id in vinted_item_ids
            if vinted_item_id in GLOBAL_KNOWN_ID_CACHE
        }


def _remember_known_items(items_by_vinted_id: dict[str, int]) -> None:
    with _CACHE_LOCK:
        _remember_items(GLOBAL_KNOWN_ID_CACHE, items_by_vinted_id, GLOBAL_KNOWN_ID_CACHE_LIMIT)


def _remember_source_seen_items(source_id: int, items_by_vinted_id: dict[str, int]) -> None:
    with _CACHE_LOCK:
        cache = SOURCE_SEEN_ID_CACHE.setdefault(source_id, OrderedDict())
        _remember_items(cache, items_by_vinted_id, SOURCE_SEEN_ID_CACHE_LIMIT)


def _remember_items(cache: OrderedDict[str, int], items_by_vinted_id: dict[str, int], limit: int) -> None:
    for vinted_item_id, item_id in items_by_vinted_id.items():
        cache.pop(vinted_item_id, None)
        cache[vinted_item_id] = item_id
    while len(cache) > limit:
        cache.popitem(last=False)


def _fetch_and_persist_details(
    db: Session,
    provider: ManualRunProvider,
    source: SearchSource,
    run: Run,
    candidates: list[CatalogItemCandidate],
    items_by_vinted_id: dict[str, Item],
) -> None:
    fetch_detail = getattr(provider, "fetch_detail", None)
    if fetch_detail is None or not candidates:
        return

    settings = getattr(provider, "settings", get_settings())
    max_candidates = max(settings.vinted_detail_max_candidates_per_run, 0)
    if max_candidates == 0:
        return

    selected_candidates = candidates[:max_candidates]
    concurrency = max(min(settings.vinted_detail_concurrency, len(selected_candidates)), 1)
    if concurrency == 1:
        for candidate in selected_candidates:
            _fetch_and_persist_one_detail(db, fetch_detail, source, run, candidate, items_by_vinted_id)
        return

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(fetch_detail, candidate): candidate for candidate in selected_candidates}
        for future in as_completed(futures):
            candidate = futures[future]
            item = items_by_vinted_id[candidate.vinted_item_id]
            try:
                detail = future.result()
            except Exception as exc:
                _record_detail_error(db, source, run, item, exc)
                continue
            apply_item_detail(db, item, detail)


def _fetch_and_persist_one_detail(
    db: Session,
    fetch_detail,
    source: SearchSource,
    run: Run,
    candidate: CatalogItemCandidate,
    items_by_vinted_id: dict[str, Item],
) -> None:
    item = items_by_vinted_id[candidate.vinted_item_id]
    try:
        detail = fetch_detail(candidate)
    except Exception as exc:
        _record_detail_error(db, source, run, item, exc)
        return
    apply_item_detail(db, item, detail)


def _record_detail_error(db: Session, source: SearchSource, run: Run, item: Item, exc: Exception) -> None:
    message = redact_sensitive_text(str(exc))
    record_item_detail_error(db, item, message)
    db.add(
        ErrorLog(
            run_id=run.id,
            source_id=source.id,
            kind=exc.__class__.__name__,
            message=message,
            details={"stage": "item_detail", "vinted_item_id": item.vinted_item_id},
        )
    )
    db.flush()
