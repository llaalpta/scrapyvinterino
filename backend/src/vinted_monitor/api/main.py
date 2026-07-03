from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from vinted_monitor.api.schemas import (
    ActionRequestCreate,
    ActionRequestRead,
    ItemRead,
    ItemResultPageRead,
    ItemResultRead,
    OpportunityResultPageRead,
    OpportunityResultRead,
    RunRead,
    SchedulerStateRead,
    SchedulerUpdate,
    SearchSourceCreate,
    SearchSourceRead,
    SearchSourceUpdate,
)
from vinted_monitor.core.config import get_settings
from vinted_monitor.core.logging import configure_logging
from vinted_monitor.db.session import get_db
from vinted_monitor.providers.vinted_catalog import HttpVintedCatalogProvider
from vinted_monitor.services.actions import create_action_request
from vinted_monitor.services.browse import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ItemResult,
    OpportunityResult,
    list_item_results,
    list_opportunity_results,
)
from vinted_monitor.services.runs import (
    ManualRunProvider,
    SearchSourceInactiveError,
    SearchSourceNotFoundError,
    execute_manual_run,
    list_runs,
)
from vinted_monitor.services.scheduler import get_scheduler_state, update_scheduler_enabled
from vinted_monitor.services.search_sources import (
    SearchSourceNotFoundError as SourceUpdateNotFoundError,
)
from vinted_monitor.services.search_sources import (
    create_source,
    list_sources,
    update_source,
)

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title="Vinted Monitor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def get_manual_run_provider() -> ManualRunProvider:
    return HttpVintedCatalogProvider()


@app.get("/api/sources", response_model=list[SearchSourceRead])
def get_sources(db: Session = Depends(get_db)) -> list:
    return list_sources(db)


@app.post("/api/sources", response_model=SearchSourceRead, status_code=201)
def post_source(payload: SearchSourceCreate, db: Session = Depends(get_db)):
    return create_source(db, payload.name, payload.url)


@app.patch("/api/sources/{source_id}", response_model=SearchSourceRead)
def patch_source(source_id: int, payload: SearchSourceUpdate, db: Session = Depends(get_db)):
    try:
        return update_source(
            db,
            source_id,
            is_active=payload.is_active,
            scheduler_config=payload.scheduler_config,
        )
    except SourceUpdateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/scheduler", response_model=SchedulerStateRead)
def get_scheduler(db: Session = Depends(get_db)):
    return get_scheduler_state(db, settings)


@app.patch("/api/scheduler", response_model=SchedulerStateRead)
def patch_scheduler(payload: SchedulerUpdate, db: Session = Depends(get_db)):
    return update_scheduler_enabled(db, payload.enabled, settings)


@app.get("/api/items", response_model=ItemResultPageRead)
def get_items(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    source_id: int | None = Query(None, ge=1),
    scraped_from: datetime | None = None,
    scraped_to: datetime | None = None,
    price_min: Decimal | None = Query(None, ge=0),
    price_max: Decimal | None = Query(None, ge=0),
    db: Session = Depends(get_db),
) -> ItemResultPageRead:
    try:
        result_page = list_item_results(
            db,
            page=page,
            page_size=page_size,
            source_id=source_id,
            scraped_from=scraped_from,
            scraped_to=scraped_to,
            price_min=price_min,
            price_max=price_max,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ItemResultPageRead(
        items=[_item_result_read(item_result) for item_result in result_page.items],
        total=result_page.total,
        page=result_page.page,
        page_size=result_page.page_size,
        total_pages=result_page.total_pages,
    )


@app.get("/api/opportunities", response_model=OpportunityResultPageRead)
def get_opportunities(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
) -> OpportunityResultPageRead:
    try:
        result_page = list_opportunity_results(db, page=page, page_size=page_size)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return OpportunityResultPageRead(
        items=[_opportunity_result_read(opportunity_result) for opportunity_result in result_page.items],
        total=result_page.total,
        page=result_page.page,
        page_size=result_page.page_size,
        total_pages=result_page.total_pages,
    )


@app.get("/api/runs", response_model=list[RunRead])
def get_runs(limit: int = 50, db: Session = Depends(get_db)) -> list:
    return list_runs(db, limit=limit)


@app.post("/api/sources/{source_id}/runs", response_model=RunRead, status_code=201)
def post_source_run(
    source_id: int,
    db: Session = Depends(get_db),
    provider: ManualRunProvider = Depends(get_manual_run_provider),
):
    try:
        return execute_manual_run(db, source_id, provider=provider)
    except SearchSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SearchSourceInactiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/actions", response_model=ActionRequestRead, status_code=201)
def post_action(payload: ActionRequestCreate, db: Session = Depends(get_db)):
    if not settings.action_requests_enabled:
        raise HTTPException(status_code=404, detail="Action requests are disabled")

    try:
        return create_action_request(db, payload.item_id, payload.action_type, payload.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _item_result_read(result: ItemResult) -> ItemResultRead:
    return ItemResultRead.model_validate(
        {
            **ItemRead.model_validate(result.item).model_dump(),
            "last_scraped_at": result.last_scraped_at,
            "last_scraped_source_id": result.last_scraped_source_id,
            "last_scraped_source_name": result.last_scraped_source_name,
            "last_run_id": result.last_run_id,
        }
    )


def _opportunity_result_read(result: OpportunityResult) -> OpportunityResultRead:
    return OpportunityResultRead(
        id=result.opportunity.id,
        item=ItemRead.model_validate(result.item),
        source_id=result.opportunity.source_id,
        source_name=result.source_name,
        rule_id=result.opportunity.rule_id,
        status=result.opportunity.status,
        score=result.opportunity.score,
        created_at=result.opportunity.created_at,
    )
