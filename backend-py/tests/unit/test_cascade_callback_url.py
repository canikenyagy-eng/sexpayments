"""Cross-cutting tests for cascade callback URL auto-forming.

These cover:
  * ``ProviderAdapter.build_cascade_callback_url`` — the shared helper
    that every adapter falls back to when no per-provider override exists.
  * ``CascadeProviderResponse.callback_url`` — the computed Pydantic field
    that exposes each provider's webhook URL to the admin UI.

We assert that the URL is **per-provider** (derived from ``CascadeProvider.code``,
not from ``adapter_type``) and degrades gracefully to "" when
``PROJECT_BASE_URL`` is unset.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests-must-be-long!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")

from app.common.enums.cascading import CascadeRateSource
from app.core.config import get_settings
from app.modules.cascading.integrations.base import ProviderAdapter
from app.modules.cascading.schemas import CascadeProviderResponse


# ─── build_cascade_callback_url ───────────────────────────────


def test_build_callback_url_uses_provider_code_not_adapter_type():
    # PROJECT_BASE_URL is set in conftest.
    url_main = ProviderAdapter.build_cascade_callback_url("legacy_main")
    url_backup = ProviderAdapter.build_cascade_callback_url("legacy_backup")
    assert url_main == "https://api.test/api/cascade/v1/callbacks/legacy_main"
    assert url_backup == "https://api.test/api/cascade/v1/callbacks/legacy_backup"


def test_build_callback_url_returns_empty_when_base_unconfigured():
    fake_settings = MagicMock(PROJECT_BASE_URL="")
    with patch(
        "app.modules.cascading.integrations.base.get_settings",
        return_value=fake_settings,
        create=True,
    ):
        # The helper imports get_settings lazily by name inside the method,
        # so we patch the import target it uses.
        from app.core import config as _cfg

        with patch.object(_cfg, "get_settings", return_value=fake_settings):
            get_settings.cache_clear()
            assert ProviderAdapter.build_cascade_callback_url("legacy_main") == ""
    get_settings.cache_clear()


def test_build_callback_url_strips_trailing_slash_from_base():
    fake_settings = MagicMock(PROJECT_BASE_URL="https://primepay.com/")
    from app.core import config as _cfg

    with patch.object(_cfg, "get_settings", return_value=fake_settings):
        get_settings.cache_clear()
        url = ProviderAdapter.build_cascade_callback_url("legacy_main")
    get_settings.cache_clear()
    assert url == "https://primepay.com/api/cascade/v1/callbacks/legacy_main"


# ─── CascadeProviderResponse.callback_url ─────────────────────


class _FakeProvider:
    """Stand-in for the CascadeProvider ORM row — Pydantic reads it via
    ``from_attributes=True``. Attributes mirror ``CascadeProvider`` columns
    that ``CascadeProviderResponse`` declares.
    """

    id = 42
    code = "legacy_main"
    name = "Legacy main"
    adapter_type = "legacy_crypto"
    is_active = True
    base_url = "https://payment-legacy.com"
    fees: dict[str, Any] = {}
    rate_source = CascadeRateSource.PROVIDER
    rate_config_id: int | None = None
    min_amount_fiat = None
    max_amount_fiat = None
    cb_window_seconds = 60
    cb_threshold_failures = 5
    cb_threshold_rate = 0.5
    cb_cooldown_seconds = 120
    disabled_until = None
    request_timeout_ms = 5000
    cancel_timeout_ms = 2000
    priority_weight = 100
    settings: dict[str, Any] = {}
    virtual_user_id = 1
    virtual_trader_id = 1
    created_at = datetime(2026, 5, 15, tzinfo=timezone.utc)
    updated_at = datetime(2026, 5, 15, tzinfo=timezone.utc)


def test_response_exposes_per_provider_callback_url():
    p = _FakeProvider()
    p.code = "legacy_main"
    resp = CascadeProviderResponse.model_validate(p, from_attributes=True)
    assert resp.callback_url == "https://api.test/api/cascade/v1/callbacks/legacy_main"


def test_response_callback_url_changes_with_provider_code():
    p1 = _FakeProvider()
    p1.code = "legacy_main"
    p2 = _FakeProvider()
    p2.code = "legacy_backup"
    r1 = CascadeProviderResponse.model_validate(p1, from_attributes=True)
    r2 = CascadeProviderResponse.model_validate(p2, from_attributes=True)
    assert r1.callback_url != r2.callback_url
    assert r1.callback_url.endswith("/legacy_main")
    assert r2.callback_url.endswith("/legacy_backup")


def test_response_callback_url_in_serialized_payload():
    p = _FakeProvider()
    resp = CascadeProviderResponse.model_validate(p, from_attributes=True)
    dumped = resp.model_dump()
    assert dumped["callback_url"] == "https://api.test/api/cascade/v1/callbacks/legacy_main"
