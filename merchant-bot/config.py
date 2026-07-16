from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    API_BASE_URL: str
    BOT_SECRET: str

    SENTRY_DSN: str | None = None

    TELEGRAM_PROXY: str | None = None
    STATUS_POLL_INTERVAL_SECONDS: int = 10
    REQUEST_TIMEOUT_SECONDS: int = 30

    # Inbound HTTP server — the backend pushes PDF/video proof requests to
    # ``/proof_requested`` for bot-created orders (auth = shared ``BOT_SECRET``).
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8080

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("API_BASE_URL")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("TELEGRAM_PROXY", mode="before")
    @classmethod
    def normalize_proxy(cls, value: Any) -> str | None:
        if value in (None, "", []):
            return None
        return str(value).strip()


def get_settings() -> Settings:
    return Settings()
