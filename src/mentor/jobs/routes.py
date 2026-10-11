"""Job pages (M7, D78): import a posting (`/vagas/nova`), follow it while the worker reads it,
then see what it asks for. A posting is only viewed or deleted here; nothing is edited."""

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.datastructures import UploadFile  # what request.form() returns
from starlette.responses import Response

from mentor.auth.deps import DB, CurrentUser
from mentor.jobs import service
from mentor.jobs.models import JobPosting, JobStatus
from mentor.jobs.queue import Queue
from mentor.quotas import service as quotas
from mentor.quotas.models import QuotaKind
from mentor.web import templates
from mentor.web.redirects import redirect

router = APIRouter(prefix="/vagas")

# Above every allowed upload together (5 images of 10 MB) plus the form: refused before reading.
MAX_REQUEST_BYTES = service.MAX_IMAGES * service.MAX_UPLOAD_BYTES + 1024 * 1024
WAITING = (JobStatus.QUEUED, JobStatus.PROCESSING)


async def _job(db: DB, user: CurrentUser, job_id: uuid.UUID) -> JobPosting:
    """The user's own posting, or 404 (never another user's)."""
    job = await db.scalar(
        select(JobPosting)
        .where(JobPosting.id == job_id, JobPosting.user_id == user.id)
        .options(selectinload(JobPosting.requirements))
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise HTTPException(status_code=404)
    return job


@router.get("", response_class=HTMLResponse)
async def job_list(request: Request, db: DB, user: CurrentUser) -> Response:
    jobs = await service.user_jobs(db, user)
    return templates.TemplateResponse(
        request, "jobs/list.html", {"active_nav": "jobs", "jobs": jobs, "waiting": WAITING}
    )


@router.get("/nova", response_class=HTMLResponse)
async def new_job(request: Request, db: DB, user: CurrentUser) -> Response:
    quota = await quotas.status(db, user, QuotaKind.JOB_IMPORT)
    return templates.TemplateResponse(
        request, "jobs/new.html", {"active_nav": "jobs", "quota": quota, "text": "", "error": None}
    )


@router.post("", response_class=HTMLResponse)
async def submit_job(request: Request, db: DB, user: CurrentUser, queue: Queue) -> Response:
    if int(request.headers.get("content-length") or 0) > MAX_REQUEST_BYTES:
        return _form(request, "", "file_size")
    form = await request.form(max_files=service.MAX_IMAGES + 1)
    text = str(form.get("entrada") or "")
    files = [f for f in form.getlist("arquivos") if isinstance(f, UploadFile) and f.filename]
    if len(files) > service.MAX_IMAGES:
        return _form(request, text, "too_many_images")
    uploads = [
        service.Upload(content_type=(f.content_type or "").lower(), data=await f.read())
        for f in files
    ]
    await quotas.check(db, user, QuotaKind.JOB_IMPORT)
    try:
        job = await service.submit(db, user, text, uploads)
    except service.SubmitError as exc:  # raised before anything is written
        return _form(request, text, exc.code)
    # Counted on submission: reading it costs the same whatever the outcome (D78).
    await quotas.record(db, user, QuotaKind.JOB_IMPORT)
    await db.commit()
    await queue.import_job(job.id)
    return redirect(request, f"/vagas/{job.id}")


def _form(request: Request, text: str, error: str) -> Response:
    """The form again with the error; a 200, since htmx only swaps 2xx responses."""
    return templates.TemplateResponse(request, "jobs/_form.html", {"text": text, "error": error})


@router.get("/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    job = await _job(db, user, job_id)
    return templates.TemplateResponse(
        request, "jobs/detail.html", {"active_nav": "jobs", "job": job, "waiting": WAITING}
    )


@router.get("/{job_id}/estado", response_class=HTMLResponse)
async def job_state(request: Request, job_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    """Polled while the worker reads the posting; stops polling once it's done."""
    job = await _job(db, user, job_id)
    return templates.TemplateResponse(request, "jobs/_body.html", {"job": job, "waiting": WAITING})


@router.delete("/{job_id}")
async def delete_job(request: Request, job_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    job = await _job(db, user, job_id)
    await db.delete(job)
    await db.commit()
    return redirect(request, "/vagas")
