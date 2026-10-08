import pytest
from pydantic import ValidationError

from mentor.settings import Settings

VALID_KEY = "x" * 32


def test_secrets_have_no_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DATABASE_URL", "SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # type: ignore[call-arg]

    missing = {e["loc"][0] for e in exc.value.errors() if e["type"] == "missing"}
    assert missing == {"database_url", "secret_key"}


def test_short_secret_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql+asyncpg://u@h/db", secret_key="short")  # type: ignore[call-arg]


def test_defaults_to_prod_and_hides_secrets_from_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url="postgresql+asyncpg://u:hunter2@h/db", secret_key=VALID_KEY
    )

    assert settings.app_env == "prod"
    assert "hunter2" not in repr(settings)
    assert VALID_KEY not in repr(settings)
