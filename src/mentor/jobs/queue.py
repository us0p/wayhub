"""Queueing worker tasks from a request (D78): imports, fit analyses and CV generations. Routes
take the `Queue` dependency; tests replace it with one that records the ids and run the service
functions themselves."""

import uuid
from typing import Annotated, Protocol

from fastapi import Depends


class JobQueue(Protocol):
    async def import_job(self, job_id: uuid.UUID) -> None: ...

    async def analyze_fit(self, job_id: uuid.UUID) -> None: ...

    async def generate_cv(self, cv_id: uuid.UUID) -> None: ...


class ProcrastinateQueue:
    async def import_job(self, job_id: uuid.UUID) -> None:
        from mentor.jobs.tasks import import_job

        # Keyed by the posting: queueing the same one twice runs it once.
        await import_job.configure(queueing_lock=str(job_id)).defer_async(job_id=str(job_id))

    async def analyze_fit(self, job_id: uuid.UUID) -> None:
        from mentor.fit.tasks import analyze_fit

        lock = f"fit:{job_id}"
        await analyze_fit.configure(queueing_lock=lock).defer_async(job_id=str(job_id))

    async def generate_cv(self, cv_id: uuid.UUID) -> None:
        from mentor.cv.tasks import generate_cv

        await generate_cv.configure(queueing_lock=f"cv:{cv_id}").defer_async(cv_id=str(cv_id))


def get_job_queue() -> JobQueue:
    return ProcrastinateQueue()


Queue = Annotated[JobQueue, Depends(get_job_queue)]
