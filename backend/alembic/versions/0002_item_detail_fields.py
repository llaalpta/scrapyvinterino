"""add item detail fields

Revision ID: 0002_item_detail_fields
Revises: 0001_initial
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_item_detail_fields"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("items", sa.Column("description", sa.Text()))
    op.add_column("items", sa.Column("color", sa.String(length=120)))
    op.add_column("items", sa.Column("category", sa.Text()))
    op.add_column("items", sa.Column("shipping_price_amount", sa.Numeric(10, 2)))
    op.add_column("items", sa.Column("buyer_protection_fee_amount", sa.Numeric(10, 2)))
    op.add_column("items", sa.Column("total_price_amount", sa.Numeric(10, 2)))
    op.add_column(
        "items",
        sa.Column("photos", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("items", sa.Column("seller_rating", sa.Numeric(5, 2)))
    op.add_column(
        "items",
        sa.Column("seller_badges", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "items",
        sa.Column("availability_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "items",
        sa.Column("detail_raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("items", sa.Column("detail_last_fetched_at", sa.DateTime(timezone=True)))
    op.add_column("items", sa.Column("detail_error", sa.Text()))


def downgrade() -> None:
    op.drop_column("items", "detail_error")
    op.drop_column("items", "detail_last_fetched_at")
    op.drop_column("items", "detail_raw")
    op.drop_column("items", "availability_flags")
    op.drop_column("items", "seller_badges")
    op.drop_column("items", "seller_rating")
    op.drop_column("items", "photos")
    op.drop_column("items", "total_price_amount")
    op.drop_column("items", "buyer_protection_fee_amount")
    op.drop_column("items", "shipping_price_amount")
    op.drop_column("items", "category")
    op.drop_column("items", "color")
    op.drop_column("items", "description")
