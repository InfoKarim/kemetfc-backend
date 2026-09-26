"""add idempotent identity to tracking telemetry (sequence_number, client_event_id)

Additive and non-destructive: nullable columns plus UNIQUE indexes.

  tracking_samples.sequence_number      INTEGER NULL
  tracking_events.client_event_id       VARCHAR NULL
  tracking_events.client_occurred_at    DATETIME NULL
  UNIQUE (session_id, sequence_number)  on tracking_samples
  UNIQUE (session_id, client_event_id)  on tracking_events

Existing rows keep NULL identities and are never rewritten. NULLs do not
collide in a unique index (SQLite and PostgreSQL), so old rows and clients
that do not send an identity keep working exactly as before.

Revision ID: a47c1e9d3b52
Revises: d5f9a2c74e18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a47c1e9d3b52"
down_revision: Union[str, Sequence[str], None] = "d5f9a2c74e18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tracking_samples") as batch_op:
        batch_op.add_column(sa.Column("sequence_number", sa.Integer(), nullable=True))
    with op.batch_alter_table("tracking_events") as batch_op:
        batch_op.add_column(sa.Column("client_event_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("client_occurred_at", sa.DateTime(), nullable=True))

    op.create_index(
        "uq_tracking_samples_session_sequence",
        "tracking_samples",
        ["session_id", "sequence_number"],
        unique=True,
    )
    op.create_index(
        "uq_tracking_events_session_client_event",
        "tracking_events",
        ["session_id", "client_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_tracking_events_session_client_event", table_name="tracking_events")
    op.drop_index("uq_tracking_samples_session_sequence", table_name="tracking_samples")
    with op.batch_alter_table("tracking_events") as batch_op:
        batch_op.drop_column("client_occurred_at")
        batch_op.drop_column("client_event_id")
    with op.batch_alter_table("tracking_samples") as batch_op:
        batch_op.drop_column("sequence_number")
