from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


JsonDict = dict[str, Any]


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchSource(Base):
    __tablename__ = "search_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    url: Mapped[str] = mapped_column(Text)
    normalized_query: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    scheduler_config: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    monitor_mode: Mapped[str] = mapped_column(String(40), default="manual")
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    filter_definition: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    monitor_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monitor_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    vinted_item_id: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(160))
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    size: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str | None] = mapped_column(String(120))
    seller_login: Mapped[str | None] = mapped_column(String(160))
    seller_country: Mapped[str | None] = mapped_column(String(80))
    favorite_count: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(Text)
    shipping_price_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    buyer_protection_fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_price_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    photos: Mapped[list[str]] = mapped_column(JSONB, default=list)
    seller_rating: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    seller_badges: Mapped[list[str]] = mapped_column(JSONB, default=list)
    availability_flags: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    detail_raw: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    detail_last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail_error: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("search_sources.id"))
    monitor_session_id: Mapped[int | None] = mapped_column(ForeignKey("monitor_sessions.id"))
    status: Mapped[str] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(40), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_filter_passed: Mapped[int] = mapped_column(Integer, default=0)
    items_discarded_by_filters: Mapped[int] = mapped_column(Integer, default=0)
    items_filter_pending: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_created: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    runtime_metadata: Mapped[JsonDict] = mapped_column(JSONB, default=dict)


class MonitorSession(Base):
    __tablename__ = "monitor_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("search_sources.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(String(80))


class ProxyProfile(Base):
    __tablename__ = "proxy_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    scheme: Mapped[str] = mapped_column(String(16), default="http")
    kind: Mapped[str] = mapped_column(String(32), default="own")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(255))
    password_encrypted: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str] = mapped_column(String(2), default="ES")
    locale: Mapped[str] = mapped_column(String(20), default="es-ES")
    accept_language: Mapped[str] = mapped_column(String(120), default="es-ES,es;q=0.9,en;q=0.8")
    screen: Mapped[str] = mapped_column(String(40), default="1920x1080")
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(40))
    last_test_ip: Mapped[str | None] = mapped_column(String(80))
    last_test_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("source_id", "item_id", name="uq_opportunity_monitor_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("search_sources.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    status: Mapped[str] = mapped_column(String(40), default="new")
    evaluation_status: Mapped[str] = mapped_column(String(40), default="passed")
    filter_snapshot: Mapped[list[JsonDict]] = mapped_column(JSONB, default=list)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("search_sources.id"))
    phase: Mapped[str] = mapped_column(String(80))
    level: Mapped[str] = mapped_column(String(20), default="info")
    method: Mapped[str | None] = mapped_column(String(12))
    url: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    proxy_profile_id: Mapped[int | None] = mapped_column(ForeignKey("proxy_profiles.id"))
    egress_ip: Mapped[str | None] = mapped_column(String(80))
    user_agent: Mapped[str | None] = mapped_column(Text)
    auth_mode: Mapped[str | None] = mapped_column(String(80))
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionRequest(Base):
    __tablename__ = "action_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    action_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    payload: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionExecution(Base):
    __tablename__ = "action_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_request_id: Mapped[int] = mapped_column(ForeignKey("action_requests.id"))
    status: Mapped[str] = mapped_column(String(40))
    redacted_request: Mapped[JsonDict | None] = mapped_column(JSONB)
    redacted_response: Mapped[JsonDict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CheckoutSnapshot(Base):
    __tablename__ = "checkout_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    snapshot: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ErrorLog(Base):
    __tablename__ = "errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("search_sources.id"))
    kind: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[JsonDict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
