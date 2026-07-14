import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from curl_cffi.requests import Session as CurlSession
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.api.local_auth import (
    auth_router,
    local_session_hash_is_active_in_database,
    require_api_access,
)
from vinted_monitor.api.schemas import (
    ActionRequestCreate,
    ActionRequestRead,
    ItemDetailProbeCreate,
    ItemDetailProbeRead,
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
    VintedSessionRead,
)
from vinted_monitor.core.config import get_settings
from vinted_monitor.core.logging import configure_logging
from vinted_monitor.db.models import RunEvent, SearchSource
from vinted_monitor.db.session import get_db
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
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
from vinted_monitor.services.run_event_stream import monitor_event_stream, resolve_monitor_event_cursor
from vinted_monitor.services.run_events import list_run_events
from vinted_monitor.services.runs import (
    BaselineRequiredError,
    ManualRunProvider,
    RunAlreadyActiveError,
    SearchSourceInactiveError,
    SearchSourceNotFoundError,
    execute_manual_run,
    execute_monitor_baseline,
    execute_monitor_item_detail_probe,
    execute_monitor_session_prepare,
    list_runs,
)
from vinted_monitor.services.scheduler import (
    SchedulerCapacityError,
    SchedulerConfigError,
    SchedulerUnavailableError,
    acquire_initial_run_admission_lock,
    choose_run_egress,
    ensure_scheduler_can_activate,
    get_scheduler_state,
    update_scheduler_config,
)
from vinted_monitor.services.search_sources import (
    SearchSourceActiveError,
    SearchSourceConfigError,
    SearchSourceRunActiveError,
    archive_source,
    catalog_filter_compatibility,
    create_source,
    list_sources,
    stop_source_monitor,
    update_source,
    validate_vinted_catalog_url,
)
from vinted_monitor.services.search_sources import (
    SearchSourceNotFoundError as SourceUpdateNotFoundError,
)
from vinted_monitor.services.seen_cache import SeenCacheUnavailableError
from vinted_monitor.services.vinted_sessions import (
    VintedSessionRequiredError,
    list_vinted_session_summaries_for_source,
)

settings = get_settings()
configure_logging(settings.log_level)

development_like = settings.app_env.strip().lower() in {"development", "test"}
app = FastAPI(
    title="Vinted Monitor API",
    version="0.1.0",
    docs_url="/docs" if development_like else None,
    redoc_url="/redoc" if development_like else None,
    openapi_url="/openapi.json" if development_like else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Last-Event-ID", "X-CSRF-Token"],
)
app.include_router(auth_router)
business_router = APIRouter(prefix="/api", dependencies=[Depends(require_api_access)])


@app.middleware("http")
async def prevent_api_response_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def get_manual_run_provider() -> ManualRunProvider | None:
    return None


def _source_read(source: SearchSource, db: Session) -> SearchSourceRead:
    return SearchSourceRead.model_validate(source).model_copy(
        update={
            "catalog_filter_compatibility": catalog_filter_compatibility(source.url),
            "prepared_sessions": [
                VintedSessionRead(**summary.__dict__)
                for summary in list_vinted_session_summaries_for_source(db, source.id, settings)
            ],
        }
    )


def _proxy_profile_read(profile) -> ProxyProfileRead:
    return ProxyProfileRead(**profile_to_public_fields(profile, settings).__dict__)


@business_router.get("/monitors", response_model=list[SearchSourceRead])
def get_monitors(db: Session = Depends(get_db)) -> list[SearchSourceRead]:
    return [_source_read(source, db) for source in list_sources(db)]


@business_router.post("/monitors", response_model=SearchSourceRead, status_code=201)
def post_monitor(payload: SearchSourceCreate, db: Session = Depends(get_db)):
    return _source_read(create_source(db, payload.name, payload.url), db)


@business_router.patch("/monitors/{monitor_id}", response_model=SearchSourceRead)
def patch_monitor(monitor_id: int, payload: SearchSourceUpdate, db: Session = Depends(get_db)):
    try:
        return _source_read(
            update_source(
                db,
                monitor_id,
                name=payload.name,
                url=payload.url,
                scheduler_config=payload.scheduler_config,
                monitor_mode=payload.monitor_mode,
                duration_minutes=payload.duration_minutes,
                clear_duration_minutes="duration_minutes" in payload.model_fields_set and payload.duration_minutes is None,
                filter_definition=payload.filter_definition,
            ),
            db,
        )
    except SourceUpdateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchSourceActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SearchSourceConfigError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@business_router.delete("/monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        archive_source(db, monitor_id)
    except SourceUpdateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@business_router.post("/monitors/{monitor_id}/start", response_model=RunRead, status_code=201)
def post_monitor_start(
    monitor_id: int,
    db: Session = Depends(get_db),
    provider: ManualRunProvider | None = Depends(get_manual_run_provider),
):
    try:
        source = db.get(SearchSource, monitor_id)
        if source is None or source.archived_at is not None:
            raise SearchSourceNotFoundError(f"Search source {monitor_id} does not exist")
        validate_vinted_catalog_url(source.url)
        if source.monitor_mode == "manual":
            return execute_monitor_baseline(
                db,
                monitor_id,
                provider=provider,
                activate_session=True,
            )
        if source.is_active:
            raise SearchSourceActiveError(f"Search source {monitor_id} is already active")
        acquire_initial_run_admission_lock(db)
        ensure_scheduler_can_activate(db, settings, source_id=monitor_id)
        egress = choose_run_egress(db, settings)
        return execute_monitor_baseline(
            db,
            monitor_id,
            provider=provider,
            egress=egress,
            activate_session=True,
        )
    except (SearchSourceNotFoundError, SourceUpdateNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchSourceActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SchedulerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SchedulerCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (BaselineRequiredError, SeenCacheUnavailableError, VintedSessionRequiredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SearchSourceConfigError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@business_router.post("/monitors/{monitor_id}/stop", response_model=SearchSourceRead)
def post_monitor_stop(monitor_id: int, db: Session = Depends(get_db)):
    try:
        return _source_read(stop_source_monitor(db, monitor_id), db)
    except SourceUpdateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SearchSourceRunActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@business_router.get("/scheduler", response_model=SchedulerStateRead)
def get_scheduler(db: Session = Depends(get_db)):
    try:
        return get_scheduler_state(db, settings)
    except SchedulerConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@business_router.patch("/scheduler", response_model=SchedulerStateRead)
def patch_scheduler(payload: SchedulerUpdate, db: Session = Depends(get_db)):
    try:
        return update_scheduler_config(db, payload.model_dump(exclude_unset=True), settings)
    except SchedulerConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@business_router.get("/proxy-profiles", response_model=list[ProxyProfileRead])
def get_proxy_profiles(db: Session = Depends(get_db)) -> list[ProxyProfileRead]:
    return [_proxy_profile_read(profile) for profile in list_proxy_profiles(db)]


@business_router.post("/proxy-profiles", response_model=ProxyProfileRead, status_code=201)
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
            country_code=payload.country_code,
            max_concurrent_runs=payload.max_concurrent_runs,
            is_active=payload.is_active,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _proxy_profile_read(profile)


@business_router.patch("/proxy-profiles/{profile_id}", response_model=ProxyProfileRead)
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
            country_code=payload.country_code,
            max_concurrent_runs=payload.max_concurrent_runs,
            is_active=payload.is_active,
            settings=settings,
        )
    except ProxyProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _proxy_profile_read(profile)


@business_router.post("/proxy-profiles/{profile_id}/test", response_model=ProxyProfileRead)
def post_proxy_profile_test(profile_id: int, db: Session = Depends(get_db)) -> ProxyProfileRead:
    profile = next((entry for entry in list_proxy_profiles(db) if entry.id == profile_id), None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Proxy profile {profile_id} does not exist")
    try:
        proxy_url = proxy_url_for_profile(profile, settings)
        proxy_dict = {"https": proxy_url, "http": proxy_url} if proxy_url else None
        browser_profile = profile_for_impersonate(settings.curl_impersonate_browser)
        with CurlSession(impersonate=browser_profile.impersonate, proxies=proxy_dict) as client:
            response = client.get("https://api.ipify.org?format=json", timeout=10)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            ip = response.json().get("ip")
        updated = mark_proxy_test_result(db, profile_id, status="success", ip=str(ip) if ip else None)
    except Exception as exc:
        updated = mark_proxy_test_result(db, profile_id, status="failed", error=str(exc))
    return _proxy_profile_read(updated)


@business_router.post("/proxy-profiles/{profile_id}/vinted-session/preflight", status_code=410)
def post_proxy_profile_vinted_session_preflight(profile_id: int) -> None:
    del profile_id
    raise HTTPException(
        status_code=410,
        detail="Las sesiones Vinted son propiedad del monitor; usa Preparar sesion o inicia un monitor para prepararlas",
    )


@business_router.post("/proxy-profiles/{profile_id}/catalog-api/probe", status_code=410)
def post_proxy_profile_catalog_api_probe(profile_id: int) -> None:
    del profile_id
    raise HTTPException(
        status_code=410,
        detail="El probe de catalogo por proxy se elimino; el probe real ocurre dentro de la preparacion de sesion del monitor",
    )


@business_router.post("/proxy-profiles/{profile_id}/vinted-session/import", status_code=410)
def post_proxy_profile_vinted_session_import(profile_id: int) -> None:
    del profile_id
    raise HTTPException(
        status_code=410,
        detail="La importacion de sesiones Vinted por proxy se elimino antes de produccion; las sesiones se preparan desde el monitor",
    )


@business_router.get("/opportunities", response_model=OpportunityResultPageRead)
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


@business_router.get("/runs", response_model=list[RunRead])
def get_runs(limit: int = 50, source_id: int | None = None, db: Session = Depends(get_db)) -> list:
    return list_runs(db, limit=limit, source_id=source_id)


@business_router.get("/monitors/{monitor_id}/stats", response_model=MonitorStatsRead)
def get_monitor_stats_endpoint(monitor_id: int, range: str = "hours", db: Session = Depends(get_db)):  # noqa: A002
    try:
        return get_monitor_stats(db, monitor_id, range_name=range)
    except MonitorStatsNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MonitorStatsRangeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@business_router.get("/runs/{run_id}/events", response_model=list[RunEventRead])
def get_run_events(run_id: int, db: Session = Depends(get_db)) -> list:
    return list_run_events(db, run_id)


@business_router.get("/monitors/{monitor_id}/events", response_model=list[RunEventRead])
def get_monitor_events(monitor_id: int, db: Session = Depends(get_db)) -> list:
    return list(db.scalars(select(RunEvent).where(RunEvent.source_id == monitor_id).order_by(RunEvent.created_at.asc(), RunEvent.id.asc())))


@business_router.get("/monitors/events/stream")
async def stream_monitor_events(
    request: Request,
    last_event_id: Annotated[int | None, Query(ge=0)] = None,
    last_event_id_header: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
):
    initial_cursor = await asyncio.to_thread(resolve_monitor_event_cursor, last_event_id, last_event_id_header)
    session_token_hash = getattr(getattr(request, "state", None), "local_session_token_hash", None)

    async def stream_should_close() -> bool:
        if await request.is_disconnected():
            return True
        if session_token_hash is None:
            # Direct unit calls do not run FastAPI dependencies. Every routed
            # request receives this state from require_api_access.
            return False
        return not await asyncio.to_thread(local_session_hash_is_active_in_database, session_token_hash)

    return StreamingResponse(
        monitor_event_stream(initial_cursor, is_disconnected=stream_should_close),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@business_router.post("/monitors/{monitor_id}/runs", response_model=RunRead, status_code=201)
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
    except SearchSourceInactiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SchedulerCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchSourceConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (BaselineRequiredError, SeenCacheUnavailableError, VintedSessionRequiredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@business_router.post("/monitors/{monitor_id}/vinted-session/prepare", response_model=RunRead, status_code=201)
def post_monitor_vinted_session_prepare(
    monitor_id: int,
    db: Session = Depends(get_db),
):
    try:
        return execute_monitor_session_prepare(db, monitor_id)
    except SearchSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SchedulerCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VintedSessionRequiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchSourceConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@business_router.post("/monitors/{monitor_id}/items/detail-probe", response_model=ItemDetailProbeRead, status_code=201)
def post_monitor_item_detail_probe(
    monitor_id: int,
    payload: ItemDetailProbeCreate,
    db: Session = Depends(get_db),
):
    try:
        run, result = execute_monitor_item_detail_probe(db, monitor_id, item_ref=payload.item_ref)
        return ItemDetailProbeRead(run=RunRead.model_validate(run), result=result)
    except SearchSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SchedulerCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VintedSessionRequiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchSourceConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@business_router.post("/actions", response_model=ActionRequestRead, status_code=201)
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


@business_router.api_route(
    "/{unmatched_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def reject_unknown_api_route(unmatched_path: str) -> None:
    del unmatched_path
    raise HTTPException(status_code=404, detail="Not found")


app.include_router(business_router)
