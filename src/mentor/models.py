"""Shared column helpers. Each feature package declares its own models on `mentor.db.Base`;
`mentor.db.import_models()` imports them all so Alembic and tests see the full metadata."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def str_enum[E: StrEnum](enum: type[E], name: str) -> Enum:
    """A Postgres enum type storing the members' values (not their names)."""
    return Enum(enum, name=name, values_callable=lambda e: [m.value for m in e])
