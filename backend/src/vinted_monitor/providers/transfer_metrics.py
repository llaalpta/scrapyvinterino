from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PROXY_TRANSFER_DETAIL_KEY = "proxy_transfer"
PROXY_TRAFFIC_METADATA_KEY = "proxy_traffic_estimate"
PROXY_TRAFFIC_VERSION = 1
PROXY_TRAFFIC_CATEGORIES = {"egress", "session_setup", "catalog", "detail"}
_BYTE_FIELDS = (
    "request_size_bytes",
    "upload_size_bytes",
    "header_size_bytes",
    "download_size_bytes",
)


def response_transfer_observation(response: Any, *, category: str) -> dict[str, Any]:
    redirect_count = _non_negative_int(getattr(response, "redirect_count", 0))
    observation = {
        "category": _category(category),
        "observed_requests": redirect_count + 1,
        "unobserved_attempts": 0,
        "request_size_bytes": _non_negative_int(getattr(response, "request_size", 0)),
        "upload_size_bytes": _non_negative_int(getattr(response, "upload_size", 0)),
        "header_size_bytes": _non_negative_int(getattr(response, "header_size", 0)),
        "download_size_bytes": _non_negative_int(getattr(response, "download_size", 0)),
    }
    observation["total_observed_bytes"] = _byte_total(observation)
    return observation


def transfer_observation_from_response(response: Any, *, category: str) -> dict[str, Any]:
    attached = getattr(response, "proxy_transfer_observation", None)
    if isinstance(attached, Mapping):
        return _normalized_observation(attached, category=category)
    return response_transfer_observation(response, category=category)


def unobserved_transfer_attempt(*, category: str) -> dict[str, Any]:
    return {
        "category": _category(category),
        "observed_requests": 0,
        "unobserved_attempts": 1,
        **{field: 0 for field in _BYTE_FIELDS},
        "total_observed_bytes": 0,
    }


def transfer_observation_from_exception(exc: Exception, *, category: str) -> dict[str, Any]:
    attached = getattr(exc, "proxy_transfer_observation", None)
    if isinstance(attached, Mapping):
        return _normalized_observation(attached, category=category)
    response = getattr(exc, "response", None)
    if response is not None:
        return transfer_observation_from_response(response, category=category)
    return unobserved_transfer_attempt(category=category)


def attach_transfer_observation(target: Any, observation: Mapping[str, Any]) -> None:
    try:
        target.proxy_transfer_observation = dict(observation)
    except Exception:
        return


def merge_transfer_observations(
    current: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
    *,
    category: str,
) -> dict[str, Any]:
    left = _normalized_observation(current or {}, category=category)
    right = _normalized_observation(incoming, category=category)
    merged = {
        "category": _category(category),
        "observed_requests": left["observed_requests"] + right["observed_requests"],
        "unobserved_attempts": left["unobserved_attempts"] + right["unobserved_attempts"],
    }
    for field in _BYTE_FIELDS:
        merged[field] = left[field] + right[field]
    merged["total_observed_bytes"] = _byte_total(merged)
    return merged


def aggregate_proxy_traffic_estimate(
    current: Mapping[str, Any] | None,
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = _normalized_observation(observation, category=str(observation.get("category") or "session_setup"))
    category = normalized["category"]
    current_mapping = current if isinstance(current, Mapping) else {}
    aggregate = {
        "version": PROXY_TRAFFIC_VERSION,
        "observed_requests": _non_negative_int(current_mapping.get("observed_requests")),
        "unobserved_attempts": _non_negative_int(current_mapping.get("unobserved_attempts")),
        **{field: _non_negative_int(current_mapping.get(field)) for field in _BYTE_FIELDS},
    }
    aggregate["observed_requests"] += normalized["observed_requests"]
    aggregate["unobserved_attempts"] += normalized["unobserved_attempts"]
    for field in _BYTE_FIELDS:
        aggregate[field] += normalized[field]
    aggregate["total_observed_bytes"] = _byte_total(aggregate)

    raw_categories = current_mapping.get("by_category")
    by_category = dict(raw_categories) if isinstance(raw_categories, Mapping) else {}
    current_category = by_category.get(category)
    category_total = {
        "observed_requests": _non_negative_int(
            current_category.get("observed_requests") if isinstance(current_category, Mapping) else None
        )
        + normalized["observed_requests"],
        "unobserved_attempts": _non_negative_int(
            current_category.get("unobserved_attempts") if isinstance(current_category, Mapping) else None
        )
        + normalized["unobserved_attempts"],
        "total_observed_bytes": _non_negative_int(
            current_category.get("total_observed_bytes") if isinstance(current_category, Mapping) else None
        )
        + normalized["total_observed_bytes"],
    }
    by_category[category] = category_total
    aggregate["by_category"] = by_category
    return aggregate


def _normalized_observation(value: Mapping[str, Any], *, category: str) -> dict[str, Any]:
    normalized = {
        "category": _category(str(value.get("category") or category)),
        "observed_requests": _non_negative_int(value.get("observed_requests")),
        "unobserved_attempts": _non_negative_int(value.get("unobserved_attempts")),
        **{field: _non_negative_int(value.get(field)) for field in _BYTE_FIELDS},
    }
    normalized["total_observed_bytes"] = _byte_total(normalized)
    return normalized


def _byte_total(value: Mapping[str, Any]) -> int:
    return sum(_non_negative_int(value.get(field)) for field in _BYTE_FIELDS)


def _category(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in PROXY_TRAFFIC_CATEGORIES else "session_setup"


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError, OverflowError):
        return 0
