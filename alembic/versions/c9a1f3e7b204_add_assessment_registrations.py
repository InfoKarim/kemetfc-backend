"""add assessment registrations table

Revision ID: c9a1f3e7b204
Revises: f6c93e2a8b1d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9a1f3e7b204"
down_revision: Union[str, Sequence[str], None] = "f6c93e2a8b1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assessment_registrations",
        sa.Column("registration_id", sa.String(), nullable=False),
        sa.Column("parent_name", sa.String(), nullable=False),
        sa.Column("parent_email", sa.String(), nullable=False),
        sa.Column("parent_phone", sa.String(), nullable=False),
        sa.Column("emergency_contact", sa.String(), nullable=False),
        sa.Column("player_name", sa.String(), nullable=False),
        sa.Column("player_date_of_birth", sa.Date(), nullable=False),
        sa.Column("player_age", sa.Integer(), nullable=False),
        sa.Column("preferred_position", sa.String(), nullable=True),
        sa.Column("experience_level", sa.String(), nullable=True),
        sa.Column("current_team", sa.String(), nullable=True),
        sa.Column("consents", sa.JSON(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("registration_id"),
    )
    op.create_index(
        op.f("ix_assessment_registrations_parent_email"),
        "assessment_registrations",
        ["parent_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_registrations_submitted_at"),
        "assessment_registrations",
        ["submitted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_assessment_registrations_submitted_at"),
        table_name="assessment_registrations",
    )
    op.drop_index(
        op.f("ix_assessment_registrations_parent_email"),
        table_name="assessment_registrations",
    )
    op.drop_table("assessment_registrations")
