"""add model-version traceability fields to tracking_sessions

Revision ID: a6822f6a1653
Revises: 174e3d3ed98b

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6822f6a1653"
down_revision: Union[str, Sequence[str], None] = "174e3d3ed98b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tracking_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("player_detector_version", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("ball_detector_version", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("ball_model_status", sa.String(), nullable=False, server_default="missing")
        )
        batch_op.add_column(sa.Column("pose_model_version", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("tracker_algorithm_version", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("framing_algorithm_version", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("ios_app_version", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tracking_sessions", schema=None) as batch_op:
        batch_op.drop_column("ios_app_version")
        batch_op.drop_column("framing_algorithm_version")
        batch_op.drop_column("tracker_algorithm_version")
        batch_op.drop_column("pose_model_version")
        batch_op.drop_column("ball_model_status")
        batch_op.drop_column("ball_detector_version")
        batch_op.drop_column("player_detector_version")
