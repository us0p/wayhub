from typing import Annotated, Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from mentor.auth.deps import DB, OptionalUser
from mentor.auth.models import Plan, User
from mentor.auth.sessions import (
    SESSION_TTL,
    cookie_name,
    cookie_secure,
    create_session,
    revoke_session,
)
from mentor.auth.users import upsert_user
from mentor.settings import get_settings
from mentor.web import templates
from mentor.web.redirects import redirect, safe_next

router = APIRouter()
dev_router = APIRouter()  # registered only when APP_ENV is dev/test (D42)

_oauth = OAuth()


def _google() -> Any:  # authlib's starlette client is untyped
    settings = get_settings()
    if not settings.google_login_enabled:
        raise HTTPException(503, "Login com Google não configurado.")
    client = _oauth.create_client("google")
    if client is None:
        assert settings.google_client_secret is not None
        client = _oauth.register(
            "google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    return client


async def start_session(db: DB, request: Request, user: User, next_url: str) -> Response:
    token = await create_session(db, user)
    await db.commit()
    response = redirect(request, safe_next(next_url))
    response.set_cookie(
        cookie_name(),
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    return response


@router.get("/entrar", response_class=HTMLResponse)
async def login_page(request: Request, user: OptionalUser, next: str = "/") -> Response:
    if user is not None:
        return redirect(request, safe_next(next))
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {
            "next": safe_next(next),
            "notice": request.query_params.get("aviso"),
            "google_enabled": settings.google_login_enabled,
            "dev_login": settings.is_dev_like,
        },
    )


@router.get("/auth/google")
async def google_login(request: Request, next: str = "/") -> Response:
    client = _google()
    request.session["next"] = safe_next(next)
    redirect_uri = str(request.url_for("google_callback"))
    response: Response = await client.authorize_redirect(request, redirect_uri)
    return response


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, db: DB) -> Response:
    client = _google()
    try:
        token = await client.authorize_access_token(request)
    except OAuthError:
        return redirect(request, "/entrar?aviso=erro-google")
    info = token.get("userinfo") or {}
    if not info.get("email_verified"):
        return redirect(request, "/entrar?aviso=email-nao-verificado")
    user = await upsert_user(
        db,
        subject=info["sub"],
        email=info["email"],
        name=info.get("name") or info["email"].split("@")[0],
        avatar_url=info.get("picture"),
    )
    return await start_session(db, request, user, request.session.pop("next", "/"))


@router.post("/sair")
async def logout(request: Request, db: DB) -> Response:
    token = request.cookies.get(cookie_name())
    if token:
        await revoke_session(db, token)
        await db.commit()
    response = redirect(request, "/entrar?aviso=saiu")
    response.delete_cookie(cookie_name(), path="/", secure=cookie_secure(), httponly=True)
    return response


@dev_router.get("/auth/dev-login", response_class=HTMLResponse)
async def dev_login_page(request: Request, next: str = "/") -> Response:
    return templates.TemplateResponse(
        request, "auth/dev_login.html", {"next": safe_next(next), "plans": list(Plan)}
    )


@dev_router.post("/auth/dev-login")
async def dev_login(
    request: Request,
    db: DB,
    email: Annotated[str, Form(max_length=320)],
    plan: Annotated[Plan, Form()] = Plan.TESTER,
    name: Annotated[str, Form(max_length=200)] = "",
    next: Annotated[str, Form()] = "/",
) -> Response:
    email = email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "E-mail inválido.")
    user = await upsert_user(
        db, subject=f"dev:{email}", email=email, name=name.strip() or email.split("@")[0], plan=plan
    )
    return await start_session(db, request, user, next)
