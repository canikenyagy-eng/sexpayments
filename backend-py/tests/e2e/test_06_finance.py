"""
E2E-тесты: Финансовые операции.

Тесты покрывают:
- Получение балансов трейдера (GET /api/v1/finances/my-balances)
- Получение всех балансов через admin
- Список ledger-записей
- Admin deposit
- Создание заявки на вывод мерчантом
- Одобрение/отклонение вывода через admin
- Список выводов
"""

import httpx
import pytest

from tests.e2e.conftest import TestMerchant, TestTrader, TestUser, admin_deposit, rand_suffix

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# GET /api/v1/finances/my-balances
# ---------------------------------------------------------------------------

async def test_trader_get_own_balances(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/finances/my-balances", headers=trader.user.auth())
    assert resp.status_code == 200
    balances = resp.json()
    assert isinstance(balances, list)
    assert len(balances) > 0
    for b in balances:
        assert "type" in b
        assert "currency" in b
        assert "amount" in b


async def test_trader_balance_includes_work_usdt(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    """После deposit в фикстуре у трейдера должен быть WORK USDT баланс > 0."""
    resp = await http.get("/api/v1/finances/my-balances", headers=trader.user.auth())
    assert resp.status_code == 200
    balances = resp.json()
    work_usdt = next(
        (b for b in balances if b["type"] == "work" and b["currency"] == "USDT"),
        None,
    )
    assert work_usdt is not None, f"No WORK USDT balance found: {balances}"
    assert float(work_usdt["amount"]) > 0, "WORK USDT balance is 0 after deposit"


async def test_merchant_get_own_balances(
    http: httpx.AsyncClient, merchant_user: TestUser
) -> None:
    resp = await http.get("/api/v1/finances/my-balances", headers=merchant_user.auth())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /api/v1/finances/balances (admin)
# ---------------------------------------------------------------------------

async def test_admin_list_all_balances(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.get("/api/v1/finances/balances", headers=admin.auth())
    assert resp.status_code == 200
    balances = resp.json()
    assert isinstance(balances, list)


async def test_list_balances_forbidden_for_trader(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/finances/balances", headers=trader.user.auth())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/finances/admin/deposit
# ---------------------------------------------------------------------------

async def test_admin_deposit_to_trader(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    balances_before = await http.get(
        "/api/v1/finances/my-balances", headers=trader.user.auth()
    )
    work_before = next(
        (b for b in balances_before.json() if b["type"] == "work" and b["currency"] == "USDT"),
        None,
    )
    amount_before = float(work_before["amount"]) if work_before else 0.0

    deposit_resp = await admin_deposit(
        http, admin, user_id=trader.user.id, amount=50, reason="e2e_deposit_trader_test"
    )
    assert deposit_resp.status_code == 200
    data = deposit_resp.json()
    assert float(data["amount"]) == 50

    balances_after = await http.get(
        "/api/v1/finances/my-balances", headers=trader.user.auth()
    )
    work_after = next(
        (b for b in balances_after.json() if b["type"] == "work" and b["currency"] == "USDT"),
        None,
    )
    amount_after = float(work_after["amount"]) if work_after else 0.0
    assert amount_after == pytest.approx(amount_before + 50, abs=0.01)


async def test_admin_deposit_to_merchant(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    resp = await admin_deposit(
        http, admin, merchant_id=merchant.id, amount=100, reason="e2e_deposit_merchant_test"
    )
    assert resp.status_code == 200
    assert float(resp.json()["amount"]) == 100


async def test_admin_deposit_forbidden_for_trader(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.post(
        "/api/v1/finances/admin/adjust",
        headers=trader.user.auth(),
        json={
            "user_id": trader.user.id,
            "amount": 10,
            "currency": "USDT",
            "balance_type": "work",
            "reason": "e2e_forbidden",
        },
    )
    assert resp.status_code == 403


async def test_admin_deposit_no_target(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    """Без user_id и без merchant_id — ожидаем 400/422."""
    resp = await http.post(
        "/api/v1/finances/admin/adjust",
        headers=admin.auth(),
        json={
            "amount": 10,
            "currency": "USDT",
            "balance_type": "work",
            "reason": "e2e_no_target",
        },
    )
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# GET /api/v1/finances/ledger
# ---------------------------------------------------------------------------

async def test_admin_list_ledger_entries(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/finances/ledger?limit=20", headers=admin.auth())
    assert resp.status_code == 200
    entries = resp.json()
    assert isinstance(entries, list)
    if entries:
        entry = entries[0]
        assert "id" in entry
        assert "amount" in entry
        assert "currency" in entry


async def test_ledger_forbidden_for_trader(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/finances/ledger", headers=trader.user.auth())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Withdrawals — мерчант через JWT API (/api/v1/merchants/me/withdrawals)
# ---------------------------------------------------------------------------

async def test_merchant_list_withdrawals(
    http: httpx.AsyncClient, merchant_user: TestUser
) -> None:
    resp = await http.get(
        "/api/v1/merchants/me/withdrawals",
        headers=merchant_user.auth(),
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_merchant_create_withdrawal_insufficient_balance(
    http: httpx.AsyncClient, merchant_user: TestUser
) -> None:
    """Попытка вывода без достаточного баланса → ошибка."""
    resp = await http.post(
        "/api/v1/merchants/me/withdrawals",
        headers=merchant_user.auth(),
        json={
            "amount": "99999999.00",
            "currency": "USDT",
            "destination_address": "TXe2eTestAddressPlaceholder001",
        },
    )
    assert resp.status_code in (400, 409, 422)


async def test_merchant_create_withdrawal_via_api_key_insufficient(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    """Через X-Api-Key — вывод без баланса должен упасть."""
    resp = await http.post(
        "/api/merchant/v1/finances/withdrawals/create",
        headers=merchant.headers(),
        json={
            "amount": "99999999.00",
            "currency": "USDT",
            "destination_address": "TXe2eTestAddressPlaceholder002",
        },
    )
    assert resp.status_code in (400, 404, 409, 422)


async def test_admin_list_withdrawals(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.get("/api/v1/finances/withdrawals", headers=admin.auth())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_list_my_withdrawals_as_trader(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/finances/my-withdrawals", headers=trader.user.auth())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Withdrawal approve / reject flow (если есть pending)
# ---------------------------------------------------------------------------

async def test_withdrawal_approve_reject_nonexistent(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    approve_resp = await http.post(
        "/api/v1/finances/withdrawals/999999999/approve",
        headers=admin.auth(),
    )
    assert approve_resp.status_code in (404, 400)

    reject_resp = await http.post(
        "/api/v1/finances/withdrawals/999999999/reject",
        headers=admin.auth(),
        json={"reason": "not found test"},
    )
    assert reject_resp.status_code in (404, 400)
