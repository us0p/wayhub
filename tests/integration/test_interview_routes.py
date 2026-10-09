import asyncio
import re
import uuid

import pytest
from httpx import AsyncClient
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeAI
from mentor.ai.ports import AIProviderError
from mentor.auth.models import User
from mentor.interview import routes as interview_routes
from mentor.interview.checklist import ChecklistItem
from mentor.interview.models import Interview, InterviewStatus, InterviewTurn, TurnRole
from mentor.interview.schemas import ProfilePatch, TurnPlan
from mentor.quotas import service as quotas
from mentor.quotas.models import QuotaKind, UsageEvent

from .helpers import login_and_consent


async def _start(client: AsyncClient, db: AsyncSession) -> tuple[str, User]:
    email = f"int-{uuid.uuid4().hex[:8]}@example.com"
    token = await login_and_consent(client, email)
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    page = await client.get("/entrevista")
    assert page.status_code == 200
    return token, user


async def _post(client: AsyncClient, token: str, text: str) -> tuple[int, str]:
    response = await client.post(
        "/entrevista/turnos", data={"texto": text}, headers={"X-CSRF-Token": token}
    )
    return response.status_code, response.text


DISABLED = re.compile(r"\sdisabled[\s>]")  # the attribute, not Tailwind's `disabled:` variant


def _reply_url(html: str) -> str:
    match = re.search(r'sse-connect="(/entrevista/turnos/[0-9a-f-]+/resposta)"', html)
    assert match, html
    return match.group(1)


async def test_interview_requires_login(client: AsyncClient) -> None:
    response = await client.get("/entrevista")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/entrar")


async def test_page_greets_and_loads_the_sse_extension_with_sri(
    client: AsyncClient, db: AsyncSession
) -> None:
    await login_and_consent(client, "page@example.com")

    page = await client.get("/entrevista")

    assert "Oi, Ana!" in page.text
    assert 'aria-current="page"' in page.text  # Entrevista is the active nav item
    assert re.search(r'htmx-ext-sse@[\d.]+/sse\.js"\s+integrity="sha384-', page.text)
    assert 'name="texto"' in page.text
    assert "Perfil 0%" in page.text


async def test_posting_a_turn_returns_the_bubble_and_a_streaming_placeholder(
    client: AsyncClient, db: AsyncSession
) -> None:
    token, user = await _start(client, db)

    status, html = await _post(client, token, "Sou <b>dev</b> backend")

    assert status == 200
    assert "Sou &lt;b&gt;dev&lt;/b&gt; backend" in html  # escaped
    assert 'sse-swap="chunk"' in html
    assert 'id="composer" hx-swap-oob="true"' in html
    assert DISABLED.search(html)  # composer locked until the reply arrives
    used = await db.scalar(
        select(func.sum(UsageEvent.amount)).where(
            UsageEvent.user_id == user.id, UsageEvent.kind == QuotaKind.INTERVIEW_TURN
        )
    )
    assert used == 1


async def test_reply_streams_as_events_and_ends_with_out_of_band_updates(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    token, _ = await _start(client, db)
    _, html = await _post(client, token, "Sou dev backend")
    fake_ai.chat.script("Que legal! <Onde> você trabalha?")
    fake_ai.chat.script_for(ProfilePatch, {"checklist": [{"item": "objective", "status": "done"}]})

    response = await client.get(_reply_url(html))

    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: chunk\ndata: Que " in body
    assert "&lt;Onde&gt;" in body and "<Onde>" not in body
    done = body[body.index("event: done") :]
    assert 'hx-swap-oob="outerHTML:#pending-' in done
    assert 'id="progress-panel" hx-swap-oob="true"' in done
    assert 'value="10"' in done  # 1 of 10 checklist items done
    assert 'name="texto"' in done and not DISABLED.search(done)

    page = await client.get("/entrevista")
    assert "Que legal! &lt;Onde&gt; você trabalha?" in page.text
    assert "sse-connect" not in page.text


async def test_a_second_turn_waits_for_the_pending_reply(
    client: AsyncClient, db: AsyncSession
) -> None:
    token, _ = await _start(client, db)
    await _post(client, token, "Primeira")

    status, html = await _post(client, token, "Segunda")

    assert status == 409
    assert "Aguarde a resposta anterior" in html


async def test_reloading_with_a_pending_turn_resumes_the_stream(
    client: AsyncClient, db: AsyncSession
) -> None:
    token, _ = await _start(client, db)
    await _post(client, token, "Oi")

    page = await client.get("/entrevista")

    assert 'sse-connect="/entrevista/turnos/' in page.text


async def test_a_failed_reply_offers_a_retry(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    token, _ = await _start(client, db)
    _, html = await _post(client, token, "Oi")
    fake_ai.chat.script(AIProviderError("503"))

    body = (await client.get(_reply_url(html))).text

    assert "event: done" in body
    assert "Não consegui responder agora." in body
    retry = re.search(r'hx-get="(/entrevista/turnos/[0-9a-f-]+/pendente)"', body)
    assert retry
    bubble = await client.get(retry.group(1))
    assert _reply_url(bubble.text) == _reply_url(html)


async def test_an_abandoned_turn_unlocks_the_composer(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mentor.interview import engine as engine_module

    monkeypatch.setattr(engine_module, "MAX_REPLY_ATTEMPTS", 1)
    token, _ = await _start(client, db)
    _, html = await _post(client, token, "Oi")
    fake_ai.chat.script(AIProviderError("503"))

    body = (await client.get(_reply_url(html))).text

    assert "Por favor, envie de novo." in body
    assert "Tentar de novo" not in body
    assert 'id="composer" hx-swap-oob="true"' in body and not DISABLED.search(body)
    status, _ = await _post(client, token, "Oi de novo")
    assert status == 200


async def test_turns_of_other_users_are_not_found(client: AsyncClient, db: AsyncSession) -> None:
    token, _ = await _start(client, db)
    _, html = await _post(client, token, "Oi")
    url = _reply_url(html)
    await client.post("/sair", headers={"X-CSRF-Token": token})

    await _start(client, db)  # another user

    assert (await client.get(url)).status_code == 404
    assert (await client.get(url.replace("resposta", "pendente"))).status_code == 404


async def test_turn_quota_is_enforced(client: AsyncClient, db: AsyncSession) -> None:
    token, user = await _start(client, db)
    await quotas.record(db, user, QuotaKind.INTERVIEW_TURN, 150)

    status, html = await _post(client, token, "Oi")

    assert status == 429
    assert "Limite atingido" in html
    assert (
        await db.scalar(
            select(func.count()).where(
                InterviewTurn.user_id == user.id, InterviewTurn.role == TurnRole.USER
            )
        )
        == 0
    )


async def test_empty_turn_is_ignored(client: AsyncClient, db: AsyncSession) -> None:
    token, _ = await _start(client, db)

    status, _ = await _post(client, token, "   ")

    assert status == 204


async def test_pause_returns_home_and_the_next_visit_resumes(
    client: AsyncClient, db: AsyncSession
) -> None:
    token, user = await _start(client, db)

    response = await client.post("/entrevista/pausar", headers={"X-CSRF-Token": token})

    assert response.status_code == 303
    interview = await db.scalar(select(Interview).where(Interview.user_id == user.id))
    assert interview is not None and interview.status is InterviewStatus.PAUSED
    home = await client.get("/")
    assert "Continuar entrevista" in home.text
    page = await client.get("/entrevista")
    assert "Que bom que você voltou!" in page.text


async def test_completion_shows_the_review_card_and_allows_a_follow_up(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    token, user = await _start(client, db)
    _, html = await _post(client, token, "Não tenho mais nada a acrescentar.")
    fake_ai.chat.script_for(
        TurnPlan, {"reasoning": "tudo coberto", "brief": "Agradeça.", "done": True}
    )
    fake_ai.chat.script("Obrigado, seu perfil está pronto!")
    fake_ai.chat.script_for(
        ProfilePatch,
        {"checklist": [{"item": item.value, "status": "done"} for item in ChecklistItem]},
    )

    body = (await client.get(_reply_url(html))).text

    assert "Entrevista concluída!" in body
    home = await client.get("/")
    assert "Revisar perfil" in home.text and "100%" in home.text

    response = await client.post("/entrevista/nova", headers={"X-CSRF-Token": token})
    assert response.status_code == 303
    page = await client.get("/entrevista")
    assert "O que mudou desde a nossa última conversa" in page.text
    count = await db.scalar(select(func.count()).where(Interview.user_id == user.id))
    assert count == 2


async def test_deleting_the_account_deletes_the_agent_threads(
    client: AsyncClient, db: AsyncSession, checkpointer: BaseCheckpointSaver[str]
) -> None:
    token, user = await _start(client, db)
    _, html = await _post(client, token, "Oi")
    await client.get(_reply_url(html))
    interview = await db.scalar(select(Interview).where(Interview.user_id == user.id))
    assert interview is not None
    config: RunnableConfig = {"configurable": {"thread_id": str(interview.id)}}
    assert await checkpointer.aget_tuple(config) is not None

    response = await client.post(
        "/conta/excluir", data={"confirmacao": "EXCLUIR"}, headers={"X-CSRF-Token": token}
    )

    assert response.status_code == 303
    assert await checkpointer.aget_tuple(config) is None


async def test_a_slow_reply_keeps_the_connection_alive_with_heartbeats(
    client: AsyncClient,
    db: AsyncSession,
    fake_ai: FakeAI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(interview_routes, "HEARTBEAT_SECONDS", 0.01)

    async def slow_plan(_: object) -> TurnPlan:  # e.g. retries or a fallback model
        await asyncio.sleep(0.05)
        return TurnPlan(reasoning="", brief="Pergunte algo.")

    original = fake_ai.chat.structured
    monkeypatch.setattr(
        fake_ai.chat,
        "structured",
        lambda schema, **kw: (
            RunnableLambda(slow_plan) if schema is TurnPlan else original(schema, **kw)
        ),
    )
    token, _ = await _start(client, db)
    _, html = await _post(client, token, "Oi")

    body = (await client.get(_reply_url(html))).text

    assert body.startswith(": ping\n\n")
    assert "event: done" in body
