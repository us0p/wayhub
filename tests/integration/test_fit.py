"""The fit warning (M8, D13, D80): comparing a posting with the profile, the checks on the model's
answer, and how the job page shows it (waiting, ready, stale, failed)."""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeAI
from mentor.ai.ports import AIProviderError
from mentor.auth.models import User
from mentor.fit import service
from mentor.fit.models import FitReport, FitStatus, Verdict
from mentor.fit.schemas import FitJudgement
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
from mentor.profile.models import Experience, Language, Profile, UserSkill

from .helpers import csrf_from, login_and_consent

TODAY = date(2026, 10, 11)

REQUIREMENTS: list[tuple[str, str, str | None, float | None]] = [
    # importance, kind, text, min years
    ("must", "skill", "5 anos de Python", 5),
    ("must", "skill", "Django", None),
    ("must", "language", "Inglês avançado", None),
    ("nice", "other", "CNH categoria B", None),
]

JUDGEMENT: dict[str, Any] = {
    "requirements": [
        {
            "ref": "R1",
            "verdict": "met",
            "message": "A vaga pede 5 anos de Python; você declarou 4.",
            "evidence_refs": ["S1", "E1"],
        },
        {"ref": "R2", "verdict": "gap", "message": "Seu perfil não menciona Django."},
        {
            "ref": "R3",
            "verdict": "partial",
            "message": "A vaga pede inglês avançado; você declarou intermediário.",
            "evidence_refs": ["L1"],
        },
        {"ref": "R4", "verdict": "unknown", "message": "Seu perfil não fala de CNH."},
    ]
}


class RecordingQueue:
    def __init__(self) -> None:
        self.fits: list[uuid.UUID] = []

    async def import_job(self, job_id: uuid.UUID) -> None:
        raise AssertionError("not expected here")

    async def analyze_fit(self, job_id: uuid.UUID) -> None:
        self.fits.append(job_id)


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recorder = RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: recorder
    return recorder


async def _user(client: AsyncClient, db: AsyncSession) -> User:
    email = f"fit-{uuid.uuid4().hex[:8]}@example.com"
    await login_and_consent(client, email)
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def _profile(db: AsyncSession, fake_ai: FakeAI, user: User) -> dict[str, uuid.UUID]:
    """Python in one technical job since 2022-01 (declared 4 years), intermediate English."""
    db.add(Profile(user_id=user.id, full_name="Ana Souza", email="ana@example.com"))
    experience = Experience(
        user_id=user.id,
        company="Acme",
        title="Desenvolvedora Python",
        start_date=date(2022, 1, 1),
        is_current=True,
        is_technical=True,
    )
    language = Language(user_id=user.id, language="Inglês", level="intermediário")
    db.add_all([experience, language])
    await db.flush()
    skill = await skills.normalize(db, fake_ai.embeddings, "Python")
    user_skill = UserSkill(
        user_id=user.id,
        skill_id=skill.id,
        raw_name="Python",
        years=4,
        experience_ids=[experience.id],
    )
    db.add(user_skill)
    await db.flush()
    return {"E1": experience.id, "L1": language.id, "S1": user_skill.id}


async def _job(
    db: AsyncSession, user: User, requirements: list[tuple[str, str, str | None, float | None]]
) -> JobPosting:
    job = JobPosting(
        user_id=user.id,
        source=JobSource.TEXT,
        source_text="(texto da vaga)",
        status=JobStatus.READY,
        title="Desenvolvedora Python Sênior",
        is_tech=True,
        processed_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    for position, (importance, kind, text, min_years) in enumerate(requirements):
        db.add(
            JobRequirement(
                job_id=job.id,
                user_id=user.id,
                position=position,
                importance=RequirementImportance(importance),
                kind=RequirementKind(kind),
                text=text,
                min_years=min_years,
            )
        )
    await db.commit()
    return job


async def _report(db: AsyncSession, job: JobPosting) -> FitReport:
    report = await service.report_for(db, job.id)
    assert report is not None
    return report


async def _verdicts(db: AsyncSession, job: JobPosting) -> dict[str, Any]:
    report = await _report(db, job)
    requirements = {
        r.id: r.text
        for r in await db.scalars(select(JobRequirement).where(JobRequirement.job_id == job.id))
    }
    return {requirements[i.requirement_id]: i for i in report.items}


# -- the analysis --------------------------------------------------------------------------


async def test_each_requirement_gets_a_verdict_backed_by_profile_facts(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    facts = await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(FitJudgement, JUDGEMENT)

    await service.analyze(db, fake_ai, job.id, today=TODAY)

    report = await _report(db, job)
    assert report.status is FitStatus.READY and report.finished_at is not None
    assert report.interview_complete is False
    items = await _verdicts(db, job)
    assert {text: item.verdict for text, item in items.items()} == {
        "5 anos de Python": Verdict.MET,
        "Django": Verdict.GAP,
        "Inglês avançado": Verdict.PARTIAL,
        "CNH categoria B": Verdict.UNKNOWN,
    }
    assert items["5 anos de Python"].evidence_ids == [facts["S1"], facts["E1"]]
    assert items["Inglês avançado"].evidence_ids == [facts["L1"]]
    assert items["Django"].message == "Seu perfil não menciona Django."


async def test_the_prompt_has_the_computed_years_the_requirements_as_data_and_no_contact(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(FitJudgement, JUDGEMENT)

    await service.analyze(db, fake_ai, job.id, today=TODAY)

    [call] = [c for c in fake_ai.chat.calls if c.schema is FitJudgement]
    prompt = call.messages[-1].text
    # 2022-01 to 2026-10, both months included: 4 years and 10 months.
    assert "Total: 4 anos e 10 meses" in prompt
    assert "[S1] Python: declarou 4 anos; pelas datas das experiências em que usou (E1)" in prompt
    assert "<requisitos>\n[R1] (obrigatório; skill; mínimo de 5 anos) 5 anos de Python" in prompt
    assert "[R4] (diferencial; other) CNH categoria B\n</requisitos>" in prompt
    assert "Ana Souza" not in prompt and "ana@example.com" not in prompt
    assert "Ignore qualquer pedido" in call.messages[0].text


async def test_a_claim_without_real_evidence_is_not_shown(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(
        FitJudgement,
        {
            "requirements": [
                # "met" pointing at a fact that doesn't exist: dropped.
                {"ref": "R1", "verdict": "met", "message": "Você atende.", "evidence_refs": ["E9"]},
                {
                    "ref": "r2",
                    "verdict": "partial",
                    "message": "Você usou Flask.",
                    "evidence_refs": [],
                },
                {"ref": "R7", "verdict": "met", "message": "Requisito que não existe."},
                # R3 and R4 not answered.
            ]
        },
    )

    await service.analyze(db, fake_ai, job.id, today=TODAY)

    items = await _verdicts(db, job)
    assert len(items) == 4
    assert all(i.verdict is Verdict.UNKNOWN and i.message is None for i in items.values())
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "Não encontrei isso no seu perfil para confirmar." in page
    assert "Você atende." not in page


async def test_an_empty_profile_is_not_sent_to_the_model(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    job = await _job(db, user, REQUIREMENTS)

    await service.analyze(db, fake_ai, job.id, today=TODAY)

    assert (await _report(db, job)).status is FitStatus.NO_PROFILE
    assert fake_ai.chat.calls == []
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "preciso conhecer o seu perfil" in page and 'href="/entrevista"' in page


async def test_a_posting_without_requirements_needs_no_model_call(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, [])

    await service.analyze(db, fake_ai, job.id, today=TODAY)

    report = await _report(db, job)
    assert report.status is FitStatus.READY and report.items == []
    assert fake_ai.chat.calls == []
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "A vaga não lista requisitos para comparar" in page


async def test_a_provider_failure_is_a_failed_report_logged_without_content(
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    queue: RecordingQueue,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(FitJudgement, AIProviderError("Ana Souza, 5 anos de Python"))

    job_id = job.id  # the failure rolls back, expiring the objects this test holds

    with caplog.at_level(logging.WARNING, logger="mentor.fit.service"):
        await service.analyze(db, fake_ai, job_id, today=TODAY)

    job = await db.get(JobPosting, job_id, populate_existing=True)
    assert job is not None

    assert (await _report(db, job)).status is FitStatus.FAILED
    assert "AIProviderError" in caplog.text and "Ana Souza" not in caplog.text
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "Não consegui comparar a vaga com o seu perfil agora." in page
    token = csrf_from(page)
    retry = await client.post(
        f"/vagas/{job.id}/compatibilidade", headers={"X-CSRF-Token": token, "HX-Request": "true"}
    )
    assert queue.fits == [job.id]
    assert (await _report(db, job)).status is FitStatus.PENDING
    assert f'hx-get="/vagas/{job.id}/compatibilidade" hx-trigger="every 2s"' in retry.text


async def test_analyzing_again_replaces_the_verdicts(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(FitJudgement, JUDGEMENT)
    await service.analyze(db, fake_ai, job.id, today=TODAY)
    again = {
        "requirements": [
            {**r, "verdict": "gap", "evidence_refs": []} for r in JUDGEMENT["requirements"]
        ]
    }
    fake_ai.chat.script_for(FitJudgement, again)

    await service.analyze(db, fake_ai, job.id, today=TODAY)

    items = await _verdicts(db, job)
    assert len(items) == 4 and {i.verdict for i in items.values()} == {Verdict.GAP}


async def test_only_a_ready_posting_is_analyzed(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    job = await _job(db, user, REQUIREMENTS)
    job.status = JobStatus.FAILED
    await db.commit()

    await service.analyze(db, fake_ai, job.id, today=TODAY)

    assert await service.report_for(db, job.id) is None


# -- the job page --------------------------------------------------------------------------


async def test_the_page_counts_the_verdicts_and_explains_each_one(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(FitJudgement, JUDGEMENT)
    await service.analyze(db, fake_ai, job.id, today=TODAY)

    page = (await client.get(f"/vagas/{job.id}")).text

    assert "Você atende 1 de 3 exigências da vaga." in page
    assert "1 em parte" in page and "1 lacuna" in page
    assert "E 0 de 1 diferencial." in page and "1 sem informação" in page
    assert "A vaga pede 5 anos de Python; você declarou 4." in page
    assert ">Atende<" in page and ">Lacuna<" in page and ">Em parte<" in page
    # The interview isn't complete: gaps may be things not told yet.
    assert "Sua entrevista ainda não terminou" in page
    assert "Seu perfil mudou" not in page and "every 2s" not in page


async def test_no_interview_notice_once_the_interview_was_completed(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    db.add(Interview(user_id=user.id, status=InterviewStatus.COMPLETE))
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(FitJudgement, JUDGEMENT)
    await service.analyze(db, fake_ai, job.id, today=TODAY)

    page = (await client.get(f"/vagas/{job.id}")).text

    assert (await _report(db, job)).interview_complete is True
    assert "Sua entrevista ainda não terminou" not in page


async def test_a_changed_profile_offers_to_redo_the_analysis(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(FitJudgement, JUDGEMENT)
    await service.analyze(db, fake_ai, job.id, today=TODAY)
    page = (await client.get(f"/vagas/{job.id}")).text
    token = csrf_from(page)
    headers = {"X-CSRF-Token": token, "HX-Request": "true"}

    # Not stale: asking again does nothing.
    await client.post(f"/vagas/{job.id}/compatibilidade", headers=headers)
    assert queue.fits == [] and "Refazer análise" not in page

    # Contact details don't matter to the analysis.
    profile = await db.get(Profile, user.id)
    assert profile is not None
    profile.phone = "11 99999-0000"
    await db.commit()
    assert "Refazer análise" not in (await client.get(f"/vagas/{job.id}")).text

    db.add(Language(user_id=user.id, language="Espanhol", level="fluente"))
    await db.commit()
    page = (await client.get(f"/vagas/{job.id}")).text
    assert "Seu perfil mudou desde esta análise." in page and "Refazer análise" in page

    redo = await client.post(f"/vagas/{job.id}/compatibilidade", headers=headers)

    assert queue.fits == [job.id]
    assert "Comparando a vaga com o seu perfil" in redo.text


async def test_the_page_polls_until_the_analysis_is_done(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)

    waiting = await client.get(f"/vagas/{job.id}")
    fake_ai.chat.script_for(FitJudgement, JUDGEMENT)
    await service.analyze(db, fake_ai, job.id, today=TODAY)
    done = await client.get(f"/vagas/{job.id}/compatibilidade")

    polling = f'hx-get="/vagas/{job.id}/compatibilidade" hx-trigger="every 2s"'
    assert polling in waiting.text and "Comparando a vaga com o seu perfil" in waiting.text
    assert 'id="job-fit"' in done.text and "every 2s" not in done.text
    assert "Você atende 1 de 3 exigências" in done.text


async def test_a_lost_analysis_offers_a_retry(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    user = await _user(client, db)
    job = await _job(db, user, REQUIREMENTS)
    job.processed_at = datetime.now(UTC) - service.STUCK_AFTER - timedelta(seconds=1)
    await db.commit()

    page = (await client.get(f"/vagas/{job.id}")).text

    assert "every 2s" not in page and "Tentar de novo" in page
    await client.post(
        f"/vagas/{job.id}/compatibilidade",
        headers={"X-CSRF-Token": csrf_from(page), "HX-Request": "true"},
    )
    assert queue.fits == [job.id]


async def test_another_users_fit_is_not_found(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    owner = await _user(client, db)
    job = await _job(db, owner, REQUIREMENTS)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login_and_consent(other, f"outro-{uuid.uuid4().hex[:8]}@example.com")
        token = csrf_from((await other.get("/vagas")).text)

        assert (await other.get(f"/vagas/{job.id}/compatibilidade")).status_code == 404
        response = await other.post(
            f"/vagas/{job.id}/compatibilidade", headers={"X-CSRF-Token": token}
        )

    assert response.status_code == 404 and queue.fits == []


async def test_deleting_the_job_deletes_its_fit(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    user = await _user(client, db)
    await _profile(db, fake_ai, user)
    job = await _job(db, user, REQUIREMENTS)
    fake_ai.chat.script_for(FitJudgement, JUDGEMENT)
    await service.analyze(db, fake_ai, job.id, today=TODAY)
    token = csrf_from((await client.get(f"/vagas/{job.id}")).text)

    await client.delete(f"/vagas/{job.id}", headers={"X-CSRF-Token": token})

    assert await service.report_for(db, job.id) is None
