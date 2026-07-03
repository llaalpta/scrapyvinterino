from datetime import datetime
from decimal import Decimal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from vinted_monitor.api.schemas import (
    ActionRequestCreate,
    ActionRequestRead,
    FilterRuleCreate,
    FilterRuleRead,
    FilterRuleUpdate,
    ItemRead,
    ItemResultPageRead,
    ItemResultRead,
    MonitorSessionCreate,
    MonitorSessionRead,
    OpportunityResultPageRead,
    OpportunityResultRead,
    ProxyProfileCreate,
    ProxyProfileRead,
    ProxyProfileUpdate,
    RunEventRead,
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
from vinted_monitor.services.filters import (
    FilterRuleNotFoundError,
    create_filter_rule,
    list_filter_rules,
    update_filter_rule,
)
from vinted_monitor.services.proxies import (
    ProxyProfileNotFoundError,
    create_proxy_profile,
    list_proxy_profiles,
    mark_proxy_test_result,
    profile_to_public_fields,
    proxy_url_for_profile,
    update_proxy_profile,
)
from vinted_monitor.services.run_events import list_run_events
from vinted_monitor.services.runs import (
    ManualRunProvider,
    MonitorSessionRunError,
    RunAlreadyActiveError,
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
    archive_source,
    create_source,
    list_sources,
    update_source,
)
from vinted_monitor.services.sessions import (
    MonitorSessionConflictError,
    MonitorSessionNotFoundError,
    MonitorSessionSourceError,
    list_monitor_sessions,
    run_monitor_session,
    start_monitor_session,
    stop_monitor_session,
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


@app.delete("/api/sources/{source_id}", status_code=204)
def delete_source(source_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        archive_source(db, source_id)
    except SourceUpdateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@app.get("/api/scheduler", response_model=SchedulerStateRead)
def get_scheduler(db: Session = Depends(get_db)):
    return get_scheduler_state(db, settings)


@app.patch("/api/scheduler", response_model=SchedulerStateRead)
def patch_scheduler(payload: SchedulerUpdate, db: Session = Depends(get_db)):
    return update_scheduler_enabled(db, payload.enabled, settings)


@app.get("/api/filter-rules", response_model=list[FilterRuleRead])
def get_filter_rules(db: Session = Depends(get_db)) -> list:
    return list_filter_rules(db)


@app.post("/api/filter-rules", response_model=FilterRuleRead, status_code=201)
def post_filter_rule(payload: FilterRuleCreate, db: Session = Depends(get_db)):
    try:
        return create_filter_rule(db, name=payload.name, definition=payload.definition, is_active=payload.is_active)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/filter-rules/{rule_id}", response_model=FilterRuleRead)
def patch_filter_rule(rule_id: int, payload: FilterRuleUpdate, db: Session = Depends(get_db)):
    try:
        return update_filter_rule(db, rule_id, name=payload.name, definition=payload.definition, is_active=payload.is_active)
    except FilterRuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/proxy-profiles", response_model=list[ProxyProfileRead])
def get_proxy_profiles(db: Session = Depends(get_db)) -> list[ProxyProfileRead]:
    return [ProxyProfileRead(**profile_to_public_fields(profile, settings).__dict__) for profile in list_proxy_profiles(db)]


@app.post("/api/proxy-profiles", response_model=ProxyProfileRead, status_code=201)
def post_proxy_profile(payload: ProxyProfileCreate, db: Session = Depends(get_db)) -> ProxyProfileRead:
    try:
        profile = create_proxy_profile(
            db,
            name=payload.name,
            scheme=payload.scheme,
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password,
            is_active=payload.is_active,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProxyProfileRead(**profile_to_public_fields(profile, settings).__dict__)


@app.patch("/api/proxy-profiles/{profile_id}", response_model=ProxyProfileRead)
def patch_proxy_profile(profile_id: int, payload: ProxyProfileUpdate, db: Session = Depends(get_db)) -> ProxyProfileRead:
    try:
        profile = update_proxy_profile(
            db,
            profile_id,
            name=payload.name,
            scheme=payload.scheme,
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password,
            clear_password=payload.clear_password,
            is_active=payload.is_active,
            settings=settings,
        )
    except ProxyProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProxyProfileRead(**profile_to_public_fields(profile, settings).__dict__)


@app.post("/api/proxy-profiles/{profile_id}/test", response_model=ProxyProfileRead)
def post_proxy_profile_test(profile_id: int, db: Session = Depends(get_db)) -> ProxyProfileRead:
    profile = next((entry for entry in list_proxy_profiles(db) if entry.id == profile_id), None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Proxy profile {profile_id} does not exist")
    try:
        with httpx.Client(proxy=proxy_url_for_profile(profile, settings), timeout=10) as client:
            response = client.get("https://api.ipify.org?format=json")
            response.raise_for_status()
            ip = response.json().get("ip")
        updated = mark_proxy_test_result(db, profile_id, status="success", ip=str(ip) if ip else None)
    except Exception as exc:
        updated = mark_proxy_test_result(db, profile_id, status="failed", error=str(exc))
    return ProxyProfileRead(**profile_to_public_fields(updated, settings).__dict__)


@app.get("/api/monitor-sessions", response_model=list[MonitorSessionRead])
def get_monitor_sessions(db: Session = Depends(get_db)) -> list[MonitorSessionRead]:
    return [
        MonitorSessionRead.model_validate(
            {
                **result.session.__dict__,
                "source_name": result.source_name,
                "proxy_name": result.proxy_name,
            }
        )
        for result in list_monitor_sessions(db)
    ]


@app.post("/api/monitor-sessions", response_model=MonitorSessionRead, status_code=201)
def post_monitor_session(payload: MonitorSessionCreate, db: Session = Depends(get_db)) -> MonitorSessionRead:
    try:
        session = start_monitor_session(
            db,
            source_id=payload.source_id,
            filter_rule_ids=payload.filter_rule_ids,
            proxy_profile_id=payload.proxy_profile_id,
            duration_minutes=payload.duration_minutes,
        )
    except (MonitorSessionSourceError, FilterRuleNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MonitorSessionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MonitorSessionRead.model_validate({**session.__dict__, "source_name": None, "proxy_name": None})


@app.post("/api/monitor-sessions/{session_id}/stop", response_model=MonitorSessionRead)
def post_monitor_session_stop(session_id: int, db: Session = Depends(get_db)) -> MonitorSessionRead:
    try:
        session = stop_monitor_session(db, session_id)
    except MonitorSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MonitorSessionRead.model_validate({**session.__dict__, "source_name": None, "proxy_name": None})


@app.post("/api/monitor-sessions/{session_id}/runs", response_model=RunRead, status_code=201)
def post_monitor_session_run(session_id: int, db: Session = Depends(get_db)):
    try:
        return run_monitor_session(db, session_id, settings=settings)
    except MonitorSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MonitorSessionConflictError, RunAlreadyActiveError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MonitorSessionRunError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@app.get("/api/runs/{run_id}/events", response_model=list[RunEventRead])
def get_run_events(run_id: int, db: Session = Depends(get_db)) -> list:
    return list_run_events(db, run_id)


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
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
        session_id=result.opportunity.session_id,
        rule_id=result.opportunity.rule_id,
        status=result.opportunity.status,
        evaluation_status=result.opportunity.evaluation_status,
        filter_snapshot=result.opportunity.filter_snapshot,
        score=result.opportunity.score,
        created_at=result.opportunity.created_at,
    )
