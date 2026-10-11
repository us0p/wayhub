"""Worker tasks for job import (M7, D78). Each opens its own DB session; the work itself is
`service.process`, tested without the queue."""

import uuid

from mentor.ai.registry import get_ai
from mentor.db import get_sessionmaker
from mentor.jobs import service
from mentor.worker import JOBS_QUEUE, app


@app.task(name="import_job", queue=JOBS_QUEUE, pass_context=False)
async def import_job(job_id: str) -> None:
    async with get_sessionmaker()() as db:
        await service.process(db, get_ai(), uuid.UUID(job_id))
