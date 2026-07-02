"""make opportunity uniqueness global per item and rule

Revision ID: 0003_opp_global_unique
Revises: 0002_item_detail_fields
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_opp_global_unique"
down_revision: str | None = "0002_item_detail_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_opportunity_source_item_rule", "opportunities", type_="unique")
    op.alter_column("opportunities", "rule_id", existing_type=sa.Integer(), nullable=False)
    op.create_unique_constraint("uq_opportunity_item_rule", "opportunities", ["item_id", "rule_id"])


def downgrade() -> None:
    op.drop_constraint("uq_opportunity_item_rule", "opportunities", type_="unique")
    op.alter_column("opportunities", "rule_id", existing_type=sa.Integer(), nullable=True)
    op.create_unique_constraint("uq_opportunity_source_item_rule", "opportunities", ["source_id", "item_id", "rule_id"])
