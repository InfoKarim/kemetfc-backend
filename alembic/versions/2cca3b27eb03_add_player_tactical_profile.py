"""add player tactical profile

Revision ID: 2cca3b27eb03
Revises: c9a1f3e7b204
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2cca3b27eb03"
down_revision: Union[str, Sequence[str], None] = "c9a1f3e7b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column(
            "tactical_profile",
            sa.JSON(),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("tactical_profile")
