"""Unit tests for the trader-bot receipt-check entry point
``ReceiptCheckService.run_check_from_trader_group`` — it authorises the order via
the trader's Telegram group, resolves that trader, and runs a MANUAL check
charging them. The check/charge itself lives in ``run_check_for_order`` (tested
elsewhere); here we assert the orchestration + guards.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.enums.receipt_checks import ReceiptCheckTrigger
from app.core.exceptions import ConflictException, NotFoundException
from app.modules.receipt_checks.service import ReceiptCheckService

_OS = "app.modules.orders.service.OrderService"
_TS = "app.modules.traders.service.TraderService"


def _svc() -> ReceiptCheckService:
    return ReceiptCheckService(session=MagicMock())


@pytest.mark.asyncio
async def test_authorises_by_group_then_runs_manual_check():
    svc = _svc()
    order = MagicMock(trader_id=42)
    trader_user = MagicMock(id=42)
    trader = MagicMock()
    provider = MagicMock()
    check = MagicMock()

    svc.session.get = AsyncMock(return_value=trader_user)
    svc.resolve_provider_for_trader = AsyncMock(return_value=provider)
    svc.run_check_for_order = AsyncMock(return_value=check)

    with patch(_OS) as OS, patch(_TS) as TS:
        OS.return_value.get_order_for_trader_group = AsyncMock(return_value=order)
        TS.return_value.get_or_create_trader = AsyncMock(return_value=trader)
        out = await svc.run_check_from_trader_group(
            order_uuid="uuid-1", telegram_group_id=555, provider_id=7
        )

    assert out is check
    OS.return_value.get_order_for_trader_group.assert_awaited_once_with("uuid-1", 555)
    svc.resolve_provider_for_trader.assert_awaited_once_with(trader, 7)
    svc.run_check_for_order.assert_awaited_once()
    kwargs = svc.run_check_for_order.await_args.kwargs
    assert kwargs["order"] is order
    assert kwargs["trader_user"] is trader_user
    assert kwargs["trigger"] == ReceiptCheckTrigger.MANUAL
    assert kwargs["provider"] is provider


@pytest.mark.asyncio
async def test_no_active_provider_raises_conflict():
    svc = _svc()
    svc.session.get = AsyncMock(return_value=MagicMock(id=42))
    svc.resolve_provider_for_trader = AsyncMock(return_value=None)
    svc.run_check_for_order = AsyncMock()

    with patch(_OS) as OS, patch(_TS) as TS:
        OS.return_value.get_order_for_trader_group = AsyncMock(
            return_value=MagicMock(trader_id=42)
        )
        TS.return_value.get_or_create_trader = AsyncMock(return_value=MagicMock())
        with pytest.raises(ConflictException):
            await svc.run_check_from_trader_group(
                order_uuid="uuid-1", telegram_group_id=555, provider_id=None
            )
    svc.run_check_for_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_trader_raises_not_found():
    svc = _svc()
    svc.session.get = AsyncMock(return_value=None)  # trader user gone
    svc.run_check_for_order = AsyncMock()

    with patch(_OS) as OS, patch(_TS):
        OS.return_value.get_order_for_trader_group = AsyncMock(
            return_value=MagicMock(trader_id=42)
        )
        with pytest.raises(NotFoundException):
            await svc.run_check_from_trader_group(
                order_uuid="uuid-1", telegram_group_id=555, provider_id=1
            )
    svc.run_check_for_order.assert_not_awaited()
