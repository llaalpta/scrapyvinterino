from __future__ import annotations

import json
import random
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from curl_cffi.requests import Session

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.redaction import redact_sensitive_text, safe_cookie_markers, safe_headers, safe_secret_marker
from vinted_monitor.providers.browser_profiles import BrowserProfile, profile_for_impersonate
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult
from vinted_monitor.providers.catalog_url import build_catalog_api_params
from vinted_monitor.providers.datadome import (
    DataDomeChallengeError,
    DataDomeCookieCollector,
    build_datadome_tags_headers,
    extract_datadome_client_key,
    extract_datadome_script_url,
    extract_datadome_tags_version,
    has_datadome_cookie,
    human_delay,
    is_datadome_challenge,
)

NEXT_FLIGHT_CHUNK_PATTERN = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)
JSON_LD_PATTERN = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
NEXT_FLIGHT_RECORD_ID_PATTERN = re.compile(r"^[0-9A-Za-z]+$")
VINTED_IMAGE_HOST_PATTERN = re.compile(r"^images\d*\.vinted\.net$", re.IGNORECASE)
VINTED_ITEM_PATH_PATTERN = re.compile(r"^/items/(\d+)(?:-[^/]+)?/?$")
DETAIL_PARSER_VERSION = "next_flight_v2"
CSRF_TOKEN_PATTERNS = (
    re.compile(r'"CSRF_TOKEN"\s*:\s*"([^"]+)"'),
    re.compile(r'\\"CSRF_TOKEN\\"\s*:\s*\\"([^"\\]+)\\"'),
    re.compile(r'"csrfToken"\s*:\s*"([^"]+)"'),
    re.compile(r'\\"csrfToken\\"\s*:\s*\\"([^"\\]+)\\"'),
    re.compile(r'"X-CSRF-Token"\s*,\s*"([^"]+)"'),
    re.compile(r'\\"X-CSRF-Token\\"\s*,\s*\\"([^"\\]+)\\"'),
    re.compile(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
)
VIEWPORT_PATTERN = re.compile(r"^\d{3,5}x\d{3,5}$")
DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS = 5.0
MAX_RATE_LIMIT_RETRY_AFTER_SECONDS = 30.0
SESSION_REFRESH_COOKIE_NAMES = {
    "__cf_bm",
    "_vinted_fr_session",
    "access_token_web",
    "anon_id",
    "csrf_token",
    "datadome",
    "refresh_token_web",
    "v_sid",
    "v_udt",
}


@dataclass
class CatalogSessionContext:
    csrf_token: str | None = None
    anon_id: str | None = None
    access_token_web: str | None = None
    datadome: str | None = None
    cf_bm: str | None = None
    v_udt: str | None = None
    user_iso_locale: str | None = None
    screen: str | None = None


@dataclass
class PreparedCatalogSession:
    session_id: int | None = None
    proxy_session_id: str | None = None
    cookies: dict[str, str] | None = None
    csrf_token: str | None = None
    anon_id: str | None = None
    access_token_web: str | None = None
    datadome: str | None = None
    cf_bm: str | None = None
    v_udt: str | None = None
    user_iso_locale: str | None = None
    vinted_screen: str | None = None
    egress_ip: str | None = None
    egress_country_code: str | None = None


@dataclass
class EgressContext:
    ip: str | None = None
    country: str | None = None
    country_code: str | None = None
    asn: int | str | None = None
    org: str | None = None


class VintedCatalogProviderError(RuntimeError):
    pass


class VintedCatalogSessionError(VintedCatalogProviderError):
    pass


class VintedCatalogChallengeError(VintedCatalogSessionError):
    pass


class VintedItemDetailHTTPError(VintedCatalogProviderError):
    def __init__(self, item_id: str, status_code: int) -> None:
        super().__init__(f"Vinted detail request failed for {item_id}: HTTP {status_code}")
        self.status_code = status_code
        self.failure_kind = f"detail_http_{status_code}"
        self.retryable = status_code not in {404, 410}


class VintedCatalogRateLimitError(VintedCatalogProviderError):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None,
        retry_after_source: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.retry_after_source = retry_after_source
        self.retryable = retryable


class VintedCatalogSessionContextError(VintedCatalogProviderError):
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
        expected_country_code: str | None = "ES",
        locale: str = "es-ES",
        accept_language: str = "en-GB,en;q=0.9",
        screen: str = "catalog",
        viewport_size: str = "1920x1080",
        prepared_session: PreparedCatalogSession | None = None,
        require_complete_session_context: bool = True,
        require_datadome_cookie: bool = True,
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
        self.expected_country_code = expected_country_code.strip().upper() if expected_country_code else None
        self.locale = locale
        self.accept_language = accept_language
        self.vinted_screen = screen.strip().lower()
        self.viewport_size = viewport_size.strip().lower()
        self.prepared_session = prepared_session
        self.require_complete_session_context = require_complete_session_context
        self.require_datadome_cookie = require_datadome_cookie
        self.http_session_id = str(uuid.uuid4())
        self._session: Session | None = None
        self._bootstrapped = prepared_session is not None
        self._egress_diagnosed = False
        self._catalog_session_context = CatalogSessionContext()
        self._egress_context = EgressContext()
        self._last_bootstrap_html = ""
        self._last_item_document_html = ""
        self.prepared_session_refreshed = False
        if prepared_session is not None:
            self._catalog_session_context = CatalogSessionContext(
                csrf_token=prepared_session.csrf_token,
                anon_id=prepared_session.anon_id,
                access_token_web=prepared_session.access_token_web,
                datadome=prepared_session.datadome or (prepared_session.cookies or {}).get("datadome"),
                cf_bm=prepared_session.cf_bm or (prepared_session.cookies or {}).get("__cf_bm"),
                v_udt=prepared_session.v_udt or (prepared_session.cookies or {}).get("v_udt"),
                user_iso_locale=prepared_session.user_iso_locale,
                screen=prepared_session.vinted_screen,
            )
            self._egress_context = EgressContext(
                ip=prepared_session.egress_ip,
                country_code=_normalize_country_code(prepared_session.egress_country_code),
            )

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
            except VintedCatalogSessionContextError:
                raise
            except VintedCatalogRateLimitError as exc:
                if not exc.retryable:
                    raise
                self._refresh_anonymous_session_in_place(
                    source.url,
                    attempt=retry_index + 2,
                    retry_reason="rate_limited",
                    retry_after_seconds=exc.retry_after_seconds,
                    retry_after_source=exc.retry_after_source,
                )
                response = self._request_catalog_api(source, page, attempt=retry_index + 2)
                return parse_catalog_api_payload(response, base_url=str(self.settings.vinted_base_url))
            except VintedCatalogSessionError:
                self._refresh_anonymous_session_in_place(
                    source.url,
                    attempt=retry_index + 2,
                    retry_reason="session_rejected",
                )
                response = self._request_catalog_api(source, page, attempt=retry_index + 2)
                return parse_catalog_api_payload(response, base_url=str(self.settings.vinted_base_url))
            except VintedCatalogProviderError as exc:
                last_error = exc
                if retry_index >= self.request_retries:
                    raise

        raise last_error or VintedCatalogProviderError("Vinted catalog API request failed")

    def bootstrap_for_session(self, source_url: str, *, collect_datadome: bool = False) -> dict[str, Any]:
        """Warm only the catalog document and return a safe context report."""
        self._ensure_session()
        self._diagnose_egress(attempt=1)
        if not self._bootstrapped:
            self._bootstrap_anonymous_session(source_url, attempt=1)
        if collect_datadome:
            self._try_datadome_collector(source_url)
        return self._session_context_report()

    def probe_catalog_api(self, source_url: str, *, include_payload: bool = False) -> dict[str, Any]:
        """Probe the catalog API once and return a safe diagnostic report.

        This intentionally bypasses the conservative prepared-session gate but
        does not persist data or mark the session as ready. It is only for
        operator diagnostics.
        """
        self._ensure_session()
        self._diagnose_egress(attempt=1)
        if not self._bootstrapped:
            self._bootstrap_anonymous_session(source_url, attempt=1)
        return self._probe_catalog_api_request(source_url, include_payload=include_payload)

    def probe_item_detail_document(self, item_ref: str, *, referer_url: str | None = None) -> dict[str, Any]:
        """Run the production public-document parser and return only safe diagnostics."""
        item_id = extract_vinted_item_id(item_ref)
        if item_id is None:
            raise ValueError("item_ref must be a Vinted item id or item URL")
        parsed_ref = urlparse(item_ref)
        item_url = (
            item_ref
            if parsed_ref.scheme in {"http", "https"} and parsed_ref.netloc.lower().endswith("vinted.es")
            else urljoin(str(self.settings.vinted_base_url), f"/items/{item_id}")
        )
        candidate = CatalogItemCandidate(
            vinted_item_id=item_id,
            title="",
            brand=None,
            price_amount=None,
            currency=None,
            size=None,
            status=None,
            seller_login=None,
            seller_country=None,
            favorite_count=None,
            url=item_url,
            image_url=None,
            raw={},
        )
        started_at = time.perf_counter()
        detail = self.fetch_detail(candidate, referer_url=referer_url)
        summary = {
            "parser_version": detail.raw.get("parser_version"),
            "field_sources": detail.field_sources,
            "missing_required": detail.raw.get("missing_fields", []),
            "title": detail.title,
            "brand": detail.brand,
            "size": detail.size,
            "status": detail.status,
            "description_observed": "description" in detail.observed_fields,
            "description_length": len(detail.description or ""),
            "photo_count": len(detail.photos),
            "price_amount": str(detail.price_amount) if detail.price_amount is not None else None,
            "currency": detail.currency,
            "buyer_protection_fee_amount": (
                str(detail.buyer_protection_fee_amount)
                if detail.buyer_protection_fee_amount is not None
                else None
            ),
            "total_price_amount": str(detail.total_price_amount) if detail.total_price_amount is not None else None,
            "shipping_price_amount": (
                str(detail.shipping_price_amount) if detail.shipping_price_amount is not None else None
            ),
            "availability_state": detail.availability_flags.get("state"),
            "availability_reason_codes": detail.availability_flags.get("reason_codes", []),
        }
        return {
            "outcome": "accepted_html",
            "item_id": item_id,
            "detail_document_url": build_item_detail_navigation_url(item_url),
            "status_code": 200,
            "duration_ms": _elapsed_ms(started_at),
            "detail_summary": summary,
            "missing_required": summary["missing_required"],
            "error": None,
        }

    def export_prepared_session(self, *, proxy_session_id: str | None = None) -> PreparedCatalogSession:
        if self._session is None or not self._bootstrapped:
            raise VintedCatalogSessionContextError("Catalog session has not been bootstrapped")
        context = self._catalog_session_context
        cookies = self._cookie_values()
        return PreparedCatalogSession(
            session_id=self.prepared_session.session_id if self.prepared_session else None,
            proxy_session_id=proxy_session_id,
            cookies=cookies,
            csrf_token=context.csrf_token,
            anon_id=context.anon_id,
            access_token_web=context.access_token_web or cookies.get("access_token_web"),
            datadome=context.datadome or cookies.get("datadome"),
            cf_bm=context.cf_bm or cookies.get("__cf_bm"),
            v_udt=context.v_udt or cookies.get("v_udt"),
            user_iso_locale=context.user_iso_locale,
            vinted_screen=context.screen,
            egress_ip=self._egress_context.ip,
            egress_country_code=self._egress_context.country_code,
        )

    def fetch_detail(self, candidate: CatalogItemCandidate, *, referer_url: str | None = None) -> CatalogItemDetail:
        self._ensure_session()
        self._diagnose_egress(attempt=1)
        assert self._session is not None
        detail_url = build_item_detail_navigation_url(candidate.url)
        cookies_before_request = self._cookie_values()
        context_before_request = self._session_context_values()
        headers = self.profile.build_bootstrap_headers(referer=referer_url, accept_language=self.accept_language)
        if referer_url:
            headers["sec-fetch-site"] = "same-origin"
        self._emit_event(
            phase="detail_http_request_start",
            method="GET",
            url=detail_url,
            details={
                "vinted_item_id": candidate.vinted_item_id,
                "referer_url": referer_url,
                "http_session": self._session_marker(),
                "request_headers": safe_headers(headers),
                "cookies_before": self._cookie_markers(),
                "default_headers": False,
            },
        )
        started_at = time.perf_counter()
        try:
            response, final_url, redirect_count = self._request_item_detail_response(
                detail_url,
                candidate.vinted_item_id,
                headers,
            )
            refreshed_markers = self._refresh_session_context_from_cookies(
                cookies_before=cookies_before_request,
                context_before=context_before_request,
            )
            refreshed_markers = sorted(
                set(refreshed_markers).union(self._refresh_session_context_from_detail_response(response))
            )
            response_details = {
                "vinted_item_id": candidate.vinted_item_id,
                "referer_url": referer_url,
                "http_session": self._session_marker(),
                "request_headers": safe_headers(headers),
                "response_headers": safe_headers(dict(response.headers)),
                "cookies_after": self._cookie_markers(),
                "session_context_refreshed": bool(refreshed_markers),
                "refreshed_markers": refreshed_markers,
                "final_url": final_url,
                "redirect_count": redirect_count,
            }
            if is_cloudflare_challenge(response.status_code, dict(response.headers)):
                self._emit_event(
                    phase="detail_http_request_error",
                    method="GET",
                    url=detail_url,
                    status_code=response.status_code,
                    duration_ms=_elapsed_ms(started_at),
                    level="warning",
                    message="Cloudflare challenge detected on item detail request",
                    details=response_details,
                )
                raise VintedCatalogChallengeError("Cloudflare challenge detected on item detail request")
            if is_datadome_challenge(response.status_code, dict(response.headers), response.text[:3000]):
                self._emit_event(
                    phase="detail_http_request_error",
                    method="GET",
                    url=detail_url,
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
                    url=detail_url,
                    status_code=response.status_code,
                    duration_ms=_elapsed_ms(started_at),
                    level="error",
                    message=f"HTTP {response.status_code}",
                    details=response_details,
                )
                raise VintedItemDetailHTTPError(candidate.vinted_item_id, response.status_code)
            content_type = _header_value(response.headers, "content-type")
            if content_type and "text/html" not in content_type.lower():
                raise VintedCatalogProviderError(
                    f"Vinted detail request returned non-HTML content for {candidate.vinted_item_id}"
                )
            self._emit_event(
                phase="detail_http_request_success",
                method="GET",
                url=detail_url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                details=response_details,
            )
            try:
                detail = parse_item_detail_html(response.text, candidate)
            except Exception as exc:
                safe_error = redact_sensitive_text(str(exc))
                self._emit_event(
                    phase="detail_parse_error",
                    method="GET",
                    url=detail_url,
                    status_code=response.status_code,
                    duration_ms=_elapsed_ms(started_at),
                    level="error",
                    message=safe_error,
                    details={
                        "vinted_item_id": candidate.vinted_item_id,
                        "referer_url": referer_url,
                        "html_length": len(response.text or ""),
                        "error": safe_error,
                    },
                )
                raise VintedCatalogProviderError(
                    f"Vinted detail parse failed for {candidate.vinted_item_id}: {safe_error}"
                ) from exc
            self._emit_event(
                phase="detail_parse_success",
                method="GET",
                url=detail_url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                details={
                    "vinted_item_id": candidate.vinted_item_id,
                    "referer_url": referer_url,
                    "html_length": len(response.text or ""),
                    "has_description": bool(detail.description),
                    "description_observed": "description" in detail.observed_fields,
                    "photo_count": len(detail.photos),
                    "has_total_price": detail.total_price_amount is not None,
                    "availability_state": detail.availability_flags.get("state"),
                    "field_sources": detail.field_sources,
                    "missing_fields": detail.raw.get("missing_fields", []),
                },
            )
            return detail
        except (DataDomeChallengeError, VintedCatalogChallengeError):
            raise
        except Exception as exc:
            if not isinstance(exc, VintedCatalogProviderError):
                safe_error = redact_sensitive_text(str(exc))
                self._emit_event(
                    phase="detail_http_request_error",
                    method="GET",
                    url=detail_url,
                    duration_ms=_elapsed_ms(started_at),
                    level="error",
                    message=safe_error,
                    details={
                        "vinted_item_id": candidate.vinted_item_id,
                        "referer_url": referer_url,
                        "http_session": self._session_marker(),
                        "request_headers": safe_headers(headers),
                        "cookies_after": self._cookie_markers(),
                        "default_headers": False,
                    },
                )
                raise VintedCatalogProviderError(
                    f"Vinted detail request failed for {candidate.vinted_item_id}: {safe_error}"
                ) from exc
            raise

    def _request_item_detail_response(
        self,
        detail_url: str,
        item_id: str,
        headers: Mapping[str, str],
    ) -> tuple[Any, str, int]:
        assert self._session is not None
        current_url = detail_url
        for redirect_count in range(4):
            _validate_item_detail_url(current_url, expected_item_id=item_id)
            response = self._session.get(
                current_url,
                headers=dict(headers),
                timeout=self.timeout_ms / 1000,
                default_headers=False,
                allow_redirects=False,
            )
            effective_url = str(getattr(response, "url", None) or current_url)
            _validate_item_detail_url(effective_url, expected_item_id=item_id)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response, effective_url, redirect_count
            if is_cloudflare_challenge(response.status_code, dict(response.headers)) or is_datadome_challenge(
                response.status_code,
                dict(response.headers),
                response.text[:3000],
            ):
                return response, effective_url, redirect_count
            location = _header_value(response.headers, "location")
            if not location:
                raise VintedCatalogProviderError("Vinted item detail redirect omitted Location")
            next_url = urljoin(effective_url, location)
            _validate_item_detail_url(next_url, expected_item_id=item_id)
            current_url = build_item_detail_navigation_url(next_url)
        raise VintedCatalogProviderError("Vinted item detail redirect limit exceeded")

    def _request_vinted_response(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        expected_path: str,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[Any, str, int]:
        assert self._session is not None
        current_url = url
        current_params = params
        for redirect_count in range(4):
            _validate_vinted_response_url(current_url, expected_path=expected_path)
            response = self._session.get(
                current_url,
                params=current_params,
                headers=dict(headers),
                timeout=self.timeout_ms / 1000,
                default_headers=False,
                allow_redirects=False,
            )
            effective_url = str(getattr(response, "url", None) or current_url)
            _validate_vinted_response_url(effective_url, expected_path=expected_path)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response, effective_url, redirect_count
            if is_cloudflare_challenge(response.status_code, dict(response.headers)) or is_datadome_challenge(
                response.status_code,
                dict(response.headers),
                response.text[:3000],
            ):
                return response, effective_url, redirect_count
            location = _header_value(response.headers, "location")
            if not location:
                raise VintedCatalogProviderError("Vinted redirect omitted Location")
            next_url = urljoin(effective_url, location)
            _validate_vinted_response_url(next_url, expected_path=expected_path)
            current_url = next_url
            current_params = None
        raise VintedCatalogProviderError("Vinted redirect limit exceeded")

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
        self._last_bootstrap_html = ""
        self._last_item_document_html = ""

    def _ensure_session(self) -> None:
        if self._session is None:
            proxy_dict = {"https": self.proxy_url, "http": self.proxy_url} if self.proxy_url else None
            self._session = self.session_factory(
                impersonate=self.profile.impersonate,
                proxies=proxy_dict,
            )
            if self.prepared_session is not None:
                self._load_prepared_cookies(self.prepared_session)
            self._emit_event(
                phase="http_session_created",
                details={
                    "http_session": self._session_marker(),
                    "browser_profile": self.profile.name,
                    "impersonate": self.profile.impersonate,
                    "proxy_configured": bool(self.proxy_url),
                    "proxy_session": self.proxy_session_marker,
                    "prepared_vinted_session_id": self.prepared_session.session_id if self.prepared_session else None,
                },
            )

    def _load_prepared_cookies(self, prepared: PreparedCatalogSession) -> None:
        if self._session is None:
            return
        for name, value in (prepared.cookies or {}).items():
            if not value:
                continue
            cookies = self._session.cookies
            set_value = getattr(cookies, "set", None)
            if callable(set_value):
                try:
                    set_value(name, value, domain=".vinted.es", path="/", secure=True)
                    continue
                except TypeError:
                    try:
                        set_value(name, value)
                        continue
                    except Exception:
                        pass
                except Exception:
                    pass
            try:
                cookies.update({name: value})
            except Exception:
                pass

    def _reset_session(self) -> None:
        """Close and recreate the session (same proxy, fresh cookies)."""
        self.close()
        self._catalog_session_context = CatalogSessionContext()
        self._egress_context = EgressContext()
        self._ensure_session()

    def _refresh_anonymous_session_in_place(
        self,
        source_url: str,
        *,
        attempt: int,
        retry_reason: str,
        retry_after_seconds: float | None = None,
        retry_after_source: str | None = None,
    ) -> None:
        assert self._session is not None
        started_at = time.perf_counter()
        if retry_after_seconds is not None:
            backoff_seconds = retry_after_seconds + _rate_limit_jitter_seconds()
            self._emit_event(
                phase="catalog_api_rate_limit_backoff",
                method="GET",
                url=source_url,
                level="warning",
                message="Rate limit backoff applied before refreshing anonymous session",
                details={
                    "attempt": attempt,
                    "retry_reason": retry_reason,
                    "retry_after_seconds": retry_after_seconds,
                    "retry_after_source": retry_after_source,
                    "backoff_seconds": round(backoff_seconds, 3),
                    "max_retry_after_seconds": MAX_RATE_LIMIT_RETRY_AFTER_SECONDS,
                    "http_session": self._session_marker(),
                },
            )
            time.sleep(backoff_seconds)

        self._emit_event(
            phase="anonymous_session_refresh_start",
            method="GET",
            url=source_url,
            level="warning",
            message="Refreshing anonymous public session with the current HTTP session",
            details={
                "attempt": attempt,
                "retry_reason": retry_reason,
                "retry_after_seconds": retry_after_seconds,
                "retry_after_source": retry_after_source,
                "http_session": self._session_marker(),
                "proxy_session": self.proxy_session_marker,
                "cookies_before": self._cookie_markers(),
            },
        )
        self._bootstrap_anonymous_session(source_url, attempt=attempt)
        self.prepared_session_refreshed = True
        self._emit_event(
            phase="anonymous_session_refresh_success",
            method="GET",
            url=source_url,
            duration_ms=_elapsed_ms(started_at),
            message="Anonymous public session refreshed with the current HTTP session",
            details={
                "attempt": attempt,
                "retry_reason": retry_reason,
                "refresh_duration_ms": _elapsed_ms(started_at),
                "http_session": self._session_marker(),
                "proxy_session": self.proxy_session_marker,
                "context": self._session_context_report(),
                "cookies_after": self._cookie_markers(),
            },
        )

    def _request_catalog_api(self, source: Any, page: int | None, *, attempt: int) -> dict[str, Any]:
        assert self._session is not None
        url = urljoin(str(self.settings.vinted_base_url), "/api/v2/catalog/items")
        params = build_catalog_api_params(source.url, page, self.catalog_per_page)
        context_report = self._session_context_report()
        missing_context = self._missing_session_context(context_report)
        if self.require_complete_session_context and missing_context:
            self._emit_event(
                phase="catalog_session_context_incomplete",
                level="error",
                details={
                    **context_report,
                    "missing_required": missing_context,
                    "message": "Catalog session context incomplete; refusing catalog API request",
                },
            )
            raise VintedCatalogSessionContextError(
                f"Catalog session context incomplete; refusing catalog API request: {', '.join(missing_context)}"
            )
        self._emit_event(
            phase="catalog_session_context_ready",
            details=context_report,
        )

        headers = dict(
            self.profile.build_api_headers(
                referer=source.url,
                accept_language=self.accept_language,
                locale=self.locale,
                screen=self.vinted_screen,
            )
        )
        if self._catalog_session_context.anon_id:
            headers["x-anon-id"] = self._catalog_session_context.anon_id
        if self._catalog_session_context.csrf_token:
            headers["x-csrf-token"] = self._catalog_session_context.csrf_token

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
                "api_param_summary": _api_param_summary(params),
                "session_marker_count": len(cookie_names),
                "timeout_ms": self.timeout_ms,
                "attempt": attempt,
                "request_profile": "api_har146",
                "browser_profile": self.profile.name,
                "impersonate": self.profile.impersonate,
                "http_session": self._session_marker(),
                **context_report,
                "csrf_token": _secret_marker_or_none("csrf_token", self._catalog_session_context.csrf_token),
                "anon_id": _secret_marker_or_none("anon_id", self._catalog_session_context.anon_id),
                "request_headers": safe_headers(headers),
                "cookies_before": self._cookie_markers(),
                "default_headers": False,
            },
        )
        started_at = time.perf_counter()
        try:
            response, final_url, redirect_count = self._request_vinted_response(
                url,
                headers,
                expected_path="/api/v2/catalog/items",
                params=params,
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

        if is_cloudflare_challenge(response.status_code, dict(response.headers)):
            self._emit_event(
                phase="cloudflare_challenge_detected",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message="Cloudflare served a challenge instead of catalog data",
                details={
                    "attempt": attempt,
                    "browser_profile": self.profile.name,
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                    "final_url": final_url,
                    "redirect_count": redirect_count,
                },
            )
            raise VintedCatalogChallengeError("Cloudflare challenge detected on catalog API request")

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

        if response.status_code == 429:
            retry_after_seconds, retry_after_source = _retry_after_seconds(_header_value(response.headers, "Retry-After"))
            retryable = retry_after_seconds is not None and retry_after_seconds <= MAX_RATE_LIMIT_RETRY_AFTER_SECONDS
            self._emit_event(
                phase="catalog_api_rate_limited",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message="Catalog API rate limited the request",
                details={
                    "attempt": attempt,
                    "retryable": retryable,
                    "retry_after_seconds": retry_after_seconds,
                    "retry_after_source": retry_after_source,
                    "retry_after_too_long": retry_after_seconds is not None
                    and retry_after_seconds > MAX_RATE_LIMIT_RETRY_AFTER_SECONDS,
                    "max_retry_after_seconds": MAX_RATE_LIMIT_RETRY_AFTER_SECONDS,
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                },
            )
            raise VintedCatalogRateLimitError(
                "Vinted catalog API rate limited the request"
                if retryable
                else "Vinted catalog API rate limited the request beyond the retry budget",
                retry_after_seconds=retry_after_seconds,
                retry_after_source=retry_after_source,
                retryable=retryable,
            )

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
                "request_profile": "api_har146",
                "browser_profile": self.profile.name,
                "http_session": self._session_marker(),
                "response_summary": _response_summary(response.headers),
                "response_headers": safe_headers(dict(response.headers)),
                "cookies_after": self._cookie_markers(),
                "cookie_flags": _cookie_flags_from_values(self._cookie_values()),
                "final_url": final_url,
                "redirect_count": redirect_count,
            },
        )
        return payload

    def _probe_catalog_api_request(self, source_url: str, *, include_payload: bool = False) -> dict[str, Any]:
        assert self._session is not None
        url = urljoin(str(self.settings.vinted_base_url), "/api/v2/catalog/items")
        params = build_catalog_api_params(source_url, None, self.catalog_per_page)
        context_report = self._session_context_report()
        missing_context = self._missing_session_context(context_report)
        headers = dict(
            self.profile.build_api_headers(
                referer=source_url,
                accept_language=self.accept_language,
                locale=self.locale,
                screen=self.vinted_screen,
            )
        )
        if self._catalog_session_context.anon_id:
            headers["x-anon-id"] = self._catalog_session_context.anon_id
        if self._catalog_session_context.csrf_token:
            headers["x-csrf-token"] = self._catalog_session_context.csrf_token

        request_details = {
            "method": "GET",
            "url": url,
            "params": params,
            "headers": safe_headers(headers),
            "cookie_count": len(list(self._session.cookies.keys())) if self._session.cookies else 0,
            "cookies": self._cookie_markers(),
        }
        self._emit_event(
            phase="catalog_api_probe_start",
            method="GET",
            url=url,
            details={
                "source_url": source_url,
                "api_params": params,
                "api_param_summary": _api_param_summary(params),
                "request_profile": "api_har146",
                "missing_required": missing_context,
                "context": context_report,
                **_context_summary(context_report, self._cookie_values()),
                "request_headers": safe_headers(headers),
                "cookies_before": self._cookie_markers(),
                "default_headers": False,
            },
        )
        started_at = time.perf_counter()
        try:
            response, final_url, redirect_count = self._request_vinted_response(
                url,
                headers,
                expected_path="/api/v2/catalog/items",
                params=params,
            )
        except Exception as exc:
            duration_ms = _elapsed_ms(started_at)
            self._emit_event(
                phase="catalog_api_probe_error",
                method="GET",
                url=url,
                duration_ms=duration_ms,
                level="warning",
                message=redact_sensitive_text(str(exc)),
                details={
                    "outcome": "transport_error",
                    "source_url": source_url,
                    "missing_required": missing_context,
                    "context": context_report,
                    "request_headers": safe_headers(headers),
                    "cookies_after": self._cookie_markers(),
                },
            )
            return {
                "outcome": "transport_error",
                "source_url": source_url,
                "catalog_api_url": url,
                "status_code": None,
                "duration_ms": duration_ms,
                "egress_ip": self._egress_context.ip,
                "egress_country_code": self._egress_context.country_code,
                "context": context_report,
                "missing_required": missing_context,
                "request": request_details,
                "response": {},
                "error": redact_sensitive_text(str(exc)),
            }

        duration_ms = _elapsed_ms(started_at)
        content_type = str(response.headers.get("content-type", ""))
        response_details: dict[str, Any] = {
            "headers": safe_headers(dict(response.headers)),
            "content_type": content_type,
            "final_url": final_url,
            "redirect_count": redirect_count,
        }
        outcome = "rejected"
        error: str | None = None
        accepted_payload: Mapping[str, Any] | None = None
        body_snippet = getattr(response, "text", "")[:1200]

        if is_cloudflare_challenge(response.status_code, dict(response.headers)):
            outcome = "challenge"
            response_details["challenge_kind"] = "cloudflare"
            response_details["body_snippet"] = redact_sensitive_text(body_snippet)
        elif is_datadome_challenge(response.status_code, dict(response.headers), body_snippet):
            outcome = "challenge"
            response_details["challenge_kind"] = "datadome"
            response_details["body_snippet"] = redact_sensitive_text(body_snippet)
        elif response.status_code >= 400:
            outcome = "rejected"
            response_details["body_snippet"] = redact_sensitive_text(body_snippet)
        elif "json" not in content_type.lower():
            outcome = "non_json"
            response_details["body_snippet"] = redact_sensitive_text(body_snippet)
        else:
            try:
                payload = response.json()
            except Exception as exc:
                outcome = "non_json"
                error = redact_sensitive_text(str(exc))
                response_details["body_snippet"] = redact_sensitive_text(body_snippet)
            else:
                if isinstance(payload, Mapping):
                    outcome = "accepted_json"
                    accepted_payload = payload
                    items = payload.get("items")
                    response_details["json_keys"] = sorted(str(key) for key in payload.keys())[:25]
                    response_details["items_count"] = len(items) if isinstance(items, list) else None
                else:
                    outcome = "non_json"
                    response_details["body_snippet"] = redact_sensitive_text(body_snippet)

        event_phase = "catalog_api_probe_success" if outcome == "accepted_json" else "catalog_api_probe_failed"
        self._emit_event(
            phase=event_phase,
            method="GET",
            url=url,
            status_code=response.status_code,
            duration_ms=duration_ms,
            level=None if outcome == "accepted_json" else "warning",
            details={
                "outcome": outcome,
                "source_url": source_url,
                "missing_required": missing_context,
                "context": context_report,
                **_context_summary(context_report, self._cookie_values()),
                "request_profile": "api_har146",
                "response_summary": _response_summary(response.headers),
                "content_type": response_details.get("content_type"),
                "items_count": response_details.get("items_count"),
                "json_keys": response_details.get("json_keys"),
                "request_headers": safe_headers(headers),
                "response": response_details,
                "cookies_after": self._cookie_markers(),
                "error": error,
            },
        )
        result = {
            "outcome": outcome,
            "source_url": source_url,
            "catalog_api_url": url,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "egress_ip": self._egress_context.ip,
            "egress_country_code": self._egress_context.country_code,
            "context": context_report,
            "missing_required": missing_context,
            "request": request_details,
            "response": response_details,
            "error": error,
        }
        if include_payload and accepted_payload is not None:
            result["payload"] = accepted_payload
        return result

    def _bootstrap_anonymous_session(self, source_url: str, *, attempt: int) -> None:
        assert self._session is not None
        bootstrap_url = source_url
        headers = dict(self.profile.build_bootstrap_headers(referer=None, accept_language=self.accept_language))

        self._emit_event(
            phase="anonymous_session_bootstrap_start",
            method="GET",
            url=bootstrap_url,
            message="Obtaining anonymous public Vinted session from catalog document via curl_cffi",
            details={
                "timeout_ms": self.timeout_ms,
                "attempt": attempt,
                "request_profile": "bootstrap_har146",
                "browser_profile": self.profile.name,
                "impersonate": self.profile.impersonate,
                "expected_country_code": self.expected_country_code,
                "locale": self.locale,
                "accept_language": self.accept_language,
                "viewport_size": self.viewport_size,
                "vinted_screen": self.vinted_screen,
                "bootstrap_origin": "catalog_document",
                "http_session": self._session_marker(),
                "request_headers": safe_headers(headers),
                "cookies_before": self._cookie_markers(),
            },
        )
        started_at = time.perf_counter()
        try:
            response, final_url, redirect_count = self._request_vinted_response(
                bootstrap_url,
                headers,
                expected_path=urlparse(bootstrap_url).path,
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

        if is_cloudflare_challenge(response.status_code, dict(response.headers)):
            self._emit_event(
                phase="cloudflare_challenge_detected",
                method="GET",
                url=bootstrap_url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message="Cloudflare challenge detected during bootstrap",
                details={
                    "attempt": attempt,
                    "browser_profile": self.profile.name,
                    "bootstrap_origin": "catalog_document",
                    "http_session": self._session_marker(),
                    "response_headers": safe_headers(dict(response.headers)),
                    "cookies_after": self._cookie_markers(),
                    "final_url": final_url,
                    "redirect_count": redirect_count,
                },
            )
            raise VintedCatalogChallengeError("Cloudflare challenge detected during bootstrap")

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

        self._last_bootstrap_html = response.text or ""
        cookie_names = list(self._session.cookies.keys()) if self._session.cookies else []
        dd_present = has_datadome_cookie(dict(self._session.cookies)) if self._session.cookies else False
        self._catalog_session_context = self._build_catalog_session_context(response)
        self._bootstrapped = True
        bootstrap_duration_ms = _elapsed_ms(started_at)
        context_report = self._session_context_report()
        response_summary = _response_summary(response.headers)

        self._emit_event(
            phase="anonymous_session_bootstrap_success",
            method="GET",
            url=bootstrap_url,
            status_code=response.status_code,
            duration_ms=bootstrap_duration_ms,
            message="Anonymous public session obtained from catalog document via curl_cffi",
            details={
                "session_marker_count": len(cookie_names),
                "bootstrap_duration_ms": bootstrap_duration_ms,
                "timeout_ms": self.timeout_ms,
                "attempt": attempt,
                "request_profile": "bootstrap_har146",
                "http_session": self._session_marker(),
                **context_report,
                "datadome_cookie_seen_by_detector": dd_present,
                "response_summary": response_summary,
                **response_summary,
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

    def _try_datadome_collector(
        self,
        source_url: str,
        *,
        page_html: str | None = None,
        expected_screen: str | None = None,
        bootstrap_origin: str = "catalog_document",
    ) -> None:
        assert self._session is not None
        collector_url = str(self.settings.vinted_datadome_collector_url)
        html = self._last_bootstrap_html if page_html is None else page_html
        screen = (expected_screen or self.vinted_screen).strip().lower()
        if not self.settings.vinted_datadome_collector_enabled:
            self._emit_event(
                phase="datadome_collector_skipped",
                details={
                    "reason": "disabled",
                    "post_sent": False,
                    "collector_endpoint": collector_url,
                    "bootstrap_origin": bootstrap_origin,
                    "vinted_screen": screen,
                },
            )
            return
        if self._catalog_session_context.datadome:
            self._emit_event(
                phase="datadome_collector_skipped",
                details={
                    "reason": "datadome_already_present",
                    "datadome_cookie": True,
                    "post_sent": False,
                    "collector_endpoint": collector_url,
                    "bootstrap_origin": bootstrap_origin,
                    "vinted_screen": screen,
                    "cookies_after": self._cookie_markers(),
                    "cookie_flags": _cookie_flags_from_values(self._cookie_values()),
                },
            )
            return
        if not html:
            self._emit_event(
                phase="datadome_collector_skipped",
                level="warning",
                details={
                    "reason": "bootstrap_html_missing",
                    "post_sent": False,
                    "collector_endpoint": collector_url,
                    "bootstrap_origin": bootstrap_origin,
                    "vinted_screen": screen,
                },
            )
            return

        report = self._session_context_report(expected_screen=screen, bootstrap_origin=bootstrap_origin)
        missing_without_datadome = [name for name in self._missing_session_context(report) if name != "datadome"]
        if missing_without_datadome:
            self._emit_event(
                phase="datadome_collector_skipped",
                level="warning",
                details={
                    "reason": "base_context_incomplete",
                    "post_sent": False,
                    "collector_endpoint": collector_url,
                    "bootstrap_origin": bootstrap_origin,
                    "vinted_screen": screen,
                    "missing_required": missing_without_datadome,
                    **report,
                },
            )
            return

        self._emit_event(
            phase="datadome_collector_start",
            details={
                "timeout_ms": self.timeout_ms,
                "request_profile": "datadome_collector",
                "collector_endpoint": collector_url,
                "post_sent": False,
                "browser_profile": self.profile.name,
                "impersonate": self.profile.impersonate,
                "http_session": self._session_marker(),
                "proxy_session": self.proxy_session_marker,
                "locale": self.locale,
                "accept_language": self.accept_language,
                "viewport_size": self.viewport_size,
                "vinted_screen": screen,
                "bootstrap_origin": bootstrap_origin,
                **_context_summary(report, self._cookie_values()),
            },
        )
        datadome_client_key = self.settings.vinted_datadome_client_key or extract_datadome_client_key(html)
        tags_url = extract_datadome_script_url(html, source_url)
        if not datadome_client_key and tags_url:
            datadome_client_key = self._fetch_datadome_client_key_from_tags(tags_url=tags_url, source_url=source_url)
        started_at = time.perf_counter()
        result = DataDomeCookieCollector(
            session=self._session,
            profile=self.profile,
            collector_url=collector_url,
            source_url=source_url,
            page_html=html,
            accept_language=self.accept_language,
            locale=self.locale,
            viewport_size=self.viewport_size,
            vinted_screen=screen,
            timeout_seconds=self.timeout_ms / 1000,
            default_ddv=self.settings.vinted_datadome_collector_default_ddv,
            configured_client_key=datadome_client_key,
            event_sink=self._emit_event,
        ).collect()
        if result.success:
            self._catalog_session_context.datadome = result.datadome_cookie or self._cookie_value("datadome")
            self._emit_event(
                phase="datadome_collector_success",
                duration_ms=_elapsed_ms(started_at),
                details={
                    **result.safe_details(),
                    "context": self._session_context_report(expected_screen=screen, bootstrap_origin=bootstrap_origin),
                    "collector_endpoint": collector_url,
                    "bootstrap_origin": bootstrap_origin,
                    "vinted_screen": screen,
                    "non_blocking": not self.require_datadome_cookie,
                    "cookies_after": self._cookie_markers(),
                    "cookie_flags": _cookie_flags_from_values(self._cookie_values()),
                },
            )
            return

        self._emit_event(
            phase="datadome_collector_failed",
            duration_ms=_elapsed_ms(started_at),
            level="warning",
            message="DataDome collector did not return a cookie",
            details={
                **result.safe_details(),
                "context": self._session_context_report(expected_screen=screen, bootstrap_origin=bootstrap_origin),
                "collector_endpoint": collector_url,
                "bootstrap_origin": bootstrap_origin,
                "vinted_screen": screen,
                "non_blocking": not self.require_datadome_cookie,
                "cookies_after": self._cookie_markers(),
                "cookie_flags": _cookie_flags_from_values(self._cookie_values()),
            },
        )

    def _fetch_datadome_client_key_from_tags(self, *, tags_url: str, source_url: str) -> str | None:
        assert self._session is not None
        headers = build_datadome_tags_headers(
            source_url=source_url,
            profile=self.profile,
            accept_language=self.accept_language,
        )
        self._emit_event(
            phase="datadome_tags_request_start",
            method="GET",
            url=tags_url,
            details={
                "browser_profile": self.profile.name,
                "impersonate": self.profile.impersonate,
                "http_session": self._session_marker(),
                "proxy_session": self.proxy_session_marker,
                "request_headers": safe_headers(headers),
                "cookies_before": self._cookie_markers(),
                "default_headers": False,
            },
        )
        started_at = time.perf_counter()
        try:
            response = self._session.get(
                tags_url,
                headers=dict(headers),
                timeout=self.timeout_ms / 1000,
                default_headers=False,
            )
        except Exception as exc:
            self._emit_event(
                phase="datadome_tags_request_error",
                method="GET",
                url=tags_url,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message=str(exc),
                details={
                    "request_headers": safe_headers(headers),
                    "cookies_after": self._cookie_markers(),
                },
            )
            return None

        script_text = response.text or ""
        ddk = extract_datadome_client_key(script_text)
        ddv = extract_datadome_tags_version(tags_url) or self.settings.vinted_datadome_collector_default_ddv
        self._emit_event(
            phase="datadome_tags_request_success" if response.status_code < 400 else "datadome_tags_request_error",
            method="GET",
            url=tags_url,
            status_code=response.status_code,
            duration_ms=_elapsed_ms(started_at),
            level=None if response.status_code < 400 else "warning",
            details={
                "ddv": ddv,
                "ddk_found": ddk is not None,
                "ddk_length": len(ddk) if ddk else None,
                "script_length": len(script_text),
                "request_headers": safe_headers(headers),
                "response_headers": safe_headers(dict(response.headers)),
                "cookies_after": self._cookie_markers(),
            },
        )
        return ddk if response.status_code < 400 else None

    def _build_catalog_session_context(self, response: Any) -> CatalogSessionContext:
        headers = dict(response.headers)
        return CatalogSessionContext(
            csrf_token=extract_csrf_token(response.text)
            or self._cookie_value("csrf_token")
            or self._cookie_value("_csrf_token"),
            anon_id=_header_value(headers, "x-anon-id") or self._cookie_value("anon_id"),
            access_token_web=self._cookie_value("access_token_web"),
            datadome=self._cookie_value("datadome"),
            cf_bm=self._cookie_value("__cf_bm"),
            v_udt=_header_value(headers, "x-v-udt") or self._cookie_value("v_udt"),
            user_iso_locale=_header_value(headers, "x-user-iso-locale"),
            screen=_header_value(headers, "x-screen"),
        )

    def _session_context_report(
        self,
        *,
        expected_screen: str | None = None,
        bootstrap_origin: str = "catalog_document",
    ) -> dict[str, Any]:
        context = self._catalog_session_context
        expected_vinted_screen = (expected_screen or self.vinted_screen).strip().lower()
        response_locale = _normalize_country_code(context.user_iso_locale)
        expected_country_code = self.expected_country_code
        egress_country_code = _normalize_country_code(self._egress_context.country_code)
        locale_country = _country_from_locale(self.locale)
        viewport_configured = bool(self.viewport_size and VIEWPORT_PATTERN.match(self.viewport_size.strip().lower()))
        vinted_screen_configured = bool(expected_vinted_screen in {"catalog", "item"})
        response_screen_matches = bool(context.screen and context.screen.strip().lower() == expected_vinted_screen)
        response_locale_matches = bool(response_locale and (expected_country_code is None or response_locale == expected_country_code))

        return {
            "browser_profile": self.profile.name,
            "impersonate": self.profile.impersonate,
            "impersonate_ready": self.profile.impersonate.startswith("chrome"),
            "bootstrap_origin": bootstrap_origin,
            "expected_country_code": expected_country_code,
            "egress_ip": self._egress_context.ip,
            "egress_country": self._egress_context.country,
            "egress_country_code": egress_country_code,
            "egress_country_match": expected_country_code is None or egress_country_code == expected_country_code,
            "egress_asn": self._egress_context.asn,
            "egress_org": self._egress_context.org,
            "csrf_token_found": context.csrf_token is not None,
            "anon_id_found": context.anon_id is not None,
            "access_token_found": context.access_token_web is not None,
            "datadome_cookie": context.datadome is not None,
            "cf_bm_cookie": (context.cf_bm is not None) or bool(self._cookie_value("__cf_bm")),
            "v_udt_found": context.v_udt is not None,
            "locale": self.locale,
            "locale_configured": bool(self.locale and locale_country == expected_country_code),
            "accept_language": self.accept_language,
            "accept_language_configured": _accept_language_configured(self.accept_language),
            "viewport_size": self.viewport_size,
            "viewport_configured": viewport_configured,
            "vinted_screen": expected_vinted_screen,
            "vinted_screen_configured": vinted_screen_configured,
            "response_locale": context.user_iso_locale,
            "response_locale_matches": response_locale_matches,
            "response_screen": context.screen,
            "response_screen_matches": response_screen_matches,
            "csrf_token": _secret_marker_or_none("csrf_token", context.csrf_token),
            "anon_id": _secret_marker_or_none("anon_id", context.anon_id),
            "access_token_web": _secret_marker_or_none("access_token_web", context.access_token_web),
            "datadome": _secret_marker_or_none("datadome", context.datadome),
            "cf_bm": _secret_marker_or_none("__cf_bm", context.cf_bm or self._cookie_value("__cf_bm")),
            "v_udt": _secret_marker_or_none("v_udt", context.v_udt),
            "cookies_after_bootstrap": self._cookie_markers(),
            **_context_summary(
                {
                    "csrf_token_found": context.csrf_token is not None,
                    "anon_id_found": context.anon_id is not None,
                    "access_token_found": context.access_token_web is not None,
                    "datadome_cookie": context.datadome is not None,
                    "v_udt_found": context.v_udt is not None,
                    "response_locale_matches": response_locale_matches,
                    "response_screen_matches": response_screen_matches,
                },
                self._cookie_values(),
            ),
        }

    def _missing_session_context(self, report: Mapping[str, Any]) -> list[str]:
        missing: list[str] = []
        required_truthy_flags = {
            "impersonate": report.get("impersonate_ready"),
            "csrf_token": report.get("csrf_token_found"),
            "anon_id": report.get("anon_id_found"),
            "access_token_web": report.get("access_token_found"),
            "v_udt": report.get("v_udt_found"),
            "locale": report.get("locale_configured"),
            "accept_language": report.get("accept_language_configured"),
            "vinted_screen": report.get("vinted_screen_configured"),
            "viewport": report.get("viewport_configured"),
            "egress_country_code": report.get("egress_country_match") and bool(report.get("egress_country_code")),
            "response_locale": report.get("response_locale_matches"),
            "response_screen": report.get("response_screen_matches"),
        }
        if self.require_datadome_cookie:
            required_truthy_flags["datadome"] = report.get("datadome_cookie")
            required_truthy_flags["cf_bm"] = report.get("cf_bm_cookie")
        for name, present in required_truthy_flags.items():
            if not present:
                missing.append(name)
        return missing

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
        url = str(self.settings.egress_diagnostic_url)
        proxy_dict = {"https": self.proxy_url, "http": self.proxy_url} if self.proxy_url else None
        diagnostic_session = self.session_factory(
            impersonate=self.profile.impersonate,
            proxies=proxy_dict,
        )
        headers = {"accept": "application/json", "user-agent": self.profile.user_agent}
        self._emit_event(
            phase="egress_diagnostic_start",
            method="GET",
            url=url,
            details={
                "attempt": attempt,
                "diagnostic_session": "isolated",
                "vinted_http_session": self._session_marker(),
                "proxy_configured": bool(self.proxy_url),
                "proxy_session": self.proxy_session_marker,
                "request_headers": safe_headers(headers),
                "cookies_sent": False,
                "default_headers": False,
            },
        )
        started_at = time.perf_counter()
        try:
            response = diagnostic_session.get(
                url,
                headers=headers,
                timeout=self.timeout_ms / 1000,
                default_headers=False,
            )
            payload = response.json() if "json" in str(response.headers.get("content-type", "")).lower() else {}
            self._egress_context = _egress_context_from_payload(payload)
            details = {
                "attempt": attempt,
                "diagnostic_session": "isolated",
                "vinted_http_session": self._session_marker(),
                "proxy_configured": bool(self.proxy_url),
                "proxy_session": self.proxy_session_marker,
                "egress": _egress_details_from_payload(payload),
                "response_headers": safe_headers(dict(response.headers)),
                "diagnostic_cookies_after": safe_cookie_markers(diagnostic_session.cookies),
                "vinted_cookies_after": self._cookie_markers(),
                "cookies_sent": False,
                "default_headers": False,
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
                    "diagnostic_session": "isolated",
                    "vinted_http_session": self._session_marker(),
                    "proxy_configured": bool(self.proxy_url),
                    "proxy_session": self.proxy_session_marker,
                    "cookies_sent": False,
                    "default_headers": False,
                },
            )
        finally:
            close = getattr(diagnostic_session, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            self._egress_diagnosed = True

    def _session_marker(self) -> dict[str, Any]:
        return safe_secret_marker("http_session_id", self.http_session_id, kind="http_session")

    def _cookie_markers(self) -> list[dict[str, Any]]:
        if self._session is None:
            return []
        return safe_cookie_markers(self._session.cookies)

    def _cookie_values(self) -> dict[str, str]:
        if self._session is None or not self._session.cookies:
            return {}
        cookies = self._session.cookies
        jar = getattr(cookies, "jar", None)
        if jar is not None:
            values: dict[str, str] = {}
            for cookie in jar:
                cookie_name = getattr(cookie, "name", None)
                cookie_value = getattr(cookie, "value", None)
                cookie_domain = getattr(cookie, "domain", None)
                if cookie_name and cookie_value and _is_vinted_cookie_domain(cookie_domain):
                    values[str(cookie_name)] = str(cookie_value)
            return values

        items = getattr(cookies, "items", None)
        if callable(items):
            try:
                return {str(name): str(value) for name, value in items() if value}
            except Exception:
                pass
        return {}

    def _session_context_values(self) -> dict[str, str | None]:
        return {
            "csrf_token": self._catalog_session_context.csrf_token,
            "anon_id": self._catalog_session_context.anon_id,
            "access_token_web": self._catalog_session_context.access_token_web,
            "datadome": self._catalog_session_context.datadome,
            "cf_bm": self._catalog_session_context.cf_bm,
            "v_udt": self._catalog_session_context.v_udt,
            "user_iso_locale": self._catalog_session_context.user_iso_locale,
        }

    def _refresh_session_context_from_detail_response(self, response: Any) -> list[str]:
        before = self._session_context_values()
        headers = dict(response.headers)
        values = {
            "csrf_token": extract_csrf_token(response.text)
            or self._cookie_value("csrf_token")
            or self._cookie_value("_csrf_token"),
            "anon_id": _header_value(headers, "x-anon-id") or self._cookie_value("anon_id"),
            "access_token_web": self._cookie_value("access_token_web"),
            "datadome": self._cookie_value("datadome"),
            "cf_bm": self._cookie_value("__cf_bm"),
            "v_udt": _header_value(headers, "x-v-udt") or self._cookie_value("v_udt"),
            "user_iso_locale": _header_value(headers, "x-user-iso-locale"),
        }
        for name, value in values.items():
            if value:
                setattr(self._catalog_session_context, name, value)

        after = self._session_context_values()
        refreshed = sorted(name for name, value in after.items() if value and value != before.get(name))
        if refreshed:
            self.prepared_session_refreshed = True
        return refreshed

    def _refresh_session_context_from_cookies(
        self,
        *,
        cookies_before: Mapping[str, str],
        context_before: Mapping[str, str | None],
    ) -> list[str]:
        cookies_after = self._cookie_values()
        for cookie_name in ("access_token_web", "datadome", "v_udt"):
            value = cookies_after.get(cookie_name)
            if value:
                setattr(self._catalog_session_context, cookie_name, value)

        context_after = self._session_context_values()
        refreshed = {
            name
            for name in SESSION_REFRESH_COOKIE_NAMES
            if cookies_after.get(name) and cookies_after.get(name) != cookies_before.get(name)
        }
        refreshed.update(
            name
            for name, value in context_after.items()
            if value and value != context_before.get(name)
        )
        if refreshed:
            self.prepared_session_refreshed = True
        return sorted(refreshed)

    def _cookie_value(self, name: str) -> str | None:
        if self._session is None or not self._session.cookies:
            return None

        cookies = self._session.cookies
        jar = getattr(cookies, "jar", None)
        if jar is not None:
            for cookie in jar:
                cookie_name = getattr(cookie, "name", None)
                cookie_value = getattr(cookie, "value", None)
                cookie_domain = getattr(cookie, "domain", None)
                if cookie_name == name and cookie_value and _is_vinted_cookie_domain(cookie_domain):
                    return str(cookie_value)
            return None

        get_value = getattr(cookies, "get", None)
        if callable(get_value):
            try:
                value = get_value(name)
            except Exception:
                value = None
            if value:
                return str(value)

        return None


def _context_summary(report: Mapping[str, Any], cookie_values: Mapping[str, str]) -> dict[str, Any]:
    recovered: list[str] = []
    missing: list[str] = []
    checks = [
        ("csrf_token_found", "csrf"),
        ("anon_id_found", "anon_id"),
        ("access_token_found", "access_token_web"),
        ("v_udt_found", "v_udt"),
        ("datadome_cookie", "datadome"),
    ]
    for key, label in checks:
        if report.get(key):
            recovered.append(label)
        else:
            missing.append(label)

    for cookie_name in ("__cf_bm", "v_sid", "_vinted_fr_session"):
        if cookie_values.get(cookie_name):
            recovered.append(cookie_name)
        else:
            missing.append(cookie_name)

    locale_ok = bool(report.get("response_locale_matches") or report.get("locale_configured"))
    screen_ok = bool(report.get("response_screen_matches") or report.get("vinted_screen_configured"))
    if locale_ok:
        recovered.append("locale")
    else:
        missing.append("locale")
    if screen_ok:
        recovered.append("x_screen")
    else:
        missing.append("x_screen")

    return {
        "recovered_context": recovered,
        "missing_context": missing,
        "cookie_flags": _cookie_flags_from_values(cookie_values),
        "cf_bm_cookie": bool(cookie_values.get("__cf_bm")),
        "v_sid_cookie": bool(cookie_values.get("v_sid")),
        "vinted_fr_session_cookie": bool(cookie_values.get("_vinted_fr_session")),
    }


def _cookie_flags_from_values(cookie_values: Mapping[str, str]) -> list[str]:
    safe_names = (
        "__cf_bm",
        "datadome",
        "v_sid",
        "_vinted_fr_session",
        "access_token_web",
        "refresh_token_web",
        "anon_id",
        "v_udt",
        "anonymous-iso-locale",
    )
    return [name for name in safe_names if cookie_values.get(name)]


def _response_summary(headers: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "cf_ray": _header_value(headers, "cf-ray"),
            "cf_cache_status": _header_value(headers, "cf-cache-status"),
            "cf_mitigated": _header_value(headers, "cf-mitigated"),
            "request_id": _header_value(headers, "x-request-id"),
            "upstream_ms": _header_value(headers, "x-envoy-upstream-service-time"),
            "accept_ch": _header_value(headers, "accept-ch"),
            "critical_ch": _header_value(headers, "critical-ch"),
        }.items()
        if value
    }


def _api_param_summary(params: Mapping[str, Any]) -> dict[str, str]:
    summary_keys = ("catalog_ids", "brand_ids", "status_ids", "size_ids", "price_to", "currency", "page", "per_page", "order")
    summary: dict[str, str] = {}
    for key in summary_keys:
        value = params.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple)):
            summary[key] = "|".join(str(part) for part in value)
        else:
            summary[key] = str(value)
    return summary


def is_cloudflare_challenge(status_code: int, headers: Mapping[str, Any]) -> bool:
    if status_code < 400:
        return False
    mitigated = _header_value(headers, "cf-mitigated")
    return bool(mitigated and mitigated.strip().lower() == "challenge")


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


def _egress_context_from_payload(payload: Any) -> EgressContext:
    details = _egress_details_from_payload(payload)
    return EgressContext(
        ip=_optional_str(details.get("ip")),
        country=_optional_str(details.get("country")),
        country_code=_normalize_country_code(details.get("country_code")),
        asn=details.get("asn"),
        org=_optional_str(details.get("org")),
    )


def _normalize_country_code(value: Any) -> str | None:
    if not value:
        return None
    cleaned = str(value).strip().upper()
    if len(cleaned) != 2:
        if "-" in cleaned:
            return _normalize_country_code(cleaned.rsplit("-", 1)[-1])
        return None
    return cleaned


def _country_from_locale(value: str | None) -> str | None:
    if not value or "-" not in value:
        return None
    return _normalize_country_code(value.rsplit("-", 1)[-1])


def _accept_language_configured(value: str | None) -> bool:
    if not value or not value.strip():
        return False
    return any(chunk.split(";", 1)[0].strip() for chunk in value.split(","))


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


def _retry_after_seconds(value: str | None, *, now: datetime | None = None) -> tuple[float | None, str]:
    if value is None or not str(value).strip():
        return DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS, "missing"

    text = str(value).strip()
    try:
        seconds = float(text)
    except ValueError:
        seconds = None
    if seconds is not None:
        return max(seconds, 0.0), "seconds"

    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None, "invalid"

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return max((retry_at - current).total_seconds(), 0.0), "http_date"


def _rate_limit_jitter_seconds() -> float:
    return random.uniform(0.25, 0.75)


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


def extract_vinted_item_id(item_ref: str) -> str | None:
    text = str(item_ref or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    parsed = urlparse(text)
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) >= 2 and path_parts[0] == "items":
        candidate = path_parts[1].split("-", 1)[0]
        return candidate if candidate.isdigit() else None
    match = re.search(r"/items/(\d+)", text)
    if match:
        return match.group(1)
    return None


def build_item_detail_navigation_url(item_url: str) -> str:
    parsed = _validate_item_detail_url(item_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(name == "referrer" for name, _ in query):
        query.append(("referrer", "catalog"))
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def _validate_vinted_response_url(url: str, *, expected_path: str):
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Vinted response URL has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.vinted.es", "vinted.es"}
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.path.rstrip("/") != expected_path.rstrip("/")
    ):
        raise ValueError("Vinted response URL left the expected HTTPS endpoint")
    return parsed


def _is_vinted_cookie_domain(domain: Any) -> bool:
    normalized = str(domain or "").lstrip(".").lower()
    return normalized in {"vinted.es", "www.vinted.es"}


def _validate_item_detail_url(item_url: str, *, expected_item_id: str | None = None):
    parsed = urlparse(item_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.vinted.es", "vinted.es"}
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or not VINTED_ITEM_PATH_PATTERN.fullmatch(parsed.path)
    ):
        raise ValueError("Item detail URL must be an HTTPS Vinted ES item URL")
    observed_item_id = VINTED_ITEM_PATH_PATTERN.fullmatch(parsed.path).group(1)
    if observed_item_id is None or (expected_item_id is not None and observed_item_id != expected_item_id):
        raise ValueError("Item detail URL must reference the requested Vinted item")
    return parsed


def parse_item_detail_html(html: str, candidate: CatalogItemCandidate) -> CatalogItemDetail:
    product_data = extract_product_json_ld(html, item_id=candidate.vinted_item_id)
    flight_records = parse_next_flight_records(decode_next_flight_payload(html))
    flight = _extract_item_flight_parts(flight_records, candidate.vinted_item_id)
    if product_data and not _json_ld_identity_urls(product_data) and not flight["matched"]:
        product_data = {}
    if not product_data and not flight["matched"]:
        raise ValueError("No public item detail data found in item document")

    rich_item = flight["rich_item"]
    plugins = flight["plugins"]
    item_status = _plugin_data(plugins, "item_status")
    summary = _plugin_data(plugins, "summary")
    attributes = _attribute_values(_plugin_data(plugins, "attributes"))
    summary_attributes = _summary_attributes(summary)
    description = _plugin_data(plugins, "description")
    make_offer = _plugin_data(plugins, "make_offer")
    ask_seller = _plugin_data(plugins, "ask_seller")
    seller_badges_info = _plugin_data(plugins, "seller_badges_info")
    user_info = _plugin_data(plugins, "user_info_header")
    offers = product_data.get("offers") if isinstance(product_data.get("offers"), Mapping) else {}
    product_brand = product_data.get("brand") if isinstance(product_data.get("brand"), Mapping) else {}

    values: dict[str, Any] = {}
    observed_fields: set[str] = set()
    field_sources: dict[str, str] = {}

    def choose(name: str, *options: tuple[str, bool, Any], allow_empty: bool = False) -> None:
        for source, present, value in options:
            if present:
                observed_fields.add(name)
            if not present or value is None or (not allow_empty and value == ""):
                continue
            values[name] = value
            field_sources[name] = source
            return

    choose(
        "title",
        ("flight.rich_item", "title" in rich_item, _optional_str(rich_item.get("title"))),
        ("flight.make_offer", "title" in make_offer, _optional_str(make_offer.get("title"))),
        ("flight.summary", bool(summary), _summary_title(summary)),
        ("json_ld", "name" in product_data, _optional_str(product_data.get("name"))),
        ("catalog", bool(candidate.title), candidate.title),
    )
    choose(
        "brand",
        ("flight.attributes", "brand" in attributes, attributes.get("brand")),
        ("flight.summary", "brand" in summary_attributes, summary_attributes.get("brand")),
        ("json_ld", "name" in product_brand, _optional_str(product_brand.get("name"))),
        ("catalog", candidate.brand is not None, candidate.brand),
    )
    choose(
        "size",
        ("flight.attributes", "size" in attributes, attributes.get("size")),
        ("flight.summary", "size" in summary_attributes, summary_attributes.get("size")),
        ("catalog", candidate.size is not None, candidate.size),
    )
    choose(
        "status",
        ("flight.attributes", "status" in attributes, attributes.get("status")),
        ("flight.summary", "status" in summary_attributes, summary_attributes.get("status")),
        ("catalog", candidate.status is not None, candidate.status),
    )
    choose(
        "description",
        ("flight.description", "description" in description, _optional_str(description.get("description"))),
        ("json_ld", "description" in product_data, _optional_str(product_data.get("description"))),
        allow_empty=True,
    )
    choose(
        "color",
        ("flight.attributes", "color" in attributes, attributes.get("color")),
        ("json_ld", "color" in product_data, _optional_str(product_data.get("color"))),
    )
    choose(
        "category",
        ("json_ld", "category" in product_data, _optional_str(product_data.get("category"))),
    )

    rich_price = _money_parts(rich_item.get("price"))
    offer_price = _money_parts(make_offer.get("price"))
    pricing_price = _money_parts(flight["pricing_services"].get("originalAskingAmount"))
    json_ld_price = (
        "price" in offers,
        _optional_decimal(offers.get("price")),
        _optional_str(offers.get("priceCurrency")),
    )
    catalog_price = (candidate.price_amount is not None, candidate.price_amount, candidate.currency)
    price_options = (
        ("flight.rich_item", *rich_price),
        ("flight.make_offer", *offer_price),
        ("flight.pricing", *pricing_price),
        ("json_ld", *json_ld_price),
        ("catalog", *catalog_price),
    )
    for source, present, amount, currency in price_options:
        if present:
            observed_fields.update({"price_amount", "currency"})
        if not present or amount is None or not currency:
            continue
        values["price_amount"] = amount
        values["currency"] = currency
        field_sources["price_amount"] = source
        field_sources["currency"] = source
        break

    photo_candidates = (
        ("flight.rich_item", "photos" in rich_item, _resolve_flight_value(rich_item.get("photos"), flight_records)),
        ("flight.make_offer", "photos" in make_offer, _resolve_flight_value(make_offer.get("photos"), flight_records)),
        ("json_ld", "image" in product_data, product_data.get("image")),
        ("catalog", candidate.image_url is not None, candidate.image_url),
    )
    photos: list[str] = []
    for source, present, raw_photos in photo_candidates:
        if present:
            observed_fields.add("photos")
        if not present:
            continue
        photos = _extract_vinted_photo_urls(raw_photos)
        if photos:
            field_sources["photos"] = source
            break
    values["photos"] = photos

    choose(
        "seller_rating",
        (
            "flight.user_info_header",
            "feedback_reputation" in user_info,
            _optional_decimal(user_info.get("feedback_reputation")),
        ),
    )
    badge_value = seller_badges_info.get("badges")
    seller_badges = _extract_string_list(badge_value)
    if "badges" in seller_badges_info:
        observed_fields.add("seller_badges")
        field_sources["seller_badges"] = "flight.seller_badges_info"

    resolved_currency = values.get("currency")
    shipping_amount, shipping_observed = _shipping_amount(flight["shipping_details"], resolved_currency)
    protection_amount, protection_observed = _pricing_service_amount(
        flight["pricing_services"], "buyerProtection", resolved_currency
    )
    total_amount, total_observed = _validated_total_amount(
        flight["pricing_services"], values.get("price_amount"), resolved_currency
    )
    validation_warnings: list[str] = []
    if shipping_observed and shipping_amount is None:
        validation_warnings.append("shipping_price_invalid")
    if protection_observed and protection_amount is None:
        validation_warnings.append("buyer_protection_price_invalid")
    if total_observed and total_amount is None:
        validation_warnings.append("total_price_invalid")
    choose(
        "shipping_price_amount",
        ("flight.shipping_details", shipping_observed, shipping_amount),
    )
    choose(
        "buyer_protection_fee_amount",
        ("flight.pricing", protection_observed, protection_amount),
    )
    choose(
        "total_price_amount",
        ("flight.pricing", total_observed, total_amount),
    )

    availability_flags = _derive_public_availability(
        rich_item=rich_item,
        item_status=item_status,
        ask_seller=ask_seller,
        shipping_details=flight["shipping_details"],
        shipping_details_observed=flight["shipping_details_observed"],
        shipping_amount=shipping_amount,
        offers=offers,
    )
    if availability_flags:
        observed_fields.add("availability_flags")
        field_sources["availability_flags"] = "flight" if flight["matched"] else "json_ld"

    diagnostic_fields = (
        "title",
        "description",
        "brand",
        "size",
        "status",
        "price_amount",
        "currency",
        "photos",
    )
    missing_fields = [
        name
        for name in diagnostic_fields
        if name not in observed_fields
        or (name == "photos" and not values.get(name))
        or (name not in {"description", "photos"} and values.get(name) in {None, ""})
    ]
    raw = {
        "parser_version": DETAIL_PARSER_VERSION,
        "field_sources": dict(sorted(field_sources.items())),
        "observed_fields": sorted(observed_fields),
        "missing_fields": missing_fields,
        "flight_record_count": len(flight_records),
        "flight_sections": flight["sections"],
        "json_ld_present": bool(product_data),
        "validation_warnings": validation_warnings,
    }

    return CatalogItemDetail(
        vinted_item_id=candidate.vinted_item_id,
        title=values.get("title"),
        brand=values.get("brand"),
        size=values.get("size"),
        status=values.get("status"),
        price_amount=values.get("price_amount"),
        currency=values.get("currency"),
        description=values.get("description"),
        color=values.get("color"),
        category=values.get("category"),
        shipping_price_amount=values.get("shipping_price_amount"),
        buyer_protection_fee_amount=values.get("buyer_protection_fee_amount"),
        total_price_amount=values.get("total_price_amount"),
        photos=photos,
        seller_rating=values.get("seller_rating"),
        seller_badges=seller_badges,
        availability_flags=availability_flags,
        observed_fields=frozenset(observed_fields),
        field_sources=field_sources,
        raw=raw,
    )


def parse_next_flight_records(payload: str) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for line in payload.splitlines():
        record_id, separator, raw_value = line.partition(":")
        if not separator or not NEXT_FLIGHT_RECORD_ID_PATTERN.fullmatch(record_id):
            continue
        try:
            records[record_id] = json.loads(raw_value)
        except json.JSONDecodeError:
            continue
    return records


def _extract_item_flight_parts(records: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    rich_item: Mapping[str, Any] = {}
    plugins: list[Any] = []
    shipping_details: Any = None
    shipping_details_observed = False
    pricing_services: Mapping[str, Any] = {}
    sections: set[str] = set()
    matched = False

    for record in records.values():
        props = _react_props(record)
        if not props:
            continue
        value = _resolve_flight_value(props.get("value"), records)
        if isinstance(value, Mapping) and _same_item_id(value.get("id"), item_id):
            rich_item = value
            sections.add("rich_item")
            matched = True

        plugin_value = _resolve_flight_value(props.get("plugins"), records)
        if _same_item_id(props.get("itemId"), item_id) and isinstance(plugin_value, list):
            plugins = [
                plugin
                for plugin in plugin_value
                if not isinstance(plugin, Mapping)
                or _value_is_unscoped_or_matches_item(
                    _resolve_flight_value(plugin.get("data"), records),
                    item_id,
                )
            ]
            sections.add("plugins")
            matched = True

        scoped_shipping = _find_item_scoped_field(props, "shippingDetails", item_id, records)
        if scoped_shipping is not _MISSING:
            shipping_details_observed = True
            shipping_details = scoped_shipping
            sections.add("shipping_details")
            matched = True

        scoped_pricing = _find_item_scoped_field(props, "pricingServices", item_id, records)
        if isinstance(scoped_pricing, Mapping):
            pricing_services = scoped_pricing
            sections.add("pricing")
            matched = True

    plugin_map: dict[str, Mapping[str, Any]] = {}
    plugin_scope_rank: dict[str, int] = {}
    for plugin in plugins:
        if not isinstance(plugin, Mapping):
            continue
        plugin_type = _optional_str(plugin.get("type"))
        data = _resolve_flight_value(plugin.get("data"), records)
        if plugin_type and isinstance(data, Mapping):
            identities = _item_identity_values(data)
            scope_rank = 1 if identities == {item_id} else 0
            if scope_rank > plugin_scope_rank.get(plugin_type, -1):
                plugin_map[plugin_type] = data
                plugin_scope_rank[plugin_type] = scope_rank

    return {
        "matched": matched,
        "rich_item": rich_item,
        "plugins": plugin_map,
        "shipping_details": shipping_details,
        "shipping_details_observed": shipping_details_observed,
        "pricing_services": pricing_services,
        "sections": sorted(sections),
    }


_MISSING = object()


def _find_item_scoped_field(
    value: Any,
    field_name: str,
    item_id: str,
    records: Mapping[str, Any],
) -> Any:
    candidates: list[tuple[int, int, Any]] = []
    for depth, node in _walk_mappings_with_depth(value):
        if field_name not in node:
            continue
        item_ids = _item_identity_values(node)
        if item_ids != {item_id}:
            continue
        resolved = _resolve_flight_value(node.get(field_name), records)
        candidates.append((_mapping_size(node), -depth, resolved))
    if not candidates:
        return _MISSING
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    return candidates[0][2]


def _value_is_unscoped_or_matches_item(value: Any, item_id: str) -> bool:
    item_ids = _item_identity_values(value)
    return not item_ids or item_ids == {item_id}


def _item_identity_values(value: Any) -> set[str]:
    identities: set[str] = set()
    for mapping in _walk_mappings(value):
        for key in ("item_id", "itemId"):
            if mapping.get(key) is not None:
                identities.add(str(mapping[key]))
        if mapping.get("id") is not None and any(
            marker in mapping for marker in ("title", "photos", "price", "can_buy", "instant_buy")
        ):
            identities.add(str(mapping["id"]))
    return identities


def _mapping_size(value: Any) -> int:
    if isinstance(value, Mapping):
        return 1 + sum(_mapping_size(child) for child in value.values())
    if isinstance(value, list):
        return 1 + sum(_mapping_size(child) for child in value)
    return 1


def _react_props(record: Any) -> Mapping[str, Any]:
    if isinstance(record, list) and len(record) >= 4 and isinstance(record[3], Mapping):
        return record[3]
    return {}


def _resolve_flight_value(value: Any, records: Mapping[str, Any]) -> Any:
    current = value
    for _ in range(3):
        if not isinstance(current, str) or not current.startswith("$") or current == "$undefined":
            return current
        parts = current[1:].split(":")
        if len(parts) < 2 or parts[1] != "props":
            return current
        resolved: Any = _react_props(records.get(parts[0]))
        if not resolved:
            return current
        for part in parts[2:]:
            if isinstance(resolved, Mapping) and part in resolved:
                resolved = resolved[part]
            elif isinstance(resolved, list) and part.isdigit() and int(part) < len(resolved):
                resolved = resolved[int(part)]
            else:
                return current
        current = resolved
    return current


def _walk_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _walk_mappings_with_depth(value: Any, depth: int = 0):
    if isinstance(value, Mapping):
        yield depth, value
        for child in value.values():
            yield from _walk_mappings_with_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings_with_depth(child, depth + 1)


def _find_mapping_with_key(value: Any, key: str) -> Mapping[str, Any] | None:
    return next((mapping for mapping in _walk_mappings(value) if key in mapping), None)


def _contains_item_id(value: Any, item_id: str) -> bool:
    return any(
        _same_item_id(mapping.get(key), item_id)
        for mapping in _walk_mappings(value)
        for key in ("id", "item_id", "itemId")
        if key in mapping
    )


def _same_item_id(value: Any, item_id: str) -> bool:
    return value is not None and str(value) == item_id


def _json_ld_matches_item(product_data: Mapping[str, Any], item_id: str) -> bool:
    identity_urls = _json_ld_identity_urls(product_data)
    if not identity_urls:
        return False
    observed_ids = [_strict_vinted_item_id(url) for url in identity_urls]
    return all(observed_id == item_id for observed_id in observed_ids)


def _json_ld_identity_urls(product_data: Mapping[str, Any]) -> list[str]:
    offers = product_data.get("offers") if isinstance(product_data.get("offers"), Mapping) else {}
    return [str(url) for url in (product_data.get("url"), offers.get("url")) if url]


def _strict_vinted_item_id(url: str) -> str | None:
    try:
        parsed = _validate_item_detail_url(url)
    except (TypeError, ValueError):
        return None
    match = VINTED_ITEM_PATH_PATTERN.fullmatch(parsed.path)
    return match.group(1) if match else None


def _plugin_data(plugins: Mapping[str, Mapping[str, Any]], plugin_type: str) -> Mapping[str, Any]:
    return plugins.get(plugin_type, {})


def _attribute_values(data: Mapping[str, Any]) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    attributes = data.get("attributes")
    if not isinstance(attributes, list):
        return values
    for attribute in attributes:
        if not isinstance(attribute, Mapping):
            continue
        code = _optional_str(attribute.get("code"))
        attribute_data = attribute.get("data") if isinstance(attribute.get("data"), Mapping) else {}
        if code:
            values[code] = _optional_str(attribute_data.get("value"))
    return values


def _summary_title(summary: Mapping[str, Any]) -> str | None:
    for mapping in _walk_mappings(summary.get("lines")):
        if mapping.get("style") == "title" and mapping.get("value") is not None:
            return _optional_str(mapping.get("value"))
    return None


def _summary_attributes(summary: Mapping[str, Any]) -> dict[str, str]:
    for line in summary.get("lines") if isinstance(summary.get("lines"), list) else []:
        if not isinstance(line, Mapping) or not isinstance(line.get("elements"), list):
            continue
        elements = line["elements"]
        brand_index = next(
            (
                index
                for index, element in enumerate(elements)
                if isinstance(element, Mapping) and element.get("code") == "summary_brand"
            ),
            None,
        )
        if brand_index is None:
            continue
        brand_element = elements[brand_index]
        preceding_values = [
            value
            for element in elements[:brand_index]
            if isinstance(element, Mapping)
            and element.get("type") == "text"
            and element.get("style") == "body"
            for value in [_optional_str(element.get("value"))]
            if value
        ]
        values: dict[str, str] = {}
        brand = _optional_str(brand_element.get("value"))
        if brand:
            values["brand"] = brand
        if len(preceding_values) >= 2:
            values["size"] = preceding_values[0]
            values["status"] = preceding_values[1]
        return values
    return {}


def _money_parts(value: Any) -> tuple[bool, Decimal | None, str | None]:
    if not isinstance(value, Mapping):
        return False, None, None
    amount = _optional_decimal(value.get("amount"))
    currency = _optional_str(value.get("currency_code") or value.get("currencyCode"))
    return "amount" in value, amount, currency


def _extract_vinted_photo_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates: list[Any] = [value]
    elif isinstance(value, Mapping):
        candidates = [value]
    elif isinstance(value, list):
        candidates = sorted(
            enumerate(value),
            key=lambda entry: (
                _optional_int(entry[1].get("image_no")) if isinstance(entry[1], Mapping) else None
            )
            or (1_000_000 + entry[0]),
        )
        candidates = [entry for _, entry in candidates]
    else:
        return []

    photos: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            if candidate.get("is_hidden") is True:
                continue
            url = candidate.get("url")
        else:
            url = candidate
        if isinstance(url, str) and _is_allowed_vinted_photo_url(url):
            photos.append(url)
    return list(dict.fromkeys(photos))


def _is_allowed_vinted_photo_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return False
    signature = dict(parse_qsl(parsed.query, keep_blank_values=True)).get("s")
    return bool(
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port is None
        and parsed.hostname
        and VINTED_IMAGE_HOST_PATTERN.fullmatch(parsed.hostname)
        and signature
    )


def _shipping_amount(shipping_details: Any, currency: str | None) -> tuple[Decimal | None, bool]:
    if not isinstance(shipping_details, Mapping):
        return None, shipping_details is not None
    price = shipping_details.get("price")
    present, amount, price_currency = _money_parts(price)
    if present and _currency_matches(price_currency, currency):
        return amount, True
    if shipping_details.get("isFreeShipping") is True:
        return Decimal("0"), True
    return None, "price" in shipping_details


def _pricing_service_amount(
    pricing_services: Mapping[str, Any], service_name: str, currency: str | None
) -> tuple[Decimal | None, bool]:
    services = pricing_services.get("services") if isinstance(pricing_services.get("services"), Mapping) else {}
    service = services.get(service_name) if isinstance(services.get(service_name), Mapping) else {}
    present, amount, service_currency = _money_parts(service.get("finalPrice"))
    return (amount if present and _currency_matches(service_currency, currency) else None, present)


def _validated_total_amount(
    pricing_services: Mapping[str, Any], base_amount: Decimal | None, currency: str | None
) -> tuple[Decimal | None, bool]:
    present, amount, total_currency = _money_parts(pricing_services.get("totalAmount"))
    valid = present and _currency_matches(total_currency, currency)
    if valid and base_amount is not None and amount is not None and amount < base_amount:
        valid = False
    return (amount if valid else None, present)


def _currency_matches(value: str | None, expected: str | None) -> bool:
    return bool(value and expected and value.upper() == expected.upper())


def _derive_public_availability(
    *,
    rich_item: Mapping[str, Any],
    item_status: Mapping[str, Any],
    ask_seller: Mapping[str, Any],
    shipping_details: Any,
    shipping_details_observed: bool,
    shipping_amount: Decimal | None,
    offers: Mapping[str, Any],
) -> dict[str, Any]:
    flags: dict[str, Any] = {"source": "public_snapshot"}
    sources = (item_status, ask_seller, rich_item)
    positive_flags = ("can_buy", "instant_buy", "transaction_permitted")
    blocking_flags = ("is_closed", "is_hidden", "is_reserved", "is_draft", "is_processing")
    for name in positive_flags:
        observed = [source.get(name) for source in sources if name in source]
        if observed:
            if any(value is False for value in observed):
                flags[name] = False
            elif all(value is True for value in observed):
                flags[name] = True
            else:
                flags[name] = None
    for name in blocking_flags:
        observed = [source.get(name) for source in sources if name in source]
        if observed:
            if any(value is True for value in observed):
                flags[name] = True
            elif all(value is False for value in observed):
                flags[name] = False
            else:
                flags[name] = None
    reservations = [source.get("reservation") for source in sources if "reservation" in source]
    if reservations:
        flags["has_reservation"] = any(_has_active_reservation(reservation) for reservation in reservations)
    if "availability" in offers:
        flags["availability"] = offers.get("availability")
    if shipping_details_observed:
        flags["shipping_available"] = isinstance(shipping_details, Mapping) and shipping_amount is not None

    availability = str(flags.get("availability") or "").lower()
    out_of_stock = availability.endswith("outofstock") or availability.endswith("soldout")
    schema_reserved = availability.endswith("reserved")
    blockers = [
        ("processing", flags.get("is_processing") is True),
        ("draft", flags.get("is_draft") is True),
        ("closed", flags.get("is_closed") is True),
        ("hidden", flags.get("is_hidden") is True),
        (
            "reserved",
            flags.get("is_reserved") is True or flags.get("has_reservation") is True or schema_reserved,
        ),
        ("out_of_stock", out_of_stock),
        ("shipping_unavailable", shipping_details_observed and not flags.get("shipping_available")),
        ("not_permitted", flags.get("transaction_permitted") is False),
        ("not_buyable", flags.get("can_buy") is False or flags.get("instant_buy") is False),
    ]
    reason_codes = [reason for reason, blocked in blockers if blocked]
    if reason_codes:
        state = "not_buyable" if reason_codes[0] == "out_of_stock" else reason_codes[0]
    elif (
        flags.get("can_buy") is True
        and flags.get("instant_buy") is True
        and flags.get("transaction_permitted") is True
        and flags.get("is_closed") is False
        and flags.get("is_hidden") is False
        and flags.get("is_reserved") is False
        and flags.get("has_reservation") is False
        and flags.get("is_draft") is False
        and flags.get("is_processing") is False
        and flags.get("shipping_available") is True
        and not out_of_stock
    ):
        state = "buyable"
    else:
        state = "unknown"
    flags["state"] = state
    flags["reason_codes"] = reason_codes if reason_codes else ([] if state == "buyable" else [state])
    return flags


def _has_active_reservation(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        return bool(value)
    return bool(value)


def extract_product_json_ld(html: str, *, item_id: str | None = None) -> dict[str, Any]:
    products: list[dict[str, Any]] = []
    for match in JSON_LD_PATTERN.findall(html):
        try:
            parsed = json.loads(match.strip())
        except json.JSONDecodeError:
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph")
            graph_candidates = graph if isinstance(graph, list) else []
            for product in [candidate, *graph_candidates]:
                if isinstance(product, dict) and product.get("@type") == "Product":
                    products.append(product)
    if item_id is None:
        return products[0] if products else {}
    for product in products:
        if _json_ld_matches_item(product, item_id):
            return product
    unscoped_products = [product for product in products if not _json_ld_identity_urls(product)]
    if len(unscoped_products) == 1:
        return unscoped_products[0]
    return {}


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
                label = entry.get("name") or entry.get("title") or entry.get("code") or entry.get("type")
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
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


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
