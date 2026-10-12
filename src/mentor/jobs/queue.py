"""Queueing worker tasks from a request (D78): imports, fit analyses and CV generations. Routes
take the `Queue` dependency; tests replace it with one that records the ids and run the service
functions themselves."""

import contextlib
import functools
import uuid
from typing import Annotated, Protocol

from fastapi import Depends
from procrastinate.exceptions import AlreadyEnqueued


class JobQueue(Protocol):
    async def import_job(self, job_id: uuid.UUID) -> None: ...

    async def analyze_fit(self, job_id: uuid.UUID) -> None: ...

    async def generate_cv(self, cv_id: uuid.UUID) -> None: ...


# A retry while the first run still waits in the queue (say, the worker is down) adds nothing:
# that run will do it.
_already_queued = functools.partial(contextlib.suppress, AlreadyEnqueued)


class ProcrastinateQueue:
    async def import_job(self, job_id: uuid.UUID) -> None:
        from mentor.jobs.tasks import import_job

        # Keyed by the posting: queueing the same one twice runs it once.
        with _already_queued():
            await import_job.configure(queueing_lock=str(job_id)).defer_async(job_id=str(job_id))

    async def analyze_fit(self, job_id: uuid.UUID) -> None:
        from mentor.fit.tasks import analyze_fit

        lock = f"fit:{job_id}"
        with _already_queued():
            await analyze_fit.configure(queueing_lock=lock).defer_async(job_id=str(job_id))

    async def generate_cv(self, cv_id: uuid.UUID) -> None:
        from mentor.cv.tasks import generate_cv

        with _already_queued():
            await generate_cv.configure(queueing_lock=f"cv:{cv_id}").defer_async(cv_id=str(cv_id))


def get_job_queue() -> JobQueue:
    return ProcrastinateQueue()


Queue = Annotated[JobQueue, Depends(get_job_queue)]
