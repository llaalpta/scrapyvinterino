from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.redaction import redact_sensitive_text
from vinted_monitor.db.models import ErrorLog, Item, Run, SearchSource, SourceSeenItem
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult, CatalogSource
from vinted_monitor.providers.vinted_catalog import HttpVintedCatalogProvider
from vinted_monitor.services.items import apply_item_detail, get_items_by_vinted_ids, persist_catalog_items, record_item_detail_error

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
SOURCE_SEEN_ID_CACHE_LIMIT = 10_000
GLOBAL_KNOWN_ID_CACHE_LIMIT = 50_000
# These caches are hints only. Database writes remain the source of truth for
# newness, item updates, and source traceability.
SOURCE_SEEN_ID_CACHE: dict[int, OrderedDict[str, None]] = {}
GLOBAL_KNOWN_ID_CACHE: OrderedDict[str, None] = OrderedDict()


class ManualRunProvider(Protocol):
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        """Return public catalog candidates for a manual run."""

    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        """Return public detail data for a candidate."""


class SearchSourceNotFoundError(ValueError):
    pass


class SearchSourceInactiveError(ValueError):
    pass


def execute_manual_run(
    db: Session,
    source_id: int,
    provider: ManualRunProvider | None = None,
) -> Run:
    source = db.get(SearchSource, source_id)
    if source is None:
        raise SearchSourceNotFoundError(f"Search source {source_id} does not exist")
    if not source.is_active:
        raise SearchSourceInactiveError(f"Search source {source_id} is inactive")

    run = Run(
        source_id=source.id,
        status=RUNNING,
        items_found=0,
        items_new=0,
        opportunities_created=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    run_provider = provider or HttpVintedCatalogProvider()
    try:
        result = run_provider.search(source)
    except Exception as exc:
        return _record_failed_run(db, run, source, exc)

    try:
        persistence_result = persist_catalog_items(db, result.items)
        unique_candidates = _deduplicate_candidates(result.items)
        items_by_vinted_id = get_items_by_vinted_ids(db, [candidate.vinted_item_id for candidate in unique_candidates])
        inserted_vinted_item_ids = set(persistence_result.inserted_vinted_item_ids)
        global_new_candidates = [
            candidate for candidate in unique_candidates if candidate.vinted_item_id in inserted_vinted_item_ids
        ]
        _track_source_seen_items(db, source.id, run.id, unique_candidates, items_by_vinted_id)
        _fetch_and_persist_details(db, run_provider, source, run, global_new_candidates, items_by_vinted_id)

        run.status = SUCCESS
        run.finished_at = datetime.now(UTC)
        run.items_found = persistence_result.found_count
        run.items_new = persistence_result.inserted_count
        run.opportunities_created = 0
        run.error_message = None
        db.commit()
        committed_vinted_item_ids = [candidate.vinted_item_id for candidate in unique_candidates]
        _remember_known_ids(committed_vinted_item_ids)
        _remember_source_seen_ids(source.id, committed_vinted_item_ids)
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


def _record_failed_run(db: Session, run: Run, source: SearchSource, exc: Exception) -> Run:
    message = redact_sensitive_text(str(exc))
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
    items_by_vinted_id: dict[str, Item],
) -> None:
    if not candidates:
        return

    _upsert_source_seen_items(db, source_id, run_id, [items_by_vinted_id[candidate.vinted_item_id] for candidate in candidates])


def _upsert_source_seen_items(db: Session, source_id: int, run_id: int, items: list[Item]) -> None:
    if not items:
        return

    now = datetime.now(UTC)
    rows = [
        {
            "source_id": source_id,
            "item_id": item.id,
            "first_run_id": run_id,
            "last_run_id": run_id,
            "first_seen_at": now,
            "last_seen_at": now,
        }
        for item in items
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


def _remember_known_ids(vinted_item_ids: list[str]) -> None:
    _remember_ids(GLOBAL_KNOWN_ID_CACHE, vinted_item_ids, GLOBAL_KNOWN_ID_CACHE_LIMIT)


def _remember_source_seen_ids(source_id: int, vinted_item_ids: list[str]) -> None:
    cache = SOURCE_SEEN_ID_CACHE.setdefault(source_id, OrderedDict())
    _remember_ids(cache, vinted_item_ids, SOURCE_SEEN_ID_CACHE_LIMIT)


def _remember_ids(cache: OrderedDict[str, None], vinted_item_ids: list[str], limit: int) -> None:
    for vinted_item_id in vinted_item_ids:
        cache.pop(vinted_item_id, None)
        cache[vinted_item_id] = None
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
