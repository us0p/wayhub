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

    # AI providers (D6, D7, D45). `fake` adapters are deterministic and offline; never in prod.
    ai_llm: Literal["gemini", "fake"] = "gemini"
    ai_embedder: Literal["gemini", "fake"] = "gemini"
    ai_stt: Literal["google", "fake"] = "google"
    ai_tts: Literal["google", "fake"] = "google"

    # Gemini (D46): `api_key` = Gemini Developer API (paid tier only, so data is not used for
    # training); `vertex` = Vertex AI in GOOGLE_CLOUD_PROJECT with Application Default Credentials.
    gemini_backend: Literal["api_key", "vertex"] = "api_key"
    gemini_api_key: SecretStr | None = None
    gemini_location: str = "global"
    gemini_model: str = "gemini-3.8-flash"
    # Tried once when the main model is overloaded (D51); empty = no fallback.
    gemini_fallback_model: str | None = "gemini-3.7-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = Field(default=768, gt=0)

    # Google Cloud Speech-to-Text v2 / Text-to-Speech (D7, D46). Credentials come from
    # Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS), never from settings.
    google_cloud_project: str | None = None
    google_stt_location: str = "us"
    google_stt_model: str = "chirp_3"
    google_tts_voice: str = "pt-BR-Chirp3-HD-Kore"

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

    @property
    def uses_fake_ai(self) -> bool:
        return "fake" in (self.ai_llm, self.ai_embedder, self.ai_stt, self.ai_tts)

    @property
    def uses_gemini(self) -> bool:
        return "gemini" in (self.ai_llm, self.ai_embedder)

    def missing_ai_settings(self) -> list[str]:
        """Settings the selected AI providers need but that are not set."""
        missing = []
        if self.uses_gemini and self.gemini_backend == "api_key" and not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        needs_project = (self.uses_gemini and self.gemini_backend == "vertex") or "google" in (
            self.ai_stt,
            self.ai_tts,
        )
        if needs_project and not self.google_cloud_project:
            missing.append("GOOGLE_CLOUD_PROJECT")
        return missing

    @model_validator(mode="after")
    def _prod_requirements(self) -> Self:
        if self.app_env == "prod":
            if self.uses_fake_ai:
                raise ValueError("fake AI adapters are not allowed in production")
            missing = [
                name
                for name, ok in (
                    ("GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET", self.google_login_enabled),
                    ("PRIVACY_CONTACT_EMAIL", self.privacy_contact_email is not None),
                )
                if not ok
            ]
            missing += self.missing_ai_settings()
            if missing:
                raise ValueError(f"required in production: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
