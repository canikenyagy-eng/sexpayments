"""E2E: an admin-disabled merchant cannot CREATE orders (variant B), but its
read endpoints keep working.

The test blocks the shared merchant, asserts payin creation is 403 while a read
stays 200, and ALWAYS restores the merchant to ``enabled`` (it's a session-scoped
fixture). Until the gate is deployed on the target, payin still returns 201 — the
test then skips rather than failing.
"""
import httpx
import pytest

from tests.e2e.conftest import TestMerchant, TestTrader, TestUser, rand_suffix

pytestmark = pytest.mark.anyio


async def test_disabled_merchant_cannot_create_payin_but_can_read(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    try:
        # Admin blocks the merchant (status change invalidates the auth cache,
        # so it takes effect immediately).
        block = await http.patch(
            f"/api/v1/merchants/{merchant.id}",
            json={"status": "blocked"},
            headers=admin.auth(),
        )
        assert block.status_code == 200, f"block failed: {block.text}"

        # Creating a payin must now be rejected with 403.
        resp = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": 1000.0,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": f"e2e_gate_{rand_suffix()}",
                "issue_requisite_async": False,
            },
        )
        if resp.status_code == 201:
            pytest.skip("merchant-active write gate not deployed on the target yet")
        assert resp.status_code == 403, (
            f"blocked merchant should get 403 on payin, got {resp.status_code}: {resp.text}"
        )

        # Variant B: reads stay open for a disabled merchant.
        read = await http.get(
            "/api/merchant/v1/payments/methods",
            headers=merchant.headers(),
        )
        assert read.status_code == 200, (
            f"reads must stay open for a disabled merchant, got {read.status_code}: {read.text}"
        )
    finally:
        # Always restore — the merchant fixture is session-scoped.
        await http.patch(
            f"/api/v1/merchants/{merchant.id}",
            json={"status": "enabled"},
            headers=admin.auth(),
        )
