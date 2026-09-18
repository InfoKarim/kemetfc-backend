"""add player jersey_number

Revision ID: 209b571faf12
Revises: c8e1f4a92b7d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "209b571faf12"
down_revision: Union[str, Sequence[str], None] = "c8e1f4a92b7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column(
            "jersey_number",
            sa.Integer(),
            nullable=True,
        ))
    op.create_index(
        "ix_players_jersey_number",
        "players",
        ["jersey_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_players_jersey_number", table_name="players")
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("jersey_number")
