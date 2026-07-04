from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import ceil

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import Item, Opportunity, SearchSource

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class Page:
    items: list
    total: int
    page: int
    page_size: int
    total_pages: int


@dataclass(frozen=True)
class OpportunityResult:
    opportunity: Opportunity
    item: Item
    source_name: str
    last_scraped_at: datetime | None
    last_run_id: int | None


def list_opportunity_results(
    db: Session,
    *,
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
    source_id: int | None = None,
    scraped_from: datetime | None = None,
    scraped_to: datetime | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    evaluation_status: str | None = None,
) -> Page:
    _validate_page(page, page_size)
    _validate_ranges(scraped_from, scraped_to, price_min, price_max)
    statement = (
        select(
            Opportunity,
            Item,
            SearchSource.name.label("source_name"),
        )
        .join(Item, Item.id == Opportunity.item_id)
        .join(SearchSource, SearchSource.id == Opportunity.source_id)
    )
    if source_id is not None:
        statement = statement.where(Opportunity.source_id == source_id)
    if scraped_from is not None:
        statement = statement.where(Opportunity.last_scraped_at >= scraped_from)
    if scraped_to is not None:
        statement = statement.where(Opportunity.last_scraped_at <= scraped_to)
    if price_min is not None:
        statement = statement.where(Item.price_amount >= price_min)
    if price_max is not None:
        statement = statement.where(Item.price_amount <= price_max)
    if evaluation_status:
        statement = statement.where(Opportunity.evaluation_status == evaluation_status)

    total = _count_rows(db, statement)
    rows = db.execute(
        statement.order_by(Opportunity.last_scraped_at.desc().nullslast(), Opportunity.created_at.desc(), Opportunity.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[
            OpportunityResult(
                opportunity=row.Opportunity,
                item=row.Item,
                source_name=row.source_name,
                last_scraped_at=row.Opportunity.last_scraped_at,
                last_run_id=row.Opportunity.last_run_id,
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=_total_pages(total, page_size),
    )


def _validate_page(page: int, page_size: int) -> None:
    if page < 1:
        raise ValueError("page must be greater than or equal to 1")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")


def _validate_ranges(
    scraped_from: datetime | None,
    scraped_to: datetime | None,
    price_min: Decimal | None,
    price_max: Decimal | None,
) -> None:
    if scraped_from is not None and scraped_to is not None and scraped_from > scraped_to:
        raise ValueError("scraped_from must be before or equal to scraped_to")
    if price_min is not None and price_min < 0:
        raise ValueError("price_min must be greater than or equal to 0")
    if price_max is not None and price_max < 0:
        raise ValueError("price_max must be greater than or equal to 0")
    if price_min is not None and price_max is not None and price_min > price_max:
        raise ValueError("price_min must be less than or equal to price_max")


def _count_rows(db: Session, statement: Select) -> int:
    return db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0


def _total_pages(total: int, page_size: int) -> int:
    if total == 0:
        return 0
    return ceil(total / page_size)
