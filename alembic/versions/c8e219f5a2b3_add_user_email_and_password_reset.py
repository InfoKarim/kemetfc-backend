"""add user email and password reset codes

Revision ID: c8e219f5a2b3
Revises: b32f6a8d901c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e219f5a2b3"
down_revision: Union[str, Sequence[str], None] = "b32f6a8d901c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column(
            "email",
            sa.String(),
            nullable=True,
        ))

    op.create_table(
        "password_reset_codes",
        sa.Column("reset_id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_password_reset_codes_user_id",
        "password_reset_codes",
        ["user_id"],
    )
    op.create_index(
        "ix_password_reset_codes_expires_at",
        "password_reset_codes",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_password_reset_codes_expires_at",
        table_name="password_reset_codes",
    )
    op.drop_index(
        "ix_password_reset_codes_user_id",
        table_name="password_reset_codes",
    )
    op.drop_table("password_reset_codes")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("email")
