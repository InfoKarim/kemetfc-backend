"""add drills table

Revision ID: 7651da172068
Revises: 6dc5700b0bd3
Create Date: 2026-08-16 10:14:56.543918

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '7651da172068'
down_revision: Union[str, Sequence[str], None] = '6dc5700b0bd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Retain the legacy revision without duplicating 6dc5700b0bd3."""
    pass


def downgrade() -> None:
    """The preceding revision remains responsible for the drills table."""
    pass
