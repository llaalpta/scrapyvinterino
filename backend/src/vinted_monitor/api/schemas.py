from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vinted_monitor.services.run_events import redact_run_event_details
from vinted_monitor.services.scheduler import SchedulerConfigError, normalize_scheduler_config
from vinted_monitor.services.search_sources import validate_search_source_name, validate_vinted_catalog_url


class SearchSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    monitor_mode: str
    duration_minutes: int | None
    filter_definition: dict[str, Any]
    monitor_started_at: datetime | None
    monitor_until: datetime | None
    last_run_at: datetime | None
    next_run_at: datetime | None
    archived_at: datetime | None
    baseline_ready: bool = False
    baseline_policy_hash: str | None = None
    catalog_filter_compatibility: dict[str, Any] = Field(default_factory=dict)


class SearchSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    url: str | None = None
    scheduler_config: dict[str, Any] | None = None
    monitor_mode: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    filter_definition: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_search_source_name(value)

    @field_validator("url")
    @classmethod
    def validate_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_vinted_catalog_url(value)

    @field_validator("monitor_mode")
    @classmethod
    def validate_monitor_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in {"manual", "continuous", "duration", "window"}:
            raise ValueError("monitor_mode must be one of manual, continuous, duration, window")
        return value

    @field_validator("scheduler_config")
    @classmethod
    def validate_scheduler_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            return normalize_scheduler_config(value)
        except SchedulerConfigError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("filter_definition")
    @classmethod
    def validate_filter_definition(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        from vinted_monitor.services.filters import normalize_filter_definition

        return normalize_filter_definition(value)


class SchedulerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=20)
    allow_direct_without_proxy: bool | None = None
    direct_max_concurrent_runs: int | None = Field(default=None, ge=0, le=10)
    catalog_per_page: int | None = Field(default=None, ge=1, le=96)
    detail_max_candidates_per_run: int | None = Field(default=None, ge=0, le=96)
    request_timeout_ms: int | None = Field(default=None, ge=1000, le=60000)
    stop_monitor_after_consecutive_failures: int | None = Field(default=None, ge=1, le=20)
    proxy_cooldown_minutes: int | None = Field(default=None, ge=1, le=1440)


class SchedulerStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    runtime_enabled: bool
    effective_enabled: bool
    max_concurrent_runs: int
    per_source_concurrency: int
    poll_interval_seconds: int
    timezone: str
    allow_direct_without_proxy: bool
    direct_max_concurrent_runs: int
    active_proxy_count: int
    proxy_capacity: int
    direct_runtime_enabled: bool
    direct_capacity: int
    effective_capacity: int
    active_periodic_monitors: int
    catalog_per_page: int
    detail_max_candidates_per_run: int
    request_timeout_ms: int
    stop_monitor_after_consecutive_failures: int
    proxy_cooldown_minutes: int


class ProxyProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    scheme: str = "http"
    kind: str = "own"
    host: str
    port: int = Field(ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    country_code: str = "ES"
    max_concurrent_runs: int = Field(default=1, ge=1, le=10)
    is_active: bool = True


class ProxyProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    scheme: str | None = None
    kind: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    clear_password: bool = False
    country_code: str | None = None
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=10)
    is_active: bool | None = None


class ProxyProfileRead(BaseModel):
    id: int
    name: str
    scheme: str
    kind: str
    host: str
    port: int
    username: str | None
    username_masked: str | None
    has_password: bool
    password_fingerprint: str | None
    country_code: str
    locale: str
    accept_language: str
    screen: str
    vinted_screen: str
    is_active: bool
    max_concurrent_runs: int
    cooldown_until: datetime | None
    failure_count: int
    last_used_at: datetime | None
    last_test_status: str | None
    last_test_ip: str | None
    last_test_error: str | None
    vinted_session: VintedSessionRead | None = None


class VintedSessionContextRead(BaseModel):
    csrf_token: bool
    anon_id: bool
    access_token_web: bool
    datadome: bool
    v_udt: bool
    user_iso_locale: bool
    vinted_screen: bool


class VintedSessionRead(BaseModel):
    id: int
    source_id: int
    proxy_profile_id: int
    status: str
    browser_profile: str
    impersonate: str
    country_code: str
    locale: str
    accept_language: str
    viewport_size: str
    vinted_screen: str
    egress_ip: str | None
    egress_country_code: str | None
    proxy_session: dict[str, Any] | None
    request_count: int
    max_requests: int
    failure_count: int
    prepared_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    invalidated_at: datetime | None
    last_error: str | None
    context: VintedSessionContextRead


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


class OpportunityResultRead(BaseModel):
    id: int
    item: ItemRead
    source_id: int
    source_name: str
    status: str
    evaluation_status: str
    filter_snapshot: list[dict[str, Any]]
    score: Decimal | None
    created_at: datetime
    last_scraped_at: datetime
    last_run_id: int | None


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
    monitor_session_id: int | None
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    items_found: int
    items_new: int
    items_filter_passed: int
    items_discarded_by_filters: int
    items_filter_pending: int
    opportunities_created: int
    error_message: str | None
    runtime_metadata: dict[str, Any]


class ItemDetailProbeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ref: str = Field(min_length=1, max_length=500)


class ItemDetailProbeRead(BaseModel):
    run: RunRead
    result: dict[str, Any]


class MonitorChartPointRead(BaseModel):
    bucket_start: datetime
    bucket_end: datetime
    items_found: int
    runs_count: int


class MonitorSummaryRead(BaseModel):
    sessions_count: int
    active_seconds: int
    runs_count: int
    failed_runs: int
    items_found: int
    items_new: int
    items_discarded_by_filters: int
    opportunities_created: int


class MonitorSessionRead(BaseModel):
    id: int
    started_at: datetime
    stopped_at: datetime | None
    stop_reason: str | None
    duration_seconds: int


class MonitorStatsRead(BaseModel):
    range: str
    range_start: datetime
    range_end: datetime
    bucket_label: str
    bucket_seconds: int | None
    active_session: MonitorSessionRead | None
    latest_session: MonitorSessionRead | None
    session_summary: MonitorSummaryRead
    historical_summary: MonitorSummaryRead
    chart_points: list[MonitorChartPointRead]


class RunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int | None
    source_id: int | None
    phase: str
    level: str
    method: str | None
    url: str | None
    status_code: int | None
    duration_ms: int | None
    proxy_profile_id: int | None
    egress_ip: str | None
    user_agent: str | None
    auth_mode: str | None
    message: str | None
    details: dict[str, Any]
    created_at: datetime

    @field_validator("details", mode="before")
    @classmethod
    def validate_details(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        return redact_run_event_details(value)


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
