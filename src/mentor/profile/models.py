"""The candidate's profile: facts gathered by the interview (D14) and edited later (D15).

Every fact carries `source_turn_id`, the interview turn it was last stated in (provenance for
Model A, D12). Skills are normalized against a shared `skills` table (D19).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mentor.db import Base
from mentor.models import created_at, str_enum, uuid_pk

# Fixed per database (D46): changing the embedding model or size means a migration + re-embed.
EMBEDDING_DIMENSIONS = 768


def _source_turn() -> Mapped[uuid.UUID | None]:
    # SET NULL: a fact outlives the turn it came from (turns are not deleted today, but a
    # transcript purge must never drop profile facts).
    return mapped_column(ForeignKey("interview_turns.id", ondelete="SET NULL"))


def _owner() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"  # staff/principal/specialist
    LEAD = "lead"  # tech lead / manager


class Profile(Base):
    """One row per user: contact details and job preferences."""

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    full_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(120))
    headline: Mapped[str | None] = mapped_column(String(300))
    work_modes: Mapped[list[WorkMode]] = mapped_column(
        ARRAY(str_enum(WorkMode, "work_mode")), server_default="{}", default=list
    )
    desired_locations: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), server_default="{}", default=list
    )
    open_to_relocation: Mapped[bool | None] = mapped_column(Boolean)
    seniority: Mapped[Seniority | None] = mapped_column(str_enum(Seniority, "seniority"))
    target_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), server_default="{}", default=list
    )
    source_turn_id: Mapped[uuid.UUID | None] = _source_turn()
    updated_at: Mapped[datetime] = _updated_at()


class Experience(Base):
    __tablename__ = "experiences"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    company: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    # Month precision: the day is always 1.
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    employment_type: Mapped[str | None] = mapped_column(String(60))
    is_technical: Mapped[bool | None] = mapped_column(Boolean)
    source_turn_id: Mapped[uuid.UUID | None] = _source_turn()
    created_at: Mapped[datetime] = created_at()

    bullets: Mapped[list["ExperienceBullet"]] = relationship(
        back_populates="experience",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExperienceBullet.created_at",
    )


class ExperienceBullet(Base):
    """One statement about an experience (responsibility, achievement, tool used)."""

    __tablename__ = "experience_bullets"
    __table_args__ = (
        Index(
            "ix_experience_bullets_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    experience_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiences.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    source_turn_id: Mapped[uuid.UUID | None] = _source_turn()
    created_at: Mapped[datetime] = created_at()

    experience: Mapped[Experience] = relationship(back_populates="bullets")


class EducationStatus(StrEnum):
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    INCOMPLETE = "incomplete"


class Education(Base):
    __tablename__ = "education"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    institution: Mapped[str | None] = mapped_column(String(200))
    degree: Mapped[str | None] = mapped_column(String(200))  # e.g. Bacharelado, Tecnólogo
    field: Mapped[str | None] = mapped_column(String(200))
    start_year: Mapped[int | None] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[EducationStatus | None] = mapped_column(
        str_enum(EducationStatus, "education_status")
    )
    source_turn_id: Mapped[uuid.UUID | None] = _source_turn()
    created_at: Mapped[datetime] = created_at()


class Language(Base):
    __tablename__ = "languages"
    __table_args__ = (UniqueConstraint("user_id", "language"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    language: Mapped[str] = mapped_column(String(80))
    level: Mapped[str | None] = mapped_column(String(80))  # as stated: "fluente", "B2"...
    source_turn_id: Mapped[uuid.UUID | None] = _source_turn()
    created_at: Mapped[datetime] = created_at()


class Link(Base):
    __tablename__ = "links"
    __table_args__ = (UniqueConstraint("user_id", "url"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    kind: Mapped[str] = mapped_column(String(40))  # linkedin, github, portfolio, other
    url: Mapped[str] = mapped_column(String(500))  # http(s) only, validated on write
    source_turn_id: Mapped[uuid.UUID | None] = _source_turn()
    created_at: Mapped[datetime] = created_at()


class SkillStatus(StrEnum):
    APPROVED = "approved"
    UNCATEGORIZED = "uncategorized"  # created from a user's words, awaiting curation


class Skill(Base):
    """Shared canonical skill (D19). Not user-owned: survives account deletion, holds no PII."""

    __tablename__ = "skills"
    __table_args__ = (
        Index("uq_skills_name_lower", func.lower(text("name")), unique=True),
        Index(
            "ix_skills_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120))
    # Lowercased alternative spellings that resolve to this skill.
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), server_default="{}", default=list
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    status: Mapped[SkillStatus] = mapped_column(
        str_enum(SkillStatus, "skill_status"),
        default=SkillStatus.UNCATEGORIZED,
        server_default=SkillStatus.UNCATEGORIZED.value,
    )
    created_at: Mapped[datetime] = created_at()


class UserSkill(Base):
    __tablename__ = "user_skills"
    __table_args__ = (UniqueConstraint("user_id", "skill_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _owner()
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"))
    raw_name: Mapped[str] = mapped_column(String(120))  # as the candidate said it
    years: Mapped[float | None] = mapped_column(Float)  # as declared; M8 also derives from dates
    level: Mapped[str | None] = mapped_column(String(60))
    # Experiences the candidate used the skill in (evidence for fit and CV, D12/D13).
    experience_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}", default=list
    )
    source_turn_id: Mapped[uuid.UUID | None] = _source_turn()
    created_at: Mapped[datetime] = created_at()

    skill: Mapped[Skill] = relationship(lazy="joined")
