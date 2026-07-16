"""Shared fixtures for both unit and integration tests.

The ``session`` fixture below (in-memory SQLite mounted via aiosqlite) lives
here rather than in ``tests/integration/conftest.py`` so that any "unit" test
that asserts on real ORM behaviour can ``def test(session): ...`` without
being moved to a different directory. Pytest discovers conftests up the
directory tree, so both ``tests/unit/`` and ``tests/integration/`` inherit
these.
"""
from __future__ import annotations

import os

# Seed env vars BEFORE importing app.* (pydantic-settings reads them eagerly
# at Settings() instantiation, which happens transitively from app.main).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "shared-test-secret-key-for-tests-must-be-long!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("PROJECT_BASE_URL", "https://api.test")

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def _stub_celery_send_task(monkeypatch):
    """Stub ``celery_app.send_task`` on the shared object so services that
    fire-and-forget background work (e.g. the долив receipt → trader-bot push)
    never reach a broker. Patches the METHOD on the object, so it covers every
    module that did ``from app.workers.celery_app import celery_app``."""
    try:
        from app.workers import celery_app as _celery_module
    except Exception:
        yield
        return
    monkeypatch.setattr(_celery_module.celery_app, "send_task", MagicMock())
    yield


def _visit_jsonb(self, type_, **kwargs):  # type: ignore[override]
    """Teach the SQLite dialect to accept PostgreSQL JSONB columns."""
    return "JSON"


SQLiteTypeCompiler.visit_JSONB = _visit_jsonb  # type: ignore[attr-defined]

# Importing app.main registers all ORM models against Base.metadata, which the
# in-memory SQLite engine then materialises in ``test_engine``.
from app.main import app  # noqa: E402,F401
from app.infrastructure.db.base import Base  # noqa: E402


@pytest_asyncio.fixture
async def test_engine():
    """Isolated in-memory SQLite DB with shared connection (StaticPool)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(test_engine) -> AsyncSession:
    factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as s:
        yield s
