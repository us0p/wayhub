from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from mentor.settings import get_settings


class Base(DeclarativeBase):
    pass


def import_models() -> None:
    """Import every model module so `Base.metadata` is complete (Alembic, tests)."""
    import mentor.auth.models
    import mentor.interview.models
    import mentor.profile.models
    import mentor.quotas.models  # noqa: F401


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_dsn, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
