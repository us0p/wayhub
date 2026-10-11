"""The job import task on Procrastinate (D78), with its in-memory connector: queueing from a
request runs `service.process` in the worker, once per posting."""

import uuid
from typing import Any

import pytest
from procrastinate.testing import InMemoryConnector

from mentor import worker
from mentor.jobs import service, tasks
from mentor.jobs.queue import ProcrastinateQueue


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_a_queued_import_runs_in_the_worker_once(monkeypatch: pytest.MonkeyPatch) -> None:
    processed: list[uuid.UUID] = []

    async def process(db: Any, ai: Any, job_id: uuid.UUID) -> None:
        processed.append(job_id)

    monkeypatch.setattr(service, "process", process)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: FakeSession)
    monkeypatch.setattr(tasks, "get_ai", lambda: None)
    job_id = uuid.uuid4()

    with worker.app.replace_connector(InMemoryConnector()) as app:
        await ProcrastinateQueue().import_job(job_id)
        with pytest.raises(Exception, match="queueing lock"):  # the same posting twice
            await ProcrastinateQueue().import_job(job_id)
        [job] = app.connector.jobs.values()  # type: ignore[attr-defined]
        assert (job["queue_name"], job["task_name"]) == (worker.JOBS_QUEUE, "import_job")

        await app.run_worker_async(queues=[worker.JOBS_QUEUE], wait=False)

    assert processed == [job_id]
