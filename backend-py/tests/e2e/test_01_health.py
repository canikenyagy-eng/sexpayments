"""
E2E-тесты: доступность сервиса и базовая проверка API.
"""

import httpx
import pytest
import pytest_asyncio

from tests.e2e.conftest import BASE_URL, TIMEOUT


pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def test_health_endpoint(http: httpx.AsyncClient) -> None:
    """GET /health должен вернуть {"status": "ok"}."""
    resp = await http.get("/health")
    assert resp.status_code == 200, f"Health check failed: {resp.text}"
    data = resp.json()
    assert data.get("status") == "ok", f"Unexpected health response: {data}"


# ---------------------------------------------------------------------------
# Swagger / OpenAPI
# ---------------------------------------------------------------------------

async def test_admin_openapi_accessible(http: httpx.AsyncClient) -> None:
    """Документация admin API должна быть доступна."""
    resp = await http.get("/docs")
    assert resp.status_code in (200, 301, 302), f"Admin docs not accessible: {resp.status_code}"


async def test_merchant_openapi_accessible(http: httpx.AsyncClient) -> None:
    """Документация Merchant API должна быть доступна."""
    resp = await http.get("/api/merchant/openapi.json")
    assert resp.status_code == 200, f"Merchant OpenAPI not accessible: {resp.text}"
    schema = resp.json()
    assert "paths" in schema
    assert "openapi" in schema


async def test_merchant_docs_accessible(http: httpx.AsyncClient) -> None:
    """Swagger UI для Merchant API должен отдавать HTML."""
    resp = await http.get("/api/merchant/docs")
    assert resp.status_code in (200, 301, 302)


# ---------------------------------------------------------------------------
# Защита эндпоинтов — неавторизованный доступ
# ---------------------------------------------------------------------------

async def test_admin_api_requires_auth(http: httpx.AsyncClient) -> None:
    """GET /api/v1/users/ без токена → 401 или 403."""
    resp = await http.get("/api/v1/users/")
    assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


async def test_merchant_api_requires_key(http: httpx.AsyncClient) -> None:
    """GET /api/merchant/v1/profile/me без ключа → 401 или 403."""
    resp = await http.get("/api/merchant/v1/profile/me")
    assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


async def test_merchant_api_invalid_key(http: httpx.AsyncClient) -> None:
    """X-Api-Key с невалидным значением → 401."""
    resp = await http.get(
        "/api/merchant/v1/profile/me",
        headers={"X-Api-Key": "totally_invalid_key_000"},
    )
    assert resp.status_code in (401, 403)
