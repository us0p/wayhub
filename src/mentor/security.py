"""HTTP security headers applied to every response (D37)."""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data: https://lh3.googleusercontent.com",  # Google avatars
        "connect-src 'self'",
        "media-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self' https://accounts.google.com",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Microphone is needed for the voice interview, only from our own origin.
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# Production only (D84): Cloudflare terminates TLS, and its zone-wide HSTS would also bind the
# owner's other subdomains, so the app sends it for its own host (no includeSubDomains).
HSTS = "max-age=31536000"

# Pages and event streams ask proxies not to change them: Cloudflare then injects none of its
# scripts (Web Analytics beacon, email obfuscation, Rocket Loader), which the CSP would block
# (D89). Static files keep their compression.
NO_TRANSFORM_TYPES = ("text/html", "text/event-stream")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        super().__init__(app)
        self.headers = SECURITY_HEADERS | ({"Strict-Transport-Security": HSTS} if hsts else {})

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in self.headers.items():
            response.headers.setdefault(name, value)
        if response.headers.get("content-type", "").startswith(NO_TRANSFORM_TYPES):
            cache = response.headers.get("cache-control")
            if cache is None:
                response.headers["Cache-Control"] = "no-transform"
            elif "no-transform" not in cache:
                response.headers["Cache-Control"] = f"{cache}, no-transform"
        return response
