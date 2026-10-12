"""CV pages (M9, D81): a version while it's written, then the CV, what the verification
changed, and the PDF/DOCX downloads; and the editor (M9b), where each block of the CV swaps
itself for its edit form and back with htmx. Validation errors re-render the form with a 200,
because htmx only swaps 2xx responses."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from starlette.responses import Response

from mentor.auth.deps import DB, CurrentUser
from mentor.cv import edit, render
from mentor.cv.models import CV, CVStatus
from mentor.cv.schemas import Change, CVContent, ExperienceEntry
from mentor.i18n import gettext_ as _
from mentor.jobs.models import JobPosting
from mentor.jobs.queue import Queue
from mentor.profile.edit import Reader
from mentor.web import templates
from mentor.web.redirects import redirect

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
            "edited": edit.edited(content),
        }
    return context


async def _ready(db: DB, user: CurrentUser, cv_id: uuid.UUID) -> tuple[CV, CVContent]:
    """The user's own CV version, once written: only those can be edited."""
    cv, _job = await _cv(db, user, cv_id)
    if cv.status is not CVStatus.READY or cv.content is None:
        raise HTTPException(status_code=404)
    return cv, CVContent.model_validate(cv.content)


def _item(content: CVContent, path: str) -> edit.Item:
    if (item := edit.find(content, path)) is None:
        raise HTTPException(status_code=404)
    return item


def _editor(content: CVContent) -> dict[str, object]:
    words = render.labels(content.language)
    return {
        "content": content,
        "words": words,
        "periods": [render.period(e, words) for e in content.experiences],
    }


def _block(
    request: Request,
    cv: CV,
    content: CVContent,
    block: str,
    editing: str | None = None,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
) -> Response:
    return templates.TemplateResponse(
        request,
        "cv/_edit_block.html",
        {
            "cv": cv,
            "key": block,
            "editing": editing,
            "values": values or {},
            "errors": errors or {},
            **_editor(content),
        },
    )


async def _store(db: DB, cv: CV, content: CVContent) -> None:
    cv.content = content.model_dump(mode="json")
    await db.commit()


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


@router.post("/{cv_id}/tentar-novamente", response_class=HTMLResponse)
async def retry(
    request: Request, cv_id: uuid.UUID, db: DB, user: CurrentUser, queue: Queue
) -> Response:
    """Queue a lost version again (M10). Not metered: it was counted when requested, and
    losing the task was our fault."""
    cv, job = await _cv(db, user, cv_id)
    if cv.lost:
        cv.requested_at = datetime.now(UTC)
        await db.commit()
        await queue.generate_cv(cv.id)
    return templates.TemplateResponse(request, "cv/_body.html", _context(cv, job))


@router.get("/{cv_id}/editar", response_class=HTMLResponse)
async def editor(request: Request, cv_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    cv, job = await _cv(db, user, cv_id)
    if cv.status is not CVStatus.READY or cv.content is None:
        return redirect(request, f"/curriculos/{cv.id}")
    content = CVContent.model_validate(cv.content)
    return templates.TemplateResponse(
        request,
        "cv/edit.html",
        {
            "active_nav": "jobs",
            "cv": cv,
            "job": job,
            "blocks": edit.blocks(content),
            **_editor(content),
        },
    )


@router.get("/{cv_id}/itens/{path}", response_class=HTMLResponse)
async def item_view(
    request: Request, cv_id: uuid.UUID, path: str, db: DB, user: CurrentUser
) -> Response:
    cv, content = await _ready(db, user, cv_id)
    return _block(request, cv, content, _item(content, path).block)


@router.get("/{cv_id}/itens/{path}/editar", response_class=HTMLResponse)
async def item_form(
    request: Request, cv_id: uuid.UUID, path: str, db: DB, user: CurrentUser
) -> Response:
    cv, content = await _ready(db, user, cv_id)
    item = _item(content, path)
    if not item.editable or isinstance(item.node, ExperienceEntry):
        raise HTTPException(status_code=404)
    return _block(request, cv, content, item.block, path, {"texto": item.node.text})


@router.post("/{cv_id}/itens/{path}", response_class=HTMLResponse)
async def item_save(
    request: Request, cv_id: uuid.UUID, path: str, db: DB, user: CurrentUser
) -> Response:
    cv, content = await _ready(db, user, cv_id)
    item = _item(content, path)
    if item.limit is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    reader = Reader(form)
    text = reader.text("texto", item.limit)
    if not text:
        reader.errors.setdefault("texto", _("Escreva o texto, ou oculte este item."))
    if reader.errors or text is None:
        values = {"texto": str(form.get("texto", ""))}
        return _block(request, cv, content, item.block, path, values, reader.errors)
    edit.set_text(item, text)
    await _store(db, cv, content)
    return _block(request, cv, content, item.block)


@router.post("/{cv_id}/itens/{path}/{action}", response_class=HTMLResponse)
async def item_visibility(
    request: Request, cv_id: uuid.UUID, path: str, action: str, db: DB, user: CurrentUser
) -> Response:
    if action not in ("ocultar", "mostrar"):
        raise HTTPException(status_code=404)
    cv, content = await _ready(db, user, cv_id)
    item = _item(content, path)
    if not item.hideable:
        raise HTTPException(status_code=404)
    edit.set_hidden(item, action == "ocultar")
    await _store(db, cv, content)
    return _block(request, cv, content, item.block)


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
