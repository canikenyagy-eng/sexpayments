import os

# Seed env vars before importing app.* (pydantic-settings reads them eagerly).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "integration-test-secret-key-for-tests-must-be-long!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

# Patch SQLite type compiler for PostgreSQL-specific types (JSONB -> JSON).
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler  # noqa: E402


def _visit_jsonb(self, type_, **kwargs):  # type: ignore[override]
    return "JSON"


SQLiteTypeCompiler.visit_JSONB = _visit_jsonb  # type: ignore[attr-defined]

# Importing app.main registers all ORM models against Base.metadata.
from app.main import app  # noqa: F401,E402
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
