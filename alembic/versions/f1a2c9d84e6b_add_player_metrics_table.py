"""add player_metrics table

Revision ID: f1a2c9d84e6b
Revises: 209b571faf12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2c9d84e6b"
down_revision: Union[str, Sequence[str], None] = "209b571faf12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_metrics",
        sa.Column("metric_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("player_id", sa.String(), sa.ForeignKey("players.player_id"), nullable=False),
        sa.Column("assessment_id", sa.String(), sa.ForeignKey("player_assessments.assessment_id"), nullable=False),
        sa.Column("metric_name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("metric_score", sa.Float(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("recorded_at", sa.Date(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_player_metrics_player_id", "player_metrics", ["player_id"])
    op.create_index("ix_player_metrics_assessment_id", "player_metrics", ["assessment_id"])
    op.create_index("ix_player_metrics_metric_name", "player_metrics", ["metric_name"])
    op.create_index("ix_player_metrics_category", "player_metrics", ["category"])
    op.create_index("ix_player_metrics_recorded_at", "player_metrics", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_player_metrics_recorded_at", table_name="player_metrics")
    op.drop_index("ix_player_metrics_category", table_name="player_metrics")
    op.drop_index("ix_player_metrics_metric_name", table_name="player_metrics")
    op.drop_index("ix_player_metrics_assessment_id", table_name="player_metrics")
    op.drop_index("ix_player_metrics_player_id", table_name="player_metrics")
    op.drop_table("player_metrics")
