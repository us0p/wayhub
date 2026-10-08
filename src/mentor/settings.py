from functools import lru_cache
from typing import Literal, Self

from pydantic import EmailStr, Field, PostgresDsn, SecretStr, model_validator
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

    # Google OIDC (D41). Optional in dev/test, where /auth/dev-login exists (D42).
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None

    # Shown in the privacy policy as the data controller's contact (LGPD Art. 41).
    privacy_contact_email: EmailStr | None = None

    @property
    def is_dev_like(self) -> bool:
        return self.app_env in ("dev", "test")

    @property
    def google_login_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def database_dsn(self) -> str:
        url = self.database_url.get_secret_value()
        PostgresDsn(url)  # validate shape without keeping the plain value on the model
        return url

    @model_validator(mode="after")
    def _prod_requirements(self) -> Self:
        if self.app_env == "prod":
            missing = [
                name
                for name, ok in (
                    ("GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET", self.google_login_enabled),
                    ("PRIVACY_CONTACT_EMAIL", self.privacy_contact_email is not None),
                )
                if not ok
            ]
            if missing:
                raise ValueError(f"required in production: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
