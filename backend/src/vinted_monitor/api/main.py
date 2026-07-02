from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from vinted_monitor.api.schemas import (
    ActionRequestCreate,
    ActionRequestRead,
    ItemRead,
    RunRead,
    SearchSourceCreate,
    SearchSourceRead,
)
from vinted_monitor.core.config import get_settings
from vinted_monitor.core.logging import configure_logging
from vinted_monitor.db.session import get_db
from vinted_monitor.providers.vinted_catalog import HttpVintedCatalogProvider
from vinted_monitor.services.actions import create_action_request
from vinted_monitor.services.items import list_items
from vinted_monitor.services.runs import (
    ManualRunProvider,
    SearchSourceInactiveError,
    SearchSourceNotFoundError,
    execute_manual_run,
    list_runs,
)
from vinted_monitor.services.search_sources import create_source, list_sources

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


@app.get("/api/items", response_model=list[ItemRead])
def get_items(limit: int = 100, db: Session = Depends(get_db)) -> list:
    return list_items(db, limit=limit)


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
