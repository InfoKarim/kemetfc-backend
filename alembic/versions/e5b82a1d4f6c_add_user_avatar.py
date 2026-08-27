"""add user avatar filename

Revision ID: e5b82a1d4f6c
Revises: d4a71f0c93e7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b82a1d4f6c"
down_revision: Union[str, Sequence[str], None] = "d4a71f0c93e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column(
            "avatar_filename",
            sa.String(),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("avatar_filename")
