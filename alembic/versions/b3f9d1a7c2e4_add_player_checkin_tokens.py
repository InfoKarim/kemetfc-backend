"""add player checkin tokens

Revision ID: b3f9d1a7c2e4
Revises: a6822f6a1653
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3f9d1a7c2e4"
down_revision: Union[str, Sequence[str], None] = "a6822f6a1653"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_checkin_tokens",
        sa.Column("token_id", sa.String(), primary_key=True),
        sa.Column(
            "player_id",
            sa.String(),
            sa.ForeignKey("players.player_id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_player_checkin_tokens_player_id",
        "player_checkin_tokens",
        ["player_id"],
    )
    op.create_index(
        "ix_player_checkin_tokens_token_hash",
        "player_checkin_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_player_checkin_tokens_expires_at",
        "player_checkin_tokens",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_player_checkin_tokens_expires_at", table_name="player_checkin_tokens")
    op.drop_index("ix_player_checkin_tokens_token_hash", table_name="player_checkin_tokens")
    op.drop_index("ix_player_checkin_tokens_player_id", table_name="player_checkin_tokens")
    op.drop_table("player_checkin_tokens")
