from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    first_seen_at: datetime
    last_seen_at: datetime


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    status: str
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
