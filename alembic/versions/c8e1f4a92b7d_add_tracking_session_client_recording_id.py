"""add tracking session client_recording_id for idempotent retries

Revision ID: c8e1f4a92b7d
Revises: b3f9d1a7c2e4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e1f4a92b7d"
down_revision: Union[str, Sequence[str], None] = "b3f9d1a7c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tracking_sessions") as batch_op:
        batch_op.add_column(sa.Column(
            "client_recording_id",
            sa.String(),
            nullable=True,
        ))
        batch_op.create_unique_constraint(
            "uq_tracking_sessions_client_recording_id",
            ["client_recording_id"],
        )
    op.create_index(
        "ix_tracking_sessions_client_recording_id",
        "tracking_sessions",
        ["client_recording_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tracking_sessions_client_recording_id", table_name="tracking_sessions")
    with op.batch_alter_table("tracking_sessions") as batch_op:
        batch_op.drop_constraint("uq_tracking_sessions_client_recording_id", type_="unique")
        batch_op.drop_column("client_recording_id")
