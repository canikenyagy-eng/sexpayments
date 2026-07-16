"""Unit/integration tests for the premoderation reminder task.

Covers ``support_bot._remind_stale_premoderation_async`` — the periodic nudge
that re-pings the support chat about receipt checks left without a reaction:

  * gating: bot unconfigured / reminder disabled (minutes = 0) → no-op
  * selection: ONLY rows that are undecided, delivered (message_id set), past
    the threshold, not reminded within the interval, and on a still-active order
    are reminded; everything else is left untouched
  * anti-spam: a reminded row stamps ``reminded_at`` so it isn't re-sent within
    the interval
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.receipt_moderations import ModerationDecision
from app.common.types import utcnow
from app.modules.orders.models import Order
from app.modules.merchants.models import Merchant
from app.modules.receipts.models import ReceiptModeration
from app.modules.settings.service import SettingsService
from app.modules.users.models import User
from app.workers.tasks import support_bot as sb


# ─── fixtures helpers ──────────────────────────────────────────────────

async def _mk_merchant(session) -> Merchant:
    from app.common.enums.cascading import CascadeMode
    from app.common.enums.merchants import TerminalStatus
    from app.common.enums.users import UserRole

    owner = User(
        username="premod_owner", password="x", role=UserRole.MERCHANT,
        is_system=False, is_blocked=False,
    )
    session.add(owner)
    await session.flush()
    merchant = Merchant(
        user_id=owner.id, name="Premod M", api_key="pk", api_secret="ps",
        status=TerminalStatus.ENABLED, currency=Currency.RUB, fees={"sbp": 2.5},
        order_ttl_seconds=1800, requisite_search_timeout_ms=5000,
        cascade_mode=CascadeMode.POOLED,
    )
    session.add(merchant)
    await session.flush()
    return merchant


async def _mk_order(session, merchant, *, external_id, status=OrderStatus.PENDING) -> Order:
    order = Order(
        external_id=external_id, merchant_id=merchant.id,
        payment_method=PaymentMethod.SBP, amount=Decimal("1000"),
        currency=Currency.RUB, status=status,
    )
    session.add(order)
    await session.flush()
    return order


async def _mk_moderation(
    session, order, *, message_id, decision=None,
    created_min_ago=20, reminded_min_ago=None,
) -> ReceiptModeration:
    mod = ReceiptModeration(
        order_id=order.id, chat_id=-1001234567890, message_id=message_id,
        decision=decision,
        created_at=utcnow() - timedelta(minutes=created_min_ago),
        reminded_at=(
            utcnow() - timedelta(minutes=reminded_min_ago)
            if reminded_min_ago is not None else None
        ),
    )
    session.add(mod)
    await session.flush()
    return mod


class _SessionCtx:
    """Yield the test session to the task without closing it (fixture owns it)."""

    def __init__(self, sess):
        self._sess = sess

    async def __aenter__(self):
        return self._sess

    async def __aexit__(self, *exc):
        return False


class _CaptureClient:
    """Fake httpx.AsyncClient that records every POST and returns 200."""

    calls: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, json=None, headers=None):
        _CaptureClient.calls.append({"url": url, "json": json, "headers": headers})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp


def _bot_settings():
    return MagicMock(SUPPORT_BOT_URL="http://bot", SUPPORT_BOT_SECRET="sec")


# ─── gating ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reminder_bot_unconfigured_no_op():
    with patch.object(
        sb, "get_settings",
        return_value=MagicMock(SUPPORT_BOT_URL="", SUPPORT_BOT_SECRET=""),
    ):
        assert await sb._remind_stale_premoderation_async() == "bot_unconfigured"


@pytest.mark.asyncio
async def test_reminder_disabled_when_minutes_zero(session):
    await SettingsService(session).set("premoderation_reminder_minutes", 0)
    with (
        patch.object(sb, "get_settings", return_value=_bot_settings()),
        patch.object(sb, "SessionLocal", lambda: _SessionCtx(session)),
    ):
        assert await sb._remind_stale_premoderation_async() == "disabled"


@pytest.mark.asyncio
async def test_reminder_none_when_no_stale_rows(session):
    merchant = await _mk_merchant(session)
    await SettingsService(session).set("premoderation_reminder_minutes", 10)
    # A fresh (within-threshold) undecided check → not yet stale.
    order = await _mk_order(session, merchant, external_id="FRESH")
    await _mk_moderation(session, order, message_id=1, created_min_ago=2)

    _CaptureClient.calls = []
    with (
        patch.object(sb, "get_settings", return_value=_bot_settings()),
        patch.object(sb, "SessionLocal", lambda: _SessionCtx(session)),
        patch.object(sb.httpx, "AsyncClient", _CaptureClient),
    ):
        assert await sb._remind_stale_premoderation_async() == "none"
    assert _CaptureClient.calls == []


# ─── selection ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reminder_selects_only_stale_undecided_active(session):
    merchant = await _mk_merchant(session)
    await SettingsService(session).set("premoderation_reminder_minutes", 10)

    # (1) stale + undecided + delivered + active order → SHOULD remind.
    o1 = await _mk_order(session, merchant, external_id="STALE-1")
    m1 = await _mk_moderation(session, o1, message_id=1001, created_min_ago=20)
    # (2) reminded 2 min ago (< 10) → skip (anti-spam).
    o2 = await _mk_order(session, merchant, external_id="RECENT")
    m2 = await _mk_moderation(session, o2, message_id=1002, created_min_ago=20, reminded_min_ago=2)
    # (3) already decided → skip.
    o3 = await _mk_order(session, merchant, external_id="DECIDED")
    m3 = await _mk_moderation(session, o3, message_id=1003, created_min_ago=20,
                              decision=ModerationDecision.ACCEPT)
    # (4) never delivered to Telegram (message_id NULL) → skip.
    o4 = await _mk_order(session, merchant, external_id="NO-MSG")
    m4 = await _mk_moderation(session, o4, message_id=None, created_min_ago=20)
    # (5) owning order already terminal → skip.
    o5 = await _mk_order(session, merchant, external_id="TERMINAL", status=OrderStatus.SUCCESS)
    m5 = await _mk_moderation(session, o5, message_id=1005, created_min_ago=20)
    # (6) too fresh (within threshold) → skip.
    o6 = await _mk_order(session, merchant, external_id="FRESH")
    m6 = await _mk_moderation(session, o6, message_id=1006, created_min_ago=2)

    _CaptureClient.calls = []
    with (
        patch.object(sb, "get_settings", return_value=_bot_settings()),
        patch.object(sb, "SessionLocal", lambda: _SessionCtx(session)),
        patch.object(sb.httpx, "AsyncClient", _CaptureClient),
    ):
        result = await sb._remind_stale_premoderation_async()

    # Exactly one reminder, for the stale undecided check.
    assert result == "reminded:1"
    assert len(_CaptureClient.calls) == 1
    call = _CaptureClient.calls[0]
    assert call["url"].endswith("/remind_premoderation")
    assert call["headers"]["X-Bot-Secret"] == "sec"
    assert call["json"]["message_id"] == 1001
    assert call["json"]["chat_id"] == -1001234567890
    assert call["json"]["external_id"] == "STALE-1"
    assert call["json"]["order_uuid"] == str(o1.uuid)

    # Only m1 got stamped; the rest are untouched.
    for m in (m1, m2, m3, m4, m5, m6):
        await session.refresh(m)
    assert m1.reminded_at is not None
    assert m3.reminded_at is None
    assert m4.reminded_at is None
    assert m5.reminded_at is None
    assert m6.reminded_at is None
    # m2 keeps its (recent) reminder timestamp — only one POST went out (m1's
    # message_id above), so the recently-reminded row was not re-sent this tick.
    assert m2.reminded_at is not None


@pytest.mark.asyncio
async def test_reminder_repeats_after_interval(session):
    """A row reminded MORE than an interval ago is eligible again — recurring
    nudge until an admin clicks."""
    merchant = await _mk_merchant(session)
    await SettingsService(session).set("premoderation_reminder_minutes", 10)
    order = await _mk_order(session, merchant, external_id="REPEAT")
    # Reminded 15 min ago (> 10) → due for another nudge.
    mod = await _mk_moderation(session, order, message_id=2001,
                               created_min_ago=40, reminded_min_ago=15)

    _CaptureClient.calls = []
    with (
        patch.object(sb, "get_settings", return_value=_bot_settings()),
        patch.object(sb, "SessionLocal", lambda: _SessionCtx(session)),
        patch.object(sb.httpx, "AsyncClient", _CaptureClient),
    ):
        result = await sb._remind_stale_premoderation_async()

    # A reminder went out again (result + single POST prove the repeat); the
    # row was reminded > 1 interval ago so it re-qualified.
    assert result == "reminded:1"
    assert len(_CaptureClient.calls) == 1
    assert _CaptureClient.calls[0]["json"]["message_id"] == 2001
