"""add player weak foot profile

Revision ID: a4e1f0c8d372
Revises: 2cca3b27eb03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4e1f0c8d372"
down_revision: Union[str, Sequence[str], None] = "2cca3b27eb03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column(
            "weak_foot_profile",
            sa.JSON(),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("weak_foot_profile")
