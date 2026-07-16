"""Unit tests for the adapter catalog and rate-source semantics.

Covers:
  * AdapterFieldSpec.to_dict drops empty options
  * ProviderAdapter.describe() returns expected shape
  * Registry knows about mock + legacy_crypto
  * supports_provider_rate is correctly declared
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests-must-be-long!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")

import pytest

from app.modules.cascading.integrations import registry
from app.modules.cascading.integrations.base import (
    AdapterFieldOption,
    AdapterFieldSpec,
    ProviderAdapter,
)
from app.modules.cascading.integrations.legacy_crypto import LegacyCryptoAdapter
from app.modules.cascading.integrations.mock import MockProviderAdapter


def test_field_spec_dict_drops_empty_options():
    spec = AdapterFieldSpec(key="x", label="X", type="string")
    out = spec.to_dict()
    assert out["key"] == "x"
    assert "options" not in out


def test_field_spec_keeps_options_when_present():
    spec = AdapterFieldSpec(
        key="m",
        label="Method",
        type="select",
        options=[AdapterFieldOption("sbp", "SBP"), AdapterFieldOption("card", "Card")],
    )
    out = spec.to_dict()
    assert len(out["options"]) == 2
    assert out["options"][0]["value"] == "sbp"


def test_mock_adapter_describe():
    info = MockProviderAdapter.describe()
    assert info.code == "mock"
    assert info.display_name
    assert info.supports_provider_rate is False  # mock can't quote rates
    assert any(f["key"] == "supported_methods" for f in info.settings_schema)
    assert "sbp" in info.supported_methods
    assert "card" in info.supported_methods


def test_legacy_crypto_adapter_describe():
    info = LegacyCryptoAdapter.describe()
    assert info.code == "legacy_crypto"
    assert info.display_name == "LegacyCrypto"
    assert info.supports_provider_rate is True
    field_keys = {f["key"] for f in info.settings_schema}
    assert "bank_code_map" in field_keys
    assert "preferred_method_map" in field_keys
    # SIM is not in LegacyCrypto's supported set.
    assert "sbp" in info.supported_methods
    assert "card" in info.supported_methods
    assert "sim" not in info.supported_methods


def test_registry_contains_both_adapters():
    codes = registry.list_codes()
    assert "mock" in codes
    assert "legacy_crypto" in codes


def test_registry_get_returns_instance():
    adapter = registry.get("legacy_crypto")
    assert isinstance(adapter, ProviderAdapter)
    assert adapter.code == "legacy_crypto"


def test_kv_map_field_uses_value_type():
    spec = AdapterFieldSpec(
        key="bank_code_map",
        label="Banks",
        type="kv_map",
        value_type="select",
        options=[AdapterFieldOption("SBER", "Сбер")],
    )
    out = spec.to_dict()
    assert out["value_type"] == "select"
    assert len(out["options"]) == 1
