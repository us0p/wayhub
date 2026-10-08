from httpx import AsyncClient


async def test_home_renders_mobile_shell_in_pt_br(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    html = response.text
    assert '<html lang="pt-BR">' in html
    assert 'name="viewport"' in html
    assert 'aria-label="Navegação principal"' in html
    assert 'aria-label="Nova vaga"' in html


async def test_home_marks_active_nav_item(client: AsyncClient) -> None:
    response = await client.get("/")

    assert 'aria-label="Início" aria-current="page"' in response.text
