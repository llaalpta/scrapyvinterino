from __future__ import annotations

import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from vinted_monitor.providers.catalog import CatalogItemCandidate, CatalogSearchResult

NEXT_FLIGHT_CHUNK_PATTERN = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)


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
