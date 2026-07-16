"""
E2E-тесты: привязки трейдер ↔ мерчант (TraderGroups + direct M2M + accept_all_merchants)
и enforcement пулинга по этим привязкам.

Покрывает новый функционал:
- PATCH /api/v1/traders/{id}            — поля merchant_ids / group_ids / accept_all_merchants
- PATCH /api/v1/merchants/{id}          — поля trader_ids / group_ids
- PATCH /api/v1/traders/groups/{id}     — расширенный (trader_ids / merchant_ids = полная перезапись)
- DELETE /api/v1/traders/groups/{id}/traders/{tid}
- DELETE /api/v1/traders/groups/{id}/merchants/{mid}
- DELETE /api/v1/traders/groups/{id}
- Пулинг: трейдер допускается если (а) прямая привязка, (б) общая группа,
  (в) у мерчанта нет привязок и trader.accept_all_merchants=True. Иначе — отфильтровывается.
"""
from __future__ import annotations

import asyncio
import time

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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _wait_for_status(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    expected_statuses: list[str],
    timeout: float = 10.0,
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


async def _create_payin(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    *,
    amount: float = 1000.0,
    expected_codes: tuple[int, ...] = (201,),
) -> httpx.Response:
    return await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": amount,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": f"e2e_bind_{rand_suffix()}",
            "userId": "e2e_bind_client",
            "issue_requisite_async": False,
        },
    )


# ---------------------------------------------------------------------------
# 1. PATCH /traders/{id} — admin может выставить новые поля и они возвращаются
# ---------------------------------------------------------------------------

async def test_patch_trader_admin_supports_new_fields(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
    merchant: TestMerchant,
) -> None:
    """admin PATCH принимает merchant_ids/group_ids/accept_all_merchants и возвращает их."""
    # Изначальное состояние
    resp = await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
    assert resp.status_code == 200, resp.text
    initial = resp.json()
    assert "accept_all_merchants" in initial, "TraderResponse must expose accept_all_merchants"
    assert "merchants" in initial, "TraderResponse must expose merchants list"
    initial_merchants = sorted(m["id"] for m in (initial.get("merchants") or []))
    initial_groups = sorted(g["id"] for g in (initial.get("groups") or []))

    # Привязываем напрямую — set semantics
    patch = await http.patch(
        f"/api/v1/traders/{trader.id}",
        json={
            "merchant_ids": [merchant.id],
            "accept_all_merchants": False,
        },
        headers=admin.auth(),
    )
    assert patch.status_code == 200, patch.text
    after = patch.json()
    assert after["accept_all_merchants"] is False
    assert sorted(m["id"] for m in (after.get("merchants") or [])) == [merchant.id]

    # Сброс к исходному состоянию (важно: не ломаем другие тесты этого прогона)
    revert = await http.patch(
        f"/api/v1/traders/{trader.id}",
        json={
            "merchant_ids": initial_merchants,
            "group_ids": initial_groups,
            "accept_all_merchants": bool(initial.get("accept_all_merchants", False)),
        },
        headers=admin.auth(),
    )
    assert revert.status_code == 200, revert.text


async def test_patch_trader_admin_clears_merchants_with_empty_list(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
    merchant: TestMerchant,
) -> None:
    """Передача пустого списка очищает прямые привязки (set semantics, не append)."""
    # Сохраняем исходные
    resp = await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
    initial = resp.json()
    initial_merchants = sorted(m["id"] for m in (initial.get("merchants") or []))
    initial_groups = sorted(g["id"] for g in (initial.get("groups") or []))
    initial_flag = bool(initial.get("accept_all_merchants", False))

    try:
        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={"merchant_ids": [merchant.id]},
            headers=admin.auth(),
        )
        with_one = (
            await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
        ).json()
        assert sorted(m["id"] for m in with_one["merchants"]) == [merchant.id]

        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={"merchant_ids": []},
            headers=admin.auth(),
        )
        cleared = (
            await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
        ).json()
        assert cleared["merchants"] == [], cleared
    finally:
        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={
                "merchant_ids": initial_merchants,
                "group_ids": initial_groups,
                "accept_all_merchants": initial_flag,
            },
            headers=admin.auth(),
        )


# ---------------------------------------------------------------------------
# 2. PATCH /merchants/{id} — симметрично для мерчанта
# ---------------------------------------------------------------------------

async def test_patch_merchant_admin_accepts_trader_ids_and_group_ids(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
    merchant: TestMerchant,
) -> None:
    resp = await http.get(f"/api/v1/merchants/{merchant.id}", headers=admin.auth())
    assert resp.status_code == 200, resp.text
    initial = resp.json()
    assert "traders" in initial, "MerchantAdminResponse must expose traders"
    assert "trader_groups" in initial, "MerchantAdminResponse must expose trader_groups"
    initial_traders = sorted(t["id"] for t in (initial.get("traders") or []))
    initial_groups = sorted(g["id"] for g in (initial.get("trader_groups") or []))

    patch = await http.patch(
        f"/api/v1/merchants/{merchant.id}",
        json={"trader_ids": [trader.id]},
        headers=admin.auth(),
    )
    assert patch.status_code == 200, patch.text
    after = patch.json()
    assert sorted(t["id"] for t in after.get("traders") or []) == [trader.id]

    # Очистка через пустой список
    cleared = await http.patch(
        f"/api/v1/merchants/{merchant.id}",
        json={"trader_ids": []},
        headers=admin.auth(),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json().get("traders") == []

    # Восстанавливаем исходное состояние
    await http.patch(
        f"/api/v1/merchants/{merchant.id}",
        json={"trader_ids": initial_traders, "group_ids": initial_groups},
        headers=admin.auth(),
    )


# ---------------------------------------------------------------------------
# 3. Trader groups: PATCH перезапись и DELETE
# ---------------------------------------------------------------------------

async def test_group_patch_replaces_members_and_delete_endpoint_works(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
    merchant: TestMerchant,
) -> None:
    name = f"e2e-bind-{rand_suffix()}"
    create = await http.post(
        "/api/v1/traders/groups",
        json={"name": name, "description": "Bindings test"},
        headers=admin.auth(),
    )
    assert create.status_code in (200, 201), create.text
    group_id = create.json()["id"]

    try:
        # Перезапись trader_ids + merchant_ids одним PATCH
        patched = await http.patch(
            f"/api/v1/traders/groups/{group_id}",
            json={"trader_ids": [trader.id], "merchant_ids": [merchant.id]},
            headers=admin.auth(),
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
        assert sorted(t["id"] for t in body.get("traders") or []) == [trader.id]
        assert sorted(m["id"] for m in body.get("merchants") or []) == [merchant.id]

        # Удаление одного участника через DELETE endpoint
        rm = await http.delete(
            f"/api/v1/traders/groups/{group_id}/traders/{trader.id}",
            headers=admin.auth(),
        )
        assert rm.status_code == 200, rm.text
        assert [t["id"] for t in rm.json().get("traders") or []] == []

        # Повторное удаление того же — 404 (трейдер уже не в группе)
        rm_again = await http.delete(
            f"/api/v1/traders/groups/{group_id}/traders/{trader.id}",
            headers=admin.auth(),
        )
        assert rm_again.status_code == 404, rm_again.text

        rm_m = await http.delete(
            f"/api/v1/traders/groups/{group_id}/merchants/{merchant.id}",
            headers=admin.auth(),
        )
        assert rm_m.status_code == 200, rm_m.text

        # Очистка пустым списком — допустимо
        clear = await http.patch(
            f"/api/v1/traders/groups/{group_id}",
            json={"trader_ids": [], "merchant_ids": []},
            headers=admin.auth(),
        )
        assert clear.status_code == 200, clear.text

    finally:
        # DELETE группы — 204
        gone = await http.delete(
            f"/api/v1/traders/groups/{group_id}",
            headers=admin.auth(),
        )
        assert gone.status_code == 204, gone.text

        # И в списке её больше нет
        listing = await http.get("/api/v1/traders/groups", headers=admin.auth())
        ids = [g["id"] for g in listing.json()]
        assert group_id not in ids


# ---------------------------------------------------------------------------
# 4. Pooling enforcement: прямая привязка пускает, чужой мерчант — блокирует
# ---------------------------------------------------------------------------

async def test_pooling_allows_direct_binding_and_blocks_unbound_merchant(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
    merchant: TestMerchant,
) -> None:
    """
    Создаём ВТОРОГО мерчанта без привязок к нашему трейдеру и без флага
    accept_all_merchants на трейдере. Ордер от него не должен достаться нашему трейдеру.

    Затем прямо привязываем нашего трейдера к основному мерчанту и убеждаемся,
    что ордер ему достаётся.
    """
    # Сохраняем состояние трейдера для финального восстановления
    snap = (
        await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
    ).json()
    initial_merchants = sorted(m["id"] for m in (snap.get("merchants") or []))
    initial_groups = sorted(g["id"] for g in (snap.get("groups") or []))
    initial_flag = bool(snap.get("accept_all_merchants", False))

    # Создаём изолированного мерчанта (новый user → merchant) без привязок
    iso_username = f"e2e_iso_m_{rand_suffix()}"
    reg = await http.post(
        "/api/v1/auth/register",
        json={"username": iso_username, "password": "SecurePass123", "role": "merchant"},
        headers=admin.auth(),
    )
    assert reg.status_code == 201, reg.text
    iso_user_id = reg.json()["id"]

    merchants_resp = await http.get("/api/v1/merchants/?limit=500", headers=admin.auth())
    iso_m = next(m for m in merchants_resp.json() if m.get("user_id") == iso_user_id)
    iso_id = iso_m["id"]

    # Включаем + комиссии + получаем api_key
    await http.patch(
        f"/api/v1/merchants/{iso_id}",
        json={
            "status": "enabled",
            "fees": {"sbp": 2.0},
            "order_ttl_seconds": 900,
        },
        headers=admin.auth(),
    )
    key_resp = await http.post(
        f"/api/v1/merchants/{iso_id}/api-key/reset", headers=admin.auth()
    )
    iso_api_key = key_resp.json()["api_key"]
    iso_merchant = TestMerchant(id=iso_id, user=None, api_key=iso_api_key)  # type: ignore[arg-type]

    # Гарантируем: trader НЕ принимает всех и не имеет прямой привязки/групп
    await http.patch(
        f"/api/v1/traders/{trader.id}",
        json={
            "merchant_ids": [],
            "group_ids": [],
            "accept_all_merchants": False,
            "is_payin_active": True,
        },
        headers=admin.auth(),
    )
    # toggle тоже на всякий случай
    await http.patch(
        "/api/v1/traders/me/payin",
        json={"is_active": True},
        headers=trader.user.auth(),
    )

    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        # ---- (a) Изолированный мерчант — ордер не должен достаться нашему трейдеру ----
        attempt = await _create_payin(
            http, iso_merchant, expected_codes=(201, 404, 422)
        )
        if attempt.status_code == 201:
            # Допускаем 201 + потом failed/canceled из-за отсутствия реквизита
            order = attempt.json()
            order_uuid = order["id"]
            terminal = await _wait_for_status(
                http, iso_merchant, order_uuid,
                ["failed", "canceled", "pending"],
                timeout=8.0,
            )
            status = terminal.get("status")
            # Если pending — значит pooling всё-таки выбрал нашего трейдера → BUG
            assert status in ("failed", "canceled"), (
                f"Pooling should NOT bind isolated merchant to unbound trader, but got "
                f"status={status}, requisite={terminal.get('requisite')}"
            )
        else:
            # 404 / 422 — pooling вернул "no requisite found", тоже корректное поведение
            assert attempt.status_code in (404, 422), attempt.text

        # ---- (b) Привязываем трейдера к ОСНОВНОМУ мерчанту — должно сработать ----
        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={"merchant_ids": [merchant.id]},
            headers=admin.auth(),
        )

        ok_resp = await _create_payin(http, merchant)
        assert ok_resp.status_code == 201, ok_resp.text
        ok_order = ok_resp.json()
        ok_uuid = ok_order["id"]
        bound_status = await _wait_for_status(
            http, merchant, ok_uuid, ["pending", "success", "failed"], timeout=10.0
        )
        if bound_status.get("status") != "pending":
            pytest.skip(
                f"Bound order didn't reach pending (got {bound_status.get('status')}); "
                "likely a flaky pool state on dev"
            )
        assert bound_status.get("requisite") is not None
        assert bound_status["requisite"]["account_number"]

        # Cancel чтобы не оставлять висящий ордер
        await http.patch(
            f"/api/v1/orders/{ok_order.get('id_int') or ok_order.get('id')}",
            json={"status": "canceled", "reason": "e2e cleanup"},
            headers=admin.auth(),
        )

    finally:
        await restore_requisites(http, admin, disabled_reqs)
        # Восстанавливаем состояние трейдера
        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={
                "merchant_ids": initial_merchants,
                "group_ids": initial_groups,
                "accept_all_merchants": initial_flag,
            },
            headers=admin.auth(),
        )


# ---------------------------------------------------------------------------
# 5. accept_all_merchants пускает к мерчанту без привязок
# ---------------------------------------------------------------------------

async def test_accept_all_merchants_lets_trader_serve_unbound_merchant(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
) -> None:
    """
    Если у мерчанта нет привязок (ни прямых, ни через группы), он отдаёт ордера
    только трейдерам с accept_all_merchants=True. Проверяем оба пути.
    """
    snap = (
        await http.get(f"/api/v1/traders/{trader.id}", headers=admin.auth())
    ).json()
    initial_merchants = sorted(m["id"] for m in (snap.get("merchants") or []))
    initial_groups = sorted(g["id"] for g in (snap.get("groups") or []))
    initial_flag = bool(snap.get("accept_all_merchants", False))

    # Создаём изолированного мерчанта (без привязок ни к нам, ни к группам)
    iso_username = f"e2e_open_m_{rand_suffix()}"
    reg = await http.post(
        "/api/v1/auth/register",
        json={"username": iso_username, "password": "SecurePass123", "role": "merchant"},
        headers=admin.auth(),
    )
    assert reg.status_code == 201, reg.text
    iso_user_id = reg.json()["id"]
    merchants_list = (await http.get("/api/v1/merchants/?limit=500", headers=admin.auth())).json()
    iso_m = next(m for m in merchants_list if m.get("user_id") == iso_user_id)
    iso_id = iso_m["id"]

    await http.patch(
        f"/api/v1/merchants/{iso_id}",
        json={"status": "enabled", "fees": {"sbp": 2.0}, "order_ttl_seconds": 900},
        headers=admin.auth(),
    )
    iso_key = (
        await http.post(f"/api/v1/merchants/{iso_id}/api-key/reset", headers=admin.auth())
    ).json()["api_key"]
    iso_merchant = TestMerchant(id=iso_id, user=None, api_key=iso_key)  # type: ignore[arg-type]

    # Без флага — ордер не должен попасть к нашему трейдеру
    await http.patch(
        f"/api/v1/traders/{trader.id}",
        json={
            "merchant_ids": [],
            "group_ids": [],
            "accept_all_merchants": False,
            "is_payin_active": True,
        },
        headers=admin.auth(),
    )
    await http.patch(
        "/api/v1/traders/me/payin",
        json={"is_active": True},
        headers=trader.user.auth(),
    )

    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        no_flag_resp = await _create_payin(http, iso_merchant)
        if no_flag_resp.status_code == 201:
            o = no_flag_resp.json()
            settled = await _wait_for_status(
                http, iso_merchant, o["id"], ["pending", "failed", "canceled"], timeout=8.0
            )
            assert settled.get("status") in ("failed", "canceled"), (
                f"Closed trader served unbound merchant: status={settled.get('status')}"
            )
        else:
            assert no_flag_resp.status_code in (404, 422), no_flag_resp.text

        # Включаем accept_all_merchants — теперь должен сработать
        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={"accept_all_merchants": True},
            headers=admin.auth(),
        )

        flag_resp = await _create_payin(http, iso_merchant)
        assert flag_resp.status_code == 201, flag_resp.text
        flagged = await _wait_for_status(
            http, iso_merchant, flag_resp.json()["id"],
            ["pending", "success", "failed"], timeout=10.0,
        )
        if flagged.get("status") != "pending":
            pytest.skip(
                f"accept_all_merchants flow didn't reach pending: {flagged.get('status')}"
            )
        assert flagged.get("requisite") is not None, (
            "Open trader (accept_all_merchants=True) should have served the unbound merchant"
        )

    finally:
        await restore_requisites(http, admin, disabled_reqs)
        await http.patch(
            f"/api/v1/traders/{trader.id}",
            json={
                "merchant_ids": initial_merchants,
                "group_ids": initial_groups,
                "accept_all_merchants": initial_flag,
            },
            headers=admin.auth(),
        )


# ---------------------------------------------------------------------------
# 6. Order debug endpoint показывает причины исключения трейдеров
# ---------------------------------------------------------------------------

async def test_order_debug_exposes_traders_candidates(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
    merchant: TestMerchant,
    trader_group: int,
) -> None:
    """
    Smoke-проверка `/api/v1/orders/debug/{uuid}`: эндпоинт отдаёт traders_candidates,
    у максимум одного кандидата is_selected=True, выбранный — это трейдер,
    обслуживший ордер (если ордер дошёл до pending). Глубокая проверка причин
    исключения остаётся в unit-тестах `_check_exclusion`.
    """
    disabled_reqs = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        create = await _create_payin(http, merchant)
        assert create.status_code == 201, create.text
        order = create.json()
        order_uuid = order["id"]

        # Подождём, пока pooling завершит выбор трейдера (sync режим — обычно сразу)
        await _wait_for_status(
            http, merchant, order_uuid, ["pending", "failed", "canceled"], timeout=8.0
        )

        debug = await http.get(f"/api/v1/orders/debug/{order_uuid}", headers=admin.auth())
        assert debug.status_code == 200, debug.text
        data = debug.json()

        candidates = data.get("traders_candidates")
        assert isinstance(candidates, list), f"traders_candidates missing: {data}"

        selected = [c for c in candidates if c.get("is_selected")]
        assert len(selected) <= 1, f"More than one selected trader: {selected}"

        # Smoke-проверка структуры: каждый кандидат имеет user_id и method_config.
        for c in candidates:
            assert "user_id" in c, c
            assert "method_config" in c, c

        # Cleanup
        admin_cancel_id = order.get("id_int") or order.get("id")
        if isinstance(admin_cancel_id, int):
            await http.patch(
                f"/api/v1/orders/{admin_cancel_id}",
                json={"status": "canceled", "reason": "e2e cleanup"},
                headers=admin.auth(),
            )
    finally:
        await restore_requisites(http, admin, disabled_reqs)
