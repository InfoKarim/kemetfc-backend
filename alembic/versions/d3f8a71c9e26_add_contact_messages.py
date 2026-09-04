"""add contact messages table

Revision ID: d3f8a71c9e26
Revises: b6f2d4a19e05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3f8a71c9e26"
down_revision: Union[str, Sequence[str], None] = "b6f2d4a19e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contact_messages",
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        op.f("ix_contact_messages_email"),
        "contact_messages",
        ["email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_contact_messages_submitted_at"),
        "contact_messages",
        ["submitted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_contact_messages_submitted_at"),
        table_name="contact_messages",
    )
    op.drop_index(
        op.f("ix_contact_messages_email"),
        table_name="contact_messages",
    )
    op.drop_table("contact_messages")
