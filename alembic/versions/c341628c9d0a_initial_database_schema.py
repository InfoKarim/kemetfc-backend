"""Initial database schema

Revision ID: c341628c9d0a
Revises:
Create Date: 2026-08-12 17:27:43.150761
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c341628c9d0a"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "matches",
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("competition_id", sa.String(), nullable=False),
        sa.Column("season_id", sa.String(), nullable=False),
        sa.Column("home_team_id", sa.String(), nullable=False),
        sa.Column("away_team_id", sa.String(), nullable=False),
        sa.Column("match_date", sa.DateTime(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("match_id"),
    )

    op.create_table(
        "players",
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("first_name_ar", sa.String(), nullable=False),
        sa.Column("last_name_ar", sa.String(), nullable=False),
        sa.Column("first_name_en", sa.String(), nullable=False),
        sa.Column("last_name_en", sa.String(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("sex", sa.String(), nullable=False),
        sa.Column("physical_profile", sa.JSON(), nullable=False),
        sa.Column("technical_profile", sa.JSON(), nullable=False),
        sa.Column("mental_profile", sa.JSON(), nullable=False),
        sa.Column("match_performance", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("player_id"),
    )

    op.create_table(
        "data_records",
        sa.Column("record_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("data_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("original_file_path", sa.String(), nullable=False),
        sa.Column("analysis_id", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.PrimaryKeyConstraint("record_id"),
    )
    op.create_index(
        "ix_data_records_player_id",
        "data_records",
        ["player_id"],
        unique=False,
    )

    op.create_table(
        "videos",
        sa.Column("video_id", sa.String(), nullable=False),
        sa.Column("record_id", sa.String(), nullable=False),
        sa.Column("video_type", sa.String(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("location_id", sa.String(), nullable=False),
        sa.Column("capture_device", sa.String(), nullable=False),
        sa.Column("resolution", sa.String(), nullable=False),
        sa.Column("frame_rate_fps", sa.Float(), nullable=False),
        sa.Column("file_size_mb", sa.Float(), nullable=False),
        sa.Column("file_format", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("original_preserved", sa.Boolean(), nullable=False),
        sa.Column("ai_processing_status", sa.String(), nullable=False),
        sa.Column("ai_processed_at", sa.DateTime(), nullable=True),
        sa.Column("ai_model_version", sa.String(), nullable=True),
        sa.Column("ai_confidence_score", sa.Float(), nullable=True),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("review_reason", sa.String(), nullable=False),
        sa.Column("human_review_status", sa.String(), nullable=False),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_notes", sa.String(), nullable=True),
        sa.Column("analysis_approved", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("video_id"),
    )

    op.create_table(
        "analyses",
        sa.Column("analysis_id", sa.String(), nullable=False),
        sa.Column("video_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("analysis_type", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("processing_status", sa.String(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("weaknesses", sa.JSON(), nullable=False),
        sa.Column("recommendations", sa.JSON(), nullable=False),
        sa.Column("raw_output_path", sa.String(), nullable=True),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("human_review_status", sa.String(), nullable=False),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_notes", sa.String(), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("analysis_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("analyses")
    op.drop_table("videos")
    op.drop_index("ix_data_records_player_id", table_name="data_records")
    op.drop_table("data_records")
    op.drop_table("players")
    op.drop_table("matches")
