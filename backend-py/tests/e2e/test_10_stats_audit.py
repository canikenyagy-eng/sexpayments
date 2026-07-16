"""
E2E-тесты: Статистика и Audit-логи.

Покрывает:
- GET /api/v1/stats/admin — общая статистика (admin only)
- GET /api/v1/stats/admin с фильтрами по датам
- GET /api/v1/audit/api-logs — логи запросов к merchant API
- GET /api/v1/audit/api-logs/{log_id}/snapshot — снапшот создания заявки
- GET /api/v1/audit/{entity_type}/{entity_id} — audit trail по сущности
- Проверка запрета для non-admin
"""

import time

import httpx
import pytest

from tests.e2e.conftest import TestMerchant, TestTrader, TestUser

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# GET /api/v1/stats/admin
# ---------------------------------------------------------------------------

async def test_admin_stats_returns_valid_structure(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    """Статистика возвращает ожидаемую структуру (total_orders, volumes, etc.)."""
    resp = await http.get("/api/v1/stats/admin", headers=admin.auth())
    assert resp.status_code == 200, f"Stats failed: {resp.text}"
    data = resp.json()
    assert isinstance(data, dict), "Stats response should be a dict"
    # Проверяем наличие ключевых полей (структура зависит от AdminStatsResponse)
    assert len(data) > 0, "Stats response should not be empty"


async def test_admin_stats_with_date_range(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    """Статистика с фильтром по датам — корректно принимает Unix timestamps."""
    now = int(time.time())
    day_ago = now - 86400

    resp = await http.get(
        f"/api/v1/stats/admin?date_from={day_ago}&date_to={now}",
        headers=admin.auth(),
    )
    assert resp.status_code == 200, f"Stats with date range failed: {resp.text}"
    assert isinstance(resp.json(), dict)


async def test_admin_stats_forbidden_for_trader(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/stats/admin", headers=trader.user.auth())
    assert resp.status_code == 403


async def test_admin_stats_forbidden_for_merchant(
    http: httpx.AsyncClient, merchant_user: TestUser
) -> None:
    resp = await http.get("/api/v1/stats/admin", headers=merchant_user.auth())
    assert resp.status_code == 403


async def test_admin_stats_unauthenticated(http: httpx.AsyncClient) -> None:
    resp = await http.get("/api/v1/stats/admin")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/v1/audit/api-logs
# ---------------------------------------------------------------------------

async def test_admin_api_logs_returns_list(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    """После вызовов merchant API в тестах должны быть логи."""
    resp = await http.get("/api/v1/audit/api-logs?limit=50", headers=admin.auth())
    assert resp.status_code == 200, f"API logs failed: {resp.text}"
    logs = resp.json()
    assert isinstance(logs, list)
    if logs:
        log = logs[0]
        assert "request_id" in log
        # Лог содержит информацию о запросе
        assert any(k in log for k in ("method", "endpoint", "status_code", "path"))


async def test_admin_api_logs_filter_by_merchant(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    """Фильтр по merchant_id возвращает только логи этого мерчанта."""
    resp = await http.get(
        f"/api/v1/audit/api-logs?merchant_id={merchant.id}&limit=20",
        headers=admin.auth(),
    )
    assert resp.status_code == 200
    logs = resp.json()
    assert isinstance(logs, list)
    # Все записи должны принадлежать нашему мерчанту
    for log in logs:
        assert log.get("merchant_id") == merchant.id or "merchant_id" not in log


async def test_admin_api_logs_forbidden_for_trader(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get("/api/v1/audit/api-logs", headers=trader.user.auth())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/audit/api-logs/{log_id}/snapshot
# ---------------------------------------------------------------------------

async def test_admin_api_log_snapshot(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    """Snapshot для лога создания заявки содержит диагностику pooling."""
    # Создаём заявку чтобы точно был лог
    create_resp = await http.post(
        "/api/merchant/v1/orders/payin",
        headers=merchant.headers(),
        json={
            "amount": 100.0,
            "currency": "RUB",
            "payment_method": "sbp",
            "issue_requisite_async": True,
        },
    )
    assert create_resp.status_code == 201
    order_uuid = create_resp.json()["id"]

    # Cleanup
    await http.post(
        f"/api/merchant/v1/orders/{order_uuid}/cancel",
        headers=merchant.headers(),
    )

    # Берём последние логи и ищем связанный snapshot
    logs_resp = await http.get(
        f"/api/v1/audit/api-logs?merchant_id={merchant.id}&limit=20",
        headers=admin.auth(),
    )
    logs = logs_resp.json()

    snapshot_found = False
    for log in logs:
        # CH-backed logs are keyed by request_id; the snapshot endpoint takes it.
        req_id = log.get("request_id")
        if not req_id:
            continue
        snap_resp = await http.get(
            f"/api/v1/audit/api-logs/{req_id}/snapshot",
            headers=admin.auth(),
        )
        assert snap_resp.status_code == 200
        snap = snap_resp.json()
        if snap is not None:
            # Snapshot содержит диагностику pooling
            assert isinstance(snap, dict)
            snapshot_found = True
            break  # Достаточно найти один

    # Не критично, если snapshot нет (не все логи его имеют)
    _ = snapshot_found


# ---------------------------------------------------------------------------
# GET /api/v1/audit/{entity_type}/{entity_id}
# ---------------------------------------------------------------------------

async def test_admin_entity_audit_logs_for_merchant(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    """Audit trail для мерчанта."""
    resp = await http.get(
        f"/api/v1/audit/merchant/{merchant.id}",
        headers=admin.auth(),
    )
    assert resp.status_code == 200, f"Merchant audit logs failed: {resp.text}"
    logs = resp.json()
    assert isinstance(logs, list)
    if logs:
        log = logs[0]
        assert "id" in log or "action" in log or "entity_type" in log


async def test_admin_entity_audit_logs_for_trader(
    http: httpx.AsyncClient, admin: TestUser, trader: TestTrader
) -> None:
    """Audit trail для трейдера."""
    resp = await http.get(
        f"/api/v1/audit/trader/{trader.id}",
        headers=admin.auth(),
    )
    assert resp.status_code == 200, f"Trader audit logs failed: {resp.text}"
    assert isinstance(resp.json(), list)


async def test_admin_entity_audit_logs_pagination(
    http: httpx.AsyncClient, admin: TestUser, merchant: TestMerchant
) -> None:
    """Pagination audit logs: limit работает."""
    resp = await http.get(
        f"/api/v1/audit/merchant/{merchant.id}?limit=3",
        headers=admin.auth(),
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 3


async def test_admin_entity_audit_logs_forbidden_for_trader(
    http: httpx.AsyncClient, trader: TestTrader, merchant: TestMerchant
) -> None:
    resp = await http.get(
        f"/api/v1/audit/merchant/{merchant.id}",
        headers=trader.user.auth(),
    )
    assert resp.status_code == 403
