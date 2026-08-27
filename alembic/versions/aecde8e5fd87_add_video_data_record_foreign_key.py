from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aecde8e5fd87'
down_revision: Union[str, Sequence[str], None] = 'd7a3583c8166'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("videos") as batch_op:
        batch_op.create_index(
            "ix_videos_record_id",
            ["record_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_videos_record_id_data_records",
            "data_records",
            ["record_id"],
            ["record_id"],
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("videos") as batch_op:
        batch_op.drop_constraint(
            "fk_videos_record_id_data_records",
            type_="foreignkey",
        )
        batch_op.drop_index(
            "ix_videos_record_id",
        )


