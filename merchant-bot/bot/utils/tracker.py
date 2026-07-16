"""
ActivePaymentTracker — in-memory tracker for orders awaiting final status.

On restart all previously tracked orders are lost from the map, but they can
be recovered via `restore_from_api`: the backend stores the chat_id inside
the order's internalId field using the format  bot_{chat_id}_{hex}.
"""

import re


_INTERNAL_ID_RE = re.compile(r"^bot_(\d+)_[0-9a-f]+$")


def parse_chat_id_from_internal_id(internal_id: str) -> int | None:
    """Extract the Telegram chat_id encoded in the order's internalId."""
    match = _INTERNAL_ID_RE.match(internal_id or "")
    return int(match.group(1)) if match else None


class TrackedOrder:
    __slots__ = ("chat_id", "merchant_id", "tg_user_id", "status")

    def __init__(self, chat_id: int, merchant_id: int, tg_user_id: int, status: str) -> None:
        self.chat_id = chat_id
        self.merchant_id = merchant_id
        self.tg_user_id = tg_user_id
        self.status = status


class ActivePaymentTracker:
    def __init__(self) -> None:
        self._tracked: dict[str, TrackedOrder] = {}

    def track(self, order_id: str, chat_id: int, merchant_id: int, tg_user_id: int, status: str) -> None:
        self._tracked[order_id] = TrackedOrder(
            chat_id=chat_id,
            merchant_id=merchant_id,
            tg_user_id=tg_user_id,
            status=status,
        )

    def update_status(self, order_id: str, status: str) -> None:
        if order_id in self._tracked:
            self._tracked[order_id].status = status

    def untrack(self, order_id: str) -> None:
        self._tracked.pop(order_id, None)

    def snapshot(self) -> list[tuple[str, TrackedOrder]]:
        return list(self._tracked.items())

    async def restore_from_api(
        self,
        api_client,
        tg_user_id: int,
        merchant_id: int,
    ) -> int:
        """
        Re-populate the tracker with active bot orders for a merchant.
        Returns the number of restored entries.
        """
        try:
            active_orders = await api_client.get_active_orders(tg_user_id, merchant_id)
        except Exception:
            return 0

        restored = 0
        for order in active_orders:
            if order.is_final:
                continue
            chat_id = parse_chat_id_from_internal_id(order.internal_id or "")
            if chat_id is None:
                continue
            self.track(
                order_id=order.id,
                chat_id=chat_id,
                merchant_id=merchant_id,
                tg_user_id=tg_user_id,
                status=order.status,
            )
            restored += 1

        return restored
