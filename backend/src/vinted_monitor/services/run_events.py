from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.core.redaction import (
    MARKER_ONLY_KEYS,
    SENSITIVE_HEADER_TOKENS,
    SafeSecretMarker,
    is_safe_secret_marker,
    redact_sensitive_text,
    sensitive_value_requires_redaction,
)
from vinted_monitor.db.models import RunEvent

MAX_MESSAGE_LENGTH = 1200
LOG_LEVELS = {"debug", "info", "warning", "error"}
_timestamp_lock = Lock()
_last_event_timestamp: datetime | None = None


def record_run_event(
    db: Session,
    *,
    phase: str,
    run_id: int | None = None,
    source_id: int | None = None,
    method: str | None = None,
    url: str | None = None,
    status_code: int | None = None,
    duration_ms: int | None = None,
    level: str | None = None,
    proxy_profile_id: int | None = None,
    egress_ip: str | None = None,
    user_agent: str | None = None,
    auth_mode: str | None = None,
    message: str | None = None,
    details: dict | None = None,
) -> RunEvent:
    event = RunEvent(
        run_id=run_id,
        source_id=source_id,
        phase=phase,
        level=_event_level(phase, level),
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
        created_at=_event_timestamp(),
    )
    db.add(event)
    db.flush()
    return event


def list_run_events(db: Session, run_id: int) -> list[RunEvent]:
    return list(db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.created_at.asc(), RunEvent.id.asc())))


def redact_run_event_details(details: dict[str, Any] | None) -> dict[str, Any]:
    return _redacted_details(details or {})


def redact_persisted_run_event_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Redact a DB-loaded event while restoring only strict markers sanitized before persistence."""
    return _redacted_details(_restore_persisted_markers(details or {}))


def _event_timestamp() -> datetime:
    global _last_event_timestamp
    with _timestamp_lock:
        current = datetime.now(UTC)
        if _last_event_timestamp is not None and current <= _last_event_timestamp:
            current = _last_event_timestamp + timedelta(microseconds=1)
        _last_event_timestamp = current
        return current


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
    return _redact_value(details)


def _restore_persisted_markers(value: Any, *, key: str | None = None) -> Any:
    lowered_key = key.lower() if key is not None else None
    if lowered_key in MARKER_ONLY_KEYS:
        if isinstance(value, dict):
            candidate = SafeSecretMarker(value)
            return candidate if is_safe_secret_marker(candidate) else value
        if isinstance(value, list):
            candidates = [SafeSecretMarker(child) if isinstance(child, dict) else child for child in value]
            if all(is_safe_secret_marker(candidate) for candidate in candidates):
                return candidates
            return value
    if isinstance(value, dict):
        return {child_key: _restore_persisted_markers(child_value, key=str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_restore_persisted_markers(child_value) for child_value in value]
    return value


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if key is not None and sensitive_value_requires_redaction(key, value):
        return "<redacted>"
    if isinstance(value, str):
        return redact_sensitive_text(value)[:MAX_MESSAGE_LENGTH]
    if isinstance(value, dict):
        return {child_key: _redact_value(child_value, key=str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact_value(child_value) for child_value in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"masked", "fingerprint"}:
        return False
    return any(token in lowered for token in SENSITIVE_HEADER_TOKENS)


def _event_level(phase: str, level: str | None) -> str:
    if level is not None:
        normalized = level.lower()
        return normalized if normalized in LOG_LEVELS else "info"
    lowered = phase.lower()
    if any(token in lowered for token in ["failed", "failure", "error"]):
        return "error"
    if any(token in lowered for token in ["rejected", "discarded", "skipped", "unavailable", "blocked"]):
        return "warning"
    return "info"
