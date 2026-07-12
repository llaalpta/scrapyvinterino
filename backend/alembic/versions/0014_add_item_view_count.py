"""add optional item view count

Revision ID: 0014_add_item_view_count
Revises: 0013_add_egress_validated_at
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_add_item_view_count"
down_revision: str | None = "0013_add_egress_validated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("items", sa.Column("view_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "view_count")
