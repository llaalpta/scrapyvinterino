from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from vinted_monitor.core.text import matched_exclusion_terms, normalize_search_text
from vinted_monitor.db.models import Item

MONITOR_FILTER_NAME = "Terminos excluyentes"


@dataclass(frozen=True)
class FilterDecision:
    status: str
    matched_terms: list[str]


def normalize_filter_definition(definition: dict[str, Any] | None) -> dict[str, list[str]]:
    raw_terms = (definition or {}).get("blacklist_terms", [])
    if isinstance(raw_terms, str):
        raw_terms = [entry.strip() for entry in raw_terms.replace("\n", ",").split(",")]
    if not isinstance(raw_terms, list):
        raise ValueError("blacklist_terms must be a list or comma-separated string")
    cleaned_terms = [str(term).strip() for term in raw_terms if str(term).strip()]
    return {"blacklist_terms": list(dict.fromkeys(cleaned_terms))}


def monitor_filter_snapshot(definition: dict[str, Any] | None) -> list[dict[str, Any]]:
    normalized = normalize_filter_definition(definition)
    if not normalized["blacklist_terms"]:
        return []
    return [{"name": MONITOR_FILTER_NAME, "definition": normalized}]


def filter_term_count(definition: dict[str, Any] | None) -> int:
    return len(normalize_filter_definition(definition)["blacklist_terms"])


def filter_snapshot_term_count(filter_snapshot: list[dict[str, Any]]) -> int:
    return sum(len(rule.get("definition", {}).get("blacklist_terms", [])) for rule in filter_snapshot)


def filter_snapshot_terms(filter_snapshot: list[dict[str, Any]]) -> tuple[str, ...]:
    terms: list[str] = []
    for rule in filter_snapshot:
        terms.extend(str(term) for term in rule.get("definition", {}).get("blacklist_terms", []) if str(term))
    return tuple(dict.fromkeys(terms))


def filter_hash(filter_snapshot: list[dict[str, Any]]) -> str:
    serialized = json.dumps(filter_snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def evaluate_exclusion_filters(item: Item, filter_snapshot: list[dict[str, Any]]) -> FilterDecision:
    if not filter_snapshot:
        return FilterDecision(status="passed_without_filters", matched_terms=[])

    text = _item_filter_text(item)
    terms: list[str] = []
    for rule in filter_snapshot:
        terms.extend(rule["definition"].get("blacklist_terms", []))
    matched_terms = matched_exclusion_terms(text, terms)
    if matched_terms:
        return FilterDecision(status="discarded", matched_terms=list(dict.fromkeys(matched_terms)))
    return FilterDecision(status="passed", matched_terms=[])


def _item_filter_text(item: Item) -> str:
    values = [
        item.title,
        item.brand,
        item.size,
        item.status,
        item.seller_login,
        item.seller_country,
        item.description,
        item.color,
        item.category,
        " ".join(item.seller_badges or []),
    ]
    return normalize_search_text(" ".join(value for value in values if value))
