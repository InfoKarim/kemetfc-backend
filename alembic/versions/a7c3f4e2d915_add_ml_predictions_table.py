"""add ml_predictions table

Revision ID: a7c3f4e2d915
Revises: f1a2c9d84e6b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c3f4e2d915"
down_revision: Union[str, Sequence[str], None] = "f1a2c9d84e6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ml_predictions",
        sa.Column("prediction_id", sa.String(), primary_key=True),
        sa.Column("player_id", sa.String(), sa.ForeignKey("players.player_id"), nullable=False),
        sa.Column("metric_name", sa.String(), nullable=False),
        sa.Column("model_scope", sa.String(), nullable=False),
        sa.Column("current_score", sa.Float(), nullable=False),
        sa.Column("predicted_score", sa.Float(), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("forecast_days", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("training_sample_count", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("lower_bound", sa.Float(), nullable=True),
        sa.Column("upper_bound", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ml_predictions_player_id", "ml_predictions", ["player_id"])
    op.create_index("ix_ml_predictions_metric_name", "ml_predictions", ["metric_name"])
    op.create_index("ix_ml_predictions_created_at", "ml_predictions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ml_predictions_created_at", table_name="ml_predictions")
    op.drop_index("ix_ml_predictions_metric_name", table_name="ml_predictions")
    op.drop_index("ix_ml_predictions_player_id", table_name="ml_predictions")
    op.drop_table("ml_predictions")
