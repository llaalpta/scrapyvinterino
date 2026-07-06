import asyncio
import json
from datetime import datetime
from decimal import Decimal

from curl_cffi.requests import Session as CurlSession
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.api.schemas import (
    ActionRequestCreate,
    ActionRequestRead,
    ItemRead,
    MonitorStatsRead,
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
from vinted_monitor.db.models import RunEvent, SearchSource
from vinted_monitor.db.session import SessionLocal, get_db
from vinted_monitor.services.actions import create_action_request
from vinted_monitor.services.browse import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    OpportunityResult,
    list_opportunity_results,
)
from vinted_monitor.services.monitor_stats import MonitorStatsNotFoundError, MonitorStatsRangeError, get_monitor_stats
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
    RunAlreadyActiveError,
    SearchSourceNotFoundError,
    execute_manual_run,
    execute_monitor_run,
    list_runs,
)
from vinted_monitor.services.scheduler import (
    SchedulerCapacityError,
    SchedulerConfigError,
    ensure_scheduler_can_activate,
    get_scheduler_state,
    update_scheduler_config,
)
from vinted_monitor.services.search_sources import (
    SearchSourceActiveError,
    SearchSourceConfigError,
    archive_source,
    create_source,
    list_sources,
    start_source_monitor,
    stop_source_monitor,
    update_source,
)
from vinted_monitor.services.search_sources import (
    SearchSourceNotFoundError as SourceUpdateNotFoundError,
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


def get_manual_run_provider() -> ManualRunProvider | None:
    return None


@app.get("/api/monitors", response_model=list[SearchSourceRead])
def get_monitors(db: Session = Depends(get_db)) -> list:
    return list_sources(db)


@app.post("/api/monitors", response_model=SearchSourceRead, status_code=201)
def post_monitor(payload: SearchSourceCreate, db: Session = Depends(get_db)):
    return create_source(db, payload.name, payload.url)


@app.patch("/api/monitors/{monitor_id}", response_model=SearchSourceRead)
def patch_monitor(monitor_id: int, payload: SearchSourceUpdate, db: Session = Depends(get_db)):
    try:
        return update_source(
            db,
            monitor_id,
            name=payload.name,
            url=payload.url,
            scheduler_config=payload.scheduler_config,
            monitor_mode=payload.monitor_mode,
            duration_minutes=payload.duration_minutes,
            clear_duration_minutes="duration_minutes" in payload.model_fields_set and payload.duration_minutes is None,
            filter_definition=payload.filter_definition,
        )
    except SourceUpdateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchSourceActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SearchSourceConfigError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        archive_source(db, monitor_id)
    except SourceUpdateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@app.post("/api/monitors/{monitor_id}/start", response_model=RunRead, status_code=201)
def post_monitor_start(
    monitor_id: int,
    db: Session = Depends(get_db),
    provider: ManualRunProvider | None = Depends(get_manual_run_provider),
):
    try:
        source = db.get(SearchSource, monitor_id)
        if source is not None and source.monitor_mode == "manual":
            return execute_manual_run(db, monitor_id, provider=provider)
        ensure_scheduler_can_activate(db, settings, source_id=monitor_id)
        source = start_source_monitor(db, monitor_id)
        return execute_monitor_run(db, monitor_id, provider=provider)
    except (SearchSourceNotFoundError, SourceUpdateNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SchedulerCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SearchSourceConfigError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/monitors/{monitor_id}/stop", response_model=SearchSourceRead)
def post_monitor_stop(monitor_id: int, db: Session = Depends(get_db)):
    try:
        return stop_source_monitor(db, monitor_id)
    except SourceUpdateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/scheduler", response_model=SchedulerStateRead)
def get_scheduler(db: Session = Depends(get_db)):
    try:
        return get_scheduler_state(db, settings)
    except SchedulerConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/scheduler", response_model=SchedulerStateRead)
def patch_scheduler(payload: SchedulerUpdate, db: Session = Depends(get_db)):
    try:
        return update_scheduler_config(db, payload.model_dump(exclude_unset=True), settings)
    except SchedulerConfigError as exc:
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
            kind=payload.kind,
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password,
            max_concurrent_runs=payload.max_concurrent_runs,
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
            kind=payload.kind,
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password,
            clear_password=payload.clear_password,
            max_concurrent_runs=payload.max_concurrent_runs,
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
        proxy_url = proxy_url_for_profile(profile, settings)
        proxy_dict = {"https": proxy_url, "http": proxy_url} if proxy_url else None
        with CurlSession(impersonate=settings.curl_impersonate_browser, proxies=proxy_dict) as client:
            response = client.get("https://api.ipify.org?format=json", timeout=10)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            ip = response.json().get("ip")
        updated = mark_proxy_test_result(db, profile_id, status="success", ip=str(ip) if ip else None)
    except Exception as exc:
        updated = mark_proxy_test_result(db, profile_id, status="failed", error=str(exc))
    return ProxyProfileRead(**profile_to_public_fields(updated, settings).__dict__)


@app.get("/api/opportunities", response_model=OpportunityResultPageRead)
def get_opportunities(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    source_id: int | None = Query(None, ge=1),
    scraped_from: datetime | None = None,
    scraped_to: datetime | None = None,
    price_min: Decimal | None = Query(None, ge=0),
    price_max: Decimal | None = Query(None, ge=0),
    evaluation_status: str | None = None,
    db: Session = Depends(get_db),
) -> OpportunityResultPageRead:
    try:
        result_page = list_opportunity_results(
            db,
            page=page,
            page_size=page_size,
            source_id=source_id,
            scraped_from=scraped_from,
            scraped_to=scraped_to,
            price_min=price_min,
            price_max=price_max,
            evaluation_status=evaluation_status,
        )
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
def get_runs(limit: int = 50, source_id: int | None = None, db: Session = Depends(get_db)) -> list:
    return list_runs(db, limit=limit, source_id=source_id)


@app.get("/api/monitors/{monitor_id}/stats", response_model=MonitorStatsRead)
def get_monitor_stats_endpoint(monitor_id: int, range: str = "hours", db: Session = Depends(get_db)):  # noqa: A002
    try:
        return get_monitor_stats(db, monitor_id, range_name=range)
    except MonitorStatsNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MonitorStatsRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/events", response_model=list[RunEventRead])
def get_run_events(run_id: int, db: Session = Depends(get_db)) -> list:
    return list_run_events(db, run_id)


@app.get("/api/monitors/{monitor_id}/events", response_model=list[RunEventRead])
def get_monitor_events(monitor_id: int, db: Session = Depends(get_db)) -> list:
    return list(db.scalars(select(RunEvent).where(RunEvent.source_id == monitor_id).order_by(RunEvent.created_at.asc(), RunEvent.id.asc())))


@app.get("/api/monitors/events/stream")
def stream_monitor_events(last_event_id: int = Query(0, ge=0)):
    async def event_stream():
        current_id = last_event_id
        while True:
            with SessionLocal() as db:
                events = list(
                    db.scalars(
                        select(RunEvent)
                        .where(RunEvent.id > current_id, RunEvent.source_id.is_not(None))
                        .order_by(RunEvent.id.asc())
                        .limit(100)
                    )
                )
            for event in events:
                current_id = event.id
                payload = {
                    "id": event.id,
                    "source_id": event.source_id,
                    "run_id": event.run_id,
                    "phase": event.phase,
                    "level": event.level,
                    "created_at": event.created_at.isoformat(),
                    "method": event.method,
                    "url": event.url,
                    "status_code": event.status_code,
                    "duration_ms": event.duration_ms,
                    "proxy_profile_id": event.proxy_profile_id,
                    "egress_ip": event.egress_ip,
                    "user_agent": event.user_agent,
                    "auth_mode": event.auth_mode,
                    "message": event.message,
                    "details": event.details,
                }
                yield f"id: {event.id}\nevent: monitor_event\ndata: {json.dumps(payload)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/monitors/{monitor_id}/runs", response_model=RunRead, status_code=201)
def post_monitor_run(
    monitor_id: int,
    db: Session = Depends(get_db),
    provider: ManualRunProvider | None = Depends(get_manual_run_provider),
):
    try:
        return execute_manual_run(db, monitor_id, provider=provider)
    except SearchSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SchedulerCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/actions", response_model=ActionRequestRead, status_code=201)
def post_action(payload: ActionRequestCreate, db: Session = Depends(get_db)):
    if not settings.action_requests_enabled:
        raise HTTPException(status_code=404, detail="Action requests are disabled")

    try:
        return create_action_request(db, payload.item_id, payload.action_type, payload.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _opportunity_result_read(result: OpportunityResult) -> OpportunityResultRead:
    return OpportunityResultRead(
        id=result.opportunity.id,
        item=ItemRead.model_validate(result.item),
        source_id=result.opportunity.source_id,
        source_name=result.source_name,
        status=result.opportunity.status,
        evaluation_status=result.opportunity.evaluation_status,
        filter_snapshot=result.opportunity.filter_snapshot,
        score=result.opportunity.score,
        created_at=result.opportunity.created_at,
        last_scraped_at=result.last_scraped_at or result.opportunity.created_at,
        last_run_id=result.last_run_id,
    )
