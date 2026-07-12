"""add indexed pending outbox for run event publication

Revision ID: 0017_run_event_outbox
Revises: 0016_run_event_publications
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_run_event_outbox"
down_revision: str | None = "0016_run_event_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_event_outbox",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["run_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_run_event_outbox_created_at_event_id",
        "run_event_outbox",
        ["created_at", "event_id"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO run_event_outbox (event_id, created_at)
        SELECT run_events.id, run_events.created_at
        FROM run_events
        LEFT JOIN run_event_publications
          ON run_event_publications.event_id = run_events.id
        WHERE run_events.source_id IS NOT NULL
          AND run_event_publications.event_id IS NULL
        ON CONFLICT (event_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_run_event_outbox_created_at_event_id", table_name="run_event_outbox")
    op.drop_table("run_event_outbox")
