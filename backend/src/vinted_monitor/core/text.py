from __future__ import annotations

import unicodedata
from collections.abc import Iterable


def normalize_search_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def matched_exclusion_terms(text: str, terms: Iterable[str]) -> list[str]:
    normalized_text = normalize_search_text(text)
    matched: list[str] = []
    for term in terms:
        normalized_term = normalize_search_text(term)
        if normalized_term and normalized_term in normalized_text:
            matched.append(term)
    return list(dict.fromkeys(matched))
