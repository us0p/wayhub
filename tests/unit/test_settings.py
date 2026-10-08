import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from mentor.main import create_app
from mentor.settings import Settings

VALID_KEY = "x" * 32
DB = "postgresql+asyncpg://u@h/db"
PROD_ENV = {
    "APP_ENV": "prod",
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    "PRIVACY_CONTACT_EMAIL": "privacidade@example.com",
}


def test_secrets_have_no_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DATABASE_URL", "SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # type: ignore[call-arg]

    missing = {e["loc"][0] for e in exc.value.errors() if e["type"] == "missing"}
    assert missing == {"database_url", "secret_key"}


def test_short_secret_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=DB, secret_key="short")  # type: ignore[call-arg]


def test_defaults_to_prod_and_hides_secrets_from_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    for var, value in PROD_ENV.items():
        monkeypatch.setenv(var, value)
    monkeypatch.delenv("APP_ENV")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="postgresql+asyncpg://u:hunter2@h/db", secret_key=VALID_KEY
    )

    assert settings.app_env == "prod"
    assert "hunter2" not in repr(settings)
    assert VALID_KEY not in repr(settings)
    assert "client-secret" not in repr(settings)


def test_prod_requires_google_login_and_privacy_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    for var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "PRIVACY_CONTACT_EMAIL"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError, match=r"GOOGLE_CLIENT_ID.*PRIVACY_CONTACT_EMAIL"):
        Settings(_env_file=None, database_url=DB, secret_key=VALID_KEY)  # type: ignore[call-arg]


async def _status(path: str) -> int:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        return (await client.get(path)).status_code


@pytest.mark.usefixtures("fresh_settings")
async def test_dev_login_route_does_not_exist_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    for var, value in PROD_ENV.items():
        monkeypatch.setenv(var, value)

    assert await _status("/auth/dev-login") == 404
    assert await _status("/entrar") == 200


@pytest.mark.usefixtures("fresh_settings")
async def test_dev_login_route_exists_in_test_env() -> None:
    assert await _status("/auth/dev-login") == 200
