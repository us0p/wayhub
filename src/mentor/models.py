"""Shared column helpers. Each feature package declares its own models on `mentor.db.Base`;
`mentor.db.import_models()` imports them all so Alembic and tests see the full metadata."""

import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from typing import Any

from sqlalchemy import DateTime, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# A worker task not finished this long after it was queued is taken as lost (the worker died or
# restarted mid-task): the page stops waiting and offers to try again, at no cost (M10).
LOST_AFTER = timedelta(minutes=5)


def requested_at() -> Mapped[datetime]:
    """When the task was last queued (set again when it's retried)."""
    return mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


def is_lost(requested: datetime) -> bool:
    return datetime.now(UTC) - requested > LOST_AFTER


def str_enum[E: StrEnum](enum: type[E], name: str) -> SAEnum:
    """A Postgres enum type storing the members' values (not their names)."""
    return SAEnum(enum, name=name, values_callable=lambda e: [m.value for m in e])


def fit_to_column(model: type[Any], name: str, value: object) -> object:
    """Clip strings (and string list items) to their column's length, so one over-long value
    from the model doesn't fail the whole patch."""
    column_type = sa_inspect(model).columns[name].type
    if isinstance(value, Enum):  # a StrEnum is a str: slicing it would turn it into plain text
        return value
    if isinstance(value, str) and (length := getattr(column_type, "length", None)):
        return value[:length]
    if (
        isinstance(value, list)
        and isinstance(column_type, ARRAY)
        and (length := getattr(column_type.item_type, "length", None))
    ):
        return [v[:length] if isinstance(v, str) and not isinstance(v, Enum) else v for v in value]
    return value
