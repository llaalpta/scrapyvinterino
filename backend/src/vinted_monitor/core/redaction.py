from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from http.cookies import SimpleCookie
from typing import Any

SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"\b(access_token_web|anon_id|authorization|cookie|csrf(?:_token)?|datadome|ddk|jspl|password|refresh_token|secret|set-cookie|token|v_udt)(\s*[:=]\s*)([^\r\n;,&]+)",
    re.IGNORECASE,
)
QUOTED_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<prefix>[\"'](?:access_token_web|anon_id|authorization|cookie|csrf(?:_token)?|datadome|ddk|jspl|password|refresh_token|secret|set-cookie|token|v_udt)[\"']\s*:\s*)"
    r"(?P<quote>[\"'])(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
BEARER_TOKEN_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
BASIC_AUTH_PATTERN = re.compile(r"\bBasic\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
URL_USERINFO_PATTERN = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^\s/]+)@([^\s/]+)", re.IGNORECASE)
SENSITIVE_HEADER_TOKENS = (
    "anon-id",
    "authorization",
    "cookie",
    "csrf",
    "datadome",
    "ddk",
    "jspl",
    "password",
    "secret",
    "token",
    "v-udt",
)
MARKER_ONLY_KEYS = frozenset(
    {
        "cookies_after",
        "cookies_before",
        "cookies_before_close",
        "http_session",
        "proxy_session",
        "proxy_sticky_session",
        "session_markers",
        "vinted_http_session",
    }
)
SANITIZED_HEADER_CONTAINER_KEYS = frozenset({"request_headers", "response_headers"})
SENSITIVE_CONTENT_KEYS = frozenset({"body_snippet", "html", "response_body"})
SAFE_SECRET_MARKER_KINDS = frozenset(
    {"cookie", "header", "http_session", "proxy_session", "secret", "session", "session_secret", "set-cookie"}
)
SAFE_SECRET_MARKER_NAMES = frozenset(
    {
        "__cf_bm",
        "access_token_web",
        "anon_id",
        "authorization",
        "cookie",
        "csrf_token",
        "datadome",
        "ddk",
        "http_session",
        "http_session_id",
        "jspl",
        "password",
        "proxy_session",
        "proxy_sticky_session_id",
        "refresh_token",
        "secret",
        "set-cookie",
        "token",
        "v_udt",
        "x-anon-id",
        "x-csrf-token",
        "x-v-udt",
    }
)
MAX_MARKER_SECRET_LENGTH = 1_000_000
_SAFE_MARKER_FACTORY_TOKEN = object()
_REDACTED_MARKER_NAME_PATTERN = re.compile(r"<redacted-name:sha256:[0-9a-f]{12}>")


class SafeSecretMarker(dict[str, Any]):
    """Immutable runtime-branded mapping created only by this module's factory."""

    def __init__(self, value: Mapping[str, Any], *, _factory_token: object | None = None) -> None:
        if _factory_token is not _SAFE_MARKER_FACTORY_TOKEN:
            raise TypeError("SafeSecretMarker values must be created by safe_secret_marker")
        dict.__init__(self, value)

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("SafeSecretMarker values are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def redact_sensitive_text(value: str) -> str:
    redacted = URL_USERINFO_PATTERN.sub(lambda match: f"{match.group(1)}<redacted>:<redacted>@{match.group(4)}", value)
    redacted = BEARER_TOKEN_PATTERN.sub("Bearer <redacted>", redacted)
    redacted = BASIC_AUTH_PATTERN.sub("Basic <redacted>", redacted)
    redacted = QUOTED_SENSITIVE_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}<redacted>{match.group('quote')}",
        redacted,
    )
    return SENSITIVE_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", redacted)


def redact_sensitive_value(value: Any, *, key: str | None = None) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if key is not None and sensitive_value_requires_redaction(key, value):
        return "<redacted>"
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {str(child_key): redact_sensitive_value(child_value, key=str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_value(child_value) for child_value in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(child_value) for child_value in value)
    return redact_sensitive_text(str(value))


def safe_secret_marker(name: str, value: str, *, kind: str = "session") -> SafeSecretMarker:
    normalized = str(value or "")
    normalized_kind = str(kind).strip().lower()
    safe_kind = normalized_kind if normalized_kind in SAFE_SECRET_MARKER_KINDS else "secret"
    return _new_safe_secret_marker(
        {
            "kind": safe_kind,
            "name": _safe_marker_name(name),
            "masked": mask_secret(normalized),
            "length": len(normalized),
            "fingerprint": fingerprint_secret(normalized),
        }
    )


def mask_secret(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) < 12:
        return "<masked>"
    return f"{value[:4]}****{value[-4:]}"


def fingerprint_secret(value: str) -> str:
    if not value:
        return "sha256:empty"
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def safe_cookie_markers(cookies: Any) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for cookie in _iter_cookies(cookies):
        name = str(getattr(cookie, "name", "") or "")
        value = str(getattr(cookie, "value", "") or "")
        if not name:
            continue
        marker = safe_secret_marker(name, value, kind="cookie")
        markers.append(marker)
    return markers


def _iter_cookies(cookies: Any) -> Iterable[Any]:
    if isinstance(cookies, dict):
        return [
            type("CookieMarker", (), {"name": str(name), "value": str(value)})()
            for name, value in cookies.items()
        ]
    jar = getattr(cookies, "jar", cookies)
    return list(jar) if jar is not None else []


def safe_headers(headers: Mapping[str, Any] | None) -> dict[str, Any]:
    if not headers:
        return {}
    safe: dict[str, Any] = {}
    for key, value in headers.items():
        name = str(key)
        text_value = str(value)
        lowered = name.lower()
        if lowered in {"cookie", "set-cookie"}:
            safe[name] = safe_cookie_header_markers(text_value, kind=lowered)
        elif _is_sensitive_key(name):
            marker = safe_secret_marker(name, text_value, kind="header")
            safe[marker["name"]] = marker
        else:
            safe[name] = redact_sensitive_text(text_value)[:1200]
    return safe


def safe_cookie_header_markers(header_value: str, *, kind: str = "cookie") -> list[dict[str, Any]]:
    parsed = SimpleCookie()
    try:
        parsed.load(header_value)
    except Exception:
        return [safe_secret_marker(kind, header_value, kind=kind)]
    markers: list[dict[str, Any]] = []
    for name, morsel in parsed.items():
        markers.append(safe_secret_marker(name, morsel.value, kind=kind))
    return markers or [safe_secret_marker(kind, header_value, kind=kind)]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_HEADER_TOKENS)


def is_safe_secret_marker(value: Any) -> bool:
    return isinstance(value, SafeSecretMarker) and _is_safe_secret_marker_mapping(value)


def restore_persisted_safe_secret_marker(value: Any) -> SafeSecretMarker | None:
    if not isinstance(value, Mapping) or not _is_safe_secret_marker_mapping(value):
        return None
    return _new_safe_secret_marker(value)


def _new_safe_secret_marker(value: Mapping[str, Any]) -> SafeSecretMarker:
    return SafeSecretMarker(value, _factory_token=_SAFE_MARKER_FACTORY_TOKEN)


def _is_safe_secret_marker_mapping(value: Mapping[str, Any]) -> bool:
    required = {"kind", "name", "masked", "length", "fingerprint"}
    if set(value) != required:
        return False
    kind = value.get("kind")
    name = value.get("name")
    length = value.get("length")
    masked = value.get("masked")
    fingerprint = value.get("fingerprint")
    if not (
        isinstance(kind, str)
        and kind in SAFE_SECRET_MARKER_KINDS
        and isinstance(name, str)
        and _is_safe_marker_name(name)
        and isinstance(length, int)
        and not isinstance(length, bool)
        and 0 <= length <= MAX_MARKER_SECRET_LENGTH
        and isinstance(masked, str)
        and isinstance(fingerprint, str)
    ):
        return False
    if length == 0:
        return masked == "<empty>" and fingerprint == "sha256:empty"
    if not re.fullmatch(r"sha256:[0-9a-f]{12}", fingerprint):
        return False
    if length < 12:
        return masked == "<masked>"
    return bool(re.fullmatch(r".{4}\*{4}.{4}", masked))


def _safe_marker_name(value: Any) -> str:
    normalized = str(value or "").strip()
    if normalized.lower() in SAFE_SECRET_MARKER_NAMES:
        return normalized
    return f"<redacted-name:{fingerprint_secret(normalized)}>"


def _is_safe_marker_name(value: str) -> bool:
    return bool(
        value.lower() in SAFE_SECRET_MARKER_NAMES
        or _REDACTED_MARKER_NAME_PATTERN.fullmatch(value)
    )


def is_safe_secret_marker_collection(value: Any) -> bool:
    return isinstance(value, list | tuple) and all(is_safe_secret_marker(child) for child in value)


def sensitive_value_requires_redaction(key: str, value: Any) -> bool:
    lowered = key.lower()
    if lowered in SENSITIVE_CONTENT_KEYS:
        return True
    safe_marker_value = is_safe_secret_marker(value) or is_safe_secret_marker_collection(value)
    if lowered in MARKER_ONLY_KEYS:
        return not safe_marker_value
    if lowered in SANITIZED_HEADER_CONTAINER_KEYS:
        return not isinstance(value, Mapping)
    return _is_sensitive_key(key) and not safe_marker_value
