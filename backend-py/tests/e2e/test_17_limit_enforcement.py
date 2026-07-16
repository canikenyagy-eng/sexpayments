"""
E2E: пулинг отбрасывает реквизит, не подходящий по лимитам.

Pooling-сервис фильтрует реквизиты тремя независимыми условиями:
  * `limit_min_transaction <= amount`            — мин. чек
  * `limit_max_transaction >= amount`            — макс. чек
  * `current_daily_turnover + active + amount <= limit_daily`
                                                  — дневной cap

Если ни один реквизит не проходит фильтр для конкретного `amount`, payin
получает 404 «No available requisite». Тест ставит лимиты на единственный
доступный реквизит и проверяет каждый из трёх отказов плюс happy-path
посередине.
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


async def _create_payin(
    http: httpx.AsyncClient, merchant: TestMerchant, amount: float
) -> httpx.Response:
    return await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": amount,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": f"e2e_lim_{rand_suffix()}",
            "issue_requisite_async": False,
        },
    )


async def _patch_limits(
    http: httpx.AsyncClient,
    admin: TestUser,
    requisite_id: int,
    *,
    limit_daily: float | None = None,
    limit_monthly: float | None = None,
    limit_min_transaction: float | None = None,
    limit_max_transaction: float | None = None,
    reset_enabled: bool | None = None,
    reset_turnover: bool = False,
) -> None:
    payload: dict = {}
    if limit_daily is not None:
        payload["limit_daily"] = limit_daily
    if limit_monthly is not None:
        payload["limit_monthly"] = limit_monthly
    if limit_min_transaction is not None:
        payload["limit_min_transaction"] = limit_min_transaction
    if limit_max_transaction is not None:
        payload["limit_max_transaction"] = limit_max_transaction
    if reset_enabled is not None:
        payload["reset_enabled"] = reset_enabled
    if reset_turnover:
        payload["current_daily_turnover"] = 0
        payload["current_monthly_turnover"] = 0
    resp = await http.patch(
        f"/api/v1/requisites/{requisite_id}",
        json={"limits": payload},
        headers=admin.auth(),
    )
    assert resp.status_code == 200, (
        f"PATCH limits failed: {resp.status_code} {resp.text}"
    )


async def _cancel_quiet(
    http: httpx.AsyncClient, merchant: TestMerchant, order_uuid: str
) -> None:
    try:
        await http.post(
            f"/api/merchant/v1/orders/{order_uuid}/cancel",
            headers=merchant.headers(),
        )
    except Exception:
        pass


async def _baseline_limits(
    http: httpx.AsyncClient, admin: TestUser, requisite_id: int
) -> dict:
    """Save the current limits so the test can restore them in `finally`."""
    resp = await http.get(
        f"/api/v1/requisites/{requisite_id}", headers=admin.auth()
    )
    resp.raise_for_status()
    body = resp.json()
    limits = body.get("limits") or {}
    return {
        "limit_daily": float(limits.get("limit_daily") or 1_000_000),
        "limit_monthly": float(limits.get("limit_monthly") or 1_000_000),
        "limit_min_transaction": float(limits.get("limit_min_transaction") or 0),
        "limit_max_transaction": float(limits.get("limit_max_transaction") or 1_000_000),
        "reset_enabled": bool(limits.get("reset_enabled", False)),
    }


async def _current_turnover(
    http: httpx.AsyncClient, admin: TestUser, requisite_id: int
) -> tuple[float, float, float]:
    """Read current_daily_turnover / current_monthly_turnover / active_amount
    from the requisite.

    На shared dev-сервере другие тесты могут накопить turnover на том же
    реквизите. Чтобы тесты лимита не зависели от residue, мы строим лимиты
    ОТНОСИТЕЛЬНО уже накопленного значения: ``limit_daily = current + window``.
    """
    resp = await http.get(
        f"/api/v1/requisites/{requisite_id}", headers=admin.auth()
    )
    resp.raise_for_status()
    limits = resp.json().get("limits") or {}
    return (
        float(limits.get("current_daily_turnover") or 0),
        float(limits.get("current_monthly_turnover") or 0),
        float(limits.get("active_amount") or 0),
    )


async def test_amount_below_min_transaction_blocks_routing(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,  # noqa: ARG001
) -> None:
    """min_tx=500 → amount=100 не должен пройти, amount=600 — должен."""
    saved = await _baseline_limits(http, admin, trader.requisite_id)
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        await _patch_limits(
            http, admin, trader.requisite_id,
            limit_min_transaction=500,
            limit_max_transaction=100_000,
            limit_daily=500_000,
            limit_monthly=500_000,
        )
        await asyncio.sleep(0.3)

        below = await _create_payin(http, merchant, amount=100)
        assert below.status_code in (400, 404), (
            f"amount=100 < min_tx=500 must be rejected; got "
            f"{below.status_code}: {below.text}"
        )

        ok = await _create_payin(http, merchant, amount=600)
        assert ok.status_code == 201, (
            f"amount=600 >= min_tx=500 must pass; got "
            f"{ok.status_code}: {ok.text}"
        )
        await _cancel_quiet(http, merchant, ok.json()["id"])
    finally:
        await _patch_limits(http, admin, trader.requisite_id, **saved)
        await restore_requisites(http, admin, disabled)


async def test_amount_above_max_transaction_blocks_routing(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,  # noqa: ARG001
) -> None:
    """max_tx=2000 → amount=3000 не проходит."""
    saved = await _baseline_limits(http, admin, trader.requisite_id)
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        await _patch_limits(
            http, admin, trader.requisite_id,
            limit_min_transaction=0,
            limit_max_transaction=2_000,
            limit_daily=500_000,
            limit_monthly=500_000,
        )
        await asyncio.sleep(0.3)

        above = await _create_payin(http, merchant, amount=3_000)
        assert above.status_code in (400, 404), (
            f"amount=3000 > max_tx=2000 must be rejected; got "
            f"{above.status_code}: {above.text}"
        )

        ok = await _create_payin(http, merchant, amount=1_500)
        assert ok.status_code == 201, (
            f"amount=1500 <= max_tx=2000 must pass; got "
            f"{ok.status_code}: {ok.text}"
        )
        await _cancel_quiet(http, merchant, ok.json()["id"])
    finally:
        await _patch_limits(http, admin, trader.requisite_id, **saved)
        await restore_requisites(http, admin, disabled)


async def test_daily_limit_exceeded_blocks_routing(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,  # noqa: ARG001
) -> None:
    """
    Дневной лимит = 1500. Создаём pending ордер на 1300 (active_amount=1300,
    остаток 200). Следующий ордер на 500 должен быть отвергнут (1300+500 >
    1500). Ордер на 100 должен пройти (1300+100 <= 1500).
    """
    saved = await _baseline_limits(http, admin, trader.requisite_id)
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        # На shared dev-сервере другие тесты могли накопить current_daily_turnover
        # и оставить активные PENDING ордера. Делаем тест адаптивным:
        # лимит = baseline (committed) + active_amount (pending) + WINDOW.
        # Так логика «pending откусывает 1300, остаётся 200» проверяется
        # независимо от residue.
        baseline_daily, baseline_monthly, active_amount = await _current_turnover(
            http, admin, trader.requisite_id,
        )
        WINDOW = 1_500.0
        # `+ active_amount` — поглощает уже-PENDING ордера, которые pooling
        # учтёт в своей формуле `current + pending + new <= limit`.
        daily_limit = baseline_daily + active_amount + WINDOW
        monthly_limit = baseline_monthly + active_amount + WINDOW

        await _patch_limits(
            http, admin, trader.requisite_id,
            limit_min_transaction=0,
            limit_max_transaction=10_000,
            limit_daily=daily_limit,
            limit_monthly=monthly_limit,
            reset_enabled=False,
        )
        await asyncio.sleep(0.3)

        # Открыть окно на 1300 → pending = 1300, оставшееся окно = 200.
        pending = await _create_payin(http, merchant, amount=1_300)
        assert pending.status_code == 201, (
            f"Pending order for amount=1300 must succeed "
            f"(baseline_daily={baseline_daily}, limit_daily={daily_limit}); "
            f"got {pending.status_code}: {pending.text}"
        )
        pending_uuid = pending.json()["id"]

        try:
            # 1300 + 500 = 1800 > WINDOW=1500 → reject.
            over = await _create_payin(http, merchant, amount=500)
            assert over.status_code in (400, 404), (
                f"Daily window exceeded (1300+500>1500) must be rejected; "
                f"got {over.status_code}: {over.text}"
            )

            # 1300 + 100 = 1400 <= 1500 → pass.
            within = await _create_payin(http, merchant, amount=100)
            assert within.status_code == 201, (
                f"Within remaining daily window (1300+100<=1500) must "
                f"succeed; got {within.status_code}: {within.text}"
            )
            await _cancel_quiet(http, merchant, within.json()["id"])
        finally:
            await _cancel_quiet(http, merchant, pending_uuid)
    finally:
        await _patch_limits(http, admin, trader.requisite_id, **saved)
        await restore_requisites(http, admin, disabled)
