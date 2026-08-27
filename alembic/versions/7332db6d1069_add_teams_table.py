"""add teams table

Revision ID: 7332db6d1069
Revises: 436ec133c6d9
Create Date: 2026-08-17 12:01:29.344615

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7332db6d1069'
down_revision: Union[str, Sequence[str], None] = '436ec133c6d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "teams",
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("age_group", sa.String(), nullable=False),
        sa.Column("coach_name", sa.String(), nullable=False),
        sa.Column("season_id", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("team_id"),
    )
    op.create_index(
        op.f("ix_teams_age_group"),
        "teams",
        ["age_group"],
        unique=False,
    )
    op.create_index(
        op.f("ix_teams_season_id"),
        "teams",
        ["season_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_teams_season_id"),
        table_name="teams",
    )
    op.drop_index(
        op.f("ix_teams_age_group"),
        table_name="teams",
    )
    op.drop_table("teams")
