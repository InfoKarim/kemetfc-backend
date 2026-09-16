"""add discount_percent_off to subscriptions

Revision ID: 795f33cbe67d
Revises: 090a611c7423
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "795f33cbe67d"
down_revision: Union[str, Sequence[str], None] = "090a611c7423"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("discount_percent_off", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.drop_column("discount_percent_off")
