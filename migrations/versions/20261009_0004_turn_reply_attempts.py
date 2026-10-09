"""turn reply attempts

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09 03:08:42.144067
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interview_turns",
        sa.Column("reply_attempts", sa.SmallInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("interview_turns", "reply_attempts")
