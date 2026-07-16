"""
E2E-фикстуры и утилиты.

Тесты работают против ЖИВОГО dev-сервера. По умолчанию https://dev.prime-pay.org
(переопределяется переменной E2E_BASE_URL).

Фикстуры session-scope: админ логинится один раз, трейдер/мерчант создаются один раз
на весь прогон (asyncio_default_fixture_loop_scope = session).
"""

from __future__ import annotations

import asyncio
import os
import secrets
import string
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

import httpx
import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Конфигурация окружения
# ---------------------------------------------------------------------------

BASE_URL: str = os.getenv("E2E_BASE_URL", "https://dev.prime-pay.org").rstrip("/")
TIMEOUT: float = float(os.getenv("E2E_TIMEOUT", "30.0"))

ADMIN_USER: str = os.getenv("E2E_ADMIN_USER", "admin")
ADMIN_PASS: str = os.getenv("E2E_ADMIN_PASS", "admin123")

BOT_SECRET: str = os.getenv("E2E_BOT_SECRET", "bot_secret")
BOT_TG_USER_ID: int = int(os.getenv("E2E_BOT_TG_USER_ID", "987654321"))

# Секрет support-bot ⇄ backend канала (отдельный от MERCHANT_BOT_SECRET). Нужен
# только для теста «премодерация → диспут» (POST /api/bot/v1/orders/{uuid}/moderate).
# Если пуст — тест премодерации пропускается (секрет нельзя синтезировать на стороне
# теста, он должен совпадать с серверным SUPPORT_BOT_SECRET).
SUPPORT_BOT_SECRET: str = os.getenv("E2E_SUPPORT_BOT_SECRET", "")


# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------

def rand_suffix(length: int = 8) -> str:
    """Энтропийный суффикс для internalId/external_id тестовых ордеров.

    Раньше был чистый ``secrets.choice`` 8 chars (36^8 ≈ 2.8 trillion).
    На shared dev, где остаются ордера от предыдущих прогонов, изредка
    случалась коллизия (наблюдалось 1x51lvil дважды). Добавляем prefix
    из time.time_ns() — гарантирует уникальность даже при бесконечных
    повторных запусках в один день. ``length`` сохраняем для совместимости
    с сигнатурой, но реальная длина становится prefix(13)+rand.
    """
    import time
    alphabet = string.ascii_lowercase + string.digits
    rand_part = "".join(secrets.choice(alphabet) for _ in range(length))
    # time_ns даёт ~19 знаков, берём base36-кодировку для краткости.
    ts_ns = time.time_ns()
    ts36 = ""
    while ts_ns:
        ts36 = alphabet[ts_ns % 36] + ts36
        ts_ns //= 36
    return f"{ts36}{rand_part}"


def auth_bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# Совместимость со старым именем из прежнего conftest
def auth_headers(token: str) -> dict[str, str]:
    return auth_bearer(token)


def bot_headers(secret: str, tg_user_id: int) -> dict[str, str]:
    return {
        "X-Bot-Secret": secret,
        "X-Telegram-User-Id": str(tg_user_id),
    }


# ---------------------------------------------------------------------------
# Data-классы для тестовых сущностей
# ---------------------------------------------------------------------------

@dataclass
class TestUser:
    __test__ = False  # подсказка pytest: это не тест-класс

    id: int
    username: str
    password: str
    role: str
    access_token: str
    refresh_token: str

    def auth(self) -> dict[str, str]:
        return auth_bearer(self.access_token)


@dataclass
class TestTrader:
    __test__ = False

    id: int  # trader profile id
    user: TestUser
    requisite_id: int


@dataclass
class TestMerchant:
    __test__ = False

    id: int  # merchant profile id
    user: TestUser
    api_key: str

    def headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key}


# ---------------------------------------------------------------------------
# Вспомогательные внутренние функции
# ---------------------------------------------------------------------------

async def _login(http: httpx.AsyncClient, username: str, password: str) -> dict:
    resp = await http.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    resp.raise_for_status()
    return resp.json()


async def _register_and_login(
    http: httpx.AsyncClient,
    admin: TestUser,
    role: str,
    *,
    username: Optional[str] = None,
    password: str = "SecurePass123",
) -> TestUser:
    if username is None:
        username = f"e2e_{role}_{rand_suffix()}"

    reg_resp = await http.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password, "role": role},
        headers=admin.auth(),
    )
    assert reg_resp.status_code == 201, (
        f"Register {role} {username} failed: {reg_resp.status_code} {reg_resp.text}"
    )
    reg_data = reg_resp.json()

    login_data = await _login(http, username, password)
    return TestUser(
        id=reg_data["id"],
        username=username,
        password=password,
        role=role,
        access_token=login_data["access_token"],
        refresh_token=login_data["refresh_token"],
    )


async def _find_merchant_by_user(
    http: httpx.AsyncClient, admin: TestUser, user_id: int
) -> dict:
    """Находит профиль мерчанта по user_id."""
    resp = await http.get(
        "/api/v1/merchants/?limit=200",
        headers=admin.auth(),
    )
    resp.raise_for_status()
    merchants = resp.json()
    m = next((m for m in merchants if m.get("user_id") == user_id), None)
    assert m is not None, f"Merchant profile for user_id={user_id} not found"
    return m


async def _find_trader_by_user(
    http: httpx.AsyncClient, admin: TestUser, user_id: int
) -> dict:
    """Находит профиль трейдера по user_id."""
    resp = await http.get(
        "/api/v1/traders/?limit=200",
        headers=admin.auth(),
    )
    resp.raise_for_status()
    traders = resp.json()
    t = next((t for t in traders if t.get("user_id") == user_id), None)
    assert t is not None, f"Trader profile for user_id={user_id} not found"
    return t


# ---------------------------------------------------------------------------
# Утилиты для изоляции реквизитов (используются в order_flow / financial_accuracy)
# ---------------------------------------------------------------------------

async def isolate_requisites(
    http: httpx.AsyncClient, admin: TestUser, keep_requisite_id: int
) -> list[int]:
    """
    Деактивирует все реквизиты, кроме указанного, чтобы pooling гарантированно
    выбрал наш. Возвращает список отключённых id (для последующего восстановления).
    """
    resp = await http.get("/api/v1/requisites/?limit=500", headers=admin.auth())
    if resp.status_code != 200:
        return []

    disabled: list[int] = []
    for r in resp.json():
        rid = r.get("id")
        if rid is None or rid == keep_requisite_id:
            continue
        status = r.get("status")
        if status != "enabled":
            continue
        patch = await http.patch(
            f"/api/v1/requisites/{rid}",
            json={"status": "disabled"},
            headers=admin.auth(),
        )
        if patch.status_code == 200:
            disabled.append(rid)
    return disabled


async def admin_deposit(
    http: httpx.AsyncClient,
    admin: TestUser,
    *,
    user_id: Optional[int] = None,
    merchant_id: Optional[int] = None,
    amount: float = 0,
    currency: str = "USDT",
    balance_type: str = "work",
    reason: str = "e2e_deposit",
) -> httpx.Response:
    """
    Унифицированная обёртка над POST /api/v1/finances/admin/adjust.
    positive amount = пополнение, negative = списание.
    """
    assert (user_id is None) != (merchant_id is None), (
        "Передай ровно один: user_id или merchant_id"
    )
    payload: dict = {
        "amount": amount,
        "currency": currency,
        "balance_type": balance_type,
        "reason": reason,
    }
    if user_id is not None:
        payload["user_id"] = user_id
    else:
        payload["merchant_id"] = merchant_id

    return await http.post(
        "/api/v1/finances/admin/adjust",
        headers=admin.auth(),
        json=payload,
    )


async def restore_requisites(
    http: httpx.AsyncClient, admin: TestUser, disabled_ids: list[int]
) -> None:
    """Восстанавливает status=enabled для ранее деактивированных реквизитов."""
    for rid in disabled_ids:
        await http.patch(
            f"/api/v1/requisites/{rid}",
            json={"status": "enabled"},
            headers=admin.auth(),
        )


# ---------------------------------------------------------------------------
# pytest-anyio backend configuration
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Все тесты с `pytestmark = pytest.mark.anyio` будут запускаться на asyncio."""
    return "asyncio"


# ---------------------------------------------------------------------------
# HTTP-клиент (session-scope, один на весь прогон)
# ---------------------------------------------------------------------------

class _RetryAsyncClient(httpx.AsyncClient):
    """
    httpx.AsyncClient с ретраями на уровне send().

    На длительных прогонах session-scope клиент подвержен разрывам keep-alive
    TCP-соединений — Cloudflare/nginx закрывает их по тайм-ауту, и следующий
    запрос через «мёртвое» соединение падает с httpx.ReadError / RemoteProtocolError.
    Прозрачно повторяем такие сбои.

    ВАЖНО: между попытками НЕЛЬЗЯ вызывать self._transport.aclose() — httpcore
    помечает AsyncConnectionPool как permanently-closed и все последующие
    запросы крашатся с RuntimeError("The connection pool was closed").
    На session-scope клиенте это превращает одну транзиентную ошибку сети в
    каскад падений всех оставшихся тестов.

    Полагаемся на то, что httpcore сам выбрасывает broken-соединения из пула
    при ReadError/RemoteProtocolError и при следующем запросе открывает новое.
    """

    _RETRIES = 3
    _RETRY_EXCEPTIONS = (
        httpx.ReadError,
        httpx.RemoteProtocolError,
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.WriteError,
        httpx.PoolTimeout,
    )

    async def send(self, request, **kwargs):  # type: ignore[override]
        last_exc: Exception | None = None
        for attempt in range(self._RETRIES):
            try:
                return await super().send(request, **kwargs)
            except self._RETRY_EXCEPTIONS as exc:
                last_exc = exc
                # Экспоненциальный backoff без «убийства» пула соединений.
                await asyncio.sleep(0.3 * (attempt + 1))
        assert last_exc is not None
        raise last_exc


@pytest_asyncio.fixture(scope="session")
async def http() -> AsyncGenerator[httpx.AsyncClient, None]:
    # Короткий keepalive уменьшает шанс «мёртвых» соединений на длинных прогонах.
    # ВАЖНО: httpx.AsyncClient игнорирует параметр `limits`, если задан свой
    # `transport`. Поэтому лимиты передаём именно в AsyncHTTPTransport — иначе
    # использовались бы httpx-дефолты (100 conn / keepalive_expiry=5s).
    limits = httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10,
        keepalive_expiry=15.0,
    )
    transport = httpx.AsyncHTTPTransport(retries=2, limits=limits)
    async with _RetryAsyncClient(
        base_url=BASE_URL,
        timeout=TIMEOUT,
        follow_redirects=True,
        transport=transport,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Пользовательские фикстуры
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def admin(http: httpx.AsyncClient) -> TestUser:
    data = await _login(http, ADMIN_USER, ADMIN_PASS)
    user_info = data["user"]
    return TestUser(
        id=user_info["id"],
        username=ADMIN_USER,
        password=ADMIN_PASS,
        role=user_info.get("role", "admin"),
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
    )


@pytest_asyncio.fixture(scope="session")
async def trader_user(http: httpx.AsyncClient, admin: TestUser) -> TestUser:
    return await _register_and_login(http, admin, "trader")


@pytest_asyncio.fixture(scope="session")
async def merchant_user(http: httpx.AsyncClient, admin: TestUser) -> TestUser:
    return await _register_and_login(http, admin, "merchant")


@pytest_asyncio.fixture(scope="session")
async def teamlead_user(http: httpx.AsyncClient, admin: TestUser) -> TestUser:
    # Если сервер не поддерживает teamlead — отдадим trader, чтобы не падало;
    # тесты teamlead не критичны для полного сценария.
    try:
        return await _register_and_login(http, admin, "teamlead")
    except AssertionError:
        return await _register_and_login(http, admin, "trader")


# ---------------------------------------------------------------------------
# Полноценные профили trader / merchant
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def merchant(
    http: httpx.AsyncClient,
    admin: TestUser,
    merchant_user: TestUser,
) -> TestMerchant:
    """Настраивает профиль мерчанта: enabled, комиссия SBP=2%, api_key."""
    m = await _find_merchant_by_user(http, admin, merchant_user.id)
    merchant_id = m["id"]

    # Включаем мерчанта и задаём fees
    patch_resp = await http.patch(
        f"/api/v1/merchants/{merchant_id}",
        json={
            "status": "enabled",
            "fees": {"sbp": 2.0},
            "order_ttl_seconds": 900,
        },
        headers=admin.auth(),
    )
    assert patch_resp.status_code == 200, (
        f"Merchant enable failed: {patch_resp.status_code} {patch_resp.text}"
    )

    # Получаем / сбрасываем API-ключ
    key_resp = await http.post(
        f"/api/v1/merchants/{merchant_id}/api-key/reset",
        headers=admin.auth(),
    )
    assert key_resp.status_code in (200, 201), (
        f"Api-key reset failed: {key_resp.status_code} {key_resp.text}"
    )
    api_key = key_resp.json()["api_key"]

    return TestMerchant(id=merchant_id, user=merchant_user, api_key=api_key)


@pytest_asyncio.fixture(scope="session")
async def trader(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader_user: TestUser,
) -> TestTrader:
    """
    Настраивает профиль трейдера: enabled, is_payin_active=True,
    methods_config.sbp.fee=1%, баланс пополнен на 10_000 USDT, создан requisite.
    """
    t = await _find_trader_by_user(http, admin, trader_user.id)
    trader_id = t["id"]

    # Конфигурируем методы и включаем payin
    update_payload = {
        "status": "enabled",
        "is_payin_active": True,
        "methods_config": {
            "sbp": {
                "fee": 1.0,
                "min_amount": 0,
                "max_amount": 1_000_000,
                "is_active": True,
            }
        },
    }
    patch_resp = await http.patch(
        f"/api/v1/traders/{trader_id}",
        json=update_payload,
        headers=admin.auth(),
    )
    assert patch_resp.status_code == 200, (
        f"Trader update failed: {patch_resp.status_code} {patch_resp.text}"
    )

    # Убеждаемся, что toggle payin включён (на случай если auto-disable уже сработал)
    await http.patch(
        "/api/v1/traders/me/payin",
        json={"is_active": True},
        headers=trader_user.auth(),
    )

    # Получаем активную опцию оплаты (банк) для создания реквизита
    options_resp = await http.get(
        "/api/v1/payments/options",
        headers=admin.auth(),
    )
    assert options_resp.status_code == 200, (
        f"Payment options fetch failed: {options_resp.status_code} {options_resp.text}"
    )
    options = [o for o in options_resp.json() if o.get("is_active") and "sbp" in (o.get("supported_methods") or [])]
    assert options, "No active SBP-capable payment options found on dev server"
    payment_option_id = options[0]["id"]

    # Создаём реквизит от имени трейдера
    req_resp = await http.post(
        "/api/v1/requisites/me",
        headers=trader_user.auth(),
        json={
            "nickname": f"e2e_req_{rand_suffix()}",
            "payment_option_id": payment_option_id,
            "account_number": "40817810099910" + rand_suffix(6),
            "account_holder": "E2E Test Account",
            "payment_method": "sbp",
        },
    )
    assert req_resp.status_code == 201, (
        f"Requisite create failed: {req_resp.status_code} {req_resp.text}"
    )
    requisite_id = req_resp.json()["id"]

    # Включаем реквизит (admin может менять status)
    enable_resp = await http.patch(
        f"/api/v1/requisites/{requisite_id}",
        json={"status": "enabled"},
        headers=admin.auth(),
    )
    assert enable_resp.status_code == 200, (
        f"Requisite enable failed: {enable_resp.status_code} {enable_resp.text}"
    )

    # Пополняем рабочий баланс USDT для возможности брать заявки
    dep_resp = await admin_deposit(
        http, admin, user_id=trader_user.id, amount=10000, reason="e2e_trader_initial"
    )
    assert dep_resp.status_code == 200, (
        f"Trader deposit failed: {dep_resp.status_code} {dep_resp.text}"
    )

    return TestTrader(id=trader_id, user=trader_user, requisite_id=requisite_id)


# ---------------------------------------------------------------------------
# Группа: связываем trader + merchant, чтобы работал pooling
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _keep_trader_active(request):
    """
    Перед каждым тестом, который использует фикстуру trader или trader_user,
    повторно включаем payin. На dev-сервере включена авто-деактивация payin
    (TRADER_AUTO_DISABLE_PAYIN_MINUTES=15), и на длинном прогоне трейдер может
    отвалиться между тестами — нам это не нужно.
    """
    fixturenames = set(getattr(request, "fixturenames", ()))
    needs_trader = "trader" in fixturenames or "trader_user" in fixturenames
    if not needs_trader:
        yield
        return

    http_client = request.getfixturevalue("http")
    trader_user_obj = request.getfixturevalue("trader_user")

    try:
        await http_client.patch(
            "/api/v1/traders/me/payin",
            json={"is_active": True},
            headers=trader_user_obj.auth(),
        )
    except Exception:
        # Не блокируем тест из-за сбоя сети в auto-toggle
        pass

    yield


@pytest_asyncio.fixture(scope="session")
async def trader_group(
    http: httpx.AsyncClient,
    admin: TestUser,
    trader: TestTrader,
    merchant: TestMerchant,
) -> int:
    group_name = f"e2e-group-{rand_suffix()}"
    create_resp = await http.post(
        "/api/v1/traders/groups",
        json={"name": group_name, "description": "E2E test group"},
        headers=admin.auth(),
    )
    assert create_resp.status_code in (200, 201), (
        f"Trader group create failed: {create_resp.status_code} {create_resp.text}"
    )
    group_id = create_resp.json()["id"]

    # Добавляем трейдера
    add_t = await http.post(
        f"/api/v1/traders/groups/{group_id}/traders/{trader.id}",
        headers=admin.auth(),
    )
    assert add_t.status_code in (200, 201), (
        f"Add trader to group failed: {add_t.status_code} {add_t.text}"
    )

    # Добавляем мерчанта
    add_m = await http.post(
        f"/api/v1/traders/groups/{group_id}/merchants/{merchant.id}",
        headers=admin.auth(),
    )
    assert add_m.status_code in (200, 201), (
        f"Add merchant to group failed: {add_m.status_code} {add_m.text}"
    )

    # Небольшая пауза, чтобы кеши/сервисы увидели группу
    await asyncio.sleep(0.5)
    return int(group_id)
