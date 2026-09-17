"""add promo codes, family discounts, stripe events, customer mapping, billing settings

Revision ID: 3d3a3dce9bf6
Revises: 01a356dffda7

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3d3a3dce9bf6"
down_revision: Union[str, Sequence[str], None] = "01a356dffda7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stripe_events",
        sa.Column("stripe_event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("stripe_event_id"),
    )

    op.create_table(
        "promo_codes",
        sa.Column("promo_code_id", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("discount_type", sa.String(), nullable=False),
        sa.Column("discount_value", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("per_family_limit", sa.Integer(), nullable=True),
        sa.Column("eligible_plan_ids", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("promo_code_id"),
    )
    op.create_index(
        "ix_promo_codes_code", "promo_codes", ["code"], unique=True
    )

    op.create_table(
        "promo_code_redemptions",
        sa.Column("redemption_id", sa.String(), nullable=False),
        sa.Column("promo_code_id", sa.String(), nullable=False),
        sa.Column("guardian_user_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.promo_code_id"]),
        sa.ForeignKeyConstraint(["guardian_user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.PrimaryKeyConstraint("redemption_id"),
    )
    op.create_index(
        "ix_promo_code_redemptions_promo_code_id",
        "promo_code_redemptions",
        ["promo_code_id"],
        unique=False,
    )
    op.create_index(
        "ix_promo_code_redemptions_guardian_user_id",
        "promo_code_redemptions",
        ["guardian_user_id"],
        unique=False,
    )

    op.create_table(
        "family_discount_rules",
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("sibling_position", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("rule_id"),
    )
    op.create_index(
        "ix_family_discount_rules_sibling_position",
        "family_discount_rules",
        ["sibling_position"],
        unique=True,
    )

    op.create_table(
        "stripe_customer_mappings",
        sa.Column("guardian_user_id", sa.String(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["guardian_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("guardian_user_id"),
    )
    op.create_index(
        "ix_stripe_customer_mappings_stripe_customer_id",
        "stripe_customer_mappings",
        ["stripe_customer_id"],
        unique=True,
    )

    op.create_table(
        "billing_settings",
        sa.Column("settings_id", sa.String(), nullable=False),
        sa.Column("grace_period_days", sa.Integer(), nullable=False),
        sa.Column("payment_due_reminder_days_before", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("settings_id"),
    )

    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_paused",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("membership_plans", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("enrollment_fee_cents", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("trial_period_days", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("age_min", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("age_max", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("eligible_team_id", sa.String(), nullable=True)
        )
        batch_op.add_column(sa.Column("location", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_membership_plans_eligible_team_id_teams",
            "teams",
            ["eligible_team_id"],
            ["team_id"],
        )

    with op.batch_alter_table("player_memberships", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_complimentary",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("admin_override_eligibility", sa.String(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("promo_code_id", sa.String(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_player_memberships_promo_code_id_promo_codes",
            "promo_codes",
            ["promo_code_id"],
            ["promo_code_id"],
        )
        # A COMPLIMENTARY membership may have no plan at all ("this player
        # trains free, full stop") rather than always pointing at a real
        # priced plan.
        batch_op.alter_column("plan_id", nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("player_memberships", schema=None) as batch_op:
        batch_op.alter_column("plan_id", nullable=False)
        batch_op.drop_constraint(
            "fk_player_memberships_promo_code_id_promo_codes", type_="foreignkey"
        )
        batch_op.drop_column("promo_code_id")
        batch_op.drop_column("admin_override_eligibility")
        batch_op.drop_column("is_complimentary")

    with op.batch_alter_table("membership_plans", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_membership_plans_eligible_team_id_teams", type_="foreignkey"
        )
        batch_op.drop_column("location")
        batch_op.drop_column("eligible_team_id")
        batch_op.drop_column("age_max")
        batch_op.drop_column("age_min")
        batch_op.drop_column("trial_period_days")
        batch_op.drop_column("enrollment_fee_cents")

    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.drop_column("is_paused")

    op.drop_table("billing_settings")

    op.drop_index(
        "ix_stripe_customer_mappings_stripe_customer_id",
        table_name="stripe_customer_mappings",
    )
    op.drop_table("stripe_customer_mappings")

    op.drop_index(
        "ix_family_discount_rules_sibling_position",
        table_name="family_discount_rules",
    )
    op.drop_table("family_discount_rules")

    op.drop_index(
        "ix_promo_code_redemptions_guardian_user_id",
        table_name="promo_code_redemptions",
    )
    op.drop_index(
        "ix_promo_code_redemptions_promo_code_id",
        table_name="promo_code_redemptions",
    )
    op.drop_table("promo_code_redemptions")

    op.drop_index("ix_promo_codes_code", table_name="promo_codes")
    op.drop_table("promo_codes")

    op.drop_table("stripe_events")
