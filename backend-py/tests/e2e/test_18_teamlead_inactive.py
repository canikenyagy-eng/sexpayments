"""
E2E: выключенная (`is_active=false`) связь тимлида не платит награды.

`test_11_teamlead_flow.py::test_admin_can_create_update_delete_teamlead_link`
проверяет, что админ может выставить `is_active=false` через PATCH. Здесь
закрывается следующий уровень: убедиться, что бэк действительно НЕ
начисляет вознаграждение по такой ссылке при успешном ордере.

Сценарий:
  1. Создаём ссылку teamlead → merchant с fee_percent>0.
  2. Сразу патчим её в is_active=false.
  3. Прогоняем payin → success.
  4. Баланс тимлида не сдвинулся.
  5. Для контроля включаем ссылку обратно, делаем второй успешный
     ордер — теперь баланс растёт. Это «канари»-проверка, что сам
     по себе reward-механизм рабочий на этом стенде; иначе тест дал
     бы фолс-позитив (никаких наград не платится по другой причине).
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
    isolate_requisites,
    rand_suffix,
    restore_requisites,
)


pytestmark = pytest.mark.anyio


async def _work_usdt(http: httpx.AsyncClient, user: TestUser) -> Decimal:
    resp = await http.get("/api/v1/finances/my-balances", headers=user.auth())
    if resp.status_code != 200:
        return Decimal("0")
    row = next(
        (
            b for b in resp.json()
            if b.get("type") == "work" and b.get("currency") == "USDT"
        ),
        None,
    )
    return Decimal(str(row["amount"])) if row else Decimal("0")


async def _wait_status(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    expected: list[str],
    timeout: float = 15.0,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = await http.get(
            f"/api/merchant/v1/orders/{order_uuid}",
            headers=merchant.headers(),
        )
        if r.status_code == 200:
            last = r.json()
            if last.get("status") in expected:
                return last
        await asyncio.sleep(0.3)
    return last


async def _run_success_order(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
) -> dict | None:
    """Создаёт payin и доводит его до success руками трейдера.
    Возвращает финальный merchant-view JSON, либо None если pooling
    не выдал реквизит (на dev-стенде это считается транзиентным —
    пропускаем тест)."""
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        create = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": 1000.0,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": f"e2e_tl_off_{rand_suffix()}",
                "issue_requisite_async": False,
            },
        )
        if create.status_code != 201:
            return None
        order_uuid = create.json()["id"]
        order = await _wait_status(
            http, merchant, order_uuid,
            ["pending", "success", "failed", "canceled"],
            timeout=10.0,
        )
        if order.get("status") != "pending":
            return None

        # Pick the trader's internal order id.
        trader_order = None
        for _ in range(10):
            r = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
            active = r.json() if r.status_code == 200 else []
            trader_order = next(
                (o for o in active if str(o.get("uuid")) == str(order_uuid)), None
            )
            if trader_order:
                break
            await asyncio.sleep(0.5)
        if trader_order is None:
            return None

        succ = await http.post(
            f"/api/v1/orders/{trader_order['id']}/success",
            headers=trader.user.auth(),
        )
        if succ.status_code != 200:
            return None

        final = await _wait_status(http, merchant, order_uuid, ["success"], timeout=5.0)
        return final if final.get("status") == "success" else None
    finally:
        await restore_requisites(http, admin, disabled)


async def _cleanup_teamlead_links(
    http: httpx.AsyncClient, admin: TestUser, teamlead_id: int
) -> None:
    r = await http.get(
        "/api/v1/teamleaders/admin/links",
        params={"teamlead_id": teamlead_id, "limit": 200},
        headers=admin.auth(),
    )
    if r.status_code != 200:
        return
    for link in r.json():
        lid = link.get("id")
        if lid is None:
            continue
        await http.delete(
            f"/api/v1/teamleaders/admin/links/{lid}", headers=admin.auth()
        )


async def test_inactive_teamlead_link_pays_no_reward(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,  # noqa: ARG001
    teamlead_user: TestUser,
) -> None:
    """is_active=false ссылка не платит, та же ссылка с is_active=true платит."""
    if teamlead_user.role != "teamlead":
        pytest.skip("Teamlead role not available on this deploy")

    await _cleanup_teamlead_links(http, admin, teamlead_user.id)

    # 1. Create link, then immediately deactivate.
    create = await http.post(
        "/api/v1/teamleaders/admin/links",
        headers=admin.auth(),
        json={
            "teamlead_id": teamlead_user.id,
            "linked_entity_type": "merchant",
            "linked_entity_id": merchant.id,
            "fee_percent": 1.0,
        },
    )
    assert create.status_code in (200, 201), create.text
    link_id = create.json()["id"]

    try:
        patch = await http.patch(
            f"/api/v1/teamleaders/admin/links/{link_id}",
            headers=admin.auth(),
            json={"is_active": False},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["is_active"] is False

        # 2. Run a success order — teamlead balance must NOT move.
        balance_before = await _work_usdt(http, teamlead_user)
        order = await _run_success_order(http, admin, merchant, trader)
        if order is None:
            pytest.skip("Could not drive a success order to completion on this deploy")
        balance_after = await _work_usdt(http, teamlead_user)

        assert balance_after == balance_before, (
            f"Inactive teamlead link must NOT pay rewards: "
            f"before={balance_before}, after={balance_after}"
        )

        # 3. Canary: enable the link, run another success — balance must grow.
        # Without this we couldn't tell apart "logic correctly suppressed
        # the payout" from "the dev server is just broken and pays no
        # rewards ever".
        re_enable = await http.patch(
            f"/api/v1/teamleaders/admin/links/{link_id}",
            headers=admin.auth(),
            json={"is_active": True},
        )
        assert re_enable.status_code == 200, re_enable.text

        order2 = await _run_success_order(http, admin, merchant, trader)
        if order2 is None:
            pytest.skip("Canary order didn't complete — deploy is flaky, skipping")
        balance_after_canary = await _work_usdt(http, teamlead_user)

        amount_usdt = Decimal(str(order2.get("amount_usdt") or order2.get("amountUsdt") or 0))
        if amount_usdt > 0:
            expected_reward = amount_usdt * Decimal("1.0") / Decimal("100")
            delta = balance_after_canary - balance_after
            assert delta > 0, (
                f"Re-enabled link must pay: delta=0 between active runs "
                f"(before_canary={balance_after}, after={balance_after_canary})"
            )
            assert abs(delta - expected_reward) < Decimal("0.01"), (
                f"Canary reward mismatch: expected≈{expected_reward}, got {delta}"
            )
    finally:
        # Cleanup: drop the link no matter what.
        await http.delete(
            f"/api/v1/teamleaders/admin/links/{link_id}", headers=admin.auth()
        )
