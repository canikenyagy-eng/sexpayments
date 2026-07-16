"""E2E: Prime-Time (global trader-fee boost).

Covers:
  * admin config round-trip (activate → GET → stop) + validation,
  * the actual money effect: an order created while a window is active gets
    +X points on the trader fee (compared to a baseline order with no window).

Skip-tolerant: endpoints may not be deployed on dev yet (404/405) → skip; the
tests self-activate after deploy.
"""
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

_BASE = "/api/v1/platform-settings/primetime"


# ── admin config endpoints ────────────────────────────────────────────────


async def test_primetime_activate_get_stop_roundtrip(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    activate = await http.post(
        _BASE, headers=admin.auth(), json={"points": 1.5, "minutes": 5}
    )
    if activate.status_code in (404, 405):
        pytest.skip("Prime-Time endpoints ещё не задеплоены на dev.")
    assert activate.status_code == 200, f"Activate failed: {activate.text}"
    body = activate.json()
    assert body["active"] is True
    assert float(body["points"]) == 1.5
    assert body["ends_at"] is not None

    got = await http.get(_BASE, headers=admin.auth())
    assert got.status_code == 200
    assert got.json()["active"] is True
    assert float(got.json()["points"]) == 1.5

    stop = await http.delete(_BASE, headers=admin.auth())
    assert stop.status_code in (200, 204), f"Stop failed: {stop.text}"

    after = await http.get(_BASE, headers=admin.auth())
    assert after.status_code == 200
    assert after.json()["active"] is False


async def test_primetime_validation_rejects_bad_input(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.post(_BASE, headers=admin.auth(), json={"points": 0, "minutes": 5})
    if resp.status_code in (404, 405):
        pytest.skip("Prime-Time endpoints ещё не задеплоены на dev.")
    assert resp.status_code == 422, f"Expected 422 for points=0, got {resp.status_code}"


# ── the money effect ──────────────────────────────────────────────────────


async def _trader_order_after_create(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, admin: TestUser
) -> dict:
    """Sync-create a payin (our trader wins it via isolation) and return the
    trader's own view of it — which exposes ``amount_usdt`` + ``trader_fee_usdt``.
    The fee is computed and locked at creation, so the order need not complete.
    Skips on a pooling race."""
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        create = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": 1000.0,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": f"pt_{rand_suffix()}",
                "issue_requisite_async": False,
            },
        )
        if create.status_code != 201:
            pytest.skip(f"sync payin not assigned (pooling): {create.status_code} {create.text[:160]}")
        uuid = str(create.json()["id"])
    finally:
        await restore_requisites(http, admin, disabled)

    for _ in range(12):
        r = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
        active = r.json() if r.status_code == 200 else []
        to = next((o for o in active if str(o.get("uuid")) == uuid), None)
        if to and to.get("amount_usdt") and to.get("trader_fee_usdt") is not None:
            return to
        await asyncio.sleep(0.5)
    pytest.skip("order not in trader my-active — pooling race")


def _implied_fee_pct(order: dict) -> float:
    """Trader fee as a % of amount_usdt — rate-independent."""
    return float(order["trader_fee_usdt"]) / float(order["amount_usdt"]) * 100


async def test_primetime_boosts_trader_fee_on_new_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """An order created during a +2 window earns 2 percentage points more on the
    trader fee than a baseline order — verified end-to-end via the trader's view.
    Rate-independent (compares fee/amount ratios)."""
    probe = await http.get(_BASE, headers=admin.auth())
    if probe.status_code in (404, 405):
        pytest.skip("Prime-Time endpoints ещё не задеплоены на dev.")

    # Clean slate, then a baseline order with NO window.
    await http.delete(_BASE, headers=admin.auth())
    base = await _trader_order_after_create(http, merchant, trader, admin)
    base_pct = _implied_fee_pct(base)

    activate = await http.post(_BASE, headers=admin.auth(), json={"points": 2, "minutes": 10})
    assert activate.status_code == 200, activate.text
    try:
        # Wait past the ~3s hot-path cache so every worker picks up the boost.
        await asyncio.sleep(5)
        boosted = await _trader_order_after_create(http, merchant, trader, admin)
        boost_pct = _implied_fee_pct(boosted)
        assert boost_pct - base_pct == pytest.approx(2.0, abs=0.1), (
            f"expected +2 points (base {base_pct:.4f}% → boosted {boost_pct:.4f}%)"
        )
    finally:
        await http.delete(_BASE, headers=admin.auth())
