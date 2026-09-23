"""add prediction_outcomes table

Revision ID: d5f9a2c74e18
Revises: c4e8b1a63f27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5f9a2c74e18"
down_revision: Union[str, Sequence[str], None] = "c4e8b1a63f27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prediction_outcomes",
        sa.Column("outcome_id", sa.String(), primary_key=True),
        sa.Column("prediction_id", sa.String(), sa.ForeignKey("ml_predictions.prediction_id"), nullable=False, unique=True),
        sa.Column("player_id", sa.String(), sa.ForeignKey("players.player_id"), nullable=False),
        sa.Column("metric_name", sa.String(), nullable=False),
        sa.Column("actual_score", sa.Float(), nullable=False),
        sa.Column("actual_date", sa.Date(), nullable=False),
        sa.Column("prediction_error", sa.Float(), nullable=False),
        sa.Column("absolute_error", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_prediction_outcomes_prediction_id", "prediction_outcomes", ["prediction_id"])
    op.create_index("ix_prediction_outcomes_player_id", "prediction_outcomes", ["player_id"])
    op.create_index("ix_prediction_outcomes_metric_name", "prediction_outcomes", ["metric_name"])
    op.create_index("ix_prediction_outcomes_actual_date", "prediction_outcomes", ["actual_date"])
    op.create_index("ix_prediction_outcomes_created_at", "prediction_outcomes", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_prediction_outcomes_created_at", table_name="prediction_outcomes")
    op.drop_index("ix_prediction_outcomes_actual_date", table_name="prediction_outcomes")
    op.drop_index("ix_prediction_outcomes_metric_name", table_name="prediction_outcomes")
    op.drop_index("ix_prediction_outcomes_player_id", table_name="prediction_outcomes")
    op.drop_index("ix_prediction_outcomes_prediction_id", table_name="prediction_outcomes")
    op.drop_table("prediction_outcomes")
