"""Helpers shared by integration tests."""

from httpx import AsyncClient, Response


def csrf_from(html: str) -> str:
    marker = '"X-CSRF-Token": "'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


async def login(
    client: AsyncClient, email: str = "ana@example.com", plan: str = "free"
) -> Response:
    return await client.post(
        "/auth/dev-login", data={"email": email, "name": "Ana Souza", "plan": plan}
    )


async def login_and_consent(
    client: AsyncClient, email: str = "ana@example.com", plan: str = "free"
) -> str:
    """Log in, accept the legal documents and return the CSRF token for later POSTs."""
    await login(client, email, plan)
    page = await client.get("/consentimento")
    token = csrf_from(page.text)
    response = await client.post(
        "/consentimento", data={"aceito": "sim"}, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 303
    return token
