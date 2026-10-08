import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mentor.db import Base
from mentor.models import created_at, uuid_pk


class Plan(StrEnum):
    FREE = "free"
    TESTER = "tester"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Google `sub` claim; dev-login users get "dev:<email>" (D42).
    google_sub: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[Plan] = mapped_column(
        Enum(Plan, name="plan", values_callable=lambda e: [m.value for m in e]),
        default=Plan.FREE,
        server_default=Plan.FREE.value,
    )
    # Per-user quota overrides (D18): {"<QuotaKind>": int | null}; null = unlimited.
    quota_override: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = created_at()

    consents: Mapped[list["Consent"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class UserSession(Base):
    """Server-side login session (D41). Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "user_sessions"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = created_at()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")


class Consent(Base):
    """Acceptance of specific versions of the legal documents (D21, D40)."""

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    terms_version: Mapped[str] = mapped_column(String(20))
    privacy_version: Mapped[str] = mapped_column(String(20))
    accepted_at: Mapped[datetime] = created_at()

    user: Mapped[User] = relationship(back_populates="consents")
