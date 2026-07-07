from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

SUPPORTED_DIRECT_KEYS = {"search_text", "price_from", "price_to", "currency"}
SUPPORTED_REPEATED_KEYS = {
    "catalog": "catalog_ids",
    "brand_ids": "brand_ids",
    "size_ids": "size_ids",
    "status_ids": "status_ids",
}
IGNORED_QUERY_KEYS = {"page", "time", "order"}


class UnsupportedCatalogFiltersError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogUrlAnalysis:
    api_params: dict[str, str | int]
    supported: dict[str, list[str]]
    ignored: dict[str, list[str]]
    unsupported: dict[str, list[str]]

    @property
    def compatible(self) -> bool:
        return not self.unsupported

    def as_dict(self) -> dict[str, Any]:
        return {
            "compatible": self.compatible,
            "api_params": self.api_params,
            "supported": self.supported,
            "ignored": self.ignored,
            "unsupported": self.unsupported,
        }


def analyze_catalog_url(source_url: str, page: int | None = None, per_page: int = 5) -> CatalogUrlAnalysis:
    query = parse_qs(urlparse(source_url).query, keep_blank_values=True)
    params: dict[str, str | int] = {
        "page": page or 1,
        "per_page": per_page,
        "order": "newest_first",
    }
    supported: dict[str, list[str]] = {}
    ignored: dict[str, list[str]] = {}
    unsupported: dict[str, list[str]] = {}

    for raw_key, values in sorted(query.items()):
        key = _canonical_key(raw_key)
        clean_values = list(values)
        if key in SUPPORTED_DIRECT_KEYS:
            if clean_values:
                params[key] = clean_values[0]
                supported[key] = clean_values
            continue
        if key in SUPPORTED_REPEATED_KEYS:
            if clean_values:
                params[SUPPORTED_REPEATED_KEYS[key]] = ",".join(clean_values)
                supported[key] = clean_values
            continue
        if key in IGNORED_QUERY_KEYS:
            ignored[key] = clean_values
            continue
        unsupported[raw_key] = clean_values

    return CatalogUrlAnalysis(
        api_params=params,
        supported=supported,
        ignored=ignored,
        unsupported=unsupported,
    )


def build_catalog_api_params(source_url: str, page: int | None, per_page: int) -> dict[str, str | int]:
    analysis = analyze_catalog_url(source_url, page=page, per_page=per_page)
    if analysis.unsupported:
        keys = ", ".join(sorted(analysis.unsupported))
        raise UnsupportedCatalogFiltersError(f"Unsupported Vinted catalog filters: {keys}")
    return analysis.api_params


def ensure_catalog_url_filters_supported(source_url: str) -> CatalogUrlAnalysis:
    analysis = analyze_catalog_url(source_url)
    if analysis.unsupported:
        keys = ", ".join(sorted(analysis.unsupported))
        raise UnsupportedCatalogFiltersError(f"Filtros de URL no soportados por el catalogo rapido: {keys}")
    return analysis


def _canonical_key(key: str) -> str:
    return key[:-2] if key.endswith("[]") else key
