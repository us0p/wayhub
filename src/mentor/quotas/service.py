"""Usage quotas (D18, D31, D85).

Every limit is a lifetime total per account, never reset (D85). Limits come from the user's
plan, unless `user.quota_override` has the kind as a key (int = custom limit, null =
unlimited): that's how an admin gives a user more, until buying credits exists.

`check` takes a per-user advisory lock held until the transaction ends, so a check, the action
and its `record` in one transaction can't interleave with another request of the same user:
with no next day to absorb it, a lifetime limit must not be overshot by concurrent requests.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import Plan, User
from mentor.quotas.models import QuotaKind, UsageEvent

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


async def status(db: AsyncSession, user: User, kind: QuotaKind) -> QuotaStatus:
    query = select(func.coalesce(func.sum(UsageEvent.amount), 0)).where(
        UsageEvent.user_id == user.id, UsageEvent.kind == kind
    )
    used = int(await db.scalar(query) or 0)
    return QuotaStatus(kind=kind, limit=effective_limit(user, kind), used=used)


def lock_key(user: User) -> int:
    """The user's advisory lock key: the first 8 bytes of the id, as a signed bigint."""
    return int.from_bytes(user.id.bytes[:8], "big", signed=True)


async def check(db: AsyncSession, user: User, kind: QuotaKind, amount: int = 1) -> QuotaStatus:
    """Raise `QuotaExceededError` if `amount` more units would pass the limit. Holds the user's
    lock until the transaction ends: record the usage in the same transaction."""
    if effective_limit(user, kind) is not None:
        await db.execute(select(func.pg_advisory_xact_lock(lock_key(user))))
    current = await status(db, user, kind)
    if current.limit is not None and current.used + amount > current.limit:
        raise QuotaExceededError(current)
    return current


async def record(db: AsyncSession, user: User, kind: QuotaKind, amount: int = 1) -> None:
    db.add(UsageEvent(user_id=user.id, kind=kind, amount=amount))
    await db.flush()


async def summary(db: AsyncSession, user: User) -> list[QuotaStatus]:
    return [await status(db, user, kind) for kind in QuotaKind]
