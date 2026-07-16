"""Shared fixtures for backend unit tests.

Existing unit tests build merchant objects via MagicMock and don't set every
attribute. After adding MerchantAuthCache.invalidate calls inside
MerchantService, those tests would explode when invalidate tries to hash a
MagicMock api_key or talk to Redis. Stub the singleton's invalidate by
default for unit tests; tests that exercise the cache itself
(test_merchant_auth_cache.py) instantiate their own MerchantAuthCache and
are unaffected.
"""

import os

# Match the env defaults the integration conftest uses so app.* imports cleanly
# from a bare unit-test run (no docker, no real Postgres / Redis).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-for-tests-must-be-long-enough!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("PROJECT_BASE_URL", "https://api.test")

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _stub_merchant_auth_cache_invalidate(monkeypatch):
    """Replace the global cache's invalidate with an AsyncMock for unit tests."""
    try:
        from app.modules.merchants import auth_cache as _auth_cache
    except Exception:
        yield
        return

    monkeypatch.setattr(_auth_cache.merchant_auth_cache, "invalidate", AsyncMock())
    yield


@pytest.fixture(autouse=True)
def _default_upload_dir_to_tmp(monkeypatch):
    """Cascade adapters now refuse to disk-open any evidence path outside
    UPLOAD_DIR (traversal guard). Adapter unit tests stage receipt fixtures via
    ``tempfile`` (the system temp dir), so default UPLOAD_DIR there for unit runs.
    Tests that need a specific dir override this with their own monkeypatch."""
    import tempfile

    try:
        from app.modules.receipts import storage as _storage
    except Exception:
        yield
        return

    monkeypatch.setattr(_storage.settings, "UPLOAD_DIR", tempfile.gettempdir(), raising=False)
    yield
