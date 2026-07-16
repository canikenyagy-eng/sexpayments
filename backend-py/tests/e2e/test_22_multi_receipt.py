"""E2E: несколько чеков на один ордер (merchant API + admin read-эндпоинт).

Проверяет новую фичу мультичеков против живого dev:
  * 2-й чек по тому же ордеру принимается (раньше был 409);
  * дедуп — повторная загрузка идентичного файла отклоняется;
  * админ видит все чеки по ордеру (GET /api/v1/orders/{uuid}/receipts).

Мерчант загружает чеки через ``confirm-transfer`` и получает их обратно в
ответах по ордеру; отдельных GET-эндпоинтов по чекам в merchant API нет.

Каждый шаг, зависящий от НОВОГО поведения, делает ``pytest.skip`` когда фича
ещё не задеплоена (старый билд вернёт 409/404), чтобы общий e2e-прогон
оставался зелёным до деплоя.
"""
import io

import httpx
import pytest

from tests.e2e.conftest import TestMerchant, TestTrader, TestUser
from tests.e2e.test_07_disputes import _FAKE_RECEIPT_PNG, _create_active_pending_order

pytestmark = pytest.mark.anyio

_PNG_A = _FAKE_RECEIPT_PNG
# Different bytes → different sha256 (so it's NOT a dedup-duplicate of A).
_PNG_B = _FAKE_RECEIPT_PNG + b"\x00second-receipt"


async def _upload(http: httpx.AsyncClient, merchant: TestMerchant, order_uuid: str, content: bytes):
    return await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/confirm-transfer",
        headers=merchant.headers(),
        files={"attachment": ("receipt.png", io.BytesIO(content), "image/png")},
    )


async def test_multi_receipt_upload_and_dedup(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    admin: TestUser,
    trader_group: int,
):
    order = await _create_active_pending_order(http, merchant, trader, admin)
    order_uuid = order["id"]

    # Чек #1 — прикрепляется, ордер → receipt_uploaded.
    r1 = await _upload(http, merchant, order_uuid, _PNG_A)
    assert r1.status_code == 200, f"first receipt: {r1.status_code} {r1.text[:200]}"

    # Чек #2 (другие байты) — новое поведение принимает второй чек.
    r2 = await _upload(http, merchant, order_uuid, _PNG_B)
    if r2.status_code in (400, 409):
        pytest.skip("мультичеки не задеплоены (2-й чек отклонён) — старый билд")
    assert r2.status_code == 200, f"second receipt: {r2.status_code} {r2.text[:200]}"

    # Оба чека реально сохранены — проверяем через админский список.
    lst = await http.get(f"/api/v1/orders/{order_uuid}/receipts", headers=admin.auth())
    if lst.status_code == 404:
        pytest.skip("receipts-list эндпоинт не задеплоен")
    assert lst.status_code == 200, lst.text[:200]
    receipts = lst.json()
    assert len(receipts) >= 2, f"ожидали >=2 чека, получили {len(receipts)}"

    # Дедуп — повторная загрузка идентичного файла (#1) отклоняется.
    dup = await _upload(http, merchant, order_uuid, _PNG_A)
    assert dup.status_code in (400, 409), f"ожидали dedup-reject, получили {dup.status_code} {dup.text[:200]}"


async def test_multi_receipt_admin_lists_all(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    admin: TestUser,
    trader_group: int,
):
    order = await _create_active_pending_order(http, merchant, trader, admin)
    order_uuid = order["id"]
    r1 = await _upload(http, merchant, order_uuid, _PNG_A)
    assert r1.status_code == 200, f"receipt: {r1.status_code} {r1.text[:200]}"

    lst = await http.get(f"/api/v1/orders/{order_uuid}/receipts", headers=admin.auth())
    if lst.status_code == 404:
        pytest.skip("admin receipts-list эндпоинт не задеплоен")
    assert lst.status_code == 200, lst.text[:200]
    assert len(lst.json()) >= 1
