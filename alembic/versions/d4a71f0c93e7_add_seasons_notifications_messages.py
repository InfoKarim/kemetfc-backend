"""add seasons, notifications, messages, and player/team created_at

Revision ID: d4a71f0c93e7
Revises: c8e219f5a2b3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a71f0c93e7"
down_revision: Union[str, Sequence[str], None] = "c8e219f5a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seasons",
        sa.Column("season_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "notifications",
        sa.Column("notification_id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_notifications_user_id",
        "notifications",
        ["user_id"],
    )
    op.create_index(
        "ix_notifications_user_id_read",
        "notifications",
        ["user_id", "read"],
    )

    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(), primary_key=True),
        sa.Column(
            "sender_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            nullable=True,
        ),
        sa.Column(
            "recipient_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_messages_recipient_id",
        "messages",
        ["recipient_id"],
    )
    op.create_index(
        "ix_messages_recipient_id_read",
        "messages",
        ["recipient_id", "read"],
    )

    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
        ))
    op.execute("UPDATE players SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")

    with op.batch_alter_table("teams") as batch_op:
        batch_op.add_column(sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=True,
        ))
    op.execute("UPDATE teams SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("teams") as batch_op:
        batch_op.drop_column("created_at")

    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("created_at")

    op.drop_index("ix_messages_recipient_id_read", table_name="messages")
    op.drop_index("ix_messages_recipient_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_notifications_user_id_read", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_table("seasons")
