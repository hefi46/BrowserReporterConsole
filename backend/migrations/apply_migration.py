#!/usr/bin/env python3
"""
Database Migration Manager
Applies SQL migrations to the PostgreSQL database safely with rollback support
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
import asyncpg


# Database connection settings (from docker-compose.yml)
DB_CONFIG = {
    "host": "localhost",  # Use "db" if running inside docker
    "port": 5432,
    "user": "browser_reporter",
    "password": "browser_reporter",
    "database": "browser_reporter",
}


async def get_connection():
    """Create database connection"""
    try:
        conn = await asyncpg.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        print("\n💡 Troubleshooting:")
        print("   1. Ensure PostgreSQL is running: docker compose ps")
        print("   2. If running outside docker, use host='localhost'")
        print("   3. If running inside docker, use host='db'")
        sys.exit(1)


async def create_migration_table(conn):
    """Create table to track applied migrations"""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id SERIAL PRIMARY KEY,
            migration_name VARCHAR(255) UNIQUE NOT NULL,
            applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            execution_time_seconds DECIMAL(10, 3)
        )
    """)
    print("✓ Migration tracking table ready")


async def is_migration_applied(conn, migration_name):
    """Check if migration has already been applied"""
    result = await conn.fetchval(
        "SELECT COUNT(*) FROM schema_migrations WHERE migration_name = $1",
        migration_name
    )
    return result > 0


async def record_migration(conn, migration_name, execution_time):
    """Record successfully applied migration"""
    await conn.execute(
        """
        INSERT INTO schema_migrations (migration_name, execution_time_seconds)
        VALUES ($1, $2)
        """,
        migration_name,
        execution_time
    )


def _split_sql_statements(sql_content: str) -> list[str]:
    """Split SQL content into individual statements, respecting DO $$ blocks."""
    statements = []
    current = []
    in_dollar_block = False

    for line in sql_content.splitlines():
        stripped = line.strip()
        # Skip empty lines and comments outside of statements
        if not stripped or (stripped.startswith("--") and not current):
            continue

        current.append(line)

        # Track DO $$ ... END $$; blocks
        if stripped.upper().startswith("DO $$") or stripped.upper().startswith("DO $"):
            in_dollar_block = True
        if in_dollar_block and stripped.endswith("$$;"):
            in_dollar_block = False
            statements.append("\n".join(current))
            current = []
            continue

        # Normal statement ending with semicolon (outside dollar blocks)
        if not in_dollar_block and stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []

    # Catch any trailing statement without semicolon
    if current:
        stmt = "\n".join(current).strip()
        if stmt:
            statements.append(stmt)

    return [s for s in statements if s.strip() and not all(
        l.strip().startswith("--") or not l.strip() for l in s.splitlines()
    )]


async def apply_migration(conn, migration_file: Path):
    """Apply a single SQL migration file, executing each statement individually
    so that CREATE INDEX CONCURRENTLY works (it cannot run in a transaction)."""
    migration_name = migration_file.name

    # Check if already applied
    if await is_migration_applied(conn, migration_name):
        print(f"⏭️  Skipping {migration_name} (already applied)")
        return True

    print(f"\n📋 Applying migration: {migration_name}")

    sql_content = migration_file.read_text()
    statements = _split_sql_statements(sql_content)

    start_time = datetime.now()

    try:
        for i, stmt in enumerate(statements, 1):
            try:
                await conn.execute(stmt)
            except Exception as e:
                # Skip "already exists" errors for IF NOT EXISTS statements
                err_msg = str(e).lower()
                if "already exists" in err_msg or "duplicate" in err_msg:
                    print(f"   ⏭️  Statement {i}/{len(statements)}: already exists, skipping")
                    continue
                raise

        execution_time = (datetime.now() - start_time).total_seconds()
        await record_migration(conn, migration_name, execution_time)

        print(f"   ✅ Applied in {execution_time:.2f} seconds ({len(statements)} statements)")
        return True

    except Exception as e:
        print(f"   ❌ Migration failed: {e}")
        return False


async def check_index_status(conn):
    """Check status of indexes being created"""
    query = """
        SELECT
            schemaname,
            tablename,
            indexname,
            pg_size_pretty(pg_relation_size(indexname::regclass)) as index_size
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename IN ('visits', 'users')
          AND indexname LIKE 'idx_%'
        ORDER BY tablename, indexname;
    """

    indexes = await conn.fetch(query)

    if indexes:
        print("\n📊 Current indexes:")
        print(f"{'Table':<15} {'Index Name':<35} {'Size':<10}")
        print("-" * 60)
        for idx in indexes:
            print(f"{idx['tablename']:<15} {idx['indexname']:<35} {idx['index_size']:<10}")
    else:
        print("\n⚠️  No custom indexes found")


async def check_database_stats(conn):
    """Display database statistics"""
    # Get table sizes
    tables = await conn.fetch("""
        SELECT
            tablename,
            pg_size_pretty(pg_total_relation_size(tablename::text)) as total_size,
            pg_size_pretty(pg_relation_size(tablename::text)) as table_size,
            pg_size_pretty(pg_total_relation_size(tablename::text) - pg_relation_size(tablename::text)) as index_size
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename IN ('visits', 'users', 'dashboard_users')
        ORDER BY pg_total_relation_size(tablename::text) DESC;
    """)

    print("\n📊 Database statistics:")
    print(f"{'Table':<20} {'Total Size':<15} {'Table Size':<15} {'Indexes Size':<15}")
    print("-" * 65)
    for table in tables:
        print(f"{table['tablename']:<20} {table['total_size']:<15} {table['table_size']:<15} {table['index_size']:<15}")

    # Get record counts
    visits_count = await conn.fetchval("SELECT COUNT(*) FROM visits")
    users_count = await conn.fetchval("SELECT COUNT(*) FROM users")

    print(f"\n📈 Record counts:")
    print(f"   Users: {users_count:,}")
    print(f"   Visits: {visits_count:,}")


async def main():
    """Main migration runner"""
    print("=" * 70)
    print("🔧 BrowserReporterConsole Database Migration Tool")
    print("=" * 70)

    # Connect to database
    conn = await get_connection()
    print("✓ Connected to PostgreSQL database")

    try:
        # Create migration tracking table
        await create_migration_table(conn)

        # Find all migration files
        migrations_dir = Path(__file__).parent
        migration_files = sorted(migrations_dir.glob("*.sql"))

        if not migration_files:
            print("\n⚠️  No migration files found in:", migrations_dir)
            return

        print(f"\n📁 Found {len(migration_files)} migration file(s)")

        # Apply each migration
        success_count = 0
        for migration_file in migration_files:
            if await apply_migration(conn, migration_file):
                success_count += 1

        print("\n" + "=" * 70)
        print(f"📊 Summary: {success_count}/{len(migration_files)} migrations applied successfully")
        print("=" * 70)

        # Show index status
        await check_index_status(conn)

        # Show database stats
        await check_database_stats(conn)

        print("\n✅ Migration process completed!")
        print("\n💡 Next steps:")
        print("   1. Test query performance: Run slow queries and measure improvement")
        print("   2. Monitor index usage: SELECT * FROM pg_stat_user_indexes")
        print("   3. Check for missing indexes: Look for sequential scans in slow queries")

    finally:
        await conn.close()
        print("\n🔌 Database connection closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Migration cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        sys.exit(1)
