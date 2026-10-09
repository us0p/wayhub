from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import Consent, User, UserSession
from mentor.quotas.models import QuotaKind, UsageEvent

from .helpers import login_and_consent


async def test_profile_shows_identity_and_usage(client: AsyncClient) -> None:
    await login_and_consent(client)

    page = await client.get("/conta")

    assert page.status_code == 200
    assert "ana@example.com" in page.text
    assert "Minutos de entrevista por voz" in page.text
    assert "0 / 30" in page.text  # voice shown in minutes


async def test_delete_account_requires_typed_confirmation(
    client: AsyncClient, db: AsyncSession
) -> None:
    token = await login_and_consent(client)

    response = await client.post(
        "/conta/excluir", data={"confirmacao": "sim"}, headers={"X-CSRF-Token": token}
    )

    assert response.status_code == 422
    assert await db.scalar(select(User).where(User.email == "ana@example.com")) is not None


async def test_delete_account_removes_everything(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client)
    user = await db.scalar(select(User).where(User.email == "ana@example.com"))
    assert user is not None
    user_id = user.id
    db.add(UsageEvent(user_id=user.id, kind=QuotaKind.JOB_IMPORT, amount=1))
    await db.flush()

    response = await client.post(
        "/conta/excluir", data={"confirmacao": "excluir"}, headers={"X-CSRF-Token": token}
    )

    assert response.headers["location"] == "/entrar?aviso=conta-excluida"
    assert 'mentor_session=""' in response.headers["set-cookie"]
    assert await db.scalar(select(func.count(User.id)).where(User.id == user_id)) == 0
    for model in (UserSession, Consent, UsageEvent):
        count = select(func.count()).select_from(model).where(model.user_id == user_id)
        assert await db.scalar(count) == 0, model.__name__
