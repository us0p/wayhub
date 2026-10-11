from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.requests import Request

from mentor.i18n import gettext_, ngettext_

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"

_env = Environment(
    loader=FileSystemLoader(WEB_DIR / "templates"),
    autoescape=select_autoescape(),
    extensions=["jinja2.ext.i18n"],
)
_env.install_gettext_callables(gettext_, ngettext_, newstyle=True)  # type: ignore[attr-defined]

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")  # the users' (D39)


def _local_date(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%d/%m/%Y")


_env.filters["local_date"] = _local_date


def _request_context(request: Request) -> dict[str, Any]:
    """Available in every template: the signed-in user and the CSRF token for htmx."""
    from mentor.auth.sessions import csrf_token_for

    token: str | None = getattr(request.state, "session_token", None)
    return {
        "current_user": getattr(request.state, "user", None),
        "csrf_token": csrf_token_for(token) if token else None,
    }


templates = Jinja2Templates(env=_env, context_processors=[_request_context])
