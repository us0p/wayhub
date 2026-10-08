from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import Plan, User


async def upsert_user(
    db: AsyncSession,
    *,
    subject: str,
    email: str,
    name: str,
    avatar_url: str | None = None,
    plan: Plan | None = None,
) -> User:
    """Create or refresh a user from identity-provider claims. `plan` is only applied when
    given (dev-login); real logins never change the plan, which is managed via the CLI."""
    user = await db.scalar(select(User).where(User.google_sub == subject))
    if user is None:
        user = User(google_sub=subject, email=email, name=name, avatar_url=avatar_url)
        if plan is not None:
            user.plan = plan
        db.add(user)
    else:
        user.email, user.name, user.avatar_url = email, name, avatar_url
        if plan is not None:
            user.plan = plan
    await db.flush()
    return user
