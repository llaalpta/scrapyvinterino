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


class SafeSecretMarker(dict[str, Any]):
    """Runtime-branded mapping created only after a raw secret has been reduced."""


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
    normalized = value or ""
    return SafeSecretMarker(
        {
            "kind": kind,
            "name": name,
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
        domain = getattr(cookie, "domain", None)
        expires = getattr(cookie, "expires", None)
        if domain:
            marker["domain"] = str(domain)
        if expires:
            marker["expires"] = str(expires)
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
            safe[name] = safe_secret_marker(name, text_value, kind="header")
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
        marker = safe_secret_marker(name, morsel.value, kind=kind)
        if morsel["domain"]:
            marker["domain"] = morsel["domain"]
        if morsel["expires"]:
            marker["expires"] = morsel["expires"]
        markers.append(marker)
    return markers or [safe_secret_marker(kind, header_value, kind=kind)]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in SENSITIVE_HEADER_TOKENS)


def is_safe_secret_marker(value: Any) -> bool:
    if not isinstance(value, SafeSecretMarker):
        return False
    required = {"kind", "name", "masked", "length", "fingerprint"}
    allowed = required | {"domain", "expires"}
    if not required.issubset(value) or not set(value).issubset(allowed):
        return False
    masked = value.get("masked")
    fingerprint = value.get("fingerprint")
    return bool(
        isinstance(value.get("kind"), str)
        and isinstance(value.get("name"), str)
        and isinstance(value.get("length"), int)
        and not isinstance(value.get("length"), bool)
        and isinstance(masked, str)
        and (masked in {"<empty>", "<masked>"} or re.fullmatch(r".{4}\*{4}.{4}", masked))
        and isinstance(fingerprint, str)
        and re.fullmatch(r"sha256:(?:empty|[0-9a-f]{12})", fingerprint)
        and all(isinstance(value.get(optional), str) for optional in {"domain", "expires"} if optional in value)
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
