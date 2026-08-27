"""add video analysis review

Revision ID: dbec8c6fff8d
Revises: c1f05d99c019
Create Date: 2026-08-17 20:08:02.676490

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dbec8c6fff8d'
down_revision: Union[str, Sequence[str], None] = 'c1f05d99c019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add coach review fields to video analysis jobs."""
    with op.batch_alter_table("video_analysis_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "review_status",
                sa.String(),
                server_default="pending",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("reviewed_by", sa.String(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reviewed_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("review_notes", sa.String(), nullable=True)
        )


def downgrade() -> None:
    """Remove coach review fields from video analysis jobs."""
    with op.batch_alter_table("video_analysis_jobs") as batch_op:
        batch_op.drop_column("review_notes")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("review_status")
