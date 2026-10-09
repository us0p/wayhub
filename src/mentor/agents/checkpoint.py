"""LangGraph checkpointer (D56): agent state in Postgres, one thread per interview.

The saver is created at app startup (its `setup()` creates/migrates its own tables, which
Alembic ignores) and closed on shutdown. Checkpoints don't cascade from `users`, so account
deletion must call `delete_threads()` (LGPD, D21). Tests override `get_checkpointer` with an
in-memory saver.
"""

import asyncio
from collections.abc import Iterable
from typing import Annotated

from fastapi import Depends
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from mentor.settings import get_settings

# Tables owned by the saver; excluded from Alembic autogenerate (migrations/env.py).
CHECKPOINT_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)

_lock = asyncio.Lock()
_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


def psycopg_dsn(sqlalchemy_url: str) -> str:
    """`postgresql+asyncpg://...` → `postgresql://...` (psycopg connection string)."""
    scheme, sep, rest = sqlalchemy_url.partition("://")
    return f"{scheme.split('+')[0]}{sep}{rest}"


async def get_checkpointer() -> BaseCheckpointSaver[str]:
    global _pool, _saver
    async with _lock:
        if _saver is None:
            pool = AsyncConnectionPool(
                conninfo=psycopg_dsn(get_settings().database_dsn),
                max_size=10,
                open=False,
                # Required by the saver: autocommit, no server-side prepared statements.
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            )
            await pool.open()
            saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
            await saver.setup()
            _pool, _saver = pool, saver
    return _saver


async def close_checkpointer() -> None:
    global _pool, _saver
    async with _lock:
        if _pool is not None:
            await _pool.close()
        _pool, _saver = None, None


async def delete_threads(checkpointer: BaseCheckpointSaver[str], thread_ids: Iterable[str]) -> None:
    for thread_id in thread_ids:
        await checkpointer.adelete_thread(thread_id)


Checkpointer = Annotated[BaseCheckpointSaver[str], Depends(get_checkpointer)]
