import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from mentor import health
from mentor.account import routes as account_routes
from mentor.agents.checkpoint import close_checkpointer, get_checkpointer
from mentor.ai.ports import AIConfigurationError
from mentor.ai.registry import check_google_credentials
from mentor.auth import routes as auth_routes
from mentor.auth.csrf import CSRFMiddleware
from mentor.auth.deps import ConsentRequiredError, LoginRequiredError
from mentor.interview import routes as interview_routes
from mentor.legal import routes as legal_routes
from mentor.profile import routes as profile_routes
from mentor.quotas.service import QuotaExceededError
from mentor.security import SecurityHeadersMiddleware
from mentor.settings import get_settings
from mentor.voice import routes as voice_routes
from mentor.web import STATIC_DIR, templates
from mentor.web import routes as web_routes
from mentor.web.redirects import redirect, with_next

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    try:
        check_google_credentials(settings)
    except AIConfigurationError as exc:
        if not settings.is_dev_like:
            raise
        log.warning("%s: voice mode will fail until they are set", exc)
    # The saver's setup() runs CREATE INDEX CONCURRENTLY, which waits for every open transaction.
    # Doing it on first use inside a request would deadlock against that request's own session.
    await get_checkpointer()
    yield
    await close_checkpointer()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Mentor", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    # Order: last added runs first. Security headers wrap everything.
    app.add_middleware(CSRFMiddleware)
    # Short-lived signed cookie used only for the OAuth state/nonce round-trip.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie="mentor_oauth",
        max_age=600,
        same_site="lax",
        https_only=not settings.is_dev_like,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    if settings.is_dev_like:
        app.include_router(auth_routes.dev_router)
    app.include_router(legal_routes.router)
    app.include_router(interview_routes.router)
    app.include_router(account_routes.router)
    app.include_router(voice_routes.router)
    app.include_router(profile_routes.router)
    app.include_router(web_routes.router)

    @app.exception_handler(LoginRequiredError)
    async def _login_required(request: Request, exc: LoginRequiredError) -> Response:
        return redirect(request, with_next("/entrar", request))

    @app.exception_handler(ConsentRequiredError)
    async def _consent_required(request: Request, exc: ConsentRequiredError) -> Response:
        return redirect(request, with_next("/consentimento", request))

    @app.exception_handler(QuotaExceededError)
    async def _quota_exceeded(request: Request, exc: QuotaExceededError) -> Response:
        return templates.TemplateResponse(
            request, "components/quota_exceeded.html", {"status": exc.status}, status_code=429
        )

    return app
