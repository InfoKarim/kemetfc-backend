"""add pending discount to player_memberships

Revision ID: 01a356dffda7
Revises: 086bf34fd6b5

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "01a356dffda7"
down_revision: Union[str, Sequence[str], None] = "086bf34fd6b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("player_memberships", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("discount_percent_off", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("player_memberships", schema=None) as batch_op:
        batch_op.drop_column("discount_percent_off")
