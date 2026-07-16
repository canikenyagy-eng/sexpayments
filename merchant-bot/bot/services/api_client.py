import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import aiohttp
from pydantic import BaseModel, Field

from config import Settings

FINAL_STATUSES = {"success", "canceled", "failed", "refunded"}


class ApiClientError(Exception):
    """Transport/HTTP error from the backend with optional structured metadata.

    Attributes:
        status: HTTP status code, when available.
        code:   Machine-readable error code from `{"error": {"code": "..."}}`.
        message: Human-readable message from the backend (without HTTP prefix).
    """

    def __init__(
        self,
        text: str,
        *,
        status: Optional[int] = None,
        code: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        super().__init__(text)
        self.status = status
        self.code = code
        self.message = message


class RequisiteInfo(BaseModel):
    bank_name: str
    account_number: str
    account_holder: str
    payment_method: str
    currency: str
    payment_option_id: Optional[int] = None
    payment_option_name: Optional[str] = None


class OrderInfo(BaseModel):
    id: str
    internal_id: Optional[str] = Field(None, alias="internalId")
    merchant_name: Optional[str] = None
    amount: float
    currency: str
    status: str
    payment_url: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    requisite: Optional[RequisiteInfo] = None

    model_config = {"populate_by_name": True}

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES


class BotMerchantItem(BaseModel):
    id: int
    name: Optional[str] = None
    status: str
    currency: str


class TerminalBalance(BaseModel):
    id: int
    name: Optional[str] = None
    currency: str
    work: float
    escrow: float


class BotBalances(BaseModel):
    currency: str = "USDT"
    total_work: float
    total_escrow: float
    owner_work: float = 0.0
    owner_escrow: float = 0.0
    terminals: list[TerminalBalance] = Field(default_factory=list)


class BotActiveOrder(BaseModel):
    id: str
    internal_id: Optional[str] = None
    merchant_id: int
    status: str
    is_final: bool


class BotState(BaseModel):
    telegram_user_id: int
    active_terminal_id: Optional[int] = None
    active_terminal_name: Optional[str] = None


class TerminalMethodLimits(BaseModel):
    payment_method: str
    currency: str
    # Fiat amount that can be pushed through the pool right now — already
    # collapses daily/monthly caps via MIN per requisite + sum across pool.
    available: float
    min_amount: float
    max_amount: float
    concurrent_slots: Optional[int] = None
    requisites_count: int = 0


class TerminalLimits(BaseModel):
    id: int
    name: Optional[str] = None
    currency: str
    methods: list[TerminalMethodLimits] = Field(default_factory=list)


class BotLimits(BaseModel):
    terminals: list[TerminalLimits] = Field(default_factory=list)


@dataclass(slots=True)
class MerchantBotApiClient:
    session: aiohttp.ClientSession
    settings: Settings

    def _base_headers(self, tg_user_id: int) -> dict[str, str]:
        return {
            "X-Bot-Secret": self.settings.BOT_SECRET,
            "X-Telegram-User-Id": str(tg_user_id),
        }

    async def _decode_response(self, response: aiohttp.ClientResponse) -> Any:
        import json

        text = await response.text()
        if response.status >= 400:
            try:
                payload = json.loads(text)
            except Exception:
                raise ApiClientError(
                    f"HTTP {response.status}: {text[:500]}",
                    status=response.status,
                )

            code: Optional[str] = None
            message: Optional[str] = None

            err_obj = payload.get("error")
            if isinstance(err_obj, dict):
                code = err_obj.get("code")
                message = err_obj.get("message")

            detail = (
                message
                if message is not None
                else payload.get("detail") or payload.get("message")
            )
            if detail is None:
                raise ApiClientError(
                    f"HTTP {response.status}: {text[:500]}",
                    status=response.status,
                    code=code,
                )
            if isinstance(detail, list):
                parts = []
                for item in detail:
                    if isinstance(item, dict):
                        loc = " → ".join(str(x) for x in item.get("loc", []))
                        msg = item.get("msg", str(item))
                        parts.append(f"{loc}: {msg}" if loc else msg)
                    else:
                        parts.append(str(item))
                detail = "\n".join(parts)
            raise ApiClientError(
                f"HTTP {response.status}: {detail}",
                status=response.status,
                code=code,
                message=str(detail),
            )

        try:
            return json.loads(text)
        except Exception as exc:
            raise ApiClientError(f"Невалидный ответ API: {text[:300]}") from exc

    # ── Merchant list ──────────────────────────────────────────────────────

    async def list_merchants(self, tg_user_id: int) -> list[BotMerchantItem]:
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/merchants"
        async with self.session.get(url, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return [BotMerchantItem.model_validate(item) for item in data]

    async def get_balances(self, tg_user_id: int) -> BotBalances:
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/merchants/balances"
        async with self.session.get(url, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return BotBalances.model_validate(data)

    async def get_limits(self, tg_user_id: int) -> BotLimits:
        """Per-terminal payin capacity (daily/monthly RUB headroom, min/max
        single order, free concurrent slots). Returns an empty list of
        terminals when the TG user owns none."""
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/merchants/limits"
        async with self.session.get(url, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return BotLimits.model_validate(data)

    # ── State (active terminal) ────────────────────────────────────────────

    async def get_state(self, tg_user_id: int) -> BotState:
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/state"
        async with self.session.get(url, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return BotState.model_validate(data)

    async def set_active_terminal(self, tg_user_id: int, merchant_id: int) -> BotState:
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/state/active-terminal"
        payload = {"merchant_id": merchant_id}
        async with self.session.post(
            url, json=payload, headers=self._base_headers(tg_user_id)
        ) as resp:
            data = await self._decode_response(resp)
        return BotState.model_validate(data)

    # ── Orders ─────────────────────────────────────────────────────────────

    async def create_order(
        self,
        tg_user_id: int,
        merchant_id: int,
        *,
        amount: float,
        currency: str,
        payment_method: str,
        chat_id: int,
    ) -> OrderInfo:
        """Create a payin order. Encodes chat_id in internalId for stateless tracker recovery."""
        internal_id = f"bot_{chat_id}_{secrets.token_hex(6)}"
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/merchants/{merchant_id}/orders"
        payload = {
            "amount": amount,
            "currency": currency,
            "payment_method": payment_method,
            "internalId": internal_id,
        }
        async with self.session.post(url, json=payload, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return OrderInfo.model_validate(data)

    async def get_order(self, tg_user_id: int, merchant_id: int, order_id: str) -> OrderInfo:
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/merchants/{merchant_id}/orders/{order_id}"
        async with self.session.get(url, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return OrderInfo.model_validate(data)

    async def cancel_order(self, tg_user_id: int, merchant_id: int, order_id: str) -> OrderInfo:
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/merchants/{merchant_id}/orders/{order_id}/cancel"
        async with self.session.post(url, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return OrderInfo.model_validate(data)

    async def confirm_order(
        self,
        tg_user_id: int,
        merchant_id: int,
        order_id: str,
        *,
        file_name: str,
        content: bytes,
    ) -> OrderInfo:
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/merchants/{merchant_id}/orders/{order_id}/confirm"
        form = aiohttp.FormData()
        form.add_field("attachment", content, filename=file_name, content_type="application/octet-stream")
        async with self.session.post(url, data=form, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return OrderInfo.model_validate(data)

    async def get_active_orders(self, tg_user_id: int, merchant_id: int) -> list[BotActiveOrder]:
        """Fetch non-final bot-created orders for tracker recovery on startup."""
        url = f"{self.settings.API_BASE_URL}/api/bot/v1/merchants/{merchant_id}/orders"
        async with self.session.get(url, headers=self._base_headers(tg_user_id)) as resp:
            data = await self._decode_response(resp)
        return [BotActiveOrder.model_validate(item) for item in data]