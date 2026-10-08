import hashlib
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth import routes as auth_routes
from mentor.auth.models import Consent, Plan, User, UserSession
from mentor.auth.sessions import SESSION_TTL, SLIDE_EVERY, now

from .helpers import csrf_from, login, login_and_consent


async def test_anonymous_page_redirects_to_login_with_next(client: AsyncClient) -> None:
    response = await client.get("/perfil")

    assert response.status_code == 303
    assert response.headers["location"] == "/entrar?next=%2Fperfil"


async def test_anonymous_htmx_request_gets_hx_redirect(client: AsyncClient) -> None:
    response = await client.get("/perfil", headers={"HX-Request": "true"})

    assert response.headers["hx-redirect"] == "/entrar?next=%2Fperfil"


async def test_dev_login_creates_user_and_hashed_session(
    client: AsyncClient, db: AsyncSession
) -> None:
    response = await login(client, plan="tester")

    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "mentor_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = client.cookies["mentor_session"]
    user = await db.scalar(select(User).where(User.email == "ana@example.com"))
    assert user is not None and user.plan is Plan.TESTER
    stored = await db.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert stored is not None
    assert stored.token_hash == hashlib.sha256(token.encode()).digest()  # never the raw token


async def test_login_requires_consent_before_app_pages(client: AsyncClient) -> None:
    await login(client)

    response = await client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/consentimento?next=%2F"


async def test_consent_must_be_ticked(client: AsyncClient, db: AsyncSession) -> None:
    await login(client)
    token = csrf_from((await client.get("/consentimento")).text)

    response = await client.post("/consentimento", data={}, headers={"X-CSRF-Token": token})

    assert response.status_code == 422
    user = await db.scalar(select(User).where(User.email == "ana@example.com"))
    assert user is not None
    assert await db.scalar(select(func.count(Consent.id)).where(Consent.user_id == user.id)) == 0


async def test_consent_records_versions_and_unlocks_home(
    client: AsyncClient, db: AsyncSession
) -> None:
    await login_and_consent(client)

    home = await client.get("/")

    assert home.status_code == 200
    assert "Olá, Ana" in home.text
    consent = await db.scalar(select(Consent).join(User).where(User.email == "ana@example.com"))
    assert consent is not None and consent.terms_version and consent.privacy_version


async def test_login_rejects_open_redirect(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/dev-login", data={"email": "ana@example.com", "next": "//evil.example"}
    )

    assert response.headers["location"] == "/"


async def test_post_with_session_requires_csrf_token(client: AsyncClient) -> None:
    await login_and_consent(client)

    missing = await client.post("/sair")
    wrong = await client.post("/sair", headers={"X-CSRF-Token": "nope"})

    assert missing.status_code == 403
    assert wrong.status_code == 403


async def test_cross_origin_post_is_rejected(client: AsyncClient) -> None:
    token = await login_and_consent(client)

    response = await client.post(
        "/sair", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}
    )

    assert response.status_code == 403


async def test_logout_revokes_the_session(client: AsyncClient) -> None:
    token = await login_and_consent(client)
    old_cookie = client.cookies["mentor_session"]

    response = await client.post("/sair", headers={"X-CSRF-Token": token, "HX-Request": "true"})

    assert response.headers["hx-redirect"] == "/entrar?aviso=saiu"
    client.cookies.set("mentor_session", old_cookie)
    assert (await client.get("/")).status_code == 303


async def test_expired_session_is_rejected_and_removed(
    client: AsyncClient, db: AsyncSession
) -> None:
    await login_and_consent(client)
    stored = await db.scalar(select(UserSession).join(User).where(User.email == "ana@example.com"))
    assert stored is not None
    user_id = stored.user_id
    stored.expires_at = now() - timedelta(seconds=1)
    await db.flush()

    assert (await client.get("/")).status_code == 303
    remaining = select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id)
    assert await db.scalar(remaining) == 0


async def test_session_expiry_slides_on_use(client: AsyncClient, db: AsyncSession) -> None:
    await login_and_consent(client)
    stored = await db.scalar(select(UserSession).join(User).where(User.email == "ana@example.com"))
    assert stored is not None
    stored.last_seen_at = now() - SLIDE_EVERY - timedelta(minutes=1)
    stored.expires_at = now() + timedelta(days=1)
    await db.flush()

    await client.get("/")

    await db.refresh(stored)
    assert stored.expires_at > now() + SESSION_TTL - timedelta(minutes=1)


class _FakeGoogle:
    def __init__(self, userinfo: dict[str, object]) -> None:
        self.userinfo = userinfo

    async def authorize_access_token(self, request: object) -> dict[str, object]:
        return {"userinfo": self.userinfo}


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch) -> _FakeGoogle:
    fake = _FakeGoogle({})
    monkeypatch.setattr(auth_routes, "_google", lambda: fake)
    return fake


async def test_google_callback_creates_user_from_verified_claims(
    client: AsyncClient, db: AsyncSession, fake_google: _FakeGoogle
) -> None:
    fake_google.userinfo = {
        "sub": "1234567890",
        "email": "bia@gmail.com",
        "email_verified": True,
        "name": "Bia Lima",
        "picture": "https://lh3.googleusercontent.com/a/x",
    }

    response = await client.get("/auth/google/callback")

    assert response.status_code == 303
    user = await db.scalar(select(User).where(User.google_sub == "1234567890"))
    assert user is not None and user.plan is Plan.FREE and user.name == "Bia Lima"
    assert "mentor_session" in client.cookies


async def test_google_callback_rejects_unverified_email(
    client: AsyncClient, db: AsyncSession, fake_google: _FakeGoogle
) -> None:
    fake_google.userinfo = {"sub": "1", "email": "x@example.com", "email_verified": False}

    response = await client.get("/auth/google/callback")

    assert response.headers["location"] == "/entrar?aviso=email-nao-verificado"
    assert await db.scalar(select(User).where(User.google_sub == "1")) is None
