from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mentor.web import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    response: HTMLResponse = templates.TemplateResponse(
        request, "home.html", {"active_nav": "home"}
    )
    return response
