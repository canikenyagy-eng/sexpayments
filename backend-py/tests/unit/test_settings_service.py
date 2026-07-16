"""Unit tests for the rebuilt SettingsService.

Covers:
  * defaults applied when row missing / value NULL
  * type coercion for bool / str
  * set() round-trips through coerce → upsert → coerce
  * unknown key rejected on get and set
"""
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationException
from app.modules.settings.service import SETTING_DEFS, SettingsService

# platform_settings.description was VARCHAR(512) before migration 069 widened it to
# Text. A 567-char description (achievement_rules) overflowed it on Postgres — the
# whole settings PATCH 500'd — but SQLite ignores the cap so tests missed it. Keep
# descriptions within 512 so a write never overflows a not-yet-migrated Postgres.
_DESCRIPTION_MAX_LEN = 512


@pytest.mark.asyncio
async def test_get_returns_default_when_row_missing(session):
    service = SettingsService(session)
    assert await service.get_bool("receipt_premoderation_enabled") is False
    assert await service.get_str("support_bot_chat_id") == ""


@pytest.mark.asyncio
async def test_set_and_get_bool_round_trips(session):
    service = SettingsService(session)
    await service.set("receipt_premoderation_enabled", True)
    assert await service.get_bool("receipt_premoderation_enabled") is True

    await service.set("receipt_premoderation_enabled", False)
    assert await service.get_bool("receipt_premoderation_enabled") is False


@pytest.mark.asyncio
async def test_set_and_get_str(session):
    service = SettingsService(session)
    await service.set("support_bot_chat_id", "-100123456789")
    assert await service.get_str("support_bot_chat_id") == "-100123456789"


@pytest.mark.asyncio
async def test_premoderation_reminder_minutes_int_round_trips(session):
    service = SettingsService(session)
    # Default when nothing written yet.
    assert await service.get_int("premoderation_reminder_minutes") == 10
    # Round-trips through coerce → upsert → coerce as an int.
    await service.set("premoderation_reminder_minutes", 25)
    assert await service.get_int("premoderation_reminder_minutes") == 25
    # 0 is a valid value (reminders disabled).
    await service.set("premoderation_reminder_minutes", 0)
    assert await service.get_int("premoderation_reminder_minutes") == 0


def test_premoderation_reminder_setting_registered_with_default():
    from app.modules.settings.service import SETTING_DEFS

    spec = SETTING_DEFS["premoderation_reminder_minutes"]
    assert spec["type"] == "int"
    assert spec["default"] == 10


@pytest.mark.asyncio
async def test_doliv_settings_registered_and_decimal_round_trips(session):
    from app.modules.settings.service import SETTING_DEFS

    for k in (
        "doliv_min_amount", "doliv_max_amount",
        "doliv_price_percent", "doliv_executor_reward_percent",
    ):
        assert SETTING_DEFS[k]["type"] == "decimal"
    assert SETTING_DEFS["doliv_executor_user_ids"]["type"] == "str"

    service = SettingsService(session)
    # Decimal default + round-trip.
    assert await service.get_decimal("doliv_price_percent") == Decimal("0")
    await service.set("doliv_price_percent", "12.5")
    assert await service.get_decimal("doliv_price_percent") == Decimal("12.5")
    # Executor list is a CSV string.
    await service.set("doliv_executor_user_ids", "12,34")
    assert await service.get_str("doliv_executor_user_ids") == "12,34"


@pytest.mark.asyncio
async def test_unknown_key_rejected(session):
    service = SettingsService(session)
    with pytest.raises(ValidationException):
        await service.get("nope_doesnt_exist")
    with pytest.raises(ValidationException):
        await service.set("nope_doesnt_exist", "x")


def test_all_setting_descriptions_fit_column():
    too_long = {
        key: len(spec["description"])
        for key, spec in SETTING_DEFS.items()
        if (spec.get("description") or "") and len(spec["description"]) > _DESCRIPTION_MAX_LEN
    }
    assert not too_long, f"setting descriptions exceed {_DESCRIPTION_MAX_LEN} chars: {too_long}"


@pytest.mark.asyncio
async def test_list_all_typed_includes_defaults_for_missing_rows(session):
    service = SettingsService(session)
    rows = await service.list_all_typed()
    keys = {r["key"] for r in rows}
    assert "receipt_premoderation_enabled" in keys
    assert "support_bot_chat_id" in keys
    # When nothing is written yet, the typed value should equal the default.
    for r in rows:
        if r["key"] == "receipt_premoderation_enabled":
            assert r["value"] is False
        elif r["key"] == "support_bot_chat_id":
            assert r["value"] == ""
