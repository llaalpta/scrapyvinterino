from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from curl_cffi.requests import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.redaction import safe_cookie_markers, safe_headers, safe_secret_marker
from vinted_monitor.providers.browser_profiles import BrowserProfile, profile_for_impersonate
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult
from vinted_monitor.providers.catalog_url import build_catalog_api_params
from vinted_monitor.providers.datadome import DataDomeChallengeError, has_datadome_cookie, human_delay, is_datadome_challenge

NEXT_FLIGHT_CHUNK_PATTERN = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)
JSON_LD_PATTERN = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
CSRF_TOKEN_PATTERNS = (
    re.compile(r'"CSRF_TOKEN"\s*:\s*"([^"]+)"'),
    re.compile(r'\\"CSRF_TOKEN\\"\s*:\s*\\"([^"\\]+)\\"'),
    re.compile(r'"csrfToken"\s*:\s*"([^"]+)"'),
    re.compile(r'\\"csrfToken\\"\s*:\s*\\"([^"\\]+)\\"'),
    re.compile(r'"X-CSRF-Token"\s*,\s*"([^"]+)"'),
    re.compile(r'\\"X-CSRF-Token\\"\s*,\s*\\"([^"\\]+)\\"'),
    re.compile(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
)


@dataclass
class CatalogSessionContext:
    csrf_token: str | None = None
    anon_id: str | None = None
    v_udt: str | None = None
    user_iso_locale: str | None = None
    screen: str | None = None


class VintedCatalogProviderError(RuntimeError):
    pass


class VintedCatalogSessionError(VintedCatalogProviderError):
    pass


class ProviderEventSink:
    def __call__(
        self,
        *,
        phase: str,
        method: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
        duration_ms: int | None = None,
        level: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        pass


class CurlCffiVintedCatalogProvider:
    """Vinted catalog provider using curl_cffi for TLS/JA3 fingerprint bypass.

    Each instance holds a single ``curl_cffi.requests.Session`` that is reused
    across the bootstrap and catalog API requests so that the same TCP
    connection, cookies, and proxy IP are shared for the full task lifecycle.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        profile: BrowserProfile | None = None,
        proxy_url: str | None = None,
        timeout_ms: int | None = None,
        catalog_per_page: int | None = None,
        request_retries: int = 1,
        event_sink: ProviderEventSink | None = None,
        human_delay_min: float = 1.2,
        human_delay_max: float = 3.8,
        session_factory: Callable[..., Any] | None = None,
        proxy_session_marker: dict[str, Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.profile = profile or profile_for_impersonate(self.settings.curl_impersonate_browser)
        self.proxy_url = proxy_url
        self.timeout_ms = timeout_ms or self.settings.vinted_request_timeout_ms
        self.catalog_per_page = catalog_per_page or self.settings.vinted_fast_catalog_per_page
        self.request_retries = max(request_retries, 0)
        self.event_sink = event_sink
        self.human_delay_min = human_delay_min
        self.human_delay_max = human_delay_max
        self.session_factory = session_factory or Session
        self.proxy_session_marker = proxy_session_marker
        self.http_session_id = str(uuid.uuid4())
        self._session: Session | None = None
        self._bootstrapped = False
        self._egress_diagnosed = False
        self._catalog_session_context = CatalogSessionContext()

    def search(self, source: Any, page: int | None = None) -> CatalogSearchResult:
        last_error: VintedCatalogProviderError | None = None
        for retry_index in range(self.request_retries + 1):
            self._ensure_session()
            self._diagnose_egress(attempt=retry_index + 1)
            if not self._bootstrapped:
                self._bootstrap_anonymous_session(source.url, attempt=retry_index + 1)

            try:
                response = self._request_catalog_api(source, page, attempt=retry_index + 1)
                return parse_catalog_api_payload(response, base_url=str(self.settings.vinted_base_url))
            except VintedCatalogSessionError:
                self._emit_event(
                    phase="anonymous_session_refresh_start",
                    method="GET",
                    url=source.url,
                    level="warning",
                    message="Catalog session was rejected; refreshing anonymous public session",
                    details={"attempt": retry_index + 2, "retry_reason": "session_rejected"},
                )
                self._reset_session()
                self._diagnose_egress(attempt=retry_index + 2)
                self._bootstrap_anonymous_session(source.url, attempt=retry_index + 2)
                response = self._request_catalog_api(source, page, attempt=retry_index + 2)
                return parse_catalog_api_payload(response, base_url=str(self.settings.vinted_base_url))
            except VintedCatalogProviderError as exc:
                last_error = exc
                if retry_index >= self.request_retries:
                    raise

        raise last_error or VintedCatalogProviderError("Vinted catalog API request failed")

    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        self._ensure_session()
        self._diagnose_egress(attempt=1)
        assert self._session is not None
        headers = self.profile.build_bootstrap_headers()
        self._emit_event(
            phase="detail_http_request_start",
            method="GET",
            url=candidate.url,
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "http_session": self._session_marker(),
                "request_headers": safe_headers(headers),
                "cookies_before": self._cookie_markers(),
            },
        )
        started_at = time.perf_counter()
        try:
            response = self._session.get(
                candidate.url,
                headers=dict(headers),
                timeout=self.timeout_ms / 1000,
            )
            response_details = {
                "vinted_item_id": candidate.vinted_item_id,
                "http_session": self._session_marker(),
                "request_headers": safe_headers(headers),
                "response_headers": safe_headers(dict(response.headers)),
                "cookies_after": self._cookie_markers(),
            }
            if is_datadome_challenge(response.status_code, dict(response.headers), response.text[:3000]):
                self._emit_event(
                    phase="detail_http_request_error",
                    method="GET",
                    url=candidate.url,
                    status_code=response.status_code,
                    duration_ms=_elapsed_ms(started_at),
                    level="warning",
                    message="DataDome challenge detected on item detail request",
                    details=response_details,
                )
                raise DataDomeChallengeError("DataDome challenge detected on item detail request")
            if response.status_code >= 400:
                self._emit_event(
                    phase="detail_http_request_error",
                    method="GET",
                    url=candidate.url,
                    status_code=response.status_code,
                    duration_ms=_elapsed_ms(started_at),
                    level="error",
                    message=f"HTTP {response.status_code}",
                    details=response_details,
                )
                raise VintedCatalogProviderError(
                    f"Vinted detail request failed for {candidate.vinted_item_id}: HTTP {response.status_code}"
                )
            self._emit_event(
                phase="detail_http_request_success",
                method="GET",
                url=candidate.url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                details=response_details,
            )
        except DataDomeChallengeError:
            raise
        except Exception as exc:
            if not isinstance(exc, VintedCatalogProviderError):
                self._emit_event(
                    phase="detail_http_request_error",
                    method="GET",
                    url=candidate.url,
                    duration_ms=_elapsed_ms(started_at),
                    level="error",
                    message=str(exc),
                    details={
                        "vinted_item_id": candidate.vinted_item_id,
                        "http_session": self._session_marker(),
                        "request_headers": safe_headers(headers),
                        "cookies_after": self._cookie_markers(),
                    },
                )
                raise VintedCatalogProviderError(
                    f"Vinted detail request failed for {candidate.vinted_item_id}: {exc}"
                ) from exc
            raise

        return parse_item_detail_html(response.text, candidate)

    def close(self) -> None:
        """Discard the session, cookies, and proxy connection."""
        if self._session is not None:
            self._emit_event(
                phase="http_session_closed",
                details={
                    "http_session": self._session_marker(),
                    "cookies_before_close": self._cookie_markers(),
                },
            )
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        self._bootstrapped = False
        self._egress_diagnosed = False
        self._catalog_session_context = CatalogSessionContext()

    def _ensure_session(self) -> None:
        if self._session is None:
            proxy_dict = {"https": self.proxy_url, "http": self.proxy_url} if self.proxy_url else None
            self._session = self.session_factory(
                impersonate=self.profile.impersonate,
                proxies=proxy_dict,
            )
            self._emit_event(
                phase="http_session_created",
                details={
                    "http_session": self._session_marker(),
                    "browser_profile": self.profile.name,
                    "impersonate": self.profile.impersonate,
                    "proxy_configured": bool(self.proxy_url),
                    "proxy_session": self.proxy_session_marker,
                },
            )

    def _reset_session(self) -> None:
        """Close and recreate the session (same proxy, fresh cookies)."""
        self.close()
        self._catalog_session_context = CatalogSessionContext()
        self._ensure_session()

    def _request_catalog_api(self, source: Any, page: int | None, *, attempt: int) -> dict[str, Any]:
        assert self._session is not None
        url = urljoin(str(self.settings.vinted_base_url), "/api/v2/catalog/items")
        params = build_catalog_api_params(source.url, page, self.catalog_per_page)
        headers = dict(self.profile.build_api_headers(referer=source.url))
        if self._catalog_session_context.anon_id:
            headers["X-Anon-Id"] = self._catalog_session_context.anon_id
        if self._catalog_session_context.csrf_token:
            headers["X-CSRF-Token"] = self._catalog_session_context.csrf_token

        cookie_names = list(self._session.cookies.keys()) if self._session.cookies else []
        self._emit_event(
            phase="catalog_api_request_start",
            method="GET",
            url=url,
            details={
                "page": params["page"],
                "per_page": params["per_page"],
                "order": params["order"],
                "api_params": params,
                "session_marker_count": len(cookie_names),
                "timeout_ms": self.timeout_ms,
                "attempt": attempt,
                "browser_profile": self.profile.name,
                "http_session": self._session_marker(),
                "csrf_token_found": self._catalog_session_context.csrf_token is not None,
                "anon_id_found": self._catalog_session_context.anon_id is not None,
                "csrf_token": _secret_marker_or_none("csrf_token", self._catalog_session_context.csrf_token),
                "anon_id": _secret_marker_or_none("anon_id", self._catalog_session_context.anon_id),
                "request_headers": safe_headers(headers),
                "cookies_before": self._cookie_markers(),
            },
        )
        started_at = time.perf_counter()
        try:
            response = self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout_ms / 1000,
            )
        except Exception as exc:
            self._emit_event(
                phase="catalog_api_request_error",
                method="GET",
                url=url,
                duration_ms=_elapsed_ms(started_at),
                level="error",
                message=str(exc),
                details={
                    "timeout_ms": self.timeout_ms,
                    "attempt": attempt,
                    "http_session": self._session_marker(),
                    "request_headers": safe_headers(headers),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise VintedCatalogProviderError(f"Vinted catalog API request failed: {exc}") from exc

        # DataDome challenge detection
        if is_datadome_challenge(response.status_code, dict(response.headers), response.text[:3000]):
            self._emit_event(
                phase="datadome_challenge_detected",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message="DataDome served a challenge instead of catalog data",
                details={
                    "attempt": attempt,
                    "browser_profile": self.profile.name,
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise DataDomeChallengeError("DataDome challenge detected on catalog API request")

        if response.status_code in {401, 403}:
            self._emit_event(
                phase="catalog_api_session_rejected",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message=f"Catalog API rejected anonymous session with status {response.status_code}",
                details={
                    "attempt": attempt,
                    "retryable": attempt == 1,
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise VintedCatalogSessionError(f"Vinted catalog API session rejected with status {response.status_code}")

        if response.status_code >= 400:
            self._emit_event(
                phase="catalog_api_request_error",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="error",
                message=f"HTTP {response.status_code}",
                details={
                    "attempt": attempt,
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise VintedCatalogProviderError(f"Vinted catalog API request failed: HTTP {response.status_code}")

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            self._emit_event(
                phase="catalog_api_session_rejected",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message="Catalog API returned a non-JSON response",
                details={
                    "content_type": content_type,
                    "attempt": attempt,
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise VintedCatalogSessionError("Vinted catalog API returned a non-JSON response")

        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            self._emit_event(
                phase="catalog_api_parse_error",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="error",
                message="Catalog API response did not contain items",
                details={
                    "attempt": attempt,
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise VintedCatalogProviderError("Vinted catalog API response did not contain items")

        self._emit_event(
            phase="catalog_api_request_success",
            method="GET",
            url=url,
            status_code=response.status_code,
            duration_ms=_elapsed_ms(started_at),
            details={
                "item_count": len(payload.get("items", [])),
                "content_type": content_type,
                "attempt": attempt,
                "browser_profile": self.profile.name,
                "http_session": self._session_marker(),
                "response_headers": safe_headers(dict(response.headers)),
                "cookies_after": self._cookie_markers(),
            },
        )
        return payload

    def _bootstrap_anonymous_session(self, source_url: str, *, attempt: int) -> None:
        assert self._session is not None
        bootstrap_url = source_url
        headers = dict(self.profile.build_bootstrap_headers(referer=None))

        self._emit_event(
            phase="anonymous_session_bootstrap_start",
            method="GET",
            url=bootstrap_url,
            message="Obtaining anonymous public Vinted session from catalog document via curl_cffi",
            details={
                "timeout_ms": self.timeout_ms,
                "attempt": attempt,
                "browser_profile": self.profile.name,
                "impersonate": self.profile.impersonate,
                "bootstrap_origin": "catalog_document",
                "http_session": self._session_marker(),
                "request_headers": safe_headers(headers),
                "cookies_before": self._cookie_markers(),
            },
        )
        started_at = time.perf_counter()
        try:
            response = self._session.get(
                bootstrap_url,
                headers=headers,
                timeout=self.timeout_ms / 1000,
            )
        except Exception as exc:
            self._emit_event(
                phase="anonymous_session_bootstrap_error",
                method="GET",
                url=bootstrap_url,
                duration_ms=_elapsed_ms(started_at),
                level="error",
                message=str(exc),
                details={
                    "timeout_ms": self.timeout_ms,
                    "attempt": attempt,
                    "bootstrap_origin": "catalog_document",
                    "http_session": self._session_marker(),
                    "request_headers": safe_headers(headers),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise VintedCatalogProviderError(f"Vinted anonymous session bootstrap failed: {exc}") from exc

        if is_datadome_challenge(response.status_code, dict(response.headers), response.text[:3000]):
            self._emit_event(
                phase="datadome_challenge_detected",
                method="GET",
                url=bootstrap_url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message="DataDome challenge detected during bootstrap",
                details={
                    "attempt": attempt,
                    "browser_profile": self.profile.name,
                    "bootstrap_origin": "catalog_document",
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise DataDomeChallengeError("DataDome challenge detected during bootstrap")

        if response.status_code >= 400:
            self._emit_event(
                phase="anonymous_session_bootstrap_error",
                method="GET",
                url=bootstrap_url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="error",
                message=f"Bootstrap returned HTTP {response.status_code}",
                details={
                    "timeout_ms": self.timeout_ms,
                    "attempt": attempt,
                    "bootstrap_origin": "catalog_document",
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise VintedCatalogProviderError(f"Vinted anonymous session bootstrap failed: HTTP {response.status_code}")

        cookie_names = list(self._session.cookies.keys()) if self._session.cookies else []
        dd_present = has_datadome_cookie(dict(self._session.cookies)) if self._session.cookies else False
        self._catalog_session_context = self._build_catalog_session_context(response)
        self._bootstrapped = True
        bootstrap_duration_ms = _elapsed_ms(started_at)

        self._emit_event(
            phase="anonymous_session_bootstrap_success",
            method="GET",
            url=bootstrap_url,
            status_code=response.status_code,
            duration_ms=bootstrap_duration_ms,
            message="Anonymous public session obtained from catalog document via curl_cffi",
            details={
                "session_marker_count": len(cookie_names),
                "datadome_cookie": dd_present,
                "bootstrap_duration_ms": bootstrap_duration_ms,
                "timeout_ms": self.timeout_ms,
                "attempt": attempt,
                "browser_profile": self.profile.name,
                "bootstrap_origin": "catalog_document",
                "http_session": self._session_marker(),
                "csrf_token_found": self._catalog_session_context.csrf_token is not None,
                "anon_id_found": self._catalog_session_context.anon_id is not None,
                "v_udt_found": self._catalog_session_context.v_udt is not None,
                "user_iso_locale": self._catalog_session_context.user_iso_locale,
                "screen": self._catalog_session_context.screen,
                "csrf_token": _secret_marker_or_none("csrf_token", self._catalog_session_context.csrf_token),
                "anon_id": _secret_marker_or_none("anon_id", self._catalog_session_context.anon_id),
                "v_udt": _secret_marker_or_none("v_udt", self._catalog_session_context.v_udt),
                "response_headers": safe_headers(dict(response.headers)),
                "cookies_after": self._cookie_markers(),
            },
        )

        # Human-like delay between bootstrap and catalog request
        delay_applied = human_delay(self.human_delay_min, self.human_delay_max)
        self._emit_event(
            phase="human_delay_applied",
            duration_ms=round(delay_applied * 1000),
            details={
                "min_seconds": self.human_delay_min,
                "max_seconds": self.human_delay_max,
                "bootstrap_origin": "catalog_document",
            },
        )

    def _build_catalog_session_context(self, response: Any) -> CatalogSessionContext:
        headers = dict(response.headers)
        return CatalogSessionContext(
            csrf_token=extract_csrf_token(response.text)
            or self._cookie_value("csrf_token")
            or self._cookie_value("_csrf_token"),
            anon_id=_header_value(headers, "x-anon-id") or self._cookie_value("anon_id"),
            v_udt=_header_value(headers, "x-v-udt") or self._cookie_value("v_udt"),
            user_iso_locale=_header_value(headers, "x-user-iso-locale"),
            screen=_header_value(headers, "x-screen"),
        )

    def _emit_event(
        self,
        *,
        phase: str,
        method: str | None = None,
        url: str | None = None,
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
            method=method,
            url=url,
            status_code=status_code,
            duration_ms=duration_ms,
            level=level,
            message=message,
            details=details,
        )

    def _diagnose_egress(self, *, attempt: int) -> None:
        if self._egress_diagnosed or not self.settings.egress_diagnostic_url:
            return
        assert self._session is not None
        url = str(self.settings.egress_diagnostic_url)
        headers = {"Accept": "application/json", "User-Agent": self.profile.user_agent}
        self._emit_event(
            phase="egress_diagnostic_start",
            method="GET",
            url=url,
            details={
                "attempt": attempt,
                "http_session": self._session_marker(),
                "proxy_configured": bool(self.proxy_url),
                "proxy_session": self.proxy_session_marker,
                "request_headers": safe_headers(headers),
            },
        )
        started_at = time.perf_counter()
        try:
            response = self._session.get(url, headers=headers, timeout=self.timeout_ms / 1000)
            payload = response.json() if "json" in str(response.headers.get("content-type", "")).lower() else {}
            details = {
                "attempt": attempt,
                "http_session": self._session_marker(),
                "proxy_configured": bool(self.proxy_url),
                "proxy_session": self.proxy_session_marker,
                "egress": _egress_details_from_payload(payload),
                "response_headers": safe_headers(dict(response.headers)),
                "cookies_after": self._cookie_markers(),
            }
            self._emit_event(
                phase="egress_diagnostic_success" if response.status_code < 400 else "egress_diagnostic_error",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level=None if response.status_code < 400 else "warning",
                message=None if response.status_code < 400 else f"HTTP {response.status_code}",
                details=details,
            )
        except Exception as exc:
            self._emit_event(
                phase="egress_diagnostic_error",
                method="GET",
                url=url,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message=str(exc),
                details={
                    "attempt": attempt,
                    "http_session": self._session_marker(),
                    "proxy_configured": bool(self.proxy_url),
                    "proxy_session": self.proxy_session_marker,
                },
            )
        finally:
            self._egress_diagnosed = True

    def _session_marker(self) -> dict[str, Any]:
        return safe_secret_marker("http_session_id", self.http_session_id, kind="http_session")

    def _cookie_markers(self) -> list[dict[str, Any]]:
        if self._session is None:
            return []
        return safe_cookie_markers(self._session.cookies)

    def _cookie_value(self, name: str) -> str | None:
        if self._session is None or not self._session.cookies:
            return None

        cookies = self._session.cookies
        get_value = getattr(cookies, "get", None)
        if callable(get_value):
            try:
                value = get_value(name)
            except Exception:
                value = None
            if value:
                return str(value)

        jar = getattr(cookies, "jar", cookies)
        for cookie in jar:
            cookie_name = getattr(cookie, "name", None)
            cookie_value = getattr(cookie, "value", None)
            if cookie_name == name and cookie_value:
                return str(cookie_value)
        return None


# ---------------------------------------------------------------------------
# Pure parsing and mapping functions with no transport dependency.
# ---------------------------------------------------------------------------


def _egress_details_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    connection = payload.get("connection") if isinstance(payload.get("connection"), dict) else {}
    return {
        "ip": payload.get("ip") or payload.get("query"),
        "country": payload.get("country"),
        "country_code": payload.get("country_code") or payload.get("countryCode"),
        "asn": payload.get("asn") or connection.get("asn"),
        "org": payload.get("org") or payload.get("isp") or connection.get("org"),
    }


def extract_csrf_token(html: str) -> str | None:
    for pattern in CSRF_TOKEN_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1)
    return None


def _header_value(headers: Mapping[str, Any], key: str) -> str | None:
    lowered_key = key.lower()
    for name, value in headers.items():
        if str(name).lower() == lowered_key and value:
            return str(value)
    return None


def _secret_marker_or_none(name: str, value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    return safe_secret_marker(name, value, kind="session_secret")


def _elapsed_ms(started_at: float) -> int:
    return max(round((time.perf_counter() - started_at) * 1000), 0)


def parse_catalog_api_payload(payload: Mapping[str, Any], base_url: str = "https://www.vinted.es") -> CatalogSearchResult:
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    pagination = payload.get("pagination") if isinstance(payload.get("pagination"), Mapping) else {}
    page = _optional_int(pagination.get("current_page") or pagination.get("page"))
    total_pages = _optional_int(pagination.get("total_pages"))
    next_page = page + 1 if page is not None and total_pages is not None and page < total_pages else None

    return CatalogSearchResult(
        items=[map_catalog_item(raw_item, base_url=base_url) for raw_item in raw_items],
        page=page,
        total_pages=total_pages,
        total_entries=_optional_int(pagination.get("total_entries") or pagination.get("total_count")),
        per_page=_optional_int(pagination.get("per_page")),
        next_page=next_page,
        provider_metadata={"source": "catalog_api_json"},
    )


def parse_item_detail_html(html: str, candidate: CatalogItemCandidate) -> CatalogItemDetail:
    product_data = extract_product_json_ld(html)
    embedded_data = extract_embedded_detail_data(html)
    raw_detail = sanitize_item_detail(product_data)

    return CatalogItemDetail(
        vinted_item_id=candidate.vinted_item_id,
        description=_optional_str(product_data.get("description")),
        color=_optional_str(product_data.get("color")),
        category=_optional_str(product_data.get("category")),
        shipping_price_amount=_optional_decimal(_find_first_key_value(embedded_data, {"shipping_price", "shipping_price_amount"})),
        buyer_protection_fee_amount=_optional_decimal(
            _find_first_key_value(embedded_data, {"buyer_protection_fee", "buyer_protection_fee_amount"})
        ),
        total_price_amount=_optional_decimal(_find_first_key_value(embedded_data, {"total_price", "total_price_amount"})),
        photos=_extract_photo_urls(product_data, embedded_data, candidate),
        seller_rating=_optional_decimal(_find_first_key_value(embedded_data, {"seller_rating", "rating"})),
        seller_badges=_extract_string_list(_find_first_key_value(embedded_data, {"seller_badges", "badges"})),
        availability_flags=_extract_availability_flags(product_data, embedded_data),
        raw={**raw_detail, "embedded": sanitize_embedded_detail(embedded_data)},
    )


def extract_product_json_ld(html: str) -> dict[str, Any]:
    for match in JSON_LD_PATTERN.findall(html):
        try:
            parsed = json.loads(match.strip())
        except json.JSONDecodeError:
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "Product":
                return candidate
    return {}


def sanitize_item_detail(raw_detail: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "description": raw_detail.get("description"),
        "color": raw_detail.get("color"),
        "category": raw_detail.get("category"),
        "image": raw_detail.get("image"),
        "offers": raw_detail.get("offers") if isinstance(raw_detail.get("offers"), Mapping) else None,
        "aggregateRating": raw_detail.get("aggregateRating") if isinstance(raw_detail.get("aggregateRating"), Mapping) else None,
    }


def extract_embedded_detail_data(html: str) -> dict[str, Any]:
    marker = '"item":'
    marker_index = html.find(marker)
    if marker_index == -1:
        return {}

    object_start = html.find("{", marker_index)
    object_end = _find_matching_object_end(html, object_start)
    if object_start == -1 or object_end is None:
        return {}

    try:
        parsed = json.loads(html[object_start:object_end])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def sanitize_embedded_detail(raw_detail: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "shipping_price": _find_first_key_value(raw_detail, {"shipping_price", "shipping_price_amount"}),
        "buyer_protection_fee": _find_first_key_value(raw_detail, {"buyer_protection_fee", "buyer_protection_fee_amount"}),
        "total_price": _find_first_key_value(raw_detail, {"total_price", "total_price_amount"}),
        "photos": _extract_photo_urls({}, raw_detail, None),
        "seller_rating": _find_first_key_value(raw_detail, {"seller_rating", "rating"}),
        "seller_badges": _extract_string_list(_find_first_key_value(raw_detail, {"seller_badges", "badges"})),
    }


def _extract_photo_urls(
    product_data: Mapping[str, Any],
    embedded_data: Mapping[str, Any],
    candidate: CatalogItemCandidate | None,
) -> list[str]:
    photos: list[str] = []
    product_images = product_data.get("image")
    if isinstance(product_images, str):
        photos.append(product_images)
    elif isinstance(product_images, list):
        photos.extend(str(image) for image in product_images if image)

    embedded_photos = _find_first_key_value(embedded_data, {"photos", "images"})
    if isinstance(embedded_photos, list):
        for photo in embedded_photos:
            if isinstance(photo, str):
                photos.append(photo)
            elif isinstance(photo, Mapping):
                url = photo.get("url") or photo.get("full_size_url") or photo.get("src")
                if url:
                    photos.append(str(url))

    if candidate and candidate.image_url:
        photos.append(candidate.image_url)

    return list(dict.fromkeys(photos))


def _extract_availability_flags(product_data: Mapping[str, Any], embedded_data: Mapping[str, Any]) -> dict[str, Any]:
    offers = product_data.get("offers") if isinstance(product_data.get("offers"), Mapping) else {}
    flags = {
        "availability": offers.get("availability"),
        "is_visible": _find_first_key_value(embedded_data, {"is_visible"}),
        "is_sold": _find_first_key_value(embedded_data, {"is_sold", "sold"}),
        "can_be_sold": _find_first_key_value(embedded_data, {"can_be_sold"}),
    }
    return {key: value for key, value in flags.items() if value is not None}


def _find_first_key_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in keys:
                if isinstance(child, Mapping) and "amount" in child:
                    return child.get("amount")
                return child
        for child in value.values():
            found = _find_first_key_value(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first_key_value(child, keys)
            if found is not None:
                return found
    return None


def _extract_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        results: list[str] = []
        for entry in value:
            if isinstance(entry, str):
                results.append(entry)
            elif isinstance(entry, Mapping):
                label = entry.get("name") or entry.get("title") or entry.get("code")
                if label:
                    results.append(str(label))
        return results
    return []


def parse_catalog_html(html: str, base_url: str = "https://www.vinted.es") -> CatalogSearchResult:
    flight_payload = decode_next_flight_payload(html)
    raw_items = extract_items_from_flight_payload(flight_payload)
    pagination = extract_pagination_from_flight_payload(flight_payload)
    page = _optional_int(pagination.get("current_page"))
    total_pages = _optional_int(pagination.get("total_pages"))
    next_page = page + 1 if page is not None and total_pages is not None and page < total_pages else None

    return CatalogSearchResult(
        items=[map_catalog_item(raw_item, base_url=base_url) for raw_item in raw_items],
        page=page,
        total_pages=total_pages,
        total_entries=_optional_int(pagination.get("total_entries")),
        per_page=_optional_int(pagination.get("per_page")),
        next_page=next_page,
        provider_metadata={"source": "next_flight_html"},
    )


def decode_next_flight_payload(html: str) -> str:
    chunks = []
    for raw_chunk in NEXT_FLIGHT_CHUNK_PATTERN.findall(html):
        chunks.append(json.loads(f'"{raw_chunk}"'))
    return "".join(chunks)


def extract_items_from_flight_payload(payload: str) -> list[dict[str, Any]]:
    marker = '"items":{"items":'
    marker_index = payload.find(marker)
    if marker_index == -1:
        return []

    array_start = payload.find("[", marker_index)
    array_end = _find_matching_array_end(payload, array_start)
    if array_start == -1 or array_end is None:
        return []

    parsed = json.loads(payload[array_start:array_end])
    return parsed if isinstance(parsed, list) else []


def extract_pagination_from_flight_payload(payload: str) -> dict[str, Any]:
    marker = '"pagination":'
    marker_index = payload.find(marker)
    if marker_index == -1:
        return {}

    object_start = payload.find("{", marker_index)
    object_end = _find_matching_object_end(payload, object_start)
    if object_start == -1 or object_end is None:
        return {}

    parsed = json.loads(payload[object_start:object_end])
    return parsed if isinstance(parsed, dict) else {}


def map_catalog_item(raw_item: Mapping[str, Any], base_url: str = "https://www.vinted.es") -> CatalogItemCandidate:
    price = raw_item.get("price") if isinstance(raw_item.get("price"), Mapping) else {}
    photo = raw_item.get("photo") if isinstance(raw_item.get("photo"), Mapping) else {}
    user = raw_item.get("user") if isinstance(raw_item.get("user"), Mapping) else {}

    item_path = str(raw_item.get("path") or raw_item.get("url") or "")
    return CatalogItemCandidate(
        vinted_item_id=str(raw_item["id"]),
        title=str(raw_item["title"]),
        brand=_optional_str(raw_item.get("brand_title")),
        price_amount=_optional_decimal(price.get("amount")),
        currency=_optional_str(price.get("currency_code")),
        size=_optional_str(raw_item.get("size_title")),
        status=_optional_str(raw_item.get("status")),
        seller_login=_optional_str(user.get("login")),
        seller_country=None,
        favorite_count=_optional_int(raw_item.get("favourite_count")),
        url=urljoin(base_url, item_path),
        image_url=_optional_str(photo.get("url")),
        raw=sanitize_catalog_item(raw_item),
    )


def sanitize_catalog_item(raw_item: Mapping[str, Any]) -> dict[str, Any]:
    price = raw_item.get("price") if isinstance(raw_item.get("price"), Mapping) else {}
    photo = raw_item.get("photo") if isinstance(raw_item.get("photo"), Mapping) else {}
    user = raw_item.get("user") if isinstance(raw_item.get("user"), Mapping) else {}

    return {
        "id": raw_item.get("id"),
        "title": raw_item.get("title"),
        "brand_title": raw_item.get("brand_title"),
        "price": {
            "amount": price.get("amount"),
            "currency_code": price.get("currency_code"),
        },
        "path": raw_item.get("path"),
        "size_title": raw_item.get("size_title"),
        "status": raw_item.get("status"),
        "favourite_count": raw_item.get("favourite_count"),
        "photo": {
            "url": photo.get("url"),
        },
        "user": {
            "login": user.get("login"),
        },
    }


def _find_matching_array_end(text: str, start: int) -> int | None:
    return _find_matching_end(text, start, "[", "]")


def _find_matching_object_end(text: str, start: int) -> int | None:
    return _find_matching_end(text, start, "{", "}")


def _find_matching_end(text: str, start: int, open_char: str, close_char: str) -> int | None:
    if start < 0 or start >= len(text) or text[start] != open_char:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
