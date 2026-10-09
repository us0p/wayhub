from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select
from starlette.responses import Response

from mentor.agents.checkpoint import Checkpointer, delete_threads
from mentor.auth.deps import DB, CurrentUser
from mentor.auth.models import User
from mentor.auth.sessions import cookie_name, cookie_secure
from mentor.interview.models import Interview
from mentor.quotas import service as quotas
from mentor.web import templates
from mentor.web.redirects import redirect

router = APIRouter()

DELETE_CONFIRMATION = "EXCLUIR"


@router.get("/perfil", response_class=HTMLResponse)
async def profile(request: Request, user: CurrentUser, db: DB) -> Response:
    return templates.TemplateResponse(
        request,
        "profile/profile.html",
        {"active_nav": "profile", "user": user, "quotas": await quotas.summary(db, user)},
    )


@router.get("/conta/excluir", response_class=HTMLResponse)
async def delete_account_page(request: Request, user: CurrentUser) -> Response:
    return templates.TemplateResponse(
        request,
        "profile/delete_account.html",
        {"active_nav": "profile", "confirmation": DELETE_CONFIRMATION},
    )


@router.post("/conta/excluir")
async def delete_account(
    request: Request, user: CurrentUser, db: DB, checkpointer: Checkpointer
) -> Response:
    form = await request.form()
    if str(form.get("confirmacao", "")).strip().upper() != DELETE_CONFIRMATION:
        return templates.TemplateResponse(
            request,
            "profile/delete_account.html",
            {"active_nav": "profile", "confirmation": DELETE_CONFIRMATION, "error": True},
            status_code=422,
        )
    # Hard delete (D21): every user-owned table cascades on users.id; the agent's checkpoints
    # live outside that schema and are deleted per interview thread (D56).
    interview_ids = await db.scalars(select(Interview.id).where(Interview.user_id == user.id))
    await delete_threads(checkpointer, [str(i) for i in interview_ids])
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()
    response = redirect(request, "/entrar?aviso=conta-excluida")
    response.delete_cookie(cookie_name(), path="/", secure=cookie_secure(), httponly=True)
    return response
