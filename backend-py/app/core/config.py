from functools import lru_cache
from pydantic import PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    DEBUG: bool = True

    DATABASE_URL: str
    REDIS_URL: RedisDsn

    SECRET_KEY: SecretStr
    ACCESS_TOKEN_EXPIRE_MIN: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    CHECKOUT_BASE_URL: str = "https://checkout.primepay.local"

    PROJECT_BASE_URL: str = ""

    TRADER_AUTO_DISABLE_PAYIN_MINUTES: int = 15
    
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    SENTRY_PROFILES_SAMPLE_RATE: float = 0.0
    SENTRY_RELEASE: str = ""
    
    UPLOAD_DIR: str = "uploads/receipts"
    MAX_RECEIPT_SIZE_MB: int = 10
    # Dispute-evidence files (uploaded or downloaded from a merchant-supplied
    # link). Separate knob so video can be tuned independently of receipts.
    MAX_EVIDENCE_SIZE_MB: int = 10
    EVIDENCE_DOWNLOAD_TIMEOUT_SECONDS: float = 5.0

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"

    # Secret shared with the Telegram merchant bot
    MERCHANT_BOT_SECRET: str = ""

    # Trader bot internal URL and secret
    TRADER_BOT_URL: str = ""
    TRADER_BOT_SECRET: str = ""

    # Support-bot (receipt premoderation) internal URL and secret.
    # When either is empty the moderation worker task short-circuits with
    # a warning instead of raising — feature degrades gracefully.
    SUPPORT_BOT_URL: str = ""
    SUPPORT_BOT_SECRET: str = ""

    # Merchant-notify-bot (PDF/Video re-upload requests) internal URL and secret.
    # Same graceful-degradation policy as the support-bot pair.
    MERCHANT_NOTIFY_BOT_URL: str = ""
    MERCHANT_NOTIFY_BOT_SECRET: str = ""

    # merchant-dispute-bot: a polling bot that reads inbound receipts from a
    # merchant-bound Telegram chat and POSTs them here. Only the shared secret
    # is needed (the bot calls us, we never call the bot — no URL). Empty =
    # the intake endpoint rejects everything (bot effectively disabled).
    MERCHANT_DISPUTE_BOT_SECRET: str = ""

    # merchant-bot (order-creation bot): the backend pushes proof-request
    # notifications (with an "attach proof" button) here for BOT-created orders.
    # Same graceful-degradation policy — empty URL/secret ⇒ push skipped (the API
    # webhook + merchant-notify-bot still carry the request where applicable).
    MERCHANT_BOT_URL: str = ""
    MERCHANT_BOT_SECRET: str = ""

    # TronGrid — TRON HTTP API used to verify TRC20 (USDT) deposits by tx hash
    # (admin "top-up by hash"). Empty key ⇒ keyless free-tier (low rate limits).
    TRONGRID_BASE_URL: str = "https://api.trongrid.io"
    TRONGRID_API_KEY: str = ""
    # USDT TRC20 contract (mainnet default) — a deposit must transfer THIS token.
    USDT_TRC20_CONTRACT: str = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

    # ClickHouse — high-volume analytics/audit store (e.g. provider request logs).
    # Disabled by default so dev/tests don't require a CH instance; enable in prod.
    CLICKHOUSE_ENABLED: bool = False
    CLICKHOUSE_HOST: str = "localhost"
    CLICKHOUSE_PORT: int = 8123
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = ""
    CLICKHOUSE_DB: str = "default"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
