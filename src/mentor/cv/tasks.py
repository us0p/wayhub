"""Worker task for CV generation (M9, D81); the work itself is `service.generate`."""

import uuid

from mentor.ai.registry import get_ai
from mentor.cv import service
from mentor.db import get_sessionmaker
from mentor.worker import JOBS_QUEUE, app


@app.task(name="generate_cv", queue=JOBS_QUEUE, pass_context=False)
async def generate_cv(cv_id: str) -> None:
    async with get_sessionmaker()() as db:
        await service.generate(db, get_ai(), uuid.UUID(cv_id))
