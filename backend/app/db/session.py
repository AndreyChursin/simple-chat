from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


def _create_engine() -> AsyncEngine:
    settings = get_settings()
    if settings.db_disable_pool:
        return create_async_engine(settings.database_url, poolclass=NullPool)
    # A fixed pool: overflow connections are closed when returned, and opening one costs a SCRAM
    # handshake (thousands of HMAC rounds in Python, on the event loop). Under a burst — 300 sockets
    # connecting at once — that churn stalled message delivery for seconds; waiting for a pooled
    # connection is far cheaper.
    return create_async_engine(
        settings.database_url,
        pool_size=30,
        max_overflow=0,
        pool_timeout=30,
        pool_pre_ping=True,
    )


engine: AsyncEngine = _create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
