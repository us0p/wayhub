"""Reading a job link (M7, D78): the SSRF guard and the page download, against a fake resolver
and transport (no network)."""

import httpx
import pytest

from mentor.jobs import fetch
from mentor.jobs.errors import NeedsManualError
from mentor.jobs.service import as_url

PUBLIC = "93.184.216.34"
ARTICLE = (
    "<html><body><article><h1>Vaga</h1><p>"
    + "Requisitos: Python e SQL. " * 30
    + "</p></article></body></html>"
)


def resolver(table: dict[str, list[str]]) -> fetch.Resolver:
    async def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError("no such host")
        return table[host]

    return resolve


async def no_render(url: str, resolve: fetch.Resolver) -> str | None:
    return None


@pytest.mark.parametrize(
    ("text", "url"),
    [
        ("https://jobs.example.com/1", "https://jobs.example.com/1"),
        ("  http://x.com/a?b=c  ", "http://x.com/a?b=c"),
        ("www.example.com/vaga", "https://www.example.com/vaga"),
        ("ftp://example.com/vaga", None),
        ("javascript:alert(1)", None),
        ("Vaga de dev em https://x.com", None),  # text around it: a pasted posting
        ("https://", None),
    ],
)
def test_a_lone_link_is_told_from_pasted_text(text: str, url: str | None) -> None:
    assert as_url(text) == url


@pytest.mark.parametrize(
    ("url", "addresses"),
    [
        ("http://localhost/", ["127.0.0.1"]),
        ("http://intranet/", ["10.0.0.5"]),
        ("http://router/", ["192.168.0.1"]),
        ("http://metadata/", ["169.254.169.254"]),  # cloud metadata endpoint
        ("http://v6/", ["::1"]),
        ("http://v6local/", ["fe80::1%eth0"]),
        ("http://cgnat/", ["100.64.0.1"]),
        ("http://mixed/", [PUBLIC, "10.0.0.5"]),  # any private answer is enough to refuse
        ("http://[::ffff:127.0.0.1]/", ["::ffff:127.0.0.1"]),
        ("https://jobs.example.com:8443/", [PUBLIC]),  # only the default ports
        ("https://user:pass@jobs.example.com/", [PUBLIC]),
        ("file:///etc/passwd", []),
    ],
)
async def test_unsafe_destinations_are_refused(url: str, addresses: list[str]) -> None:
    host = httpx.URL(url).host if url.startswith("http") else ""
    with pytest.raises(NeedsManualError) as refused:
        await fetch.checked_address(url, resolver({host: addresses}))
    assert refused.value.reason == "unsafe"


async def test_unknown_hosts_are_unreachable() -> None:
    with pytest.raises(NeedsManualError) as refused:
        await fetch.checked_address("https://nope.example/", resolver({}))
    assert refused.value.reason == "unreachable"


async def test_the_page_is_fetched_from_the_checked_address_with_its_name() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, html=ARTICLE)

    text = await fetch.page_text(
        "https://jobs.example.com/vaga/1?x=2",
        resolver=resolver({"jobs.example.com": [PUBLIC]}),
        transport=httpx.MockTransport(handler),
        renderer=no_render,
    )

    assert "Requisitos: Python e SQL." in text
    [request] = seen
    assert str(request.url) == f"https://{PUBLIC}/vaga/1?x=2"  # pinned: no second DNS lookup
    assert request.headers["host"] == "jobs.example.com"
    assert request.extensions["sni_hostname"] == "jobs.example.com"


async def test_each_redirect_is_checked_again() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})

    with pytest.raises(NeedsManualError) as refused:
        await fetch.page_text(
            "https://jobs.example.com/1",
            resolver=resolver({"jobs.example.com": [PUBLIC], "internal.example": ["10.1.2.3"]}),
            transport=httpx.MockTransport(handler),
            renderer=no_render,
        )
    assert refused.value.reason == "unsafe"


async def test_redirects_to_public_pages_are_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "short.example":
            return httpx.Response(301, headers={"location": "https://jobs.example.com/real"})
        return httpx.Response(200, html=ARTICLE)

    text = await fetch.page_text(
        "https://short.example/x",
        resolver=resolver({"short.example": [PUBLIC], "jobs.example.com": [PUBLIC]}),
        transport=httpx.MockTransport(handler),
        renderer=no_render,
    )
    assert "Python e SQL" in text


async def test_endless_redirects_give_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(NeedsManualError) as refused:
        await fetch.page_text(
            "https://jobs.example.com/1",
            resolver=resolver({"jobs.example.com": [PUBLIC]}),
            transport=httpx.MockTransport(handler),
            renderer=no_render,
        )
    assert refused.value.reason == "unreachable"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(403, html="Forbidden"), "blocked"),
        (httpx.Response(404, html="Not found"), "unreachable"),
        (
            httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
            "not_html",
        ),
        (httpx.Response(200, html="<p>" + "x" * (fetch.MAX_BYTES + 10) + "</p>"), "too_big"),
        (
            httpx.Response(200, html="<html><body><p>Entre para ver a vaga</p></body></html>"),
            "blocked",
        ),
    ],
)
async def test_pages_that_cant_be_read_ask_for_the_text(
    response: httpx.Response, reason: str
) -> None:
    with pytest.raises(NeedsManualError) as refused:
        await fetch.page_text(
            "https://jobs.example.com/1",
            resolver=resolver({"jobs.example.com": [PUBLIC]}),
            transport=httpx.MockTransport(lambda request: response),
            renderer=no_render,
        )
    assert refused.value.reason == reason


async def test_thin_pages_are_rendered_in_a_browser() -> None:
    rendered: list[str] = []

    async def render(url: str, resolve: fetch.Resolver) -> str | None:
        rendered.append(url)
        return ARTICLE

    text = await fetch.page_text(
        "https://spa.example.com/vaga",
        resolver=resolver({"spa.example.com": [PUBLIC]}),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, html="<div id=app></div>")),
        renderer=render,
    )

    assert rendered == ["https://spa.example.com/vaga"] and "Python e SQL" in text


async def test_network_errors_are_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(NeedsManualError) as refused:
        await fetch.page_text(
            "https://jobs.example.com/1",
            resolver=resolver({"jobs.example.com": [PUBLIC]}),
            transport=httpx.MockTransport(handler),
            renderer=no_render,
        )
    assert refused.value.reason == "unreachable"
