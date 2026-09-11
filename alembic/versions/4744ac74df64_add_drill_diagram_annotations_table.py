"""add drill diagram annotations table

Revision ID: 4744ac74df64
Revises: 6c56795a081a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4744ac74df64"
down_revision: Union[str, Sequence[str], None] = "6c56795a081a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drill_diagram_annotations",
        sa.Column("diagram_key", sa.String(), nullable=False),
        sa.Column("strokes_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("diagram_key"),
    )


def downgrade() -> None:
    op.drop_table("drill_diagram_annotations")
