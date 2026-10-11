"""Reading a job posting from its link (M7, D78), with an SSRF guard: the server fetches a URL
the user typed, so it must never be made to reach our own network.

- Only http(s) on the default ports, without credentials in the URL.
- The hostname is resolved first and every address must be public (`is_global`); the request
  then goes to that checked address (Host header + TLS SNI keep the name), so a DNS answer
  that changes between the check and the connection (DNS rebinding) can't redirect it.
- Redirects are followed by hand, each hop checked again; size and time are capped.
- The main text comes from trafilatura. When it's too thin (a page built by JavaScript), a
  headless Chromium renders it, with every request it makes checked the same way.
- Anything that still yields too little text (a login wall, a captcha) asks the user to paste
  the posting or send screenshots (`NeedsManualError`).
"""

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import trafilatura

from mentor.jobs.errors import NeedsManualError

log = logging.getLogger(__name__)

MAX_BYTES = 3 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 5
MIN_PAGE_CHARS = 300  # less than this isn't a whole posting
RENDER_TIMEOUT_MS = 20_000
DEFAULT_PORTS = {"http": 80, "https": 443}
HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
# A common browser's: many job boards refuse unknown clients. The fetch is on the user's
# behalf, for a page they opened themselves.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

type Resolver = Callable[[str, int], Awaitable[list[str]]]
type Renderer = Callable[[str, Resolver], Awaitable[str | None]]


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def checked_address(url: str, resolver: Resolver = resolve) -> str:
    """The public IP address to connect to for `url`; `NeedsManualError` if it isn't safe."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or not parts.hostname or parts.username or parts.password:
        raise NeedsManualError("unsafe")
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError:
        raise NeedsManualError("unsafe") from None
    if port != DEFAULT_PORTS[scheme]:
        raise NeedsManualError("unsafe")
    try:
        addresses = await resolver(parts.hostname, port)
    except OSError:
        raise NeedsManualError("unreachable") from None
    if not addresses:
        raise NeedsManualError("unreachable")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            log.info("job link refused: resolves to a non-public address")
            raise NeedsManualError("unsafe")
    return addresses[0]


async def page_text(
    url: str,
    *,
    resolver: Resolver = resolve,
    transport: httpx.AsyncBaseTransport | None = None,
    renderer: Renderer | None = None,
) -> str:
    """The posting's text at `url`, or `NeedsManualError`."""
    html, final_url = await _download(url, resolver, transport)
    text = _main_text(html, final_url)
    if len(text) < MIN_PAGE_CHARS:
        rendered = await (renderer or render)(final_url, resolver)
        if rendered:
            text = max(text, _main_text(rendered, final_url), key=len)
    if len(text) < MIN_PAGE_CHARS:
        raise NeedsManualError("blocked")
    return text


def _main_text(html: str | bytes, url: str) -> str:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True)
    return (text or "").strip()


async def _download(
    url: str, resolver: Resolver, transport: httpx.AsyncBaseTransport | None
) -> tuple[bytes, str]:
    timeout = httpx.Timeout(TIMEOUT_SECONDS)
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        for _ in range(MAX_REDIRECTS + 1):
            address = await checked_address(url, resolver)
            parts = urlsplit(url)
            assert parts.hostname is not None
            host = f"[{address}]" if ":" in address else address
            target = urlunsplit((parts.scheme, host, parts.path or "/", parts.query, ""))
            headers = {
                "Host": parts.hostname,
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            }
            try:
                async with client.stream(
                    "GET", target, headers=headers, extensions={"sni_hostname": parts.hostname}
                ) as response:
                    if response.is_redirect and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code in (401, 403, 429):
                        raise NeedsManualError("blocked")
                    if response.status_code >= 400:
                        raise NeedsManualError("unreachable")
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith(HTML_TYPES):
                        raise NeedsManualError("not_html")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise NeedsManualError("too_big")
                    return bytes(body), url
            except httpx.HTTPError:
                raise NeedsManualError("unreachable") from None
    raise NeedsManualError("unreachable")  # too many redirects


async def render(url: str, resolver: Resolver) -> str | None:
    """The page's HTML after a headless Chromium ran its JavaScript; None if it can't."""
    try:
        from playwright.async_api import Route, async_playwright

        async def guard(route: Route) -> None:
            request = route.request
            if request.resource_type in ("image", "media", "font"):
                await route.abort()
                return
            if not request.url.startswith(("data:", "blob:")):
                try:
                    await checked_address(request.url, resolver)
                except NeedsManualError:
                    await route.abort()
                    return
            await route.continue_()

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                context = await browser.new_context(user_agent=USER_AGENT, service_workers="block")
                await context.route("**/*", guard)
                page = await context.new_page()
                await page.goto(url, wait_until="load", timeout=RENDER_TIMEOUT_MS)
                await page.wait_for_timeout(1500)  # late scripts that fill in the posting
                return await page.content()
            finally:
                await browser.close()
    except Exception as exc:  # no browser installed, a timeout, a crash: fall back to manual
        log.info("rendering the job page failed: %s", type(exc).__name__)
        return None
