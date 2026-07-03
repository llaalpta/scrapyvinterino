from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.core.redaction import redact_sensitive_text
from vinted_monitor.db.models import RunEvent

MAX_MESSAGE_LENGTH = 1200


def record_run_event(
    db: Session,
    *,
    phase: str,
    run_id: int | None = None,
    session_id: int | None = None,
    source_id: int | None = None,
    method: str | None = None,
    url: str | None = None,
    status_code: int | None = None,
    duration_ms: int | None = None,
    proxy_profile_id: int | None = None,
    egress_ip: str | None = None,
    user_agent: str | None = None,
    auth_mode: str | None = None,
    message: str | None = None,
    details: dict | None = None,
) -> RunEvent:
    event = RunEvent(
        run_id=run_id,
        session_id=session_id,
        source_id=source_id,
        phase=phase,
        method=method,
        url=sanitize_url(url) if url else None,
        status_code=status_code,
        duration_ms=duration_ms,
        proxy_profile_id=proxy_profile_id,
        egress_ip=egress_ip,
        user_agent=user_agent,
        auth_mode=auth_mode,
        message=_redacted_message(message),
        details=_redacted_details(details or {}),
    )
    db.add(event)
    db.flush()
    return event


def list_run_events(db: Session, run_id: int) -> list[RunEvent]:
    return list(db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.created_at.asc(), RunEvent.id.asc())))


def sanitize_url(url: str) -> str:
    redacted = redact_sensitive_text(url)
    parsed = urlsplit(redacted)
    if not parsed.query:
        return redacted

    safe_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_sensitive_key(key):
            safe_params.append((key, "<redacted>"))
        else:
            safe_params.append((key, value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(safe_params), parsed.fragment))


def _redacted_message(message: str | None) -> str | None:
    if message is None:
        return None
    return redact_sensitive_text(message)[:MAX_MESSAGE_LENGTH]


def _redacted_details(details: dict) -> dict:
    redacted: dict = {}
    for key, value in details.items():
        if _is_sensitive_key(str(key)):
            redacted[key] = "<redacted>"
        elif isinstance(value, str):
            redacted[key] = redact_sensitive_text(value)[:MAX_MESSAGE_LENGTH]
        else:
            redacted[key] = value
    return redacted


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ["token", "cookie", "password", "secret", "authorization", "csrf"])
