from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.redaction import safe_cookie_markers
from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogItemDetail, CatalogSearchResult

NEXT_FLIGHT_CHUNK_PATTERN = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)
JSON_LD_PATTERN = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)


class VintedCatalogProviderError(RuntimeError):
    pass


class VintedCatalogSessionError(VintedCatalogProviderError):
    pass


class ProviderEventSink(Protocol):
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


class HttpVintedCatalogProvider:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
        proxy_url: str | None = None,
        timeout_ms: int | None = None,
        catalog_per_page: int | None = None,
        request_retries: int = 1,
        event_sink: ProviderEventSink | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport
        self.proxy_url = proxy_url
        self.timeout_ms = timeout_ms or self.settings.vinted_request_timeout_ms
        self.catalog_per_page = catalog_per_page or self.settings.vinted_fast_catalog_per_page
        self.request_retries = max(request_retries, 0)
        self.event_sink = event_sink
        self._cookies = httpx.Cookies()

    def search(self, source: Any, page: int | None = None) -> CatalogSearchResult:
        last_error: VintedCatalogProviderError | None = None
        for retry_index in range(self.request_retries + 1):
            with self._client(_json_headers(self.settings.vinted_user_agent, referer=source.url)) as client:
                if not self._cookies:
                    self._bootstrap_anonymous_session(client, source.url, attempt=retry_index + 1)

                try:
                    response = self._request_catalog_api(client, source, page, attempt=retry_index + 1)
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
                    self._bootstrap_anonymous_session(client, source.url, attempt=retry_index + 2)
                    response = self._request_catalog_api(client, source, page, attempt=retry_index + 2)
                    return parse_catalog_api_payload(response, base_url=str(self.settings.vinted_base_url))
                except VintedCatalogProviderError as exc:
                    last_error = exc
                    if retry_index >= self.request_retries:
                        raise

        raise last_error or VintedCatalogProviderError("Vinted catalog API request failed")

    def fetch_detail(self, candidate: CatalogItemCandidate) -> CatalogItemDetail:
        with self._client(_html_headers(self.settings.vinted_user_agent)) as client:
            try:
                response = client.get(candidate.url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise VintedCatalogProviderError(f"Vinted detail request failed for {candidate.vinted_item_id}: {exc}") from exc

        return parse_item_detail_html(response.text, candidate)

    def _request_catalog_api(self, client: httpx.Client, source: Any, page: int | None, *, attempt: int) -> dict[str, Any]:
        url = urljoin(str(self.settings.vinted_base_url), "/api/v2/catalog/items")
        params = build_catalog_api_params(source.url, page, self.catalog_per_page)
        self._emit_event(
            phase="catalog_api_request_start",
            method="GET",
            url=url,
            details={
                "page": params["page"],
                "per_page": params["per_page"],
                "order": params["order"],
                "session_marker_count": len(client.cookies),
                "session_markers": safe_cookie_markers(client.cookies),
                "timeout_ms": self.timeout_ms,
                "attempt": attempt,
            },
        )
        started_at = time.perf_counter()
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            self._emit_event(
                phase="catalog_api_request_error",
                method="GET",
                url=url,
                duration_ms=_elapsed_ms(started_at),
                level="error",
                message=str(exc),
                details={"timeout_ms": self.timeout_ms, "attempt": attempt},
            )
            raise VintedCatalogProviderError(f"Vinted catalog API request failed: {exc}") from exc

        if response.status_code in {401, 403}:
            self._emit_event(
                phase="catalog_api_session_rejected",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="warning",
                message=f"Catalog API rejected anonymous session with status {response.status_code}",
                details={"attempt": attempt, "retryable": attempt == 1},
            )
            raise VintedCatalogSessionError(f"Vinted catalog API session rejected with status {response.status_code}")

        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._emit_event(
                phase="catalog_api_request_error",
                method="GET",
                url=url,
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started_at),
                level="error",
                message=str(exc),
                details={"attempt": attempt},
            )
            raise VintedCatalogProviderError(f"Vinted catalog API request failed: {exc}") from exc

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
                details={"content_type": content_type, "attempt": attempt},
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
                details={"attempt": attempt},
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
            },
        )
        return payload

    def _bootstrap_anonymous_session(self, client: httpx.Client, referer_url: str, *, attempt: int) -> None:
        self._emit_event(
            phase="anonymous_session_bootstrap_start",
            method="GET",
            url=referer_url,
            message="Obtaining anonymous public Vinted session",
            details={"timeout_ms": self.timeout_ms, "attempt": attempt},
        )
        started_at = time.perf_counter()
        try:
            response = client.get(referer_url, headers=_html_headers(self.settings.vinted_user_agent))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._emit_event(
                phase="anonymous_session_bootstrap_error",
                method="GET",
                url=referer_url,
                duration_ms=_elapsed_ms(started_at),
                level="error",
                message=str(exc),
                details={"timeout_ms": self.timeout_ms, "attempt": attempt},
            )
            raise VintedCatalogProviderError(f"Vinted anonymous session bootstrap failed: {exc}") from exc

        self._cookies = client.cookies
        self._emit_event(
            phase="anonymous_session_bootstrap_success",
            method="GET",
            url=referer_url,
            status_code=response.status_code,
            duration_ms=_elapsed_ms(started_at),
            message="Anonymous public session obtained",
            details={
                "session_marker_count": len(self._cookies),
                "session_markers": safe_cookie_markers(self._cookies),
                "timeout_ms": self.timeout_ms,
                "attempt": attempt,
            },
        )

    def _client(self, headers: dict[str, str]) -> httpx.Client:
        timeout = self.timeout_ms / 1000
        return httpx.Client(
            follow_redirects=True,
            headers=headers,
            cookies=self._cookies,
            proxy=self.proxy_url,
            timeout=timeout,
            transport=self.transport,
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


def build_catalog_api_params(source_url: str, page: int | None, per_page: int) -> dict[str, str | int]:
    query = parse_qs(urlparse(source_url).query, keep_blank_values=True)
    params: dict[str, str | int] = {
        "page": page or 1,
        "per_page": per_page,
        "order": "newest_first",
    }

    direct_keys = ["search_text", "price_from", "price_to", "currency"]
    for key in direct_keys:
        value = _first_query_value(query, key)
        if value is not None:
            params[key] = value

    repeated_mapping = {
        "catalog[]": "catalog_ids",
        "brand_ids[]": "brand_ids",
        "size_ids[]": "size_ids",
        "status_ids[]": "status_ids",
    }
    for public_key, api_key in repeated_mapping.items():
        values = query.get(public_key) or query.get(public_key.removesuffix("[]")) or []
        if values:
            params[api_key] = ",".join(values)

    return params


def _html_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }


def _json_headers(user_agent: str, referer: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Referer": referer,
    }


def _first_query_value(query: Mapping[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]


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


def _with_page(url: str, page: int | None) -> str:
    if page is None or page <= 1:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}page={page}"


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
