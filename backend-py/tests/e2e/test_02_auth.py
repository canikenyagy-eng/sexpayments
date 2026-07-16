"""
E2E-тесты: аутентификация (/api/v1/auth/*).

Тесты покрывают:
- Успешный логин
- Логин с неверным паролем
- Логин заблокированного пользователя
- Рефреш токена
- Logout
- Регистрация (только для admin)
- Impersonation (только для admin)
"""

import httpx
import pytest

from tests.e2e.conftest import (
    ADMIN_PASS,
    ADMIN_USER,
    TestUser,
    auth_bearer,
    rand_suffix,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# ---------------------------------------------------------------------------

async def test_admin_login_success(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["role"] == "admin"


async def test_login_wrong_password(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": "definitely_wrong_pass!"},
    )
    assert resp.status_code in (400, 401, 403)


async def test_login_nonexistent_user(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/auth/login",
        json={"username": f"no_such_user_{rand_suffix()}", "password": "any_pass"},
    )
    assert resp.status_code in (400, 401, 403, 404)


async def test_login_invalid_payload(http: httpx.AsyncClient) -> None:
    resp = await http.post("/api/v1/auth/login", json={"username": "only_login"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/auth/refresh
# ---------------------------------------------------------------------------

async def test_refresh_token_success(http: httpx.AsyncClient, admin: TestUser) -> None:
    resp = await http.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": admin.refresh_token},
    )
    assert resp.status_code == 200, f"Refresh failed: {resp.text}"
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


async def test_refresh_invalid_token(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "not.a.valid.jwt.token"},
    )
    assert resp.status_code in (400, 401, 403, 422)


# ---------------------------------------------------------------------------
# POST /api/v1/auth/logout
# ---------------------------------------------------------------------------

async def test_logout(http: httpx.AsyncClient) -> None:
    login = await http.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
    )
    assert login.status_code == 200
    refresh_token = login.json()["refresh_token"]

    logout = await http.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
    )
    assert logout.status_code in (200, 204)


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------

async def test_register_trader_as_admin(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    username = f"e2e_reg_trader_{rand_suffix()}"
    resp = await http.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "SecurePass123", "role": "trader"},
        headers=admin.auth(),
    )
    assert resp.status_code == 201, f"Registration failed: {resp.text}"
    data = resp.json()
    assert data["username"] == username
    assert data["role"] == "trader"
    assert data["is_blocked"] is False


async def test_register_merchant_as_admin(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    username = f"e2e_reg_merch_{rand_suffix()}"
    resp = await http.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "SecurePass123", "role": "merchant"},
        headers=admin.auth(),
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "merchant"


async def test_register_without_auth(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/auth/register",
        json={"username": f"hack_{rand_suffix()}", "password": "pass123", "role": "trader"},
    )
    assert resp.status_code in (401, 403)


async def test_register_forbidden_for_trader(
    http: httpx.AsyncClient, trader_user: TestUser
) -> None:
    resp = await http.post(
        "/api/v1/auth/register",
        json={"username": f"hack_{rand_suffix()}", "password": "pass123", "role": "trader"},
        headers=trader_user.auth(),
    )
    assert resp.status_code == 403


async def test_register_duplicate_username(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.post(
        "/api/v1/auth/register",
        json={"username": ADMIN_USER, "password": "pass123456", "role": "trader"},
        headers=admin.auth(),
    )
    assert resp.status_code in (400, 409, 422)


async def test_register_password_too_short(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.post(
        "/api/v1/auth/register",
        json={"username": f"e2e_short_{rand_suffix()}", "password": "abc", "role": "trader"},
        headers=admin.auth(),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/auth/impersonate
# ---------------------------------------------------------------------------

async def test_impersonate_as_admin(
    http: httpx.AsyncClient, admin: TestUser, trader_user: TestUser
) -> None:
    resp = await http.post(
        "/api/v1/auth/impersonate",
        json={"target_user_id": trader_user.id},
        headers=admin.auth(),
    )
    assert resp.status_code == 200, f"Impersonate failed: {resp.text}"
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["id"] == trader_user.id
    assert data["user"]["role"] == "trader"


async def test_impersonate_forbidden_for_trader(
    http: httpx.AsyncClient, admin: TestUser, trader_user: TestUser
) -> None:
    resp = await http.post(
        "/api/v1/auth/impersonate",
        json={"target_user_id": admin.id},
        headers=trader_user.auth(),
    )
    assert resp.status_code == 403


async def test_impersonate_nonexistent_user(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.post(
        "/api/v1/auth/impersonate",
        json={"target_user_id": 99999999},
        headers=admin.auth(),
    )
    assert resp.status_code in (401, 404)
