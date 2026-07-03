"""add scheduler settings and run trigger

Revision ID: 0004_scheduler_settings
Revises: 0003_opp_global_unique
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_scheduler_settings"
down_revision: str | None = "0003_opp_global_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("runs", sa.Column("trigger", sa.String(length=40), nullable=False, server_default="manual"))


def downgrade() -> None:
    op.drop_column("runs", "trigger")
    op.drop_table("app_settings")
