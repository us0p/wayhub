"""Worker tasks for job import (M7, D78). Each opens its own DB session; the work itself is
`service.process`, tested without the queue. Once the posting is read, the same task compares
it with the profile (the fit warning, D80)."""

import uuid

from mentor.ai.registry import get_ai
from mentor.db import get_sessionmaker
from mentor.fit import service as fit
from mentor.jobs import service
from mentor.worker import JOBS_QUEUE, app


@app.task(name="import_job", queue=JOBS_QUEUE, pass_context=False)
async def import_job(job_id: str) -> None:
    async with get_sessionmaker()() as db:
        ai = get_ai()
        await service.process(db, ai, uuid.UUID(job_id))
        await fit.analyze(db, ai, uuid.UUID(job_id))  # only once the posting is ready
