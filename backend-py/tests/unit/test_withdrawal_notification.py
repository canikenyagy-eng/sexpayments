"""Tests for the new-withdrawal platform notification wiring:
  * FinanceService._notify_withdrawal_created — enqueues the support-bot task.
  * support_bot._resolve_withdrawal_user — (user_id, username) of the requester.
  * SETTING_DEFS — the two new notification settings are registered.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.finance.service import FinanceService


# ── _notify_withdrawal_created (enqueue) ───────────────────────────────


def test_notify_withdrawal_created_enqueues_task():
    with patch("app.workers.celery_app.celery_app") as celery:
        FinanceService._notify_withdrawal_created(42)
    celery.send_task.assert_called_once()
    assert (
        celery.send_task.call_args.args[0]
        == "app.workers.tasks.support_bot.notify_withdrawal_request"
    )
    assert celery.send_task.call_args.kwargs["args"] == [42]


def test_notify_withdrawal_created_swallows_broker_error():
    celery = MagicMock()
    celery.send_task = MagicMock(side_effect=RuntimeError("broker down"))
    with patch("app.workers.celery_app.celery_app", celery):
        # Must not raise — the withdrawal is already committed.
        FinanceService._notify_withdrawal_created(42)


# ── _resolve_withdrawal_user ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_user_returns_id_and_username():
    """Always the USER account — never merchant id/name (the bug being fixed)."""
    from app.workers.tasks.support_bot import _resolve_withdrawal_user

    wr = MagicMock()
    wr.user_id = 7
    user = MagicMock()
    user.username = "trader_bob"
    session = MagicMock()
    session.get = AsyncMock(return_value=user)

    uid, login = await _resolve_withdrawal_user(session, wr)
    assert uid == 7
    assert login == "trader_bob"


@pytest.mark.asyncio
async def test_resolve_user_missing_user_gives_none_login():
    from app.workers.tasks.support_bot import _resolve_withdrawal_user

    wr = MagicMock()
    wr.user_id = 12
    session = MagicMock()
    session.get = AsyncMock(return_value=None)

    uid, login = await _resolve_withdrawal_user(session, wr)
    assert uid == 12
    assert login is None


@pytest.mark.asyncio
async def test_resolve_user_no_user_id():
    from app.workers.tasks.support_bot import _resolve_withdrawal_user

    wr = MagicMock()
    wr.user_id = None
    session = MagicMock()
    session.get = AsyncMock()

    uid, login = await _resolve_withdrawal_user(session, wr)
    assert uid is None
    assert login is None
    session.get.assert_not_awaited()


# ── settings registration ──────────────────────────────────────────────


def test_notification_settings_registered_with_defaults():
    from app.modules.settings.service import SETTING_DEFS

    assert SETTING_DEFS["notifications_chat_id"]["type"] == "str"
    assert SETTING_DEFS["notifications_chat_id"]["default"] == ""
    assert SETTING_DEFS["notify_withdrawal_requests"]["type"] == "bool"
    assert SETTING_DEFS["notify_withdrawal_requests"]["default"] is False


# ── _notify_withdrawal_async (task gating) ─────────────────────────────


class _FakeSession:
    def __init__(self, *, wr=None):
        self.get = AsyncMock(return_value=wr)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttpClient:
    """Captures the outbound POST and returns an OK response."""

    last_call = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, json=None, headers=None):
        _FakeHttpClient.last_call = {"url": url, "json": json, "headers": headers}
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp


def _start_patches(*, get_bool, get_str, url="http://bot", secret="sec", wr=None):
    from app.workers.tasks import support_bot as sb

    settings_obj = MagicMock(SUPPORT_BOT_URL=url, SUPPORT_BOT_SECRET=secret)
    settings_svc = MagicMock()
    settings_svc.get_bool = AsyncMock(return_value=get_bool)
    settings_svc.get_str = AsyncMock(return_value=get_str)
    patchers = [
        patch.object(sb, "SessionLocal", lambda: _FakeSession(wr=wr)),
        patch.object(sb, "get_settings", return_value=settings_obj),
        patch("app.modules.settings.service.SettingsService", return_value=settings_svc),
    ]
    for p in patchers:
        p.start()
    return patchers


def _stop(patchers):
    for p in patchers:
        p.stop()


@pytest.mark.asyncio
async def test_notify_async_disabled():
    from app.workers.tasks.support_bot import _notify_withdrawal_async
    patchers = _start_patches(get_bool=False, get_str="123")
    try:
        assert await _notify_withdrawal_async(1) == "disabled"
    finally:
        _stop(patchers)


@pytest.mark.asyncio
async def test_notify_async_no_chat():
    from app.workers.tasks.support_bot import _notify_withdrawal_async
    patchers = _start_patches(get_bool=True, get_str="")
    try:
        assert await _notify_withdrawal_async(1) == "no_chat"
    finally:
        _stop(patchers)


@pytest.mark.asyncio
async def test_notify_async_bot_unconfigured():
    from app.workers.tasks.support_bot import _notify_withdrawal_async
    patchers = _start_patches(get_bool=True, get_str="123", url="")
    try:
        assert await _notify_withdrawal_async(1) == "bot_unconfigured"
    finally:
        _stop(patchers)


@pytest.mark.asyncio
async def test_notify_async_withdrawal_not_found():
    from app.workers.tasks.support_bot import _notify_withdrawal_async
    patchers = _start_patches(get_bool=True, get_str="123", wr=None)
    try:
        assert await _notify_withdrawal_async(999) == "withdrawal_not_found"
    finally:
        _stop(patchers)


@pytest.mark.asyncio
async def test_notify_async_sends_payload():
    from app.workers.tasks import support_bot as sb

    wr = MagicMock()
    wr.id = 42
    wr.amount = 100.5
    wr.currency = MagicMock(value="USDT")
    wr.fee_amount = 1.0
    wr.destination_address = "TXxx"

    patchers = _start_patches(get_bool=True, get_str="-100500", wr=wr)
    patchers.append(patch.object(sb.httpx, "AsyncClient", _FakeHttpClient))
    patchers.append(patch.object(
        sb, "_resolve_withdrawal_user",
        AsyncMock(return_value=(7, "trader_bob")),
    ))
    # start the two appended patchers (the first three already started)
    patchers[-2].start()
    patchers[-1].start()
    try:
        assert await sb._notify_withdrawal_async(42) == "sent"
    finally:
        _stop(patchers)

    call = _FakeHttpClient.last_call
    assert call["url"].endswith("/notify_withdrawal")
    assert call["headers"]["X-Bot-Secret"] == "sec"
    assert call["json"]["chat_id"] == -100500
    assert call["json"]["withdrawal_id"] == 42
    assert call["json"]["amount"] == 100.5
    assert call["json"]["currency"] == "USDT"
    # USER id + username — not merchant id/name.
    assert call["json"]["user_id"] == 7
    assert call["json"]["user_login"] == "trader_bob"


# ── message_id capture on create (для последующего edit карточки) ───────


class _FakeHttpClientWithMsgId(_FakeHttpClient):
    """Like _FakeHttpClient but the bot replies with a real message_id body."""

    async def post(self, url, *, json=None, headers=None):
        _FakeHttpClient.last_call = {"url": url, "json": json, "headers": headers}
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = b'{"message_id": 555}'
        resp.json = MagicMock(return_value={"ok": True, "message_id": 555})
        return resp


@pytest.mark.asyncio
async def test_notify_async_stores_message_id_in_redis():
    """On create, the bot's message_id is cached so the decision can later edit
    the same card."""
    from app.workers.tasks import support_bot as sb

    wr = MagicMock()
    wr.id = 42
    wr.amount = 100.5
    wr.currency = MagicMock(value="USDT")
    wr.fee_amount = 1.0
    wr.destination_address = "TXxx"

    stored: dict = {}

    async def _fake_set(key, value, ttl):
        stored.update(key=key, value=value, ttl=ttl)

    patchers = _start_patches(get_bool=True, get_str="-100500", wr=wr)
    extra = [
        patch.object(sb.httpx, "AsyncClient", _FakeHttpClientWithMsgId),
        patch.object(sb, "_resolve_withdrawal_user", AsyncMock(return_value=(7, "bob"))),
        patch.object(sb, "_redis_set_json", _fake_set),
    ]
    for p in extra:
        p.start()
    patchers.extend(extra)
    try:
        assert await sb._notify_withdrawal_async(42) == "sent"
    finally:
        _stop(patchers)

    assert stored["key"] == "withdrawal:notify:42"
    assert stored["value"] == {"chat_id": -100500, "message_id": 555}


# ── _notify_withdrawal_decided (enqueue) ───────────────────────────────


def test_notify_withdrawal_decided_enqueues_task():
    with patch("app.workers.celery_app.celery_app") as celery:
        FinanceService._notify_withdrawal_decided(42, "approved")
    celery.send_task.assert_called_once()
    assert (
        celery.send_task.call_args.args[0]
        == "app.workers.tasks.support_bot.notify_withdrawal_decided"
    )
    assert celery.send_task.call_args.kwargs["args"] == [42, "approved"]


def test_notify_withdrawal_decided_swallows_broker_error():
    celery = MagicMock()
    celery.send_task = MagicMock(side_effect=RuntimeError("broker down"))
    with patch("app.workers.celery_app.celery_app", celery):
        # Must not raise — the decision is already committed.
        FinanceService._notify_withdrawal_decided(42, "rejected")


# ── _notify_withdrawal_decided_async ───────────────────────────────────


@pytest.mark.asyncio
async def test_notify_decided_bot_unconfigured():
    from app.workers.tasks import support_bot as sb

    with patch.object(
        sb, "get_settings",
        return_value=MagicMock(SUPPORT_BOT_URL="", SUPPORT_BOT_SECRET=""),
    ):
        assert await sb._notify_withdrawal_decided_async(1, "approved") == "bot_unconfigured"


@pytest.mark.asyncio
async def test_notify_decided_no_card_when_message_id_missing():
    """Card was never sent / its message_id expired from Redis → graceful no-op."""
    from app.workers.tasks import support_bot as sb

    with patch.object(
        sb, "get_settings",
        return_value=MagicMock(SUPPORT_BOT_URL="http://bot", SUPPORT_BOT_SECRET="sec"),
    ), patch.object(sb, "_redis_get_json", AsyncMock(return_value=None)):
        assert await sb._notify_withdrawal_decided_async(1, "approved") == "no_card"


@pytest.mark.asyncio
async def test_notify_decided_sends_payload_with_status():
    from app.workers.tasks import support_bot as sb

    wr = MagicMock()
    wr.id = 333
    wr.amount = 880.0
    wr.currency = MagicMock(value="USDT")
    wr.fee_amount = 0
    wr.destination_address = "TBLaw1sWfHrW8ViZzU7UaJBQ3vfVrd9ojT"

    patchers = [
        patch.object(
            sb, "get_settings",
            return_value=MagicMock(SUPPORT_BOT_URL="http://bot", SUPPORT_BOT_SECRET="sec"),
        ),
        patch.object(sb, "SessionLocal", lambda: _FakeSession(wr=wr)),
        patch.object(
            sb, "_redis_get_json",
            AsyncMock(return_value={"chat_id": -100500, "message_id": 555}),
        ),
        patch.object(sb, "_resolve_withdrawal_user", AsyncMock(return_value=(23, "GorrillaBTC"))),
        patch.object(sb.httpx, "AsyncClient", _FakeHttpClient),
    ]
    for p in patchers:
        p.start()
    try:
        assert await sb._notify_withdrawal_decided_async(333, "approved") == "sent"
    finally:
        _stop(patchers)

    call = _FakeHttpClient.last_call
    assert call["url"].endswith("/notify_withdrawal_decided")
    assert call["headers"]["X-Bot-Secret"] == "sec"
    assert call["json"]["status"] == "approved"
    assert call["json"]["message_id"] == 555
    assert call["json"]["chat_id"] == -100500
    assert call["json"]["withdrawal_id"] == 333
    assert call["json"]["user_login"] == "GorrillaBTC"
