from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from vinted_monitor.api.schemas import LocalAuthLogin, LocalAuthSessionRead, LocalAuthUserRead
from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.local_auth import (
    LOCAL_CSRF_HEADER_NAME,
    LOCAL_SESSION_COOKIE_NAME,
    LOCAL_SESSION_COOKIE_PATH,
    LocalAuthenticationRequiredError,
    LocalCredentialsError,
    LocalCsrfError,
    LocalOriginError,
    LocalSessionGrant,
    authenticate_local_session,
    bootstrap_local_session,
    csrf_token_for_session,
    local_session_cookie_secure,
    local_session_hash_is_active,
    login_local_user,
    require_trusted_origin,
    require_valid_csrf,
    revoke_local_session,
)

UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
auth_router = APIRouter(prefix="/api/auth", tags=["local-auth"])


def require_login_request_boundary(request: Request) -> None:
    settings = get_settings()
    try:
        require_trusted_origin(request.headers.get("origin"), settings)
        require_valid_csrf(
            request.cookies.get(LOCAL_SESSION_COOKIE_NAME),
            request.headers.get(LOCAL_CSRF_HEADER_NAME),
            settings,
        )
    except (LocalOriginError, LocalCsrfError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def require_api_access(request: Request) -> None:
    settings = get_settings()
    raw_token = request.cookies.get(LOCAL_SESSION_COOKIE_NAME)
    try:
        with SessionLocal() as db:
            principal = authenticate_local_session(db, raw_token=raw_token)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if request.method in UNSAFE_HTTP_METHODS:
        try:
            require_trusted_origin(request.headers.get("origin"), settings)
            require_valid_csrf(raw_token, request.headers.get(LOCAL_CSRF_HEADER_NAME), settings)
        except (LocalOriginError, LocalCsrfError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    request.state.local_user_id = principal.user_id
    request.state.local_user_email = principal.email
    request.state.local_session_token_hash = principal.token_hash


def local_session_hash_is_active_in_database(token_hash: str) -> bool:
    try:
        with SessionLocal() as db:
            return local_session_hash_is_active(db, token_hash)
    except SQLAlchemyError:
        return False


@auth_router.get("/session", response_model=LocalAuthSessionRead)
def get_local_auth_session(request: Request, response: Response) -> LocalAuthSessionRead:
    settings = get_settings()
    try:
        with SessionLocal() as db:
            grant = bootstrap_local_session(
                db,
                raw_token=request.cookies.get(LOCAL_SESSION_COOKIE_NAME),
                settings=settings,
            )
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc
    if grant.issued:
        _set_local_session_cookie(response, grant, settings)
    response.headers["Cache-Control"] = "no-store"
    return _session_read(grant, settings)


@auth_router.post(
    "/login",
    response_model=LocalAuthSessionRead,
    dependencies=[Depends(require_login_request_boundary)],
)
def post_local_auth_login(payload: LocalAuthLogin, request: Request, response: Response) -> LocalAuthSessionRead:
    settings = get_settings()
    try:
        require_trusted_origin(request.headers.get("origin"), settings)
        with SessionLocal() as db:
            grant = login_local_user(
                db,
                raw_token=request.cookies.get(LOCAL_SESSION_COOKIE_NAME),
                csrf_token=request.headers.get(LOCAL_CSRF_HEADER_NAME),
                email=payload.email,
                password=payload.password.get_secret_value(),
                settings=settings,
            )
    except (LocalOriginError, LocalCsrfError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (LocalAuthenticationRequiredError, LocalCredentialsError) as exc:
        raise HTTPException(status_code=401, detail="Invalid email or password") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc
    _set_local_session_cookie(response, grant, settings)
    response.headers["Cache-Control"] = "no-store"
    return _session_read(grant, settings)


@auth_router.post("/logout", status_code=204, dependencies=[Depends(require_api_access)])
def post_local_auth_logout(request: Request) -> Response:
    settings = get_settings()
    try:
        with SessionLocal() as db:
            revoke_local_session(db, raw_token=request.cookies.get(LOCAL_SESSION_COOKIE_NAME))
    except LocalAuthenticationRequiredError as exc:
        raise HTTPException(status_code=401, detail="Authentication required") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        LOCAL_SESSION_COOKIE_NAME,
        path=LOCAL_SESSION_COOKIE_PATH,
        secure=local_session_cookie_secure(settings),
        httponly=True,
        samesite="strict",
    )
    return response


def _session_read(grant: LocalSessionGrant, settings: Settings) -> LocalAuthSessionRead:
    principal = grant.principal
    return LocalAuthSessionRead(
        authenticated=principal is not None,
        user=LocalAuthUserRead(id=principal.user_id, email=principal.email) if principal is not None else None,
        csrf_token=csrf_token_for_session(grant.raw_token, settings),
        expires_at=grant.expires_at,
    )


def _set_local_session_cookie(response: Response, grant: LocalSessionGrant, settings: Settings) -> None:
    max_age = max(int((grant.expires_at - datetime.now(UTC)).total_seconds()), 0)
    response.set_cookie(
        LOCAL_SESSION_COOKIE_NAME,
        grant.raw_token,
        max_age=max_age,
        expires=grant.expires_at,
        path=LOCAL_SESSION_COOKIE_PATH,
        secure=local_session_cookie_secure(settings),
        httponly=True,
        samesite="strict",
    )
