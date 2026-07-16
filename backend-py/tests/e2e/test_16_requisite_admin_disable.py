"""
E2E: admin-флаг `is_active` на реквизите блокирует маршрутизацию.

Запоминаем разницу между двумя выключателями:
  * `status: enabled / disabled / blocked / archived` — управляет трейдер
    через self-service эндпоинты `/requisites/me/<id>/disable` etc.
  * `is_active: bool` — отдельный admin-only флаг, который трейдер вообще
    не видит и не может тронуть. Pooling-сервис явно проверяет оба
    (см. pooling/service.py:115 `Requisite.is_active == True`).

Тест проверяет, что админский is_active=false полностью выкидывает реквизит
из маршрутизации, даже если status=enabled.
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
            "internalId": f"e2e_admin_off_{rand_suffix()}",
            "issue_requisite_async": False,
        },
    )


async def _set_active(
    http: httpx.AsyncClient,
    admin: TestUser,
    requisite_id: int,
    *,
    is_active: bool,
) -> None:
    resp = await http.patch(
        f"/api/v1/requisites/{requisite_id}",
        json={"is_active": is_active},
        headers=admin.auth(),
    )
    assert resp.status_code == 200, (
        f"PATCH requisite is_active failed: {resp.status_code} {resp.text}"
    )


async def test_admin_is_active_false_blocks_routing(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,  # noqa: ARG001 — make sure group binding exists
) -> None:
    """
    1. Изолируем все прочие реквизиты (только наш может выдаться).
    2. Baseline: создаём payin → 201 (реквизит выдаётся).
    3. Админ ставит is_active=false на нашем реквизите.
    4. status в ответе должен остаться enabled — флаги независимы.
    5. Создаём payin → 404 (pooling его отфильтровал).
    6. Возвращаем is_active=true → payin снова работает.
    """
    disabled = await isolate_requisites(http, admin, trader.requisite_id)

    try:
        # 2. Baseline: pooling видит наш реквизит.
        ok = await _create_payin(http, merchant)
        assert ok.status_code == 201, (
            f"Baseline payin must succeed; got {ok.status_code}: {ok.text}"
        )
        order_uuid = ok.json()["id"]
        await http.post(
            f"/api/merchant/v1/orders/{order_uuid}/cancel",
            headers=merchant.headers(),
        )

        # 3. Снимаем admin-flag.
        await _set_active(http, admin, trader.requisite_id, is_active=False)
        await asyncio.sleep(0.3)

        # 4. Проверяем: status НЕ изменился, изменился только is_active.
        detail = await http.get(
            f"/api/v1/requisites/{trader.requisite_id}", headers=admin.auth()
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["is_active"] is False, body
        # status остаётся 'enabled' — admin не трогает self-service статус.
        assert body["status"] == "enabled", (
            f"Admin is_active toggle must NOT change trader-managed status: {body}"
        )

        # 5. Pooling должен отфильтровать реквизит.
        blocked = await _create_payin(http, merchant)
        assert blocked.status_code in (400, 404), (
            f"With is_active=false, payin must be rejected; "
            f"got {blocked.status_code}: {blocked.text}"
        )

        # 6. Возвращаем флаг.
        await _set_active(http, admin, trader.requisite_id, is_active=True)
        await asyncio.sleep(0.3)

        restored = await _create_payin(http, merchant)
        assert restored.status_code == 201, (
            f"After restoring is_active=true, payin must succeed; "
            f"got {restored.status_code}: {restored.text}"
        )
        await http.post(
            f"/api/merchant/v1/orders/{restored.json()['id']}/cancel",
            headers=merchant.headers(),
        )
    finally:
        # Гарантированно вернём is_active=True даже если что-то сломалось.
        try:
            await _set_active(http, admin, trader.requisite_id, is_active=True)
        except Exception:
            pass
        await restore_requisites(http, admin, disabled)


async def test_admin_is_active_independent_of_trader_status(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
) -> None:
    """Симметричная проверка: трейдерский `status=disabled` не сбрасывает
    `is_active` (флаги ортогональны). Иначе админ потерял бы свой
    «жёсткий» оверрайд, как только трейдер сделает disable/enable."""
    # Сделаем status=disabled от трейдера
    off = await http.post(
        f"/api/v1/requisites/me/{trader.requisite_id}/disable",
        headers=trader.user.auth(),
    )
    assert off.status_code == 200, off.text
    body = off.json()
    assert body["status"] == "disabled"
    assert body["is_active"] is True, (
        f"Trader's disable must NOT clear admin's is_active flag: {body}"
    )

    # Восстановим в enabled — admin is_active по-прежнему True.
    on = await http.post(
        f"/api/v1/requisites/me/{trader.requisite_id}/enable",
        headers=trader.user.auth(),
    )
    assert on.status_code == 200, on.text
    body2 = on.json()
    assert body2["status"] == "enabled"
    assert body2["is_active"] is True
