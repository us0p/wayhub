from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mentor.auth.deps import DB, CurrentUser
from mentor.interview.store import checklist_progress, latest_interview
from mentor.web import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, user: CurrentUser, db: DB) -> HTMLResponse:
    interview = await latest_interview(db, user)
    response: HTMLResponse = templates.TemplateResponse(
        request,
        "home.html",
        {
            "active_nav": "home",
            "user": user,
            "progress": await checklist_progress(db, user),
            "interview_status": interview.status.value if interview else None,
        },
    )
    return response
