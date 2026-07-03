from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vinted_monitor.services.scheduler import SchedulerConfigError, normalize_scheduler_config
from vinted_monitor.services.search_sources import validate_search_source_name, validate_vinted_catalog_url


class SearchSourceCreate(BaseModel):
    name: str
    url: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return validate_search_source_name(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return validate_vinted_catalog_url(value)


class SearchSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    normalized_query: dict[str, Any]
    is_active: bool
    scheduler_config: dict[str, Any]


class SearchSourceUpdate(BaseModel):
    is_active: bool | None = None
    scheduler_config: dict[str, Any] | None = None

    @field_validator("scheduler_config")
    @classmethod
    def validate_scheduler_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            return normalize_scheduler_config(value)
        except SchedulerConfigError as exc:
            raise ValueError(str(exc)) from exc


class SchedulerUpdate(BaseModel):
    enabled: bool


class SchedulerStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    runtime_enabled: bool
    effective_enabled: bool
    max_concurrent_runs: int
    per_source_concurrency: int
    poll_interval_seconds: int
    timezone: str
    proxy_enabled: bool
    proxy_configured: bool


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
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
    description: str | None
    color: str | None
    category: str | None
    shipping_price_amount: Decimal | None
    buyer_protection_fee_amount: Decimal | None
    total_price_amount: Decimal | None
    photos: list[str]
    seller_rating: Decimal | None
    seller_badges: list[str]
    availability_flags: dict[str, Any]
    detail_last_fetched_at: datetime | None
    detail_error: str | None
    first_seen_at: datetime
    last_seen_at: datetime


class ItemResultRead(ItemRead):
    last_scraped_at: datetime
    last_scraped_source_id: int
    last_scraped_source_name: str
    last_run_id: int


class ItemResultPageRead(BaseModel):
    items: list[ItemResultRead]
    total: int
    page: int
    page_size: int
    total_pages: int


class OpportunityResultRead(BaseModel):
    id: int
    item: ItemRead
    source_id: int
    source_name: str
    rule_id: int
    status: str
    score: Decimal | None
    created_at: datetime


class OpportunityResultPageRead(BaseModel):
    items: list[OpportunityResultRead]
    total: int
    page: int
    page_size: int
    total_pages: int


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    items_found: int
    items_new: int
    opportunities_created: int
    error_message: str | None


class ActionRequestCreate(BaseModel):
    item_id: int
    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    action_type: str
    status: str
    payload: dict[str, Any]
    created_at: datetime
