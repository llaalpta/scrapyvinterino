"""add commit-ordered run event publications

Revision ID: 0016_run_event_publications
Revises: 0015_redact_event_bodies
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_run_event_publications"
down_revision: str | None = "0015_redact_event_bodies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_event_publications",
        sa.Column("position", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["run_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("position"),
        sa.UniqueConstraint("event_id"),
    )


def downgrade() -> None:
    op.drop_table("run_event_publications")
