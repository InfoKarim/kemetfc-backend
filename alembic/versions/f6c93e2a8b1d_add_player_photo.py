"""add player photo filename

Revision ID: f6c93e2a8b1d
Revises: e5b82a1d4f6c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6c93e2a8b1d"
down_revision: Union[str, Sequence[str], None] = "e5b82a1d4f6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column(
            "photo_filename",
            sa.String(),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("photo_filename")
