"""Worker task for the fit warning when the user asks for it again (M8, D80). The first
analysis runs inside `jobs.tasks.import_job`, right after the posting is read."""

import uuid

from mentor.ai.registry import get_ai
from mentor.db import get_sessionmaker
from mentor.fit import service
from mentor.worker import JOBS_QUEUE, app


@app.task(name="analyze_fit", queue=JOBS_QUEUE, pass_context=False)
async def analyze_fit(job_id: str) -> None:
    async with get_sessionmaker()() as db:
        await service.analyze(db, get_ai(), uuid.UUID(job_id))
