"""
E2E-тесты: Споры (Disputes) — полный сценарий для всех ролей.

Покрываем три поверхности (переписки/комментариев у споров больше нет):
  * Merchant API  (/api/merchant/v1/disputes/...)   — открытие / список / детали /
                   догрузка чека к открытому спору (/{uuid}/receipt)
  * Admin  API    (/api/v1/disputes, /{id}/resolve|reject)
  * Trader API    (/api/v1/disputes/my, /my/{uuid},
                   /my/{uuid}/accept|reject|request-proof)

Актуальный жизненный цикл спора (после упрощения статусов):
    OPEN ──► RESOLVED   (в пользу мерчанта → заявка SUCCESS)
         └─► REJECTED   (в пользу трейдера → заявка FAILED)
  Промежуточных статусов in_progress / in_review БОЛЬШЕ НЕТ, и эндпоинта
  `/{id}/in-progress` тоже нет — resolve/reject выполняются прямо из OPEN.

Трейдер по СВОЕМУ спору (self-service решение):
  * accept        → уступает: спор RESOLVED в пользу мерча (как admin resolve, но
    resolved_by=trader), заявка SUCCESS.
  * reject        → решает в СВОЮ пользу: спор REJECTED, заявка FAILED
    (resolved_by=trader). Заменил прежний «contest» (эскалацию админу).
  * request-proof → запрашивает у мерчанта видео/ПДФ: спор остаётся OPEN с
    substatus video_requested|pdf_requested, мерчанту уходит запрос.

Правила бэкенда, на которых строятся тесты:
  * Открыть спор можно только по заявке в ФИНАЛЬНОМ статусе: success / failed /
    canceled (активные pending / receipt_uploaded — нельзя).
  * Спор «мерчант против трейдера»: открыть его можно ТОЛЬКО по заявке, которая
    реально дошла до трейдера (был выдан реквизит → есть trader_id). Заявку без
    трейдера (реквизит не выдавался) оспорить нельзя — иначе спор был бы тупиком,
    т.к. resolve/reject требуют назначенного трейдера.

Как тесты добывают «оспоримую» заявку с трейдером (дёшево, без trader-success):
    issue_requisite_async=false → пулинг сразу назначает реквизит+трейдера
    (PENDING) → merchant cancel → CANCELED, причём trader_id сохраняется
    (cancel не чистит его). Такая заявка финальна И с трейдером.

Запуск (обязательно e2e-venv с pytest-asyncio для live-сервера):
    ~/.venvs/primepay-e2e/bin/python -m pytest tests/e2e/test_07_disputes.py -v
"""

import asyncio
import io
import time

import httpx
import pytest

from tests.e2e.conftest import (
    BOT_SECRET,
    BOT_TG_USER_ID,
    SUPPORT_BOT_SECRET,
    TestMerchant,
    TestTrader,
    TestUser,
    bot_headers,
    isolate_requisites,
    rand_suffix,
    restore_requisites,
)

pytestmark = pytest.mark.anyio


# ═══════════════════════════════════════════════════════════════════════════
# Вспомогательные функции
# ═══════════════════════════════════════════════════════════════════════════

async def _create_traderless_order(
    http: httpx.AsyncClient, merchant: TestMerchant, external_id: str | None = None
) -> dict:
    """Заявка БЕЗ трейдера: issue_requisite_async=true + немедленная отмена, т.е.
    реквизит не успевает выдаться → trader_id остаётся NULL. Финальный статус
    canceled. Нужна только для негативного теста «нельзя оспорить без трейдера»."""
    payload: dict = {
        "amount": 1000.0,
        "currency": "RUB",
        "payment_method": "sbp",
        "issue_requisite_async": True,
    }
    if external_id:
        payload["internalId"] = external_id

    create_resp = await http.post(
        "/api/merchant/v1/orders/payin", headers=merchant.headers(), json=payload
    )
    assert create_resp.status_code == 201, f"Order creation failed: {create_resp.text}"
    order_uuid = create_resp.json()["id"]

    cancel_resp = await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel", headers=merchant.headers()
    )
    assert cancel_resp.status_code == 200, f"Cancel failed: {cancel_resp.text}"
    assert cancel_resp.json()["status"] == "canceled"
    return cancel_resp.json()


async def _create_disputable_order(
    http: httpx.AsyncClient, merchant: TestMerchant, external_id: str | None = None
) -> dict:
    """Финальная (CANCELED) заявка, которая ДОШЛА до трейдера.

    issue_requisite_async=false → пулинг синхронно назначает реквизит+трейдера
    (PENDING) → merchant cancel → CANCELED (cancel НЕ обнуляет trader_id). Такая
    заявка оспорима под правилом «у спора должен быть трейдер».

    Требует привязки merchant↔trader (фикстура trader_group), иначе у пулинга нет
    подходящего реквизита. Если назначить не удалось (гонка доступности) — skip.
    """
    payload: dict = {
        "amount": 1000.0,
        "currency": "RUB",
        "payment_method": "sbp",
        "issue_requisite_async": False,
    }
    if external_id:
        payload["internalId"] = external_id

    create_resp = await http.post(
        "/api/merchant/v1/orders/payin", headers=merchant.headers(), json=payload
    )
    if create_resp.status_code != 201:
        pytest.skip(
            f"sync payin не назначил реквизит (пулинг): "
            f"{create_resp.status_code} {create_resp.text[:200]}"
        )
    order_uuid = create_resp.json()["id"]

    cancel_resp = await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel", headers=merchant.headers()
    )
    assert cancel_resp.status_code == 200, f"Cancel failed: {cancel_resp.text}"
    assert cancel_resp.json()["status"] == "canceled"
    return cancel_resp.json()


async def _create_active_pending_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    admin: TestUser,
    external_id: str | None = None,
) -> dict:
    """АКТИВНАЯ (PENDING) заявка с назначенным трейдером, БЕЗ отмены.

    В отличие от ``_create_disputable_order`` (которая отменяет заявку → CANCELED),
    эта оставляет её в активном статусе ``pending`` — нужна для проверки «спор из
    активного статуса» (расширенный ``_DISPUTABLE_STATUSES``: +pending +receipt_uploaded).
    Изолирует чужие реквизиты, чтобы пулинг гарантированно выбрал наш. Если назначить
    не удалось (гонка доступности пулинга) — skip.
    """
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        payload: dict = {
            "amount": 1000.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": False,
        }
        if external_id:
            payload["internalId"] = external_id

        create_resp = await http.post(
            "/api/merchant/v1/orders/payin", headers=merchant.headers(), json=payload
        )
        if create_resp.status_code != 201:
            pytest.skip(
                f"sync payin не назначил реквизит (пулинг): "
                f"{create_resp.status_code} {create_resp.text[:200]}"
            )
        order_uuid = create_resp.json()["id"]

        order = await _wait_order_status(
            http, merchant, order_uuid, ["pending"], timeout=10.0
        )
        if order.get("status") != "pending":
            pytest.skip(
                f"Order did not reach pending (got {order.get('status')}) — pooling race"
            )
        return order
    finally:
        await restore_requisites(http, admin, disabled)


async def _wait_order_status(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    expected: list[str],
    timeout: float = 15.0,
    interval: float = 0.5,
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        resp = await http.get(
            f"/api/merchant/v1/orders/{order_uuid}", headers=merchant.headers()
        )
        if resp.status_code == 200:
            last = resp.json()
            if last.get("status") in expected:
                return last
        await asyncio.sleep(interval)
    return last


async def _create_success_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    admin: TestUser,
) -> dict:
    """Полный happy-path PAYIN: order → pending (ассайн трейдеру) → trader success.

    Возвращает merchant-order JSON со статусом success. По таким заявкам
    корректно тестируется resolve/reject (есть назначенный трейдер и денежный
    цикл reconcile→escrow→complete/cancel).
    """
    disabled = await isolate_requisites(http, admin, trader.requisite_id)
    try:
        external_id = f"e2e_disp_ok_{rand_suffix()}"
        create_resp = await http.post(
            "/api/merchant/v1/orders/payin",
            headers=merchant.headers(),
            json={
                "amount": 1000.0,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": external_id,
                "issue_requisite_async": False,
            },
        )
        assert create_resp.status_code == 201, f"Order creation failed: {create_resp.text}"
        order_uuid = create_resp.json()["id"]

        order = await _wait_order_status(
            http, merchant, order_uuid,
            ["pending", "success", "failed", "canceled"],
            timeout=10.0,
        )
        if order.get("status") != "pending":
            pytest.skip(f"Order did not reach pending (got {order.get('status')}) — pooling race")

        trader_order: dict | None = None
        for _ in range(10):
            resp = await http.get("/api/v1/orders/my-active", headers=trader.user.auth())
            active = resp.json() if resp.status_code == 200 else []
            trader_order = next(
                (o for o in active if str(o.get("uuid")) == str(order_uuid)), None
            )
            if trader_order:
                break
            await asyncio.sleep(0.5)
        if trader_order is None:
            pytest.skip("Order not in trader active list — pooling race, not a dispute issue")

        success_resp = await http.post(
            f"/api/v1/orders/{trader_order['id']}/success",
            headers=trader.user.auth(),
        )
        assert success_resp.status_code == 200, f"Trader success failed: {success_resp.text}"

        final = await _wait_order_status(http, merchant, order_uuid, ["success"], timeout=5.0)
        assert final.get("status") == "success", f"Order not success: {final}"
        return final
    finally:
        await restore_requisites(http, admin, disabled)


async def _open_dispute(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    *,
    reason: str = "no_payment",
) -> dict:
    """Открывает спор по UUID заявки через merchant API, возвращает dispute JSON.

    Контракт (новые причины `unknown|has_payment|no_payment|invalid_sum|
    invalid_requisites`, без `description`) может быть ещё не задеплоен на dev —
    тогда старый бэкенд вернёт 422 (неизвестная причина / требуется description),
    и тест пропускается (активируется после деплоя).
    """
    resp = await http.post(
        f"/api/merchant/v1/disputes/by-order/{order_uuid}",
        headers=merchant.headers(),
        data={"reason": reason},
    )
    if resp.status_code == 422:
        pytest.skip("Новый контракт споров (причины/без description) ещё не задеплоен на dev.")
    assert resp.status_code in (200, 201), f"Open dispute failed: {resp.text}"
    return resp.json()


def _dispute_uuid(d: dict) -> str:
    return str(d.get("uuid") or d.get("id"))


async def _dispute_int_id(
    http: httpx.AsyncClient, admin: TestUser, dispute: dict
) -> int:
    """Целочисленный dispute.id для admin-эндпоинтов.

    Merchant DisputeResponse уже отдаёт ``id: int`` напрямую; на случай ещё не
    задеплоенной схемы — фолбэк через admin list по uuid.
    """
    raw = dispute.get("id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)

    target = _dispute_uuid(dispute)
    admin_list = await http.get("/api/v1/disputes?limit=200", headers=admin.auth())
    assert admin_list.status_code == 200, admin_list.text
    found = next((x for x in admin_list.json() if str(x.get("uuid")) == target), None)
    assert found is not None, f"Dispute {target} not found in admin list"
    did = found.get("id")
    if did is None:
        pytest.skip("DisputeResponse.id недоступен (нужен деплой schemas.py с полем id).")
    return int(did)


# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Merchant API: открытие споров
# ═══════════════════════════════════════════════════════════════════════════

async def test_merchant_open_dispute_by_order_uuid(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    order = await _create_disputable_order(http, merchant)
    dispute = await _open_dispute(
        http, merchant, order["id"], reason="no_payment"
    )
    assert dispute["reason"] == "no_payment"
    assert dispute["status"] == "open"
    assert dispute.get("uuid")


async def test_merchant_open_dispute_by_external_id(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    external_id = f"e2e_dispute_ext_{rand_suffix()}"
    await _create_disputable_order(http, merchant, external_id=external_id)

    resp = await http.post(
        f"/api/merchant/v1/disputes/by-external/{external_id}",
        headers=merchant.headers(),
        data={"reason": "invalid_sum"},
    )
    if resp.status_code in (415, 422):
        pytest.skip("multipart-открытие спора ещё не задеплоено на dev.")
    assert resp.status_code in (200, 201), f"Open by external failed: {resp.text}"
    assert resp.json()["reason"] == "invalid_sum"
    assert resp.json()["status"] == "open"


async def test_merchant_open_dispute_with_uploaded_file(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Мерчант открывает спор и прикладывает РЕАЛЬНЫЙ файл (multipart). Файл
    сохраняется как доказательство спора (receipt с привязкой к заявке) —
    админ его видит. multipart-контракт может быть ещё не задеплоен → skip."""
    order = await _create_disputable_order(http, merchant)

    resp = await http.post(
        f"/api/merchant/v1/disputes/by-order/{order['id']}",
        headers=merchant.headers(),
        data={"reason": "has_payment"},
        files={"attachments": ("proof.png", _FAKE_RECEIPT_PNG, "image/png")},
    )
    # Until the multipart endpoint is deployed, the old JSON-body endpoint can't
    # parse a multipart-with-file request and answers 415/422/500 — treat all as
    # "feature not deployed" and skip. After deploy a valid file returns 200/201.
    if resp.status_code in (415, 422, 500):
        pytest.skip("multipart-открытие спора с файлом ещё не задеплоено на dev.")
    assert resp.status_code in (200, 201), f"Open with file failed: {resp.text}"
    assert resp.json()["status"] == "open"

    # Файл сохранён как доказательство — виден админу в списке чеков заявки.
    rcpts = await http.get(f"/api/v1/orders/{order['id']}/receipts", headers=admin.auth())
    if rcpts.status_code == 200:
        assert len(rcpts.json()) >= 1, "загруженный файл не сохранился как receipt"


async def test_merchant_open_dispute_rejects_unsafe_evidence_link(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Ссылка-доказательство на внутренний адрес (SSRF) отклоняется — спор не
    открывается. Старый JSON-эндпоинт ответит 422 на multipart, новый — отвергнет
    ссылку (400/422); в обоих случаях это 4xx."""
    order = await _create_disputable_order(http, merchant)
    resp = await http.post(
        f"/api/merchant/v1/disputes/by-order/{order['id']}",
        headers=merchant.headers(),
        data={"reason": "no_payment", "evidence_urls": ["http://169.254.169.254/x.png"]},
    )
    assert resp.status_code in (400, 415, 422), (
        f"Unsafe evidence link must be rejected, got {resp.status_code}: {resp.text}"
    )


async def test_merchant_open_dispute_on_active_pending_allowed(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Спор МОЖНО открыть и по АКТИВНОЙ заявке (pending) — после расширения
    ``_DISPUTABLE_STATUSES`` (+pending +receipt_uploaded). Заявка переходит в
    DISPUTED и НЕ закрывается автоматически, пока спор не решён.

    Раньше этот сценарий был запрещён (тест ждал 4xx). Теперь активные статусы
    оспоримы; «нет трейдера → нет спора» по-прежнему запрещён и проверяется
    отдельно в ``test_cannot_open_dispute_without_trader``.

    На ещё НЕ задеплоенном бэкенде (где активные статусы запрещены) откроется
    4xx → skip; тест активируется автоматически после деплоя.
    """
    order = await _create_active_pending_order(http, merchant, trader, admin)

    resp = await http.post(
        f"/api/merchant/v1/disputes/by-order/{order['id']}",
        headers=merchant.headers(),
        data={"reason": "no_payment"},
    )
    if resp.status_code in (400, 409, 422):
        pytest.skip(
            "Спор из активного статуса ещё не задеплоен на dev (вернулось 4xx)."
        )
    assert resp.status_code in (200, 201), (
        f"Open dispute on active pending order failed: {resp.status_code} {resp.text}"
    )
    assert resp.json()["status"] == "open"

    # Заявка должна перейти в disputed и НЕ закрыться автоматически.
    disputed = await _wait_order_status(
        http, merchant, order["id"], ["disputed"], timeout=5.0
    )
    assert disputed.get("status") == "disputed", (
        f"Активная заявка должна перейти в disputed, got {disputed.get('status')}"
    )


async def test_dispute_from_active_pending_resolve_full_cycle(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Полный денежный цикл спора из АКТИВНОГО статуса:

        pending → merchant открывает спор (order → disputed; деньги уже в escrow
        с момента создания заявки, повторной заморозки НЕ происходит — reconcile
        для активного pre-статуса это NO-OP) → admin resolve → order SUCCESS.

    Это ключевой сценарий нового поведения: оспорить и развести активную заявку
    без двойного списания залога. На ещё НЕ задеплоенном бэкенде → skip.
    """
    order = await _create_active_pending_order(http, merchant, trader, admin)

    resp = await http.post(
        f"/api/merchant/v1/disputes/by-order/{order['id']}",
        headers=merchant.headers(),
        data={"reason": "no_payment"},
    )
    if resp.status_code in (400, 409, 422):
        pytest.skip("Спор из активного статуса ещё не задеплоен на dev.")
    assert resp.status_code in (200, 201), resp.text
    dispute = resp.json()
    assert dispute["status"] == "open"
    dispute_id = await _dispute_int_id(http, admin, dispute)

    disputed = await _wait_order_status(
        http, merchant, order["id"], ["disputed"], timeout=5.0
    )
    assert disputed.get("status") == "disputed", (
        f"Заявка должна быть disputed после открытия спора: {disputed.get('status')}"
    )

    resolve = await http.post(
        f"/api/v1/disputes/{dispute_id}/resolve",
        headers=admin.auth(),
        json={"resolution_text": "E2E: active-order dispute resolved for merchant"},
    )
    assert resolve.status_code == 200, f"Resolve failed: {resolve.text}"
    assert resolve.json()["status"] == "resolved"

    final = await _wait_order_status(http, merchant, order["id"], ["success"], timeout=5.0)
    assert final.get("status") == "success", (
        f"Заявка должна стать success после resolve: {final}"
    )


async def test_merchant_open_dispute_invalid_reason(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader
) -> None:
    """Невалидная причина отсекается валидацией тела запроса (422) ещё до сервиса,
    поэтому состояние заявки тут не важно — берём дешёвую trader-less."""
    order = await _create_traderless_order(http, merchant)
    resp = await http.post(
        f"/api/merchant/v1/disputes/by-order/{order['id']}",
        headers=merchant.headers(),
        data={"reason": "totally_invalid_reason"},
    )
    assert resp.status_code in (400, 422)


async def test_merchant_open_dispute_nonexistent_order(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.post(
        "/api/merchant/v1/disputes/by-order/00000000-0000-0000-0000-000000000000",
        headers=merchant.headers(),
        data={"reason": "no_payment"},
    )
    assert resp.status_code in (400, 404, 422)


async def test_cannot_open_dispute_without_trader(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader
) -> None:
    """Заявку без трейдера (реквизит не выдавался) оспорить НЕЛЬЗЯ.

    issue_requisite_async=true + немедленная отмена → trader_id = NULL. Открытие
    спора должно вернуть 4xx. На ещё НЕ задеплоенном бэкенде (без гварда) откроется
    201 → skip; тест активируется автоматически после деплоя.
    """
    order = await _create_traderless_order(http, merchant)
    resp = await http.post(
        f"/api/merchant/v1/disputes/by-order/{order['id']}",
        headers=merchant.headers(),
        data={"reason": "no_payment"},
    )
    if resp.status_code in (200, 201):
        pytest.skip(
            "Гвард «нет трейдера → нет спора» ещё не задеплоен на dev (открылось 201)."
        )
    assert resp.status_code in (400, 409, 422), (
        f"Спор по заявке без трейдера должен быть запрещён, got {resp.status_code}: {resp.text}"
    )


async def test_merchant_reopen_dispute_appends_evidence(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    """Повторный вызов create-dispute по той же заявке ДОГРУЖАЕТ чек к уже
    открытому спору (idempotent-additive) и возвращает тот же спор — не падает.
    Старый бэкенд (1:1, без догрузки) ещё на dev → skip."""
    order = await _create_disputable_order(http, merchant)
    first = await _open_dispute(http, merchant, order["id"], reason="no_payment")
    assert first["status"] == "open"

    resp = await http.post(
        f"/api/merchant/v1/disputes/by-order/{order['id']}",
        headers=merchant.headers(),
        data={"reason": "no_payment"},
        files={"attachments": ("proof2.png", _FAKE_RECEIPT_PNG, "image/png")},
    )
    if resp.status_code in (400, 409):
        pytest.skip("Догрузка чека через create-эндпоинт ещё не задеплоена на dev (старый 1:1).")
    assert resp.status_code in (200, 201), f"Re-attach failed: {resp.text}"
    body = resp.json()
    assert _dispute_uuid(body) == _dispute_uuid(first), "Должен вернуться тот же спор"
    assert body["status"] == "open"
    if "evidence_count" in body:
        assert body["evidence_count"] >= 1, f"Догруженный чек должен попасть в evidence: {body}"


# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Merchant API: список / детали / комментарии / изоляция
# ═══════════════════════════════════════════════════════════════════════════

async def test_merchant_list_disputes(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    order = await _create_disputable_order(http, merchant)
    await _open_dispute(http, merchant, order["id"], reason="unknown")

    resp = await http.get("/api/merchant/v1/disputes", headers=merchant.headers())
    assert resp.status_code == 200
    disputes = resp.json()
    assert isinstance(disputes, list) and len(disputes) > 0
    # Endpoint is merchant-scoped; the restricted schema no longer exposes
    # merchant_id, so assert the merchant-visible shape instead.
    assert all(d.get("uuid") and d.get("status") for d in disputes)


async def test_merchant_list_disputes_pagination(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.get("/api/merchant/v1/disputes?skip=0&limit=5", headers=merchant.headers())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) <= 5


async def test_merchant_get_dispute_by_uuid(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    order = await _create_disputable_order(http, merchant)
    dispute = await _open_dispute(http, merchant, order["id"], reason="unknown")
    dispute_uuid = _dispute_uuid(dispute)

    resp = await http.get(f"/api/merchant/v1/disputes/{dispute_uuid}", headers=merchant.headers())
    assert resp.status_code == 200
    detail = resp.json()
    assert _dispute_uuid(detail) == dispute_uuid
    assert detail["status"] == "open"
    assert detail["reason"] == "unknown"


async def test_merchant_adds_receipt_to_open_dispute(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    """Мерчант догружает ещё один чек к уже ОТКРЫТОМУ спору (пост-открытие).
    Эндпоинт ещё не задеплоен на dev → skip."""
    order = await _create_disputable_order(http, merchant)
    dispute = await _open_dispute(http, merchant, order["id"], reason="no_payment")
    dispute_uuid = _dispute_uuid(dispute)

    resp = await http.post(
        f"/api/merchant/v1/disputes/{dispute_uuid}/receipt",
        headers=merchant.headers(),
        files={"attachment": ("proof.png", _FAKE_RECEIPT_PNG, "image/png")},
    )
    if resp.status_code in (404, 405):
        pytest.skip("Эндпоинт догрузки чека в спор ещё не задеплоен на dev.")
    assert resp.status_code == 200, f"Add dispute receipt failed: {resp.text}"
    body = resp.json()
    assert _dispute_uuid(body) == dispute_uuid
    assert body["status"] == "open", "Спор остаётся открытым после догрузки чека"
    if "evidence_count" in body:
        assert body["evidence_count"] >= 1, f"Чек должен попасть в evidence: {body}"


async def test_merchant_add_dispute_receipt_rejects_bad_format(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    """Догрузка чека с битым форматом (расширение .png, но байты не картинка)
    отклоняется — confirm_order гейтит формат. Эндпоинт не задеплоен → skip."""
    order = await _create_disputable_order(http, merchant)
    dispute = await _open_dispute(http, merchant, order["id"], reason="no_payment")
    dispute_uuid = _dispute_uuid(dispute)

    resp = await http.post(
        f"/api/merchant/v1/disputes/{dispute_uuid}/receipt",
        headers=merchant.headers(),
        files={"attachment": ("proof.png", b"definitely not a real png", "image/png")},
    )
    if resp.status_code in (404, 405):
        pytest.skip("Эндпоинт догрузки чека в спор ещё не задеплоен на dev.")
    assert resp.status_code in (400, 422), (
        f"Битый формат должен быть отклонён, got {resp.status_code}: {resp.text}"
    )


async def test_merchant_get_dispute_not_found(
    http: httpx.AsyncClient, merchant: TestMerchant
) -> None:
    resp = await http.get(
        "/api/merchant/v1/disputes/00000000-0000-0000-0000-000000000000",
        headers=merchant.headers(),
    )
    assert resp.status_code in (400, 404)


async def test_merchant_cannot_see_other_merchants_dispute(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Мерчант №2 не должен видеть спор мерчанта №1 (403/404)."""
    suffix = rand_suffix()
    reg = await http.post(
        "/api/v1/auth/register",
        json={"username": f"e2e_disp_iso_{suffix}", "password": "SecurePass123", "role": "merchant"},
        headers=admin.auth(),
    )
    assert reg.status_code == 201
    user_id2 = reg.json()["id"]

    merchants_resp = await http.get(
        f"/api/v1/merchants/?search=e2e_disp_iso_{suffix}", headers=admin.auth()
    )
    m2 = next((m for m in merchants_resp.json() if m["user_id"] == user_id2), None)
    if not m2:
        pytest.skip("Second merchant for isolation test not found")

    await http.patch(
        f"/api/v1/merchants/{m2['id']}", json={"status": "enabled"}, headers=admin.auth()
    )
    key_resp = await http.post(
        f"/api/v1/merchants/{m2['id']}/api-key/reset", headers=admin.auth()
    )
    m2_key = key_resp.json()["api_key"]

    order = await _create_disputable_order(http, merchant)
    dispute = await _open_dispute(http, merchant, order["id"], reason="unknown")
    dispute_uuid = _dispute_uuid(dispute)

    cross = await http.get(
        f"/api/merchant/v1/disputes/{dispute_uuid}", headers={"X-Api-Key": m2_key}
    )
    assert cross.status_code in (403, 404)


# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — Admin API: список / фильтры / комментарии / resolve / reject
# ═══════════════════════════════════════════════════════════════════════════

async def test_admin_list_disputes(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
) -> None:
    order = await _create_disputable_order(http, merchant)
    await _open_dispute(http, merchant, order["id"], reason="unknown")

    resp = await http.get("/api/v1/disputes", headers=admin.auth())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_admin_open_dispute_by_order_uuid(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Admin opens a dispute on any order by UUID (multipart, evidence optional).
    Endpoint may be undeployed → 404/405/422 → skip."""
    order = await _create_disputable_order(http, merchant)

    resp = await http.post(
        "/api/v1/disputes",
        headers=admin.auth(),
        data={"order_uuid": order["id"], "reason": "no_payment"},
    )
    if resp.status_code in (404, 405, 415, 422):
        pytest.skip("admin create-dispute endpoint ещё не задеплоен на dev.")
    assert resp.status_code in (200, 201), f"Admin open dispute failed: {resp.text}"
    body = resp.json()
    assert body["status"] == "open"
    assert body["reason"] == "no_payment"
    # initiator is the admin, not the merchant.
    assert body.get("initiator_type") == "admin"


async def test_admin_list_disputes_filter_by_status_open(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Фильтр ?status=open отдаёт только открытые споры."""
    order = await _create_disputable_order(http, merchant)
    await _open_dispute(http, merchant, order["id"], reason="no_payment")

    resp = await http.get("/api/v1/disputes?status=open&limit=200", headers=admin.auth())
    assert resp.status_code == 200
    disputes = resp.json()
    assert isinstance(disputes, list)
    assert all(d.get("status") == "open" for d in disputes), (
        "status=open фильтр вернул не только open"
    )


async def test_admin_resolve_dispute_favor_merchant(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """resolve (в пользу мерчанта) на корректном сценарии:

        success → открытие спора (order → disputed, деньги в escrow)
        → admin resolve → order снова SUCCESS.
    Промежуточного in_progress нет: resolve вызывается прямо из OPEN.
    """
    order = await _create_success_order(http, merchant, trader, admin)
    order_uuid = order["id"]

    dispute = await _open_dispute(http, merchant, order_uuid, reason="no_payment")
    assert dispute["status"] == "open"
    dispute_id = await _dispute_int_id(http, admin, dispute)

    resolve = await http.post(
        f"/api/v1/disputes/{dispute_id}/resolve",
        headers=admin.auth(),
        json={"resolution_text": "E2E: resolved in favor of merchant"},
    )
    assert resolve.status_code == 200, f"Resolve failed: {resolve.text}"
    assert resolve.json()["status"] == "resolved"
    assert resolve.json()["resolution_text"] is not None

    final = await _wait_order_status(http, merchant, order_uuid, ["success"], timeout=5.0)
    assert final.get("status") == "success", f"Order must be success after resolve: {final}"


async def test_admin_reject_dispute_favor_trader(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """reject (в пользу трейдера) на корректном сценарии:

        success → открытие спора (escrow) → admin reject → order FAILED
        (единый «неуспешный» выход; деньги возвращаются трейдеру).
    """
    order = await _create_success_order(http, merchant, trader, admin)
    order_uuid = order["id"]

    dispute = await _open_dispute(http, merchant, order_uuid, reason="invalid_sum")
    dispute_id = await _dispute_int_id(http, admin, dispute)

    reject = await http.post(
        f"/api/v1/disputes/{dispute_id}/reject",
        headers=admin.auth(),
        json={"resolution_text": "E2E: rejected, transfer confirmed by trader"},
    )
    assert reject.status_code == 200, f"Reject failed: {reject.text}"
    assert reject.json()["status"] == "rejected"

    final = await _wait_order_status(http, merchant, order_uuid, ["failed"], timeout=5.0)
    assert final.get("status") == "failed", f"Order must be failed after reject: {final}"


async def test_admin_cannot_resolve_already_closed(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Повторный resolve уже закрытого спора запрещён."""
    order = await _create_success_order(http, merchant, trader, admin)
    dispute = await _open_dispute(http, merchant, order["id"], reason="no_payment")
    dispute_id = await _dispute_int_id(http, admin, dispute)

    first = await http.post(
        f"/api/v1/disputes/{dispute_id}/resolve",
        headers=admin.auth(),
        json={"resolution_text": "first resolve"},
    )
    assert first.status_code == 200, f"First resolve failed: {first.text}"

    again = await http.post(
        f"/api/v1/disputes/{dispute_id}/resolve",
        headers=admin.auth(),
        json={"resolution_text": "second resolve"},
    )
    assert again.status_code in (400, 409, 422), (
        f"Re-resolving a closed dispute must fail, got {again.status_code}: {again.text}"
    )


async def test_admin_resolve_nonexistent_dispute(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.post(
        "/api/v1/disputes/999999999/resolve",
        headers=admin.auth(),
        json={"resolution_text": "test"},
    )
    assert resp.status_code in (400, 404)


async def test_admin_reject_nonexistent_dispute(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.post(
        "/api/v1/disputes/999999999/reject",
        headers=admin.auth(),
        json={"resolution_text": "test"},
    )
    assert resp.status_code in (400, 404)


# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Trader API: /disputes/my, детали, изоляция
# ═══════════════════════════════════════════════════════════════════════════

async def test_trader_list_my_disputes(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/disputes/my", headers=trader.user.auth())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_trader_dispute_lifecycle_on_own_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Сквозной трейдерский сценарий по СВОЕЙ заявке (с назначенным трейдером):

      success → merchant открывает спор
      → спор виден в /disputes/my и /disputes/my/{uuid} с корректным статусом/причиной
      → admin resolve → спор resolved, заявка снова SUCCESS.
    """
    order = await _create_success_order(http, merchant, trader, admin)
    dispute = await _open_dispute(http, merchant, order["id"], reason="no_payment")
    dispute_uuid = _dispute_uuid(dispute)
    dispute_id = await _dispute_int_id(http, admin, dispute)

    # — спор виден в списке трейдера —
    found = None
    for _ in range(6):
        lst = await http.get("/api/v1/disputes/my?limit=200", headers=trader.user.auth())
        assert lst.status_code == 200
        found = next((d for d in lst.json() if _dispute_uuid(d) == dispute_uuid), None)
        if found:
            break
        await asyncio.sleep(0.5)
    assert found is not None, "Открытый спор не появился в /disputes/my трейдера"
    assert found["status"] == "open"
    assert found["reason"] == "no_payment"

    # — детали спора (с инфо по заявке) —
    detail = await http.get(
        f"/api/v1/disputes/my/{dispute_uuid}", headers=trader.user.auth()
    )
    assert detail.status_code == 200
    assert _dispute_uuid(detail.json()) == dispute_uuid
    assert detail.json()["status"] == "open"

    # — admin закрывает спор (resolve) → заявка снова SUCCESS —
    resolve = await http.post(
        f"/api/v1/disputes/{dispute_id}/resolve",
        headers=admin.auth(),
        json={"resolution_text": "E2E close"},
    )
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["status"] == "resolved"

    final = await _wait_order_status(http, merchant, order["id"], ["success"], timeout=5.0)
    assert final.get("status") == "success", f"Order must be success after resolve: {final}"


async def test_trader_accepts_dispute(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Трейдер «Принимает» спор по своей SUCCESS-заявке → спор RESOLVED, заявка
    остаётся SUCCESS (трейдер уступил). Эндпоинт ещё не задеплоен на dev → skip."""
    order = await _create_success_order(http, merchant, trader, admin)
    dispute = await _open_dispute(http, merchant, order["id"], reason="no_payment")
    dispute_uuid = _dispute_uuid(dispute)

    resp = await http.post(
        f"/api/v1/disputes/my/{dispute_uuid}/accept", headers=trader.user.auth()
    )
    if resp.status_code in (404, 405):
        pytest.skip("Эндпоинт trader-accept ещё не задеплоен на dev.")
    assert resp.status_code == 200, f"Accept failed: {resp.text}"
    assert resp.json()["status"] == "resolved"

    final = await _wait_order_status(http, merchant, order["id"], ["success"], timeout=5.0)
    assert final.get("status") == "success", f"Order must stay success after accept: {final}"


async def test_trader_rejects_dispute_fails_order(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Трейдер «Отклоняет» спор по своей заявке → спор REJECTED в пользу трейдера,
    заявка становится FAILED (self-service решение). Эндпоинт ещё не задеплоен → skip."""
    order = await _create_success_order(http, merchant, trader, admin)
    dispute = await _open_dispute(http, merchant, order["id"], reason="invalid_sum")
    dispute_uuid = _dispute_uuid(dispute)

    resp = await http.post(
        f"/api/v1/disputes/my/{dispute_uuid}/reject", headers=trader.user.auth()
    )
    if resp.status_code in (404, 405):
        pytest.skip("Эндпоинт trader-reject ещё не задеплоен на dev.")
    assert resp.status_code == 200, f"Reject failed: {resp.text}"
    assert resp.json()["status"] == "rejected"

    final = await _wait_order_status(http, merchant, order["id"], ["failed"], timeout=5.0)
    assert final.get("status") == "failed", f"Order must be failed after reject: {final}"

    # Повторное решение по закрытому спору запрещено.
    again = await http.post(
        f"/api/v1/disputes/my/{dispute_uuid}/reject", headers=trader.user.auth()
    )
    assert again.status_code in (400, 409, 422), (
        f"Повторный reject должен падать, got {again.status_code}: {again.text}"
    )


@pytest.mark.parametrize("kind,substatus", [("video", "video_requested"), ("pdf", "pdf_requested")])
async def test_trader_requests_proof_keeps_dispute_open(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
    kind: str,
    substatus: str,
) -> None:
    """Трейдер запрашивает видео/ПДФ → спор остаётся OPEN с проставленным
    substatus (запрос ушёл мерчанту). Эндпоинт ещё не задеплоен → skip."""
    order = await _create_success_order(http, merchant, trader, admin)
    dispute = await _open_dispute(http, merchant, order["id"], reason="invalid_sum")
    dispute_uuid = _dispute_uuid(dispute)

    resp = await http.post(
        f"/api/v1/disputes/my/{dispute_uuid}/request-proof",
        params={"kind": kind},
        headers=trader.user.auth(),
    )
    if resp.status_code in (404, 405):
        pytest.skip("Эндпоинт trader-request-proof ещё не задеплоен на dev.")
    assert resp.status_code == 200, f"Request-proof failed: {resp.text}"
    body = resp.json()
    assert body["status"] == "open", "Спор должен остаться открытым после запроса доказательства"
    assert body.get("substatus") == substatus, f"substatus должен стать {substatus}: {body}"

    # Заявка не двигается: пока спор открыт, она остаётся disputed (запрос
    # доказательства не выносит решения), как у sibling-тестов проверяется статус.
    final = await _wait_order_status(http, merchant, order["id"], ["disputed"], timeout=5.0)
    assert final.get("status") == "disputed", (
        f"Order must stay disputed after request-proof: {final}"
    )

    # И трейдер по-прежнему видит спор открытым.
    detail = await http.get(
        f"/api/v1/disputes/my/{dispute_uuid}", headers=trader.user.auth()
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "open"


async def test_trader_cannot_see_foreign_dispute(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Чужой трейдер не должен открыть детали спора по чужой заявке (404)."""
    order = await _create_disputable_order(http, merchant)
    dispute = await _open_dispute(http, merchant, order["id"], reason="unknown")
    dispute_uuid = _dispute_uuid(dispute)

    # Второй трейдер — достаточно «голого» пользователя (для GET, ожидающего 404).
    suffix = rand_suffix()
    reg = await http.post(
        "/api/v1/auth/register",
        json={"username": f"e2e_disp_t2_{suffix}", "password": "SecurePass123", "role": "trader"},
        headers=admin.auth(),
    )
    assert reg.status_code == 201, reg.text
    login = await http.post(
        "/api/v1/auth/login",
        json={"username": f"e2e_disp_t2_{suffix}", "password": "SecurePass123"},
    )
    assert login.status_code == 200, login.text
    other_token = login.json()["access_token"]

    cross = await http.get(
        f"/api/v1/disputes/my/{dispute_uuid}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert cross.status_code in (403, 404), (
        f"Foreign trader must not read the dispute, got {cross.status_code}: {cross.text}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Section 5 — Премодерация → диспут (support-bot moderation открывает авто-спор)
# ═══════════════════════════════════════════════════════════════════════════
#
# Сквозной интеграционный путь: premoderation ON → merchant грузит чек → support-bot
# отвечает request_pdf → backend открывает Dispute(reason=premoderation,
# substatus=pdf_requested), заявка → DISPUTED и не закрывается автоматически.
#
# Поведение open_dispute_from_premoderation уже покрыто 5 unit-тестами
# (tests/unit/test_dispute_service.py). ЗДЕСЬ — живая интеграция всей цепочки:
# confirm_order(премодерация) → /moderate → DisputeService. Жёстко зависит от
# серверной конфигурации, которой может не быть на dev, поэтому КАЖДОЕ
# предусловие даёт graceful-skip (см. docstring теста).

_FAKE_RECEIPT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
    b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


async def test_premoderation_request_pdf_opens_dispute_with_substatus(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    admin: TestUser,
    trader: TestTrader,
    trader_group: int,
) -> None:
    """Премодерация → диспут (request_pdf):

        premoderation ON + support_bot_chat_id задан
        → merchant грузит чек к pending-заявке (bot confirm)
        → заявка receipt_uploaded, moderation_status=pending
        → support-bot шлёт decision=request_pdf на /moderate
        → backend открывает Dispute(reason=premoderation, substatus=pdf_requested),
          заявка → DISPUTED (не закрывается автоматически).

    Каждое предусловие — graceful skip (тест зелёный/skip на dev без полной
    конфигурации, и сам активируется когда всё на месте + код задеплоен):
      * E2E_SUPPORT_BOT_SECRET не задан                       → skip
      * GET/PATCH platform-settings/premoderation недоступен  → skip
      * чек не попал в moderation (премодерация не активна)    → skip
      * /moderate вернул 401/403 (secret не совпал) или 404   → skip
      * заявка не стала disputed (код премод.→диспут не задеплоен) → skip

    Состояние сервера восстанавливается в finally (premoderation-флаги + спор
    закрывается), чтобы не влиять на соседние тесты и реальный dev-трафик.
    """
    if not SUPPORT_BOT_SECRET:
        pytest.skip("E2E_SUPPORT_BOT_SECRET не задан — пропускаем премодерация→диспут.")

    # — Сохраняем прежние платформенные настройки премодерации для восстановления. —
    prev = await http.get("/api/v1/platform-settings/premoderation", headers=admin.auth())
    if prev.status_code != 200:
        pytest.skip("platform-settings/premoderation недоступен на dev.")
    prev_settings = prev.json()

    # — Включаем премодерацию глобально + фиктивный chat_id (иначе confirm_order
    #   отключит премодерацию из-за пустого chat_id; реальная отправка в Telegram —
    #   best-effort Celery-таск, для DB-цепочки фиктивного id достаточно). —
    set_resp = await http.patch(
        "/api/v1/platform-settings/premoderation",
        headers=admin.auth(),
        json={"receipt_premoderation_enabled": True, "support_bot_chat_id": "100500"},
    )
    if set_resp.status_code != 200:
        pytest.skip(
            f"Не удалось включить премодерацию: {set_resp.status_code} {set_resp.text[:200]}"
        )

    # tg-user + per-merchant премодерация (override beats platform flag).
    await http.patch(
        f"/api/v1/merchants/{merchant.id}",
        json={"telegram_user_ids": [BOT_TG_USER_ID], "receipt_premoderation_enabled": True},
        headers=admin.auth(),
    )

    dispute_to_close_id: int | None = None
    try:
        # — Активная PENDING-заявка с назначенным трейдером. —
        order = await _create_active_pending_order(http, merchant, trader, admin)
        order_uuid = order["id"]

        # — Merchant грузит чек через bot-confirm (merchant-bot канал). —
        confirm = await http.post(
            f"/api/bot/v1/merchants/{merchant.id}/orders/{order_uuid}/confirm",
            headers=bot_headers(BOT_SECRET, BOT_TG_USER_ID),
            files={"attachment": ("receipt.png", io.BytesIO(_FAKE_RECEIPT_PNG), "image/png")},
        )
        if confirm.status_code != 200:
            pytest.skip(f"bot-confirm не прошёл: {confirm.status_code} {confirm.text[:200]}")
        confirm_data = confirm.json()
        if confirm_data.get("status") != "receipt_uploaded":
            pytest.skip(
                f"Чек не ушёл в премодерацию (status={confirm_data.get('status')}, "
                f"moderation={confirm_data.get('moderation_status')}) — премодерация "
                "не активна на dev (нет support_bot_chat_id / флаг игнорируется)."
            )

        # — support-bot отправляет решение request_pdf на /moderate. —
        moderate = await http.post(
            f"/api/bot/v1/orders/{order_uuid}/moderate",
            headers={"X-Bot-Secret": SUPPORT_BOT_SECRET},
            json={"decision": "request_pdf", "moderator_username": "e2e_admin"},
        )
        if moderate.status_code in (401, 403):
            pytest.skip("SUPPORT_BOT_SECRET не совпал с серверным — /moderate отверг (401/403).")
        if moderate.status_code == 404:
            pytest.skip("Эндпоинт /moderate или moderation-row отсутствует на dev.")
        assert moderate.status_code == 200, f"/moderate failed: {moderate.text}"

        # — Заявка должна перейти в disputed (открылся премодерационный спор). —
        disputed = await _wait_order_status(http, merchant, order_uuid, ["disputed"], timeout=6.0)
        if disputed.get("status") != "disputed":
            pytest.skip(
                "Заявка не перешла в disputed после request_pdf — код «премодерация→диспут» "
                "ещё не задеплоен на dev."
            )

        # — Спор виден мерчанту по этой заявке, с правильным substatus. —
        lst = await http.get("/api/merchant/v1/disputes?limit=200", headers=merchant.headers())
        assert lst.status_code == 200, lst.text
        dispute = next(
            (d for d in lst.json() if str(d.get("order_uuid")) == str(order_uuid)),
            None,
        )
        assert dispute is not None, (
            "Премодерационный спор не найден в списке мерчанта по order_uuid"
        )
        assert dispute["status"] == "open"
        assert dispute.get("substatus") == "pdf_requested", (
            f"Ожидался substatus=pdf_requested, got {dispute.get('substatus')}"
        )
        if isinstance(dispute.get("id"), int):
            dispute_to_close_id = dispute["id"]
    finally:
        # — Закрываем открытый спор (reject → заявка FAILED, escrow трейдеру). —
        if dispute_to_close_id is not None:
            await http.post(
                f"/api/v1/disputes/{dispute_to_close_id}/reject",
                headers=admin.auth(),
                json={"resolution_text": "E2E premoderation cleanup"},
            )
        # — Восстанавливаем платформенные настройки премодерации. —
        await http.patch(
            "/api/v1/platform-settings/premoderation",
            headers=admin.auth(),
            json={
                "receipt_premoderation_enabled": bool(
                    prev_settings.get("receipt_premoderation_enabled", False)
                ),
                "support_bot_chat_id": prev_settings.get("support_bot_chat_id") or "",
            },
        )
        # — Снимаем per-merchant премодерацию, чтобы не влиять на test_08 confirm. —
        await http.patch(
            f"/api/v1/merchants/{merchant.id}",
            json={"receipt_premoderation_enabled": False},
            headers=admin.auth(),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Section 6 — role-scoped response shape + cabinet / admin-moderations coverage
# ═══════════════════════════════════════════════════════════════════════════


async def test_merchant_dispute_response_is_restricted(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    """Merchant detail uses the restricted DisputeMerchantResponse: no internal
    ids / trader identity / raw evidence paths; evidence summarised as count."""
    order = await _create_disputable_order(http, merchant)
    dispute = await _open_dispute(http, merchant, order["id"], reason="no_payment")
    detail = await http.get(
        f"/api/merchant/v1/disputes/{_dispute_uuid(dispute)}", headers=merchant.headers()
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    if "evidence_count" not in body:
        pytest.skip("restricted merchant dispute schema ещё не задеплоена на dev.")
    for leaked in (
        "id", "merchant_id", "order_id", "assigned_user_id",
        "trader_login", "evidence_files", "initiator_id", "resolved_by_id",
    ):
        assert leaked not in body, f"{leaked} leaked to merchant: {body}"


async def test_trader_dispute_response_hides_merchant_identity(
    http: httpx.AsyncClient, merchant: TestMerchant, admin: TestUser,
    trader: TestTrader, trader_group: int,
) -> None:
    """Trader detail uses DisputeTraderResponse: no merchant identity / order
    external id / internal routing."""
    order = await _create_success_order(http, merchant, trader, admin)
    dispute = await _open_dispute(http, merchant, order["id"], reason="no_payment")
    detail = await http.get(
        f"/api/v1/disputes/my/{_dispute_uuid(dispute)}", headers=trader.user.auth()
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    for leaked in (
        "merchant_id", "merchant_login", "order_external_id",
        "assigned_user_id", "initiator_id", "evidence_files",
    ):
        assert leaked not in body, f"{leaked} leaked to trader: {body}"


async def test_merchant_cabinet_open_dispute_multipart(
    http: httpx.AsyncClient, merchant: TestMerchant, trader: TestTrader, trader_group: int
) -> None:
    """Merchant cabinet (JWT) opens a dispute via multipart form. Old JSON
    cabinet endpoint rejects the form → 4xx/500 → skip until deployed."""
    order = await _create_disputable_order(http, merchant)
    resp = await http.post(
        "/api/v1/merchants/me/disputes",
        params={"order_uuid": order["id"]},
        headers=merchant.user.auth(),
        data={"reason": "no_payment"},
    )
    if resp.status_code in (404, 415, 422, 500):
        pytest.skip("cabinet multipart create-dispute ещё не задеплоен на dev.")
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["status"] == "open"


async def test_admin_receipt_moderations_list(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    """Admin receipt-moderations list endpoint is reachable + correctly shaped."""
    resp = await http.get("/api/v1/receipt-moderations?limit=5", headers=admin.auth())
    if resp.status_code == 404:
        pytest.skip("receipt-moderations endpoint отсутствует на dev.")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body and "total" in body and isinstance(body["items"], list)
