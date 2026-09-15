"""generalize physical_assessments into player_assessments (pillar column)

Revision ID: a39119689fcd
Revises: d9d51f1842b3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a39119689fcd"
down_revision: Union[str, Sequence[str], None] = "d9d51f1842b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("physical_assessments", "player_assessments")

    with op.batch_alter_table("player_assessments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "pillar",
                sa.String(),
                nullable=False,
                server_default="physical",
            )
        )
        batch_op.alter_column("pillar", server_default=None)
        batch_op.create_index(
            "ix_player_assessments_pillar",
            ["pillar"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("player_assessments", schema=None) as batch_op:
        batch_op.drop_index("ix_player_assessments_pillar")
        batch_op.drop_column("pillar")

    op.rename_table("player_assessments", "physical_assessments")
