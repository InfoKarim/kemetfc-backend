"""add privacy requests

Revision ID: b15e2a87d390
Revises: 8a29de5ecb11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b15e2a87d390"
down_revision: Union[str, Sequence[str], None] = "8a29de5ecb11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "privacy_requests",
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("guardian_user_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("request_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.String(), nullable=True),
        sa.Column("review_notes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["guardian_user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("request_id"),
    )
    for column in (
        "guardian_user_id",
        "player_id",
        "request_type",
        "status",
        "reviewed_by_user_id",
    ):
        op.create_index(f"ix_privacy_requests_{column}", "privacy_requests", [column])


def downgrade() -> None:
    for column in (
        "reviewed_by_user_id",
        "status",
        "request_type",
        "player_id",
        "guardian_user_id",
    ):
        op.drop_index(f"ix_privacy_requests_{column}", table_name="privacy_requests")
    op.drop_table("privacy_requests")
