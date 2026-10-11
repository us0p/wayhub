"""The task queue (D28, D78): Procrastinate on Postgres, run by the compose `worker` service
(`python -m mentor worker`). The web app opens the same `app` only to queue tasks.

Tasks live next to their feature (`jobs/tasks.py`) and are registered through `import_paths`.
Its schema comes from an Alembic revision (0005), never from `procrastinate schema --apply`.
"""

import contextlib
import logging
from collections.abc import AsyncIterator

import procrastinate
from psycopg_pool import AsyncConnectionPool

from mentor.agents.checkpoint import psycopg_dsn
from mentor.settings import get_settings

log = logging.getLogger(__name__)

JOBS_QUEUE = "jobs"
WORKER_CONCURRENCY = 4

# No connection parameters here: `opened()` hands it a pool built from the settings, so
# importing this module (the web app does) never needs the database.
app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(),
    import_paths=["mentor.jobs.tasks", "mentor.fit.tasks"],
)


@contextlib.asynccontextmanager
async def opened(max_size: int = 4) -> AsyncIterator[procrastinate.App]:
    pool = AsyncConnectionPool(
        conninfo=psycopg_dsn(get_settings().database_dsn),
        max_size=max_size,
        open=False,
        check=AsyncConnectionPool.check_connection,
    )
    await pool.open(wait=True)
    try:
        async with app.open_async(pool=pool):
            yield app
    finally:
        await pool.close()


async def run() -> None:
    """The worker process: runs queued tasks until stopped (SIGINT/SIGTERM)."""
    from mentor.db import import_models

    import_models()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    async with opened(max_size=WORKER_CONCURRENCY + 2):
        log.info("worker started")
        await app.run_worker_async(queues=[JOBS_QUEUE], concurrency=WORKER_CONCURRENCY)
