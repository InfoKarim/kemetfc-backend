"""add training plans table

Revision ID: 436ec133c6d9
Revises: 7651da172068
Create Date: 2026-08-16 13:18:53.403884

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '436ec133c6d9'
down_revision: Union[str, Sequence[str], None] = '7651da172068'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "training_plans",
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("analysis_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("player_difficulty", sa.String(), nullable=False),
        sa.Column("target_duration", sa.Integer(), nullable=False),
        sa.Column("available_equipment", sa.JSON(), nullable=False),
        sa.Column("recommendations", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.player_id"],
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["analyses.analysis_id"],
        ),
        sa.PrimaryKeyConstraint("plan_id"),
    )
    op.create_index(
        "ix_training_plans_player_id",
        "training_plans",
        ["player_id"],
        unique=False,
    )
    op.create_index(
        "ix_training_plans_analysis_id",
        "training_plans",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        "ix_training_plans_status",
        "training_plans",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_training_plans_status",
        table_name="training_plans",
    )
    op.drop_index(
        "ix_training_plans_analysis_id",
        table_name="training_plans",
    )
    op.drop_index(
        "ix_training_plans_player_id",
        table_name="training_plans",
    )
    op.drop_table("training_plans")
