from __future__ import annotations

import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://browser_reporter:browser_reporter@localhost/browser_reporter",
)

engine = create_async_engine(DATABASE_URL, echo=False, future=True)

# expire_on_commit=False will prevent attributes from being expired
# after commit.
AsyncSessionLocal = sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False, autocommit=False
)

Base = declarative_base()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session 