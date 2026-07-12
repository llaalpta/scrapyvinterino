"""add egress validation timestamp

Revision ID: 0013_add_egress_validated_at
Revises: 0012_add_run_task_id
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_add_egress_validated_at"
down_revision: str | None = "0012_add_run_task_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vinted_sessions",
        sa.Column("egress_validated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vinted_sessions", "egress_validated_at")
