"""Job import (M7, D78): the request side creates a `JobPosting` (and its uploads) and queues it;
the worker side (`process`) turns the source into text, reads the posting with the model and
stores the result.

`process` is idempotent: a posting already processed is left alone, so a task that runs twice
(a worker restart) does no harm.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.bundle import AI
from mentor.ai.errors import describe
from mentor.auth.models import User
from mentor.jobs import prompts, sources
from mentor.jobs.errors import NeedsManualError, NotAJobError
from mentor.jobs.models import (
    JobPosting,
    JobRequirement,
    JobSource,
    JobStatus,
    JobUpload,
    RequirementKind,
)
from mentor.jobs.schemas import ParsedJob
from mentor.models import fit_to_column as _fit

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # shorter than this, pasted text is not a whole posting
MAX_TEXT_CHARS = 50_000
MAX_URL_CHARS = 2000
MAX_IMAGES = 5
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
PDF_TYPE = "application/pdf"


class SubmitError(ValueError):
    """The submission can't be imported; `code` picks the message the form shows."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Upload:
    content_type: str
    data: bytes


def as_url(text: str) -> str | None:
    """A lone http(s) link (or `www.` address), else None: then the text is the posting."""
    text = text.strip()
    if not text or any(c.isspace() for c in text) or len(text) > MAX_URL_CHARS:
        return None
    if text.lower().startswith("www."):
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return text


def _kind_of_uploads(uploads: list[Upload]) -> JobSource:
    types = {u.content_type for u in uploads}
    if types == {PDF_TYPE}:
        if len(uploads) > 1:
            raise SubmitError("one_pdf")
        return JobSource.PDF
    if not types <= IMAGE_TYPES:
        raise SubmitError("file_type")
    if len(uploads) > MAX_IMAGES:
        raise SubmitError("too_many_images")
    return JobSource.IMAGES


async def submit(db: AsyncSession, user: User, text: str, uploads: list[Upload]) -> JobPosting:
    """Create the posting to import; attachments win over the text field (D78). Everything
    is validated before anything is written, so a `SubmitError` leaves the session clean."""
    uploads = [u for u in uploads if u.data]
    if any(len(u.data) > MAX_UPLOAD_BYTES for u in uploads):
        raise SubmitError("file_size")
    job = JobPosting(user_id=user.id)
    if uploads:
        job.source = _kind_of_uploads(uploads)
    elif (url := as_url(text)) is not None:
        job.source, job.source_url = JobSource.URL, url
    else:
        text = text.strip()
        if not text:
            raise SubmitError("empty")
        if len(text) < MIN_TEXT_CHARS:
            raise SubmitError("too_short")
        if len(text) > MAX_TEXT_CHARS:
            raise SubmitError("too_long")
        job.source, job.source_text = JobSource.TEXT, text
    db.add(job)
    await db.flush()
    for position, upload in enumerate(uploads):
        db.add(
            JobUpload(
                job_id=job.id,
                user_id=user.id,
                position=position,
                content_type=upload.content_type,
                data=upload.data,
            )
        )
    await db.flush()
    return job


async def process(db: AsyncSession, ai: AI, job_id: uuid.UUID) -> None:
    """Read the posting and store what it asks for. Never raises for a bad posting or a
    provider failure: the outcome is the posting's status."""
    job = await db.get(JobPosting, job_id, populate_existing=True)
    if job is None or job.status not in (JobStatus.QUEUED, JobStatus.PROCESSING):
        return  # deleted meanwhile, or already done
    job.status = JobStatus.PROCESSING
    await db.commit()
    try:
        if job.source_text is None:
            job.source_text = await sources.read(db, ai, job)
        parsed = await ai.chat.structured(ParsedJob).ainvoke(
            prompts.extract_messages(job.source_text)
        )
        _store(db, job, parsed)
    except NeedsManualError as exc:
        job.status, job.status_reason = JobStatus.NEEDS_MANUAL, exc.reason
    except NotAJobError:
        job.status = JobStatus.NOT_A_JOB
    except Exception as exc:
        await db.rollback()
        job = await db.get(JobPosting, job_id, populate_existing=True)
        if job is None:
            return
        log.warning(
            "job import failed (%s, prompt %s): %s", job.source, prompts.VERSION, describe(exc)
        )
        job.status = JobStatus.FAILED
    # The raw files are only needed to get the text (D29).
    await db.execute(delete(JobUpload).where(JobUpload.job_id == job.id))
    job.processed_at = datetime.now(UTC)
    await db.commit()


def _store(db: AsyncSession, job: JobPosting, parsed: ParsedJob) -> None:
    if not parsed.is_job_posting:
        job.status = JobStatus.NOT_A_JOB
        return
    for name in ("title", "company", "location", "work_mode", "seniority", "language"):
        setattr(job, name, _fit(JobPosting, name, getattr(parsed, name)))
    job.is_tech, job.summary = parsed.is_tech, parsed.summary
    for position, req in enumerate(r for r in parsed.requirements if r.text.strip()):
        skill = req.skill.strip() if req.skill and req.kind is RequirementKind.SKILL else None
        db.add(
            JobRequirement(
                job_id=job.id,
                user_id=job.user_id,
                position=position,
                importance=req.importance,
                kind=req.kind,
                text=req.text.strip(),
                skill=skill[:120] if skill else None,
                min_years=req.min_years if req.min_years and req.min_years > 0 else None,
            )
        )
    job.status = JobStatus.READY


async def user_jobs(db: AsyncSession, user: User) -> list[JobPosting]:
    return list(
        await db.scalars(
            select(JobPosting).where(JobPosting.user_id == user.id).order_by(JobPosting.seq.desc())
        )
    )
