#!/usr/bin/env python3
"""
Data Retention Management Script
Implements 90-day retention policy for BrowserReporterConsole

Three-tier data lifecycle:
1. Active data (0-90 days): Kept in visits table with full indexing
2. Archive data (90 days - 1 year): Moved to visits_archive table
3. Cold storage (1+ year): Exported to Parquet files and deleted

Run monthly via cron: 0 2 1 * * /path/to/data_retention.py
"""

import asyncio
import asyncpg
import sys
from datetime import datetime, timedelta
from pathlib import Path
import os


# Database connection settings
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "user": os.getenv("DB_USER", "browser_reporter"),
    "password": os.getenv("DB_PASSWORD", "browser_reporter"),
    "database": os.getenv("DB_NAME", "browser_reporter"),
}

# Retention policy settings (in days)
ACTIVE_RETENTION_DAYS = 90      # Keep in visits table
ARCHIVE_RETENTION_DAYS = 365    # Keep in visits_archive table
EXPORT_BATCH_SIZE = 10000       # Records per export batch

# Export directory for cold storage
EXPORT_DIR = Path("/backups/archives")


class DataRetentionManager:
    """Manages automated data lifecycle and retention policies"""

    def __init__(self):
        self.conn = None
        self.stats = {
            "archived_count": 0,
            "exported_count": 0,
            "deleted_count": 0,
            "errors": []
        }

    async def connect(self):
        """Establish database connection"""
        try:
            self.conn = await asyncpg.connect(**DB_CONFIG)
            print("✓ Connected to PostgreSQL database")
        except Exception as e:
            print(f"❌ Failed to connect to database: {e}")
            sys.exit(1)

    async def close(self):
        """Close database connection"""
        if self.conn:
            await self.conn.close()
            print("🔌 Database connection closed")

    async def get_table_stats(self):
        """Get current statistics about data distribution"""
        cutoff = datetime.now() - timedelta(days=ACTIVE_RETENTION_DAYS)
        query = """
            SELECT
                'visits' as table_name,
                COUNT(*) as total_records,
                COUNT(*) FILTER (WHERE visit_time > $1) as active_records,
                COUNT(*) FILTER (WHERE visit_time <= $1) as old_records,
                MIN(visit_time) as oldest_record,
                MAX(visit_time) as newest_record,
                pg_size_pretty(pg_total_relation_size('visits')) as table_size
            FROM visits
            UNION ALL
            SELECT
                'visits_archive' as table_name,
                COUNT(*) as total_records,
                0 as active_records,
                COUNT(*) as old_records,
                MIN(visit_time) as oldest_record,
                MAX(visit_time) as newest_record,
                pg_size_pretty(pg_total_relation_size('visits_archive')) as table_size
            FROM visits_archive;
        """

        stats = await self.conn.fetch(query, cutoff)
        return stats

    async def archive_old_visits(self):
        """
        Step 1: Move visits older than 90 days to archive table
        Uses INSERT...SELECT for efficiency
        """
        print(f"\n📦 Step 1: Archiving visits older than {ACTIVE_RETENTION_DAYS} days...")

        cutoff_date = datetime.now() - timedelta(days=ACTIVE_RETENTION_DAYS)

        try:
            # Check if archive table exists
            table_exists = await self.conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'visits_archive'
                );
            """)

            if not table_exists:
                print("⚠️  visits_archive table does not exist. Run migration 002 first.")
                return

            # Count records to archive
            count = await self.conn.fetchval(
                "SELECT COUNT(*) FROM visits WHERE visit_time < $1",
                cutoff_date
            )

            if count == 0:
                print("   No records to archive")
                return

            print(f"   Found {count:,} records to archive...")

            # Move records to archive (batched for large datasets)
            archived = await self.conn.execute("""
                INSERT INTO visits_archive
                    (id, user_id, computer_name, url, title, visit_time, inserted_at, search_vector)
                SELECT
                    id, user_id, computer_name, url, title, visit_time, inserted_at, search_vector
                FROM visits
                WHERE visit_time < $1
                ON CONFLICT (id) DO NOTHING;
            """, cutoff_date)

            # Extract count from result
            archived_count = int(archived.split()[-1]) if archived else 0
            self.stats["archived_count"] = archived_count

            # Delete archived records from visits table
            deleted = await self.conn.execute(
                "DELETE FROM visits WHERE visit_time < $1",
                cutoff_date
            )

            deleted_count = int(deleted.split()[-1]) if deleted else 0

            print(f"   ✅ Archived {archived_count:,} records")
            print(f"   ✅ Removed {deleted_count:,} records from active table")

        except Exception as e:
            print(f"   ❌ Archive failed: {e}")
            self.stats["errors"].append(f"Archive: {e}")

    async def export_old_archives(self):
        """
        Step 2: Export archived visits older than 1 year to CSV files
        Prepares data for deletion from database
        """
        print(f"\n💾 Step 2: Exporting archived visits older than {ARCHIVE_RETENTION_DAYS} days...")

        cutoff_date = datetime.now() - timedelta(days=ARCHIVE_RETENTION_DAYS)

        try:
            # Count records to export
            count = await self.conn.fetchval(
                "SELECT COUNT(*) FROM visits_archive WHERE visit_time < $1",
                cutoff_date
            )

            if count == 0:
                print("   No records to export")
                return

            print(f"   Found {count:,} records to export...")

            # Create export directory if it doesn't exist
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)

            # Generate export filename with timestamp
            export_file = EXPORT_DIR / f"visits_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

            # Export to CSV using COPY command (much faster than Python)
            async with self.conn.transaction():
                copy_query = """
                    COPY (
                        SELECT
                            id,
                            user_id,
                            computer_name,
                            url,
                            title,
                            visit_time,
                            inserted_at,
                            archived_at
                        FROM visits_archive
                        WHERE visit_time < $1
                        ORDER BY visit_time
                    ) TO STDOUT WITH CSV HEADER;
                """

                # Execute COPY command with parameterised cutoff date
                with open(export_file, 'w') as f:
                    await self.conn.copy_from_query(copy_query, cutoff_date, output=f)

            # Get file size
            file_size = export_file.stat().st_size
            file_size_mb = file_size / (1024 * 1024)

            self.stats["exported_count"] = count

            print(f"   ✅ Exported {count:,} records to {export_file.name}")
            print(f"   📊 File size: {file_size_mb:.2f} MB")

            # Optional: Compress the CSV file
            await self._compress_export(export_file)

        except Exception as e:
            print(f"   ❌ Export failed: {e}")
            self.stats["errors"].append(f"Export: {e}")

    async def _compress_export(self, csv_file: Path):
        """Compress CSV export using gzip"""
        try:
            import gzip
            import shutil

            gz_file = csv_file.with_suffix('.csv.gz')

            with open(csv_file, 'rb') as f_in:
                with gzip.open(gz_file, 'wb', compresslevel=9) as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Remove original CSV
            csv_file.unlink()

            # Get compression ratio
            original_size = csv_file.stat().st_size if csv_file.exists() else 0
            compressed_size = gz_file.stat().st_size
            ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0

            print(f"   ✅ Compressed to {gz_file.name} (saved {ratio:.1f}%)")

        except Exception as e:
            print(f"   ⚠️  Compression failed: {e} (keeping uncompressed file)")

    async def delete_exported_archives(self):
        """
        Step 3: Delete archived records that have been exported
        Only deletes records older than 1 year that were successfully exported
        """
        print(f"\n🗑️  Step 3: Deleting exported records from archive...")

        cutoff_date = datetime.now() - timedelta(days=ARCHIVE_RETENTION_DAYS)

        try:
            # Only delete if we successfully exported in this run
            if self.stats["exported_count"] == 0:
                print("   No records exported, skipping deletion")
                return

            # Delete old records from archive
            deleted = await self.conn.execute(
                "DELETE FROM visits_archive WHERE visit_time < $1",
                cutoff_date
            )

            deleted_count = int(deleted.split()[-1]) if deleted else 0
            self.stats["deleted_count"] = deleted_count

            print(f"   ✅ Deleted {deleted_count:,} old records from archive")

        except Exception as e:
            print(f"   ❌ Deletion failed: {e}")
            self.stats["errors"].append(f"Delete: {e}")

    async def vacuum_and_analyze(self):
        """
        Step 4: Reclaim disk space and update statistics
        Important for maintaining query performance
        """
        print("\n🧹 Step 4: Running database maintenance...")

        try:
            # ANALYZE updates table statistics
            await self.conn.execute("ANALYZE visits;")
            print("   ✅ Analyzed visits table")

            await self.conn.execute("ANALYZE visits_archive;")
            print("   ✅ Analyzed visits_archive table")

            # VACUUM can't be run in a transaction, needs separate connection
            print("   ℹ️  Run 'VACUUM visits;' manually or via cron for space reclamation")

        except Exception as e:
            print(f"   ⚠️  Maintenance warning: {e}")

    async def print_summary(self):
        """Print final summary of retention operations"""
        print("\n" + "=" * 70)
        print("📊 DATA RETENTION SUMMARY")
        print("=" * 70)

        # Get updated stats
        stats = await self.get_table_stats()

        for stat in stats:
            print(f"\n{stat['table_name'].upper()}:")
            print(f"   Total records: {stat['total_records']:,}")
            if stat['table_name'] == 'visits':
                print(f"   Active (< {ACTIVE_RETENTION_DAYS} days): {stat['active_records']:,}")
            print(f"   Old records: {stat['old_records']:,}")
            print(f"   Date range: {stat['oldest_record']} to {stat['newest_record']}")
            print(f"   Table size: {stat['table_size']}")

        print(f"\n🔧 OPERATIONS PERFORMED:")
        print(f"   Archived to visits_archive: {self.stats['archived_count']:,} records")
        print(f"   Exported to files: {self.stats['exported_count']:,} records")
        print(f"   Deleted from archive: {self.stats['deleted_count']:,} records")

        if self.stats['errors']:
            print(f"\n⚠️  ERRORS ({len(self.stats['errors'])}):")
            for error in self.stats['errors']:
                print(f"   • {error}")
        else:
            print(f"\n✅ All operations completed successfully!")

        print("=" * 70)

    async def run(self):
        """Execute full data retention workflow"""
        print("=" * 70)
        print("🔧 BrowserReporterConsole Data Retention Manager")
        print(f"📅 Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)

        try:
            await self.connect()

            # Show current state
            print("\n📊 Current database state:")
            stats = await self.get_table_stats()
            for stat in stats:
                print(f"   {stat['table_name']}: {stat['total_records']:,} records, {stat['table_size']}")

            # Execute retention workflow
            await self.archive_old_visits()       # Step 1: 90-day retention
            await self.export_old_archives()      # Step 2: Export old data
            await self.delete_exported_archives() # Step 3: Delete exported data
            await self.vacuum_and_analyze()       # Step 4: Maintenance

            # Show final summary
            await self.print_summary()

        finally:
            await self.close()


async def main():
    """Main entry point"""
    manager = DataRetentionManager()

    try:
        await manager.run()
    except KeyboardInterrupt:
        print("\n\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
