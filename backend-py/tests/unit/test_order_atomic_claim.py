"""Unit-tests for ``OrderService._atomic_claim_requisite_capacity``.

Это финансово-критичный helper, защищающий от race condition при
параллельном создании ордеров на один реквизит. Проверяем:
  * FOR UPDATE-локирует строку requisite_limits (вызывает execute с
    select(RequisiteLimit).with_for_update())
  * пересчитывает pending_amount из реальных ордеров под локом
  * raise NotFoundException если daily/monthly лимит вылетает
  * raise NotFoundException если concurrent-limit вылетает
  * raise NotFoundException при lock_timeout (asyncpg LockNotAvailable)
  * raise NotFoundException если requisite_limits row пропал между
    pooling и сейчас (на случай race с архивацией)

Не тестируем happy path сквозь create_payin_order — для этого есть
test_order_service.py.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.common.enums.orders import OrderStatus
from app.core.exceptions import NotFoundException
from app.modules.orders.service import OrderService


# ───────── helpers ─────────


class _FakeLimit:
    """Минимальный stub RequisiteLimit для проверки логики claim'а."""

    def __init__(
        self,
        *,
        limit_daily: str = "100000",
        limit_monthly: str = "1000000",
        current_daily: str = "0",
        current_monthly: str = "0",
        limit_max_concurrent: int | None = None,
    ):
        self.limit_daily = Decimal(limit_daily)
        self.limit_monthly = Decimal(limit_monthly)
        self.current_daily_turnover = Decimal(current_daily)
        self.current_monthly_turnover = Decimal(current_monthly)
        self.limit_max_concurrent_orders = limit_max_concurrent


def _setup_mock_session(
    *,
    limit: _FakeLimit | None,
    pending_sum: str = "0",
    active_count: int = 0,
    lock_timeout: bool = False,
):
    """Сконструировать mock_session так, чтобы:
      * 1-й execute (SET LOCAL lock_timeout) — no-op result
      * 2-й execute (SELECT … FOR UPDATE) — возвращает ``limit`` (или поднимает LockNotAvailable)
      * 3-й execute (SUM pending) — возвращает ``pending_sum``
      * 4-й execute (COUNT active) — возвращает ``active_count``
    """
    session = MagicMock()

    # Подменяем `with_for_update()` через side_effect — порядок вызовов важен.
    call_results = []

    # 1) SET LOCAL
    set_local_result = MagicMock()
    call_results.append(set_local_result)

    # 2) SELECT … FOR UPDATE
    if lock_timeout:
        # OperationalError содержит 'lock_not_available' в строке.
        op_err = OperationalError(
            statement="SELECT…FOR UPDATE",
            params=None,
            orig=Exception("lock_not_available — 55P03"),
        )
        # asyncmock с side_effect: первый вызов — set_local OK, второй — exception.
        async def execute_side_effect(stmt, *a, **kw):
            if call_results:
                return call_results.pop(0)
            raise op_err
        session.execute = AsyncMock(side_effect=execute_side_effect)
        return session

    select_for_update_result = MagicMock()
    select_for_update_result.scalar_one_or_none = MagicMock(return_value=limit)
    call_results.append(select_for_update_result)

    # 3) SUM pending
    sum_result = MagicMock()
    sum_result.scalar_one = MagicMock(return_value=Decimal(pending_sum))
    call_results.append(sum_result)

    # 4) COUNT active — может не вызываться если limit_max_concurrent_orders=None
    count_result = MagicMock()
    count_result.scalar_one = MagicMock(return_value=active_count)
    call_results.append(count_result)

    async def execute_side_effect(stmt, *a, **kw):
        if call_results:
            return call_results.pop(0)
        # Защита от лишних вызовов — пустой результат.
        return MagicMock()

    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


def _make_service(session):
    svc = OrderService(session)
    svc.repository = MagicMock()
    return svc


# ───────── happy path ─────────


@pytest.mark.asyncio
async def test_claim_passes_when_capacity_available():
    """Лимит даёт capacity → helper тихо возвращается, никаких exception'ов."""
    limit = _FakeLimit(
        limit_daily="100000",
        current_daily="50000",
    )
    session = _setup_mock_session(limit=limit, pending_sum="10000")

    svc = _make_service(session)
    # 50000 + 10000 + 5000 = 65000 < 100000 → проходит
    await svc._atomic_claim_requisite_capacity(
        requisite_id=42, amount=Decimal("5000"),
    )


@pytest.mark.asyncio
async def test_claim_sets_lock_timeout():
    """Перед FOR UPDATE должен пройти SET LOCAL lock_timeout — это критично
    для fail-fast'а при насыщении пула."""
    limit = _FakeLimit()
    session = _setup_mock_session(limit=limit)

    svc = _make_service(session)
    await svc._atomic_claim_requisite_capacity(
        requisite_id=42, amount=Decimal("100"), lock_timeout_ms=3000,
    )

    # Первый execute — SET LOCAL с нужным значением.
    first_call_stmt = str(session.execute.await_args_list[0].args[0])
    assert "SET LOCAL lock_timeout" in first_call_stmt
    assert "3000ms" in first_call_stmt


@pytest.mark.asyncio
async def test_claim_uses_for_update_on_requisite_limits():
    """SELECT FOR UPDATE должен таргетить именно requisite_limits — без
    этого race не закрывается."""
    limit = _FakeLimit()
    session = _setup_mock_session(limit=limit)

    svc = _make_service(session)
    await svc._atomic_claim_requisite_capacity(
        requisite_id=42, amount=Decimal("100"),
    )

    # Второй execute — SELECT с FOR UPDATE на requisite_limits.
    second_stmt = str(session.execute.await_args_list[1].args[0])
    assert "requisite_limits" in second_stmt.lower()
    assert "for update" in second_stmt.lower()


# ───────── limit-exceeded scenarios ─────────


@pytest.mark.asyncio
async def test_claim_rejects_when_daily_limit_exceeded():
    """current_daily + pending + new > limit_daily → NotFoundException."""
    limit = _FakeLimit(
        limit_daily="100000",
        current_daily="90000",
    )
    session = _setup_mock_session(limit=limit, pending_sum="9000")

    svc = _make_service(session)
    # 90000 + 9000 + 5000 = 104000 > 100000 → fail
    with pytest.raises(NotFoundException, match="Daily limit"):
        await svc._atomic_claim_requisite_capacity(
            requisite_id=42, amount=Decimal("5000"),
        )


@pytest.mark.asyncio
async def test_claim_rejects_when_monthly_limit_exceeded():
    """current_monthly + pending + new > limit_monthly → NotFoundException."""
    limit = _FakeLimit(
        limit_daily="9999999",  # daily не блокирует
        limit_monthly="100000",
        current_monthly="80000",
    )
    session = _setup_mock_session(limit=limit, pending_sum="15000")

    svc = _make_service(session)
    # monthly: 80000 + 15000 + 6000 = 101000 > 100000 → fail
    with pytest.raises(NotFoundException, match="Monthly limit"):
        await svc._atomic_claim_requisite_capacity(
            requisite_id=42, amount=Decimal("6000"),
        )


@pytest.mark.asyncio
async def test_claim_rejects_when_concurrent_limit_reached():
    """active_count >= limit_max_concurrent_orders → NotFoundException."""
    limit = _FakeLimit(
        limit_max_concurrent=3,
    )
    session = _setup_mock_session(
        limit=limit, pending_sum="100", active_count=3,
    )

    svc = _make_service(session)
    with pytest.raises(NotFoundException, match="Concurrent"):
        await svc._atomic_claim_requisite_capacity(
            requisite_id=42, amount=Decimal("100"),
        )


@pytest.mark.asyncio
async def test_claim_passes_when_max_concurrent_is_null():
    """limit_max_concurrent_orders=NULL — concurrent check пропускается."""
    limit = _FakeLimit(limit_max_concurrent=None)
    session = _setup_mock_session(limit=limit, pending_sum="0")

    svc = _make_service(session)
    # Должно пройти без exception
    await svc._atomic_claim_requisite_capacity(
        requisite_id=42, amount=Decimal("100"),
    )


# ───────── error paths ─────────


@pytest.mark.asyncio
async def test_claim_raises_when_limit_row_disappeared():
    """Между pooling SELECT и нашим лoc'ом реквизит могли архивировать —
    requisite_limits row отсутствует → fail-fast, не плодим невалидные ордера."""
    session = _setup_mock_session(limit=None)

    svc = _make_service(session)
    with pytest.raises(NotFoundException, match="no longer available"):
        await svc._atomic_claim_requisite_capacity(
            requisite_id=42, amount=Decimal("100"),
        )


@pytest.mark.asyncio
async def test_claim_translates_lock_timeout_to_not_found():
    """asyncpg.exceptions.LockNotAvailableError (PG SQLSTATE 55P03) при
    lock_timeout → NotFoundException, клиент видит то же что при пустом
    пуле и может ретраить. БЕЗ этого — pool коннектов высохнет за минуту."""
    session = _setup_mock_session(limit=None, lock_timeout=True)

    svc = _make_service(session)
    with pytest.raises(NotFoundException, match="busy"):
        await svc._atomic_claim_requisite_capacity(
            requisite_id=42, amount=Decimal("100"),
        )
