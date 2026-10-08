import argparse
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import Plan, User
from mentor.auth.sessions import now
from mentor.cli import apply, build_parser
from mentor.quotas import service as quotas
from mentor.quotas.models import QuotaKind, UsageEvent


@pytest.fixture
async def user(db: AsyncSession) -> User:
    user = User(google_sub="dev:q@example.com", email="q@example.com", name="Q")
    db.add(user)
    await db.flush()
    return user


async def test_free_plan_blocks_after_daily_limit(db: AsyncSession, user: User) -> None:
    await quotas.record(db, user, QuotaKind.JOB_IMPORT, 10)

    with pytest.raises(quotas.QuotaExceededError) as exc:
        await quotas.check(db, user, QuotaKind.JOB_IMPORT)

    assert exc.value.status.limit == 10
    assert exc.value.status.resets_at is not None


async def test_daily_usage_from_yesterday_does_not_count(db: AsyncSession, user: User) -> None:
    db.add(
        UsageEvent(
            user_id=user.id,
            kind=QuotaKind.CV_GENERATION,
            amount=10,
            created_at=now() - timedelta(days=1, hours=1),
        )
    )
    await db.flush()

    status = await quotas.check(db, user, QuotaKind.CV_GENERATION)

    assert status.used == 0


async def test_voice_is_a_lifetime_allowance(db: AsyncSession, user: User) -> None:
    db.add(
        UsageEvent(
            user_id=user.id,
            kind=QuotaKind.VOICE_SECONDS,
            amount=29 * 60 + 30,
            created_at=now() - timedelta(days=40),
        )
    )
    await db.flush()

    assert (await quotas.status(db, user, QuotaKind.VOICE_SECONDS)).remaining == 30
    with pytest.raises(quotas.QuotaExceededError):
        await quotas.check(db, user, QuotaKind.VOICE_SECONDS, amount=31)


async def test_tester_plan_is_unlimited(db: AsyncSession, user: User) -> None:
    user.plan = Plan.TESTER
    await quotas.record(db, user, QuotaKind.INTERVIEW_TURN, 10_000)

    status = await quotas.check(db, user, QuotaKind.INTERVIEW_TURN)

    assert status.limit is None and status.remaining is None


async def test_per_user_override_beats_plan(db: AsyncSession, user: User) -> None:
    user.quota_override = {"job_import": 1, "cv_generation": None}

    assert quotas.effective_limit(user, QuotaKind.JOB_IMPORT) == 1
    assert quotas.effective_limit(user, QuotaKind.CV_GENERATION) is None
    assert quotas.effective_limit(user, QuotaKind.INTERVIEW_TURN) == 150


def _args(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


async def test_cli_sets_plan_and_overrides(db: AsyncSession, user: User) -> None:
    await apply(db, _args("set-plan", "q@example.com", "tester"))
    await apply(db, _args("set-quota", "q@example.com", "job_import", "3"))
    lines = await apply(db, _args("set-quota", "q@example.com", "cv_generation", "unlimited"))

    assert user.plan is Plan.TESTER
    assert user.quota_override == {"job_import": 3, "cv_generation": None}
    assert any("job_import" in line and "limit=3" in line for line in lines)

    await apply(db, _args("set-quota", "q@example.com", "job_import", "default"))
    assert user.quota_override == {"cv_generation": None}
