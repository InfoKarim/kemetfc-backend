"""add payments table

Revision ID: 090a611c7423
Revises: c299b0424e1e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "090a611c7423"
down_revision: Union[str, Sequence[str], None] = "c299b0424e1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("stripe_invoice_id", sa.String(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("hosted_invoice_url", sa.String(), nullable=True),
        sa.Column("invoice_pdf_url", sa.String(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["stripe_subscription_id"],
            ["subscriptions.stripe_subscription_id"],
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.PrimaryKeyConstraint("stripe_invoice_id"),
    )
    op.create_index(
        "ix_payments_player_id",
        "payments",
        ["player_id"],
    )
    op.create_index(
        "ix_payments_created_at",
        "payments",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("payments")
