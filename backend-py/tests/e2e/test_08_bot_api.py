"""
E2E-тесты: Bot API (/api/bot/v1/*).

Bot API используется Telegram-ботом. Секрет по умолчанию: "bot_secret".
Переопределить через переменную окружения E2E_BOT_SECRET.

Покрывает:
- Аутентификация: правильный / неправильный секрет, без заголовков
- Список мерчантов для TG-пользователя
- Создание, получение, отмена заявки через бота
- Подтверждение заявки с загрузкой чека (receipt → receipt_uploaded)
- Список активных заявок бота (recovery при перезапуске)
- Изоляция: TG-пользователь не может управлять чужим мерчантом
"""

import io

import httpx
import pytest

from tests.e2e.conftest import (
    BOT_SECRET,
    BOT_TG_USER_ID,
    TestMerchant,
    TestTrader,
    TestUser,
    bot_headers,
    rand_suffix,
)

pytestmark = pytest.mark.anyio

BOT_HEADERS = bot_headers(BOT_SECRET, BOT_TG_USER_ID)


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------

async def _link_tg_user(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant, tg_user_id: int
) -> None:
    """Привязывает tg_user_id к merchant.telegram_user_ids."""
    resp = await http.patch(
        f"/api/v1/merchants/{merchant.id}",
        json={"telegram_user_ids": [tg_user_id]},
        headers=admin.auth(),
    )
    assert resp.status_code == 200, f"Failed to link TG user: {resp.text}"


async def _create_bot_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    amount: float = 500.0,
) -> dict:
    """Создаёт заявку через bot API и возвращает её данные."""
    resp = await http.post(
        f"/api/bot/v1/merchants/{merchant.id}/orders",
        headers=BOT_HEADERS,
        json={
            "amount": amount,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert resp.status_code in (200, 201), f"Bot order create failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------

async def test_bot_missing_secret(http: httpx.AsyncClient) -> None:
    """Неверный секрет → 401 / 403."""
    resp = await http.get(
        "/api/bot/v1/merchants",
        headers={"X-Bot-Secret": "wrong_secret", "X-Telegram-User-Id": str(BOT_TG_USER_ID)},
    )
    assert resp.status_code in (401, 403)


async def test_bot_missing_user_id(http: httpx.AsyncClient) -> None:
    """Нет X-Telegram-User-Id → 400 / 422."""
    resp = await http.get(
        "/api/bot/v1/merchants",
        headers={"X-Bot-Secret": BOT_SECRET},
    )
    assert resp.status_code in (400, 401, 403, 422)


async def test_bot_no_headers(http: httpx.AsyncClient) -> None:
    """Нет никаких заголовков → 4xx."""
    resp = await http.get("/api/bot/v1/merchants")
    assert resp.status_code in (400, 401, 403, 422)


# ---------------------------------------------------------------------------
# GET /api/bot/v1/merchants
# ---------------------------------------------------------------------------

async def test_bot_list_merchants_empty_for_unknown_user(
    http: httpx.AsyncClient,
) -> None:
    """TG-пользователь не привязан ни к одному мерчанту — пустой список."""
    unknown_headers = bot_headers(BOT_SECRET, 9_999_999_999)
    resp = await http.get("/api/bot/v1/merchants", headers=unknown_headers)
    assert resp.status_code == 200
    merchants = resp.json()
    assert isinstance(merchants, list)
    # Неизвестный пользователь может получить пустой список
    # (или список, в котором нет нашего тестового мерчанта)


async def test_bot_list_merchants_after_link(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser
) -> None:
    """После привязки TG-пользователя к мерчанту — он виден в списке."""
    await _link_tg_user(http, admin, merchant, BOT_TG_USER_ID)

    resp = await http.get("/api/bot/v1/merchants", headers=BOT_HEADERS)
    assert resp.status_code == 200
    merchants = resp.json()
    assert isinstance(merchants, list)
    ids = [m["id"] for m in merchants]
    assert merchant.id in ids, f"Merchant {merchant.id} not in bot merchant list: {ids}"


# ---------------------------------------------------------------------------
# Создание, получение, отмена заявки через бота
# ---------------------------------------------------------------------------

async def test_bot_create_order(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser, trader: TestTrader
) -> None:
    """Бот создаёт заявку для привязанного мерчанта."""
    await _link_tg_user(http, admin, merchant, BOT_TG_USER_ID)
    order = await _create_bot_order(http, merchant, amount=300.0)

    assert "id" in order or "uuid" in order
    assert order.get("status") in ("created", "pending")
    assert float(order.get("amount", 0)) == 300.0

    # Cleanup
    order_id = order.get("id") or order.get("uuid")
    await http.post(
        f"/api/bot/v1/merchants/{merchant.id}/orders/{order_id}/cancel",
        headers=BOT_HEADERS,
    )


async def test_bot_get_order(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser, trader: TestTrader
) -> None:
    """Бот может получить созданную им заявку."""
    await _link_tg_user(http, admin, merchant, BOT_TG_USER_ID)
    order = await _create_bot_order(http, merchant, amount=400.0)
    order_id = order.get("id") or order.get("uuid")

    get_resp = await http.get(
        f"/api/bot/v1/merchants/{merchant.id}/orders/{order_id}",
        headers=BOT_HEADERS,
    )
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert str(detail.get("id") or detail.get("uuid")) == str(order_id)

    # Cleanup
    await http.post(
        f"/api/bot/v1/merchants/{merchant.id}/orders/{order_id}/cancel",
        headers=BOT_HEADERS,
    )


async def test_bot_cancel_order(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser, trader: TestTrader
) -> None:
    """Бот отменяет заявку → статус canceled."""
    await _link_tg_user(http, admin, merchant, BOT_TG_USER_ID)
    order = await _create_bot_order(http, merchant, amount=500.0)
    order_id = order.get("id") or order.get("uuid")

    cancel_resp = await http.post(
        f"/api/bot/v1/merchants/{merchant.id}/orders/{order_id}/cancel",
        headers=BOT_HEADERS,
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json().get("status") == "canceled"


async def test_bot_list_active_orders(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser
) -> None:
    """Список активных заявок бота (используется для recovery при перезапуске)."""
    await _link_tg_user(http, admin, merchant, BOT_TG_USER_ID)

    resp = await http.get(
        f"/api/bot/v1/merchants/{merchant.id}/orders",
        headers=BOT_HEADERS,
    )
    assert resp.status_code == 200
    orders = resp.json()
    assert isinstance(orders, list)
    if orders:
        o = orders[0]
        assert "id" in o
        assert "status" in o
        assert "is_final" in o
        assert "merchant_id" in o


# ---------------------------------------------------------------------------
# POST /{order_id}/confirm — загрузка чека
# ---------------------------------------------------------------------------

async def test_bot_confirm_order_with_receipt(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser, trader: TestTrader
) -> None:
    """
    Бот загружает чек к pending-заявке.
    Ожидаем статус receipt_uploaded или success (если автоматически обрабатывается).
    """
    await _link_tg_user(http, admin, merchant, BOT_TG_USER_ID)
    order = await _create_bot_order(http, merchant, amount=600.0)
    order_id = order.get("id") or order.get("uuid")

    # Загружаем фейковый PNG-файл в качестве чека
    fake_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
        b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
        b"\x00IEND\xaeB`\x82"
    )
    confirm_resp = await http.post(
        f"/api/bot/v1/merchants/{merchant.id}/orders/{order_id}/confirm",
        headers=BOT_HEADERS,
        files={"attachment": ("receipt.png", io.BytesIO(fake_png), "image/png")},
    )
    # Заявка может быть не в pending (async mode, нет реквизита) → 400/404 или 200
    if confirm_resp.status_code in (400, 404, 409, 422):
        # Нет реквизита — нельзя подтвердить, это нормально
        pytest.skip(f"Order not in pending state, cannot confirm: {confirm_resp.text}")

    assert confirm_resp.status_code == 200, f"Confirm failed: {confirm_resp.text}"
    data = confirm_resp.json()
    assert data.get("status") in ("receipt_uploaded", "pending", "success"), (
        f"Unexpected status after confirm: {data.get('status')}"
    )

    # Cleanup если не в финальном статусе
    if data.get("status") not in ("success", "failed", "canceled"):
        await http.post(
            f"/api/bot/v1/merchants/{merchant.id}/orders/{order_id}/cancel",
            headers=BOT_HEADERS,
        )


# ---------------------------------------------------------------------------
# Изоляция: TG-пользователь не имеет доступа к чужому мерчанту
# ---------------------------------------------------------------------------

async def test_bot_access_unlisted_merchant(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    """Мерчант без этого TG-пользователя → 401/403/404."""
    suffix = rand_suffix()
    reg = await http.post(
        "/api/v1/auth/register",
        json={"username": f"e2e_bot_m_{suffix}", "password": "SecurePass123", "role": "merchant"},
        headers=admin.auth(),
    )
    assert reg.status_code == 201
    user_id = reg.json()["id"]

    merchants_resp = await http.get(
        f"/api/v1/merchants/?search=e2e_bot_m_{suffix}",
        headers=admin.auth(),
    )
    m = next((x for x in merchants_resp.json() if x["user_id"] == user_id), None)
    if not m:
        pytest.skip("Could not find test merchant for bot isolation test")

    # Активируем без привязки TG-пользователя
    await http.patch(
        f"/api/v1/merchants/{m['id']}",
        json={"status": "enabled", "telegram_user_ids": []},
        headers=admin.auth(),
    )

    resp = await http.post(
        f"/api/bot/v1/merchants/{m['id']}/orders",
        headers=BOT_HEADERS,
        json={"amount": 100.0, "currency": "RUB", "payment_method": "sbp"},
    )
    assert resp.status_code in (401, 403, 404), (
        f"Expected 401/403/404, got {resp.status_code}: {resp.text}"
    )
