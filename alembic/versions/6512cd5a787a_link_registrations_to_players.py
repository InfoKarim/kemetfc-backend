"""link registrations to players

Revision ID: 6512cd5a787a
Revises: 795f33cbe67d

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6512cd5a787a"
down_revision: Union[str, Sequence[str], None] = "795f33cbe67d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("assessment_registrations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                server_default="submitted",
            )
        )
        batch_op.add_column(sa.Column("player_id", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("linked_by_user_id", sa.String(), nullable=True)
        )
        batch_op.add_column(sa.Column("linked_at", sa.DateTime(), nullable=True))
        batch_op.create_index(
            "ix_assessment_registrations_player_id",
            ["player_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_assessment_registrations_player_id_players",
            "players",
            ["player_id"],
            ["player_id"],
        )
        batch_op.create_foreign_key(
            "fk_assessment_registrations_linked_by_user_id_users",
            "users",
            ["linked_by_user_id"],
            ["user_id"],
        )

    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(),
                nullable=False,
                server_default="manual",
            )
        )
        batch_op.add_column(
            sa.Column("created_by_user_id", sa.String(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_players_created_by_user_id_users",
            "users",
            ["created_by_user_id"],
            ["user_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("players", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_players_created_by_user_id_users", type_="foreignkey"
        )
        batch_op.drop_column("created_by_user_id")
        batch_op.drop_column("source")

    with op.batch_alter_table("assessment_registrations", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_assessment_registrations_linked_by_user_id_users",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_assessment_registrations_player_id_players", type_="foreignkey"
        )
        batch_op.drop_index("ix_assessment_registrations_player_id")
        batch_op.drop_column("linked_at")
        batch_op.drop_column("linked_by_user_id")
        batch_op.drop_column("player_id")
        batch_op.drop_column("status")
