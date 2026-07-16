"""Thin async HTTP client for the trader-bot ⇄ backend channel.

Wraps non-2xx responses in ``BackendError`` so the confirm handler can branch on
status code (400 conflict → already closed, 403 → wrong group, 404 → unknown
order) without re-parsing the response at the call site.
"""
from typing import Any, Optional

import httpx


class BackendError(Exception):
    """Wraps non-2xx responses from the backend."""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"backend returned {status}: {body}")


class BackendClient:
    def __init__(self, base_url: str, bot_secret: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Bot-Secret": bot_secret}
        self._client = httpx.AsyncClient(timeout=timeout)

    async def post_confirm(
        self,
        order_uuid: str,
        telegram_group_id: int,
        telegram_user_id: Optional[int],
        telegram_username: Optional[str],
    ) -> dict[str, Any]:
        """Confirm an order as paid ('оплачено') on behalf of the trader group."""
        url = f"{self._base_url}/api/bot/v1/trader/orders/{order_uuid}/confirm"
        body = {
            "telegram_group_id": telegram_group_id,
            "telegram_user_id": telegram_user_id,
            "telegram_username": telegram_username,
        }
        resp = await self._client.post(url, json=body, headers=self._headers)
        if resp.status_code >= 400:
            raise BackendError(resp.status_code, resp.text)
        return resp.json()

    async def post_request_proof(
        self,
        order_uuid: str,
        kind: str,
        telegram_group_id: int,
        telegram_user_id: Optional[int],
        telegram_username: Optional[str],
    ) -> dict[str, Any]:
        """Ask the merchant for stronger proof (video / pdf) — opens a
        check_suspended dispute on the backend."""
        url = f"{self._base_url}/api/bot/v1/trader/orders/{order_uuid}/request-proof"
        body = {
            "telegram_group_id": telegram_group_id,
            "kind": kind,
            "telegram_user_id": telegram_user_id,
            "telegram_username": telegram_username,
        }
        resp = await self._client.post(url, json=body, headers=self._headers)
        if resp.status_code >= 400:
            raise BackendError(resp.status_code, resp.text)
        return resp.json()

    async def get_receipt_check_providers(self, telegram_group_id: int) -> list[dict[str, Any]]:
        """Active receipt-check providers the trader may pick ({id, name, price_usdt})."""
        url = f"{self._base_url}/api/bot/v1/trader/receipt-check/providers"
        body = {"telegram_group_id": telegram_group_id}
        resp = await self._client.post(url, json=body, headers=self._headers)
        if resp.status_code >= 400:
            raise BackendError(resp.status_code, resp.text)
        return resp.json()

    async def post_receipt_check(
        self,
        order_uuid: str,
        provider_id: int,
        telegram_group_id: int,
        telegram_user_id: Optional[int],
        telegram_username: Optional[str],
    ) -> dict[str, Any]:
        """Run a manual receipt anti-fraud check via the chosen provider (charges
        the trader). The backend blocks up to the provider timeout (~90s), so use
        a long per-call timeout, not the client default."""
        url = f"{self._base_url}/api/bot/v1/trader/orders/{order_uuid}/receipt-check"
        body = {
            "telegram_group_id": telegram_group_id,
            "provider_id": provider_id,
            "telegram_user_id": telegram_user_id,
            "telegram_username": telegram_username,
        }
        resp = await self._client.post(url, json=body, headers=self._headers, timeout=100.0)
        if resp.status_code >= 400:
            raise BackendError(resp.status_code, resp.text)
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()
