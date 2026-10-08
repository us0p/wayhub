"""Responsive smoke tests (D24, D38). Run against a live server with APP_ENV=test:
pytest -m e2e --base-url http://localhost:8000
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

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


def _sign_up(page: Page, base_url: str) -> None:
    """Dev login (D42) + consent, as a fresh user each time."""
    page.goto(base_url + "/")
    expect(page).to_have_url(f"{base_url}/entrar?next=%2F")
    page.get_by_role("link", name="Entrar como usuário de teste").click()
    page.get_by_label("E-mail").fill(f"e2e-{uuid.uuid4().hex[:8]}@example.com")
    page.get_by_label("Nome (opcional)").fill("Ana Souza")
    page.get_by_role("button", name="Entrar").click()
    expect(page.get_by_role("heading", name="Antes de começar")).to_be_visible()
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Aceitar e continuar").click()
    expect(page.get_by_text("Olá, Ana")).to_be_visible()


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
    expect(page.get_by_text("Minutos de entrevista por voz")).to_be_visible()
    page.get_by_role("button", name="Sair").click()
    expect(page.get_by_text("Você saiu da sua conta.")).to_be_visible()


def test_delete_account(viewport_page: tuple[str, Page], base_url: str) -> None:
    kind, page = viewport_page
    if kind != "mobile":
        pytest.skip("flow is identical across breakpoints")

    _sign_up(page, base_url)
    page.goto(base_url + "/conta/excluir")
    page.get_by_label("Digite EXCLUIR para confirmar").fill("EXCLUIR")
    page.get_by_role("button", name="Excluir definitivamente").click()
    expect(page.get_by_text("Sua conta e todos os seus dados foram excluídos.")).to_be_visible()
