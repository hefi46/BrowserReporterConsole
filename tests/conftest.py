"""Shared fixtures for the BrowserReporterConsole test suite.

Uses an in-process SQLite database (via aiosqlite) so tests can run
without Docker or PostgreSQL.  The app's get_db dependency is overridden
to use the test engine.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import TSVECTOR

# ── Patch environment BEFORE any application imports ─────────────────────
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("SESSION_SECRET", "test-secret-key-not-for-prod")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "TestMasterKey2024!")

# ── Stub out asyncpg so imports don't fail ───────────────────────────────
# asyncpg is a C extension that may not be installable locally on Windows.
# We only need it for PostgreSQL migrations which don't run in tests.
if "asyncpg" not in sys.modules:
    asyncpg_mock = MagicMock()
    asyncpg_mock.exceptions = MagicMock()
    asyncpg_mock.exceptions.InvalidCatalogNameError = type("InvalidCatalogNameError", (Exception,), {})
    asyncpg_mock.exceptions.PostgresConnectionError = type("PostgresConnectionError", (Exception,), {})
    sys.modules["asyncpg"] = asyncpg_mock
    sys.modules["asyncpg.exceptions"] = asyncpg_mock.exceptions

# ── Now safe to import application modules ───────────────────────────────
from backend.database import Base  # noqa: E402
from backend.models import DashboardUser, DashboardRoleEnum  # noqa: E402
from backend.utils import get_password_hash  # noqa: E402

# passlib + bcrypt 5.x on Python 3.13 has a known incompatibility.
# Integration tests that need login will be skipped when hashing is broken.
BCRYPT_WORKS = bool(get_password_hash("probe"))

# ── Test database engine (SQLite, in-memory) ────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)

TestSessionLocal = sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── Register TSVECTOR as TEXT for SQLite ─────────────────────────────────
# PostgreSQL's TSVECTOR type doesn't exist in SQLite.  We register a
# custom type compiler so that create_all() renders it as TEXT instead.
from sqlalchemy.ext.compiler import compiles

@compiles(TSVECTOR, "sqlite")
def _compile_tsvector_sqlite(element, compiler, **kw):
    return "TEXT"


@event.listens_for(test_engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable foreign keys for SQLite."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop for all async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Create all tables once for the entire test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session that rolls back after each test."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture()
async def clean_db(db: AsyncSession) -> AsyncSession:
    """Provide a clean database session — truncates all tables before the test."""
    for table in reversed(Base.metadata.sorted_tables):
        await db.execute(text(f"DELETE FROM {table.name}"))
    await db.commit()
    return db


async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency override for FastAPI's get_db."""
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an httpx AsyncClient wired to the FastAPI app with test DB."""
    from backend.main import app
    from backend.database import get_db

    app.dependency_overrides[get_db] = _override_get_db

    # Clean tables before each client test
    async with TestSessionLocal() as session:
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(text(f"DELETE FROM {table.name}"))
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def admin_user(client: AsyncClient) -> dict:
    """Create an admin user and return credentials dict."""
    async with TestSessionLocal() as session:
        user = DashboardUser(
            username="testadmin",
            password_hash=get_password_hash("adminpass123"),
            role=DashboardRoleEnum.admin,
        )
        session.add(user)
        await session.commit()
    return {"username": "testadmin", "password": "adminpass123"}


@pytest_asyncio.fixture()
async def regular_user(client: AsyncClient) -> dict:
    """Create a regular (non-admin) user and return credentials dict."""
    async with TestSessionLocal() as session:
        user = DashboardUser(
            username="testuser",
            password_hash=get_password_hash("userpass123"),
            role=DashboardRoleEnum.user,
        )
        session.add(user)
        await session.commit()
    return {"username": "testuser", "password": "userpass123"}


async def login_as(client: AsyncClient, username: str, password: str) -> AsyncClient:
    """Log in via the POST /login form endpoint and return the same client (now with session cookie)."""
    resp = await client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302, f"Login failed: {resp.status_code} {resp.text}"
    return client
