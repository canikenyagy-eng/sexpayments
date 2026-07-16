import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.broadcasts import BroadcastStatus
from app.modules.base.service import BaseService
from app.modules.broadcasts.models import Broadcast
from app.modules.broadcasts.repository import BroadcastRepository
from app.modules.broadcasts.schemas import BroadcastCreateRequest
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_BROADCAST_TASK = "app.workers.tasks.trader_bot.broadcast_message_to_all_traders"


class BroadcastService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repo = BroadcastRepository(session)

    async def create_broadcast(
        self, data: BroadcastCreateRequest, admin_user_id: int
    ) -> Broadcast:
        """Snapshot the recipient count, persist the broadcast, audit-log it, and
        enqueue the async send. Returns immediately — the worker does the sending."""
        chat_ids = await self.repo.recipient_chat_ids(data.audience)

        async with self.session.begin_nested():
            broadcast = await self.repo.create({
                "admin_user_id": admin_user_id,
                "text": data.text,
                "audience": data.audience,
                "status": BroadcastStatus.PENDING,
                "total_recipients": len(chat_ids),
            })
            await self.audit_log(
                action="create_broadcast",
                entity_type="broadcast",
                entity_id=broadcast.id,
                user_id=admin_user_id,
                new_values={
                    "audience": data.audience.value,
                    "total_recipients": len(chat_ids),
                    "text_length": len(data.text),
                },
            )

        _enqueue_broadcast(broadcast.id)
        return broadcast

    async def list_recent(self) -> list[Broadcast]:
        return await self.repo.list_recent()

    async def recipient_counts(self) -> dict:
        return await self.repo.recipient_counts()


def _enqueue_broadcast(broadcast_id: int) -> None:
    """Fire the send task off the request path; a broker blip must never roll
    back the created broadcast (mirrors receipts.effects._safe_send).

    ``countdown`` gives the request transaction time to COMMIT before the worker
    picks the task up — the row is created inside the request's (not-yet-committed)
    transaction, and the worker reads it on a separate connection. The same guard
    the sibling notify tasks use (enqueue_trader_notify / dispute notifications).
    Combined with ``max_retries=0`` this avoids both a lost broadcast (worker sees
    no row) and a double-send (task re-run)."""
    try:
        celery_app.send_task(_BROADCAST_TASK, args=[broadcast_id], countdown=3)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("broadcast enqueue failed (broadcast_id=%s): %s", broadcast_id, exc)
