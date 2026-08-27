"""Add analysis player foreign key

Revision ID: d7a3583c8166
Revises: c341628c9d0a
Create Date: 2026-08-13 10:08:51.713421

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7a3583c8166'
down_revision: Union[str, Sequence[str], None] = 'c341628c9d0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("analyses") as batch_op:
        batch_op.create_index(
            "ix_analyses_player_id",
            ["player_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_analyses_player_id_players",
            "players",
            ["player_id"],
            ["player_id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("analyses") as batch_op:
        batch_op.drop_constraint(
            "fk_analyses_player_id_players",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_analyses_player_id")
