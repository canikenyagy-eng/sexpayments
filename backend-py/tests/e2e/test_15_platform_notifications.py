"""
E2E-тесты: платформенные уведомления (таб «Уведомления» настроек площадки).

Покрывают admin-only эндпоинты:
  GET   /api/v1/platform-settings/notifications
  PATCH /api/v1/platform-settings/notifications
— чтение, частичное обновление, персистентность и запрет для не-админа.

Тест восстанавливает безопасные дефолты в конце, чтобы не оставить
живую площадку с включёнными уведомлениями.
"""
import httpx
import pytest

from tests.e2e.conftest import TestTrader, TestUser

pytestmark = pytest.mark.anyio

NOTIF = "/api/v1/platform-settings/notifications"


async def test_get_notification_settings_as_admin(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    resp = await http.get(NOTIF, headers=admin.auth())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "notifications_chat_id" in body
    assert "notify_withdrawal_requests" in body
    assert isinstance(body["notifications_chat_id"], str)
    assert isinstance(body["notify_withdrawal_requests"], bool)


async def test_update_notification_settings_roundtrip(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    chat_id = "-100999000111"
    patch_resp = await http.patch(
        NOTIF,
        headers=admin.auth(),
        json={"notifications_chat_id": chat_id, "notify_withdrawal_requests": True},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    updated = patch_resp.json()
    assert updated["notifications_chat_id"] == chat_id
    assert updated["notify_withdrawal_requests"] is True

    # Persisted — read back.
    get_resp = await http.get(NOTIF, headers=admin.auth())
    assert get_resp.status_code == 200
    assert get_resp.json()["notify_withdrawal_requests"] is True

    # Cleanup: reset to safe defaults so the live platform isn't left notifying.
    reset = await http.patch(
        NOTIF,
        headers=admin.auth(),
        json={"notify_withdrawal_requests": False, "notifications_chat_id": ""},
    )
    assert reset.status_code == 200
    assert reset.json()["notify_withdrawal_requests"] is False


async def test_partial_update_only_toggle(
    http: httpx.AsyncClient, admin: TestUser
) -> None:
    # Omitting notifications_chat_id must be allowed (partial update).
    resp = await http.patch(
        NOTIF, headers=admin.auth(), json={"notify_withdrawal_requests": False}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["notify_withdrawal_requests"] is False


async def test_notification_settings_forbidden_for_trader(
    http: httpx.AsyncClient, trader: TestTrader
) -> None:
    resp = await http.get(NOTIF, headers=trader.user.auth())
    assert resp.status_code in (401, 403)

    resp2 = await http.patch(
        NOTIF, headers=trader.user.auth(), json={"notify_withdrawal_requests": True}
    )
    assert resp2.status_code in (401, 403)
