"""add ml dataset entries table

Revision ID: b6f2d4a19e05
Revises: a4e1f0c8d372
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6f2d4a19e05"
down_revision: Union[str, Sequence[str], None] = "a4e1f0c8d372"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ml_dataset_entries",
        sa.Column("entry_id", sa.String(), nullable=False),
        sa.Column("video_id", sa.String(), nullable=False),
        sa.Column("team_id", sa.String(), nullable=True),
        sa.Column("age_band", sa.String(), nullable=False),
        sa.Column("sex_cohort", sa.String(), nullable=False),
        sa.Column("camera_id", sa.String(), nullable=False),
        sa.Column("lighting", sa.String(), nullable=False),
        sa.Column("consent_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("flagged_by_user_id", sa.String(), nullable=False),
        sa.Column("flagged_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["video_id"], ["videos.video_id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.team_id"]),
        sa.ForeignKeyConstraint(["consent_id"], ["guardian_consents.consent_id"]),
        sa.ForeignKeyConstraint(["flagged_by_user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("entry_id"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_index(
        op.f("ix_ml_dataset_entries_video_id"),
        "ml_dataset_entries",
        ["video_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_ml_dataset_entries_video_id"),
        table_name="ml_dataset_entries",
    )
    op.drop_table("ml_dataset_entries")
