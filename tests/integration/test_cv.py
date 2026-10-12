"""CV generation (M9, D10 to D12, D16, D81): generating a version, the Model A checks (sentences
without a source dropped, the verifier's rewrites and removals applied and shown), the pages
and the PDF/DOCX downloads."""

import io
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import docx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeAI
from mentor.ai.ports import AIProviderError
from mentor.auth.models import User
from mentor.cv import service
from mentor.cv.models import CV, CVStatus
from mentor.cv.schemas import CVContent, GeneratedCV, Verification
from mentor.interview.models import Interview, InterviewStatus
from mentor.jobs.models import (
    JobPosting,
    JobRequirement,
    JobSource,
    JobStatus,
    RequirementImportance,
    RequirementKind,
)
from mentor.jobs.queue import get_job_queue
from mentor.profile import skills
from mentor.profile.models import (
    Education,
    EducationStatus,
    Experience,
    ExperienceBullet,
    Language,
    Link,
    Profile,
    UserSkill,
)
from mentor.profile.service import stamps
from mentor.quotas.models import QuotaKind, UsageEvent

from .helpers import csrf_from, login_and_consent

TODAY = date(2026, 10, 11)

GENERATED: dict[str, Any] = {
    "headline": {"text": "Python Developer", "refs": ["P1"]},
    "summary": {
        "text": "Developer with 4 years of Python and Kubernetes.",
        "refs": ["E1", "S1"],
    },
    "experiences": [
        {
            "ref": "E1",
            "title": {"text": "Python Developer", "refs": ["E1"]},
            "bullets": [
                {"text": "Built REST APIs with Django", "refs": ["B1"]},
                {"text": "Led a team of 10 engineers", "refs": []},  # no source: dropped
                {"text": "Automated deployments, cutting release time by 80%", "refs": ["B2"]},
            ],
        },
        {"ref": "E9", "title": {"text": "Ghost job"}, "bullets": []},  # not in the profile
    ],
    "skill_refs": ["S1", "S9"],
    "education": [{"ref": "F1", "text": "Bachelor's in Computer Science"}],
    "languages": [{"ref": "L1", "text": "English (intermediate)"}],
}

# Claims in order: C1 headline, C2 summary, C3 title, C4 bullet 1, C5 bullet 3,
# C6 education, C7 language.
VERIFIED: dict[str, Any] = {
    "claims": [
        {"ref": "C1", "verdict": "supported"},
        {
            "ref": "C2",
            "verdict": "rewrite",
            "revised": "Developer with 4 years of Python.",
            "reason": "Seu perfil não menciona Kubernetes.",
        },
        {"ref": "C3", "verdict": "supported"},
        {"ref": "C4", "verdict": "supported"},
        {"ref": "C5", "verdict": "remove", "reason": "Seu perfil não fala em 80%."},
        {"ref": "C6", "verdict": "supported"},
        # C7 not answered: unchecked, so removed.
    ]
}


class RecordingQueue:
    def __init__(self) -> None:
        self.cvs: list[uuid.UUID] = []

    async def generate_cv(self, cv_id: uuid.UUID) -> None:
        self.cvs.append(cv_id)


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recorder = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: recorder
    return recorder


async def _user(client: AsyncClient, db: AsyncSession, plan: str = "tester") -> User:
    email = f"cv-{uuid.uuid4().hex[:8]}@example.com"
    await login_and_consent(client, email, plan)
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def _profile(db: AsyncSession, fake_ai: FakeAI, user: User, *, complete: bool = True) -> None:
    db.add(
        Profile(
            user_id=user.id,
            full_name="Ana Souza",
            email="ana@example.com",
            phone="11 99999-0000",
            city="São Paulo",
            state="SP",
            headline="Desenvolvedora Python",
        )
    )
    experience = Experience(
        user_id=user.id,
        company="Acme",
        title="Desenvolvedora Python",
        start_date=date(2022, 1, 1),
        is_current=True,
        is_technical=True,
    )
    db.add(experience)
    await db.flush()
    texts = ["Criei APIs REST com Django", "Automatizei os deploys"]
    for text, created in zip(texts, stamps(2), strict=True):
        db.add(
            ExperienceBullet(
                user_id=user.id, experience_id=experience.id, text=text, created_at=created
            )
        )
    db.add(
        Education(
            user_id=user.id,
            institution="USP",
            degree="Bacharelado",
            field="Ciência da Computação",
            start_year=2016,
            end_year=2020,
            status=EducationStatus.COMPLETED,
        )
    )
    db.add(Language(user_id=user.id, language="Inglês", level="intermediário"))
    db.add(Link(user_id=user.id, kind="github", url="https://github.com/ana"))
    skill = await skills.normalize(db, fake_ai.embeddings, "Python")
    db.add(
        UserSkill(
            user_id=user.id,
            skill_id=skill.id,
            raw_name="Python",
            years=4,
            experience_ids=[experience.id],
        )
    )
    if complete:
        db.add(Interview(user_id=user.id, status=InterviewStatus.COMPLETE))
    await db.commit()


async def _job(db: AsyncSession, user: User, language: str = "en") -> JobPosting:
    job = JobPosting(
        user_id=user.id,
        source=JobSource.TEXT,
        source_text="(texto da vaga)",
        status=JobStatus.READY,
        title="Senior Python Developer",
        company="Globex",
        language=language,
        is_tech=True,
        processed_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    db.add(
        JobRequirement(
            job_id=job.id,
            user_id=user.id,
            position=0,
            importance=RequirementImportance.MUST,
            kind=RequirementKind.SKILL,
            text="5 years of Python",
            min_years=5,
        )
    )
    await db.commit()
    return job


async def _generate(client: AsyncClient, job: JobPosting, language: str | None = None) -> Any:
    token = csrf_from((await client.get(f"/vagas/{job.id}")).text)
    return await client.post(
        f"/vagas/{job.id}/curriculos",
        data={"idioma": language} if language else {},
        headers={"X-CSRF-Token": token, "HX-Request": "true"},
    )


async def _cvs(db: AsyncSession, job: JobPosting) -> list[CV]:
    return list(
        await db.scalars(
            select(CV)
            .where(CV.job_id == job.id)
            .order_by(CV.version)
            .execution_options(populate_existing=True)
        )
    )


async def _ready_cv(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> tuple[User, JobPosting, CV]:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user)
    await _generate(client, job)
    fake_ai.chat.script_for(GeneratedCV, GENERATED)
    fake_ai.chat.script_for(Verification, VERIFIED)
    await service.generate(db, fake_ai, queue.cvs[-1], today=TODAY)
    [cv] = await _cvs(db, job)
    return user, job, cv


# -- requesting a version ----------------------------------------------------------------------


async def test_generating_queues_a_new_version_counts_it_and_opens_its_page(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, language="en")

    first = await _generate(client, job)
    second = await _generate(client, job, "pt-BR")

    one, two = await _cvs(db, job)
    assert (one.version, one.language, one.status) == (1, "en", CVStatus.PENDING)
    assert (two.version, two.language) == (2, "pt-BR")
    assert queue.cvs == [one.id, two.id]
    assert first.headers["HX-Redirect"] == f"/curriculos/{one.id}"
    used = await db.scalar(
        select(UsageEvent.amount)
        .where(UsageEvent.user_id == user.id, UsageEvent.kind == QuotaKind.CV_GENERATION)
        .limit(1)
    )
    assert used == 1 and second.status_code == 200


async def test_an_unknown_language_falls_back_to_the_postings(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, language="es-AR")

    await _generate(client, job, "klingon")

    [cv] = await _cvs(db, job)
    assert cv.language == "es"


async def test_cvs_are_locked_until_the_interview_is_complete(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user, complete=False)
    job = await _job(db, user)

    page = (await client.get(f"/vagas/{job.id}")).text
    response = await _generate(client, job)

    assert "termine a entrevista com a Mari" in page and "Gerar CV" not in page
    assert response.status_code == 403 and queue.cvs == []


async def test_the_free_plan_quota_applies(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user = await _user(client, db, plan="free")
    await _profile(db, fake_ai, user)
    job = await _job(db, user)
    db.add(UsageEvent(user_id=user.id, kind=QuotaKind.CV_GENERATION, amount=10))
    await db.commit()

    page = (await client.get(f"/vagas/{job.id}")).text
    response = await _generate(client, job)

    assert "Currículos restantes na sua conta: 0 de 10" in page
    assert response.status_code == 429 and queue.cvs == []


async def test_the_job_page_says_gerar_mesmo_assim_when_the_fit_has_gaps(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    from mentor.fit import service as fit
    from mentor.fit.schemas import FitJudgement

    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user)
    fake_ai.chat.script_for(
        FitJudgement,
        {"requirements": [{"ref": "R1", "verdict": "gap", "message": "Faltam anos."}]},
    )
    await fit.analyze(db, fake_ai, job.id, today=TODAY)

    page = (await client.get(f"/vagas/{job.id}")).text

    assert "Gerar CV mesmo assim" in page  # a warning, never a block (D13)


# -- generating (Model A) -------------------------------------------------------------------


async def test_the_cv_is_assembled_from_the_profile_and_checked_sentence_by_sentence(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)

    assert cv.status is CVStatus.READY and cv.finished_at is not None
    content = CVContent.model_validate(cv.content)
    # Copied from the profile, never written by the model.
    assert content.contact.name == "Ana Souza" and content.contact.email == "ana@example.com"
    assert content.contact.location == "São Paulo, SP"
    assert [link.url for link in content.contact.links] == ["https://github.com/ana"]
    [experience] = content.experiences  # E9 isn't in the profile
    assert (experience.company, experience.start, experience.current) == ("Acme", "2022-01", True)
    assert experience.title.text == "Python Developer"
    assert [s.text for s in content.skills] == ["Python"]  # S9 isn't either
    # Checked: rewritten, removed, and the unchecked language line removed too.
    assert content.headline is not None and content.headline.text == "Python Developer"
    assert content.summary is not None
    assert content.summary.text == "Developer with 4 years of Python."
    assert [b.text for b in experience.bullets] == ["Built REST APIs with Django"]
    assert [e.text for e in content.education] == ["Bachelor's in Computer Science"]
    assert content.education[0].detail == "USP · 2016–2020"  # noqa: RUF001
    assert content.languages == []
    changes = {c["original"]: c for c in cv.changes or []}
    assert changes["Led a team of 10 engineers"]["revised"] is None
    assert changes["Developer with 4 years of Python and Kubernetes."]["revised"] == (
        "Developer with 4 years of Python."
    )
    assert changes["Automated deployments, cutting release time by 80%"]["reason"] == (
        "Seu perfil não fala em 80%."
    )
    assert changes["English (intermediate)"]["reason"] == "Não consegui conferir essa frase."
    assert len(changes) == 4


async def test_every_line_keeps_the_ids_of_the_facts_behind_it(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user, _, cv = await _ready_cv(client, db, fake_ai, queue)
    content = CVContent.model_validate(cv.content)
    bullet = await db.scalar(
        select(ExperienceBullet).where(
            ExperienceBullet.user_id == user.id, ExperienceBullet.text.startswith("Criei")
        )
    )
    assert bullet is not None

    assert content.experiences[0].bullets[0].source_ids == [bullet.id]
    assert content.headline is not None and content.headline.source_ids == [user.id]  # P1


async def test_the_prompts_cite_facts_and_the_verifier_never_sees_the_posting(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    await _ready_cv(client, db, fake_ai, queue)

    [generate] = [c for c in fake_ai.chat.calls if c.schema is GeneratedCV]
    [verify] = [c for c in fake_ai.chat.calls if c.schema is Verification]
    prompt = generate.messages[-1].text
    assert prompt.startswith("Idioma do currículo: inglês.")
    assert "[E1] Experiência: Desenvolvedora Python em Acme (01/2022 a atual; técnica)" in prompt
    assert "    [B1] Sobre E1: Criei APIs REST com Django" in prompt
    assert "<vaga>\nCargo: Senior Python Developer" in prompt
    assert "11 99999-0000" not in prompt  # contact details are copied, never sent
    assert "Nunca invente" in generate.messages[0].text
    checked = verify.messages[-1].text
    assert "[C4] Built REST APIs with Django\nFontes:\n[B1] Sobre E1: Criei APIs" in checked
    assert "[E1] Experiência: Desenvolvedora Python" in checked  # the bullet's experience
    assert "Led a team" not in checked  # dropped before: it cited nothing
    assert "<vaga>" not in checked and "Globex" not in checked


async def test_a_provider_failure_fails_the_version_and_logs_no_content(
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    queue: RecordingQueue,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user)
    await _generate(client, job)
    cv_id = queue.cvs[0]
    fake_ai.chat.script_for(GeneratedCV, AIProviderError("Ana Souza, Acme"))

    with caplog.at_level(logging.WARNING, logger="mentor.cv.service"):
        await service.generate(db, fake_ai, cv_id, today=TODAY)

    cv = await db.get(CV, cv_id, populate_existing=True)
    assert cv is not None and cv.status is CVStatus.FAILED
    assert "AIProviderError" in caplog.text and "Ana Souza" not in caplog.text
    page = (await client.get(f"/curriculos/{cv_id}")).text
    assert "Não consegui gerar este currículo agora." in page


async def test_generating_twice_does_nothing_the_second_time(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)

    await service.generate(db, fake_ai, cv.id, today=TODAY)  # nothing scripted: would fail

    again = await db.get(CV, cv.id, populate_existing=True)
    assert again is not None and again.status is CVStatus.READY


# -- pages and downloads ----------------------------------------------------------------------


async def test_the_page_polls_then_shows_the_cv_and_what_changed(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user)
    await _generate(client, job)
    cv_id = queue.cvs[0]

    waiting = (await client.get(f"/curriculos/{cv_id}")).text
    fake_ai.chat.script_for(GeneratedCV, GENERATED)
    fake_ai.chat.script_for(Verification, VERIFIED)
    await service.generate(db, fake_ai, cv_id, today=TODAY)
    done = (await client.get(f"/curriculos/{cv_id}/estado")).text
    job_page = (await client.get(f"/vagas/{job.id}")).text

    assert f'hx-get="/curriculos/{cv_id}/estado" hx-trigger="every 2s"' in waiting
    assert "Escrevendo seu currículo" in waiting
    assert "every 2s" not in done
    assert "Built REST APIs with Django" in done and "Experience" in done  # English headings
    assert "O que a verificação mudou" in done and "Seu perfil não menciona Kubernetes." in done
    assert "Led a team of 10 engineers" in done and "Removida" in done
    assert f'href="/curriculos/{cv_id}/pdf"' in done and f'href="/curriculos/{cv_id}/docx"' in done
    assert f'href="/curriculos/{cv_id}"' in job_page and "Versão 1" in job_page
    assert "Vaga em inglês" in job_page


async def test_the_pdf_has_the_cv_as_real_text(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)

    response = await client.get(f"/curriculos/{cv.id}/pdf")

    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="CV - Ana Souza - Senior Python Developer.pdf"'
    )
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
    for expected in ("Ana Souza", "Python Developer", "EXPERIENCE", "Built REST APIs with Django"):
        assert expected in text
    assert "Led a team" not in text


async def test_the_docx_has_the_same_cv(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)

    response = await client.get(f"/curriculos/{cv.id}/docx")

    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    paragraphs = [p.text for p in docx.Document(io.BytesIO(response.content)).paragraphs]
    assert paragraphs[0] == "Ana Souza"
    assert "Built REST APIs with Django" in paragraphs and "EXPERIENCE" in paragraphs
    assert "Python" in paragraphs and "Developer with 4 years of Python." in paragraphs


async def test_a_cv_not_ready_has_no_downloads(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user)
    await _generate(client, job)

    assert (await client.get(f"/curriculos/{queue.cvs[0]}/pdf")).status_code == 404
    assert (await client.get(f"/curriculos/{queue.cvs[0]}/txt")).status_code == 404


async def test_another_users_cv_is_not_found(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, job, cv = await _ready_cv(client, db, fake_ai, queue)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login_and_consent(other, f"outro-{uuid.uuid4().hex[:8]}@example.com", "tester")
        token = csrf_from((await other.get("/vagas")).text)

        page = await other.get(f"/curriculos/{cv.id}")
        download = await other.get(f"/curriculos/{cv.id}/pdf")
        generate = await other.post(f"/vagas/{job.id}/curriculos", headers={"X-CSRF-Token": token})

    assert (page.status_code, download.status_code, generate.status_code) == (404, 404, 404)


async def test_deleting_the_job_deletes_its_cvs(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, job, cv = await _ready_cv(client, db, fake_ai, queue)
    token = csrf_from((await client.get(f"/vagas/{job.id}")).text)

    await client.delete(f"/vagas/{job.id}", headers={"X-CSRF-Token": token})

    assert await db.scalar(select(CV.id).where(CV.id == cv.id)) is None


async def test_a_lost_version_offers_a_free_retry(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    """M10: a version the worker lost stops polling and can be queued again, uncounted."""
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user)
    await _generate(client, job)
    [cv] = await _cvs(db, job)
    cv.requested_at = datetime.now(UTC) - timedelta(minutes=6)
    await db.commit()
    token = csrf_from((await client.get(f"/vagas/{job.id}")).text)

    page = (await client.get(f"/curriculos/{cv.id}")).text
    job_page = (await client.get(f"/vagas/{job.id}")).text
    retry = await client.post(
        f"/curriculos/{cv.id}/tentar-novamente",
        headers={"X-CSRF-Token": token, "HX-Request": "true"},
    )

    assert "Este currículo parou no meio do caminho." in page and "every 2s" not in page
    assert "Falhou" in job_page
    assert queue.cvs == [cv.id, cv.id]
    assert "Escrevendo seu currículo" in retry.text
    used = await db.scalar(
        select(func.sum(UsageEvent.amount)).where(
            UsageEvent.user_id == user.id, UsageEvent.kind == QuotaKind.CV_GENERATION
        )
    )
    assert used == 1
