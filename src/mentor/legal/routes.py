from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from mentor.auth.deps import DB, AuthenticatedUser
from mentor.auth.models import Consent
from mentor.legal import PRIVACY_VERSION, TERMS_VERSION, has_current_consent
from mentor.settings import get_settings
from mentor.web import templates
from mentor.web.redirects import redirect, safe_next

router = APIRouter()


def _legal_context() -> dict[str, object]:
    return {
        "terms_version": TERMS_VERSION,
        "privacy_version": PRIVACY_VERSION,
        "contact_email": get_settings().privacy_contact_email,
    }


@router.get("/termos", response_class=HTMLResponse)
async def terms(request: Request) -> Response:
    return templates.TemplateResponse(request, "legal/terms.html", _legal_context())


@router.get("/privacidade", response_class=HTMLResponse)
async def privacy(request: Request) -> Response:
    return templates.TemplateResponse(request, "legal/privacy.html", _legal_context())


@router.get("/consentimento", response_class=HTMLResponse)
async def consent_page(
    request: Request, user: AuthenticatedUser, db: DB, next: str = "/"
) -> Response:
    if await has_current_consent(db, user.id):
        return redirect(request, safe_next(next))
    return templates.TemplateResponse(
        request, "legal/consent.html", {"next": safe_next(next), **_legal_context()}
    )


@router.post("/consentimento")
async def accept_consent(request: Request, user: AuthenticatedUser, db: DB) -> Response:
    form = await request.form()
    if form.get("aceito") != "sim":
        return templates.TemplateResponse(
            request,
            "legal/consent.html",
            {"next": safe_next(str(form.get("next", "/"))), "error": True, **_legal_context()},
            status_code=422,
        )
    if not await has_current_consent(db, user.id):
        db.add(
            Consent(user_id=user.id, terms_version=TERMS_VERSION, privacy_version=PRIVACY_VERSION)
        )
        await db.commit()
    return redirect(request, safe_next(str(form.get("next", "/"))))
