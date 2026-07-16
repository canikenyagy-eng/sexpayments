"""
E2E-тесты: Функционал тимлида.

Покрытие:
1. Admin CRUD связей тимлида с мерчантом/трейдером (create, update, delete).
2. Защита от дубликатов связей — повторный create с теми же (teamlead, entity)
   должен отклоняться 4xx.
3. Тимлид видит свои связи через `/teamleaders/my-links` и enriched-версию.
4. Полный happy-path PAYIN → success → тимлид получает комиссию на WORK USDT
   (merchant-link и trader-link выплачивают независимо).
5. Dispute reject (SUCCESS → REFUNDED) автоматически реверсирует ранее
   выплаченные награды через TEAMLEAD_REWARD `_reversal` записи.
6. Тимлид может получить агрегированную статистику /my-stats и историю /my-rewards.
"""

import asyncio
import time
from decimal import Decimal

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


MERCHANT_FEE_PERCENT = Decimal("1.00")
TRADER_FEE_PERCENT = Decimal("0.50")


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

async def _work_usdt(http: httpx.AsyncClient, user: TestUser) -> Decimal:
    resp = await http.get("/api/v1/finances/my-balances", headers=user.auth())
    assert resp.status_code == 200, f"balances failed: {resp.text}"
    row = next(
        (
            b for b in resp.json()
            if b.get("type") == "work" and b.get("currency") == "USDT"
        ),
        None,
    )
    return Decimal(str(row["amount"])) if row else Decimal("0")


async def _wait_order_status(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    expected: list[str],
    timeout: float = 15.0,
    interval: float = 0.3,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        resp = await http.get(
            f"/api/merchant/v1/orders/{order_uuid}",
            headers=merchant.headers(),
        )
        if resp.status_code == 200:
            last = resp.json()
            if last.get("status") in expected:
                return last
        await asyncio.sleep(interval)
    return last


async def _run_success_order(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
) -> dict:
    """Создаёт заявку PAYIN и проводит её через pending → trader success.
    Возвращает финальный JSON со статусом success. Если pooling не успел
    выдать реквизит — пропускает тест (это не баг тимлида)."""
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        create_resp = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": 1000.0,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": f"e2e_tl_{rand_suffix()}",
                "issue_requisite_async": False,
            },
        )
        assert create_resp.status_code == 201, f"create failed: {create_resp.text}"
        order_uuid = create_resp.json()["id"]

        order = await _wait_order_status(
            http, merchant, order_uuid,
            ["pending", "success", "failed", "canceled"],
            timeout=10.0,
        )
        if order.get("status") != "pending":
            pytest.skip(f"Order did not reach pending (got {order.get('status')})")

        trader_order: dict | None = None
        for _ in range(10):
            resp = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
            active = resp.json() if resp.status_code == 200 else []
            trader_order = next(
                (o for o in active if str(o.get("uuid")) == str(order_uuid)), None
            )
            if trader_order:
                break
            await asyncio.sleep(0.5)
        if trader_order is None:
            pytest.skip("Order not in trader active list (pooling race)")

        success_resp = await http.post(
            f"/api/v1/orders/{trader_order['id']}/success",
            headers=trader.user.auth(),
        )
        assert success_resp.status_code == 200, f"trader success failed: {success_resp.text}"

        final = await _wait_order_status(http, merchant, order_uuid, ["success"], timeout=5.0)
        assert final.get("status") == "success", f"Order not success: {final}"
        return final
    finally:
        await restore_requisites(http, admin, disabled)


async def _create_link(
    http: httpx.AsyncClient,
    admin: TestUser,
    teamlead_id: int,
    *,
    entity_type: str,
    entity_id: int,
    fee_percent: float,
) -> dict:
    resp = await http.post(
        "/api/v1/teamleaders/admin/links",
        headers=admin.auth(),
        json={
            "teamlead_id": teamlead_id,
            "linked_entity_type": entity_type,
            "linked_entity_id": entity_id,
            "fee_percent": fee_percent,
        },
    )
    assert resp.status_code in (200, 201), f"create link failed: {resp.status_code} {resp.text}"
    return resp.json()


async def _cleanup_teamlead_links(
    http: httpx.AsyncClient, admin: TestUser, teamlead_id: int
) -> None:
    resp = await http.get(
        "/api/v1/teamleaders/admin/links",
        params={"teamlead_id": teamlead_id, "limit": 200},
        headers=admin.auth(),
    )
    if resp.status_code != 200:
        return
    for link in resp.json():
        lid = link.get("id")
        if lid is None:
            continue
        await http.delete(
            f"/api/v1/teamleaders/admin/links/{lid}",
            headers=admin.auth(),
        )


# ---------------------------------------------------------------------------
# 1. Admin CRUD + duplicate guard
# ---------------------------------------------------------------------------

async def test_admin_can_create_update_delete_teamlead_link(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    teamlead_user: TestUser,
) -> None:
    if teamlead_user.role != "teamlead":
        pytest.skip("Teamlead role not available on this deploy")

    await _cleanup_teamlead_links(http, admin, teamlead_user.id)

    created = await _create_link(
        http, admin, teamlead_user.id,
        entity_type="merchant", entity_id=merchant.id,
        fee_percent=float(MERCHANT_FEE_PERCENT),
    )
    link_id = created["id"]
    assert created["teamlead_id"] == teamlead_user.id
    assert int(created["linked_entity_id"]) == merchant.id
    assert Decimal(str(created["fee_percent"])) == MERCHANT_FEE_PERCENT

    # duplicate → 4xx
    dup = await http.post(
        "/api/v1/teamleaders/admin/links",
        headers=admin.auth(),
        json={
            "teamlead_id": teamlead_user.id,
            "linked_entity_type": "merchant",
            "linked_entity_id": merchant.id,
            "fee_percent": 1.0,
        },
    )
    assert dup.status_code in (400, 409, 422), (
        f"duplicate link should be rejected, got {dup.status_code}: {dup.text}"
    )

    # update
    upd = await http.patch(
        f"/api/v1/teamleaders/admin/links/{link_id}",
        headers=admin.auth(),
        json={"fee_percent": 2.25, "is_active": False},
    )
    assert upd.status_code == 200, upd.text
    assert Decimal(str(upd.json()["fee_percent"])) == Decimal("2.25")
    assert upd.json()["is_active"] is False

    # delete
    delete_resp = await http.delete(
        f"/api/v1/teamleaders/admin/links/{link_id}",
        headers=admin.auth(),
    )
    assert delete_resp.status_code in (200, 204), delete_resp.text

    # gone from admin list
    list_resp = await http.get(
        "/api/v1/teamleaders/admin/links",
        params={"teamlead_id": teamlead_user.id, "limit": 200},
        headers=admin.auth(),
    )
    assert list_resp.status_code == 200
    assert all(l.get("id") != link_id for l in list_resp.json())


# ---------------------------------------------------------------------------
# 2. Тимлид видит свои связи
# ---------------------------------------------------------------------------

async def test_teamlead_sees_own_links(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    teamlead_user: TestUser,
) -> None:
    if teamlead_user.role != "teamlead":
        pytest.skip("Teamlead role not available on this deploy")

    await _cleanup_teamlead_links(http, admin, teamlead_user.id)
    link = await _create_link(
        http, admin, teamlead_user.id,
        entity_type="merchant", entity_id=merchant.id,
        fee_percent=float(MERCHANT_FEE_PERCENT),
    )
    try:
        my_links = await http.get(
            "/api/v1/teamleaders/my-links",
            headers=teamlead_user.auth(),
        )
        assert my_links.status_code == 200, my_links.text
        ids = [l.get("id") for l in my_links.json()]
        assert link["id"] in ids

        enriched = await http.get(
            "/api/v1/teamleaders/my-links-enriched",
            headers=teamlead_user.auth(),
        )
        assert enriched.status_code == 200, enriched.text
        row = next((r for r in enriched.json() if r.get("linked_entity_id") == merchant.id), None)
        assert row is not None, f"Merchant link not in enriched: {enriched.json()}"
        assert row.get("login")
        assert "income_usdt" in row
    finally:
        await http.delete(
            f"/api/v1/teamleaders/admin/links/{link['id']}",
            headers=admin.auth(),
        )


# ---------------------------------------------------------------------------
# 3. Полный цикл: успешный ордер → награды тимлидам (merchant + trader)
# ---------------------------------------------------------------------------

async def test_teamlead_receives_rewards_on_success_order(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    teamlead_user: TestUser,
) -> None:
    """Тимлид с двумя связями (merchant 1% + trader 0.5%) получает оба
    начисления при успешном ордере, сумма = (merchant_fee + trader_fee)% от
    amount_usdt."""
    if teamlead_user.role != "teamlead":
        pytest.skip("Teamlead role not available on this deploy")

    await _cleanup_teamlead_links(http, admin, teamlead_user.id)

    m_link = await _create_link(
        http, admin, teamlead_user.id,
        entity_type="merchant", entity_id=merchant.id,
        fee_percent=float(MERCHANT_FEE_PERCENT),
    )
    t_link = await _create_link(
        http, admin, teamlead_user.id,
        entity_type="trader", entity_id=trader.user.id,
        fee_percent=float(TRADER_FEE_PERCENT),
    )

    try:
        balance_before = await _work_usdt(http, teamlead_user)
        order = await _run_success_order(http, admin, merchant, trader)
        balance_after = await _work_usdt(http, teamlead_user)

        amount_usdt = order.get("amount_usdt") or order.get("amountUsdt")
        if amount_usdt is None:
            pytest.skip("amount_usdt not available in merchant order response")
        amount_usdt = Decimal(str(amount_usdt))
        expected_reward = (
            amount_usdt * (MERCHANT_FEE_PERCENT + TRADER_FEE_PERCENT) / Decimal("100")
        )
        delta = balance_after - balance_before

        assert delta > 0, f"Teamlead WORK USDT not increased: before={balance_before} after={balance_after}"
        assert abs(delta - expected_reward) < Decimal("0.01"), (
            f"Reward mismatch: delta={delta}, expected≈{expected_reward} "
            f"(amount_usdt={amount_usdt})"
        )

        # Stats and history
        stats_resp = await http.get(
            "/api/v1/teamleaders/my-stats", headers=teamlead_user.auth()
        )
        assert stats_resp.status_code == 200, stats_resp.text
        stats = stats_resp.json()
        assert stats.get("active_links_count") >= 2
        # Stats округляет суммы до 4 знаков (0.0000), а expected_reward —
        # точное число; сравниваем с тем же tolerance, что и delta выше.
        assert (
            Decimal(str(stats.get("total_earned_usdt", 0)))
            >= expected_reward - Decimal("0.01")
        )

        rewards_resp = await http.get(
            "/api/v1/teamleaders/my-rewards", headers=teamlead_user.auth()
        )
        assert rewards_resp.status_code == 200, rewards_resp.text
        rewards = rewards_resp.json()
        assert len(rewards) >= 2, f"Expected ≥2 reward entries, got {len(rewards)}"
        teamlead_reward_types = {r.get("reference_type") for r in rewards}
        assert "teamlead_reward" in teamlead_reward_types

        enriched_resp = await http.get(
            "/api/v1/teamleaders/my-links-enriched", headers=teamlead_user.auth()
        )
        assert enriched_resp.status_code == 200
        for row in enriched_resp.json():
            if row.get("linked_entity_id") == merchant.id and row.get("linked_entity_type") == "merchant":
                assert Decimal(str(row["income_usdt"])) >= amount_usdt * MERCHANT_FEE_PERCENT / Decimal("100") - Decimal("0.01")
            elif row.get("linked_entity_id") == trader.user.id and row.get("linked_entity_type") == "trader":
                assert Decimal(str(row["income_usdt"])) >= amount_usdt * TRADER_FEE_PERCENT / Decimal("100") - Decimal("0.01")
    finally:
        for link_id in (m_link["id"], t_link["id"]):
            await http.delete(
                f"/api/v1/teamleaders/admin/links/{link_id}",
                headers=admin.auth(),
            )


# ---------------------------------------------------------------------------
# 4. Dispute reject (SUCCESS → REFUNDED) реверсирует награды тимлидов
# ---------------------------------------------------------------------------

async def test_dispute_reject_reverses_teamlead_rewards(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    teamlead_user: TestUser,
) -> None:
    """
    1) Успешный ордер → тимлид получил награду.
    2) Мерчант открывает спор по success → трейдер выигрывает, admin reject.
    3) Ордер переходит в REFUNDED, награды тимлида реверсируются в систему.
    Итог: баланс тимлида не изменился относительно состояния ДО ордера
    (с точностью до 0.01 USDT из-за округления на paу-rewards).
    """
    if teamlead_user.role != "teamlead":
        pytest.skip("Teamlead role not available on this deploy")

    await _cleanup_teamlead_links(http, admin, teamlead_user.id)
    link = await _create_link(
        http, admin, teamlead_user.id,
        entity_type="merchant", entity_id=merchant.id,
        fee_percent=float(MERCHANT_FEE_PERCENT),
    )

    try:
        # Top up merchant WORK USDT to have enough to freeze in ESCROW during dispute
        await admin_deposit(
            http, admin,
            merchant_id=merchant.id,
            amount=500.0,
            reason="e2e_teamlead_dispute_reject_topup",
        )

        balance_before = await _work_usdt(http, teamlead_user)
        order = await _run_success_order(http, admin, merchant, trader)
        balance_after_success = await _work_usdt(http, teamlead_user)
        assert balance_after_success > balance_before, (
            "Teamlead did not receive reward for successful order"
        )

        order_uuid = order["id"]

        # Merchant opens dispute on SUCCESS order
        open_resp = await http.post(
            f"/api/merchant/v1/disputes/by-order/{order_uuid}",
            headers=merchant.headers(),
            data={"reason": "no_payment"},
        )
        if open_resp.status_code not in (200, 201):
            pytest.skip(f"Open dispute failed: {open_resp.status_code} {open_resp.text}")
        dispute_uuid = str(open_resp.json().get("uuid") or open_resp.json().get("id"))

        admin_list = await http.get("/api/v1/disputes?limit=200", headers=admin.auth())
        assert admin_list.status_code == 200
        d = next(
            (x for x in admin_list.json() if str(x.get("uuid")) == dispute_uuid), None
        )
        if d is None or d.get("id") is None:
            pytest.skip("Dispute id not resolvable from admin list")
        dispute_id = int(d["id"])

        await http.post(
            f"/api/v1/disputes/{dispute_id}/in-progress", headers=admin.auth()
        )
        reject_resp = await http.post(
            f"/api/v1/disputes/{dispute_id}/reject",
            headers=admin.auth(),
            json={"resolution_text": "E2E: reject → refund, rewards must reverse"},
        )
        assert reject_resp.status_code == 200, f"reject failed: {reject_resp.text}"

        final = await _wait_order_status(
            http, merchant, order_uuid,
            ["refunded", "failed"], timeout=10.0,
        )
        assert final.get("status") in ("refunded", "failed"), (
            f"Order not refunded: {final}"
        )

        balance_after_refund = await _work_usdt(http, teamlead_user)
        reward_paid = balance_after_success - balance_before
        residual = balance_after_refund - balance_before
        assert abs(residual) < Decimal("0.01"), (
            f"Teamlead reward not reversed. before={balance_before}, "
            f"after_success={balance_after_success} (+{reward_paid}), "
            f"after_refund={balance_after_refund} (residual={residual})"
        )

        # Reversal entry is visible in history
        rewards_resp = await http.get(
            "/api/v1/teamleaders/my-rewards", headers=teamlead_user.auth()
        )
        assert rewards_resp.status_code == 200
        refs = [
            r.get("reference_id", "") for r in rewards_resp.json()
        ]
        assert any("_reversal" in r for r in refs), (
            f"No reversal entry found in reward history: {refs[:10]}"
        )
    finally:
        await http.delete(
            f"/api/v1/teamleaders/admin/links/{link['id']}",
            headers=admin.auth(),
        )


# ---------------------------------------------------------------------------
# 5. Admin listing & stats
# ---------------------------------------------------------------------------

async def test_admin_can_list_teamleads(
    http: httpx.AsyncClient, admin: TestUser, teamlead_user: TestUser
) -> None:
    if teamlead_user.role != "teamlead":
        pytest.skip("Teamlead role not available on this deploy")
    resp = await http.get(
        "/api/v1/teamleaders/admin/teamleads",
        headers=admin.auth(),
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    ids = [t.get("id") for t in resp.json()]
    assert teamlead_user.id in ids


# ═══════════════════════════════════════════════════════════════════════════
# Teamlead reward through a dispute: reversed on open, re-paid on resolve.
# (This previously broke — the system balance went negative because the
#  teamlead reward wasn't reversed when un-settling a paid order.)
# ═══════════════════════════════════════════════════════════════════════════

async def test_teamlead_reward_reversed_on_dispute_then_repaid_on_resolve(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    teamlead_user: TestUser,
) -> None:
    if teamlead_user.role != "teamlead":
        pytest.skip("no teamlead user available")

    await _cleanup_teamlead_links(http, admin, teamlead_user.id)
    await _create_link(
        http, admin, teamlead_user.id,
        entity_type="merchant", entity_id=merchant.id,
        fee_percent=float(MERCHANT_FEE_PERCENT),
    )
    try:
        before = await _work_usdt(http, teamlead_user)
        order = await _run_success_order(http, admin, merchant, trader)  # teamlead paid
        await asyncio.sleep(0.5)
        after_success = await _work_usdt(http, teamlead_user)
        reward = after_success - before
        assert reward > 0, f"teamlead must be paid on success, Δ={reward}"

        order_uuid = order["id"]
        # Open a dispute (merchant) — the paid teamlead reward must be REVERSED.
        disp = await http.post(
            f"/api/merchant/v1/disputes/by-order/{order_uuid}",
            headers=merchant.headers(),
            data={"reason": "no_payment"},
        )
        if disp.status_code == 422:
            pytest.skip("dispute contract not deployed on dev")
        assert disp.status_code in (200, 201), f"open dispute failed: {disp.text}"
        await asyncio.sleep(0.5)
        after_open = await _work_usdt(http, teamlead_user)
        assert abs((after_open - after_success) + reward) <= Decimal("0.01"), (
            f"teamlead reward must reverse on dispute open, Δ={after_open - after_success}"
        )

        # Resolve (merchant wins) → order back to SUCCESS → teamlead RE-PAID.
        dispute_id = disp.json().get("id")
        if not isinstance(dispute_id, int):
            lst = await http.get("/api/v1/disputes?limit=200", headers=admin.auth())
            found = next((x for x in lst.json() if str(x.get("uuid")) == str(disp.json().get("uuid"))), None)
            dispute_id = found and found.get("id")
        assert isinstance(dispute_id, int), "can't resolve dispute id"

        resolve = await http.post(
            f"/api/v1/disputes/{dispute_id}/resolve",
            headers=admin.auth(),
            json={"resolution_text": "e2e teamlead dispute resolve"},
        )
        assert resolve.status_code == 200, resolve.text
        await asyncio.sleep(0.5)
        after_resolve = await _work_usdt(http, teamlead_user)
        assert abs((after_resolve - after_open) - reward) <= Decimal("0.01"), (
            f"teamlead reward must be re-paid on resolve, Δ={after_resolve - after_open}"
        )
    finally:
        await _cleanup_teamlead_links(http, admin, teamlead_user.id)
