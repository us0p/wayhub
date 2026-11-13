"""interview attachments: a long pasted text sent with a message, stored whole (D92)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-12 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("interview_turns", sa.Column("attachment_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_turns", "attachment_text")
