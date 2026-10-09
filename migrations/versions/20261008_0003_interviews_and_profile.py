"""interviews and profile

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08 23:44:03.014358
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("aliases", sa.ARRAY(sa.String(length=120)), server_default="{}", nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
        sa.Column(
            "status",
            sa.Enum("approved", "uncategorized", name="skill_status"),
            server_default="uncategorized",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skills_embedding",
        "skills",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "uq_skills_name_lower", "skills", [sa.literal_column("lower(name)")], unique=True
    )
    op.create_table(
        "interview_checklist",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("item", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Enum("missing", "partial", "done", name="checklist_status"), nullable=False
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "item"),
    )
    op.create_table(
        "interviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("in_progress", "paused", "complete", name="interview_status"),
            server_default="in_progress",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
    )
    op.create_index("ix_interviews_user_seq", "interviews", ["user_id", "seq"], unique=False)
    op.create_table(
        "interview_turns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("interview_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Enum("user", "assistant", name="turn_role"), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("text", "voice", name="turn_mode"),
            server_default="text",
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("reply_to_id", sa.UUID(), nullable=True),
        sa.Column("reply_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["interview_id"], ["interviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reply_to_id"], ["interview_turns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reply_to_id"),
        sa.UniqueConstraint("seq"),
    )
    op.create_index(
        "ix_interview_turns_interview_seq", "interview_turns", ["interview_id", "seq"], unique=False
    )
    op.create_index(
        op.f("ix_interview_turns_user_id"), "interview_turns", ["user_id"], unique=False
    )
    op.create_table(
        "education",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("institution", sa.String(length=200), nullable=True),
        sa.Column("degree", sa.String(length=200), nullable=True),
        sa.Column("field", sa.String(length=200), nullable=True),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("completed", "in_progress", "incomplete", name="education_status"),
            nullable=True,
        ),
        sa.Column("source_turn_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_turn_id"], ["interview_turns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_education_user_id"), "education", ["user_id"], unique=False)
    op.create_table(
        "experiences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("employment_type", sa.String(length=60), nullable=True),
        sa.Column("is_technical", sa.Boolean(), nullable=True),
        sa.Column("source_turn_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_turn_id"], ["interview_turns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_experiences_user_id"), "experiences", ["user_id"], unique=False)
    op.create_table(
        "languages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("language", sa.String(length=80), nullable=False),
        sa.Column("level", sa.String(length=80), nullable=True),
        sa.Column("source_turn_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_turn_id"], ["interview_turns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "language"),
    )
    op.create_index(op.f("ix_languages_user_id"), "languages", ["user_id"], unique=False)
    op.create_table(
        "links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("source_turn_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_turn_id"], ["interview_turns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "url"),
    )
    op.create_index(op.f("ix_links_user_id"), "links", ["user_id"], unique=False)
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=120), nullable=True),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("headline", sa.String(length=300), nullable=True),
        sa.Column(
            "work_modes",
            sa.ARRAY(sa.Enum("remote", "hybrid", "onsite", name="work_mode")),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "desired_locations",
            sa.ARRAY(sa.String(length=120)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("open_to_relocation", sa.Boolean(), nullable=True),
        sa.Column(
            "seniority",
            sa.Enum("intern", "junior", "mid", "senior", "staff", "lead", name="seniority"),
            nullable=True,
        ),
        sa.Column(
            "target_roles", sa.ARRAY(sa.String(length=120)), server_default="{}", nullable=False
        ),
        sa.Column("source_turn_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_turn_id"], ["interview_turns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "user_skills",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("raw_name", sa.String(length=120), nullable=False),
        sa.Column("years", sa.Float(), nullable=True),
        sa.Column("level", sa.String(length=60), nullable=True),
        sa.Column("experience_ids", sa.ARRAY(sa.UUID()), server_default="{}", nullable=False),
        sa.Column("source_turn_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_turn_id"], ["interview_turns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "skill_id"),
    )
    op.create_index(op.f("ix_user_skills_user_id"), "user_skills", ["user_id"], unique=False)
    op.create_table(
        "experience_bullets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("experience_id", sa.UUID(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
        sa.Column("source_turn_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["experience_id"], ["experiences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_turn_id"], ["interview_turns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experience_bullets_embedding",
        "experience_bullets",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        op.f("ix_experience_bullets_experience_id"),
        "experience_bullets",
        ["experience_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_experience_bullets_user_id"), "experience_bullets", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_experience_bullets_user_id"), table_name="experience_bullets")
    op.drop_index(op.f("ix_experience_bullets_experience_id"), table_name="experience_bullets")
    op.drop_index(
        "ix_experience_bullets_embedding",
        table_name="experience_bullets",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("experience_bullets")
    op.drop_index(op.f("ix_user_skills_user_id"), table_name="user_skills")
    op.drop_table("user_skills")
    op.drop_table("profiles")
    op.drop_index(op.f("ix_links_user_id"), table_name="links")
    op.drop_table("links")
    op.drop_index(op.f("ix_languages_user_id"), table_name="languages")
    op.drop_table("languages")
    op.drop_index(op.f("ix_experiences_user_id"), table_name="experiences")
    op.drop_table("experiences")
    op.drop_index(op.f("ix_education_user_id"), table_name="education")
    op.drop_table("education")
    op.drop_index(op.f("ix_interview_turns_user_id"), table_name="interview_turns")
    op.drop_index("ix_interview_turns_interview_seq", table_name="interview_turns")
    op.drop_table("interview_turns")
    op.drop_index("ix_interviews_user_seq", table_name="interviews")
    op.drop_table("interviews")
    op.drop_table("interview_checklist")
    op.drop_index("uq_skills_name_lower", table_name="skills")
    op.drop_index(
        "ix_skills_embedding",
        table_name="skills",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("skills")
    sa.Enum(name="seniority").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="work_mode").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="education_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="turn_mode").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="turn_role").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="interview_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="checklist_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="skill_status").drop(op.get_bind(), checkfirst=True)
