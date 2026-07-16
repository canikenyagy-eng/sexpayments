"""Tests for the admin «Рассылка» broadcast feature: recipient enumeration by
audience, broadcast creation (snapshot + enqueue + audit), and the worker's
delivered/failed accounting.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.common.enums.broadcasts import BroadcastAudience, BroadcastStatus
from app.common.enums.traders import TraderStatus
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.broadcasts.repository import BroadcastRepository
from app.modules.broadcasts.schemas import BroadcastCreateRequest
from app.modules.broadcasts.service import BroadcastService
from app.modules.traders.models import Trader
from app.modules.users.models import User


async def _mk_trader(session, *, tg, status=TraderStatus.ENABLED, is_system=False) -> User:
    u = User(
        username=f"t_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
        role=UserRole.TRADER, totp_enabled=False, is_blocked=False,
        use_shared_balance=True, is_system=is_system,
    )
    session.add(u)
    await session.flush()
    session.add(Trader(user_id=u.id, status=status, telegram_group_id=tg))
    await session.flush()
    return u


async def _mk_admin(session) -> User:
    u = User(
        username=f"adm_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
        role=UserRole.ADMIN, totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


@pytest.mark.asyncio
async def test_recipient_enumeration_by_audience(session):
    await _mk_trader(session, tg=111, status=TraderStatus.ENABLED)
    await _mk_trader(session, tg=222, status=TraderStatus.BLOCKED)
    await _mk_trader(session, tg=None)                    # no Telegram → excluded
    await _mk_trader(session, tg=999, is_system=True)     # system → excluded

    repo = BroadcastRepository(session)
    assert set(await repo.recipient_chat_ids(BroadcastAudience.ALL)) == {111, 222}
    assert set(await repo.recipient_chat_ids(BroadcastAudience.EXCEPT_BLOCKED)) == {111}
    assert await repo.recipient_counts() == {"all": 2, "except_blocked": 1}


@pytest.mark.asyncio
async def test_create_broadcast_snapshots_recipients_and_enqueues(session):
    admin = await _mk_admin(session)
    await _mk_trader(session, tg=111, status=TraderStatus.ENABLED)
    await _mk_trader(session, tg=222, status=TraderStatus.BLOCKED)

    with patch("app.modules.broadcasts.service.celery_app.send_task") as send_task:
        svc = BroadcastService(session)
        b = await svc.create_broadcast(
            BroadcastCreateRequest(text="привет 👋", audience=BroadcastAudience.EXCEPT_BLOCKED),
            admin_user_id=admin.id,
        )

    assert b.total_recipients == 1          # blocked trader excluded
    assert b.status == BroadcastStatus.PENDING
    assert b.text == "привет 👋"
    assert b.audience == BroadcastAudience.EXCEPT_BLOCKED
    send_task.assert_called_once()
    assert send_task.call_args[0][0] == "app.workers.tasks.trader_bot.broadcast_message_to_all_traders"
    assert send_task.call_args[1]["args"] == [b.id]


class _ACM:
    """Async context manager returning ``val`` (or itself) from __aenter__."""
    def __init__(self, val=None):
        self.val = val
    async def __aenter__(self):
        return self.val if self.val is not None else self
    async def __aexit__(self, *a):
        return False


def test_broadcast_task_counts_delivered_and_failed():
    """The worker sends one DM per recipient, counts delivered vs failed (one
    recipient blocks the bot), and marks the broadcast DONE. Sync test — the task
    drives its own event loop via asyncio.run."""
    from app.workers.tasks import trader_bot as tb

    broadcast = MagicMock()
    broadcast.audience = BroadcastAudience.ALL
    broadcast.text = "hi"

    session = MagicMock()
    session.begin.return_value = _ACM()
    session.get = AsyncMock(return_value=broadcast)

    ok = MagicMock(); ok.raise_for_status = MagicMock()
    bad = MagicMock(); bad.raise_for_status = MagicMock(side_effect=httpx.HTTPError("blocked"))
    client = MagicMock()
    client.post = AsyncMock(side_effect=[ok, bad])

    with patch.object(tb, "SessionLocal", return_value=_ACM(session)), \
         patch.object(tb, "get_settings", return_value=SimpleNamespace(
             TRADER_BOT_URL="http://bot", TRADER_BOT_SECRET="s")), \
         patch("app.modules.broadcasts.repository.BroadcastRepository") as Repo, \
         patch.object(tb.asyncio, "sleep", new=AsyncMock()), \
         patch.object(tb.httpx, "AsyncClient", return_value=_ACM(client)):
        Repo.return_value.recipient_chat_ids = AsyncMock(return_value=[111, 222])
        tb.broadcast_message_to_all_traders(1)

    assert broadcast.delivered == 1
    assert broadcast.failed == 1
    assert broadcast.status == BroadcastStatus.DONE
    assert client.post.await_count == 2


def test_broadcast_task_marks_failed_when_bot_unconfigured():
    """No TRADER_BOT_URL/SECRET but recipients exist → config error → FAILED
    (not a misleading DONE with all-failed counters)."""
    from app.workers.tasks import trader_bot as tb

    broadcast = MagicMock()
    broadcast.audience = BroadcastAudience.ALL
    broadcast.text = "hi"

    session = MagicMock()
    session.begin.return_value = _ACM()
    session.get = AsyncMock(return_value=broadcast)

    with patch.object(tb, "SessionLocal", return_value=_ACM(session)), \
         patch.object(tb, "get_settings", return_value=SimpleNamespace(
             TRADER_BOT_URL="", TRADER_BOT_SECRET="")), \
         patch("app.modules.broadcasts.repository.BroadcastRepository") as Repo:
        Repo.return_value.recipient_chat_ids = AsyncMock(return_value=[111, 222])
        tb.broadcast_message_to_all_traders(1)

    assert broadcast.status == BroadcastStatus.FAILED
