"""
E2E нагрузочный / перф-тест создания payin-заявок.

ОПТ-ИН (`@pytest.mark.slow`): исключён из дефолтного прогона (`-m "not slow"`),
т.к. создаёт много РЕАЛЬНЫХ ордеров на shared-dev. Запускать явно и с `-s`,
чтобы видеть отчёт:

    ~/.venvs/primepay-e2e/bin/python -m pytest \
        tests/e2e/test_19_perf_load.py -m slow -s -q

Параметры через env:
    E2E_PERF_ORDERS=80          # сколько заявок в каждом сценарии
    E2E_PERF_CONCURRENCY=20     # параллелизм для конкурентного всплеска
    E2E_PERF_AMOUNT=100         # сумма в RUB для УСПЕШНЫХ заявок (>=100 — min)
    E2E_PERF_NOREQ_AMOUNT=60000 # сумма для сценария «нет реквизита» (> max_tx 50000)

Сценарии:
  • успешные заявки — sequential + concurrent (горячий путь pooling / tx_commit /
    trader_fee / reload);
  • путь ОТКАЗА «нет подходящего реквизита» — sequential + concurrent: сумма выше
    `limit_max_transaction` реквизита, поэтому pooling его исключает и ничего не
    отдаёт → 404. Меряем латентность/пропускную способность именно отказа (он же
    бенефициар оптимизаций pooling: ACL-в-SQL, pre-filter, raiseload).

Созданные (успешные) заявки остаются PENDING и истекают по order_ttl мерчанта.
Роутинг детерминированный: мерчант привязан к нашему трейдеру через группу,
поэтому pooling берёт только наш реквизит — изолировать чужие не нужно.
"""
import asyncio
import os
import statistics
import time
from collections import Counter

import httpx
import pytest

from tests.e2e.conftest import (
    BASE_URL,
    TIMEOUT,
    TestMerchant,
    TestTrader,
    TestUser,
    admin_deposit,
    rand_suffix,
)

pytestmark = [pytest.mark.anyio, pytest.mark.slow]

PERF_ORDERS = int(os.getenv("E2E_PERF_ORDERS", "80"))
PERF_CONCURRENCY = int(os.getenv("E2E_PERF_CONCURRENCY", "20"))
AMOUNT_RUB = float(os.getenv("E2E_PERF_AMOUNT", "100"))
# Above the default requisite limit_max_transaction (50000) → the requisite is
# excluded by amount → pooling returns nothing → 404 "no available requisite".
NOREQ_AMOUNT = float(os.getenv("E2E_PERF_NOREQ_AMOUNT", "60000"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _report(
    title: str, latencies_ms: list[float], wall_s: float, statuses: list[int],
    *, ok_status: int = 201,
) -> None:
    """Print a perf report. ``ok_status`` is the EXPECTED outcome — 201 for the
    success scenarios, 404 for the no-requisite (rejection) scenarios — so the
    latency/throughput lines describe that path, not accidental other codes."""
    n = len(statuses)
    ok = statuses.count(ok_status)
    label = {201: "ok", 404: "404"}.get(ok_status, str(ok_status))
    print(f"\n──── {title} ────")
    print(
        f"  orders={n}  {label}={ok}  other={n - ok}  wall={wall_s:.2f}s  "
        f"throughput={ok / wall_s:.1f} {label}/s"
    )
    if latencies_ms:
        print(
            f"  {label} latency ms: "
            f"p50={_pct(latencies_ms, 50):.0f}  "
            f"p95={_pct(latencies_ms, 95):.0f}  "
            f"p99={_pct(latencies_ms, 99):.0f}  "
            f"max={max(latencies_ms):.0f}  "
            f"avg={statistics.mean(latencies_ms):.0f}"
        )
    other = Counter(s for s in statuses if s != ok_status)
    if other:
        print(f"  non-{ok_status} breakdown: {dict(other)}")


async def _create_payin(client: httpx.AsyncClient, merchant: TestMerchant, amount: float) -> tuple[int, float]:
    """Create one payin; return (status_code, latency_ms)."""
    t0 = time.perf_counter()
    resp = await client.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": amount,
            "currency": "RUB",
            "payment_method": "sbp",
            "internalId": f"perf_{rand_suffix()}",
            "userId": "e2e_perf",
            "issue_requisite_async": False,
        },
    )
    return resp.status_code, (time.perf_counter() - t0) * 1000.0


async def _topup(http: httpx.AsyncClient, admin: TestUser, trader: TestTrader) -> None:
    """Large WORK deposit so escrow-freeze never runs out of funds under load."""
    await admin_deposit(
        http, admin, user_id=trader.user.id, amount=1_000_000,
        reason="e2e_perf_topup",
    )


# ---------------------------------------------------------------------------
# 1) Sequential — «фигачим подряд» одна за другой (чистая латентность пути)
# ---------------------------------------------------------------------------

async def test_payin_throughput_sequential(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    await _topup(http, admin, trader)

    statuses: list[int] = []
    latencies: list[float] = []
    t0 = time.perf_counter()
    for _ in range(PERF_ORDERS):
        st, dt = await _create_payin(http, merchant, AMOUNT_RUB)
        statuses.append(st)
        if st == 201:
            latencies.append(dt)
    wall = time.perf_counter() - t0

    _report(f"sequential x{PERF_ORDERS}", latencies, wall, statuses)

    # Под нагрузкой сервер не должен отдавать 5xx — только 2xx/4xx.
    server_errors = [s for s in statuses if s >= 500]
    assert not server_errors, f"server 5xx under sequential load: {server_errors}"
    # На чистом окружении подавляющее большинство должно успешно создаться.
    ok = statuses.count(201)
    assert ok >= int(PERF_ORDERS * 0.8), (
        f"too few successful orders: {ok}/{PERF_ORDERS} "
        f"(non-201: {dict(Counter(s for s in statuses if s != 201))})"
    )


# ---------------------------------------------------------------------------
# 2) Concurrent burst — «сильная нагрузка», N заявок с параллелизмом
# ---------------------------------------------------------------------------

async def test_payin_burst_concurrency(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    await _topup(http, admin, trader)

    # Выделенный клиент с бо́льшим пулом соединений, чтобы реально нагрузить
    # параллелизмом (session-клиент из conftest ограничен 10 соединениями).
    limits = httpx.Limits(
        max_connections=PERF_CONCURRENCY + 5,
        max_keepalive_connections=PERF_CONCURRENCY,
    )
    sem = asyncio.Semaphore(PERF_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=True, limits=limits
    ) as client:

        async def _one() -> tuple[int, float]:
            async with sem:
                return await _create_payin(client, merchant, AMOUNT_RUB)

        t0 = time.perf_counter()
        results = await asyncio.gather(
            *[_one() for _ in range(PERF_ORDERS)], return_exceptions=True
        )
        wall = time.perf_counter() - t0

    # Исключения сети считаем как «999» (не 5xx сервера, но и не успех).
    statuses = [r[0] if isinstance(r, tuple) else 999 for r in results]
    latencies = [r[1] for r in results if isinstance(r, tuple) and r[0] == 201]

    _report(f"concurrent x{PERF_ORDERS} (par={PERF_CONCURRENCY})", latencies, wall, statuses)

    server_errors = [s for s in statuses if 500 <= s < 999]
    assert not server_errors, f"server 5xx under concurrent load: {server_errors}"
    # Конкурентные заявки на один реквизит сериализуются на FOR UPDATE-локе;
    # часть может законно получить 'busy' (4xx). Требуем лишь, что система
    # выдержала и значимая доля создалась.
    ok = statuses.count(201)
    assert ok >= max(1, PERF_ORDERS // 4), (
        f"too few successful orders under concurrency: {ok}/{PERF_ORDERS} "
        f"(breakdown: {dict(Counter(statuses))})"
    )


# ---------------------------------------------------------------------------
# 3) No-requisite — долбёжка пути «нет подходящего реквизита» (→ 404)
# ---------------------------------------------------------------------------
# Сумма выше limit_max_transaction реквизита → pooling его исключает и ничего не
# отдаёт → 404 "No available requisite". Этот путь НЕ берёт FOR UPDATE-лок (нечего
# клеймить), поэтому концентрированная конкуренция тут отдаёт ~все 404 без 'busy'.

async def test_no_requisite_sequential(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    await _topup(http, admin, trader)

    statuses: list[int] = []
    latencies: list[float] = []
    t0 = time.perf_counter()
    for _ in range(PERF_ORDERS):
        st, dt = await _create_payin(http, merchant, NOREQ_AMOUNT)
        statuses.append(st)
        if st == 404:
            latencies.append(dt)
    wall = time.perf_counter() - t0

    _report(f"no-requisite sequential x{PERF_ORDERS}", latencies, wall, statuses, ok_status=404)

    assert not [s for s in statuses if s >= 500], (
        f"server 5xx on no-requisite path: {[s for s in statuses if s >= 500]}"
    )
    # Сумма заведомо выше лимита → ни одна заявка не должна создаться (201);
    # иначе где-то отроутился непригодный реквизит — это баг отбора.
    assert 201 not in statuses, "a payin unexpectedly succeeded on the no-requisite path"
    noreq = statuses.count(404)
    assert noreq >= int(PERF_ORDERS * 0.9), f"expected ~all 404, got {dict(Counter(statuses))}"


async def test_no_requisite_burst(
    http: httpx.AsyncClient,
    merchant: TestMerchant,
    trader: TestTrader,
    trader_group: int,
    admin: TestUser,
) -> None:
    await _topup(http, admin, trader)

    limits = httpx.Limits(
        max_connections=PERF_CONCURRENCY + 5,
        max_keepalive_connections=PERF_CONCURRENCY,
    )
    sem = asyncio.Semaphore(PERF_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=True, limits=limits
    ) as client:

        async def _one() -> tuple[int, float]:
            async with sem:
                return await _create_payin(client, merchant, NOREQ_AMOUNT)

        t0 = time.perf_counter()
        results = await asyncio.gather(
            *[_one() for _ in range(PERF_ORDERS)], return_exceptions=True
        )
        wall = time.perf_counter() - t0

    statuses = [r[0] if isinstance(r, tuple) else 999 for r in results]
    latencies = [r[1] for r in results if isinstance(r, tuple) and r[0] == 404]

    _report(
        f"no-requisite concurrent x{PERF_ORDERS} (par={PERF_CONCURRENCY})",
        latencies, wall, statuses, ok_status=404,
    )

    assert not [s for s in statuses if 500 <= s < 999], (
        f"server 5xx on no-requisite path: {[s for s in statuses if 500 <= s < 999]}"
    )
    assert 201 not in statuses, "a payin unexpectedly succeeded on the no-requisite path"
    assert statuses.count(404) >= int(PERF_ORDERS * 0.9), (
        f"expected ~all 404, got {dict(Counter(statuses))}"
    )
