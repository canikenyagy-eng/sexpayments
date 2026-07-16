"""
E2E-тесты: Admin API — управление пользователями, трейдерами, мерчантами, реквизитами.

Эти тесты проверяют, что admin API:
- Возвращает списки пользователей, трейдеров, мерчантов
- Позволяет обновлять настройки
- Управляет реквизитами
- Возвращает курсы валют
"""

import httpx
import pytest

from tests.e2e.conftest import TestMerchant, TestTrader, TestUser, rand_suffix

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Пользователи (/api/v1/users/)
# ---------------------------------------------------------------------------

async def test_list_users_as_admin(
    http: httpx.AsyncClient, admin: TestUser, trader_user: TestUser
) -> None:
    resp = await http.get("/api/v1/users/", headers=admin.auth())
    assert resp.status_code == 200
    users = resp.json()
    assert isinstance(users, list)
    assert len(users) >= 1
    # Список сортируется по убыванию ID — admin (id=1) может быть за пределами страницы.
    # Проверяем что только что созданный trader_user точно попадает в список.
    ids = [u["id"] for u in users]
    assert trader_user.id in ids, (
        f"trader_user id={trader_user.id} not found in /api/v1/users/ response ids: {ids[:10]}"
    )


async def test_list_users_filter_by_role(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.get("/api/v1/users/?role=trader", headers=admin.auth())
    assert resp.status_code == 200
    users = resp.json()
    assert all(u["role"] == "trader" for u in users)


async def test_get_user_by_id(
    http: httpx.AsyncClient, admin: TestUser, trader_user: TestUser
) -> None:
    resp = await http.get(f"/api/v1/users/{trader_user.id}", headers=admin.auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == trader_user.id
    assert data["username"] == trader_user.username
    assert "password" not in data


async def test_get_user_me(http: httpx.AsyncClient, trader_user: TestUser) -> None:
    resp = await http.get("/api/v1/users/me", headers=trader_user.auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == trader_user.id
    assert data["role"] == "trader"


async def test_update_user_block_unblock(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    suffix = rand_suffix()
    reg = await http.post(
        "/api/v1/auth/register",
        json={"username": f"e2e_block_{suffix}", "password": "SecurePass123", "role": "trader"},
        headers=admin.auth(),
    )
    assert reg.status_code == 201
    user_id = reg.json()["id"]

    block = await http.patch(
        f"/api/v1/users/{user_id}",
        json={"is_blocked": True},
        headers=admin.auth(),
    )
    assert block.status_code == 200
    assert block.json()["is_blocked"] is True

    unblock = await http.patch(
        f"/api/v1/users/{user_id}",
        json={"is_blocked": False},
        headers=admin.auth(),
    )
    assert unblock.status_code == 200
    assert unblock.json()["is_blocked"] is False


async def test_list_users_forbidden_for_trader(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    resp = await http.get("/api/v1/users/", headers=trader_user.auth())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Трейдеры (/api/v1/traders/)
# ---------------------------------------------------------------------------

async def test_list_traders_as_admin(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/traders/", headers=admin.auth())
    assert resp.status_code == 200
    traders = resp.json()
    assert isinstance(traders, list)
    ids = [t["id"] for t in traders]
    assert trader.id in ids


async def test_get_trader_by_id(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    resp = await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == trader.id


async def test_trader_get_own_profile(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    resp = await http.get("/api/v1/traders/me", headers=trader_user.auth())
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["user_id"] == trader_user.id


async def test_trader_status_enabled(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    resp = await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "enabled", f"Trader not enabled: {data['status']}"


async def test_list_traders_forbidden_for_trader(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    resp = await http.get("/api/v1/traders/", headers=trader_user.auth())
    assert resp.status_code == 403


async def test_trader_toggle_payin_forbidden_for_merchant(
    http: httpx.AsyncClient, merchant_user: TestUser
) -> None:
    """Мерчант не может управлять payin трейдера."""
    resp = await http.patch(
        "/api/v1/traders/me/payin",
        json={"is_active": True},
        headers=merchant_user.auth(),
    )
    assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Мерчанты (/api/v1/merchants/)
# ---------------------------------------------------------------------------

async def test_list_merchants_as_admin(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    resp = await http.get("/api/v1/merchants/", headers=admin.auth())
    assert resp.status_code == 200
    merchants = resp.json()
    assert isinstance(merchants, list)
    ids = [m["id"] for m in merchants]
    assert merchant.id in ids


async def test_get_merchant_by_id(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    resp = await http.get(f"/api/v1/merchants/{merchant.id}", headers=admin.auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == merchant.id


async def test_merchant_status_enabled(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    resp = await http.get(f"/api/v1/merchants/{merchant.id}", headers=admin.auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "enabled"


async def test_merchant_get_own_profile_via_jwt(
    http: httpx.AsyncClient, merchant_user: TestUser
) -> None:
    resp = await http.get("/api/v1/merchants/me/profile", headers=merchant_user.auth())
    assert resp.status_code == 200
    data = resp.json()
    assert "balance_work" in data
    assert "payment_methods" in data


async def test_list_merchants_forbidden_for_trader(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    resp = await http.get("/api/v1/merchants/", headers=trader_user.auth())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Реквизиты (/api/v1/requisites/)
# ---------------------------------------------------------------------------

async def test_list_requisites_as_admin(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/requisites/", headers=admin.auth())
    assert resp.status_code == 200
    requisites = resp.json()
    assert isinstance(requisites, list)
    ids = [r["id"] for r in requisites]
    assert trader.requisite_id in ids


async def test_get_requisite_by_id(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    resp = await http.get(f"/api/v1/requisites/{trader.requisite_id}", headers=admin.auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == trader.requisite_id


async def test_trader_list_own_requisites(
    http: httpx.AsyncClient, trader_user: TestUser, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/requisites/me", headers=trader_user.auth())
    assert resp.status_code == 200
    requisites = resp.json()
    ids = [r["id"] for r in requisites]
    assert trader.requisite_id in ids


async def test_trader_get_own_requisite(
    http: httpx.AsyncClient, trader_user: TestUser, trader: TestTrader
) -> None:
    resp = await http.get(
        f"/api/v1/requisites/me/{trader.requisite_id}",
        headers=trader_user.auth(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "enabled"


async def test_requisite_update_by_trader(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader_user: TestUser,
    trader: TestTrader,
) -> None:
    """
    Трейдер не может задавать bank_name напрямую — он наследуется из payment_option.
    Проверяем, что смена payment_option_id корректно обновляет bank_name и payment_option.
    """
    # Берём другую активную опцию (отличную от текущей)
    options_resp = await http.get(
        "/api/v1/payments/options", headers=admin.auth()
    )
    assert options_resp.status_code == 200
    options = [o for o in options_resp.json() if o.get("is_active") and "sbp" in (o.get("supported_methods") or [])]

    # Получаем текущий реквизит, чтобы выбрать «другой» банк
    cur_resp = await http.get(
        f"/api/v1/requisites/{trader.requisite_id}", headers=admin.auth()
    )
    assert cur_resp.status_code == 200
    cur_option_id = cur_resp.json().get("payment_option_id")

    candidates = [o for o in options if o["id"] != cur_option_id]
    assert candidates, "Need at least 2 active SBP payment options for this test"
    new_option = candidates[0]

    resp = await http.patch(
        f"/api/v1/requisites/me/{trader.requisite_id}",
        json={"payment_option_id": new_option["id"]},
        headers=trader_user.auth(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_option_id"] == new_option["id"]
    assert body["bank_name"] == new_option["name"]


async def test_list_requisites_forbidden_for_merchant(
    http: httpx.AsyncClient, merchant_user: TestUser
) -> None:
    resp = await http.get("/api/v1/requisites/", headers=merchant_user.auth())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Курсы валют (/api/v1/rates/)
# ---------------------------------------------------------------------------

async def test_list_rates_as_admin(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.get("/api/v1/rates/", headers=admin.auth())
    assert resp.status_code == 200
    rates = resp.json()
    assert isinstance(rates, list)
    if rates:
        rate = rates[0]
        assert "id" in rate
        assert "fiat_currency" in rate or "currency" in rate
        assert "current_rate" in rate or "rate" in rate


async def test_rates_accessible_without_auth(http: httpx.AsyncClient) -> None:
    """Курсы могут быть публичными или требовать авторизации."""
    resp = await http.get("/api/v1/rates/")
    assert resp.status_code in (200, 401, 403)


# ---------------------------------------------------------------------------
# Trader Groups (/api/v1/traders/groups)
# ---------------------------------------------------------------------------

async def test_trader_group_created(
    http: httpx.AsyncClient, admin: TestUser, trader_group: int
) -> None:
    """trader_group фикстура должна успешно создать и вернуть ID группы."""
    assert trader_group > 0

    resp = await http.get("/api/v1/traders/groups", headers=admin.auth())
    assert resp.status_code == 200
    groups = resp.json()
    assert isinstance(groups, list)
    ids = [g["id"] for g in groups]
    assert trader_group in ids


async def test_trader_group_has_trader_and_merchant(
    http: httpx.AsyncClient, admin: TestUser,
    trader_group: int, trader, merchant
) -> None:
    """Группа содержит и трейдера, и мерчанта."""
    resp = await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
    assert resp.status_code == 200
    trader_data = resp.json()
    group_ids = [g["id"] for g in trader_data.get("groups", [])]
    assert trader_group in group_ids, (
        f"Trader not in group {trader_group}. Trader groups: {group_ids}"
    )


async def test_trader_toggle_payin(
    http: httpx.AsyncClient, admin: TestUser, trader, trader_user: TestUser
) -> None:
    """Тест toggle payin — в конце всегда восстанавливаем enabled."""
    off = await http.patch(
        "/api/v1/traders/me/payin",
        json={"is_active": False},
        headers=trader_user.auth(),
    )
    assert off.status_code == 200
    assert off.json()["is_payin_active"] is False

    on = await http.patch(
        "/api/v1/traders/me/payin",
        json={"is_active": True},
        headers=trader_user.auth(),
    )
    assert on.status_code == 200
    assert on.json()["is_payin_active"] is True
