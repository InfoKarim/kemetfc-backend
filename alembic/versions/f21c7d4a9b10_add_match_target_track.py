"""add match target track

Revision ID: f21c7d4a9b10
Revises: e4b7c9021a4f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f21c7d4a9b10"
down_revision: Union[str, Sequence[str], None] = "e4b7c9021a4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("video_analysis_jobs") as batch_op:
        batch_op.add_column(
            sa.Column("target_track_id", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("video_analysis_jobs") as batch_op:
        batch_op.drop_column("target_track_id")
