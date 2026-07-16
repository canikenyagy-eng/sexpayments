"""Standalone configuration for the slot mini-app service.

These keys used to live on the monorepo's ``app.core.config.Settings`` as
``MINIAPP_CASINO_*``. They were moved here when the game was carved out into
its own service — see ``../README.md``.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SlotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINIAPP_CASINO_", env_file=".env", extra="ignore")

    RTP: float = 97.0
    MIN_BET: float = 0.2
    MAX_BET: float = 100.0
    BET_STEP_MIN: float = 0.2
    BET_STEP_MAX: float = 0.5


@lru_cache
def get_settings() -> SlotSettings:
    return SlotSettings()
