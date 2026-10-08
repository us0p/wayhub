"""Responsive smoke tests (D24, D38). Run against a live server:
pytest -m e2e --base-url http://localhost:8000
"""

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
    yield request.param, context.new_page()
    context.close()


def test_home_layout_per_breakpoint(viewport_page: tuple[str, Page], base_url: str) -> None:
    kind, page = viewport_page
    console_errors: list[str] = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

    page.goto(base_url + "/")
    page.wait_for_load_state("networkidle")

    # Exactly one main navigation is exposed at each breakpoint.
    nav = page.get_by_role("navigation", name="Navegação principal")
    expect(nav).to_have_count(1)
    box = nav.bounding_box()
    assert box is not None
    viewport = page.viewport_size
    assert viewport is not None
    if kind == "mobile":
        assert box["y"] > viewport["height"] / 2, "mobile nav should be a bottom dock"
        expect(nav.get_by_role("link", name="Nova vaga", exact=True)).to_be_visible()
    else:
        assert box["y"] < 100, "desktop nav should be a top menu"
        expect(page.get_by_role("link", name="Nova vaga", exact=True)).to_be_visible()

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate("typeof window.htmx") == "object", "htmx failed to load (SRI/CSP?)"
    assert console_errors == []  # includes CSP violations
