#!/usr/bin/env python3
"""
CLI tool for manually applying database migrations.

Usage (from project root):
    python -m backend.migrations.apply_migration
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

# Ensure project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.migrations.runner import (  # noqa: E402
    get_db_config,
    apply_migrations,
    ensure_schema_columns,
)


async def check_index_status(conn: asyncpg.Connection) -> None:
    """Display custom indexes after migration."""
    indexes = await conn.fetch("""
        SELECT schemaname, tablename, indexname,
               pg_size_pretty(pg_relation_size(indexname::regclass)) as index_size
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename IN ('visits', 'users')
          AND indexname LIKE 'idx_%'
        ORDER BY tablename, indexname;
    """)

    if indexes:
        print(f"\n{'Table':<15} {'Index Name':<35} {'Size':<10}")
        print("-" * 60)
        for idx in indexes:
            print(f"{idx['tablename']:<15} {idx['indexname']:<35} {idx['index_size']:<10}")
    else:
        print("\nNo custom indexes found")


async def check_database_stats(conn: asyncpg.Connection) -> None:
    """Display database table sizes and record counts."""
    tables = await conn.fetch("""
        SELECT tablename,
               pg_size_pretty(pg_total_relation_size(tablename::text)) as total_size,
               pg_size_pretty(pg_relation_size(tablename::text)) as table_size,
               pg_size_pretty(pg_total_relation_size(tablename::text)
                             - pg_relation_size(tablename::text)) as index_size
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename IN ('visits', 'users', 'dashboard_users')
        ORDER BY pg_total_relation_size(tablename::text) DESC;
    """)

    print(f"\n{'Table':<20} {'Total Size':<15} {'Table Size':<15} {'Indexes Size':<15}")
    print("-" * 65)
    for t in tables:
        print(f"{t['tablename']:<20} {t['total_size']:<15} {t['table_size']:<15} {t['index_size']:<15}")

    visits_count = await conn.fetchval("SELECT COUNT(*) FROM visits")
    users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
    print(f"\nUsers: {users_count:,}  Visits: {visits_count:,}")


async def main() -> None:
    print("=" * 70)
    print("BrowserReporterConsole — Database Migration Tool")
    print("=" * 70)

    # CLI runs outside Docker by default
    if "DB_HOST" not in os.environ:
        os.environ["DB_HOST"] = "localhost"

    db_config = get_db_config()
    try:
        conn = await asyncpg.connect(**db_config)
    except Exception as e:
        print(f"Failed to connect: {e}")
        print("  Ensure PostgreSQL is running: docker compose ps")
        sys.exit(1)

    print(f"Connected to {db_config['host']}:{db_config['port']}/{db_config['database']}")

    try:
        await ensure_schema_columns(conn)

        migrations_dir = Path(__file__).parent
        applied, skipped = await apply_migrations(conn, migrations_dir)

        print(f"\nApplied: {applied}  Skipped: {skipped}")
        await check_index_status(conn)
        await check_database_stats(conn)
        print("\nDone.")
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
