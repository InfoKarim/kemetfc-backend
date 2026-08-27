"""add guardian consents and audit events

Revision ID: 41b712e8c4a1
Revises: 96f31e54c7a2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "41b712e8c4a1"
down_revision: Union[str, Sequence[str], None] = "96f31e54c7a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guardian_consents",
        sa.Column("consent_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("guardian_name", sa.String(), nullable=False),
        sa.Column("guardian_email", sa.String(), nullable=False),
        sa.Column("verification_method", sa.String(), nullable=False),
        sa.Column("purposes", sa.JSON(), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(), nullable=True),
        sa.Column("recorded_by_user_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("consent_id"),
    )
    op.create_index("ix_guardian_consents_player_id", "guardian_consents", ["player_id"])
    op.create_index("ix_guardian_consents_expires_at", "guardian_consents", ["expires_at"])
    op.create_index("ix_guardian_consents_recorded_by_user_id", "guardian_consents", ["recorded_by_user_id"])
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    for column in ("occurred_at", "actor_user_id", "action", "resource_type", "resource_id"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])


def downgrade() -> None:
    for column in ("resource_id", "resource_type", "action", "actor_user_id", "occurred_at"):
        op.drop_index(f"ix_audit_events_{column}", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_guardian_consents_recorded_by_user_id", table_name="guardian_consents")
    op.drop_index("ix_guardian_consents_expires_at", table_name="guardian_consents")
    op.drop_index("ix_guardian_consents_player_id", table_name="guardian_consents")
    op.drop_table("guardian_consents")
