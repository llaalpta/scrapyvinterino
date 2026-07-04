from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"\b(access_token_web|authorization|cookie|csrf(?:_token)?|password|refresh_token|secret|set-cookie|token)(\s*[:=]\s*)([^\s;,&]+)",
    re.IGNORECASE,
)
BEARER_TOKEN_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
URL_USERINFO_PATTERN = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@([^\s/]+)", re.IGNORECASE)


def redact_sensitive_text(value: str) -> str:
    redacted = URL_USERINFO_PATTERN.sub(lambda match: f"{match.group(1)}<redacted>:<redacted>@{match.group(4)}", value)
    redacted = BEARER_TOKEN_PATTERN.sub("Bearer <redacted>", redacted)
    return SENSITIVE_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", redacted)


def safe_secret_marker(name: str, value: str, *, kind: str = "session") -> dict[str, Any]:
    normalized = value or ""
    return {
        "kind": kind,
        "name": name,
        "masked": mask_secret(normalized),
        "length": len(normalized),
        "fingerprint": fingerprint_secret(normalized),
    }


def mask_secret(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) < 10:
        return "<masked>"
    return f"{value[:3]}****{value[-3:]}"


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
    jar = getattr(cookies, "jar", cookies)
    return list(jar) if jar is not None else []
