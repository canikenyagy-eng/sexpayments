"""Request/response schemas for the slot mini-app API.

Field names are camelCase because they are consumed directly by the
Telegram-WebApp frontend (``frontend/MiniAppCasinoView.vue``).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CasinoConfigSchema(BaseModel):
    rtp: float
    minBet: float
    maxBet: float
    betStepMin: float
    betStepMax: float


class CasinoConfigResponse(BaseModel):
    ok: bool = True
    game: str = "telegram-slot"
    emoji: str = "\U0001F3B0"
    config: CasinoConfigSchema


class CasinoSpinRequest(BaseModel):
    betAmount: float = Field(..., gt=0)


class CasinoBalanceSchema(BaseModel):
    balanceUsdt: float
    balanceText: str
    totalBalanceUsdt: float
    frozenBalanceUsdt: float
    availableBalanceUsdt: float


class CasinoSpinResponse(BaseModel):
    ok: bool = True
    game: str = "telegram-slot"
    symbols: dict[str, str]
    isWin: bool
    multiplier: float
    betAmount: float
    payoutAmount: float
    profitAmount: float
    balance: CasinoBalanceSchema
