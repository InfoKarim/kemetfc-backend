"""add user feature permissions

Revision ID: b32f6a8d901c
Revises: 91a6c2d4e8f0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b32f6a8d901c"
down_revision: Union[str, Sequence[str], None] = "91a6c2d4e8f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column(
            "feature_permissions",
            sa.JSON(),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("feature_permissions")
