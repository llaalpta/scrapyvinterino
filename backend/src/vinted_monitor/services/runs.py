from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import ErrorLog, Run, SearchSource
from vinted_monitor.providers.catalog import CatalogSearchResult, CatalogSource
from vinted_monitor.providers.vinted_catalog import HttpVintedCatalogProvider

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class ManualRunProvider(Protocol):
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        """Return public catalog candidates for a manual run."""


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
        run.status = FAILED
        run.finished_at = datetime.now(UTC)
        run.error_message = str(exc)
        db.add(
            ErrorLog(
                run_id=run.id,
                source_id=source.id,
                kind=exc.__class__.__name__,
                message=str(exc),
                details={},
            )
        )
        db.commit()
        db.refresh(run)
        return run

    run.status = SUCCESS
    run.finished_at = datetime.now(UTC)
    run.items_found = len(result.items)
    run.items_new = 0
    run.opportunities_created = 0
    run.error_message = None
    db.commit()
    db.refresh(run)
    return run


def list_runs(db: Session, limit: int = 50) -> list[Run]:
    statement = select(Run).order_by(Run.started_at.desc(), Run.id.desc()).limit(limit)
    return list(db.scalars(statement))
