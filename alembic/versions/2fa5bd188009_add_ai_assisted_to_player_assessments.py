"""add ai_assisted flag to player_assessments

Revision ID: 2fa5bd188009
Revises: a39119689fcd
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2fa5bd188009"
down_revision: Union[str, Sequence[str], None] = "a39119689fcd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("player_assessments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ai_assisted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.alter_column("ai_assisted", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("player_assessments", schema=None) as batch_op:
        batch_op.drop_column("ai_assisted")
