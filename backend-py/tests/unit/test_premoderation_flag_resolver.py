"""Unit tests for ReceiptModerationPolicy.is_enabled_for — the single source of
truth for *whether* an uploaded receipt must be premoderated.

Resolution rules:
  * merchant.receipt_premoderation_enabled is True  → ON
  * merchant.receipt_premoderation_enabled is False → OFF
  * NULL → fall back to the global PlatformSetting flag
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.merchants.models import Merchant
from app.modules.receipts.moderation import ReceiptModerationPolicy


@pytest.fixture
def policy():
    return ReceiptModerationPolicy(session=MagicMock())


def _make_merchant(override):
    m = MagicMock(spec=Merchant)
    m.receipt_premoderation_enabled = override
    return m


@pytest.mark.asyncio
async def test_per_merchant_true_wins_over_global_false(policy, monkeypatch):
    """Per-merchant override = True should win even when global is False."""
    fake_settings = MagicMock()
    fake_settings.get_bool = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "app.modules.settings.service.SettingsService",
        lambda _session: fake_settings,
    )

    merchant = _make_merchant(True)
    assert await policy.is_enabled_for(merchant) is True
    # SettingsService.get_bool must NOT have been called when override is set.
    fake_settings.get_bool.assert_not_called()


@pytest.mark.asyncio
async def test_per_merchant_false_wins_over_global_true(policy, monkeypatch):
    fake_settings = MagicMock()
    fake_settings.get_bool = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.modules.settings.service.SettingsService",
        lambda _session: fake_settings,
    )

    merchant = _make_merchant(False)
    assert await policy.is_enabled_for(merchant) is False
    fake_settings.get_bool.assert_not_called()


@pytest.mark.asyncio
async def test_null_override_falls_back_to_global_true(policy, monkeypatch):
    fake_settings = MagicMock()
    fake_settings.get_bool = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.modules.settings.service.SettingsService",
        lambda _session: fake_settings,
    )

    merchant = _make_merchant(None)
    assert await policy.is_enabled_for(merchant) is True
    fake_settings.get_bool.assert_awaited_once_with("receipt_premoderation_enabled")


@pytest.mark.asyncio
async def test_null_override_falls_back_to_global_false(policy, monkeypatch):
    fake_settings = MagicMock()
    fake_settings.get_bool = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "app.modules.settings.service.SettingsService",
        lambda _session: fake_settings,
    )

    merchant = _make_merchant(None)
    assert await policy.is_enabled_for(merchant) is False
    fake_settings.get_bool.assert_awaited_once_with("receipt_premoderation_enabled")
