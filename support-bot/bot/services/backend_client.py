"""Thin async HTTP client for the support-bot ⇄ backend channel.

Wraps non-2xx responses in ``BackendError`` so the moderation handler can
branch on status code (400+conflict body → "already handled", 404 →
"unknown order", …) without each call site re-parsing the response.
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

    async def post_moderation(
        self,
        order_uuid: str,
        decision: str,
        moderator_tg_id: Optional[int],
        moderator_username: Optional[str],
        message_id: Optional[int],
    ) -> dict[str, Any]:
        url = f"{self._base_url}/api/bot/v1/support/orders/{order_uuid}/moderate"
        body = {
            "decision": decision,
            "moderator_tg_id": moderator_tg_id,
            "moderator_username": moderator_username,
            "message_id": message_id,
        }
        resp = await self._client.post(url, json=body, headers=self._headers)
        if resp.status_code >= 400:
            raise BackendError(resp.status_code, resp.text)
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()
