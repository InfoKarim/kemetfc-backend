"""add user profile fields

Revision ID: e2b4c29b8fcc
Revises: c1a5e9d3f708
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2b4c29b8fcc"
down_revision: Union[str, Sequence[str], None] = "c1a5e9d3f708"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("first_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("last_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("phone", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("address", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("national_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("national_id")
        batch_op.drop_column("address")
        batch_op.drop_column("phone")
        batch_op.drop_column("last_name")
        batch_op.drop_column("first_name")
