from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram bot token + the shared secret the backend checks in X-Bot-Secret
    # (must equal backend MERCHANT_DISPUTE_BOT_SECRET).
    BOT_TOKEN: str
    BOT_SECRET: str

    # Backend base URL, e.g. http://backend:8000 (inside the compose network).
    API_BASE_URL: str
    REQUEST_TIMEOUT_SECONDS: float = 30.0

    SENTRY_DSN: str | None = None
    TELEGRAM_PROXY: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("TELEGRAM_PROXY", mode="before")
    @classmethod
    def normalize_proxy(cls, value: Any) -> str | None:
        if value in (None, "", []):
            return None
        return str(value).strip()


def get_settings() -> Settings:
    return Settings()
