from sqlalchemy import select
from sqlalchemy.orm import Session

from vinted_monitor.db.models import Item


def list_items(db: Session, limit: int = 100) -> list[Item]:
    statement = select(Item).order_by(Item.first_seen_at.desc()).limit(limit)
    return list(db.scalars(statement))
