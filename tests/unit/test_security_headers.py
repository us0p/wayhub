from httpx import ASGITransport, AsyncClient


async def test_security_headers_on_html(anon_client: AsyncClient) -> None:
    response = await anon_client.get("/entrar")

    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "microphone=(self)" in response.headers["permissions-policy"]


async def test_api_docs_are_not_exposed(anon_client: AsyncClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await anon_client.get(path)).status_code == 404


async def test_external_script_has_subresource_integrity(anon_client: AsyncClient) -> None:
    html = (await anon_client.get("/entrar")).text

    assert 'src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"' in html
    assert 'integrity="sha384-' in html


async def test_templates_have_no_inline_styles_or_scripts(anon_client: AsyncClient) -> None:
    """The CSP forbids inline styles/scripts; catch them before the browser does."""
    for path in ("/entrar", "/termos", "/privacidade"):
        html = (await anon_client.get(path)).text
        assert " style=" not in html, path
        assert "<script>" not in html, path
        assert " hx-on" not in html, path


async def test_hsts_only_when_asked_for(anon_client: AsyncClient) -> None:
    """Dev/test serve plain HTTP: no HSTS there. Production sends it for its own host only."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from mentor.security import SecurityHeadersMiddleware

    assert "strict-transport-security" not in (await anon_client.get("/entrar")).headers

    app = Starlette(routes=[Route("/", lambda _: PlainTextResponse("ok"))])
    app.add_middleware(SecurityHeadersMiddleware, hsts=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hsts = (await client.get("/")).headers["strict-transport-security"]

    assert hsts == "max-age=31536000"  # no includeSubDomains: the other subdomains aren't ours


async def test_pages_ask_proxies_not_to_transform_them() -> None:
    """Cloudflare injects no scripts into a `no-transform` response (D89); static files are
    left alone so they can still be compressed, and an existing Cache-Control is kept."""
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, PlainTextResponse
    from starlette.routing import Route

    from mentor.security import SecurityHeadersMiddleware

    def stream(_: object) -> PlainTextResponse:
        return PlainTextResponse(
            "data: x\n\n", media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    app = Starlette(
        routes=[
            Route("/page", lambda _: HTMLResponse("<p>ok</p>")),
            Route("/stream", stream),
            Route("/file.css", lambda _: PlainTextResponse("p{}", media_type="text/css")),
        ]
    )
    app.add_middleware(SecurityHeadersMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page, events, css = [await client.get(p) for p in ("/page", "/stream", "/file.css")]

    assert page.headers["cache-control"] == "no-transform"
    assert events.headers["cache-control"] == "no-cache, no-transform"
    assert "cache-control" not in css.headers
