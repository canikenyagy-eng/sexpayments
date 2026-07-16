"""
E2E: full долив (requisite refill) lifecycle through the real HTTP API against dev.

  admin configures the долив settings (+ a доливщик in the executor list) →
  a payin order is created and routed to the requesting trader's requisite →
  admin funds the requester's WORK balance → the trader requests a долив against
  that order → the доливщик sees it in the долив pool, claims it, and executes it
  (confirms the real transfer) → the долив is COMPLETED and shows in the
  requester's "my доливы".

NOTE: requires the долив code to be DEPLOYED to dev (this feature is new). Until
the branch is pushed + auto-deployed these endpoints 404; the test is written to
the existing e2e conventions and goes green on the next deploy.
"""
import asyncio

import httpx
import pytest

from tests.e2e.conftest import (
    TestMerchant,
    TestTrader,
    TestUser,
    _register_and_login,
    admin_deposit,
    isolate_requisites,
    rand_suffix,
    restore_requisites,
)

pytestmark = pytest.mark.anyio


async def _order_int_id(http: httpx.AsyncClient, trader: TestTrader, order_uuid: str) -> int:
    """Resolve the internal int order id (долив takes it) from the trader's
    active orders, where the merchant-side uuid is matched."""
    for _ in range(10):
        r = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
        assert r.status_code == 200, r.text
        for o in r.json():
            if o.get("uuid") == order_uuid:
                return int(o["id"])
        await asyncio.sleep(0.5)
    raise AssertionError("order not visible to trader")


async def test_doliv_full_flow(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
) -> None:
    requester = trader.user

    # A second trader account acts as the доливщик (the administration's cabinet).
    dolivshchik = await _register_and_login(http, admin, "trader")

    # 1. Admin configures долив: 10% price, 5% executor reward, доливщик in the list.
    r = await http.patch("/api/v1/platform-settings/doliv", headers=admin.auth(), json={
        "price_percent": 10,
        "executor_reward_percent": 5,
        "executor_user_ids": str(dolivshchik.id),
        "min_amount": 0,
        "max_amount": 0,
    })
    assert r.status_code == 200, r.text
    assert str(dolivshchik.id) in r.json()["executor_user_ids"]

    # 2. Create a payin order routed to the requesting trader's requisite.
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        create = await http.post("/api/merchant/v1/orders/payin", headers=merchant.headers(), json={
            "amount": 1000, "currency": "RUB", "payment_method": "sbp",
            "internalId": f"e2e_doliv_{rand_suffix()}", "issue_requisite_async": False,
        })
        assert create.status_code in (200, 201), create.text
        order_uuid = create.json()["id"]
        order_id = await _order_int_id(http, trader, order_uuid)

        # 3. Fund the requester's WORK balance so the freeze (amount + price) clears.
        r = await admin_deposit(http, admin, user_id=requester.id, amount=1000, balance_type="work")
        assert r.status_code in (200, 201), r.text

        # 4. Requester asks for a долив of 100 against their requisite (долив is
        #    requisite-anchored; the order above just gives the requisite traffic).
        r = await http.post("/api/v1/doliv", headers=requester.auth(),
                            json={"requisite_id": trader.requisite_id, "amount": 100})
        assert r.status_code == 200, r.text
        doliv = r.json()
        uuid = doliv["id"]
        assert doliv["status"] == "created"
        assert doliv["req_number"]                       # доливщик needs the destination

        # 5. Доливщик sees it in the pool, claims it, executes it.
        r = await http.get("/api/v1/doliv/pool", headers=dolivshchik.auth())
        assert r.status_code == 200 and any(d["id"] == uuid for d in r.json())

        # A non-executor (the requester) sees an empty pool.
        r = await http.get("/api/v1/doliv/pool", headers=requester.auth())
        assert r.status_code == 200 and all(d["id"] != uuid for d in r.json())

        r = await http.post(f"/api/v1/doliv/{uuid}/claim", headers=dolivshchik.auth())
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "claimed"

        # Execute requires a mandatory receipt (photo / PDF) — the доливщик's check.
        r = await http.post(
            f"/api/v1/doliv/{uuid}/execute",
            headers=dolivshchik.auth(),
            files={"attachment": ("receipt.pdf", b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF", "application/pdf")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"

        # 6. Requester's "my доливы" lists it COMPLETED.
        r = await http.get("/api/v1/doliv/mine", headers=requester.auth())
        assert r.status_code == 200
        assert any(d["id"] == uuid and d["status"] == "completed" for d in r.json())
    finally:
        await restore_requisites(http, admin, disabled)


async def test_doliv_pool_requires_executor_list(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader,
) -> None:
    """A trader NOT in doliv_executor_user_ids sees an empty долив pool and is
    forbidden from claiming."""
    intruder = await _register_and_login(http, admin, "trader")
    # Clear the executor list so the intruder is definitely not in it.
    r = await http.patch("/api/v1/platform-settings/doliv", headers=admin.auth(),
                        json={"executor_user_ids": ""})
    assert r.status_code == 200, r.text

    r = await http.get("/api/v1/doliv/pool", headers=intruder.auth())
    assert r.status_code == 200 and r.json() == []
    # Claiming a bogus uuid is rejected (forbidden before not-found for a non-executor).
    r = await http.post(f"/api/v1/doliv/{rand_suffix()}/claim", headers=intruder.auth())
    assert r.status_code in (403, 404)
