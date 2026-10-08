from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment (or `.env` in development).

    No secret or credential has a default: a missing value fails at startup instead of
    silently falling back to something guessable. `app_env` defaults to `prod` so a
    forgotten variable never turns on development behavior in production.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "prod"
    database_url: SecretStr
    secret_key: SecretStr = Field(min_length=32)

    @property
    def database_dsn(self) -> str:
        url = self.database_url.get_secret_value()
        PostgresDsn(url)  # validate shape without keeping the plain value on the model
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
