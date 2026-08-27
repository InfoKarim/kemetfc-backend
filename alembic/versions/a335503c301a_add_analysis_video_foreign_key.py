"""add analysis video foreign key

Revision ID: a335503c301a
Revises: aecde8e5fd87
Create Date: 2026-08-14 22:51:53.013501

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a335503c301a'
down_revision: Union[str, Sequence[str], None] = 'aecde8e5fd87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("analyses") as batch_op:
        batch_op.create_index(
            "ix_analyses_video_id",
            ["video_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_analyses_video_id_videos",
            "videos",
            ["video_id"],
            ["video_id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("analyses") as batch_op:
        batch_op.drop_constraint(
            "fk_analyses_video_id_videos",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_analyses_video_id")
