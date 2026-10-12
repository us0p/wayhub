"""Imported job postings (M7, D9, D22, D78). A posting is created when the user submits it and
filled in by the worker: the source's text, then the fields and requirements the model read
from it. Raw uploads live in `job_uploads` only until the worker has read them (D29)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mentor.db import Base
from mentor.models import created_at, is_lost, requested_at, str_enum, uuid_pk
from mentor.profile.models import Seniority, WorkMode


class JobSource(StrEnum):
    TEXT = "text"  # pasted posting
    URL = "url"
    PDF = "pdf"
    IMAGES = "images"  # one or more screenshots of the same posting


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    NEEDS_MANUAL = "needs_manual"  # the link couldn't be read: paste the text or send prints
    NOT_A_JOB = "not_a_job"  # the content isn't a job posting
    FAILED = "failed"  # gave up after retries


class RequirementImportance(StrEnum):
    MUST = "must"
    NICE = "nice"


class RequirementKind(StrEnum):
    SKILL = "skill"  # a technology, tool or competence
    EXPERIENCE = "experience"  # years or kind of experience not tied to one skill
    LANGUAGE = "language"  # a spoken language (English, Spanish...)
    EDUCATION = "education"  # degree, course, certification
    OTHER = "other"


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (Index("ix_job_postings_user_seq", "user_id", "seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[JobSource] = mapped_column(str_enum(JobSource, "job_source"))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"),
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    # Why it needs the text pasted (`needs_manual`); a short code, the UI words it.
    status_reason: Mapped[str | None] = mapped_column(String(40))
    # The posting as text: pasted, or extracted from the page, PDF or screenshots.
    source_text: Mapped[str | None] = mapped_column(Text)

    title: Mapped[str | None] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    work_mode: Mapped[WorkMode | None] = mapped_column(str_enum(WorkMode, "work_mode"))
    seniority: Mapped[Seniority | None] = mapped_column(str_enum(Seniority, "seniority"))
    language: Mapped[str | None] = mapped_column(String(10))  # the posting's, e.g. pt-BR, en
    is_tech: Mapped[bool | None] = mapped_column(Boolean)  # soft notice outside tech (D23)
    summary: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = created_at()
    requested_at: Mapped[datetime] = requested_at()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requirements: Mapped[list["JobRequirement"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="JobRequirement.position",
    )

    @property
    def lost(self) -> bool:
        """Still being read long after it was queued: the worker lost it (M10)."""
        return self.status in (JobStatus.QUEUED, JobStatus.PROCESSING) and is_lost(
            self.requested_at
        )


class JobRequirement(Base):
    __tablename__ = "job_requirements"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)  # order in the posting
    importance: Mapped[RequirementImportance] = mapped_column(
        str_enum(RequirementImportance, "requirement_importance")
    )
    kind: Mapped[RequirementKind] = mapped_column(str_enum(RequirementKind, "requirement_kind"))
    text: Mapped[str] = mapped_column(Text)  # as the posting puts it
    skill: Mapped[str | None] = mapped_column(String(120))  # canonical name, for skills
    min_years: Mapped[float | None] = mapped_column(Float)

    job: Mapped[JobPosting] = relationship(back_populates="requirements")


class JobUpload(Base):
    """A screenshot or PDF waiting for the worker; deleted once read (D29)."""

    __tablename__ = "job_uploads"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(SmallInteger)
    content_type: Mapped[str] = mapped_column(String(60))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = created_at()
