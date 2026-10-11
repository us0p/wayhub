"""The fit warning (M8, D13, D80): how a job posting's requirements compare with the
candidate's profile. One report per posting (the latest analysis), one item per requirement."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mentor.db import Base
from mentor.models import str_enum, uuid_pk


class FitStatus(StrEnum):
    PENDING = "pending"  # queued or running
    READY = "ready"
    NO_PROFILE = "no_profile"  # nothing to compare yet: the interview hasn't started
    FAILED = "failed"


class Verdict(StrEnum):
    MET = "met"
    PARTIAL = "partial"
    GAP = "gap"
    UNKNOWN = "unknown"  # the profile says nothing about it (a driver's licence, travel...)


class FitReport(Base):
    __tablename__ = "fit_reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[FitStatus] = mapped_column(
        str_enum(FitStatus, "fit_status"),
        default=FitStatus.PENDING,
        server_default=FitStatus.PENDING.value,
    )
    # Digest of the profile the analysis read: when it differs from today's, the report is
    # stale and the page offers to redo it.
    profile_digest: Mapped[str | None] = mapped_column(String(64))
    # Whether the interview had been completed when it ran (an incomplete one gets a notice).
    interview_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["FitItem"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", passive_deletes=True
    )


class FitItem(Base):
    __tablename__ = "fit_items"
    __table_args__ = (UniqueConstraint("report_id", "requirement_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    # Indexed by the unique constraint, which starts with it.
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fit_reports.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_requirements.id", ondelete="CASCADE")
    )
    verdict: Mapped[Verdict] = mapped_column(str_enum(Verdict, "fit_verdict"))
    # One factual sentence ("A vaga pede 5 anos de Python; você declarou 3."). NULL when the
    # model's answer was dropped (no evidence for a "met"), and the page words it.
    message: Mapped[str | None] = mapped_column(Text)
    # Profile facts (experiences, skills, education, languages) that back the verdict; the
    # CV generation (M9) starts from them.
    evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default="{}", default=list
    )

    report: Mapped[FitReport] = relationship(back_populates="items")
