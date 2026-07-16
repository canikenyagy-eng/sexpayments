from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    BOT_SECRET: str

    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8081

    # Outbound channel back to the backend — the "оплачено" inline button posts
    # the confirmation here. The secret is SYMMETRIC: the backend validates it as
    # TRADER_BOT_SECRET. Provided by docker-compose (= ${TRADER_BOT_SECRET});
    # required, so a missing value fails loudly instead of sending an empty one.
    BACKEND_API_URL: str = "http://backend:8000"
    BACKEND_BOT_SECRET: str

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
