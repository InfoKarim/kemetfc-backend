"""add tracking_samples.identity_ambiguous

Additive and non-destructive: one nullable boolean. Existing rows keep NULL,
which readers treat as "unknown" (never as "not ambiguous").

Revision ID: c9e1a4b7d302
Revises: a47c1e9d3b52
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9e1a4b7d302"
down_revision: Union[str, Sequence[str], None] = "a47c1e9d3b52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tracking_samples") as batch_op:
        batch_op.add_column(sa.Column("identity_ambiguous", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tracking_samples") as batch_op:
        batch_op.drop_column("identity_ambiguous")
