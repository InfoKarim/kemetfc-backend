"""add subscriptions table

Revision ID: c1a5e9d3f708
Revises: d3f8a71c9e26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1a5e9d3f708"
down_revision: Union[str, Sequence[str], None] = "d3f8a71c9e26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("stripe_subscription_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("paying_user_id", sa.String(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(), nullable=False),
        sa.Column("stripe_price_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["paying_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("stripe_subscription_id"),
    )
    op.create_index(
        op.f("ix_subscriptions_player_id"),
        "subscriptions",
        ["player_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subscriptions_paying_user_id"),
        "subscriptions",
        ["paying_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subscriptions_stripe_customer_id"),
        "subscriptions",
        ["stripe_customer_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_subscriptions_stripe_customer_id"),
        table_name="subscriptions",
    )
    op.drop_index(
        op.f("ix_subscriptions_paying_user_id"),
        table_name="subscriptions",
    )
    op.drop_index(
        op.f("ix_subscriptions_player_id"),
        table_name="subscriptions",
    )
    op.drop_table("subscriptions")
