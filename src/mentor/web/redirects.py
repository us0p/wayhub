from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response


def safe_next(target: str | None, default: str = "/") -> str:
    """Only allow same-site relative paths as post-login targets (no open redirects)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    return target


def redirect(request: Request, url: str) -> Response:
    """Redirect that also works for htmx requests (which would otherwise swap the page in)."""
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def with_next(path: str, request: Request) -> str:
    current = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return f"{path}?next={quote(current, safe='')}"
