from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.crypto import decrypt_text, encrypt_text, fingerprint_text
from vinted_monitor.core.redaction import redact_sensitive_text, safe_secret_marker
from vinted_monitor.db.models import ProxyProfile, SearchSource, VintedSession
from vinted_monitor.providers.browser_profiles import BrowserProfile, profile_for_impersonate
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession

READY = "ready"
INVALID = "invalid"
INCOMPLETE = "incomplete"
DEFAULT_MAX_REQUESTS = 50
DEFAULT_TTL_MINUTES = 120
REQUIRED_CONTEXT_FLAGS = (
    "csrf_token",
    "anon_id",
    "access_token_web",
    "datadome",
    "v_udt",
    "user_iso_locale",
    "vinted_screen",
)


class VintedSessionRequiredError(ValueError):
    pass


class VintedSessionImportError(ValueError):
    pass


@dataclass(frozen=True)
class VintedSessionSummary:
    id: int
    source_id: int
    proxy_profile_id: int
    status: str
    browser_profile: str
    impersonate: str
    country_code: str
    locale: str
    accept_language: str
    viewport_size: str
    vinted_screen: str
    egress_ip: str | None
    egress_country_code: str | None
    proxy_session: dict[str, Any] | None
    request_count: int
    max_requests: int
    failure_count: int
    prepared_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    invalidated_at: datetime | None
    last_error: str | None
    context: dict[str, bool]


def generate_proxy_session_id() -> str:
    return uuid.uuid4().hex


def get_latest_vinted_session_summary(
    db: Session,
    proxy_profile_id: int,
    settings: Settings | None = None,
    *,
    source_id: int | None = None,
) -> VintedSessionSummary | None:
    statement = select(VintedSession).where(VintedSession.proxy_profile_id == proxy_profile_id)
    if source_id is not None:
        statement = statement.where(VintedSession.source_id == source_id)
    session = db.scalar(statement.order_by(VintedSession.created_at.desc(), VintedSession.id.desc()).limit(1))
    if session is None:
        return None
    return summarize_vinted_session(session, settings=settings)


def get_ready_vinted_session(
    db: Session,
    source: SearchSource,
    proxy_profile: ProxyProfile,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    require_datadome: bool = True,
) -> tuple[VintedSession, PreparedCatalogSession]:
    settings = settings or get_settings()
    current_time = now or datetime.now(UTC)
    profile = profile_for_impersonate(settings.curl_impersonate_browser)
    statement = (
        select(VintedSession)
        .where(
            VintedSession.source_id == source.id,
            VintedSession.proxy_profile_id == proxy_profile.id,
            VintedSession.status == READY,
            VintedSession.browser_profile == profile.name,
            VintedSession.impersonate == profile.impersonate,
            VintedSession.country_code == proxy_profile.country_code,
            VintedSession.locale == proxy_profile.locale,
            VintedSession.accept_language == proxy_profile.accept_language,
            VintedSession.vinted_screen == proxy_profile.vinted_screen,
            (VintedSession.expires_at.is_(None) | (VintedSession.expires_at > current_time)),
            VintedSession.request_count < VintedSession.max_requests,
        )
        .order_by(VintedSession.last_used_at.asc().nullsfirst(), VintedSession.prepared_at.asc(), VintedSession.id.asc())
        .limit(1)
    )
    session = db.scalar(statement)
    if session is None:
        raise VintedSessionRequiredError(
            f"No hay sesion Vinted usable para el monitor {source.id} con el proxy {proxy_profile.name}"
        )
    prepared = prepared_context_from_session(session, settings)
    missing = missing_prepared_context(prepared, require_datadome=require_datadome)
    if missing:
        mark_vinted_session_invalid(db, session.id, reason=f"Prepared Vinted session missing context: {', '.join(missing)}")
        raise VintedSessionRequiredError(
            f"La sesion Vinted preparada esta incompleta ({', '.join(missing)}); prepara una sesion nueva"
        )
    mark_vinted_session_used(db, session, now=current_time)
    return session, prepared


def save_prepared_vinted_session(
    db: Session,
    source: SearchSource,
    proxy_profile: ProxyProfile,
    *,
    proxy_session_id: str,
    profile: BrowserProfile,
    context: PreparedCatalogSession,
    status: str = READY,
    settings: Settings | None = None,
    max_requests: int | None = None,
    ttl_minutes: int | None = None,
    last_error: str | None = None,
    require_datadome: bool = True,
) -> VintedSession:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    context_payload = context_to_encrypted_payload(context)
    missing = missing_prepared_context(context, require_datadome=require_datadome)
    resolved_status = status
    resolved_error = last_error
    if missing and status == READY:
        resolved_status = INCOMPLETE
        resolved_error = f"Prepared Vinted session missing context: {', '.join(missing)}"
    session = VintedSession(
        source_id=source.id,
        proxy_profile_id=proxy_profile.id,
        proxy_session_id=proxy_session_id.strip(),
        status=resolved_status,
        browser_profile=profile.name,
        impersonate=profile.impersonate,
        country_code=proxy_profile.country_code,
        locale=proxy_profile.locale,
        accept_language=proxy_profile.accept_language,
        viewport_size=proxy_profile.screen,
        vinted_screen=proxy_profile.vinted_screen,
        egress_ip=context.egress_ip,
        egress_country_code=context.egress_country_code,
        context_encrypted=encrypt_text(json.dumps(context_payload, sort_keys=True, separators=(",", ":")), settings.app_secret_key),
        context_fingerprint=fingerprint_text(json.dumps(context_payload, sort_keys=True, separators=(",", ":"))),
        request_count=0,
        max_requests=max_requests or settings.vinted_session_max_requests,
        failure_count=0,
        prepared_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes or settings.vinted_session_ttl_minutes),
        last_error=redact_sensitive_text(resolved_error) if resolved_error else None,
    )
    db.add(session)
    db.flush()
    return session


def mark_vinted_session_used(db: Session, session: VintedSession, *, now: datetime | None = None) -> None:
    session.request_count = (session.request_count or 0) + 1
    session.last_used_at = now or datetime.now(UTC)
    db.flush()


def mark_vinted_session_invalid(db: Session, session_id: int | None, *, reason: str) -> None:
    if session_id is None:
        return
    session = db.get(VintedSession, session_id)
    if session is None:
        return
    session.status = INVALID
    session.failure_count = (session.failure_count or 0) + 1
    session.invalidated_at = datetime.now(UTC)
    session.last_error = redact_sensitive_text(reason)
    db.flush()


def summarize_vinted_session(session: VintedSession, settings: Settings | None = None) -> VintedSessionSummary:
    context_flags: dict[str, bool]
    try:
        prepared = prepared_context_from_session(session, settings or get_settings())
        context_flags = prepared_context_flags(prepared)
    except Exception:
        context_flags = {key: False for key in REQUIRED_CONTEXT_FLAGS}
    return VintedSessionSummary(
        id=session.id,
        source_id=session.source_id,
        proxy_profile_id=session.proxy_profile_id,
        status=session.status,
        browser_profile=session.browser_profile,
        impersonate=session.impersonate,
        country_code=session.country_code,
        locale=session.locale,
        accept_language=session.accept_language,
        viewport_size=session.viewport_size,
        vinted_screen=session.vinted_screen,
        egress_ip=session.egress_ip,
        egress_country_code=session.egress_country_code,
        proxy_session=safe_secret_marker("proxy_sticky_session_id", session.proxy_session_id, kind="proxy_session"),
        request_count=session.request_count,
        max_requests=session.max_requests,
        failure_count=session.failure_count,
        prepared_at=session.prepared_at,
        expires_at=session.expires_at,
        last_used_at=session.last_used_at,
        invalidated_at=session.invalidated_at,
        last_error=session.last_error,
        context=context_flags,
    )


def prepared_context_from_session(session: VintedSession, settings: Settings) -> PreparedCatalogSession:
    raw = decrypt_text(session.context_encrypted, settings.app_secret_key)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise VintedSessionImportError("Prepared Vinted session payload is invalid")
    cookies = payload.get("cookies")
    if not isinstance(cookies, dict):
        cookies = {}
    return PreparedCatalogSession(
        session_id=session.id,
        proxy_session_id=session.proxy_session_id,
        cookies={str(key): str(value) for key, value in cookies.items() if value},
        csrf_token=_optional_payload_string(payload.get("csrf_token")),
        anon_id=_optional_payload_string(payload.get("anon_id")),
        access_token_web=_optional_payload_string(payload.get("access_token_web")),
        datadome=_optional_payload_string(payload.get("datadome")),
        v_udt=_optional_payload_string(payload.get("v_udt")),
        user_iso_locale=_optional_payload_string(payload.get("user_iso_locale")),
        vinted_screen=_optional_payload_string(payload.get("vinted_screen")),
        egress_ip=session.egress_ip,
        egress_country_code=session.egress_country_code,
    )


def context_to_encrypted_payload(context: PreparedCatalogSession) -> dict[str, Any]:
    cookies = context.cookies or {}
    datadome = context.datadome or cookies.get("datadome")
    access_token = context.access_token_web or cookies.get("access_token_web")
    v_udt = context.v_udt or cookies.get("v_udt")
    anon_id = context.anon_id or cookies.get("anon_id")
    return {
        "cookies": {str(key): str(value) for key, value in cookies.items() if value},
        "csrf_token": context.csrf_token,
        "anon_id": anon_id,
        "access_token_web": access_token,
        "datadome": datadome,
        "v_udt": v_udt,
        "user_iso_locale": context.user_iso_locale,
        "vinted_screen": context.vinted_screen,
    }


def prepared_context_flags(context: PreparedCatalogSession) -> dict[str, bool]:
    payload = context_to_encrypted_payload(context)
    return {
        "csrf_token": bool(payload.get("csrf_token")),
        "anon_id": bool(payload.get("anon_id")),
        "access_token_web": bool(payload.get("access_token_web")),
        "datadome": bool(payload.get("datadome")),
        "v_udt": bool(payload.get("v_udt")),
        "user_iso_locale": bool(payload.get("user_iso_locale")),
        "vinted_screen": bool(payload.get("vinted_screen")),
    }


def missing_prepared_context(context: PreparedCatalogSession, *, require_datadome: bool = True) -> list[str]:
    flags = prepared_context_flags(context)
    return [name for name, ok in flags.items() if not ok and (require_datadome or name != "datadome")]


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in cookie_header.split(";"):
        name, separator, value = chunk.strip().partition("=")
        if separator and name and value:
            cookies[name] = value
    return cookies


def _optional_payload_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
