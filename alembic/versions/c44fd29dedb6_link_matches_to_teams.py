"""link matches to teams

Revision ID: c44fd29dedb6
Revises: f7a0563f2071
Create Date: 2026-08-17 16:49:16.214877

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c44fd29dedb6'
down_revision: Union[str, Sequence[str], None] = 'f7a0563f2071'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Link match teams to the teams table."""
    with op.batch_alter_table("matches") as batch_op:
        batch_op.create_index(
            "ix_matches_away_team_id",
            ["away_team_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_matches_home_team_id",
            ["home_team_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_matches_away_team_id_teams",
            "teams",
            ["away_team_id"],
            ["team_id"],
        )
        batch_op.create_foreign_key(
            "fk_matches_home_team_id_teams",
            "teams",
            ["home_team_id"],
            ["team_id"],
        )


def downgrade() -> None:
    """Remove match-to-team links."""
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_constraint(
            "fk_matches_home_team_id_teams",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_matches_away_team_id_teams",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_matches_home_team_id")
        batch_op.drop_index("ix_matches_away_team_id")
