# Database Migrations

This directory contains SQL migrations and management scripts for the BrowserReporterConsole database.

## Quick Start

### Apply All Migrations

```bash
# From outside docker (ensure PostgreSQL is accessible on localhost:5432)
cd backend/migrations
python3 apply_migration.py

# Or from inside the backend container
docker exec -it browserreporterconsole-backend-1 python migrations/apply_migration.py
```

### Check Migration Status

```bash
# View applied migrations
docker exec -it browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter \
  -c "SELECT * FROM schema_migrations ORDER BY applied_at;"
```

### Verify Index Creation

```bash
# Check if indexes were created successfully
docker exec -it browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter \
  -c "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname LIKE 'idx_%';"
```

## Migration Files

### 001_add_performance_indexes.sql
**Purpose:** Add critical performance indexes for high-volume data (800+ users, millions of visits)

**Indexes Created:**
- `idx_visits_user_time` - Composite index for user analytics queries
- `idx_visits_time_desc` - Time-based filtering and sorting
- `idx_visits_search_vector` - Full-text search optimization
- `idx_visits_url_gin` - URL pattern search with trigrams
- `idx_visits_title_gin` - Title pattern search
- `idx_users_homegroup` - Homegroup filtering
- `idx_users_last_seen` - Last seen sorting
- `idx_visits_time_url` - Combined time + URL queries

**Expected Impact:**
- Query performance: 80-90% improvement
- Dashboard load time: 10-30s → < 2s
- Search queries: 15-45s → 1-2s

**Time to Create:**
- Empty database: < 1 minute
- 1M records: 2-5 minutes
- 10M records: 10-20 minutes

**Note:** Uses `CREATE INDEX CONCURRENTLY` to avoid table locking. Safe to run on production.

## Monitoring Index Health

### Check Index Usage

```sql
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan as times_used,
    idx_tup_read as rows_read,
    idx_tup_fetch as rows_fetched
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;
```

### Check Index Sizes

```sql
SELECT
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexname::regclass)) as index_size
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('visits', 'users')
ORDER BY pg_relation_size(indexname::regclass) DESC;
```

### Find Unused Indexes

```sql
SELECT
    schemaname,
    tablename,
    indexname
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
  AND idx_scan = 0
  AND indexname NOT LIKE 'pg_toast%';
```

## Troubleshooting

### Migration Already Applied
Migrations are tracked in the `schema_migrations` table. If you need to re-run:

```sql
DELETE FROM schema_migrations WHERE migration_name = '001_add_performance_indexes.sql';
```

### Index Creation Taking Too Long
- CONCURRENTLY mode is slower but safer (no table locking)
- For very large tables (10M+ records), consider running during off-hours
- Monitor progress: `SELECT * FROM pg_stat_activity WHERE query LIKE '%CREATE INDEX%';`

### Out of Disk Space
Indexes require 20-30% of table size. Before creating:

```bash
# Check available space
df -h

# Estimate index size
SELECT pg_size_pretty(pg_relation_size('visits')) as table_size,
       pg_size_pretty(pg_relation_size('visits') * 0.3) as estimated_index_size;
```

### Connection Refused
Ensure PostgreSQL is running and accessible:

```bash
docker compose ps
docker compose logs db
```

If running outside docker, PostgreSQL must be accessible on `localhost:5432`.

## Best Practices

1. **Test First:** Always test migrations on a copy of production data
2. **Backup:** Take a database backup before applying migrations
3. **Monitor:** Watch query performance before and after
4. **Cleanup:** Drop unused indexes to save disk space
5. **Document:** Add new migration files with clear descriptions

## Adding New Migrations

Create new migration files with sequential numbering:

```bash
# Example: 002_add_archive_table.sql
touch backend/migrations/002_add_archive_table.sql
```

Format:
```sql
-- Migration: [Brief description]
-- Purpose: [Why this migration is needed]
-- Expected improvement: [What will improve]

[SQL statements]
```

The migration script will automatically detect and apply new migrations in order.
