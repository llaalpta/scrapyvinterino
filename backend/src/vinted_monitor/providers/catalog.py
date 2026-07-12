from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class CatalogSource(Protocol):
    url: str
    normalized_query: dict[str, list[str]]


@dataclass(frozen=True)
class CatalogItemCandidate:
    vinted_item_id: str
    title: str
    brand: str | None
    price_amount: Decimal | None
    currency: str | None
    size: str | None
    status: str | None
    seller_login: str | None
    seller_country: str | None
    favorite_count: int | None
    url: str
    image_url: str | None
    view_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogSearchResult:
    items: list[CatalogItemCandidate]
    page: int | None
    total_pages: int | None
    total_entries: int | None
    per_page: int | None
    next_page: int | None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogItemDetail:
    vinted_item_id: str
    title: str | None = None
    brand: str | None = None
    size: str | None = None
    status: str | None = None
    price_amount: Decimal | None = None
    currency: str | None = None
    description: str | None = None
    color: str | None = None
    category: str | None = None
    shipping_price_amount: Decimal | None = None
    buyer_protection_fee_amount: Decimal | None = None
    total_price_amount: Decimal | None = None
    photos: list[str] = field(default_factory=list)
    seller_rating: Decimal | None = None
    seller_badges: list[str] = field(default_factory=list)
    availability_flags: dict[str, Any] = field(default_factory=dict)
    observed_fields: frozenset[str] = field(default_factory=frozenset)
    field_sources: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class VintedCatalogProvider(Protocol):
    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        """Return public catalog items for a configured Vinted source."""

    def fetch_detail(self, candidate: CatalogItemCandidate, *, referer_url: str | None = None) -> CatalogItemDetail:
        """Return public detail data for a catalog item candidate."""
