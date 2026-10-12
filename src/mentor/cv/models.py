"""Generated CVs (M9, D10 to D12, D22, D81): one row per version of a CV for a job posting. Each
generation is a new version; the content is the structured CV (`schemas.CVContent`) that the
page, the PDF and the DOCX are all rendered from, and `changes` is what the verification pass
removed or rewrote (shown to the user)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mentor.db import Base
from mentor.models import created_at, str_enum, uuid_pk


class CVStatus(StrEnum):
    PENDING = "pending"  # queued or being written
    READY = "ready"
    FAILED = "failed"


class CV(Base):
    __tablename__ = "cvs"
    __table_args__ = (UniqueConstraint("job_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)  # 1, 2... per posting
    language: Mapped[str] = mapped_column(String(10))  # pt-BR, en, es...
    status: Mapped[CVStatus] = mapped_column(
        str_enum(CVStatus, "cv_status"),
        default=CVStatus.PENDING,
        server_default=CVStatus.PENDING.value,
    )
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # `CVContent`
    changes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)  # `[Change]`
    created_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
