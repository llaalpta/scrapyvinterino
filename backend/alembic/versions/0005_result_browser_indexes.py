"""add result browser indexes

Revision ID: 0005_result_browser_idx
Revises: 0004_scheduler_settings
Create Date: 2026-07-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_result_browser_idx"
down_revision: str | None = "0004_scheduler_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_source_seen_items_last_seen_at", "source_seen_items", ["last_seen_at"])
    op.create_index("ix_source_seen_items_source_last_seen", "source_seen_items", ["source_id", "last_seen_at"])
    op.create_index("ix_items_price_amount", "items", ["price_amount"])


def downgrade() -> None:
    op.drop_index("ix_items_price_amount", table_name="items")
    op.drop_index("ix_source_seen_items_source_last_seen", table_name="source_seen_items")
    op.drop_index("ix_source_seen_items_last_seen_at", table_name="source_seen_items")
