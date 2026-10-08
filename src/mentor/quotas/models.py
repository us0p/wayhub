import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from mentor.db import Base
from mentor.models import created_at, uuid_pk


class QuotaKind(StrEnum):
    INTERVIEW_TURN = "interview_turn"
    JOB_IMPORT = "job_import"
    CV_GENERATION = "cv_generation"
    VOICE_SECONDS = "voice_seconds"


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_user_kind_time", "user_id", "kind", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[QuotaKind] = mapped_column(
        Enum(QuotaKind, name="quota_kind", values_callable=lambda e: [m.value for m in e])
    )
    amount: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at()
