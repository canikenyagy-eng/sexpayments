"""
E2E-тесты: Merchant API (/api/merchant/v1/*).

Тесты покрывают:
- Профиль мерчанта (GET /profile/me)
- Платёжные методы (GET /payments/methods)
- Платёжные провайдеры (GET /payments/options)
- Курсы обмена (GET /rates/)
- Создание заявки на ввод (POST /orders/payin)
- Получение заявки по UUID и external_id
- Отмена заявки
- Идемпотентность (повтор с тем же internalId)
- Изоляция данных между мерчантами

ВАЖНО: все тесты, создающие заявки, используют issue_requisite_async=True
чтобы не блокировать реквизит для последующих тестов полного цикла.
"""

import httpx
import pytest

from tests.e2e.conftest import TestMerchant, TestUser, rand_suffix

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# GET /api/merchant/v1/profile/me
# ---------------------------------------------------------------------------

async def test_merchant_profile_me(
    http: httpx.AsyncClient, merchant: TestMerchant, trader_group: int
) -> None:
    resp = await http.get(
        "/api/merchant/v1/profile/me",
        headers=merchant.headers(),
    )
    assert resp.status_code == 200, f"Profile request failed: {resp.text}"
    data = resp.json()
    assert "id" in data
    assert data["id"] == merchant.id
    assert data["status"] == "enabled"


async def test_merchant_profile_invalid_key(http: httpx.AsyncClient) -> None:
    resp = await http.get(
        "/api/merchant/v1/profile/me",
        headers={"X-Api-Key": "invalid_key_xyz_000"},
    )
    assert resp.status_code in (401, 403)


async def test_merchant_profile_no_key(http: httpx.AsyncClient) -> None:
    resp = await http.get("/api/merchant/v1/profile/me")
    assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# GET /api/merchant/v1/payments/methods
# ---------------------------------------------------------------------------

async def test_payment_methods(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.get(
        "/api/merchant/v1/payments/methods",
        headers=merchant.headers(),
    )
    assert resp.status_code == 200, f"Payment methods failed: {resp.text}"
    methods = resp.json()
    assert isinstance(methods, list)
    assert len(methods) > 0


# ---------------------------------------------------------------------------
# GET /api/merchant/v1/payments/options
# ---------------------------------------------------------------------------

async def test_payment_options(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.get(
        "/api/merchant/v1/payments/options",
        headers=merchant.headers(),
    )
    assert resp.status_code == 200, f"Payment options failed: {resp.text}"
    options = resp.json()
    assert isinstance(options, list)
    # logo_url is an internal asset path and must NOT be exposed on the
    # merchant API contract (restricted merchant schema).
    for opt in options:
        assert "logo_url" not in opt, f"logo_url leaked to merchant: {opt}"


# ---------------------------------------------------------------------------
# GET /api/merchant/v1/rates/
# ---------------------------------------------------------------------------

async def test_merchant_rates(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.get(
        "/api/merchant/v1/rates/",
        headers=merchant.headers(),
    )
    assert resp.status_code == 200, f"Rates request failed: {resp.text}"
    rates = resp.json()
    assert isinstance(rates, list)
    if rates:
        rate = rates[0]
        assert "currency" in rate or "fiat_currency" in rate
        assert "rate" in rate or "current_rate" in rate


# ---------------------------------------------------------------------------
# POST /api/merchant/v1/orders/payin — создание заявки
# ---------------------------------------------------------------------------

async def test_create_payin_order(
    http: httpx.AsyncClient, merchant: TestMerchant, trader_group: int
) -> None:
    """Асинхронный режим — не блокирует реквизит, заявка остаётся в created."""
    external_id = f"e2e_test_{rand_suffix()}"
    resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 1000.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": external_id,
            "userId": "e2e_client_001",
            "issue_requisite_async": True,
        },
    )
    assert resp.status_code == 201, f"Order creation failed: {resp.text}"
    order = resp.json()
    assert "id" in order
    assert order["amount"] == 1000.0
    assert order["currency"] == "RUB"
    assert order["status"] in ("created", "pending")
    assert order["internalId"] == external_id

    # Убираем заявку чтобы не засорять реквизит
    await http.post(
        f"/api/merchant/v1/orders/{order['id']}/cancel",
        headers=merchant.headers(),
    )


async def test_create_payin_order_invalid_amount(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": -100.0,
            "currency": "RUB",
            "payment_method": "sbp",
        },
    )
    assert resp.status_code in (400, 422)


async def test_create_payin_order_invalid_currency(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 1000.0,
            "currency": "XYZ",
            "payment_method": "sbp",
        },
    )
    assert resp.status_code in (400, 422)


async def test_create_payin_order_invalid_method(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 1000.0,
            "currency": "RUB",
            "payment_method": "bitcoin",
        },
    )
    assert resp.status_code in (400, 422)


async def test_payin_idempotency_same_internal_id(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    """Повтор с тем же internalId должен вернуть ошибку."""
    external_id = f"e2e_idem_{rand_suffix()}"

    first = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 500.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": external_id,
            "issue_requisite_async": True,
        },
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 500.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": external_id,
            "issue_requisite_async": True,
        },
    )
    # Сервер возвращает 422 для дубликата internalId
    assert second.status_code in (400, 409, 422), (
        f"Expected conflict on duplicate internalId, got {second.status_code}: {second.text}"
    )

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{first_id}/cancel",
        headers=merchant.headers(),
    )


# ---------------------------------------------------------------------------
# GET /api/merchant/v1/orders/{id}
# ---------------------------------------------------------------------------

async def test_get_order_by_uuid(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 750.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201
    order = create_resp.json()
    order_uuid = order["id"]

    get_resp = await http.get(
        f"/api/merchant/v1/orders/{order_uuid}",
        headers=merchant.headers(),
    )
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == order_uuid
    assert data["amount"] == 750.0

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )


async def test_get_order_not_found(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.get(
        "/api/merchant/v1/orders/00000000-0000-0000-0000-000000000000",
        headers=merchant.headers(),
    )
    assert resp.status_code in (404, 400)


# ---------------------------------------------------------------------------
# GET /api/merchant/v1/orders/external/{external_id}
# ---------------------------------------------------------------------------

async def test_get_order_by_external_id(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    external_id = f"e2e_ext_{rand_suffix()}"
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 800.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": external_id,
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201
    order_uuid = create_resp.json()["id"]

    get_resp = await http.get(
        f"/api/merchant/v1/orders/external/{external_id}",
        headers=merchant.headers(),
    )
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["internalId"] == external_id

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )


async def test_get_order_external_not_found(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.get(
        f"/api/merchant/v1/orders/external/no_such_order_{rand_suffix()}",
        headers=merchant.headers(),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/merchant/v1/orders/{id}/cancel
# ---------------------------------------------------------------------------

async def test_cancel_order_by_uuid(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 600.0,
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
    data = cancel_resp.json()
    assert data["status"] == "canceled"


async def test_cancel_order_by_external_id(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    external_id = f"e2e_cancel_{rand_suffix()}"
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 600.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": external_id,
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201

    cancel_resp = await http.post(
        f"/api/merchant/v1/orders/external/{external_id}/cancel",
        headers=merchant.headers(),
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "canceled"


async def test_cancel_already_canceled_order(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 600.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201
    order_uuid = create_resp.json()["id"]

    await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )
    second_cancel = await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )
    assert second_cancel.status_code in (400, 409, 422)


# ---------------------------------------------------------------------------
# Async режим создания заявки
# ---------------------------------------------------------------------------

async def test_create_order_async_mode(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    """При issue_requisite_async=True заявка создаётся со статусом created."""
    resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 400.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["status"] in ("created", "pending")
    if order["status"] == "created":
        assert order.get("requisite") is None

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{order['id']}/cancel",
        headers=merchant.headers(),
    )


# ---------------------------------------------------------------------------
# Проверка payment_url в заявке
# ---------------------------------------------------------------------------

async def test_order_has_payment_url(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 2000.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order.get("payment_url") is not None
    url = order["payment_url"]
    assert "http" in url.lower() or url.startswith("/")

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{order['id']}/cancel",
        headers=merchant.headers(),
    )


# ---------------------------------------------------------------------------
# Мерчант не видит заявки другого мерчанта
# ---------------------------------------------------------------------------

async def test_merchant_cannot_access_other_merchant_order(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser
) -> None:
    """Создаём другого мерчанта и убеждаемся, что первый не видит его заявок."""
    suffix = rand_suffix()
    reg = await http.post(
        "/api/v1/auth/register",
        json={"username": f"e2e_m2_{suffix}", "password": "SecurePass123", "role": "merchant"},
        headers=admin.auth(),
    )
    assert reg.status_code == 201
    user_id2 = reg.json()["id"]

    merchants_resp = await http.get(
        f"/api/v1/merchants/?search=e2e_m2_{suffix}",
        headers=admin.auth(),
    )
    merchants = merchants_resp.json()
    m2 = next((m for m in merchants if m["user_id"] == user_id2), None)
    if not m2:
        pytest.skip("Second merchant not found in list")

    await http.patch(
        f"/api/v1/merchants/{m2['id']}",
        json={"status": "enabled"},
        headers=admin.auth(),
    )
    key_resp = await http.post(
        f"/api/v1/merchants/{m2['id']}/api-key/reset",
        headers=admin.auth(),
    )
    assert key_resp.status_code == 200
    m2_api_key = key_resp.json()["api_key"]

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

    cross_resp = await http.get(
        f"/api/merchant/v1/orders/{order_uuid}",
        headers={"X-Api-Key": m2_api_key},
    )
    assert cross_resp.status_code in (403, 404)

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )
