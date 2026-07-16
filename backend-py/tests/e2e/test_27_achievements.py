"""E2E: trader achievements — full chain against dev.

Proves the earned bonus is ACTUALLY APPLIED to a trader's commission:
  1. admin configures a daily-volume-tier rule (any today-turnover → +1%);
  2. a real order is created + completed (builds today's turnover);
  3. admin force-recomputes bonuses;
  4. the trader profile now carries +1%;
  5. a SECOND order's stamped ``trader_fee_usdt`` reflects base fee + the bonus —
     asserted as the fee-ratio DELTA between the two orders (≈ +1%), which is
     robust to any platform-wide PrimeTime boost (shared by both orders).

Runs against E2E_BASE_URL (default dev); needs the feature deployed + a trader
with an active requisite. Cleans up the config + requisites afterward."""
import asyncio

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


async def _create_and_complete(http, merchant: TestMerchant, trader: TestTrader) -> int:
    """Run one full order lifecycle (merchant payin → pending → trader success) and
    return the internal order id. Skips the test if no requisite got assigned."""
    create = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 1000.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": f"e2e_ach_{rand_suffix()}",
            "userId": "e2e_ach_client",
            "issue_requisite_async": False,
        },
    )
    assert create.status_code == 201, f"payin failed: {create.text}"
    order_uuid = create.json()["id"]

    trader_order = None
    for _ in range(20):
        resp = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
        assert resp.status_code == 200
        trader_order = next((o for o in resp.json() if str(o.get("uuid")) == str(order_uuid)), None)
        if trader_order is not None:
            break
        await asyncio.sleep(0.4)
    if trader_order is None:
        pytest.skip("order never reached the trader's active list (no requisite available)")

    order_id = trader_order["id"]
    success = await http.post(f"/api/v1/orders/{order_id}/success", headers=trader.user.auth())
    assert success.status_code == 200, f"trader success failed: {success.text}"
    assert success.json()["status"] == "success"
    return order_id


async def _fee_ratio(http, admin: TestUser, order_id: int) -> float:
    """trader_fee_usdt / amount_usdt for an order (admin view). There is no
    GET /orders/{id} (that path is PATCH-only → 405), so read via the admin list
    with id_search and pick the exact row."""
    resp = await http.get("/api/v1/orders/", headers=admin.auth(),
                          params={"id_search": str(order_id), "limit": 20})
    assert resp.status_code == 200, resp.text
    o = next((x for x in resp.json()["items"] if x["id"] == order_id), None)
    assert o is not None, f"order {order_id} not found via id_search"
    assert o["amount_usdt"] and o["trader_fee_usdt"] is not None
    return float(o["trader_fee_usdt"]) / float(o["amount_usdt"])


async def test_bonus_is_applied_to_next_order(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant, trader: TestTrader,
    trader_group: int,  # noqa: ARG001 — binds merchant↔trader so pooling can route the payin
) -> None:
    prev = (await http.get("/api/v1/platform-settings/achievements", headers=admin.auth())).json()
    try:
        cfg = await http.patch(
            "/api/v1/platform-settings/achievements",
            headers=admin.auth(),
            json={
                "enabled": True,
                "bonus_max_percent": 0,
                # streak_volume_tier is the deployed achievements model. streak_days=1
                # means one qualifying day (today) unlocks the bonus, and the level is
                # taken from the average over that 1 day (= today's turnover), so any
                # today-turnover ≥ 0.01 → +1% — same intent as the old daily-tier rule.
                "rules": [{
                    "type": "streak_volume_tier",
                    "streak_days": 1,
                    "min_daily_volume": "0.01",
                    "tiers": [{"min_avg": "0.01", "percent": "1.0"}],
                }],
            },
        )
        assert cfg.status_code == 200, cfg.text

        disabled = await isolate_requisites(http, admin, trader.requisite_id)
        try:
            # Order #1 — created BEFORE the bonus (baseline fee).
            oid1 = await _create_and_complete(http, merchant, trader)
            ratio1 = await _fee_ratio(http, admin, oid1)

            # Force the recompute: today's turnover (order #1) clears the tier → +1%.
            rec = await http.post("/api/v1/traders/achievements/recompute", headers=admin.auth())
            assert rec.status_code == 200, rec.text

            me = (await http.get("/api/v1/traders/me", headers=trader.user.auth())).json()
            assert float(me["achievement_bonus_percent"]) == pytest.approx(1.0), me

            # Order #2 — created AFTER the bonus; its stamped fee must reflect it.
            oid2 = await _create_and_complete(http, merchant, trader)
            ratio2 = await _fee_ratio(http, admin, oid2)

            # The fee-ratio delta between the two orders == the +1% bonus (0.01),
            # regardless of any PrimeTime boost shared by both.
            assert (ratio2 - ratio1) == pytest.approx(0.01, abs=0.003), (ratio1, ratio2)
        finally:
            await restore_requisites(http, admin, disabled)
    finally:
        await http.patch(
            "/api/v1/platform-settings/achievements",
            headers=admin.auth(),
            json={
                "enabled": bool(prev.get("enabled", False)),
                "bonus_max_percent": prev.get("bonus_max_percent", 0),
                "rules": prev.get("rules", []),
            },
        )


async def test_achievements_endpoint_shape(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    resp = await http.get("/api/v1/traders/me/achievements", headers=trader_user.auth())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data["enabled"], bool)
    assert isinstance(data["total_bonus_percent"], (int, float))
    assert isinstance(data["today_volume_usdt"], (int, float))
    assert isinstance(data["items"], list)
