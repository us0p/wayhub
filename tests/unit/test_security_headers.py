from httpx import AsyncClient


async def test_security_headers_on_html(client: AsyncClient) -> None:
    response = await client.get("/")

    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "microphone=(self)" in response.headers["permissions-policy"]


async def test_api_docs_are_not_exposed(client: AsyncClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


async def test_external_script_has_subresource_integrity(client: AsyncClient) -> None:
    html = (await client.get("/")).text

    assert 'src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"' in html
    assert 'integrity="sha384-' in html
