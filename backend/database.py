from __future__ import annotations

import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import AsyncAdaptedQueuePool

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://browser_reporter:browser_reporter@localhost/browser_reporter",
)

# Optimized engine configuration for high-volume workloads (800+ users)
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    # Connection pooling settings
    poolclass=AsyncAdaptedQueuePool,
    pool_size=20,              # Number of permanent connections
    max_overflow=40,           # Additional connections during peak load
    pool_pre_ping=True,        # Verify connection health before use
    pool_recycle=3600,         # Recycle connections every hour (prevents stale connections)
    # Query optimization
    connect_args={
        "statement_cache_size": 0,  # Disable prepared statement cache (better for varied queries)
        "server_settings": {
            "application_name": "browser_reporter_backend",
            "jit": "off",  # Disable JIT for simpler queries (reduces overhead)
        },
    },
)

# expire_on_commit=False will prevent attributes from being expired
# after commit.
AsyncSessionLocal = sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False, autocommit=False
)

Base = declarative_base()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session 