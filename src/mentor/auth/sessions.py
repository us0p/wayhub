"""Server-side login sessions (D41).

The cookie carries a random opaque token; only its SHA-256 is stored, so a database leak does
not yield usable sessions. Expiry slides on use (written at most once per `SLIDE_EVERY`).
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import User, UserSession
from mentor.settings import get_settings

SESSION_TTL = timedelta(days=30)
SLIDE_EVERY = timedelta(hours=1)


def cookie_name() -> str:
    # `__Host-` prefix pins the cookie to this exact host over HTTPS (no Domain, Path=/).
    return "mentor_session" if get_settings().is_dev_like else "__Host-mentor_session"


def cookie_secure() -> bool:
    return not get_settings().is_dev_like


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def now() -> datetime:
    return datetime.now(UTC)


async def create_session(db: AsyncSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    current = now()
    db.add(
        UserSession(
            token_hash=_hash(token),
            user_id=user.id,
            last_seen_at=current,
            expires_at=current + SESSION_TTL,
        )
    )
    await db.flush()
    return token


async def resolve_session(db: AsyncSession, token: str) -> UserSession | None:
    session = await db.get(UserSession, _hash(token))
    if session is None:
        return None
    current = now()
    if session.expires_at <= current:
        await db.delete(session)
        await db.commit()
        return None
    if current - session.last_seen_at >= SLIDE_EVERY:
        session.last_seen_at = current
        session.expires_at = current + SESSION_TTL
        await db.commit()
    return session


async def revoke_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(UserSession).where(UserSession.token_hash == _hash(token)))


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(delete(UserSession).where(UserSession.user_id == user_id))


def csrf_token_for(session_token: str) -> str:
    """CSRF token bound to the session (HMAC, so it is never stored)."""
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, b"csrf:" + session_token.encode(), hashlib.sha256).hexdigest()


def csrf_token_valid(session_token: str, candidate: str | None) -> bool:
    return candidate is not None and hmac.compare_digest(csrf_token_for(session_token), candidate)
