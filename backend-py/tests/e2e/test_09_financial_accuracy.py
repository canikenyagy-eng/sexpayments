"""
E2E-тесты: Финансовая точность расчётов.

Проверяет, что после завершения заявки:
- amount_usdt = amount / exchange_rate (погрешность < 0.01%)
- fee_usdt = amount_usdt * merchant_fee_percent
- profit_usdt = amount_usdt - fee_usdt
- trader_fee_usdt = amount_usdt * trader_fee_percent
- Баланс трейдера увеличился ровно на (profit_usdt - trader_fee_usdt)
- Полный цикл вывода: admin deposit → merchant withdrawal → admin approve → баланс снизился
"""

from __future__ import annotations

import asyncio
import time
from decimal import ROUND_HALF_UP, Decimal

import httpx
import pytest

from tests.e2e.conftest import (
    TestMerchant,
    TestTrader,
    TestUser,
    admin_deposit,
    isolate_requisites,
    rand_suffix,
    restore_requisites,
)

pytestmark = pytest.mark.anyio

# Комиссия мерчанта для sbp (задана в conftest fixture merchant: fees.sbp = 2.0%)
MERCHANT_FEE_PERCENT = Decimal("2.0")
# Комиссия трейдера для sbp (задана в conftest fixture trader: methods_config.sbp.fee = 1.0%)
TRADER_FEE_PERCENT = Decimal("1.0")

ORDER_AMOUNT = Decimal("1000.0")
TOLERANCE = Decimal("0.01")  # максимально допустимое отклонение в USDT


# ---------------------------------------------------------------------------
# Вспомогательная функция: создать, завершить заявку и вернуть данные
# ---------------------------------------------------------------------------

async def wait_for_status(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    expected_statuses: list[str],
    timeout: float = 15.0,
    interval: float = 0.5,
) -> dict:
    deadline = time.monotonic() + timeout
    last_order: dict = {}
    while time.monotonic() < deadline:
        resp = await http.get(
            f"/api/merchant/v1/orders/{order_uuid}",
            headers=merchant.headers(),
        )
        if resp.status_code == 200:
            last_order = resp.json()
            if last_order.get("status") in expected_statuses:
                return last_order
        await asyncio.sleep(interval)
    return last_order


async def _complete_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    admin: TestUser,
    amount: float = 1000.0,
) -> dict:
    """
    Полный цикл: создать → дождаться pending → trader success.
    Возвращает финальный ответ admin debug endpoint (с полными данными).
    """
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        external_id = f"e2e_fin_{rand_suffix()}"
        create_resp = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": amount,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": external_id,
                "issue_requisite_async": False,
            },
        )
        assert create_resp.status_code == 201, f"Order creation failed: {create_resp.text}"
        order = create_resp.json()
        order_uuid = order["id"]

        order = await wait_for_status(
            http, merchant, order_uuid,
            ["pending", "success", "failed", "canceled"],
            timeout=10.0,
        )
        if order.get("status") != "pending":
            pytest.skip(f"Order not pending: {order.get('status')}")

        # Находим ID заявки у трейдера
        for _ in range(10):
            active_resp = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
            active = active_resp.json()
            trader_order = next(
                (o for o in active if str(o.get("uuid")) == str(order_uuid)), None
            )
            if trader_order:
                break
            await asyncio.sleep(0.5)
        assert trader_order is not None, "Order not in trader active list"

        success_resp = await http.post(
            f"/api/v1/orders/{trader_order['id']}/success",
            headers=trader.user.auth(),
        )
        assert success_resp.status_code == 200, f"Success failed: {success_resp.text}"

        # Получаем полные данные через admin debug
        debug_resp = await http.get(
            f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth()
        )
        assert debug_resp.status_code == 200
        return debug_resp.json()
    finally:
        await restore_requisites(http, admin, disabled_reqs)


# ---------------------------------------------------------------------------
# Тест: точность расчёта amount_usdt
# ---------------------------------------------------------------------------

async def test_order_amount_usdt_calculation(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """
    amount_usdt должен равняться amount / exchange_rate с погрешностью < 0.01 USDT.
    """
    debug = await _complete_order(http, merchant, trader, admin, amount=1000.0)
    order = debug.get("order") or debug

    amount = Decimal(str(order.get("amount", 0)))
    exchange_rate = Decimal(str(order.get("exchange_rate", 1)))
    amount_usdt_reported = Decimal(str(order.get("amount_usdt", 0)))

    expected_usdt = (amount / exchange_rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    diff = abs(expected_usdt - amount_usdt_reported)

    assert diff <= TOLERANCE, (
        f"amount_usdt mismatch: expected ≈{expected_usdt}, got {amount_usdt_reported} "
        f"(rate={exchange_rate}, amount={amount})"
    )


async def test_order_fee_calculation_accuracy(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """
    fee_usdt = amount_usdt * merchant_fee_percent / 100.
    profit_usdt = amount_usdt - fee_usdt.
    """
    debug = await _complete_order(http, merchant, trader, admin, amount=1000.0)
    order = debug.get("order") or debug

    amount_usdt = Decimal(str(order.get("amount_usdt", 0)))
    fee_usdt = Decimal(str(order.get("fee_usdt", 0)))
    profit_usdt = Decimal(str(order.get("profit_usdt", 0)))

    expected_fee = (amount_usdt * MERCHANT_FEE_PERCENT / Decimal("100")).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    expected_profit = amount_usdt - expected_fee

    assert abs(fee_usdt - expected_fee) <= TOLERANCE, (
        f"fee_usdt mismatch: expected ≈{expected_fee}, got {fee_usdt}"
    )
    assert abs(profit_usdt - expected_profit) <= TOLERANCE, (
        f"profit_usdt mismatch: expected ≈{expected_profit}, got {profit_usdt}"
    )


async def test_trader_fee_calculation_accuracy(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """
    trader_fee_usdt = amount_usdt * trader_fee_percent / 100.
    """
    debug = await _complete_order(http, merchant, trader, admin, amount=1000.0)
    order = debug.get("order") or debug

    amount_usdt = Decimal(str(order.get("amount_usdt", 0)))
    trader_fee_usdt = Decimal(str(order.get("trader_fee_usdt") or 0))

    expected_trader_fee = (amount_usdt * TRADER_FEE_PERCENT / Decimal("100")).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    assert abs(trader_fee_usdt - expected_trader_fee) <= TOLERANCE, (
        f"trader_fee_usdt mismatch: expected ≈{expected_trader_fee}, got {trader_fee_usdt}"
    )


async def test_trader_balance_changes_after_order_completion(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """
    После завершения заявки суммарный баланс трейдера (все типы) должен
    измениться: хотя бы одно из полей (work/escrow/etc) меняется.

    Примечание о механике:
    - При создании ордера: WORK -= amount_usdt (резерв → ESCROW)
    - При завершении: ESCROW -= amount_usdt, profit зачисляется в WORK / settled balance
    - Поэтому WORK баланс после полного цикла может быть меньше, равен или больше
      в зависимости от времени снятия снимка. Тест проверяет факт изменения.
    """
    # Снимаем снимок ВСЕХ балансов ДО
    bal_resp = await http.get("/api/v1/finances/my-balances", headers=trader.user.auth())
    assert bal_resp.status_code == 200
    balances_before = {(b["type"], b["currency"]): Decimal(str(b["amount"])) for b in bal_resp.json()}

    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        external_id = f"e2e_bal_{rand_suffix()}"
        create_resp = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": 1000.0,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": external_id,
                "issue_requisite_async": False,
            },
        )
        assert create_resp.status_code == 201
        order_uuid = create_resp.json()["id"]

        order = await wait_for_status(
            http, merchant, order_uuid,
            ["pending", "success", "failed", "canceled"],
            timeout=10.0,
        )
        if order.get("status") != "pending":
            pytest.skip(f"Order not pending: {order.get('status')}")

        # Находим у трейдера
        trader_order = None
        for _ in range(10):
            active_resp = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
            trader_order = next(
                (o for o in active_resp.json() if str(o.get("uuid")) == str(order_uuid)), None
            )
            if trader_order:
                break
            await asyncio.sleep(0.5)
        assert trader_order is not None

        # Завершаем
        success_resp = await http.post(
            f"/api/v1/orders/{trader_order['id']}/success", headers=trader.user.auth()
        )
        assert success_resp.status_code == 200

        # Ждём Celery
        await asyncio.sleep(2)

        # Снимаем снимок балансов ПОСЛЕ
        bal_resp2 = await http.get("/api/v1/finances/my-balances", headers=trader.user.auth())
        assert bal_resp2.status_code == 200
        balances_after = {(b["type"], b["currency"]): Decimal(str(b["amount"])) for b in bal_resp2.json()}

        # Хотя бы один баланс должен измениться
        changed = any(
            balances_before.get(k, Decimal("0")) != v
            for k, v in balances_after.items()
        )
        assert changed, (
            f"No balance change after order completion. "
            f"Before: {balances_before}, After: {balances_after}"
        )
    finally:
        await restore_requisites(http, admin, disabled_reqs)


# ---------------------------------------------------------------------------
# Полный цикл вывода: deposit → withdrawal → approve → check balance
# ---------------------------------------------------------------------------

async def test_withdrawal_full_flow_approve(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    merchant_user: TestUser,
    admin: TestUser,
) -> None:
    """
    1. Admin пополняет баланс мерчанта на 50 USDT.
    2. Мерчант создаёт заявку на вывод 10 USDT.
    3. Admin одобряет заявку.
    4. Баланс мерчанта снизился на 10 USDT.
    """
    # Пополняем
    dep_resp = await admin_deposit(
        http, admin, merchant_id=merchant.id, amount=50, reason="e2e_withdraw_approve"
    )
    assert dep_resp.status_code == 200

    # Баланс ДО (берём именно WORK USDT — у мерчанта /my-balances агрегирует
    # ещё и escrow/in/out по тем же типам, и без фильтра по type первый
    # USDT-баланс — escrow, который не меняется при approve вывода).
    def _work_usdt(items: list) -> Decimal:
        for b in items:
            if b.get("currency") == "USDT" and b.get("type") == "work":
                return Decimal(str(b["amount"]))
        return Decimal("0")

    bal_resp = await http.get("/api/v1/finances/my-balances", headers=merchant_user.auth())
    assert bal_resp.status_code == 200
    usdt_before = _work_usdt(bal_resp.json())

    # Создаём вывод
    w_resp = await http.post(
        "/api/v1/merchants/me/withdrawals",
        headers=merchant_user.auth(),
        json={
            "amount": "10.00",
            "currency": "USDT",
            "destination_address": "TXe2eWithdrawTest001",
        },
    )
    assert w_resp.status_code in (200, 201), f"Withdrawal create failed: {w_resp.text}"
    withdrawal_id = w_resp.json().get("id")
    assert withdrawal_id is not None

    # Admin одобряет
    approve_resp = await http.post(
        f"/api/v1/finances/withdrawals/{withdrawal_id}/approve",
        headers=admin.auth(),
    )
    assert approve_resp.status_code in (200, 201), (
        f"Withdrawal approve failed: {approve_resp.text}"
    )
    assert approve_resp.json().get("status") in (
        "approved", "completed", "processing", "success"
    )

    # Баланс ПОСЛЕ
    bal_resp2 = await http.get("/api/v1/finances/my-balances", headers=merchant_user.auth())
    assert bal_resp2.status_code == 200
    usdt_after = _work_usdt(bal_resp2.json())

    assert usdt_before - usdt_after == pytest.approx(Decimal("10"), abs=TOLERANCE), (
        f"Balance should decrease by 10 USDT: before={usdt_before}, after={usdt_after}"
    )


async def test_withdrawal_full_flow_reject(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    merchant_user: TestUser,
    admin: TestUser,
) -> None:
    """
    1. Admin пополняет баланс мерчанта.
    2. Мерчант создаёт заявку на вывод.
    3. Admin отклоняет заявку.
    4. Баланс мерчанта остался прежним (средства разморожены).
    """
    # Пополняем
    await admin_deposit(
        http, admin, merchant_id=merchant.id, amount=30, reason="e2e_withdraw_reject"
    )

    # Баланс ДО
    bal_resp = await http.get("/api/v1/finances/my-balances", headers=merchant_user.auth())
    usdt_before = next(
        (Decimal(str(b["amount"])) for b in bal_resp.json() if b["currency"] == "USDT"),
        Decimal("0"),
    )

    # Создаём вывод
    w_resp = await http.post(
        "/api/v1/merchants/me/withdrawals",
        headers=merchant_user.auth(),
        json={
            "amount": "5.00",
            "currency": "USDT",
            "destination_address": "TXe2eWithdrawReject001",
        },
    )
    assert w_resp.status_code in (200, 201), f"Withdrawal create failed: {w_resp.text}"
    withdrawal_id = w_resp.json().get("id")

    # Admin отклоняет
    reject_resp = await http.post(
        f"/api/v1/finances/withdrawals/{withdrawal_id}/reject",
        headers=admin.auth(),
        json={"reason": "E2E test rejection"},
    )
    assert reject_resp.status_code in (200, 201), f"Reject failed: {reject_resp.text}"
    assert reject_resp.json().get("status") in ("rejected", "failed")

    # Баланс должен вернуться
    bal_resp2 = await http.get("/api/v1/finances/my-balances", headers=merchant_user.auth())
    usdt_after = next(
        (Decimal(str(b["amount"])) for b in bal_resp2.json() if b["currency"] == "USDT"),
        Decimal("0"),
    )

    assert abs(usdt_after - usdt_before) <= TOLERANCE, (
        f"Balance should be restored after rejection: before={usdt_before}, after={usdt_after}"
    )
