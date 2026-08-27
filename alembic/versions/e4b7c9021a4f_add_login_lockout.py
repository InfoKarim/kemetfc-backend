"""add login lockout

Revision ID: e4b7c9021a4f
Revises: b15e2a87d390
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b7c9021a4f"
down_revision: Union[str, Sequence[str], None] = "b15e2a87d390"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ))
        batch_op.add_column(sa.Column(
            "locked_until",
            sa.DateTime(),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_attempts")
