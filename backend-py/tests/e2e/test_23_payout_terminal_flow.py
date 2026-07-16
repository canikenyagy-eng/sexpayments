"""
E2E: full payout-terminal lifecycle through the real HTTP API against dev.

  admin creates a payout terminal (+ trader ACL) → admin tops up its balance →
  merchant (terminal key) creates a payout → trader sees the pool, claims it
  (requisite revealed) → trader closes it with TWO partial receipts → merchant
  sees it COMPLETED.

NOTE: requires the payout-terminal code to be DEPLOYED to dev (this feature is
new). Until the branch is pushed + auto-deployed, these endpoints 404; the test
is written to the existing e2e conventions and goes green on the next deploy.
"""
import httpx
import pytest

from tests.e2e.conftest import TestMerchant, TestTrader, TestUser, rand_suffix

pytestmark = pytest.mark.anyio

# Valid JPEG magic bytes so the receipt-format gate (extension + magic-byte check
# in ReceiptStorage.validate_format) accepts the dummy content.
_JPEG = b"\xff\xd8\xff"


async def test_payout_terminal_full_flow(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant_user: TestUser,
    trader: TestTrader,
) -> None:
    trader_user = trader.user

    # Trader enables payouts.
    r = await http.patch("/api/v1/traders/me/payout", json={"is_active": True}, headers=trader_user.auth())
    assert r.status_code == 200, r.text

    # 1. Admin creates a payout terminal owned by the merchant user, ACL = trader.
    r = await http.post("/api/v1/payouts/terminals", headers=admin.auth(), json={
        "owner_user_id": merchant_user.id,
        "name": f"PT-{rand_suffix()}",
        "currency": "RUB",
        "commission_percent": 10,
        "ttl_minutes": 60,
        "receipts_to_close": 2,
        "trader_ids": [trader_user.id],
    })
    assert r.status_code == 201, r.text
    terminal = r.json()
    terminal_id = terminal["id"]
    api_key = terminal["api_key"]
    assert terminal["api_secret"]                       # plaintext secret shown once
    tkey = {"X-Api-Key": api_key}

    # 2. Admin tops up the terminal balance (USDT).
    r = await http.post(f"/api/v1/payouts/terminals/{terminal_id}/topup", headers=admin.auth(),
                        json={"amount": 100000})
    assert r.status_code == 200, r.text
    assert float(r.json()["work_usdt"]) >= 100000

    # Invalid terminal key is rejected.
    r = await http.get("/api/merchant/payout/v1/payouts/external/nope", headers={"X-Api-Key": "bad_key_000"})
    assert r.status_code in (401, 403)

    # 3. Merchant creates a payout via the terminal key.
    external_id = f"po_{rand_suffix()}"
    r = await http.post("/api/merchant/payout/v1/payouts", headers=tkey, json={
        "amount": 1000, "currency": "RUB", "payment_method": "sbp", "external_id": external_id,
        "payment_requisites": {"holder": "End User", "number": "40817810099910000001"},
    })
    assert r.status_code == 201, r.text
    payout = r.json()
    uuid = payout["id"]
    assert payout["status"] == "created"
    assert "req_number" not in payout                   # merchant view never echoes the requisite

    # Idempotency: same external_id is rejected. Duplicate → ValidationException
    # → 422, same convention as a duplicate payin internalId.
    r = await http.post("/api/merchant/payout/v1/payouts", headers=tkey, json={
        "amount": 1000, "currency": "RUB", "payment_method": "sbp", "external_id": external_id,
        "payment_requisites": {"holder": "End User", "number": "40817810099910000001"},
    })
    assert r.status_code == 422, r.text

    # Status by external id.
    r = await http.get(f"/api/merchant/payout/v1/payouts/external/{external_id}", headers=tkey)
    assert r.status_code == 200 and r.json()["id"] == uuid

    # 4. Trader sees it in the pool and claims it (requisite revealed).
    r = await http.get("/api/v1/payouts/pool", headers=trader_user.auth())
    assert r.status_code == 200 and any(p["id"] == uuid for p in r.json())

    r = await http.post(f"/api/v1/payouts/{uuid}/claim", headers=trader_user.auth())
    assert r.status_code == 200, r.text
    claimed = r.json()
    assert claimed["status"] == "claimed"
    assert claimed["req_number"] == "40817810099910000001"

    # 5. Two partial receipts (400 + 600 = 1000) close it; auto-approve by default.
    r = await http.post(f"/api/v1/payouts/{uuid}/receipt", headers=trader_user.auth(),
                        data={"amount": "400"}, files={"attachment": ("r1.jpg", _JPEG + b"receipt-1", "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("claimed", "awaiting_check")   # not yet fully covered

    r = await http.post(f"/api/v1/payouts/{uuid}/receipt", headers=trader_user.auth(),
                        data={"amount": "600"}, files={"attachment": ("r2.jpg", _JPEG + b"receipt-2", "image/jpeg")})
    assert r.status_code == 200, r.text

    # 6. Merchant sees the payout COMPLETED.
    r = await http.get(f"/api/merchant/payout/v1/payouts/{uuid}", headers=tkey)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"

    # 7. Trader's "mine" lists the completed payout with its fee.
    r = await http.get("/api/v1/payouts/mine", headers=trader_user.auth())
    assert r.status_code == 200 and any(p["id"] == uuid and p["status"] == "completed" for p in r.json())


async def test_payout_pool_requires_acl(
    http: httpx.AsyncClient, admin: TestUser, merchant_user: TestUser, trader: TestTrader,
) -> None:
    """A terminal with NO trader ACL keeps its payouts out of the trader's pool."""
    trader_user = trader.user
    await http.patch("/api/v1/traders/me/payout", json={"is_active": True}, headers=trader_user.auth())

    r = await http.post("/api/v1/payouts/terminals", headers=admin.auth(), json={
        "owner_user_id": merchant_user.id, "name": f"PT-noacl-{rand_suffix()}",
        "currency": "RUB", "commission_percent": 5, "ttl_minutes": 60, "receipts_to_close": 1,
        "trader_ids": [],   # nobody bound
    })
    assert r.status_code == 201, r.text
    api_key = r.json()["api_key"]
    await http.post(f"/api/v1/payouts/terminals/{r.json()['id']}/topup", headers=admin.auth(), json={"amount": 100000})

    external_id = f"po_{rand_suffix()}"
    r = await http.post("/api/merchant/payout/v1/payouts", headers={"X-Api-Key": api_key}, json={
        "amount": 1000, "currency": "RUB", "payment_method": "sbp", "external_id": external_id,
        "payment_requisites": {"holder": "E", "number": "40817810099910000001"},
    })
    assert r.status_code == 201, r.text
    uuid = r.json()["id"]

    # Not in the (unbound) trader's pool; claim is forbidden.
    r = await http.get("/api/v1/payouts/pool", headers=trader_user.auth())
    assert r.status_code == 200 and all(p["id"] != uuid for p in r.json())
    r = await http.post(f"/api/v1/payouts/{uuid}/claim", headers=trader_user.auth())
    assert r.status_code == 403
