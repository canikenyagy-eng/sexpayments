"""
E2E: маршрутизация через trader_group.

`TraderGroup` сейчас не имеет флага `is_active` — выключение группы выражается
в очистке её участников (удалили мерча или трейдера из группы → группа
перестаёт «работать» как канал маршрутизации).

Этот файл закрывает дыру:
  * Базовый кейс: трейдер с реквизитом, связанные через группу с мерчантом,
    получают payin → реквизит выдаётся, ордер уходит в pending.
  * После удаления мерчанта из группы (и при условии, что нет других
    привязок и accept_all_merchants=false на трейдере), pooling больше
    не может выбрать ни одного реквизита для этого мерчанта → API
    возвращает 404 NotFoundException.
  * После восстановления связи маршрутизация снова работает.

Проверяется реальный routing-эффект, не просто запись в БД.
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
    http: httpx.AsyncClient, merchant: TestMerchant, *, amount: float = 1000.0
) -> httpx.Response:
    return await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": amount,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": f"e2e_grp_{rand_suffix()}",
            "issue_requisite_async": False,
        },
    )


async def _cancel_if_pending(
    http: httpx.AsyncClient, merchant: TestMerchant, order_uuid: str
) -> None:
    # Best-effort cleanup so the trader requisite's active_amount doesn't
    # accumulate across the rest of the suite.
    try:
        await http.post(
            f"/api/merchant/v1/orders/{order_uuid}/cancel",
            headers=merchant.headers(),
        )
    except Exception:
        pass


async def _trader_snapshot(http: httpx.AsyncClient, admin: TestUser, trader_id: int) -> dict:
    """Pull the trader's current binding state so the test can restore it."""
    resp = await http.get(f"/api/v1/traders/{trader_id}", headers=admin.auth())
    resp.raise_for_status()
    snap = resp.json()
    return {
        "merchants": sorted(m["id"] for m in (snap.get("merchants") or [])),
        "groups": sorted(g["id"] for g in (snap.get("groups") or [])),
        "accept_all_merchants": bool(snap.get("accept_all_merchants", False)),
    }


async def _restore_trader(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader_id: int,
    snapshot: dict,
) -> None:
    await http.patch(
        f"/api/v1/traders/{trader_id}",
        json={
            "merchant_ids": snapshot["merchants"],
            "group_ids": snapshot["groups"],
            "accept_all_merchants": snapshot["accept_all_merchants"],
        },
        headers=admin.auth(),
    )


async def test_group_removal_blocks_pooling_routing(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Удаление мерчанта из группы трейдера должно блокировать маршрутизацию.

    Шаги:
    1. Дисэйблим все остальные реквизиты (isolate) — гарантируем, что
       только этот трейдер может выдать реквизит.
    2. Сохраняем текущее состояние привязок трейдера + флаг accept_all.
    3. Снимаем у трейдера accept_all_merchants и любые прямые привязки
       к этому мерчанту — оставляем ТОЛЬКО маршрут через `trader_group`.
    4. Создаём payin → должен сработать (через группу).
    5. Удаляем мерчанта из группы → создаём ещё payin → 404 / not_found.
    6. Возвращаем мерчанта в группу → payin снова работает.
    """
    snapshot = await _trader_snapshot(http, admin, trader.id)
    disabled = await isolate_requisites(http, admin, trader.requisite_id)

    try:
        # 1. Force routing to depend ONLY on the group membership.
        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={
                "merchant_ids": [],
                "accept_all_merchants": False,
                # Keep group membership intact — that's the only channel.
                "group_ids": [trader_group],
            },
            headers=admin.auth(),
        )
        # Cache settle for the binding change.
        await asyncio.sleep(0.5)

        # 2. Baseline: routing works via the group.
        ok_resp = await _create_payin(http, merchant)
        assert ok_resp.status_code == 201, (
            f"Baseline payin via group should succeed, got {ok_resp.status_code}: "
            f"{ok_resp.text}"
        )
        await _cancel_if_pending(http, merchant, ok_resp.json()["id"])

        # 3. Drop the merchant from the group → no other route exists.
        rm_resp = await http.delete(
            f"/api/v1/traders/groups/{trader_group}/merchants/{merchant.id}",
            headers=admin.auth(),
        )
        assert rm_resp.status_code in (200, 204), rm_resp.text
        await asyncio.sleep(0.5)

        # 4. Payin must now fail — no requisite reachable for this merchant.
        blocked = await _create_payin(http, merchant)
        # Project convention: NotFoundException maps to HTTP 404 (or
        # 400 with code='not_found' on some deploys).
        assert blocked.status_code in (400, 404), (
            f"With merchant removed from the only routing group, payin "
            f"should be rejected; got {blocked.status_code}: {blocked.text}"
        )

        # 5. Re-add merchant → routing works again.
        re_add = await http.post(
            f"/api/v1/traders/groups/{trader_group}/merchants/{merchant.id}",
            headers=admin.auth(),
        )
        assert re_add.status_code in (200, 201), re_add.text
        await asyncio.sleep(0.5)

        restored = await _create_payin(http, merchant)
        assert restored.status_code == 201, (
            f"After restoring group membership, payin must succeed; "
            f"got {restored.status_code}: {restored.text}"
        )
        await _cancel_if_pending(http, merchant, restored.json()["id"])
    finally:
        await _restore_trader(http, admin, trader.id, snapshot)
        await restore_requisites(http, admin, disabled)


async def test_group_with_no_traders_blocks_routing(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Симметричный кейс: удаляем трейдера из группы (мерчант остаётся).
    Группа становится «пустой» с точки зрения трейдеров → pooling не находит
    реквизита для этого мерчанта.
    """
    snapshot = await _trader_snapshot(http, admin, trader.id)
    disabled = await isolate_requisites(http, admin, trader.requisite_id)

    try:
        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={
                "merchant_ids": [],
                "accept_all_merchants": False,
                "group_ids": [trader_group],
            },
            headers=admin.auth(),
        )
        await asyncio.sleep(0.5)

        # Drop the trader from the group → group is empty on the trader side.
        rm = await http.delete(
            f"/api/v1/traders/groups/{trader_group}/traders/{trader.id}",
            headers=admin.auth(),
        )
        assert rm.status_code in (200, 204), rm.text
        await asyncio.sleep(0.5)

        blocked = await _create_payin(http, merchant)
        assert blocked.status_code in (400, 404), (
            f"Empty (trader-side) group must not route; "
            f"got {blocked.status_code}: {blocked.text}"
        )
    finally:
        # Put the trader back, then restore the full snapshot.
        await http.post(
            f"/api/v1/traders/groups/{trader_group}/traders/{trader.id}",
            headers=admin.auth(),
        )
        await _restore_trader(http, admin, trader.id, snapshot)
        await restore_requisites(http, admin, disabled)
