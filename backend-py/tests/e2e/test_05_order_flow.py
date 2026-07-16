"""
E2E-тесты: Полный жизненный цикл заявки.

Сценарий:
1. Мерчант создаёт payin-заявку (синхронно — с ожиданием реквизита).
2. Заявка получает статус `pending` и реквизит (трейдер из группы).
3. Трейдер видит заявку в /api/v1/orders/my-active.
4. Трейдер подтверждает оплату (POST /api/v1/orders/{id}/success).
5. Заявка переходит в статус `success`.
6. Мерчант видит обновлённый статус через Merchant API.

ВАЖНО: для тестов полного цикла необходим trader_group — без него
pooling не связывает трейдера с мерчантом.
"""

import asyncio
import time

import httpx
import pytest

from tests.e2e.conftest import (
    TestMerchant, TestTrader, TestUser, rand_suffix,
    isolate_requisites, restore_requisites,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Вспомогательная функция: ждём изменения статуса заявки
# ---------------------------------------------------------------------------

async def wait_for_status(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    expected_statuses: list[str],
    timeout: float = 15.0,
    interval: float = 0.5,
) -> dict:
    """Опрашиваем Merchant API до получения нужного статуса."""
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


# ---------------------------------------------------------------------------
# Полный цикл: создание → реквизит → подтверждение трейдером → success
# ---------------------------------------------------------------------------


async def test_full_order_lifecycle_success(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """
    Полный цикл оплаты:
    1. Деактивируем все чужие реквизиты → pooling гарантированно выберет наш.
    2. Мерчант создаёт заявку (sync, ждёт реквизит).
    3. Трейдер видит заявку в активных.
    4. Трейдер подтверждает оплату.
    5. Мерчант видит статус success.
    """
    # Изолируем пул: только наш реквизит активен
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)

    try:
        external_id = f"e2e_flow_{rand_suffix()}"

        create_resp = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": 1000.0,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": external_id,
                "userId": "e2e_flow_client",
                "issue_requisite_async": False,
            },
        )
        assert create_resp.status_code == 201, f"Order creation failed: {create_resp.text}"
        order = create_resp.json()
        order_uuid = order["id"]

        assert order["amount"] == 1000.0
        assert order["currency"] == "RUB"
        assert order["internalId"] == external_id
        assert order["status"] in ("created", "pending")

        # Ждём перехода в pending
        order = await wait_for_status(
            http, merchant, order_uuid,
            ["pending", "success", "failed", "canceled"],
            timeout=10.0,
            interval=0.3,
        )
        current_status = order.get("status", "unknown")
        if current_status != "pending":
            pytest.skip(
                f"Order status is '{current_status}' (not pending) — "
                "requisite may be unavailable"
            )

        # Проверяем реквизит
        assert order.get("requisite") is not None, "Order is pending but has no requisite"
        req = order["requisite"]
        assert req.get("account_number"), "Requisite has no account number"
        assert req.get("payment_method") == "sbp"

        # Трейдер ищет заявку в активных
        trader_order = None
        active_orders: list = []
        for _ in range(10):
            trader_active_resp = await http.get(
                "/api/v1/orders/my-active",
                headers=trader.user.auth(),
            )
            assert trader_active_resp.status_code == 200
            active_orders = trader_active_resp.json()
            trader_order = next(
                (o for o in active_orders if str(o.get("uuid")) == str(order_uuid)),
                None,
            )
            if trader_order is not None:
                break
            await asyncio.sleep(0.5)

        assert trader_order is not None, (
            f"Order {order_uuid} not found in trader's active orders. "
            f"Active UUIDs: {[str(o.get('uuid')) for o in active_orders[:10]]}"
        )
        trader_order_id = trader_order["id"]

        # Запоминаем баланс трейдера ДО подтверждения
        balances_before = await http.get(
            "/api/v1/finances/my-balances", headers=trader.user.auth()
        )
        assert balances_before.status_code == 200

        # Трейдер подтверждает оплату
        success_resp = await http.post(
            f"/api/v1/orders/{trader_order_id}/success",
            headers=trader.user.auth(),
        )
        assert success_resp.status_code == 200, (
            f"Trader order completion failed: {success_resp.text}"
        )
        completed = success_resp.json()
        assert completed["status"] == "success", (
            f"Order not completed correctly: status={completed['status']}"
        )

        # Мерчант видит success через Merchant API
        final_order = await wait_for_status(
            http, merchant, order_uuid, ["success"], timeout=15.0
        )
        assert final_order.get("status") == "success", (
            f"Merchant API shows wrong status after completion: {final_order}"
        )

        # Проверяем, что баланс трейдера изменился
        balances_after = await http.get(
            "/api/v1/finances/my-balances", headers=trader.user.auth()
        )
        assert balances_after.status_code == 200
        before_map = {(b["type"], b["currency"]): float(b["amount"]) for b in balances_before.json()}
        after_map = {(b["type"], b["currency"]): float(b["amount"]) for b in balances_after.json()}
        assert before_map != after_map, (
            "Trader balances did not change after order completion"
        )
    finally:
        # Восстанавливаем чужие реквизиты
        await restore_requisites(http, admin, disabled_reqs)


# ---------------------------------------------------------------------------
# Истечение TTL: заявка → failed
# ---------------------------------------------------------------------------

@pytest.mark.slow
async def test_order_expires_to_failed(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader_group: int,
) -> None:
    """
    Создаём заявку с issue_requisite_async=True (остаётся в created).
    Ждём TTL + 2 секунды запаса — заявка должна перейти в failed/canceled.

    Тест помечен @slow: он занимает order_ttl_seconds + ~5 с.
    Для ускорения задеплой изменение schemas.py (ge=1) и выстави
    order_ttl_seconds=5 в conftest.py — тогда тест займёт ~10 с вместо ~65 с.

    Запуск только этого теста:
        pytest tests/e2e/test_05_order_flow.py::test_order_expires_to_failed -m slow
    Пропуск slow-тестов в обычном прогоне:
        pytest tests/e2e/ -m "not slow"
    """
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 100.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201, f"Order creation failed: {create_resp.text}"
    order_data = create_resp.json()
    order_uuid = order_data["id"]
    assert order_data["status"] in ("created", "pending")

    # TTL берём из ответа или берём 60 с (текущий минимум на сервере)
    ttl = order_data.get("ttlSeconds") or order_data.get("ttl_seconds") or 60
    wait_sec = int(ttl) + 2
    await asyncio.sleep(wait_sec)

    # Даём Celery-задаче ещё до 60 с на фактическое обновление статуса в БД
    expired = await wait_for_status(
        http, merchant, order_uuid,
        ["failed", "canceled"],
        timeout=60.0,
        interval=2.0,
    )
    assert expired.get("status") in ("failed", "canceled"), (
        f"Order should have expired after {wait_sec}s + 60s polling, "
        f"but got status: {expired.get('status')}"
    )


# ---------------------------------------------------------------------------
# Цикл с отменой мерчантом
# ---------------------------------------------------------------------------

async def test_order_cancel_by_merchant(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
) -> None:
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 300.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201
    order_uuid = create_resp.json()["id"]

    cancel_resp = await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "canceled"

    get_resp = await http.get(
        f"/api/merchant/v1/orders/{order_uuid}",
        headers=merchant.headers(),
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "canceled"


# ---------------------------------------------------------------------------
# Трейдер не может завершить чужую заявку
# ---------------------------------------------------------------------------

async def test_trader_cannot_complete_nonexistent_order(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.post(
        "/api/v1/orders/999999999/success",
        headers=trader.user.auth(),
    )
    assert resp.status_code in (400, 404, 422)


# ---------------------------------------------------------------------------
# Только трейдер может завершать заявки (не admin)
# ---------------------------------------------------------------------------

async def test_admin_cannot_complete_order_via_trader_endpoint(
    http: httpx.AsyncClient,
    trader: TestTrader,
    admin: TestUser,
) -> None:
    """
    /orders/{id}/success защищён require_trader — admin всегда получает 403.
    Проверяем это с любым существующим order_id из истории трейдера,
    не создавая новую pending-заявку (чтобы не конкурировать за реквизит).
    """
    # Берём первый любой заказ из истории трейдера
    orders_resp = await http.get(
        "/api/v1/orders/my?limit=5",
        headers=trader.user.auth(),
    )
    assert orders_resp.status_code == 200
    orders = orders_resp.json()["items"]

    if not orders:
        pytest.skip("No trader orders found to test admin restriction")

    order_id = orders[0]["id"]

    # Admin должен получить 403 вне зависимости от статуса заявки
    admin_resp = await http.post(
        f"/api/v1/orders/{order_id}/success",
        headers=admin.auth(),
    )
    assert admin_resp.status_code == 403, (
        f"Expected 403 for admin trying to complete trader order, got {admin_resp.status_code}: {admin_resp.text}"
    )


# ---------------------------------------------------------------------------
# Список заявок трейдера
# ---------------------------------------------------------------------------

async def test_trader_can_list_own_orders(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/orders/my", headers=trader.user.auth())
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["items"], list) and isinstance(body["total"], int)


async def test_trader_order_search_by_numeric_term_no_overflow(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    """Поиск ордеров трейдера по длинному цифровому терму — например номеру
    банковского счёта (16-20 цифр) — не должен падать.

    ``Order.id`` это INTEGER-колонка; наивное ``Order.id == int(term)``
    переполнило бы её (Postgres: "integer out of range") и завалило бы весь
    запрос. Эндпоинт обязан вернуть 200 и список.
    """
    # 20-значная строка — заведомо больше PostgreSQL int4 (2_147_483_647).
    long_numeric = "40817810099910123456"
    resp = await http.get(
        "/api/v1/orders/my",
        headers=trader.user.auth(),
        params={"id_search": long_numeric},
    )
    assert resp.status_code == 200, (
        f"trader order search by a long numeric term must not 500: "
        f"{resp.status_code} {resp.text}"
    )
    assert isinstance(resp.json()["items"], list)


async def test_trader_can_list_active_orders(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_trader_can_list_disputed_orders(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/orders/my-disputes", headers=trader.user.auth())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Список заявок мерчанта через JWT API
# ---------------------------------------------------------------------------

async def test_merchant_can_list_own_orders_via_jwt(
    http: httpx.AsyncClient, merchant_user: TestUser
) -> None:
    resp = await http.get("/api/v1/merchants/me/orders", headers=merchant_user.auth())
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["items"], list) and isinstance(body["total"], int)
    # Убеждаемся что в списке есть наши заявки
    orders = body["items"]
    if orders:
        order = orders[0]
        assert "uuid" in order or "id" in order
        assert "status" in order
        assert "amount" in order


# ---------------------------------------------------------------------------
# Callback resend
# ---------------------------------------------------------------------------

async def test_callback_resend(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 1000.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201
    order_uuid = create_resp.json()["id"]

    resend_resp = await http.post(
        f"/api/merchant/v1/callbacks/resend/order/{order_uuid}",
        headers=merchant.headers(),
    )
    assert resend_resp.status_code == 200
    data = resend_resp.json()
    assert "message" in data

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )


# ---------------------------------------------------------------------------
# Admin: debug endpoint для заявки
# ---------------------------------------------------------------------------

async def test_admin_order_debug_info(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader_group: int,
) -> None:
    """Admin может получить полную debug-информацию по заявке."""
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 500.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201
    order_uuid = create_resp.json()["id"]

    debug_resp = await http.get(
        f"/api/v1/orders/debug/{order_uuid}",
        headers=admin.auth(),
    )
    assert debug_resp.status_code == 200
    debug_data = debug_resp.json()
    assert "order" in debug_data or "id" in debug_data or "uuid" in debug_data

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )
