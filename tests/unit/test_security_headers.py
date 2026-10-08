from httpx import AsyncClient


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
