from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram bot token + the secret backend must present in X-Bot-Secret
    # to call the push endpoint.
    BOT_TOKEN: str
    BOT_SECRET: str

    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8083

    # Backend base URL the bot calls for inbound commands like /limit, e.g.
    # ``http://backend:8000`` inside the compose network. The bot presents
    # ``BOT_SECRET`` as ``X-Bot-Secret`` (symmetric — same value the backend
    # uses for outbound pushes to the bot). Required — matches the shape used
    # by sibling bots (merchant-bot, merchant-dispute-bot).
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
