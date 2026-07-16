from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()


_ASYNCPG_SHARED_ARGS = {
    "statement_cache_size": 2048,
    "max_cached_statement_lifetime": 0,
}


engine = create_async_engine(
    str(settings.DATABASE_URL),
    pool_size=15,
    max_overflow=30,
    pool_recycle=3600,
    pool_use_lifo=True,
    echo=settings.DEBUG,
    connect_args={
        **_ASYNCPG_SHARED_ARGS,
        "server_settings": {"application_name": "backend"},
    },
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


worker_engine = create_async_engine(
    str(settings.DATABASE_URL),
    poolclass=NullPool,
    echo=settings.DEBUG,
    connect_args={
        **_ASYNCPG_SHARED_ARGS,
        "server_settings": {"application_name": "worker"},
    },
)

WorkerSessionLocal = async_sessionmaker(
    bind=worker_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
