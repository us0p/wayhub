"""Usage quotas (D18, D31, D39).

Limits come from the user's plan, unless `user.quota_override` has the kind as a key
(int = custom limit, null = unlimited). Daily windows reset at midnight America/Sao_Paulo;
voice seconds are a lifetime allowance.

Note: check-then-record is not atomic; two concurrent requests can overshoot a limit by one
action. Acceptable for cost control at MVP scale.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import Plan, User
from mentor.auth.sessions import now
from mentor.quotas.models import QuotaKind, UsageEvent

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


class Window(StrEnum):
    DAILY = "daily"
    LIFETIME = "lifetime"


WINDOWS: dict[QuotaKind, Window] = {
    QuotaKind.INTERVIEW_TURN: Window.DAILY,
    QuotaKind.JOB_IMPORT: Window.DAILY,
    QuotaKind.CV_GENERATION: Window.DAILY,
    QuotaKind.VOICE_SECONDS: Window.LIFETIME,
}

_UNLIMITED: dict[QuotaKind, int | None] = dict.fromkeys(QuotaKind, None)

PLAN_LIMITS: dict[Plan, dict[QuotaKind, int | None]] = {
    Plan.FREE: {
        QuotaKind.INTERVIEW_TURN: 150,
        QuotaKind.JOB_IMPORT: 10,
        QuotaKind.CV_GENERATION: 10,
        QuotaKind.VOICE_SECONDS: 30 * 60,
    },
    Plan.TESTER: _UNLIMITED,
    Plan.ADMIN: _UNLIMITED,
}


@dataclass(frozen=True)
class QuotaStatus:
    kind: QuotaKind
    limit: int | None  # None = unlimited
    used: int
    resets_at: datetime | None  # None = lifetime allowance

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.used, 0)


class QuotaExceededError(Exception):
    def __init__(self, status: QuotaStatus) -> None:
        super().__init__(f"quota exceeded: {status.kind}")
        self.status = status


def effective_limit(user: User, kind: QuotaKind) -> int | None:
    if kind.value in user.quota_override:
        value = user.quota_override[kind.value]
        return None if value is None else int(value)
    return PLAN_LIMITS[user.plan][kind]


def _window_bounds(kind: QuotaKind, at: datetime) -> tuple[datetime | None, datetime | None]:
    if WINDOWS[kind] is Window.LIFETIME:
        return None, None
    local_day = at.astimezone(LOCAL_TZ).date()
    start = datetime.combine(local_day, time.min, tzinfo=LOCAL_TZ)
    return start, start + timedelta(days=1)


async def status(db: AsyncSession, user: User, kind: QuotaKind) -> QuotaStatus:
    start, end = _window_bounds(kind, now())
    query = select(func.coalesce(func.sum(UsageEvent.amount), 0)).where(
        UsageEvent.user_id == user.id, UsageEvent.kind == kind
    )
    if start is not None:
        query = query.where(UsageEvent.created_at >= start)
    used = int(await db.scalar(query) or 0)
    return QuotaStatus(kind=kind, limit=effective_limit(user, kind), used=used, resets_at=end)


async def check(db: AsyncSession, user: User, kind: QuotaKind, amount: int = 1) -> QuotaStatus:
    """Raise `QuotaExceededError` if `amount` more units would pass the limit."""
    current = await status(db, user, kind)
    if current.limit is not None and current.used + amount > current.limit:
        raise QuotaExceededError(current)
    return current


async def record(db: AsyncSession, user: User, kind: QuotaKind, amount: int = 1) -> None:
    db.add(UsageEvent(user_id=user.id, kind=kind, amount=amount))
    await db.flush()


async def summary(db: AsyncSession, user: User) -> list[QuotaStatus]:
    return [await status(db, user, kind) for kind in QuotaKind]
