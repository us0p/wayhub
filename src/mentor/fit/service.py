"""The fit warning (M8, D13, D80): compare a posting's requirements with the candidate's profile.

Runs on the worker right after the posting is read (`jobs.tasks`), and again when the user asks
for it after their profile changed. Years of experience are computed here (`years.py`); the
model judges each requirement with those figures and the whole profile, and its answer is
checked: a "met" or "partial" must point to real facts of this profile, or it becomes
"unknown" with no message. The result never blocks anything (D13).
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mentor.ai.bundle import AI
from mentor.ai.errors import describe
from mentor.auth.models import User
from mentor.fit import prompts, years
from mentor.fit.models import FitItem, FitReport, FitStatus, Verdict
from mentor.fit.schemas import FitJudgement, JudgedRequirement
from mentor.interview import store
from mentor.jobs.models import JobPosting, JobRequirement, JobStatus, RequirementImportance
from mentor.profile import service as profile_service
from mentor.profile.service import Snapshot

log = logging.getLogger(__name__)

# A report still pending after this long was lost (a worker restart): the page offers to retry.
STUCK_AFTER = timedelta(minutes=3)


def now() -> datetime:
    return datetime.now(UTC)


def digest(snap: Snapshot) -> str:
    """Identifies the profile as the analysis reads it (contact details aside)."""
    text = profile_service.render(snap, contact=False) if not snap.is_empty else ""
    return hashlib.sha256(text.encode()).hexdigest()


async def report_for(db: AsyncSession, job_id: uuid.UUID) -> FitReport | None:
    return await db.scalar(
        select(FitReport)
        .where(FitReport.job_id == job_id)
        .options(selectinload(FitReport.items))
        .execution_options(populate_existing=True)
    )


async def request(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID) -> FitReport:
    """Mark the posting's analysis as pending (the caller commits and queues it)."""
    report = await report_for(db, job_id)
    if report is None:
        report = FitReport(job_id=job_id, user_id=user_id, items=[])
        db.add(report)
    report.status, report.requested_at, report.finished_at = FitStatus.PENDING, now(), None
    await db.flush()
    return report


async def analyze(db: AsyncSession, ai: AI, job_id: uuid.UUID, today: date | None = None) -> None:
    """Compare the posting with its owner's profile and store the report. Never raises for a
    provider failure: the outcome is the report's status."""
    job = await db.scalar(
        select(JobPosting)
        .where(JobPosting.id == job_id)
        .options(selectinload(JobPosting.requirements))
        .execution_options(populate_existing=True)
    )
    if job is None or job.status is not JobStatus.READY:
        return
    user = await db.get(User, job.user_id)
    assert user is not None
    await request(db, job_id, user.id)
    await db.commit()

    snap = await profile_service.snapshot(db, user)
    profile_digest = digest(snap)
    interview_complete = await store.has_completed_interview(db, user)
    refs = prompts.requirement_refs(job.requirements)
    judgement: FitJudgement | None = None
    status = FitStatus.READY
    if snap.is_empty:
        status = FitStatus.NO_PROFILE
    elif refs:
        messages = prompts.fit_messages(
            refs,
            profile_service.render(snap, contact=False),
            years.render(snap, today or date.today()),
        )
        try:
            judgement = await ai.chat.structured(FitJudgement).ainvoke(messages)
        except Exception as exc:
            await db.rollback()
            log.warning("fit analysis failed (prompt %s): %s", prompts.VERSION, describe(exc))
            status = FitStatus.FAILED

    report = await report_for(db, job_id)
    if report is None:
        return  # the posting was deleted meanwhile
    report.status, report.finished_at = status, now()
    if status is not FitStatus.FAILED:
        report.profile_digest, report.interview_complete = profile_digest, interview_complete
        report.items.clear()
        await db.flush()  # old items go before the new ones (one per requirement)
        if judgement is not None:
            report.items.extend(_items(report.user_id, refs, snap, judgement))
    await db.commit()


def _items(
    user_id: uuid.UUID,
    refs: dict[str, JobRequirement],
    snap: Snapshot,
    judgement: FitJudgement,
) -> list[FitItem]:
    answers: dict[str, JudgedRequirement] = {}
    for judged in judgement.requirements:
        answers.setdefault(judged.ref.strip().upper(), judged)
    items = []
    for ref, req in refs.items():
        answer = answers.get(ref)
        if answer is None:
            items.append(FitItem(user_id=user_id, requirement_id=req.id, verdict=Verdict.UNKNOWN))
            continue
        evidence = [
            snap.refs[r].id
            for r in dict.fromkeys(r.strip().upper() for r in answer.evidence_refs)
            if r in snap.refs
        ]
        verdict, message = answer.verdict, answer.message.strip() or None
        if verdict in (Verdict.MET, Verdict.PARTIAL) and not evidence:
            # Model A spirit (D12): a claim the profile can't back is not shown.
            verdict, message = Verdict.UNKNOWN, None
        items.append(
            FitItem(
                user_id=user_id,
                requirement_id=req.id,
                verdict=verdict,
                message=message,
                evidence_ids=evidence,
            )
        )
    return items


@dataclass(frozen=True)
class FitView:
    """What the job page shows about the fit."""

    report: FitReport | None
    waiting: bool  # being analyzed: the fragment polls
    stuck: bool  # lost on the way: offer a retry
    stale: bool  # the profile changed since: offer to redo it
    interview_complete: bool

    @property
    def can_request(self) -> bool:
        if self.report is None or self.stuck:
            return self.stuck
        return self.report.status is FitStatus.FAILED or self.stale

    def verdicts(self) -> dict[uuid.UUID, FitItem]:
        if self.report is None or self.report.status is not FitStatus.READY:
            return {}
        return {item.requirement_id: item for item in self.report.items}

    def tally(self, requirements: list[JobRequirement]) -> dict[str, dict[str, int]]:
        """Per importance (must/nice): how many requirements were judged, and per verdict."""
        verdicts = self.verdicts()
        counts = {i.value: dict.fromkeys(["total", *Verdict], 0) for i in RequirementImportance}
        for req in requirements:
            if (item := verdicts.get(req.id)) is not None:
                counts[req.importance.value]["total"] += 1
                counts[req.importance.value][item.verdict.value] += 1
        return counts


async def view(db: AsyncSession, user: User, job: JobPosting) -> FitView:
    report = await report_for(db, job.id)
    current = now()
    if report is None:
        # The import task analyzes right after reading the posting.
        lost = job.processed_at is None or current - job.processed_at > STUCK_AFTER
        return FitView(None, waiting=not lost, stuck=lost, stale=False, interview_complete=False)
    pending = report.status is FitStatus.PENDING
    stuck = pending and current - report.requested_at > STUCK_AFTER
    stale = False
    if not pending and report.status is not FitStatus.FAILED:
        stale = report.profile_digest != digest(await profile_service.snapshot(db, user))
    return FitView(
        report,
        waiting=pending and not stuck,
        stuck=stuck,
        stale=stale,
        interview_complete=await store.has_completed_interview(db, user),
    )
