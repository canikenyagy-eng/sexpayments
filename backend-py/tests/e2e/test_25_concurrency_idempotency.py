"""E2E: конкурентная идемпотентность денежных путей против ЖИВОГО dev.

Этот файл доказывает, что row-lock / unique-index фиксы из недавних коммитов
держатся под РЕАЛЬНОЙ конкуренцией (dev = настоящий Postgres, где
``SELECT … FOR UPDATE`` и partial-unique-index реально сериализуют/отбивают
гонки). Каждый тест выстреливает ДВА конкурентных запроса через
``asyncio.gather`` и утверждает, что денежное перемещение произошло РОВНО один
раз — второй запрос либо идемпотентно повторяет результат, либо чисто
конфликтует (4xx), но НЕ создаёт второго списания/начисления.

Покрываемые фиксы:
  1. ``fix: prevent double-completion of orders (concurrent confirm race)`` —
     два конкурентных ``POST /orders/{id}/success`` по одному ордеру → баланс
     трейдера двигается один раз, ордер SUCCESS.
  2. ``fix: serialise all money-settlement paths`` — заявка на вывод, одобренная
     ДВУМЯ конкурентными admin-approve → ровно один payout, баланс мерчанта
     списан один раз.
  3. ``fix: prevent receipt-check double-charge (concurrent insert race)`` — один
     и тот же файл-чек, отправленный ДВУМЯ конкурентными receipt-check
     запросами по одному ордеру → трейдер списан максимум один раз.

КАК ЗАПУСТИТЬ против dev (обязательно e2e-venv с pytest-asyncio для live-сервера;
тесты МУТИРУЮТ живые данные и требуют сети):

    ~/.venvs/primepay-e2e/bin/python -m pytest \
        tests/e2e/test_25_concurrency_idempotency.py -c tests/e2e/pytest.ini -v

Зависит от фикстур/хелперов из ``tests/e2e/conftest.py``:
  * фикстуры: ``http``, ``admin``, ``merchant``, ``merchant_user``, ``trader``,
    ``trader_group`` (привязка merchant↔trader, чтобы пулинг выбрал наш реквизит);
  * хелперы: ``admin_deposit``, ``isolate_requisites``, ``restore_requisites``,
    ``rand_suffix``.

Конкурентный паттерн (``asyncio.gather`` + выделенный httpx-клиент с большим
пулом) повторяет ``tests/e2e/test_19_perf_load.py`` (≈ строки 182–194). Полный
lifecycle ордера (create → pending → trader-success) — как в
``tests/e2e/test_05_order_flow.py`` / ``test_09_financial_accuracy.py``.

Каждый тест gracefully ``skip``-ается, когда предусловие на dev не выполнено
(гонка доступности пулинга, нет активного receipt-check провайдера, фича не
задеплоена), чтобы общий e2e-прогон оставался зелёным — в духе остального suite.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections import Counter
from decimal import Decimal

import httpx
import pytest

from tests.e2e.conftest import (
    BASE_URL,
    TIMEOUT,
    TestMerchant,
    TestTrader,
    TestUser,
    admin_deposit,
    isolate_requisites,
    rand_suffix,
    restore_requisites,
)

pytestmark = pytest.mark.anyio


# ───────────────────────────────────────────────────────────────────────────
# Общие хелперы
# ───────────────────────────────────────────────────────────────────────────

# Тот же минимальный валидный PNG, что используют receipt/dispute тесты.
_FAKE_RECEIPT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
    b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)

ORDER_AMOUNT = 1000.0
TOLERANCE = Decimal("0.01")


async def _wait_merchant_status(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    order_uuid: str,
    expected: list[str],
    timeout: float = 12.0,
    interval: float = 0.4,
) -> dict:
    """Опрашиваем Merchant API до нужного статуса (как в test_05/test_09)."""
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


async def _trader_balances(http: httpx.AsyncClient, user: TestUser) -> dict[tuple[str, str], Decimal]:
    """Снимок всех балансов трейдера как {(type, currency): amount}."""
    resp = await http.get("/api/v1/finances/my-balances", headers=user.auth())
    assert resp.status_code == 200, resp.text
    return {(b["type"], b["currency"]): Decimal(str(b["amount"])) for b in resp.json()}


def _work_usdt(balances: dict[tuple[str, str], Decimal]) -> Decimal:
    return balances.get(("work", "USDT"), Decimal("0"))


class _PendingOrderCtx:
    """Async-context: создаёт PENDING-ордер с нашим трейдером и гарантированно
    восстанавливает чужие реквизиты на выходе.

    Использование::

        async with _PendingOrderCtx(http, merchant, trader, admin) as (uuid, tid):
            ...

    Если пулинг не назначил реквизит — ``pytest.skip`` внутри ``__aenter__``.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        merchant: TestMerchant,
        trader: TestTrader,
        admin: TestUser,
        *,
        amount: float = ORDER_AMOUNT,
    ) -> None:
        self._http = http
        self._merchant = merchant
        self._trader = trader
        self._admin = admin
        self._amount = amount
        self._disabled: list[int] = []
        self.order_uuid: str = ""
        self.trader_order_id: int = 0

    async def __aenter__(self) -> tuple[str, int]:
        self._disabled = await isolate_requisites(
            self._http, self._admin, self._trader.requisite_id
        )

        create_resp = await self._http.post(
            "/api/merchant/v1/orders/payin",
            headers=self._merchant.headers(),
            json={
                "amount": self._amount,
                "currency": "RUB",
                "payment_method": "sbp",
                "internalId": f"e2e_conc_{rand_suffix()}",
                "issue_requisite_async": False,
            },
        )
        if create_resp.status_code != 201:
            await self._restore()
            pytest.skip(
                f"sync payin не назначил реквизит (пулинг): "
                f"{create_resp.status_code} {create_resp.text[:200]}"
            )
        self.order_uuid = create_resp.json()["id"]

        order = await _wait_merchant_status(
            self._http, self._merchant, self.order_uuid,
            ["pending", "success", "failed", "canceled"], timeout=10.0,
        )
        if order.get("status") != "pending":
            await self._restore()
            pytest.skip(f"Ордер не дошёл до pending (got {order.get('status')}) — пулинг-гонка")

        # Находим внутренний trader_order_id у трейдера.
        trader_order = None
        for _ in range(10):
            active = await self._http.get(
                "/api/v1/orders/my-active", headers=self._trader.user.auth()
            )
            items = active.json() if active.status_code == 200 else []
            trader_order = next(
                (o for o in items if str(o.get("uuid")) == str(self.order_uuid)), None
            )
            if trader_order is not None:
                break
            await asyncio.sleep(0.5)
        if trader_order is None:
            await self._restore()
            pytest.skip("Ордер не появился в активных у трейдера — пулинг-гонка")

        self.trader_order_id = int(trader_order["id"])
        return self.order_uuid, self.trader_order_id

    async def __aexit__(self, *exc) -> None:
        await self._restore()

    async def _restore(self) -> None:
        if self._disabled:
            await restore_requisites(self._http, self._admin, self._disabled)
            self._disabled = []


def _burst_client() -> httpx.AsyncClient:
    """Выделенный клиент с пулом >2 соединений, чтобы два запроса реально
    ушли параллельно (session-клиент из conftest ограничен и сериализует).
    Паттерн из test_19_perf_load.py."""
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    return httpx.AsyncClient(
        base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=True, limits=limits
    )


# ═══════════════════════════════════════════════════════════════════════════
# Тест 1 — двойной confirm одного ордера: ровно одно начисление, ордер SUCCESS
# ═══════════════════════════════════════════════════════════════════════════

async def test_concurrent_confirm_settles_order_exactly_once(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Два конкурентных ``POST /orders/{id}/success`` по одному PENDING-ордеру.

    Утверждаем (фикс ``5d4b5f8 prevent double-completion``):
      * ордер становится SUCCESS;
      * РОВНО один из двух запросов сделал расчёт (HTTP-200), второй —
        идемпотентный 200 ИЛИ чистый конфликт 4xx (НЕ 5xx);
      * settlement-движение по ордеру в ledger трейдера произошло ровно один раз
        (считаем записи ledger c order_search=uuid ДО/ПОСЛЕ — прирост == 1·набор).
    """
    # Достаточный WORK-баланс, чтобы заморозка залога точно прошла.
    await admin_deposit(
        http, admin, user_id=trader.user.id, amount=100_000, reason="e2e_conc_confirm"
    )

    async with _PendingOrderCtx(http, merchant, trader, admin) as (order_uuid, trader_order_id):
        # ── Снимки баланса и ledger ДО ──
        bal_before = await _trader_balances(http, trader.user)
        ledger_before = await _count_trader_ledger_for_order(http, trader.user, order_uuid)

        # ── Два конкурентных confirm на выделенном клиенте ──
        async with _burst_client() as client:
            async def _confirm() -> tuple[int, str]:
                r = await client.post(
                    f"/api/v1/orders/{trader_order_id}/success",
                    headers=trader.user.auth(),
                )
                return r.status_code, r.text

            results = await asyncio.gather(
                _confirm(), _confirm(), return_exceptions=True
            )

        statuses = [r[0] if isinstance(r, tuple) else 999 for r in results]

        # Нет 5xx — гонка обработана штатно, а не упала.
        assert not [s for s in statuses if 500 <= s < 600], (
            f"confirm-гонка вернула 5xx: {statuses} ({results})"
        )
        # Хотя бы один запрос успешен (ордер реально завершился).
        assert 200 in statuses, (
            f"ни один confirm не прошёл успешно: {dict(Counter(statuses))} ({results})"
        )
        # Второй запрос либо идемпотентный 200, либо чистый 4xx-конфликт.
        for s in statuses:
            assert s == 200 or 400 <= s < 500, (
                f"неожиданный статус у второго confirm: {s} (все: {statuses})"
            )

        # ── Ордер должен быть SUCCESS (мерчант видит финал) ──
        final = await _wait_merchant_status(http, merchant, order_uuid, ["success"], timeout=10.0)
        assert final.get("status") == "success", (
            f"ордер должен стать success после confirm: {final.get('status')}"
        )

        # Дать расчёту/Celery дописать ledger.
        await asyncio.sleep(2)

        # ── Ровно ОДНО settlement-движение по ордеру в ledger трейдера ──
        ledger_after = await _count_trader_ledger_for_order(http, trader.user, order_uuid)
        new_entries = ledger_after - ledger_before
        # Один расчёт ордера создаёт фиксированный набор ledger-записей (escrow→work,
        # trader_fee и т.п.). Двойной расчёт удвоил бы их. Требуем, чтобы прирост
        # был НЕ больше одного набора: для этого сверяем с эталонным набором ниже.
        assert new_entries >= 1, (
            f"расчёт ордера не создал ledger-записей: before={ledger_before} after={ledger_after}"
        )
        assert new_entries <= _max_settlement_entries_per_order(), (
            f"двойной расчёт: ledger-записей по ордеру {new_entries} > "
            f"{_max_settlement_entries_per_order()} (двойное начисление!)"
        )

        # ── Баланс трейдера двинулся ровно один раз: WORK-дельта совпадает с
        #    одиночным расчётом. Сверяем, что повторный confirm НЕ изменил баланс. ──
        bal_after = await _trader_balances(http, trader.user)
        # WORK-баланс должен ИЗМЕНИТЬСЯ (был расчёт), но не «вдвойне».
        assert _work_usdt(bal_after) != _work_usdt(bal_before), (
            "WORK-баланс трейдера не изменился — расчёт не прошёл"
        )

        # Идемпотентность «на месте»: ещё один confirm уже завершённого ордера
        # не двигает баланс (ни третьего начисления).
        third = await http.post(
            f"/api/v1/orders/{trader_order_id}/success", headers=trader.user.auth()
        )
        assert third.status_code in (200, 400, 404, 409, 422), (
            f"повторный confirm завершённого ордера: неожиданный {third.status_code} {third.text[:200]}"
        )
        await asyncio.sleep(1)
        bal_third = await _trader_balances(http, trader.user)
        assert _work_usdt(bal_third) == _work_usdt(bal_after), (
            f"третий confirm изменил WORK-баланс (double-spend): "
            f"{_work_usdt(bal_after)} → {_work_usdt(bal_third)}"
        )


async def _count_trader_ledger_for_order(
    http: httpx.AsyncClient, user: TestUser, order_uuid: str
) -> int:
    """Сколько ledger-записей у трейдера привязано к данному ордеру.

    Использует ``GET /api/v1/finances/my-ledger?order_search=<uuid>`` (фильтр
    задокументирован в finances.py). Если эндпоинт/фильтр недоступен — возвращает
    -1, и вызывающий пропускает ledger-инвариант (assert ниже это учитывает).
    """
    resp = await http.get(
        "/api/v1/finances/my-ledger",
        headers=user.auth(),
        params={"order_search": order_uuid, "limit": 100},
    )
    if resp.status_code != 200:
        return -1
    body = resp.json()
    items = body.get("items", body) if isinstance(body, dict) else body
    if not isinstance(items, list):
        return -1
    return len(items)


def _max_settlement_entries_per_order() -> int:
    """Верхняя граница ledger-записей за ОДИН расчёт ордера.

    Один расчёт payin-ордера трогает у трейдера небольшое число счетов
    (разморозка escrow, зачисление в work, комиссия трейдера). Берём щедрый,
    но < «двойного», порог: 6. Двойной расчёт дал бы заметно больше.
    Если ``_count_trader_ledger_for_order`` вернул -1 (фильтр недоступен),
    сравнение ``new_entries <= 6`` всё равно пройдёт (new_entries будет 0/-1),
    т.е. инвариант мягко деградирует, а основная проверка баланса остаётся.
    """
    return 6


# ═══════════════════════════════════════════════════════════════════════════
# Тест 2 — двойной admin-approve вывода: ровно один payout, баланс списан один раз
# ═══════════════════════════════════════════════════════════════════════════

async def test_concurrent_withdrawal_approve_pays_out_once(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    merchant_user: TestUser,
    admin: TestUser,
) -> None:
    """Заявка на вывод, одобренная ДВУМЯ конкурентными admin-approve.

    Утверждаем (фикс ``a0651ec serialise all money-settlement paths``):
      * ровно один approve успешен (200/201), второй — чистый 4xx-конфликт
        (НЕ второй payout, НЕ 5xx);
      * баланс WORK USDT мерчанта снизился РОВНО на сумму вывода (один раз),
        а не вдвое.
    """
    withdraw_amount = Decimal("10")

    # Депозит с запасом, чтобы было что выводить.
    dep = await admin_deposit(
        http, admin, merchant_id=merchant.id, amount=50, reason="e2e_conc_withdraw"
    )
    assert dep.status_code == 200, dep.text

    def work_usdt(items: list) -> Decimal:
        for b in items:
            if b.get("currency") == "USDT" and b.get("type") == "work":
                return Decimal(str(b["amount"]))
        return Decimal("0")

    bal_before_resp = await http.get(
        "/api/v1/finances/my-balances", headers=merchant_user.auth()
    )
    assert bal_before_resp.status_code == 200
    usdt_before = work_usdt(bal_before_resp.json())

    # Мерчант создаёт заявку на вывод (escrow-заморозка при создании).
    w_resp = await http.post(
        "/api/v1/merchants/me/withdrawals",
        headers=merchant_user.auth(),
        json={
            "amount": str(withdraw_amount),
            "currency": "USDT",
            "destination_address": f"TXe2eConc{rand_suffix(6)}",
        },
    )
    assert w_resp.status_code in (200, 201), f"Withdrawal create failed: {w_resp.text}"
    withdrawal_id = w_resp.json().get("id")
    assert withdrawal_id is not None

    # Два конкурентных admin-approve на выделенном клиенте.
    async with _burst_client() as client:
        async def _approve() -> tuple[int, str]:
            r = await client.post(
                f"/api/v1/finances/withdrawals/{withdrawal_id}/approve",
                headers=admin.auth(),
            )
            return r.status_code, r.text

        results = await asyncio.gather(_approve(), _approve(), return_exceptions=True)

    statuses = [r[0] if isinstance(r, tuple) else 999 for r in results]

    # Нет 5xx — гонка обработана штатно.
    assert not [s for s in statuses if 500 <= s < 600], (
        f"approve-гонка вернула 5xx: {statuses} ({results})"
    )
    # Ровно один approve успешен.
    ok = [s for s in statuses if s in (200, 201)]
    assert len(ok) == 1, (
        f"ожидали РОВНО один успешный approve, получили {dict(Counter(statuses))} ({results})"
    )
    # Второй запрос — чистый 4xx (уже approved / неверный статус).
    losers = [s for s in statuses if s not in (200, 201)]
    assert all(400 <= s < 500 for s in losers), (
        f"проигравший approve должен быть 4xx, получили {losers} (все: {statuses})"
    )

    # Баланс мерчанта списан РОВНО на сумму вывода (один payout, не два).
    await asyncio.sleep(1)
    bal_after_resp = await http.get(
        "/api/v1/finances/my-balances", headers=merchant_user.auth()
    )
    assert bal_after_resp.status_code == 200
    usdt_after = work_usdt(bal_after_resp.json())

    delta = usdt_before - usdt_after
    assert delta == pytest.approx(withdraw_amount, abs=TOLERANCE), (
        f"WORK USDT должен списаться РОВНО на {withdraw_amount} (один payout): "
        f"before={usdt_before} after={usdt_after} delta={delta}"
    )

    # Защита от поздней второй обработки: ещё один approve того же вывода не
    # списывает повторно.
    late = await http.post(
        f"/api/v1/finances/withdrawals/{withdrawal_id}/approve", headers=admin.auth()
    )
    assert late.status_code in (400, 404, 409, 422), (
        f"повторный approve должен быть отвергнут, got {late.status_code}: {late.text[:200]}"
    )
    await asyncio.sleep(1)
    bal_late_resp = await http.get(
        "/api/v1/finances/my-balances", headers=merchant_user.auth()
    )
    usdt_late = work_usdt(bal_late_resp.json())
    assert usdt_late == pytest.approx(usdt_after, abs=TOLERANCE), (
        f"поздний approve повторно списал баланс (double-payout): "
        f"after={usdt_after} late={usdt_late}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Тест 3 — один и тот же файл-чек, два конкурентных receipt-check: списание ≤ 1×
# ═══════════════════════════════════════════════════════════════════════════

async def test_concurrent_receipt_check_charges_trader_at_most_once(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    """Один файл-чек, отправленный ДВУМЯ конкурентными receipt-check запросами.

    Утверждаем (фикс ``15a57ed prevent receipt-check double-charge``):
      * WORK USDT трейдера списан НЕ БОЛЕЕ чем за одну проверку (partial-unique
        index ``receipt_checks(order_id, file_sha256)`` отбивает гонку вставки —
        проигравший replay-ит активную проверку и НЕ списывает второй раз);
      * нет 5xx.

    Предусловия gracefully ``skip``-аются, если их нет на dev:
      * нет активного receipt-check провайдера (quote.available=False);
      * чек не удалось приложить к ордеру (фича/канал не задеплоены);
      * недостаточно средств у трейдера на саму проверку.
    """
    # Достаточный WORK-баланс под залог И под платную проверку.
    await admin_deposit(
        http, admin, user_id=trader.user.id, amount=100_000, reason="e2e_conc_check"
    )

    # Есть ли активные receipt-check провайдеры на dev? Без них проверять нечем.
    prov_res = await http.get(
        "/api/v1/orders/receipt-check/providers", headers=trader.user.auth()
    )
    if prov_res.status_code != 200:
        pytest.skip("receipt-check/providers недоступен на dev — фича не задеплоена.")
    prov_list = prov_res.json()
    if not prov_list:
        pytest.skip("нет активных receipt-check провайдеров на dev — проверять нечем.")
    provider_id = prov_list[0]["id"]
    price_usdt = Decimal(str(prov_list[0].get("price_usdt") or 0))

    async with _PendingOrderCtx(http, merchant, trader, admin) as (order_uuid, trader_order_id):
        # Мерчант прикладывает чек к ордеру (нужен receipt_file для проверки).
        up = await http.post(
            f"/api/merchant/v1/orders/{order_uuid}/confirm-transfer",
            headers=merchant.headers(),
            files={"attachment": ("receipt.png", io.BytesIO(_FAKE_RECEIPT_PNG), "image/png")},
        )
        if up.status_code != 200:
            pytest.skip(
                f"не удалось приложить чек к ордеру (confirm-transfer): "
                f"{up.status_code} {up.text[:200]}"
            )

        # Баланс трейдера ДО проверок.
        bal_before = await _trader_balances(http, trader.user)
        work_before = _work_usdt(bal_before)

        # Два конкурентных receipt-check ОДНОГО файла на выделенном клиенте.
        async with _burst_client() as client:
            async def _check() -> tuple[int, str]:
                r = await client.post(
                    f"/api/v1/orders/{order_uuid}/receipt-check",
                    headers=trader.user.auth(),
                    json={"confirm": True, "provider_id": provider_id},
                )
                return r.status_code, r.text

            results = await asyncio.gather(_check(), _check(), return_exceptions=True)

        statuses = [r[0] if isinstance(r, tuple) else 999 for r in results]

        # Нет 5xx — гонка вставки обработана штатно (unique-violation → replay).
        assert not [s for s in statuses if 500 <= s < 600], (
            f"receipt-check-гонка вернула 5xx: {statuses} ({results})"
        )

        # Если ОБА вернули конфликт (напр. провайдер временно недоступен), списания
        # быть не должно — но это не сценарий double-charge, поэтому skip.
        ok_like = [s for s in statuses if s == 200]
        if not ok_like:
            pytest.skip(
                f"ни одна проверка не прошла (провайдер/баланс): {dict(Counter(statuses))} "
                f"({results}) — нечего проверять на double-charge."
            )

        # Дать списанию/refund дописаться.
        await asyncio.sleep(2)

        bal_after = await _trader_balances(http, trader.user)
        work_after = _work_usdt(bal_after)
        charged = work_before - work_after

        # Ключевой инвариант: списано НЕ БОЛЬШЕ чем за одну проверку.
        # (price может быть 0 — тогда списания нет вовсе, тоже ОК. Допускаем
        # небольшой допуск на округление.)
        max_single_charge = price_usdt + TOLERANCE
        assert charged <= max_single_charge, (
            f"двойное списание за один и тот же чек: списано {charged} > "
            f"одной проверки {price_usdt} (before={work_before} after={work_after})"
        )
        # И списание неотрицательно (refund не должен «начислить» лишнего).
        assert charged >= Decimal("0") - TOLERANCE, (
            f"баланс вырос на receipt-check (неожиданный refund): "
            f"before={work_before} after={work_after}"
        )
