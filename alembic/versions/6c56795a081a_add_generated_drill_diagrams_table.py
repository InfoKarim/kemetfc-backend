"""add generated drill diagrams table

Revision ID: 6c56795a081a
Revises: e2b4c29b8fcc
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6c56795a081a"
down_revision: Union[str, Sequence[str], None] = "e2b4c29b8fcc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generated_drill_diagrams",
        sa.Column("diagram_id", sa.String(), nullable=False),
        sa.Column("query", sa.String(), nullable=False),
        sa.Column("query_normalized", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("diagram_json", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("diagram_id"),
    )
    op.create_index(
        op.f("ix_generated_drill_diagrams_query_normalized"),
        "generated_drill_diagrams",
        ["query_normalized"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_generated_drill_diagrams_query_normalized"),
        table_name="generated_drill_diagrams",
    )
    op.drop_table("generated_drill_diagrams")
