"""Tests for wiring the MAB selector into the order lifecycle:
  * OrderService._emit_selector_feedback — enqueues the reward task.
  * OrderService._resolve_pooling_strategy — reads the platform setting.
  * workers.tasks.selector._record_feedback — applies feedback / no-ops.
  * PoolingService bandit helpers — safe random fallback when unwired.
"""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.enums.pooling import PoolingStrategy
from app.modules.orders.service import OrderService
from app.modules.pooling.service import PoolingService


def _order(*, trader_id=5):
    o = MagicMock()
    o.id = 1
    o.uuid = uuid.uuid4()
    o.trader_id = trader_id
    o.amount = Decimal("100")
    o.currency = MagicMock(value="RUB")
    return o


# ── _emit_selector_feedback ────────────────────────────────────────────


def test_emit_feedback_enqueues_task():
    order = _order(trader_id=7)
    with patch("app.workers.celery_app.celery_app") as celery:
        OrderService._emit_selector_feedback(order, "completed")
    celery.send_task.assert_called_once()
    assert celery.send_task.call_args.args[0] == "record_selector_feedback"
    kw = celery.send_task.call_args.kwargs["kwargs"]
    assert kw == {
        "selector_name": "traders",
        "order_id": str(order.uuid),
        "entity_id": "7",
        "signal": "completed",
    }


def test_emit_feedback_skips_when_no_trader():
    order = _order(trader_id=None)
    with patch("app.workers.celery_app.celery_app") as celery:
        OrderService._emit_selector_feedback(order, "failed")
    celery.send_task.assert_not_called()


def test_emit_feedback_swallows_broker_errors():
    order = _order(trader_id=7)
    celery = MagicMock()
    celery.send_task = MagicMock(side_effect=RuntimeError("broker down"))
    with patch("app.workers.celery_app.celery_app", celery):
        # Must not raise — feedback is best-effort telemetry.
        OrderService._emit_selector_feedback(order, "completed")


# ── _resolve_pooling_strategy ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_strategy_reads_setting():
    svc = OrderService(MagicMock())
    with patch("app.modules.settings.service.SettingsService") as MockSettings:
        MockSettings.return_value.get_str = AsyncMock(return_value="bandit")
        strat = await svc._resolve_pooling_strategy()
    assert strat == PoolingStrategy.BANDIT


@pytest.mark.asyncio
async def test_resolve_strategy_defaults_weighted_on_garbage():
    svc = OrderService(MagicMock())
    with patch("app.modules.settings.service.SettingsService") as MockSettings:
        MockSettings.return_value.get_str = AsyncMock(return_value="not-a-strategy")
        strat = await svc._resolve_pooling_strategy()
    assert strat == PoolingStrategy.WEIGHTED


@pytest.mark.asyncio
async def test_resolve_strategy_defaults_weighted_on_error():
    svc = OrderService(MagicMock())
    with patch("app.modules.settings.service.SettingsService") as MockSettings:
        MockSettings.return_value.get_str = AsyncMock(side_effect=RuntimeError("db down"))
        strat = await svc._resolve_pooling_strategy()
    assert strat == PoolingStrategy.WEIGHTED


# ── record_selector_feedback task ──────────────────────────────────────


@pytest.mark.asyncio
async def test_record_feedback_noop_without_registry():
    from app.workers.tasks.selector import _record_feedback
    with patch("app.modules.selector.bootstrap.get_registry", return_value=None):
        # Must not raise — selector not wired (dev / disabled).
        await _record_feedback("traders", "ord-1", "t5", "completed")


@pytest.mark.asyncio
async def test_record_feedback_applies_to_selector():
    from app.workers.tasks.selector import _record_feedback
    sel = MagicMock()
    sel.feedback = AsyncMock(return_value=True)
    registry = MagicMock()
    registry.names.return_value = ["traders"]
    registry.get.return_value = sel
    with patch("app.modules.selector.bootstrap.get_registry", return_value=registry):
        await _record_feedback("traders", "ord-9", "t3", "failed")
    sel.feedback.assert_awaited_once_with(order_id="ord-9", entity_id="t3", signal="failed")


# ── pooling bandit fallback ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bandit_pick_candidate_falls_back_when_unwired():
    svc = PoolingService(MagicMock())
    candidates = [{"id": 1, "user_id": 10}, {"id": 2, "user_id": 20}]
    with patch("app.modules.selector.bootstrap.get_registry", return_value=None):
        pick = await svc._bandit_pick_candidate(_order(), candidates)
    assert pick in candidates


@pytest.mark.asyncio
async def test_bandit_choose_trader_returns_none_when_unwired():
    svc = PoolingService(MagicMock())
    with patch("app.modules.selector.bootstrap.get_registry", return_value=None):
        chosen = await svc._bandit_choose_trader(_order(), ["10", "20"])
    assert chosen is None
