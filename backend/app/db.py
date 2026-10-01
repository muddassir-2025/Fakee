"""SQLAlchemy async engine and session management.

The same models run on Neon PostgreSQL (production) and SQLite (local dev).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

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

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
