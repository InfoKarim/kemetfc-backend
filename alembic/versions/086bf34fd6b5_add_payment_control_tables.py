"""add payment control tables (membership plans, manual payments, player memberships)

Revision ID: 086bf34fd6b5
Revises: 6512cd5a787a

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "086bf34fd6b5"
down_revision: Union[str, Sequence[str], None] = "6512cd5a787a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "membership_plans",
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("stripe_price_id", sa.String(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("billing_interval", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("plan_id"),
    )
    op.create_index(
        "ix_membership_plans_stripe_price_id",
        "membership_plans",
        ["stripe_price_id"],
        unique=True,
    )

    op.create_table(
        "manual_payments",
        sa.Column("manual_payment_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("recorded_by_user_id", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("manual_payment_id"),
    )
    op.create_index(
        "ix_manual_payments_player_id",
        "manual_payments",
        ["player_id"],
        unique=False,
    )

    op.create_table(
        "player_memberships",
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("plan_id", sa.String(), nullable=False),
        sa.Column("assigned_by_user_id", sa.String(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["membership_plans.plan_id"]),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("player_id"),
    )

    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("plan_id", sa.String(), nullable=True))
        batch_op.create_index(
            "ix_subscriptions_plan_id",
            ["plan_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_subscriptions_plan_id_membership_plans",
            "membership_plans",
            ["plan_id"],
            ["plan_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_subscriptions_plan_id_membership_plans", type_="foreignkey"
        )
        batch_op.drop_index("ix_subscriptions_plan_id")
        batch_op.drop_column("plan_id")

    op.drop_table("player_memberships")

    op.drop_index("ix_manual_payments_player_id", table_name="manual_payments")
    op.drop_table("manual_payments")

    op.drop_index(
        "ix_membership_plans_stripe_price_id", table_name="membership_plans"
    )
    op.drop_table("membership_plans")
