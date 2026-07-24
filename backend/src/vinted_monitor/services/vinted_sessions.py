from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.crypto import decrypt_text, encrypt_text, fingerprint_text
from vinted_monitor.core.redaction import redact_sensitive_text, safe_secret_marker
from vinted_monitor.db.models import ProxyProfile, SearchSource, VintedSession
from vinted_monitor.providers.browser_profiles import BrowserProfile, profile_for_impersonate
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession
from vinted_monitor.services.proxies import ProxyProfileEligibilityError, effective_proxy_identity_generation

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
    "cf_bm",
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
    proxy_name: str
    usable_now: bool
    unusable_reason: str | None
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
    egress_validated_at: datetime | None
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


@dataclass(frozen=True)
class VintedSessionEligibility:
    session: VintedSession
    usable_now: bool
    unusable_reason: str | None
    prepared: PreparedCatalogSession | None
    context: dict[str, bool]
    missing_context: tuple[str, ...] = ()


def generate_proxy_session_id() -> str:
    return uuid.uuid4().hex


def list_vinted_session_summaries_for_source(
    db: Session,
    source_id: int,
    settings: Settings | None = None,
    *,
    now: datetime | None = None,
    require_datadome: bool = True,
) -> list[VintedSessionSummary]:
    resolved_settings = settings or get_settings()
    current_time = now or datetime.now(UTC)
    profile = profile_for_impersonate(resolved_settings.curl_impersonate_browser)
    rows = db.execute(
        select(ProxyProfile, VintedSession)
        .join(VintedSession, VintedSession.proxy_profile_id == ProxyProfile.id)
        .where(VintedSession.source_id == source_id)
        .order_by(ProxyProfile.id.asc(), VintedSession.id.asc())
    ).all()
    sessions_by_proxy: dict[int, tuple[ProxyProfile, list[VintedSession]]] = {}
    for proxy_profile, session in rows:
        grouped = sessions_by_proxy.setdefault(proxy_profile.id, (proxy_profile, []))
        grouped[1].append(session)

    summaries: list[VintedSessionSummary] = []
    for proxy_profile, sessions in sessions_by_proxy.values():
        try:
            current_generation = effective_proxy_identity_generation(proxy_profile)
        except ProxyProfileEligibilityError:
            current_generation = None
        eligibility = resolve_vinted_session_eligibility(
            sessions,
            source_id=source_id,
            proxy_profile=proxy_profile,
            current_generation=current_generation,
            profile=profile,
            settings=resolved_settings,
            now=current_time,
            require_datadome=require_datadome,
        )
        if eligibility is not None:
            summaries.append(
                summarize_vinted_session(
                    eligibility,
                    proxy_name=proxy_profile.name,
                )
            )
    return summaries


def resolve_vinted_session_eligibility(
    sessions: list[VintedSession],
    *,
    source_id: int,
    proxy_profile: ProxyProfile,
    current_generation: str | None,
    profile: BrowserProfile,
    settings: Settings,
    now: datetime | None = None,
    require_datadome: bool = True,
) -> VintedSessionEligibility | None:
    if not sessions:
        return None
    current_time = now or datetime.now(UTC)
    metadata_candidates = [
        session
        for session in sessions
        if _vinted_session_metadata_unusable_reason(
            session,
            source_id=source_id,
            proxy_profile=proxy_profile,
            current_generation=current_generation,
            profile=profile,
            now=current_time,
        )
        is None
    ]
    if metadata_candidates:
        selected = min(metadata_candidates, key=_vinted_session_lru_key)
    else:
        selected = max(sessions, key=_vinted_session_diagnostic_key)

    metadata_reason = _vinted_session_metadata_unusable_reason(
        selected,
        source_id=source_id,
        proxy_profile=proxy_profile,
        current_generation=current_generation,
        profile=profile,
        now=current_time,
    )
    if metadata_reason is not None:
        return VintedSessionEligibility(
            session=selected,
            usable_now=False,
            unusable_reason=metadata_reason,
            prepared=None,
            context=_empty_prepared_context_flags(),
        )

    try:
        prepared = prepared_context_from_session(selected, settings)
    except VintedSessionImportError:
        return VintedSessionEligibility(
            session=selected,
            usable_now=False,
            unusable_reason="context_unreadable",
            prepared=None,
            context=_empty_prepared_context_flags(),
        )
    context_flags = prepared_context_flags(prepared)
    missing = tuple(missing_prepared_context(prepared, require_datadome=require_datadome))
    if missing:
        return VintedSessionEligibility(
            session=selected,
            usable_now=False,
            unusable_reason="context_incomplete",
            prepared=prepared,
            context=context_flags,
            missing_context=missing,
        )
    return VintedSessionEligibility(
        session=selected,
        usable_now=True,
        unusable_reason=None,
        prepared=prepared,
        context=context_flags,
    )


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
    _lock_live_source_for_session_write(db, source.id)
    current_generation = effective_proxy_identity_generation(proxy_profile)
    stale_sessions = list(
        db.scalars(
            select(VintedSession)
            .where(
                VintedSession.source_id == source.id,
                VintedSession.proxy_profile_id == proxy_profile.id,
                VintedSession.proxy_identity_generation != current_generation,
                VintedSession.status != INVALID,
            )
            .with_for_update()
        )
    )
    for stale_session in stale_sessions:
        mark_vinted_session_invalid(
            db,
            stale_session.id,
            reason="Prepared Vinted session proxy identity changed",
            settings=settings,
        )
    sessions = list(
        db.scalars(
            select(VintedSession).where(
                VintedSession.source_id == source.id,
                VintedSession.proxy_profile_id == proxy_profile.id,
            )
        )
    )
    eligibility = resolve_vinted_session_eligibility(
        sessions,
        source_id=source.id,
        proxy_profile=proxy_profile,
        current_generation=current_generation,
        profile=profile,
        settings=settings,
        now=current_time,
        require_datadome=require_datadome,
    )
    if eligibility is None or eligibility.unusable_reason not in {None, "context_incomplete", "context_unreadable"}:
        raise VintedSessionRequiredError(
            f"No hay sesion Vinted usable para el monitor {source.id} con el proxy {proxy_profile.name}"
        )
    session = eligibility.session
    if eligibility.unusable_reason == "context_unreadable":
        raise VintedSessionImportError("Prepared Vinted session context is unreadable")
    if eligibility.unusable_reason == "context_incomplete":
        missing = eligibility.missing_context
        mark_vinted_session_invalid(
            db,
            session.id,
            reason=f"Prepared Vinted session missing context: {', '.join(missing)}",
            settings=settings,
        )
        raise VintedSessionRequiredError(
            f"La sesion Vinted preparada esta incompleta ({', '.join(missing)}); prepara una sesion nueva"
        )
    prepared = eligibility.prepared
    if prepared is None:
        raise VintedSessionImportError("Prepared Vinted session context is unreadable")
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
    last_error: str | None = None,
    require_datadome: bool = True,
) -> VintedSession:
    locked_source = _lock_live_source_for_session_write(db, source.id)
    settings = settings or get_settings()
    now = datetime.now(UTC)
    context_payload = context_to_encrypted_payload(context)
    missing = missing_prepared_context(context, require_datadome=require_datadome)
    proxy_identity_generation = effective_proxy_identity_generation(proxy_profile)
    resolved_status = status
    resolved_error = last_error
    if missing and status == READY:
        resolved_status = INCOMPLETE
        resolved_error = f"Prepared Vinted session missing context: {', '.join(missing)}"
    session = VintedSession(
        source_id=locked_source.id,
        proxy_profile_id=proxy_profile.id,
        proxy_identity_generation=proxy_identity_generation,
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
        egress_validated_at=context.egress_validated_at,
        context_encrypted=encrypt_text(json.dumps(context_payload, sort_keys=True, separators=(",", ":")), settings.app_secret_key),
        context_fingerprint=fingerprint_text(json.dumps(context_payload, sort_keys=True, separators=(",", ":"))),
        request_count=0,
        max_requests=max_requests or settings.vinted_session_max_requests,
        failure_count=0,
        prepared_at=now,
        expires_at=now + timedelta(
            minutes=_effective_vinted_session_ttl_minutes(settings, proxy_profile)
        ),
        last_error=redact_sensitive_text(resolved_error) if resolved_error else None,
    )
    db.add(session)
    db.flush()
    return session


def mark_vinted_session_used(db: Session, session: VintedSession, *, now: datetime | None = None) -> None:
    session.request_count = (session.request_count or 0) + 1
    session.last_used_at = now or datetime.now(UTC)
    db.flush()


def mark_vinted_session_invalid(
    db: Session,
    session_id: int | None,
    *,
    reason: str,
    settings: Settings | None = None,
) -> None:
    if session_id is None:
        return
    session = db.get(VintedSession, session_id)
    if session is None:
        return
    if session.status != INVALID:
        session.failure_count = (session.failure_count or 0) + 1
    session.status = INVALID
    session.invalidated_at = datetime.now(UTC)
    session.egress_validated_at = None
    session.last_error = redact_sensitive_text(reason)
    session.context_encrypted = encrypt_text("{}", (settings or get_settings()).app_secret_key)
    db.flush()


def invalidate_vinted_sessions_for_source(
    db: Session,
    source_id: int,
    *,
    reason: str,
    settings: Settings | None = None,
) -> int:
    sessions = list(db.scalars(select(VintedSession).where(VintedSession.source_id == source_id)))
    resolved_settings = settings or get_settings()
    for session in sessions:
        mark_vinted_session_invalid(db, session.id, reason=reason, settings=resolved_settings)
    return len(sessions)


def invalidate_vinted_sessions_for_proxy_identity(
    db: Session,
    proxy_profile_id: int,
    *,
    reason: str,
    settings: Settings | None = None,
) -> int:
    """Purge all contexts for a proxy while locking affected monitors in stable order."""
    source_ids = list(
        db.scalars(
            select(VintedSession.source_id)
            .where(
                VintedSession.proxy_profile_id == proxy_profile_id,
                VintedSession.status != INVALID,
            )
            .distinct()
            .order_by(VintedSession.source_id.asc())
        )
    )
    if not source_ids:
        return 0
    list(
        db.scalars(
            select(SearchSource)
            .where(SearchSource.id.in_(source_ids))
            .order_by(SearchSource.id.asc())
            # Session identity invalidation does not change the source key.
            # NO KEY UPDATE still excludes session writers/archive while remaining
            # compatible with FK key-share locks from pre-fence run events.
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    )
    sessions = list(
        db.scalars(
            select(VintedSession)
            .where(
                VintedSession.proxy_profile_id == proxy_profile_id,
                VintedSession.status != INVALID,
            )
            .order_by(VintedSession.source_id.asc(), VintedSession.id.asc())
            .with_for_update()
        )
    )
    resolved_settings = settings or get_settings()
    for session in sessions:
        mark_vinted_session_invalid(db, session.id, reason=reason, settings=resolved_settings)
    return len(sessions)


def update_vinted_session_context(
    db: Session,
    session_id: int | None,
    *,
    context: PreparedCatalogSession,
    settings: Settings | None = None,
    require_datadome: bool = True,
    last_error: str | None = None,
) -> VintedSession | None:
    if session_id is None:
        return None
    session = db.get(VintedSession, session_id)
    if session is None:
        return None
    proxy_profile = db.get(ProxyProfile, session.proxy_profile_id)
    if proxy_profile is None:
        raise VintedSessionRequiredError(
            f"Prepared Vinted session {session.id} references a missing proxy profile"
        )
    _lock_live_source_for_session_write(db, session.source_id)
    settings = settings or get_settings()
    now = datetime.now(UTC)
    context_payload = context_to_encrypted_payload(context)
    context_json = json.dumps(context_payload, sort_keys=True, separators=(",", ":"))
    missing = missing_prepared_context(context, require_datadome=require_datadome)
    session.context_encrypted = encrypt_text(context_json, settings.app_secret_key)
    session.context_fingerprint = fingerprint_text(context_json)
    session.egress_ip = context.egress_ip
    session.egress_country_code = context.egress_country_code
    session.egress_validated_at = context.egress_validated_at
    session.status = READY if not missing else INCOMPLETE
    session.prepared_at = now
    session.expires_at = now + timedelta(
        minutes=_effective_vinted_session_ttl_minutes(settings, proxy_profile)
    )
    session.invalidated_at = None if not missing else session.invalidated_at
    if missing:
        session.last_error = redact_sensitive_text(f"Prepared Vinted session missing context: {', '.join(missing)}")
    elif last_error:
        session.last_error = redact_sensitive_text(last_error)
    else:
        session.last_error = None
    db.flush()
    return session


def _effective_vinted_session_ttl_minutes(
    settings: Settings,
    proxy_profile: ProxyProfile,
) -> int:
    sticky_ttl_minutes = proxy_profile.sticky_ttl_minutes
    if (
        isinstance(sticky_ttl_minutes, bool)
        or not isinstance(sticky_ttl_minutes, int)
        or not 1 <= sticky_ttl_minutes <= 120
    ):
        raise ProxyProfileEligibilityError(
            f"Proxy profile {proxy_profile.id} has an invalid sticky TTL"
        )
    return min(settings.vinted_session_ttl_minutes, sticky_ttl_minutes)


def _lock_live_source_for_session_write(db: Session, source_id: int) -> SearchSource:
    source = db.scalar(
        select(SearchSource)
        .where(SearchSource.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None or source.archived_at is not None:
        raise VintedSessionRequiredError(f"Search source {source_id} is archived")
    return source


def _vinted_session_metadata_unusable_reason(
    session: VintedSession,
    *,
    source_id: int,
    proxy_profile: ProxyProfile,
    current_generation: str | None,
    profile: BrowserProfile,
    now: datetime,
) -> str | None:
    if session.status == INCOMPLETE:
        return "status_incomplete"
    if session.status == INVALID:
        return "status_invalid"
    if session.status != READY:
        return "status_unrecognized"
    if (
        session.source_id != source_id
        or session.proxy_profile_id != proxy_profile.id
        or current_generation is None
        or session.proxy_identity_generation != current_generation
    ):
        return "proxy_identity_mismatch"
    if session.browser_profile != profile.name or session.impersonate != profile.impersonate:
        return "browser_profile_mismatch"
    if (
        session.country_code != proxy_profile.country_code
        or session.locale != proxy_profile.locale
        or session.accept_language != proxy_profile.accept_language
        or session.viewport_size != proxy_profile.screen
        or session.vinted_screen != proxy_profile.vinted_screen
    ):
        return "request_context_mismatch"
    if session.expires_at is None or _as_utc(session.expires_at) <= _as_utc(now):
        return "expired"
    if (session.request_count or 0) >= (session.max_requests or 0):
        return "exhausted"
    return None


def _vinted_session_lru_key(session: VintedSession) -> tuple[bool, datetime, datetime, int]:
    prepared_at = _as_utc(session.prepared_at)
    last_used_at = _as_utc(session.last_used_at) if session.last_used_at is not None else prepared_at
    return (session.last_used_at is not None, last_used_at, prepared_at, session.id)


def _vinted_session_diagnostic_key(session: VintedSession) -> tuple[datetime, int]:
    return (_as_utc(session.created_at or session.prepared_at), session.id)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _empty_prepared_context_flags() -> dict[str, bool]:
    return {key: False for key in REQUIRED_CONTEXT_FLAGS}


def summarize_vinted_session(
    eligibility: VintedSessionEligibility,
    *,
    proxy_name: str,
) -> VintedSessionSummary:
    session = eligibility.session
    return VintedSessionSummary(
        id=session.id,
        source_id=session.source_id,
        proxy_profile_id=session.proxy_profile_id,
        proxy_name=proxy_name,
        usable_now=eligibility.usable_now,
        unusable_reason=eligibility.unusable_reason,
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
        egress_validated_at=session.egress_validated_at,
        proxy_session=safe_secret_marker("proxy_sticky_session_id", session.proxy_session_id, kind="proxy_session"),
        request_count=session.request_count,
        max_requests=session.max_requests,
        failure_count=session.failure_count,
        prepared_at=session.prepared_at,
        expires_at=session.expires_at,
        last_used_at=session.last_used_at,
        invalidated_at=session.invalidated_at,
        last_error=redact_sensitive_text(session.last_error) if session.last_error else None,
        context=eligibility.context,
    )


def prepared_context_from_session(session: VintedSession, settings: Settings) -> PreparedCatalogSession:
    try:
        raw = decrypt_text(session.context_encrypted, settings.app_secret_key)
        payload = json.loads(raw)
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VintedSessionImportError("Prepared Vinted session context is unreadable") from exc
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
        cf_bm=_optional_payload_string(payload.get("cf_bm")),
        v_udt=_optional_payload_string(payload.get("v_udt")),
        user_iso_locale=_optional_payload_string(payload.get("user_iso_locale")),
        vinted_screen=_optional_payload_string(payload.get("vinted_screen")),
        egress_ip=session.egress_ip,
        egress_country_code=session.egress_country_code,
        egress_validated_at=session.egress_validated_at,
    )


def context_to_encrypted_payload(context: PreparedCatalogSession) -> dict[str, Any]:
    cookies = context.cookies or {}
    datadome = context.datadome or cookies.get("datadome")
    cf_bm = context.cf_bm or cookies.get("__cf_bm")
    access_token = context.access_token_web or cookies.get("access_token_web")
    v_udt = context.v_udt or cookies.get("v_udt")
    anon_id = context.anon_id or cookies.get("anon_id")
    return {
        "cookies": {str(key): str(value) for key, value in cookies.items() if value},
        "csrf_token": context.csrf_token,
        "anon_id": anon_id,
        "access_token_web": access_token,
        "datadome": datadome,
        "cf_bm": cf_bm,
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
        "cf_bm": bool(payload.get("cf_bm")),
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
