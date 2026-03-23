"""Shared migration utilities used by both the startup auto-runner and the CLI tool."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import asyncpg

logger = logging.getLogger("browser_reporter")


def get_db_config() -> dict:
    """Return asyncpg connection kwargs from environment variables."""
    return {
        "host": os.getenv("DB_HOST", "db"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "user": os.getenv("DB_USER", "browser_reporter"),
        "password": os.getenv("DB_PASSWORD", "browser_reporter"),
        "database": os.getenv("DB_NAME", "browser_reporter"),
    }


def split_sql_statements(sql_content: str) -> list[str]:
    """Split a SQL file into individual statements, respecting DO $$ blocks.

    This is necessary because CREATE INDEX CONCURRENTLY cannot run inside a
    transaction and asyncpg wraps multi-statement strings in an implicit
    transaction.
    """
    statements: list[str] = []
    current: list[str] = []
    in_dollar_block = False

    for line in sql_content.splitlines():
        stripped = line.strip()

        # Skip blank lines and standalone comments before any statement starts
        if not stripped or (stripped.startswith("--") and not current):
            continue

        current.append(line)

        # Detect start of a DO $$ ... END $$; block
        upper = stripped.upper()
        if upper.startswith("DO $$") or upper.startswith("DO $"):
            in_dollar_block = True

        if in_dollar_block and stripped.endswith("$$;"):
            in_dollar_block = False
            statements.append("\n".join(current))
            current = []
            continue

        if not in_dollar_block and stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []

    # Trailing statement without semicolon
    if current:
        stmt = "\n".join(current).strip()
        if stmt:
            statements.append(stmt)

    # Filter out comment-only fragments
    return [
        s
        for s in statements
        if s.strip()
        and not all(ln.strip().startswith("--") or not ln.strip() for ln in s.splitlines())
    ]


async def ensure_schema_columns(conn: asyncpg.Connection) -> None:
    """Add any columns that the ORM expects but that don't exist yet.

    SQLAlchemy's create_all only creates *new tables*; it won't ALTER
    existing ones.  This fills the gap for incremental schema changes.
    """
    required_columns = [
        ("visits", "search_vector", "TSVECTOR"),
        ("dashboard_users", "auth_source", "VARCHAR DEFAULT 'local' NOT NULL"),
    ]

    for table, column, col_type in required_columns:
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = $1 AND column_name = $2",
            table,
            column,
        )
        if not exists:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.info("Added missing column %s.%s", table, column)


async def apply_migrations(conn: asyncpg.Connection, migrations_dir: Path) -> tuple[int, int]:
    """Apply pending SQL migrations. Returns (applied, skipped) counts."""

    # Ensure tracking table exists
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id SERIAL PRIMARY KEY,
            migration_name VARCHAR(255) UNIQUE NOT NULL,
            applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            execution_time_seconds DECIMAL(10, 3)
        )
    """)

    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        return 0, 0

    applied = 0
    skipped = 0

    for migration_file in migration_files:
        name = migration_file.name

        already_done = await conn.fetchval(
            "SELECT COUNT(*) FROM schema_migrations WHERE migration_name = $1",
            name,
        )
        if already_done:
            skipped += 1
            continue

        logger.info("Applying migration: %s", name)
        sql_content = migration_file.read_text()
        start = datetime.now()

        try:
            for stmt in split_sql_statements(sql_content):
                try:
                    await conn.execute(stmt)
                except Exception as stmt_err:
                    msg = str(stmt_err).lower()
                    if "already exists" in msg or "duplicate" in msg:
                        continue
                    raise

            elapsed = (datetime.now() - start).total_seconds()
            await conn.execute(
                "INSERT INTO schema_migrations (migration_name, execution_time_seconds) VALUES ($1, $2)",
                name,
                elapsed,
            )
            logger.info("  Applied %s in %.2fs", name, elapsed)
            applied += 1

        except Exception:
            logger.exception("  Migration failed: %s", name)

    return applied, skipped
