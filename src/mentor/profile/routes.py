"""The profile review/edit screen (D15, D60, D61). The page lists what the interview gathered;
every item is edited or deleted in place with htmx: a card swaps itself for a form and back.
Nothing is added by hand: new facts come from talking to the interviewer. Validation errors
re-render the form with a 200, because htmx only swaps 2xx (and 409/429) responses."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from mentor.ai.registry import AIServices
from mentor.auth.deps import DB, CurrentUser
from mentor.interview.store import latest_interview
from mentor.profile import edit
from mentor.profile.edit import KINDS, Conflict, Kind
from mentor.profile.models import Experience, Profile
from mentor.profile.service import Fact, snapshot
from mentor.web import templates

router = APIRouter(prefix="/perfil")


def _kind(slug: str) -> Kind:
    if (kind := KINDS.get(slug)) is None:
        raise HTTPException(status_code=404)
    return kind


async def _load(db: DB, user: CurrentUser, kind: Kind, item_id: uuid.UUID) -> Fact:
    """The user's own fact of this kind, or 404 (never another user's)."""
    model: Any = kind.model
    query = select(model).where(model.id == item_id, model.user_id == user.id)
    if kind.model is Experience:
        query = query.options(selectinload(Experience.bullets))
    fact: Fact | None = await db.scalar(query)
    if fact is None:
        raise HTTPException(status_code=404)
    return fact


def _item(
    request: Request,
    kind: Kind,
    item: Fact,
    mode: str,
    values: edit.Values | None = None,
    errors: edit.Errors | None = None,
) -> Response:
    return templates.TemplateResponse(
        request,
        "profile/_item.html",
        {
            "kind": kind,
            "item": item,
            "mode": mode,
            "values": values if values is not None else kind.form_values(item),
            "errors": errors or {},
        },
    )


def _contact(
    request: Request,
    profile: Profile | None,
    mode: str,
    values: edit.Values | None = None,
    errors: edit.Errors | None = None,
) -> Response:
    return templates.TemplateResponse(
        request,
        "profile/_contact_fragment.html",
        {
            "profile": profile,
            "mode": mode,
            "values": values if values is not None else edit.contact_form_values(profile),
            "errors": errors or {},
        },
    )


@router.get("", response_class=HTMLResponse)
async def profile_page(request: Request, user: CurrentUser, db: DB) -> Response:
    interview = await latest_interview(db, user)
    return templates.TemplateResponse(
        request,
        "profile/profile.html",
        {
            "active_nav": "profile",
            "snapshot": await snapshot(db, user),
            "kinds": KINDS,
            "interview_status": interview.status.value if interview else None,
        },
    )


# Contact and preferences: one row per user, so no id in the URL. Registered before the
# `/{slug}/...` routes.


@router.get("/contato", response_class=HTMLResponse)
async def contact_view(request: Request, user: CurrentUser, db: DB) -> Response:
    return _contact(request, await db.get(Profile, user.id), "view")


@router.get("/contato/editar", response_class=HTMLResponse)
async def contact_form(request: Request, user: CurrentUser, db: DB) -> Response:
    return _contact(request, await db.get(Profile, user.id), "form")


@router.post("/contato", response_class=HTMLResponse)
async def contact_save(request: Request, user: CurrentUser, db: DB) -> Response:
    form = await request.form()
    values, errors = edit.parse_contact(form)
    if errors:
        return _contact(request, None, "form", edit.raw_values(form), errors)
    profile = await edit.save_contact(db, user, values)
    await db.commit()
    return _contact(request, profile, "view")


@router.get("/{slug}/{item_id}", response_class=HTMLResponse)
async def view(
    request: Request, slug: str, item_id: uuid.UUID, user: CurrentUser, db: DB
) -> Response:
    kind = _kind(slug)
    return _item(request, kind, await _load(db, user, kind, item_id), "view")


@router.get("/{slug}/{item_id}/editar", response_class=HTMLResponse)
async def edit_form(
    request: Request, slug: str, item_id: uuid.UUID, user: CurrentUser, db: DB
) -> Response:
    kind = _kind(slug)
    return _item(request, kind, await _load(db, user, kind, item_id), "form")


@router.post("/{slug}/{item_id}", response_class=HTMLResponse)
async def update(
    request: Request, slug: str, item_id: uuid.UUID, user: CurrentUser, db: DB, ai: AIServices
) -> Response:
    kind = _kind(slug)
    return await _save(request, kind, await _load(db, user, kind, item_id), user, db, ai)


@router.delete("/{slug}/{item_id}", response_class=HTMLResponse)
async def remove(slug: str, item_id: uuid.UUID, user: CurrentUser, db: DB) -> Response:
    kind = _kind(slug)
    await edit.delete_fact(db, user, await _load(db, user, kind, item_id))
    await db.commit()
    return Response(status_code=200)  # an empty body swaps the card away


async def _save(
    request: Request,
    kind: Kind,
    item: Fact,
    user: CurrentUser,
    db: DB,
    ai: AIServices,
) -> Response:
    form = await request.form()
    values, errors = kind.parse(form)
    if not errors:
        try:
            saved = await kind.save(db, ai.embeddings, user, item, values)
        except Conflict as conflict:
            errors = {conflict.field: conflict.message}
        else:
            await db.commit()
            return _item(request, kind, saved, "view")
    # Show the rejected form as typed.
    return _item(request, kind, item, "form", edit.raw_values(form), errors)
