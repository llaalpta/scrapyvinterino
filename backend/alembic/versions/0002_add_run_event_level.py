"""add run event level

Revision ID: 0002_add_run_event_level
Revises: 0001_current_schema
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_add_run_event_level"
down_revision: str | None = "0001_current_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_events", sa.Column("level", sa.String(length=20), nullable=False, server_default="info"))


def downgrade() -> None:
    op.drop_column("run_events", "level")
