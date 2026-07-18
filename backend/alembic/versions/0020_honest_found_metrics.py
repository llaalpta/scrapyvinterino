"""Make post-deduplication candidates the single found metric.

Revision ID: 0020_honest_found_metrics
Revises: 0019_proxy_session_identity
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_honest_found_metrics"
down_revision: str | None = "0019_proxy_session_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE runs SET items_found = items_new")
    op.execute("UPDATE run_events SET details = details - 'items_new' WHERE details ? 'items_new'")
    op.drop_column("runs", "items_new")


def downgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("items_new", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE runs SET items_new = items_found")
    op.execute(
        "UPDATE run_events "
        "SET details = jsonb_set(details, '{items_new}', details -> 'items_found') "
        "WHERE phase = 'run_succeeded' AND details ? 'items_found'"
    )
