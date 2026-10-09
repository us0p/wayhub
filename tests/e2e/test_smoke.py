"""Responsive smoke tests (D24, D38). Run against a live server with APP_ENV=test:
pytest -m e2e --base-url http://localhost:8000
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mentor.auth.models import User
from mentor.profile.models import Experience, Profile

pytestmark = pytest.mark.e2e

VIEWPORTS: dict[str, dict[str, Any]] = {
    "mobile": {"device": "iPhone 13"},
    "tablet": {"viewport": {"width": 768, "height": 1024}},  # md breakpoint edge
    "desktop": {"viewport": {"width": 1440, "height": 900}},
}


@pytest.fixture(params=list(VIEWPORTS))
def viewport_page(
    request: pytest.FixtureRequest, browser: Browser, playwright: Any
) -> Iterator[tuple[str, Page]]:
    spec = VIEWPORTS[request.param]
    args = (
        playwright.devices[spec["device"]] if "device" in spec else {"viewport": spec["viewport"]}
    )
    context = browser.new_context(**args)
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    yield request.param, page
    context.close()
    assert errors == []  # includes CSP violations


def _seed_profile(email: str) -> None:
    """What the interview would have gathered: facts can't be added by hand (D61)."""

    async def seed() -> None:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        async with async_sessionmaker(engine)() as db:
            user = await db.scalar(select(User).where(User.email == email))
            assert user is not None
            db.add(Profile(user_id=user.id, full_name="Ana"))
            db.add(
                Experience(
                    user_id=user.id,
                    company="Acme",
                    title="Desenvolvedora backend",
                    start_date=date(2021, 3, 1),
                )
            )
            await db.commit()
        await engine.dispose()

    with ThreadPoolExecutor(1) as pool:  # the sync Playwright API owns this thread's event loop
        pool.submit(asyncio.run, seed()).result()


def _sign_up(page: Page, base_url: str) -> str:
    """Dev login (D42) + consent, as a fresh user each time. Returns the e-mail."""
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    page.goto(base_url + "/")
    expect(page).to_have_url(f"{base_url}/entrar?next=%2F")
    page.get_by_role("link", name="Entrar como usuário de teste").click()
    page.get_by_label("E-mail").fill(email)
    page.get_by_label("Nome (opcional)").fill("Ana Souza")
    page.get_by_role("button", name="Entrar").click()
    expect(page.get_by_role("heading", name="Antes de começar")).to_be_visible()
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Aceitar e continuar").click()
    expect(page.get_by_text("Olá, Ana")).to_be_visible()
    return email


def _check_layout(kind: str, page: Page) -> None:
    nav = page.get_by_role("navigation", name="Navegação principal")
    expect(nav).to_have_count(1)
    box, viewport = nav.bounding_box(), page.viewport_size
    assert box is not None and viewport is not None
    if kind == "mobile":
        assert box["y"] > viewport["height"] / 2, "mobile nav should be a bottom dock"
    else:
        assert box["y"] < 100, "desktop nav should be a top menu"
    expect(page.get_by_role("link", name="Nova vaga", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_sign_up_profile_and_logout(viewport_page: tuple[str, Page], base_url: str) -> None:
    kind, page = viewport_page

    _sign_up(page, base_url)
    _check_layout(kind, page)
    assert page.evaluate("typeof window.htmx") == "object", "htmx failed to load (SRI/CSP?)"

    page.get_by_role("link", name="Perfil").click()
    expect(page.get_by_role("heading", name="Seu perfil")).to_be_visible()
    page.get_by_role("link", name="Conta e privacidade").click()
    expect(page.get_by_text("Minutos de entrevista por voz")).to_be_visible()
    page.get_by_role("button", name="Sair").click()
    expect(page.get_by_text("Você saiu da sua conta.")).to_be_visible()


def test_edit_profile(viewport_page: tuple[str, Page], base_url: str) -> None:
    """Review/edit screen (D15, D61): edit and delete facts in place; nothing is added by hand."""
    _, page = viewport_page
    _seed_profile(_sign_up(page, base_url))
    page.get_by_role("link", name="Perfil").click()
    page.wait_for_load_state("load")  # htmx is a deferred script
    expect(page.get_by_role("heading", name="Desenvolvedora backend em Acme")).to_be_visible()
    expect(page.get_by_role("button", name="Adicionar experiência")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    page.get_by_role("button", name="Editar Desenvolvedora backend").click()
    page.get_by_label("Cargo", exact=True).fill("")
    page.get_by_label("Empresa", exact=True).fill("")
    page.get_by_role("button", name="Salvar").click()
    expect(page.get_by_text("Informe a empresa ou o cargo.")).to_be_visible()  # form stays open

    page.get_by_label("Empresa", exact=True).fill("Globex")
    page.get_by_label("Cargo", exact=True).fill("Desenvolvedora backend")
    page.get_by_label("O que você fez").fill("Criou APIs\nReduziu custos")
    page.get_by_role("button", name="Salvar").click()
    expect(page.get_by_role("heading", name="Desenvolvedora backend em Globex")).to_be_visible()
    expect(page.get_by_text("Reduziu custos")).to_be_visible()

    page.get_by_role("button", name="Editar contato e objetivo").click()
    page.get_by_label("Nome completo").fill("Ana Souza")
    page.get_by_label("Senioridade").select_option("mid")
    page.get_by_role("button", name="Salvar").click()
    expect(page.get_by_text("Pleno")).to_be_visible()

    page.reload()  # everything was saved server-side
    expect(page.get_by_role("heading", name="Desenvolvedora backend em Globex")).to_be_visible()
    expect(page.get_by_text("Ana Souza")).to_be_visible()

    # Deleting asks in our own modal (not the browser's confirm()); cancelling keeps the item.
    page.get_by_role("button", name="Excluir Desenvolvedora backend").click()
    modal = page.get_by_role("dialog")
    expect(modal).to_be_visible()
    modal.get_by_role("button", name="Cancelar").click()
    expect(modal).to_be_hidden()
    expect(page.get_by_role("heading", name="Desenvolvedora backend em Globex")).to_be_visible()

    page.get_by_role("button", name="Excluir Desenvolvedora backend").click()
    modal.get_by_role("button", name="Excluir").click()
    expect(page.get_by_role("heading", name="Desenvolvedora backend em Globex")).to_be_hidden()
    expect(page.get_by_text("Nenhuma experiência ainda.")).to_be_visible()


def test_delete_account(viewport_page: tuple[str, Page], base_url: str) -> None:
    kind, page = viewport_page
    if kind != "mobile":
        pytest.skip("flow is identical across breakpoints")

    _sign_up(page, base_url)
    page.goto(base_url + "/conta/excluir")
    page.get_by_label("Digite EXCLUIR para confirmar").fill("EXCLUIR")
    page.get_by_role("button", name="Excluir definitivamente").click()
    expect(page.get_by_text("Sua conta e todos os seus dados foram excluídos.")).to_be_visible()


def test_text_interview(viewport_page: tuple[str, Page], base_url: str) -> None:
    """Text interview against the fake LLM (AI_LLM=fake): the reply streams in over SSE."""
    kind, page = viewport_page

    _sign_up(page, base_url)
    page.get_by_role("link", name="Entrevista", exact=True).click()
    page.wait_for_load_state("load")  # htmx and its sse extension are deferred scripts
    expect(page.get_by_text("Oi Ana, sou a Mari e vou ser sua Mentora")).to_be_visible()
    _check_layout(kind, page)

    progress = page.locator("#progress-compact" if kind == "mobile" else "#progress-panel")
    expect(progress).to_be_visible()

    box = page.get_by_label("Sua resposta")
    box.fill("linha 1")
    box.press("Shift+Enter")
    assert box.input_value() == "linha 1\n"  # Shift+Enter is a new line, not a send
    box.fill("Sou desenvolvedora backend em São Paulo.")
    page.get_by_label("Sua resposta").press("Enter")  # Enter sends (Shift+Enter is a new line)

    expect(page.get_by_text("Sou desenvolvedora backend em São Paulo.")).to_be_visible()
    expect(page.get_by_text("Resposta de teste.")).to_be_visible()
    expect(page.get_by_label("Sua resposta")).to_be_enabled()  # unlocked after the reply
    expect(page.get_by_label("Sua resposta")).to_be_empty()

    page.reload()
    expect(page.get_by_text("Resposta de teste.")).to_be_visible()

    page.get_by_role("button", name="Chega por agora").click()
    expect(page.get_by_role("link", name="Continuar entrevista")).to_be_visible()
