"""job postings

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-11 13:01:41.424818
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "source", sa.Enum("text", "url", "pdf", "images", name="job_source"), nullable=False
        ),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "processing",
                "ready",
                "needs_manual",
                "not_a_job",
                "failed",
                name="job_status",
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("status_reason", sa.String(length=40), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column(
            "work_mode",
            postgresql.ENUM("remote", "hybrid", "onsite", name="work_mode", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "seniority",
            postgresql.ENUM(
                "intern",
                "junior",
                "mid",
                "senior",
                "staff",
                "lead",
                name="seniority",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("is_tech", sa.Boolean(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
    )
    op.create_index("ix_job_postings_user_seq", "job_postings", ["user_id", "seq"], unique=False)
    op.create_table(
        "job_requirements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column(
            "importance", sa.Enum("must", "nice", name="requirement_importance"), nullable=False
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "skill", "experience", "language", "education", "other", name="requirement_kind"
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("skill", sa.String(length=120), nullable=True),
        sa.Column("min_years", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["job_postings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_job_requirements_job_id"), "job_requirements", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_job_requirements_user_id"), "job_requirements", ["user_id"], unique=False
    )
    op.create_table(
        "job_uploads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=60), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["job_postings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_uploads_job_id"), "job_uploads", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_job_uploads_job_id"), table_name="job_uploads")
    op.drop_table("job_uploads")
    op.drop_index(op.f("ix_job_requirements_user_id"), table_name="job_requirements")
    op.drop_index(op.f("ix_job_requirements_job_id"), table_name="job_requirements")
    op.drop_table("job_requirements")
    op.drop_index("ix_job_postings_user_seq", table_name="job_postings")
    op.drop_table("job_postings")
    # work_mode and seniority belong to the profile tables (0003).
    for name in ("requirement_kind", "requirement_importance", "job_status", "job_source"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
