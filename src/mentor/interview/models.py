import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from mentor.db import Base
from mentor.models import created_at, str_enum, uuid_pk


class InterviewStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"  # "Chega por agora" (D17); resuming sets it back to in_progress
    COMPLETE = "complete"


class TurnRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class TurnMode(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class Interview(Base):
    __tablename__ = "interviews"
    __table_args__ = (Index("ix_interviews_user_seq", "user_id", "seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    # Creation order; `created_at` ties for interviews created in one transaction.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[InterviewStatus] = mapped_column(
        str_enum(InterviewStatus, "interview_status"),
        default=InterviewStatus.IN_PROGRESS,
        server_default=InterviewStatus.IN_PROGRESS.value,
    )
    created_at: Mapped[datetime] = created_at()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterviewTurn(Base):
    """One message of the conversation. Only transcripts are stored, never audio (D21)."""

    __tablename__ = "interview_turns"
    __table_args__ = (Index("ix_interview_turns_interview_seq", "interview_id", "seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    # Insertion order; timestamps can tie inside one transaction.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    interview_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[TurnRole] = mapped_column(str_enum(TurnRole, "turn_role"))
    mode: Mapped[TurnMode] = mapped_column(
        str_enum(TurnMode, "turn_mode"), default=TurnMode.TEXT, server_default=TurnMode.TEXT.value
    )
    text: Mapped[str] = mapped_column(Text)
    # Assistant turns: the user turn they answer (one reply per user turn).
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interview_turns.id", ondelete="CASCADE"), unique=True
    )
    # User turns: set when a reply starts streaming, so only one stream generates it.
    reply_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # User turns: how many times a reply generation started. Capped, so retries and
    # reconnects can't spend unlimited model calls on one quota unit.
    reply_attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = created_at()


class ChecklistStatus(StrEnum):
    MISSING = "missing"
    PARTIAL = "partial"
    DONE = "done"


class ChecklistEntry(Base):
    """Coverage of one checklist item for a user (D14). Per user, not per interview: coverage
    describes the profile, so a follow-up interview starts from it."""

    __tablename__ = "interview_checklist"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    item: Mapped[str] = mapped_column(Text, primary_key=True)  # a ChecklistItem value
    status: Mapped[ChecklistStatus] = mapped_column(str_enum(ChecklistStatus, "checklist_status"))
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
