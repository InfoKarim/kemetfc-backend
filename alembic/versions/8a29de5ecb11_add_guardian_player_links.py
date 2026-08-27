"""add guardian player links

Revision ID: 8a29de5ecb11
Revises: 41b712e8c4a1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a29de5ecb11"
down_revision: Union[str, Sequence[str], None] = "41b712e8c4a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guardian_player_links",
        sa.Column("guardian_user_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["guardian_user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("guardian_user_id", "player_id"),
    )
    op.create_index(
        "ix_guardian_player_links_created_by_user_id",
        "guardian_player_links",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_guardian_player_links_created_by_user_id",
        table_name="guardian_player_links",
    )
    op.drop_table("guardian_player_links")
