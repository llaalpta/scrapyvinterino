from __future__ import annotations

import base64
import json
import random
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urljoin, urlsplit

from vinted_monitor.core.redaction import redact_sensitive_text, safe_headers
from vinted_monitor.providers.browser_profiles import BrowserProfile

DATADOME_CHALLENGE_MARKERS = [
    "geo.captcha-delivery.com",
    "interstitial",
    "dd.js",
    "t.datadome.co",
    "datadome.co/captcha",
]

DATADOME_TAGS_VERSION_PATTERN = re.compile(r"/datadome/([0-9]+(?:\.[0-9]+)*)/tags\.js", re.IGNORECASE)
DATADOME_SCRIPT_PATTERN = re.compile(r"""<script[^>]+src=["']([^"']*/datadome/[^"']*/tags\.js[^"']*)["']""", re.IGNORECASE)
DATADOME_CLIENT_KEY_PATTERNS = (
    re.compile(r"""(?:ddjskey|ddk|datadomeKey|clientKey)["']?\s*[:=]\s*["']([A-Za-z0-9_-]{12,})["']""", re.IGNORECASE),
    re.compile(r"""["']ddk["']\s*,\s*["']([A-Za-z0-9_-]{12,})["']""", re.IGNORECASE),
    re.compile(r'\\"(?:ddjskey|ddk|datadomeKey|clientKey)\\"\s*[:=]\s*\\"([A-Za-z0-9_-]{12,})\\"', re.IGNORECASE),
)


class DataDomeChallengeError(RuntimeError):
    """Raised when DataDome serves a challenge instead of real content."""


@dataclass(frozen=True)
class DataDomeCollectorAttempt:
    js_type: str
    status_code: int | None = None
    duration_ms: int | None = None
    cookie_found: bool = False
    error: str | None = None
    response_keys: list[str] = field(default_factory=list)

    def safe_details(self) -> dict[str, Any]:
        return {
            "js_type": self.js_type,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "cookie_found": self.cookie_found,
            "error": self.error,
            "response_keys": self.response_keys,
        }


@dataclass(frozen=True)
class DataDomeCollectorResult:
    success: bool
    datadome_cookie: str | None
    attempts: list[DataDomeCollectorAttempt]
    ddv: str
    ddk_found: bool
    ddk_length: int | None = None
    jspl_length: int | None = None
    error: str | None = None

    def safe_details(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "post_sent": bool(self.attempts),
            "attempts": [attempt.safe_details() for attempt in self.attempts],
            "ddv": self.ddv,
            "ddk_found": self.ddk_found,
            "ddk_length": self.ddk_length,
            "jspl_length": self.jspl_length,
            "datadome_cookie": bool(self.datadome_cookie),
            "error": self.error,
        }


class DataDomeCookieCollector:
    """Best-effort DataDome JS endpoint collector for preflight only.

    The collector never exposes raw cookies, tokens, DataDome keys, or the JS
    signal payload through its result. It is intentionally conservative: a
    missing cookie is a failed preflight, not a reason to call Vinted catalog.
    """

    def __init__(
        self,
        *,
        session: Any,
        profile: BrowserProfile,
        collector_url: str,
        source_url: str,
        page_html: str,
        accept_language: str,
        locale: str,
        viewport_size: str,
        vinted_screen: str,
        timeout_seconds: float,
        default_ddv: str,
        configured_client_key: str | None = None,
        event_sink: Callable[..., None] | None = None,
    ) -> None:
        self.session = session
        self.profile = profile
        self.collector_url = collector_url
        self.source_url = source_url
        self.page_html = page_html or ""
        self.accept_language = accept_language
        self.locale = locale
        self.viewport_size = viewport_size
        self.vinted_screen = vinted_screen
        self.timeout_seconds = timeout_seconds
        self.default_ddv = default_ddv
        self.configured_client_key = configured_client_key.strip() if configured_client_key else None
        self.event_sink = event_sink

    def collect(self) -> DataDomeCollectorResult:
        ddv = extract_datadome_tags_version(self.page_html) or self.default_ddv
        ddk = self.configured_client_key or extract_datadome_client_key(self.page_html)
        if not ddk:
            return DataDomeCollectorResult(
                success=False,
                datadome_cookie=None,
                attempts=[],
                ddv=ddv,
                ddk_found=False,
                error="datadome_client_key_missing",
            )

        attempts: list[DataDomeCollectorAttempt] = []
        cid = self._current_datadome_cookie() or ".keep"
        jspl_length: int | None = None
        collected_cookie: str | None = None
        for js_type in ("ch", "le"):
            cid = self._current_datadome_cookie() or cid or ".keep"
            payload = build_datadome_collector_payload(
                source_url=self.source_url,
                profile=self.profile,
                accept_language=self.accept_language,
                locale=self.locale,
                viewport_size=self.viewport_size,
                vinted_screen=self.vinted_screen,
                ddk=ddk,
                ddv=ddv,
                js_type=js_type,
                cid=cid,
            )
            jspl_length = len(payload["jspl"])
            headers = build_datadome_collector_headers(
                source_url=self.source_url,
                profile=self.profile,
                accept_language=self.accept_language,
            )
            self._emit_attempt_event(
                "datadome_collector_attempt_start",
                js_type=js_type,
                details={
                    "js_type": js_type,
                    "ddv": ddv,
                    "ddk_found": True,
                    "ddk_length": len(ddk),
                    "jspl_length": jspl_length,
                    "payload_keys": sorted(payload.keys()),
                    "request_headers": safe_headers(headers),
                    "default_headers": False,
                },
            )
            started_at = time.perf_counter()
            try:
                response = self.session.post(
                    self.collector_url,
                    data=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    default_headers=False,
                )
                response_payload = _response_json(response)
                cookie_value = extract_datadome_cookie_from_response_cookie(_optional_str(response_payload.get("cookie")))
                duration_ms = _elapsed_ms(started_at)
                if cookie_value:
                    _set_session_cookie(self.session, "datadome", cookie_value, source_url=self.source_url)
                    self._emit_attempt_event(
                        "datadome_collector_attempt_success",
                        js_type=js_type,
                        status_code=response.status_code,
                        duration_ms=duration_ms,
                        details={
                            "js_type": js_type,
                            "ddv": ddv,
                            "ddk_found": True,
                            "ddk_length": len(ddk),
                            "jspl_length": jspl_length,
                            "cookie_found": True,
                            "response_keys": _safe_response_keys(response_payload),
                            "response_headers": safe_headers(dict(response.headers)),
                        },
                    )
                    attempts.append(
                        DataDomeCollectorAttempt(
                            js_type=js_type,
                            status_code=response.status_code,
                            duration_ms=duration_ms,
                            cookie_found=True,
                            response_keys=_safe_response_keys(response_payload),
                        )
                    )
                    collected_cookie = cookie_value
                    continue
                cid = _optional_str(response_payload.get("cid")) or cid
                self._emit_attempt_event(
                    "datadome_collector_attempt_failed",
                    js_type=js_type,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    details={
                        "js_type": js_type,
                        "ddv": ddv,
                        "ddk_found": True,
                        "ddk_length": len(ddk),
                        "jspl_length": jspl_length,
                        "cookie_found": False,
                        "response_keys": _safe_response_keys(response_payload),
                        "response_headers": safe_headers(dict(response.headers)),
                    },
                )
                attempts.append(
                    DataDomeCollectorAttempt(
                        js_type=js_type,
                        status_code=response.status_code,
                        duration_ms=duration_ms,
                        cookie_found=False,
                        response_keys=_safe_response_keys(response_payload),
                    )
                )
            except Exception as exc:
                duration_ms = _elapsed_ms(started_at)
                safe_error = redact_sensitive_text(str(exc))
                self._emit_attempt_event(
                    "datadome_collector_attempt_failed",
                    js_type=js_type,
                    duration_ms=duration_ms,
                    level="warning",
                    message=safe_error,
                    details={
                        "js_type": js_type,
                        "ddv": ddv,
                        "ddk_found": True,
                        "ddk_length": len(ddk),
                        "jspl_length": jspl_length,
                        "cookie_found": False,
                        "error": safe_error,
                    },
                )
                attempts.append(
                    DataDomeCollectorAttempt(
                        js_type=js_type,
                        duration_ms=duration_ms,
                        cookie_found=False,
                        error=safe_error,
                    )
                )

        if collected_cookie:
            return DataDomeCollectorResult(
                success=True,
                datadome_cookie=collected_cookie,
                attempts=attempts,
                ddv=ddv,
                ddk_found=True,
                ddk_length=len(ddk),
                jspl_length=jspl_length,
            )

        return DataDomeCollectorResult(
            success=False,
            datadome_cookie=None,
            attempts=attempts,
            ddv=ddv,
            ddk_found=True,
            ddk_length=len(ddk),
            jspl_length=jspl_length,
            error="datadome_cookie_not_returned",
        )

    def _current_datadome_cookie(self) -> str | None:
        cookies = getattr(self.session, "cookies", None)
        if not cookies:
            return None
        get_value = getattr(cookies, "get", None)
        if callable(get_value):
            try:
                value = get_value("datadome")
            except Exception:
                value = None
            if value:
                return str(value)
        try:
            return extract_datadome_cookie_value(dict(cookies))
        except Exception:
            return None

    def _emit_attempt_event(
        self,
        phase: str,
        *,
        js_type: str,
        status_code: int | None = None,
        duration_ms: int | None = None,
        level: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.event_sink is None:
            return
        self.event_sink(
            phase=phase,
            method="POST",
            url=self.collector_url,
            status_code=status_code,
            duration_ms=duration_ms,
            level=level,
            message=message,
            details={"js_type": js_type, **(details or {})},
        )


def extract_datadome_tags_version(html: str) -> str | None:
    match = DATADOME_TAGS_VERSION_PATTERN.search(html or "")
    return match.group(1) if match else None


def extract_datadome_script_url(html: str, source_url: str) -> str | None:
    match = DATADOME_SCRIPT_PATTERN.search(html or "")
    if not match:
        return None
    return urljoin(source_url, match.group(1))


def extract_datadome_client_key(html: str) -> str | None:
    for pattern in DATADOME_CLIENT_KEY_PATTERNS:
        match = pattern.search(html or "")
        if match:
            return match.group(1)
    return None


def extract_datadome_cookie_from_response_cookie(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    parsed = SimpleCookie()
    try:
        parsed.load(cookie_header)
    except Exception:
        return None
    morsel = parsed.get("datadome")
    if morsel and morsel.value:
        return morsel.value
    return None


def build_datadome_collector_headers(*, source_url: str, profile: BrowserProfile, accept_language: str) -> OrderedDict[str, str]:
    origin = _origin(source_url)
    return OrderedDict(
        [
            ("accept", "*/*"),
            ("accept-encoding", "gzip, deflate, br, zstd"),
            ("accept-language", accept_language),
            ("cache-control", "no-cache"),
            ("content-type", "application/x-www-form-urlencoded"),
            ("origin", origin),
            ("pragma", "no-cache"),
            ("priority", "u=1, i"),
            ("referer", f"{origin}/"),
            ("sec-ch-ua", profile.sec_ch_ua),
            ("sec-ch-ua-mobile", profile.sec_ch_ua_mobile),
            ("sec-ch-ua-platform", profile.sec_ch_ua_platform),
            ("sec-fetch-dest", "empty"),
            ("sec-fetch-mode", "cors"),
            ("sec-fetch-site", "cross-site"),
            ("user-agent", profile.user_agent),
        ]
    )


def build_datadome_tags_headers(*, source_url: str, profile: BrowserProfile, accept_language: str) -> OrderedDict[str, str]:
    origin = _origin(source_url)
    return OrderedDict(
        [
            ("accept", "*/*"),
            ("accept-encoding", "gzip, deflate, br, zstd"),
            ("accept-language", accept_language),
            ("cache-control", "no-cache"),
            ("pragma", "no-cache"),
            ("referer", f"{origin}/"),
            ("sec-ch-ua", profile.sec_ch_ua),
            ("sec-ch-ua-mobile", profile.sec_ch_ua_mobile),
            ("sec-ch-ua-platform", profile.sec_ch_ua_platform),
            ("sec-fetch-dest", "script"),
            ("sec-fetch-mode", "no-cors"),
            ("sec-fetch-site", "cross-site"),
            ("sec-fetch-storage-access", "active"),
            ("user-agent", profile.user_agent),
        ]
    )


def build_datadome_collector_payload(
    *,
    source_url: str,
    profile: BrowserProfile,
    accept_language: str,
    locale: str,
    viewport_size: str,
    vinted_screen: str,
    ddk: str,
    ddv: str,
    js_type: str,
    cid: str,
) -> dict[str, str]:
    return {
        "jspl": _build_jspl(
            source_url=source_url,
            profile=profile,
            accept_language=accept_language,
            locale=locale,
            viewport_size=viewport_size,
            vinted_screen=vinted_screen,
            js_type=js_type,
        ),
        "eventCounters": _event_counters(js_type),
        "jsType": js_type,
        "cid": cid,
        "ddk": ddk,
        "Referer": source_url,
        "request": _request_path(source_url),
        "responsePage": "origin",
        "ddv": ddv,
    }


def is_datadome_challenge(status_code: int, headers: dict[str, str], body_snippet: str) -> bool:
    """Detect whether the response is a DataDome challenge rather than real content.

    Args:
        status_code: HTTP status code of the response.
        headers: Response headers (case-insensitive dict recommended).
        body_snippet: First ~3000 characters of the response body.

    Returns:
        True if the response appears to be a DataDome challenge.
    """
    server = _header_value(headers, "server")
    if server and "datadome" in server.lower():
        return True

    if _has_datadome_response_header(headers):
        return True

    if status_code >= 400 and _has_datadome_set_cookie(headers):
        return True

    content_type = _header_value(headers, "content-type")
    if content_type and "text/html" in content_type.lower():
        lower_snippet = body_snippet.lower()
        return any(marker in lower_snippet for marker in DATADOME_CHALLENGE_MARKERS)

    return False


def extract_datadome_cookie_value(cookies: dict[str, str]) -> str | None:
    """Extract the ``datadome`` cookie value from a cookie dict.

    Returns None if the cookie is not present.
    """
    return cookies.get("datadome")


def has_datadome_cookie(cookies: dict[str, str]) -> bool:
    """Check whether the datadome cookie is present and non-empty."""
    value = extract_datadome_cookie_value(cookies)
    return bool(value)


def human_delay(
    min_seconds: float = 1.2,
    max_seconds: float = 3.8,
    rng: random.Random | None = None,
) -> float:
    """Sleep for a human-like duration between requests.

    Uses a Beta distribution skewed toward the lower-center range to
    simulate realistic page-load wait times rather than a uniform
    distribution.

    Returns the actual delay applied in seconds.
    """
    generator = rng or random.Random()
    # Beta(2, 5) produces values skewed toward the lower end of [0,1]
    # with mean ~0.286, which maps to a natural 1.2-2.5s center.
    normalized = generator.betavariate(2, 5)
    delay = normalized * (max_seconds - min_seconds) + min_seconds
    time.sleep(delay)
    return delay


def _header_value(headers: dict[str, str], key: str) -> str | None:
    """Case-insensitive header lookup."""
    lower_key = key.lower()
    for header_key, value in headers.items():
        if header_key.lower() == lower_key:
            if isinstance(value, (list, tuple)):
                return ", ".join(str(item) for item in value)
            return str(value)
    return None


def _has_datadome_response_header(headers: dict[str, str]) -> bool:
    for header_key in headers:
        if str(header_key).lower().startswith("x-datadome"):
            return True
    return False


def _has_datadome_set_cookie(headers: dict[str, str]) -> bool:
    for header_key, value in headers.items():
        if str(header_key).lower() != "set-cookie":
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for cookie_header in values:
            if extract_datadome_cookie_from_response_cookie(str(cookie_header)):
                return True
    return False


def _build_jspl(
    *,
    source_url: str,
    profile: BrowserProfile,
    accept_language: str,
    locale: str,
    viewport_size: str,
    vinted_screen: str,
    js_type: str,
) -> str:
    width, height = _parse_viewport(viewport_size)
    payload = {
        "url": source_url,
        "origin": _origin(source_url),
        "userAgent": profile.user_agent,
        "platform": "Win32",
        "language": locale,
        "languages": _languages_from_accept_language(accept_language),
        "screen": {"width": width, "height": height, "availWidth": width, "availHeight": max(height - 40, 0)},
        "viewport": {"width": width, "height": height},
        "vintedScreen": vinted_screen,
        "jsType": js_type,
        "timestamp": round(time.time() * 1000),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _event_counters(js_type: str) -> str:
    if js_type == "le":
        return json.dumps(
            {
                "mousemove": 26,
                "pointermove": 26,
                "click": 0,
                "scroll": 0,
                "touchstart": 0,
                "touchend": 0,
                "touchmove": 0,
                "keydown": 0,
                "keyup": 0,
            },
            separators=(",", ":"),
        )
    return "[]"


def _request_path(source_url: str) -> str:
    parsed = urlsplit(source_url)
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def _origin(source_url: str) -> str:
    parsed = urlsplit(source_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_viewport(viewport_size: str) -> tuple[int, int]:
    width, _, height = (viewport_size or "1920x1080").lower().partition("x")
    try:
        return int(width), int(height)
    except ValueError:
        return 1920, 1080


def _languages_from_accept_language(accept_language: str) -> list[str]:
    languages: list[str] = []
    for chunk in accept_language.split(","):
        language = chunk.split(";", 1)[0].strip()
        if language:
            languages.append(language)
    return languages or ["es-ES", "es", "en"]


def _response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        try:
            payload = json.loads(getattr(response, "text", "") or "{}")
        except json.JSONDecodeError:
            payload = {}
    return payload if isinstance(payload, dict) else {}


def _safe_response_keys(payload: dict[str, Any]) -> list[str]:
    return sorted(key for key in payload if key not in {"cookie", "token", "ddk", "jspl"})


def _set_session_cookie(session: Any, name: str, value: str, *, source_url: str) -> None:
    cookies = getattr(session, "cookies", None)
    if cookies is None:
        return
    domain = urlsplit(source_url).hostname
    set_value = getattr(cookies, "set", None)
    if callable(set_value):
        try:
            set_value(name, value, domain=domain, path="/")
            return
        except TypeError:
            try:
                set_value(name, value)
                return
            except Exception:
                pass
        except Exception:
            pass
    try:
        cookies.update({name: value})
    except Exception:
        try:
            cookies[name] = value
        except Exception:
            pass


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _elapsed_ms(started_at: float) -> int:
    return max(round((time.perf_counter() - started_at) * 1000), 0)
