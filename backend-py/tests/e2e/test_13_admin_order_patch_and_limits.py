"""
E2E-тесты на новый функционал, который раньше не покрывался:

1. Админский ``PATCH /api/v1/orders/{id}`` — ручная смена статуса. Раньше
   только обновлял колонку ``status`` без вызова финансовых flow, и деньги в
   ESCROW залипали. Теперь:
     • ``→ SUCCESS``  — ESCROW трейдера → WORK мерчанта (минус system fee).
     • ``→ CANCELED`` — ESCROW трейдера → WORK трейдера (для PAYIN).
     • Терминал → что-либо ⇒ 409.
2. Лимиты реквизита: ``reset_enabled`` (единый toggle), ``last_reset_at``,
   ``active_amount`` (in-flight orders).
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _wait_for_status(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    expected_statuses: list[str],
    timeout: float = 12.0,
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
            if last.get("status") in expected_statuses:
                return last
        await asyncio.sleep(interval)
    return last


async def _create_pending_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    *,
    amount: float = 1000.0,
) -> dict:
    """Создаёт payin-ордер и ждёт перехода в pending. Если pooling не нашёл
    реквизит — пропускает тест."""
    create = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": amount,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": f"e2e_admpatch_{rand_suffix()}",
            "issue_requisite_async": False,
        },
    )
    assert create.status_code == 201, f"Order creation failed: {create.text}"
    order = create.json()
    state = await _wait_for_status(
        http, merchant, order["id"], ["pending", "failed", "canceled"], timeout=10.0
    )
    if state.get("status") != "pending":
        pytest.skip(f"Order didn't reach pending: {state.get('status')}")
    return order


async def _trader_balances(
    http: httpx.AsyncClient, trader_user: TestUser
) -> dict[tuple[str, str], float]:
    resp = await http.get(
        "/api/v1/finances/my-balances", headers=trader_user.auth()
    )
    resp.raise_for_status()
    return {(b["type"], b["currency"]): float(b["amount"]) for b in resp.json()}


async def _merchant_balances(
    http: httpx.AsyncClient, admin: TestUser, merchant_id: int
) -> dict[tuple[str, str], float]:
    resp = await http.get(
        "/api/v1/finances/balances", headers=admin.auth(), params={"limit": 2000}
    )
    resp.raise_for_status()
    out: dict[tuple[str, str], float] = {}
    for b in resp.json():
        if b.get("merchant_id") == merchant_id:
            out[(b["type"], b["currency"])] = float(b["amount"])
    return out


# ---------------------------------------------------------------------------
# 1. Admin PATCH /orders/{id} — status flow
# ---------------------------------------------------------------------------

async def test_admin_patch_to_success_moves_escrow_to_merchant(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Админ переводит pending → success. Деньги:
       • из ESCROW трейдера → WORK мерчанта (минус system fee);
       • колонка `confirmed_at` заполняется.
    """
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        order = await _create_pending_order(http, merchant, amount=1500.0)
        order_uuid = order["id"]

        before_trader = await _trader_balances(http, trader.user)
        before_merchant = await _merchant_balances(http, admin, merchant.id)

        # Найти числовой order.id (PATCH идёт по int id, не uuid)
        order_state = (
            await http.get(
                f"/api/merchant/v1/orders/{order_uuid}", headers=merchant.headers()
            )
        ).json()
        debug = (
            await http.get(
                f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth()
            )
        ).json()
        order_int_id = (debug.get("order") or {}).get("id") or order_state.get("id_int")
        assert isinstance(order_int_id, int), f"can't resolve numeric order id: {debug}"

        amount_usdt = Decimal(str(order_state.get("amount_usdt") or 0))
        fee_usdt = Decimal(str(order_state.get("fee_usdt") or 0))
        net_usdt = amount_usdt - fee_usdt

        patch = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "success", "reason": "e2e admin success"},
        )
        assert patch.status_code == 200, patch.text
        body = patch.json()
        assert body["status"] == "success"
        assert body.get("confirmed_at"), "confirmed_at must be set on SUCCESS"

        # Денормализованный финансовый снапшот присутствует в админском ответе.
        assert isinstance(body.get("financials"), dict), f"financials missing: {body}"
        assert body["financials"].get("settled") is True
        assert "teamlead_reward_usdt" in body and "platform_profit_usdt" in body
        # platform_profit = fee − trader_fee − teamlead (без тимлидов = fee − trader_fee).
        assert Decimal(str(body["platform_profit_usdt"])) >= 0

        # Wait for celery callback ripple — balance writes are sync inside the
        # PATCH txn but cache/replicas may need a moment.
        await asyncio.sleep(0.5)

        after_trader = await _trader_balances(http, trader.user)
        after_merchant = await _merchant_balances(http, admin, merchant.id)

        # Trader: ESCROW падает на amount_usdt. Допуск 0.01 USDT гасит
        # разницу между float-представлением балансов с API и Decimal.
        trader_escrow_delta = (
            after_trader.get(("escrow", "USDT"), 0.0)
            - before_trader.get(("escrow", "USDT"), 0.0)
        )
        assert abs(Decimal(str(trader_escrow_delta)) + amount_usdt) <= Decimal("0.01"), (
            f"trader escrow should drop by {amount_usdt}, got Δ={trader_escrow_delta}"
        )

        # Merchant: WORK поднимается на net_usdt (amount - system_fee).
        merchant_work_delta = (
            after_merchant.get(("work", "USDT"), 0.0)
            - before_merchant.get(("work", "USDT"), 0.0)
        )
        assert abs(Decimal(str(merchant_work_delta)) - net_usdt) <= Decimal("0.01"), (
            f"merchant work should grow by {net_usdt}, got Δ={merchant_work_delta}"
        )
    finally:
        await restore_requisites(http, admin, disabled_reqs)


async def test_admin_patch_to_canceled_returns_escrow_to_trader(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Админ переводит pending → canceled. Деньги ESCROW → WORK трейдера."""
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        order = await _create_pending_order(http, merchant, amount=2000.0)
        order_uuid = order["id"]

        before = await _trader_balances(http, trader.user)
        before_merchant = await _merchant_balances(http, admin, merchant.id)

        order_state = (
            await http.get(
                f"/api/merchant/v1/orders/{order_uuid}", headers=merchant.headers()
            )
        ).json()
        debug = (
            await http.get(
                f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth()
            )
        ).json()
        order_int_id = (debug.get("order") or {}).get("id")
        assert isinstance(order_int_id, int)

        amount_usdt = Decimal(str(order_state.get("amount_usdt") or 0))

        patch = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "canceled", "reason": "e2e admin cancel"},
        )
        assert patch.status_code == 200, patch.text
        body = patch.json()
        assert body["status"] == "canceled"
        assert body.get("rejected_at"), "rejected_at must be set on CANCELED"
        assert body.get("rejection_reason")

        await asyncio.sleep(0.5)
        after = await _trader_balances(http, trader.user)
        after_merchant = await _merchant_balances(http, admin, merchant.id)

        # Trader ESCROW − amount, WORK + amount; net change ≈ 0.
        escrow_delta = (
            after.get(("escrow", "USDT"), 0.0)
            - before.get(("escrow", "USDT"), 0.0)
        )
        work_delta = (
            after.get(("work", "USDT"), 0.0) - before.get(("work", "USDT"), 0.0)
        )
        assert abs(Decimal(str(escrow_delta)) + amount_usdt) <= Decimal("0.01"), (
            f"trader escrow Δ wrong: {escrow_delta}"
        )
        assert abs(Decimal(str(work_delta)) - amount_usdt) <= Decimal("0.01"), (
            f"trader work Δ wrong: {work_delta}"
        )

        # Merchant balance НЕ должен меняться.
        m_work_delta = (
            after_merchant.get(("work", "USDT"), 0.0)
            - before_merchant.get(("work", "USDT"), 0.0)
        )
        assert abs(m_work_delta) < 0.001, (
            f"merchant work shouldn't change on cancel: Δ={m_work_delta}"
        )
    finally:
        await restore_requisites(http, admin, disabled_reqs)


async def test_admin_patch_success_then_refunded_unwinds_to_trader(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Админ: pending → success → REFUNDED. Полный откат «как будто сделки не
    было»: расчётка успеха реверсится и залог возвращается трейдеру (ESCROW →
    WORK), мерчант — без чистого изменения. Возврат может делать только админ."""
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        order = await _create_pending_order(http, merchant, amount=2000.0)
        order_uuid = order["id"]

        before = await _trader_balances(http, trader.user)
        before_merchant = await _merchant_balances(http, admin, merchant.id)

        order_state = (
            await http.get(
                f"/api/merchant/v1/orders/{order_uuid}", headers=merchant.headers()
            )
        ).json()
        debug = (
            await http.get(f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth())
        ).json()
        order_int_id = (debug.get("order") or {}).get("id")
        assert isinstance(order_int_id, int)
        amount_usdt = Decimal(str(order_state.get("amount_usdt") or 0))

        # 1. pending → success (escrow settles to merchant).
        ok = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "success", "reason": "e2e admin success"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "success"

        # 2. success → REFUNDED (full unwind back to the trader).
        patch = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "refunded", "reason": "e2e admin refund"},
        )
        if patch.status_code in (400, 409, 422):
            pytest.skip(
                "REFUNDED admin override ещё не задеплоен на dev (вернулось 4xx)."
            )
        assert patch.status_code == 200, patch.text
        body = patch.json()
        assert body["status"] == "refunded"
        assert body.get("rejected_at"), "rejected_at must be set on REFUNDED"

        await asyncio.sleep(0.5)
        after = await _trader_balances(http, trader.user)
        after_merchant = await _merchant_balances(http, admin, merchant.id)

        # Net over the whole success→refund roundtrip: collateral lands back in
        # the trader's WORK (ESCROW −amount, WORK +amount) — as if it never
        # happened. (Same end-shape as a plain cancel.)
        escrow_delta = (
            after.get(("escrow", "USDT"), 0.0) - before.get(("escrow", "USDT"), 0.0)
        )
        work_delta = (
            after.get(("work", "USDT"), 0.0) - before.get(("work", "USDT"), 0.0)
        )
        assert abs(Decimal(str(escrow_delta)) + amount_usdt) <= Decimal("0.01"), (
            f"trader escrow Δ wrong after refund: {escrow_delta}"
        )
        assert abs(Decimal(str(work_delta)) - amount_usdt) <= Decimal("0.01"), (
            f"trader work Δ wrong after refund: {work_delta}"
        )

        # Merchant net change over success+refund ≈ 0 (gained on success, lost on refund).
        m_work_delta = (
            after_merchant.get(("work", "USDT"), 0.0)
            - before_merchant.get(("work", "USDT"), 0.0)
        )
        assert abs(m_work_delta) < 0.01, (
            f"merchant work should net to ~0 after success→refund: Δ={m_work_delta}"
        )
    finally:
        await restore_requisites(http, admin, disabled_reqs)


async def test_admin_patch_finalized_order_only_to_terminal(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """После SUCCESS: возврат в АКТИВНЫЙ статус запрещён (400/conflict — админ
    может ставить только терминальные), а терминал→терминал (success→canceled)
    РАЗРЕШЁН ручным оверрайдом админа — он разворачивает сеттл (без диспута)."""
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        order = await _create_pending_order(http, merchant, amount=500.0)
        order_uuid = order["id"]
        debug = (
            await http.get(
                f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth()
            )
        ).json()
        order_int_id = (debug.get("order") or {}).get("id")

        ok = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "success"},
        )
        assert ok.status_code == 200

        # Возврат в активный (pending) → 400 conflict: админ ставит только
        # терминальные статусы (у ре-активации нет осмысленной денежной семантики).
        retry = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "pending"},
        )
        assert retry.status_code == 400 and retry.json()["error"]["code"] == "conflict", retry.text

        # Терминал → терминал (success → canceled): ручной оверрайд админа РАЗРЕШЁН
        # (сеттл разворачивается, залог возвращается трейдеру).
        retry2 = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "canceled"},
        )
        assert retry2.status_code == 200, retry2.text
        assert retry2.json()["status"] == "canceled"
    finally:
        await restore_requisites(http, admin, disabled_reqs)


async def test_admin_force_failed_to_success_settles_from_trader_work(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Ручной оверрайд админа: НЕуспешный ордер (failed) → success без диспута.
    Залог трейдера, разморожённый в WORK после fail, ре-фризится и сеттлится
    мерчанту (минус system fee)."""
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        order = await _create_pending_order(http, merchant, amount=1500.0)
        order_uuid = order["id"]
        order_state = (
            await http.get(f"/api/merchant/v1/orders/{order_uuid}", headers=merchant.headers())
        ).json()
        debug = (
            await http.get(f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth())
        ).json()
        order_int_id = (debug.get("order") or {}).get("id")
        assert isinstance(order_int_id, int), f"can't resolve numeric order id: {debug}"
        amount_usdt = Decimal(str(order_state.get("amount_usdt") or 0))
        net_usdt = amount_usdt - Decimal(str(order_state.get("fee_usdt") or 0))

        # pending → failed (escrow трейдера → его WORK)
        fail = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "failed", "reason": "e2e fail"},
        )
        assert fail.status_code == 200, fail.text
        await asyncio.sleep(0.5)
        before_merchant = await _merchant_balances(http, admin, merchant.id)

        # failed → success (force): ре-фриз WORK→ESCROW + сеттл мерчанту
        succ = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "success", "reason": "e2e late payment"},
        )
        assert succ.status_code == 200, succ.text
        assert succ.json()["status"] == "success"
        await asyncio.sleep(0.5)

        after_merchant = await _merchant_balances(http, admin, merchant.id)
        merchant_work_delta = (
            after_merchant.get(("work", "USDT"), 0.0)
            - before_merchant.get(("work", "USDT"), 0.0)
        )
        assert abs(Decimal(str(merchant_work_delta)) - net_usdt) <= Decimal("0.01"), (
            f"merchant work should grow by net {net_usdt} on force failed→success, got Δ={merchant_work_delta}"
        )
    finally:
        await restore_requisites(http, admin, disabled_reqs)


async def test_trader_settle_failed_order_to_success(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Трейдер сам проводит СВОЙ неуспешный ордер в успех
    (POST /api/v1/orders/{id}/settle): его залог уходит мерчанту. Повторный
    вызов на уже-успешном ордере отвергается (не failed/canceled)."""
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        order = await _create_pending_order(http, merchant, amount=1500.0)
        order_uuid = order["id"]
        order_state = (
            await http.get(f"/api/merchant/v1/orders/{order_uuid}", headers=merchant.headers())
        ).json()
        debug = (
            await http.get(f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth())
        ).json()
        order_int_id = (debug.get("order") or {}).get("id")
        assert isinstance(order_int_id, int), f"can't resolve numeric order id: {debug}"
        net_usdt = (
            Decimal(str(order_state.get("amount_usdt") or 0))
            - Decimal(str(order_state.get("fee_usdt") or 0))
        )

        # pending → failed (admin), затем трейдер проводит в success.
        fail = await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "failed", "reason": "e2e fail"},
        )
        assert fail.status_code == 200, fail.text
        await asyncio.sleep(0.5)
        before_merchant = await _merchant_balances(http, admin, merchant.id)

        settle = await http.post(
            f"/api/v1/orders/{order_int_id}/settle", headers=trader.user.auth()
        )
        assert settle.status_code == 200, settle.text
        assert settle.json()["status"] == "success"
        await asyncio.sleep(0.5)

        after_merchant = await _merchant_balances(http, admin, merchant.id)
        merchant_work_delta = (
            after_merchant.get(("work", "USDT"), 0.0)
            - before_merchant.get(("work", "USDT"), 0.0)
        )
        assert abs(Decimal(str(merchant_work_delta)) - net_usdt) <= Decimal("0.01"), (
            f"merchant work should grow by net {net_usdt} on trader settle, got Δ={merchant_work_delta}"
        )

        # Повторный settle на уже-успешном → отказ (только из failed/canceled).
        again = await http.post(
            f"/api/v1/orders/{order_int_id}/settle", headers=trader.user.auth()
        )
        assert again.status_code in (400, 409), again.text
    finally:
        await restore_requisites(http, admin, disabled_reqs)


async def test_admin_change_amount_only_during_active_dispute(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Сумму ордера можно менять ТОЛЬКО при активном диспуте: на PENDING — 400
    conflict; на DISPUTED — разрешено, amount_usdt пересчитывается (1000→1500)."""
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        order = await _create_pending_order(http, merchant, amount=1000.0)
        order_uuid = order["id"]
        order_state = (
            await http.get(f"/api/merchant/v1/orders/{order_uuid}", headers=merchant.headers())
        ).json()
        debug = (
            await http.get(f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth())
        ).json()
        order_int_id = (debug.get("order") or {}).get("id")
        assert isinstance(order_int_id, int), f"can't resolve numeric order id: {debug}"
        orig_amount_usdt = Decimal(str(order_state.get("amount_usdt") or 0))

        # PENDING (активный, без диспута) → смена суммы отвергается.
        on_pending = await http.patch(
            f"/api/v1/orders/{order_int_id}", headers=admin.auth(), json={"amount": 1500.0}
        )
        assert (
            on_pending.status_code == 400
            and on_pending.json()["error"]["code"] == "conflict"
        ), on_pending.text

        # Открыть диспут (merchant) → DISPUTED.
        disp = await http.post(
            f"/api/merchant/v1/disputes/by-order/{order_uuid}",
            headers=merchant.headers(),
            data={"reason": "invalid_sum"},
        )
        if disp.status_code == 422:
            pytest.skip("dispute contract not deployed on dev")
        assert disp.status_code in (200, 201), disp.text
        await asyncio.sleep(0.5)

        # На DISPUTED → смена суммы разрешена, amount_usdt пересчитан (~×1.5).
        patch = await http.patch(
            f"/api/v1/orders/{order_int_id}", headers=admin.auth(), json={"amount": 1500.0}
        )
        assert patch.status_code == 200, patch.text
        body = patch.json()
        assert float(body["amount"]) == 1500.0
        new_amount_usdt = Decimal(str(body.get("amount_usdt") or 0))
        if orig_amount_usdt > 0:
            assert abs(new_amount_usdt - orig_amount_usdt * Decimal("1.5")) <= Decimal("0.05"), (
                f"amount_usdt should recompute to ×1.5 ({orig_amount_usdt}→{new_amount_usdt})"
            )
    finally:
        await restore_requisites(http, admin, disabled_reqs)


# ---------------------------------------------------------------------------
# 2. Requisite limits — reset_enabled, last_reset_at, active_amount
# ---------------------------------------------------------------------------

async def test_requisite_limits_default_reset_disabled(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    """Свежий реквизит без явного флага → reset_enabled=false и
    last_reset_at=None (общий лимит — дефолтное поведение)."""
    payment_options = (
        await http.get("/api/v1/payments/options", headers=trader_user.auth())
    ).json()
    sbp_opt = next(
        o for o in payment_options
        if o.get("is_active") and "sbp" in (o.get("supported_methods") or [])
    )

    create = await http.post(
        "/api/v1/requisites/me",
        headers=trader_user.auth(),
        json={
            "nickname": f"e2e_reset_off_{rand_suffix()}",
            "payment_option_id": sbp_opt["id"],
            "account_number": "40817810099910" + rand_suffix(6),
            "account_holder": "E2E Reset Off",
            "payment_method": "sbp",
            "limits": {"limit_daily": 500000, "limit_monthly": 500000},
        },
    )
    assert create.status_code == 201, create.text
    req_id = create.json()["id"]

    detail = (
        await http.get(f"/api/v1/requisites/me/{req_id}", headers=trader_user.auth())
    ).json()
    limits = detail["limits"]
    assert limits["reset_enabled"] is False
    assert limits.get("last_reset_at") is None

    # Cleanup
    await http.delete(f"/api/v1/requisites/me/{req_id}", headers=trader_user.auth())


async def test_requisite_limits_reset_toggle_stamps_last_reset_at(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    """Включение reset_enabled через PATCH → last_reset_at становится «сейчас»."""
    payment_options = (
        await http.get("/api/v1/payments/options", headers=trader_user.auth())
    ).json()
    sbp_opt = next(
        o for o in payment_options
        if o.get("is_active") and "sbp" in (o.get("supported_methods") or [])
    )

    create = await http.post(
        "/api/v1/requisites/me",
        headers=trader_user.auth(),
        json={
            "nickname": f"e2e_reset_on_{rand_suffix()}",
            "payment_option_id": sbp_opt["id"],
            "account_number": "40817810099910" + rand_suffix(6),
            "account_holder": "E2E Reset On",
            "payment_method": "sbp",
            "limits": {"limit_daily": 100000, "limit_monthly": 1000000},
        },
    )
    req_id = create.json()["id"]

    try:
        before = (
            await http.get(f"/api/v1/requisites/me/{req_id}", headers=trader_user.auth())
        ).json()
        assert before["limits"]["reset_enabled"] is False
        assert before["limits"]["last_reset_at"] is None

        patch = await http.patch(
            f"/api/v1/requisites/me/{req_id}",
            headers=trader_user.auth(),
            json={"limits": {"reset_enabled": True}},
        )
        assert patch.status_code == 200, patch.text
        body = patch.json()
        assert body["limits"]["reset_enabled"] is True
        assert body["limits"].get("last_reset_at"), \
            "last_reset_at должен заполняться при включении флага"

        # Включение flag-а второй раз без изменения значения не должно
        # перезаписывать last_reset_at (False→True trigger only).
        first_ts = body["limits"]["last_reset_at"]
        await asyncio.sleep(1.1)
        patch2 = await http.patch(
            f"/api/v1/requisites/me/{req_id}",
            headers=trader_user.auth(),
            json={"limits": {"reset_enabled": True, "limit_daily": 200000}},
        )
        body2 = patch2.json()
        assert body2["limits"]["last_reset_at"] == first_ts, \
            "повторный PATCH с тем же reset_enabled не должен сдвигать timestamp"
    finally:
        await http.delete(f"/api/v1/requisites/me/{req_id}", headers=trader_user.auth())


async def test_requisite_active_amount_reflects_pending_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Создаём pending-ордер на реквизит трейдера, дёргаем
    /api/v1/requisites/me — поле active_amount должно равняться сумме ордера
    (в фиате реквизита)."""
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        before = (
            await http.get(
                "/api/v1/requisites/me", headers=trader.user.auth()
            )
        ).json()
        my = next(r for r in before if r["id"] == trader.requisite_id)
        active_before = float(my["limits"].get("active_amount") or 0)

        order = await _create_pending_order(http, merchant, amount=1300.0)
        order_uuid = order["id"]

        listing = (
            await http.get(
                "/api/v1/requisites/me", headers=trader.user.auth()
            )
        ).json()
        my_after = next(r for r in listing if r["id"] == trader.requisite_id)
        active_after = float(my_after["limits"].get("active_amount") or 0)

        assert active_after - active_before == pytest.approx(1300.0, abs=0.01), (
            f"active_amount должен подняться на 1300, got Δ={active_after - active_before}"
        )

        # После cancel'а должен вернуться в исходное.
        debug = (
            await http.get(
                f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth()
            )
        ).json()
        order_int_id = (debug.get("order") or {}).get("id")
        await http.patch(
            f"/api/v1/orders/{order_int_id}",
            headers=admin.auth(),
            json={"status": "canceled", "reason": "e2e cleanup"},
        )

        listing2 = (
            await http.get(
                "/api/v1/requisites/me", headers=trader.user.auth()
            )
        ).json()
        my_final = next(r for r in listing2 if r["id"] == trader.requisite_id)
        active_final = float(my_final["limits"].get("active_amount") or 0)
        assert abs(active_final - active_before) < 0.01, (
            f"active_amount не вернулся: before={active_before}, final={active_final}"
        )
    finally:
        await restore_requisites(http, admin, disabled_reqs)


async def test_requisite_general_limit_mirrors_into_daily_and_monthly(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    """В UI «общий лимит» при reset_enabled=false фронт зеркалит значение
    в limit_daily и limit_monthly. Проверяем, что бэк сохраняет их как одно
    и то же число (это ключевая часть нового UX)."""
    payment_options = (
        await http.get("/api/v1/payments/options", headers=trader_user.auth())
    ).json()
    sbp_opt = next(
        o for o in payment_options
        if o.get("is_active") and "sbp" in (o.get("supported_methods") or [])
    )

    create = await http.post(
        "/api/v1/requisites/me",
        headers=trader_user.auth(),
        json={
            "nickname": f"e2e_gen_limit_{rand_suffix()}",
            "payment_option_id": sbp_opt["id"],
            "account_number": "40817810099910" + rand_suffix(6),
            "account_holder": "E2E Gen Limit",
            "payment_method": "sbp",
            # mirror — exactly what the frontend now sends when reset_enabled=false
            "limits": {
                "reset_enabled": False,
                "limit_daily": 750000,
                "limit_monthly": 750000,
            },
        },
    )
    req_id = create.json()["id"]

    try:
        detail = (
            await http.get(
                f"/api/v1/requisites/me/{req_id}", headers=trader_user.auth()
            )
        ).json()
        limits = detail["limits"]
        assert limits["reset_enabled"] is False
        assert float(limits["limit_daily"]) == 750000
        assert float(limits["limit_monthly"]) == 750000
    finally:
        await http.delete(f"/api/v1/requisites/me/{req_id}", headers=trader_user.auth())
