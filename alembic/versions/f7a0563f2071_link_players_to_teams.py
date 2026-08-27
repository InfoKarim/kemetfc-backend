"""link players to teams

Revision ID: f7a0563f2071
Revises: 7332db6d1069
Create Date: 2026-08-17 13:24:55.890621

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a0563f2071'
down_revision: Union[str, Sequence[str], None] = '7332db6d1069'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table(
        "players",
        schema=None,
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "team_id",
                sa.String(),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_players_team_id",
            ["team_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_players_team_id_teams",
            "teams",
            ["team_id"],
            ["team_id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table(
        "players",
        schema=None,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_players_team_id_teams",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_players_team_id")
        batch_op.drop_column("team_id")
