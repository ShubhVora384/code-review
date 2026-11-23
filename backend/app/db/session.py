from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base

log = get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    kwargs: dict = {"echo": False, "future": True}
    if settings.is_postgres:
        kwargs |= {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
            "pool_recycle": 1800,
        }
    else:
        # SQLite: allow cross-task use from the worker + API in one process.
        kwargs |= {"connect_args": {"check_same_thread": False}}
    return create_async_engine(settings.database_url, **kwargs)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope for background work (worker, agents)."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with get_sessionmaker()() as session:
        yield session


async def init_db() -> None:
    """Create tables. Alembic owns the schema in production; this keeps the
    SQLite fallback usable with zero migration steps."""
    engine = get_engine()
    async with engine.begin() as conn:
        if not settings.is_postgres:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(Base.metadata.create_all)
    log.info("database.ready", backend="postgres" if settings.is_postgres else "sqlite")


async def dispose_db() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
