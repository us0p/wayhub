"""CSRF protection (D41): SameSite=Lax cookie + Origin check + session-bound token header.

All state-changing requests from the UI go through htmx, which sends `X-CSRF-Token`
(set once via `hx-headers` on <body>). Requests without a session cookie have no session
to abuse, so they only get the Origin check.
"""

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from mentor.auth.sessions import cookie_name, csrf_token_valid

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
                return PlainTextResponse("Origem inválida.", status_code=403)
            token = request.cookies.get(cookie_name())
            if token and not csrf_token_valid(token, request.headers.get("x-csrf-token")):
                return PlainTextResponse("Token CSRF inválido.", status_code=403)
        return await call_next(request)
