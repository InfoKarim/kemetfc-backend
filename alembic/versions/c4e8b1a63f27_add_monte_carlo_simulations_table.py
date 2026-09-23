"""add monte_carlo_simulations table

Revision ID: c4e8b1a63f27
Revises: a7c3f4e2d915
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e8b1a63f27"
down_revision: Union[str, Sequence[str], None] = "a7c3f4e2d915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monte_carlo_simulations",
        sa.Column("simulation_id", sa.String(), primary_key=True),
        sa.Column("prediction_id", sa.String(), sa.ForeignKey("ml_predictions.prediction_id"), nullable=False),
        sa.Column("player_id", sa.String(), sa.ForeignKey("players.player_id"), nullable=False),
        sa.Column("metric_name", sa.String(), nullable=False),
        sa.Column("num_simulations", sa.Integer(), nullable=False),
        sa.Column("median", sa.Float(), nullable=False),
        sa.Column("p05", sa.Float(), nullable=False),
        sa.Column("p10", sa.Float(), nullable=False),
        sa.Column("p25", sa.Float(), nullable=False),
        sa.Column("p50", sa.Float(), nullable=False),
        sa.Column("p75", sa.Float(), nullable=False),
        sa.Column("p90", sa.Float(), nullable=False),
        sa.Column("p95", sa.Float(), nullable=False),
        sa.Column("simulation_method", sa.String(), nullable=False),
        sa.Column("uncertainty_level", sa.String(), nullable=False),
        sa.Column("residual_sample_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_monte_carlo_simulations_prediction_id", "monte_carlo_simulations", ["prediction_id"])
    op.create_index("ix_monte_carlo_simulations_player_id", "monte_carlo_simulations", ["player_id"])
    op.create_index("ix_monte_carlo_simulations_metric_name", "monte_carlo_simulations", ["metric_name"])
    op.create_index("ix_monte_carlo_simulations_created_at", "monte_carlo_simulations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_monte_carlo_simulations_created_at", table_name="monte_carlo_simulations")
    op.drop_index("ix_monte_carlo_simulations_metric_name", table_name="monte_carlo_simulations")
    op.drop_index("ix_monte_carlo_simulations_player_id", table_name="monte_carlo_simulations")
    op.drop_index("ix_monte_carlo_simulations_prediction_id", table_name="monte_carlo_simulations")
    op.drop_table("monte_carlo_simulations")
