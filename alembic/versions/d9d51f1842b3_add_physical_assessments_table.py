"""add physical assessments table

Revision ID: d9d51f1842b3
Revises: 4744ac74df64
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9d51f1842b3"
down_revision: Union[str, Sequence[str], None] = "4744ac74df64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "physical_assessments",
        sa.Column("assessment_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("test_category", sa.String(), nullable=False),
        sa.Column("test_type", sa.String(), nullable=False),
        sa.Column("methodology_version", sa.String(), nullable=False),
        sa.Column("test_date", sa.Date(), nullable=False),
        sa.Column("age_at_assessment_years", sa.Integer(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("calculated_metrics", sa.JSON(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("recorded_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("assessment_id"),
    )
    op.create_index(
        "ix_physical_assessments_player_id",
        "physical_assessments",
        ["player_id"],
    )
    op.create_index(
        "ix_physical_assessments_test_category",
        "physical_assessments",
        ["test_category"],
    )
    op.create_index(
        "ix_physical_assessments_test_type",
        "physical_assessments",
        ["test_type"],
    )
    op.create_index(
        "ix_physical_assessments_created_at",
        "physical_assessments",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("physical_assessments")
