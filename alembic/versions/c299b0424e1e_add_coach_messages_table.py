"""add coach messages table

Revision ID: c299b0424e1e
Revises: 2fa5bd188009
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c299b0424e1e"
down_revision: Union[str, Sequence[str], None] = "2fa5bd188009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coach_messages",
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("next_focus", sa.JSON(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("player_id"),
    )


def downgrade() -> None:
    op.drop_table("coach_messages")
