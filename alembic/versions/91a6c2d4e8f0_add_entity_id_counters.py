"""add entity id counters

Revision ID: 91a6c2d4e8f0
Revises: f21c7d4a9b10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "91a6c2d4e8f0"
down_revision: Union[str, Sequence[str], None] = "f21c7d4a9b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    counters = op.create_table(
        "id_counters",
        sa.Column("entity", sa.String(), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("entity"),
    )
    op.bulk_insert(counters, [
        {"entity": entity, "next_value": 1}
        for entity in (
            "player",
            "team",
            "match",
            "record",
            "video",
            "analysis",
            "drill",
            "training_plan",
            "analysis_job",
            "consent",
            "privacy_request",
        )
    ])


def downgrade() -> None:
    op.drop_table("id_counters")
