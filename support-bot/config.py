from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram bot token + the secret backend must present in X-Bot-Secret
    # to call our HTTP endpoint.
    BOT_TOKEN: str
    BOT_SECRET: str

    # HTTP server (inbound from backend Celery tasks).
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8082

    # Outbound channel back to the backend (used by the moderation
    # callback handler to record the admin's decision).
    BACKEND_API_URL: str = "http://backend:8000"
    BACKEND_BOT_SECRET: str

    # Whitelisted admin chat — callback queries from other chats are
    # ignored. Sourced from the same value the backend persists in
    # PlatformSetting.support_bot_chat_id; mismatch means moderation is
    # misconfigured.
    ALLOWED_CHAT_ID: int

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
