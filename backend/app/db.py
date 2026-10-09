"""SQLAlchemy async engine and session management.

The same models run on Neon PostgreSQL (production) and SQLite (local dev).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings
from .models import Base

# Ensure the local SQLite directory exists before the engine opens it.
if settings.is_sqlite:
    db_path = settings.database_url.split("///", 1)[-1]
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

_engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if not settings.is_sqlite:
    # A finite, recycled pool keeps long-lived Postgres/Neon connections healthy.
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
    )

engine = create_async_engine(settings.sqlalchemy_database_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Prepare the schema.

    In development (and the test suite) tables are created directly from the
    models. In production ``AUTO_CREATE_SCHEMA=false`` and the schema is owned
    by Alembic migrations, which the container entrypoint applies first.
    """
    if not settings.auto_create_schema:
        import logging

        logging.getLogger("app").info("AUTO_CREATE_SCHEMA=false — schema managed by Alembic")
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ping_db() -> bool:
    """Cheap connectivity check for readiness probes."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - readiness must never raise
        return False


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
