"""CV pages (M9, D81): a version while it's written, then the CV, what the verification
changed, and the PDF/DOCX downloads. Editing comes in M9b."""

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from starlette.responses import Response

from mentor.auth.deps import DB, CurrentUser
from mentor.cv import render
from mentor.cv.models import CV, CVStatus
from mentor.cv.schemas import Change, CVContent
from mentor.jobs.models import JobPosting
from mentor.web import templates

router = APIRouter(prefix="/curriculos")

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


async def _cv(db: DB, user: CurrentUser, cv_id: uuid.UUID) -> tuple[CV, JobPosting]:
    """The user's own CV version and its posting, or 404 (never another user's)."""
    row = (
        await db.execute(
            select(CV, JobPosting)
            .join(JobPosting, JobPosting.id == CV.job_id)
            .where(CV.id == cv_id, CV.user_id == user.id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404)
    return row.CV, row.JobPosting


def _context(cv: CV, job: JobPosting) -> dict[str, object]:
    context: dict[str, object] = {"cv": cv, "job": job}
    if cv.status is CVStatus.READY and cv.content is not None:
        content = CVContent.model_validate(cv.content)
        context |= {
            "doc": render.context(content),
            "changes": [Change.model_validate(c) for c in cv.changes or []],
        }
    return context


@router.get("/{cv_id}", response_class=HTMLResponse)
async def cv_page(request: Request, cv_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    cv, job = await _cv(db, user, cv_id)
    return templates.TemplateResponse(
        request, "cv/page.html", {"active_nav": "jobs", **_context(cv, job)}
    )


@router.get("/{cv_id}/estado", response_class=HTMLResponse)
async def cv_state(request: Request, cv_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    """Polled while the CV is written; stops polling once it's done."""
    cv, job = await _cv(db, user, cv_id)
    return templates.TemplateResponse(request, "cv/_body.html", _context(cv, job))


@router.get("/{cv_id}/{kind}")
async def download(cv_id: uuid.UUID, kind: str, db: DB, user: CurrentUser) -> Response:
    if kind not in MEDIA_TYPES:
        raise HTTPException(status_code=404)
    cv, job = await _cv(db, user, cv_id)
    if cv.status is not CVStatus.READY or cv.content is None:
        raise HTTPException(status_code=404)
    content = CVContent.model_validate(cv.content)
    data = await (render.pdf(content) if kind == "pdf" else render.docx(content))
    name = render.filename(content, job.title, kind)
    return Response(
        data,
        media_type=MEDIA_TYPES[kind],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
