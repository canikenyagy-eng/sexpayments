"""
E2E-тесты для покрытия пробелов из аудита сервис-функций:

  • Trader self-service: requisite enable/disable, payout toggle.
  • User self-service: update_me (timezone), 2FA setup → enable → change_password,
    reset_2fa админом.
  • Stats admin endpoints: timeseries (auto-granularity, ручная granularity),
    volume-distribution, order-requests, merchant /me/stats.
  • Bot state: GET /api/bot/v1/state, POST /active-terminal.

Гоняются против живого dev (см. conftest.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pyotp
import pytest

from tests.e2e.conftest import (
    BOT_SECRET,
    BOT_TG_USER_ID,
    TestMerchant,
    TestTrader,
    TestUser,
    _register_and_login,
    bot_headers,
    rand_suffix,
)

pytestmark = pytest.mark.anyio


# ============================================================================
# Trader self-service: requisite enable/disable + payout toggle
# ============================================================================


async def test_trader_can_disable_and_re_enable_own_requisite(
    http: httpx.AsyncClient, trader: TestTrader
):
    """POST /requisites/me/{id}/disable then /enable — service.set_enabled.

    Trader uses their own auth, no admin required. Status flips between
    'enabled' and 'disabled' without touching the admin-only is_active flag.
    """
    # Disable
    disable_resp = await http.post(
        f"/api/v1/requisites/me/{trader.requisite_id}/disable",
        headers=trader.user.auth(),
    )
    assert disable_resp.status_code == 200, disable_resp.text
    body = disable_resp.json()
    assert body["status"] == "disabled"
    # Admin-only flag must remain set (set_enabled doesn't touch is_active).
    assert body["is_active"] is True

    # Re-enable
    enable_resp = await http.post(
        f"/api/v1/requisites/me/{trader.requisite_id}/enable",
        headers=trader.user.auth(),
    )
    assert enable_resp.status_code == 200, enable_resp.text
    body = enable_resp.json()
    assert body["status"] == "enabled"


async def test_other_trader_cannot_toggle_someone_elses_requisite(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
):
    """A different trader hitting /me/{id}/disable on someone else's requisite
    must get 403 (ForbiddenException → HTTP 400 with code='forbidden')."""
    other = await _register_and_login(http, admin, "trader")

    resp = await http.post(
        f"/api/v1/requisites/me/{trader.requisite_id}/disable",
        headers=other.auth(),
    )
    # Project convention: ForbiddenException returns 400 + forbidden code.
    assert resp.status_code in (400, 403, 404), resp.text
    if resp.status_code == 400:
        err = resp.json().get("error", {}) or resp.json()
        # Either forbidden or not_found is acceptable (depends on whether
        # service masks resource existence to the caller).
        code = err.get("code") or err.get("detail")
        assert code in ("forbidden", "not_found", "not found")


async def test_trader_can_toggle_own_payout_flag(
    http: httpx.AsyncClient, trader: TestTrader
):
    """PATCH /traders/me/payout — service.toggle_payout. Symmetric with payin."""
    # Disable payout
    off = await http.patch(
        "/api/v1/traders/me/payout",
        json={"is_active": False},
        headers=trader.user.auth(),
    )
    assert off.status_code == 200, off.text
    assert off.json()["is_payout_active"] is False

    # Re-enable
    on = await http.patch(
        "/api/v1/traders/me/payout",
        json={"is_active": True},
        headers=trader.user.auth(),
    )
    assert on.status_code == 200, on.text
    assert on.json()["is_payout_active"] is True


# ============================================================================
# User self-service: update_me (timezone) + full 2FA enrollment
# ============================================================================


async def test_user_update_me_sets_valid_timezone(
    http: httpx.AsyncClient, admin: TestUser
):
    """PATCH /users/me — service.update_me. Accepts a valid IANA timezone."""
    # Use a fresh user so test is idempotent
    fresh = await _register_and_login(http, admin, "trader")

    resp = await http.patch(
        "/api/v1/users/me",
        json={"timezone": "Europe/Moscow"},
        headers=fresh.auth(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "Europe/Moscow"


async def test_user_update_me_rejects_invalid_timezone(
    http: httpx.AsyncClient, admin: TestUser
):
    fresh = await _register_and_login(http, admin, "trader")

    resp = await http.patch(
        "/api/v1/users/me",
        json={"timezone": "Mars/Olympus"},
        headers=fresh.auth(),
    )
    # FastAPI maps ValidationException → 422 (validation_error).
    assert resp.status_code in (400, 422), resp.text
    body = resp.json()
    err = body.get("error", body)
    assert "timezone" in (err.get("message") or err.get("detail") or "").lower() \
        or err.get("code") == "validation_error"


async def test_user_2fa_full_flow_setup_enable_change_password_admin_reset(
    http: httpx.AsyncClient, admin: TestUser
):
    """End-to-end: setup → enable → change_password (uses TOTP) → admin reset_2fa.

    Each step exercises a previously untested service method:
      • get_totp_uri
      • enable_2fa
      • change_password
      • reset_2fa  (admin)
    """
    fresh = await _register_and_login(http, admin, "trader")

    # 1. setup_2fa returns a secret + provisioning URI without persisting anything.
    setup = await http.get("/api/v1/users/me/2fa/setup", headers=fresh.auth())
    assert setup.status_code == 200, setup.text
    setup_body = setup.json()
    assert "secret" in setup_body and "provisioning_uri" in setup_body
    assert setup_body["provisioning_uri"].startswith("otpauth://totp/")
    secret = setup_body["secret"]

    # 2. enable_2fa with a fresh TOTP code.
    code = pyotp.TOTP(secret).now()
    enable = await http.post(
        "/api/v1/users/me/2fa/enable",
        json={"secret": secret, "google_code": code},
        headers=fresh.auth(),
    )
    assert enable.status_code == 200, enable.text
    assert enable.json()["totp_enabled"] is True

    # 3. change_password requires a valid TOTP code.
    new_password = f"NewPass!{rand_suffix(6)}"
    new_code = pyotp.TOTP(secret).now()
    change = await http.post(
        "/api/v1/users/me/password",
        json={"new_password": new_password, "google_code": new_code},
        headers=fresh.auth(),
    )
    assert change.status_code == 200, change.text

    # 4. The new password actually works on login (with current TOTP).
    login_code = pyotp.TOTP(secret).now()
    login = await http.post(
        "/api/v1/auth/login",
        json={
            "username": fresh.username,
            "password": new_password,
            "totp_code": login_code,
        },
    )
    assert login.status_code == 200, login.text

    # 5. Admin resets 2FA — fresh user now has totp_enabled=false.
    reset = await http.post(
        f"/api/v1/users/{fresh.id}/2fa/reset",
        headers=admin.auth(),
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["totp_enabled"] is False


async def test_user_change_password_rejects_bad_totp_code(
    http: httpx.AsyncClient, admin: TestUser
):
    """If 2FA is enabled, change_password without a valid TOTP must fail."""
    fresh = await _register_and_login(http, admin, "trader")

    # Enable 2FA so the next change_password is gated on TOTP.
    setup = await http.get("/api/v1/users/me/2fa/setup", headers=fresh.auth())
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    enable = await http.post(
        "/api/v1/users/me/2fa/enable",
        json={"secret": secret, "google_code": code},
        headers=fresh.auth(),
    )
    assert enable.status_code == 200

    # Now try with a bogus TOTP — expect 400/403 with forbidden code.
    bad = await http.post(
        "/api/v1/users/me/password",
        json={"new_password": f"BadPass{rand_suffix()}", "google_code": "000000"},
        headers=fresh.auth(),
    )
    assert bad.status_code in (400, 403), bad.text


# ============================================================================
# Stats admin endpoints — were previously untested in e2e
# ============================================================================


async def test_admin_timeseries_default_range_returns_day_buckets(
    http: httpx.AsyncClient, admin: TestUser
):
    """No date_from / date_to → service backfills "last 7 days" → granularity=day."""
    resp = await http.get("/api/v1/stats/admin/timeseries", headers=admin.auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["granularity"] == "day"
    # 7-day default range → 7+1 inclusive day buckets.
    assert len(body["points"]) >= 7
    # Every bucket has the expected shape.
    for p in body["points"]:
        assert "ts" in p
        assert "turnover_usdt" in p
        assert "profit_usdt" in p
        assert "orders" in p


async def test_admin_timeseries_24h_range_picks_hour_granularity(
    http: httpx.AsyncClient, admin: TestUser
):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=12)
    resp = await http.get(
        "/api/v1/stats/admin/timeseries",
        params={
            "date_from": int(start.timestamp()),
            "date_to": int(end.timestamp()),
        },
        headers=admin.auth(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["granularity"] == "hour"
    # Roughly 13 hourly buckets (00..12 inclusive).
    assert 10 <= len(body["points"]) <= 14


async def test_admin_timeseries_explicit_granularity_overrides_auto(
    http: httpx.AsyncClient, admin: TestUser
):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=2)
    resp = await http.get(
        "/api/v1/stats/admin/timeseries",
        params={
            "date_from": int(start.timestamp()),
            "date_to": int(end.timestamp()),
            "granularity": "hour",
        },
        headers=admin.auth(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["granularity"] == "hour"


async def test_admin_timeseries_invalid_granularity_rejected(
    http: httpx.AsyncClient, admin: TestUser
):
    resp = await http.get(
        "/api/v1/stats/admin/timeseries",
        params={"granularity": "year"},
        headers=admin.auth(),
    )
    # Pydantic regex check on the query param → 422 (FastAPI default).
    assert resp.status_code == 422


async def test_admin_volume_distribution_24h_ok(
    http: httpx.AsyncClient, admin: TestUser
):
    resp = await http.get(
        "/api/v1/stats/admin/volume-distribution", headers=admin.auth()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    # No strict assertion on contents — dev DB may be empty during quiet windows.


async def test_admin_order_requests_pagination_shape(
    http: httpx.AsyncClient, admin: TestUser
):
    resp = await http.get(
        "/api/v1/stats/admin/order-requests",
        params={"page": 1, "limit": 5},
        headers=admin.auth(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)
    assert isinstance(body["total"], int)
    # Each item conforms to the schema produced by service.list_order_creation_requests
    for it in body["items"]:
        assert "id" in it
        assert "merchant_id" in it
        assert "success" in it


async def test_merchant_self_stats_endpoint_returns_full_shape(
    http: httpx.AsyncClient, merchant: TestMerchant
):
    """GET /merchants/me/stats — service.get_cached_merchant_stats."""
    resp = await http.get(
        "/api/v1/merchants/me/stats", headers=merchant.user.auth()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in (
        "turnover_usdt",
        "fee_usdt",
        "orders_total",
        "orders_success",
        "orders_active",
        "orders_failed",
        "conversion_pct",
        "pending_withdrawals",
        "active_disputes",
    ):
        assert key in body, f"merchant stats missing field: {key}"


async def test_merchant_self_stats_with_date_range(
    http: httpx.AsyncClient, merchant: TestMerchant
):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    resp = await http.get(
        "/api/v1/merchants/me/stats",
        params={
            "date_from": int(start.timestamp()),
            "date_to": int(end.timestamp()),
        },
        headers=merchant.user.auth(),
    )
    assert resp.status_code == 200, resp.text


# ============================================================================
# Bot state — bot.v1.state.get_active_terminal_for_tg / set_active_terminal_for_tg
# ============================================================================


async def test_bot_state_get_with_unknown_tg_user_returns_null_terminal(
    http: httpx.AsyncClient,
):
    """No merchant linked to this random TG id → active_terminal_id is null."""
    fake_tg_id = 123456789
    resp = await http.get(
        "/api/bot/v1/state",
        headers=bot_headers(BOT_SECRET, fake_tg_id),
    )
    if resp.status_code in (401, 403):
        pytest.skip(f"Bot secret rejected on dev: {resp.status_code}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["telegram_user_id"] == fake_tg_id
    assert body["active_terminal_id"] is None


async def test_bot_state_set_active_terminal_full_round_trip(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
):
    """Bind a fresh TG id to a merchant terminal, set it active via bot API,
    then verify GET /state reports the right active_terminal_id."""
    tg_id = BOT_TG_USER_ID + abs(hash(rand_suffix())) % 100000

    # Step 1: link the TG user to the merchant via admin (otherwise the bot
    # endpoint returns "not authorized" because telegram_user_ids is empty).
    link = await http.patch(
        f"/api/v1/merchants/{merchant.id}",
        json={
            "telegram_user_ids": [{"id": tg_id, "label": "e2e_bot", "active": False}]
        },
        headers=admin.auth(),
    )
    assert link.status_code == 200, link.text

    # Step 2: bot calls set-active-terminal.
    headers = bot_headers(BOT_SECRET, tg_id)
    set_resp = await http.post(
        "/api/bot/v1/state/active-terminal",
        json={"merchant_id": merchant.id},
        headers=headers,
    )
    if set_resp.status_code in (401, 403):
        pytest.skip(f"Bot secret rejected on dev: {set_resp.status_code}")

    assert set_resp.status_code == 200, set_resp.text
    body = set_resp.json()
    assert body["telegram_user_id"] == tg_id
    assert body["active_terminal_id"] == merchant.id

    # Step 3: GET /state reports the same terminal as active.
    get_resp = await http.get("/api/bot/v1/state", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["active_terminal_id"] == merchant.id


async def test_bot_state_set_active_unauthorized_tg_user_rejected(
    http: httpx.AsyncClient, merchant: TestMerchant
):
    """A TG id not present in the merchant.telegram_user_ids list cannot
    activate that terminal."""
    rogue_tg = 999999999
    resp = await http.post(
        "/api/bot/v1/state/active-terminal",
        json={"merchant_id": merchant.id},
        headers=bot_headers(BOT_SECRET, rogue_tg),
    )
    if resp.status_code in (401, 403):
        pytest.skip(f"Bot secret rejected on dev: {resp.status_code}")

    # ValidationException — handler maps to HTTP 422 with code='validation_error'.
    assert resp.status_code in (400, 422), resp.text
    body = resp.json()
    err = body.get("error", body)
    assert "not authorized" in (err.get("message") or err.get("detail") or "").lower() \
        or err.get("code") == "validation_error"


# ============================================================================
# Bot limits — GET /api/bot/v1/merchants/limits
# (service.get_bot_limits_for_tg_user — per-terminal payin capacity)
# ============================================================================


async def test_bot_limits_unknown_tg_user_returns_empty_list(http: httpx.AsyncClient):
    """No merchants linked → terminals list empty, no SQL fired upstream."""
    fake_tg = 555444333
    resp = await http.get(
        "/api/bot/v1/merchants/limits",
        headers=bot_headers(BOT_SECRET, fake_tg),
    )
    if resp.status_code in (401, 403):
        pytest.skip(f"Bot secret rejected on dev: {resp.status_code}")
    if resp.status_code == 404:
        pytest.skip("/api/bot/v1/merchants/limits not yet deployed on this server")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"terminals": []}


async def test_bot_limits_for_linked_terminal_returns_method_breakdown(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,  # noqa: ARG001 — ensures an eligible SBP requisite exists
    trader_group: int,  # noqa: ARG001 — binds trader to merchant via group
):
    """Bind a fresh TG user to the merchant terminal, then verify
    /limits returns a row with the SBP method (the test trader has an
    SBP requisite enabled and ample limits)."""
    tg_id = BOT_TG_USER_ID + abs(hash(rand_suffix())) % 100000

    link = await http.patch(
        f"/api/v1/merchants/{merchant.id}",
        json={
            "telegram_user_ids": [
                {"id": tg_id, "label": "e2e_limits", "active": False}
            ]
        },
        headers=admin.auth(),
    )
    assert link.status_code == 200, link.text

    resp = await http.get(
        "/api/bot/v1/merchants/limits",
        headers=bot_headers(BOT_SECRET, tg_id),
    )
    if resp.status_code in (401, 403):
        pytest.skip(f"Bot secret rejected on dev: {resp.status_code}")
    if resp.status_code == 404:
        pytest.skip("/api/bot/v1/merchants/limits not yet deployed on this server")

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Exactly one terminal — the one we just linked.
    assert len(body["terminals"]) == 1
    term = body["terminals"][0]
    assert term["id"] == merchant.id
    assert term["currency"] == "RUB"

    # Methods list must contain SBP since the test trader fixture creates
    # an SBP requisite with default limits 100k/1M and the trader is bound
    # to this merchant via trader_group.
    method_codes = {m["payment_method"] for m in term["methods"]}
    assert "sbp" in method_codes, f"expected sbp method in {method_codes}"

    sbp = next(m for m in term["methods"] if m["payment_method"] == "sbp")

    # Schema sanity: every numeric field is non-negative; min ≤ max.
    # ``available`` is the single "available right now" number that already
    # collapses daily + monthly via MIN per requisite.
    assert sbp["available"] >= 0
    assert "daily_remaining" not in sbp, "old field should not be present"
    assert "monthly_remaining" not in sbp, "old field should not be present"
    assert sbp["min_amount"] >= 0
    assert sbp["max_amount"] >= sbp["min_amount"]
    assert sbp["requisites_count"] >= 1
    # concurrent_slots is None or non-negative int (DB may return numeric).
    assert sbp["concurrent_slots"] is None or sbp["concurrent_slots"] >= 0
