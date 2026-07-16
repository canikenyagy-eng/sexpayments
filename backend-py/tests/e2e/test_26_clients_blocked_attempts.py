"""
E2E: client ban + the "blocked attempts" telemetry counter.

Real end-to-end proof of the ban-attempts column on the admin Clients page —
the one path the unit tests can only verify in isolation (the fold uses a
Postgres-only UPSERT + Redis, so the SQLite integration suite can't run it):

  block a (merchant, client) → a payin for that client is rejected (generic
  'no requisite') → the withheld attempt is counted in Redis (HINCRBY) → the
  ``refresh_clients_task`` beat job folds it into ``clients.blocked_attempts``
  → it surfaces on ``GET /api/v1/clients``.

SLOW: waits for the ~60s reconcile beat. Deselected from the default e2e run;
invoke explicitly with ``-m slow``. Skip-guarded for an un-deployed dev.
"""
import asyncio
import time

import httpx
import pytest

from tests.e2e.conftest import TestMerchant, TestUser, rand_suffix

pytestmark = [pytest.mark.anyio, pytest.mark.slow]

# The beat (refresh_clients_task) fires ~every 60s; allow a couple of cycles.
_RECONCILE_WAIT_S = 95
# block_cache hot-path micro-cache TTL (~5s, per worker) — wait it out so the
# payin reliably sees the just-applied block regardless of which worker serves it.
_CACHE_SETTLE_S = 6


async def test_blocked_client_attempt_is_counted(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser
) -> None:
    client_id = f"e2e-blk-{rand_suffix()}"

    # 1. Block (merchant, client). Upserts the clients row + the Redis blocked-set.
    blk = await http.post(
        "/api/v1/clients/block",
        headers=admin.auth(),
        json={"merchant_id": merchant.id, "client_user_id": client_id, "reason": "e2e"},
    )
    if blk.status_code in (404, 405):
        pytest.skip("Эндпоинт /api/v1/clients/block ещё не задеплоен на dev.")
    assert blk.status_code == 200, f"block failed: {blk.text}"
    body = blk.json()
    if "blocked_attempts" not in body:
        pytest.skip("Поле blocked_attempts ещё не задеплоено на dev.")
    assert body["is_blocked"] is True

    try:
        # Let the per-worker hot-path micro-cache pick up the new block.
        await asyncio.sleep(_CACHE_SETTLE_S)

        # 2. Payin for the blocked client → rejected as a generic 'no requisite'.
        payin = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": 1000, "currency": "RUB", "payment_method": "sbp",
                "userId": client_id, "issue_requisite_async": False,
            },
        )
        assert payin.status_code >= 400, (
            f"Blocked client payin must be rejected, got {payin.status_code}: {payin.text}"
        )

        # 3. Poll the admin Clients list until the beat job folds the counter in.
        deadline = time.monotonic() + _RECONCILE_WAIT_S
        counted = 0
        while time.monotonic() < deadline:
            lst = await http.get(
                "/api/v1/clients", headers=admin.auth(), params={"search": client_id}
            )
            assert lst.status_code == 200, lst.text
            row = next(
                (c for c in lst.json() if c.get("client_user_id") == client_id), None
            )
            if row and row.get("blocked_attempts", 0) >= 1:
                counted = row["blocked_attempts"]
                break
            await asyncio.sleep(5)

        assert counted >= 1, (
            "blocked_attempts должен стать >=1 после отклонённой попытки "
            f"(не дождались фолда за {_RECONCILE_WAIT_S}s)"
        )
    finally:
        # Cleanup: unblock so the test client doesn't linger blocked on dev.
        await http.post(
            "/api/v1/clients/unblock",
            headers=admin.auth(),
            json={"merchant_id": merchant.id, "client_user_id": client_id},
        )
