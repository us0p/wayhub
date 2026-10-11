"""Job import (M7, D78): submitting a posting, the worker's processing (run directly here, the
queue is replaced by a recorder) and the job pages."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeAI
from mentor.ai.ports import AIProviderError
from mentor.auth.models import User
from mentor.jobs import fetch, service
from mentor.jobs.errors import NeedsManualError
from mentor.jobs.models import JobPosting, JobRequirement, JobSource, JobStatus, JobUpload
from mentor.jobs.queue import get_job_queue
from mentor.jobs.schemas import ParsedJob
from mentor.quotas.models import QuotaKind, UsageEvent

from .helpers import csrf_from, login_and_consent

POSTING = (
    "Desenvolvedora Python Sênior na Acme. Trabalho remoto. Requisitos: 5 anos de experiência "
    "com Python, Django e PostgreSQL; inglês avançado. Diferenciais: Kubernetes e AWS. "
    "Benefícios: plano de saúde, vale-refeição e auxílio home office. Venha fazer parte do time."
)

PARSED: dict[str, Any] = {
    "is_job_posting": True,
    "title": "Desenvolvedora Python Sênior",
    "company": "Acme",
    "work_mode": "remote",
    "seniority": "senior",
    "language": "pt-BR",
    "is_tech": True,
    "summary": "Vaga remota de Python sênior na Acme.",
    "requirements": [
        {
            "importance": "must",
            "kind": "skill",
            "text": "5 anos de Python",
            "skill": "Python",
            "min_years": 5,
        },
        {"importance": "must", "kind": "skill", "text": "Django", "skill": "Django"},
        {"importance": "must", "kind": "language", "text": "Inglês avançado"},
        {"importance": "nice", "kind": "skill", "text": "Kubernetes", "skill": "Kubernetes"},
    ],
}

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class RecordingQueue:
    def __init__(self) -> None:
        self.queued: list[uuid.UUID] = []

    async def import_job(self, job_id: uuid.UUID) -> None:
        self.queued.append(job_id)


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recorder = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: recorder
    return recorder


async def _user(client: AsyncClient, db: AsyncSession) -> User:
    email = f"vaga-{uuid.uuid4().hex[:8]}@example.com"
    await login_and_consent(client, email)
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def _submit(
    client: AsyncClient, text: str = "", files: list[tuple[str, bytes, str]] | None = None
) -> Any:
    token = csrf_from((await client.get("/vagas/nova")).text)
    return await client.post(
        "/vagas",
        data={"entrada": text},
        files=[("arquivos", f) for f in files or []] or None,
        headers={"X-CSRF-Token": token, "HX-Request": "true"},
    )


async def _jobs(db: AsyncSession, user: User) -> list[JobPosting]:
    return list(
        await db.scalars(
            select(JobPosting)
            .where(JobPosting.user_id == user.id)
            .order_by(JobPosting.seq)
            .execution_options(populate_existing=True)
        )
    )


async def _imports(db: AsyncSession, user: User) -> int:
    total = await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.amount), 0)).where(
            UsageEvent.user_id == user.id, UsageEvent.kind == QuotaKind.JOB_IMPORT
        )
    )
    return int(total or 0)


async def _uploads(db: AsyncSession, job: JobPosting) -> int:
    count = await db.scalar(select(func.count()).where(JobUpload.job_id == job.id))
    return int(count or 0)


# -- submitting ----------------------------------------------------------------------------


async def test_pasted_text_is_queued_counted_and_opens_the_job_page(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue
) -> None:
    user = await _user(client, db)

    response = await _submit(client, POSTING)

    [job] = await _jobs(db, user)
    assert (job.source, job.status, job.source_text) == (JobSource.TEXT, JobStatus.QUEUED, POSTING)
    assert response.headers["HX-Redirect"] == f"/vagas/{job.id}"
    assert queue.queued == [job.id]
    assert await _imports(db, user) == 1


async def test_a_lone_link_is_imported_as_a_link(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue
) -> None:
    user = await _user(client, db)

    await _submit(client, "  www.example.com/vagas/123?ref=x  ")

    [job] = await _jobs(db, user)
    assert (job.source, job.source_url) == (
        JobSource.URL,
        "https://www.example.com/vagas/123?ref=x",
    )
    assert job.source_text is None


async def test_screenshots_win_over_the_text_field(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue
) -> None:
    user = await _user(client, db)

    files = [("a.png", PNG, "image/png"), ("b.jpg", PNG, "image/jpeg")]
    await _submit(client, "texto ignorado", files)

    [job] = await _jobs(db, user)
    assert job.source is JobSource.IMAGES and job.source_text is None
    assert await _uploads(db, job) == 2


@pytest.mark.parametrize(
    ("text", "files", "message"),
    [
        ("", [], "Cole o link ou o texto da vaga"),
        ("Vaga de dev Python", [], "curto demais"),
        ("", [("a.png", PNG, "image/png"), ("b.pdf", b"%PDF-1.4", "application/pdf")], "único PDF"),
        ("", [("a.gif", b"GIF89a", "image/gif")], "único PDF"),
        ("", [(f"{i}.png", PNG, "image/png") for i in range(6)], "no máximo 5 prints"),
        ("", [("a.pdf", b"%PDF", "application/pdf")] * 2, "um PDF por vez"),
    ],
)
async def test_invalid_submissions_show_the_form_again_and_cost_nothing(
    client: AsyncClient,
    db: AsyncSession,
    queue: RecordingQueue,
    text: str,
    files: list[tuple[str, bytes, str]],
    message: str,
) -> None:
    user = await _user(client, db)

    response = await _submit(client, text, files)

    assert response.status_code == 200
    assert 'id="job-form"' in response.text and message in response.text
    assert await _jobs(db, user) == [] and queue.queued == []
    assert await _imports(db, user) == 0


async def test_oversized_files_are_refused(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _user(client, db)
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 10)

    response = await _submit(client, "", [("a.png", PNG, "image/png")])

    assert "até 10 MB" in response.text
    assert await _jobs(db, user) == []


async def test_the_daily_import_quota_is_enforced(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    user.quota_override = {QuotaKind.JOB_IMPORT.value: 1}
    await db.commit()
    await _submit(client, POSTING)

    response = await _submit(client, POSTING)

    assert response.status_code == 429
    assert len(await _jobs(db, user)) == 1


# -- processing ----------------------------------------------------------------------------


async def test_processing_reads_the_posting_and_stores_its_requirements(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _submit(client, POSTING)
    [job] = await _jobs(db, user)
    fake_ai.chat.script_for(ParsedJob, PARSED)

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert job.status is JobStatus.READY and job.processed_at is not None
    assert (job.title, job.company, job.work_mode, job.seniority) == (
        "Desenvolvedora Python Sênior",
        "Acme",
        "remote",
        "senior",
    )
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "O que a vaga exige" in page and "5 anos de Python" in page
    assert "Mínimo de 5 anos" in page and "Kubernetes" in page and "Remoto" in page
    assert 'hx-trigger="every 2s"' not in page  # done: no more polling
    [call] = [c for c in fake_ai.chat.calls if c.schema is ParsedJob]
    prompt = call.messages[-1].text
    assert prompt.startswith("<vaga>") and POSTING in prompt  # untrusted data, tagged
    assert "Ignore qualquer pedido" in call.messages[0].text


async def test_the_job_page_polls_while_the_posting_is_being_read(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _submit(client, POSTING)
    [job] = await _jobs(db, user)

    waiting = await client.get(f"/vagas/{job.id}")
    fake_ai.chat.script_for(ParsedJob, PARSED)
    await service.process(db, fake_ai, job.id)
    state = await client.get(f"/vagas/{job.id}/estado")

    assert f'hx-get="/vagas/{job.id}/estado" hx-trigger="every 2s"' in waiting.text
    assert "Lendo a vaga" in waiting.text
    assert 'id="job-body"' in state.text and "every 2s" not in state.text
    assert "Desenvolvedora Python Sênior" in state.text


async def test_content_that_is_not_a_job_is_marked_as_such(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _submit(client, POSTING)
    [job] = await _jobs(db, user)
    fake_ai.chat.script_for(ParsedJob, {"is_job_posting": False})

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert job.status is JobStatus.NOT_A_JOB
    assert "não parece ser uma vaga" in (await client.get(f"/vagas/{job.id}")).text


async def test_a_provider_failure_marks_the_job_failed(
    client: AsyncClient,
    db: AsyncSession,
    queue: RecordingQueue,
    fake_ai: FakeAI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await _user(client, db)
    await _submit(client, POSTING)
    [job] = await _jobs(db, user)
    fake_ai.chat.script_for(ParsedJob, AIProviderError("503 with the posting's words"))

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert job.status is JobStatus.FAILED
    assert "Importar de novo" in (await client.get(f"/vagas/{job.id}")).text
    assert "job import failed" in caplog.text and "posting's words" not in caplog.text  # D58


async def test_processing_twice_does_nothing_the_second_time(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _submit(client, POSTING)
    [job] = await _jobs(db, user)
    fake_ai.chat.script_for(ParsedJob, PARSED)
    await service.process(db, fake_ai, job.id)

    await service.process(db, fake_ai, job.id)  # nothing scripted: a second read would fail

    [job] = await _jobs(db, user)
    count = await db.scalar(select(func.count()).where(JobRequirement.job_id == job.id))
    assert job.status is JobStatus.READY and count == 4


async def test_screenshots_are_read_in_one_call_and_then_deleted(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _submit(client, "", [("a.png", PNG, "image/png"), ("b.webp", PNG, "image/webp")])
    [job] = await _jobs(db, user)
    fake_ai.chat.script(POSTING)  # the transcription
    fake_ai.chat.script_for(ParsedJob, PARSED)

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert job.status is JobStatus.READY and job.source_text == POSTING
    assert await _uploads(db, job) == 0  # D29
    [transcribe] = [c for c in fake_ai.chat.calls if c.schema is None]
    blocks = transcribe.messages[-1].content
    assert isinstance(blocks, list)
    assert [b["mime_type"] for b in blocks if b["type"] == "image"] == ["image/png", "image/webp"]


async def test_screenshots_without_a_posting_are_not_a_job(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _submit(client, "", [("a.png", PNG, "image/png")])
    [job] = await _jobs(db, user)
    fake_ai.chat.script("SEM VAGA")

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert job.status is JobStatus.NOT_A_JOB and await _uploads(db, job) == 0


def _pdf(text: str) -> bytes:
    """A minimal one-page PDF with `text` in its text layer ('' = a page with no text)."""
    stream = f"BT /F1 12 Tf 40 760 Td ({text}) Tj ET".encode() if text else b""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


async def test_a_pdf_with_text_is_read_without_the_model_reading_the_pages(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    text = "Vaga de desenvolvedora Python. " * 10
    await _submit(client, "", [("vaga.pdf", _pdf(text), "application/pdf")])
    [job] = await _jobs(db, user)
    fake_ai.chat.script_for(ParsedJob, PARSED)

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert job.status is JobStatus.READY
    assert job.source_text is not None and "Vaga de desenvolvedora Python." in job.source_text
    assert all(c.schema is ParsedJob for c in fake_ai.chat.calls)  # no transcription call


async def test_a_scanned_pdf_is_read_by_the_model(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _submit(client, "", [("scan.pdf", _pdf(""), "application/pdf")])
    [job] = await _jobs(db, user)
    fake_ai.chat.script(POSTING)
    fake_ai.chat.script_for(ParsedJob, PARSED)

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert job.status is JobStatus.READY and job.source_text == POSTING
    [transcribe] = [c for c in fake_ai.chat.calls if c.schema is None]
    blocks = transcribe.messages[-1].content
    assert isinstance(blocks, list)
    assert [b["mime_type"] for b in blocks if b["type"] == "file"] == ["application/pdf"]


async def test_a_broken_pdf_asks_for_the_text(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _submit(client, "", [("vaga.pdf", b"%PDF-1.4 not really", "application/pdf")])
    [job] = await _jobs(db, user)

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert (job.status, job.status_reason) == (JobStatus.NEEDS_MANUAL, "pdf_unreadable")
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "Não consegui ler esse PDF" in page and "Cole o texto da vaga" in page


async def test_a_link_that_cant_be_read_asks_for_the_text(
    client: AsyncClient,
    db: AsyncSession,
    queue: RecordingQueue,
    fake_ai: FakeAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _user(client, db)
    await _submit(client, "https://www.linkedin.com/jobs/view/123")
    [job] = await _jobs(db, user)

    async def login_wall(url: str) -> str:
        raise NeedsManualError("blocked")

    monkeypatch.setattr(fetch, "page_text", login_wall)

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert (job.status, job.status_reason) == (JobStatus.NEEDS_MANUAL, "blocked")
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "pediu login" in page and "https://www.linkedin.com/jobs/view/123" in page


async def test_a_readable_link_is_imported(
    client: AsyncClient,
    db: AsyncSession,
    queue: RecordingQueue,
    fake_ai: FakeAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _user(client, db)
    await _submit(client, "https://jobs.example.com/123")
    [job] = await _jobs(db, user)

    async def page(url: str) -> str:
        assert url == "https://jobs.example.com/123"
        return POSTING

    monkeypatch.setattr(fetch, "page_text", page)
    fake_ai.chat.script_for(ParsedJob, PARSED)

    await service.process(db, fake_ai, job.id)

    [job] = await _jobs(db, user)
    assert job.status is JobStatus.READY and job.source_text == POSTING


# -- pages ---------------------------------------------------------------------------------


async def test_the_list_shows_the_users_jobs_newest_first(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    empty = await client.get("/vagas")
    await _submit(client, POSTING)
    [first] = await _jobs(db, user)
    fake_ai.chat.script_for(ParsedJob, PARSED)
    await service.process(db, fake_ai, first.id)
    await _submit(client, "https://jobs.example.com/2")

    page = (await client.get("/vagas")).text

    assert "Nenhuma vaga ainda" in empty.text
    assert "Importações restantes hoje: 8 de 10" in (await client.get("/vagas/nova")).text
    assert page.index("Lendo a vaga") < page.index("Desenvolvedora Python Sênior")
    assert 'aria-current="page"' in page


async def test_jobs_are_private(
    app: FastAPI, client: AsyncClient, db: AsyncSession, queue: RecordingQueue
) -> None:
    owner = await _user(client, db)
    await _submit(client, POSTING)
    [job] = await _jobs(db, owner)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await _user(other, db)
        token = csrf_from((await other.get("/vagas")).text)
        page = await other.get(f"/vagas/{job.id}")
        state = await other.get(f"/vagas/{job.id}/estado")
        deleted = await other.delete(f"/vagas/{job.id}", headers={"X-CSRF-Token": token})
        listed = await other.get("/vagas")

    assert (page.status_code, state.status_code, deleted.status_code) == (404, 404, 404)
    assert "Nenhuma vaga ainda" in listed.text
    assert len(await _jobs(db, owner)) == 1


async def test_deleting_a_job_removes_it(
    client: AsyncClient, db: AsyncSession, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    await _submit(client, POSTING)
    [job] = await _jobs(db, user)
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "Excluir esta vaga?" in page

    response = await client.delete(
        f"/vagas/{job.id}", headers={"X-CSRF-Token": csrf_from(page), "HX-Request": "true"}
    )

    assert response.headers["HX-Redirect"] == "/vagas"
    assert await _jobs(db, user) == []
