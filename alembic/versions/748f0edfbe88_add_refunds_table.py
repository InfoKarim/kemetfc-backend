"""add refunds table

Revision ID: 748f0edfbe88
Revises: 3d3a3dce9bf6

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "748f0edfbe88"
down_revision: Union[str, Sequence[str], None] = "3d3a3dce9bf6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("refund_id", sa.String(), nullable=False),
        sa.Column("payment_id", sa.String(), nullable=True),
        sa.Column("manual_payment_id", sa.String(), nullable=True),
        sa.Column("stripe_refund_id", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("refund_source", sa.String(), nullable=False),
        sa.Column("refund_type", sa.String(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("internal_note", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.stripe_invoice_id"]),
        sa.ForeignKeyConstraint(["manual_payment_id"], ["manual_payments.manual_payment_id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("refund_id"),
    )
    op.create_index("ix_refunds_payment_id", "refunds", ["payment_id"], unique=False)
    op.create_index(
        "ix_refunds_manual_payment_id", "refunds", ["manual_payment_id"], unique=False
    )
    op.create_index(
        "ix_refunds_stripe_refund_id", "refunds", ["stripe_refund_id"], unique=True
    )
    op.create_index(
        "ix_refunds_idempotency_key", "refunds", ["idempotency_key"], unique=True
    )
    op.create_index("ix_refunds_created_at", "refunds", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_refunds_created_at", table_name="refunds")
    op.drop_index("ix_refunds_idempotency_key", table_name="refunds")
    op.drop_index("ix_refunds_stripe_refund_id", table_name="refunds")
    op.drop_index("ix_refunds_manual_payment_id", table_name="refunds")
    op.drop_index("ix_refunds_payment_id", table_name="refunds")
    op.drop_table("refunds")
