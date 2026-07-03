from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import FilterRule, Item


class FilterRuleNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class FilterDecision:
    status: str
    matched_terms: list[str]


def list_filter_rules(db: Session) -> list[FilterRule]:
    return list(db.scalars(select(FilterRule).order_by(FilterRule.id.desc())))


def create_filter_rule(db: Session, *, name: str, definition: dict[str, Any], is_active: bool = True) -> FilterRule:
    rule = FilterRule(name=_validate_name(name), definition=normalize_filter_definition(definition), is_active=is_active)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def update_filter_rule(
    db: Session,
    rule_id: int,
    *,
    name: str | None = None,
    definition: dict[str, Any] | None = None,
    is_active: bool | None = None,
) -> FilterRule:
    rule = db.get(FilterRule, rule_id)
    if rule is None:
        raise FilterRuleNotFoundError(f"Filter rule {rule_id} does not exist")
    if name is not None:
        rule.name = _validate_name(name)
    if definition is not None:
        rule.definition = normalize_filter_definition(definition)
    if is_active is not None:
        rule.is_active = is_active
    db.commit()
    db.refresh(rule)
    return rule


def get_filter_snapshot(db: Session, rule_ids: list[int]) -> list[dict[str, Any]]:
    if not rule_ids:
        return []
    unique_ids = list(dict.fromkeys(rule_ids))
    rules = list(db.scalars(select(FilterRule).where(FilterRule.id.in_(unique_ids), FilterRule.is_active.is_(True))))
    rules_by_id = {rule.id: rule for rule in rules}
    missing_ids = [rule_id for rule_id in unique_ids if rule_id not in rules_by_id]
    if missing_ids:
        raise FilterRuleNotFoundError(f"Active filter rules not found: {', '.join(str(rule_id) for rule_id in missing_ids)}")
    return [
        {
            "id": rule.id,
            "name": rule.name,
            "definition": normalize_filter_definition(rule.definition),
        }
        for rule in (rules_by_id[rule_id] for rule_id in unique_ids)
    ]


def filter_hash(filter_snapshot: list[dict[str, Any]]) -> str:
    serialized = json.dumps(filter_snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def evaluate_exclusion_filters(item: Item, filter_snapshot: list[dict[str, Any]]) -> FilterDecision:
    if not filter_snapshot:
        return FilterDecision(status="passed_without_filters", matched_terms=[])

    text = _item_filter_text(item)
    matched_terms: list[str] = []
    for rule in filter_snapshot:
        for term in rule["definition"].get("blacklist_terms", []):
            if _normalize_text(term) and _normalize_text(term) in text:
                matched_terms.append(term)
    if matched_terms:
        return FilterDecision(status="discarded", matched_terms=list(dict.fromkeys(matched_terms)))
    return FilterDecision(status="passed", matched_terms=[])


def normalize_filter_definition(definition: dict[str, Any]) -> dict[str, Any]:
    blacklist_terms = definition.get("blacklist_terms", [])
    if isinstance(blacklist_terms, str):
        blacklist_terms = [entry.strip() for entry in blacklist_terms.split(",")]
    if not isinstance(blacklist_terms, list):
        raise ValueError("blacklist_terms must be a list or comma-separated string")
    cleaned_terms = [str(term).strip() for term in blacklist_terms if str(term).strip()]
    return {"blacklist_terms": list(dict.fromkeys(cleaned_terms))}


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Filter name cannot be empty")
    return cleaned


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
    return _normalize_text(" ".join(value for value in values if value))


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
