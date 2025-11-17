# Database Management Guide

Comprehensive guide for managing the BrowserReporterConsole PostgreSQL database at scale (800+ users, 2GB+ data).

## Table of Contents
- [Overview](#overview)
- [Quick Start](#quick-start)
- [Performance Optimization](#performance-optimization)
- [Data Retention](#data-retention)
- [Backup & Recovery](#backup--recovery)
- [Monitoring](#monitoring)
- [Maintenance](#maintenance)
- [Troubleshooting](#troubleshooting)

---

## Overview

### System Specifications
- **Database:** PostgreSQL 15
- **Expected Scale:** 800 users, ~160,000 visits/day
- **Data Growth:** ~58GB/year (without retention)
- **Retention Policy:** 90 days active, 1 year archive
- **Performance Target:** < 5 seconds for dashboard queries

### Architecture Components
```
┌─────────────────────────────────────────────────────────┐
│ FastAPI Backend (Async)                                 │
│  ├─ Connection Pool (20 + 40 overflow)                  │
│  └─ SQLAlchemy Async ORM                                │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ PostgreSQL 15 (Docker)                                  │
│  ├─ Optimized Configuration (256MB shared_buffers)      │
│  ├─ pg_stat_statements (query monitoring)               │
│  └─ Performance Indexes                                 │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Data Lifecycle                                          │
│  ├─ Active (0-90 days):     visits table               │
│  ├─ Archive (90-365 days):  visits_archive table       │
│  └─ Cold Storage (1+ year): Compressed Parquet files   │
└─────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Apply Performance Indexes

**First time setup - CRITICAL for performance:**

```bash
# From host machine
cd backend/migrations
python3 apply_migration.py

# Expected output:
# ✓ Applied 001_add_performance_indexes.sql
# ✓ Applied 002_add_archive_table.sql
```

**What this does:**
- Adds 8 critical indexes on visits table
- Creates visits_archive table
- Expected improvement: 80-90% query time reduction

**Time required:**
- Empty database: < 1 minute
- 1M records: 2-5 minutes
- 10M records: 10-20 minutes

### 2. Restart with Optimized Configuration

```bash
# Restart PostgreSQL with new settings
docker compose down
docker compose up -d

# Verify settings
docker exec browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter \
  -c "SHOW shared_buffers; SHOW max_connections;"
```

### 3. Set Up Automated Maintenance

```bash
# Make scripts executable
chmod +x backend/scripts/*.sh
chmod +x backend/migrations/apply_migration.py

# Install cron jobs
cp backend/scripts/crontab.example ~/browser_reporter_cron
# Edit paths in ~/browser_reporter_cron
crontab ~/browser_reporter_cron
```

---

## Performance Optimization

### Indexes Created

| Index Name | Tables/Columns | Purpose | Impact |
|------------|---------------|---------|--------|
| `idx_visits_user_time` | visits(user_id, visit_time) | User analytics queries | Dashboard: 10s → <1s |
| `idx_visits_time_desc` | visits(visit_time DESC) | Date filtering | Date range: 5s → <500ms |
| `idx_visits_search_vector` | visits(search_vector GIN) | Full-text search | Search: 15s → 1-2s |
| `idx_visits_url_gin` | visits(url GIN) | URL pattern search | ILIKE queries: 10s → 1s |
| `idx_visits_title_gin` | visits(title GIN) | Title search | ILIKE queries: 10s → 1s |
| `idx_users_homegroup` | users(homegroup) | Homegroup filtering | Filter: 2s → <200ms |
| `idx_users_last_seen` | users(last_seen_at) | Active user queries | Sort: 1s → <100ms |
| `idx_visits_time_url` | visits(visit_time, url) | Combined queries | Complex: 5s → <1s |

### Query Performance Benchmarks

**Before Optimization:**
- Dashboard load (user analytics): 10-30 seconds
- Search queries: 15-45 seconds
- User detail view: 3-8 seconds

**After Optimization:**
- Dashboard load: 1-2 seconds ✅
- Search queries: 1-2 seconds ✅
- User detail view: < 500ms ✅

### Connection Pooling

**Configuration:** (`backend/database.py`)
```python
pool_size=20              # Permanent connections
max_overflow=40          # Burst capacity (total: 60)
pool_pre_ping=True       # Health checks
pool_recycle=3600        # Hourly recycle
```

**Monitoring connections:**
```bash
docker exec browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter \
  -c "SELECT count(*), state FROM pg_stat_activity WHERE datname='browser_reporter' GROUP BY state;"
```

---

## Data Retention

### 90-Day Retention Policy

**Implementation:** 3-tier data lifecycle

```
┌─────────────────┐
│ Active (0-90d)  │  visits table (fully indexed, fast queries)
│ 15M records     │  ~12-15GB
│ 12-15GB size    │
└─────────────────┘
         │ Monthly archival
         ▼
┌─────────────────┐
│ Archive (90-365)│  visits_archive table (minimal indexes)
│ 60M records     │  ~40-50GB
│ 40-50GB size    │
└─────────────────┘
         │ Yearly export
         ▼
┌─────────────────┐
│ Cold Storage    │  Compressed Parquet files
│ 200M+ records   │  ~10GB/year (compressed)
│ Parquet files   │
└─────────────────┘
```

### Running Data Retention

**Manual execution:**
```bash
cd backend/scripts
python3 data_retention.py
```

**Automated (monthly via cron):**
```cron
0 1 1 * * cd /home/hefi/BrowserReporterConsole/backend/scripts && python3 data_retention.py >> /var/log/browser_reporter/retention.log 2>&1
```

**What it does:**
1. Moves visits older than 90 days to visits_archive
2. Exports archive visits older than 1 year to Parquet files
3. Deletes exported records from database
4. Runs VACUUM and ANALYZE

**Expected results:**
- Without retention: 58GB/year → 290GB in 5 years
- With retention: ~15GB steady state + 10GB/year archives

### Storage Locations

```
/backups/
├── postgres/              # Daily database dumps
│   ├── backup_20250117_020000.sql.gz
│   └── backup_20250116_020000.sql.gz (7 days kept)
└── archives/              # Historical data exports
    ├── visits_export_20250101_010000.csv.gz
    └── visits_export_20240101_010000.csv.gz
```

---

## Backup & Recovery

### Daily Automated Backups

**Script:** `backend/scripts/backup_database.sh`

```bash
# Run manual backup
./backend/scripts/backup_database.sh

# With custom location and retention
./backend/scripts/backup_database.sh /my/backup/dir 14
```

**Features:**
- Compressed SQL dumps (gzip)
- 7-day retention by default
- Automated cleanup of old backups
- Integrity verification

**Automated schedule (via cron):**
```cron
0 2 * * * /home/hefi/BrowserReporterConsole/backend/scripts/backup_database.sh /backups/postgres 7 >> /var/log/browser_reporter/backup.log 2>&1
```

### Restore from Backup

**Full database restore:**
```bash
# Stop the application
docker compose down

# Restore database
gunzip -c /backups/postgres/backup_20250117_020000.sql.gz | \
  docker exec -i browserreporterconsole-db-1 \
  psql -U browser_reporter -d browser_reporter

# Restart application
docker compose up -d
```

**Restore specific table:**
```bash
# Extract specific table from backup
gunzip -c backup.sql.gz | grep -A 1000000 "Table: visits" | \
  docker exec -i browserreporterconsole-db-1 \
  psql -U browser_reporter -d browser_reporter
```

### Disaster Recovery Checklist

1. **Stop the application:** `docker compose down`
2. **Verify backup exists:** `ls -lh /backups/postgres/`
3. **Test backup integrity:** `gzip -t backup.sql.gz`
4. **Clear database (if needed):** `docker volume rm browserreporterconsole_db_data`
5. **Restore from backup:** (see above)
6. **Verify restoration:** Check record counts
7. **Restart application:** `docker compose up -d`
8. **Test functionality:** Login and view reports

---

## Monitoring

### Database Statistics Endpoint

**API Endpoint:** `GET /api/admin/db-stats` (Admin only)

**Access via curl:**
```bash
# Login and get session cookie
curl -c cookies.txt -X POST http://localhost:8000/login \
  -d "username=admin&password=admin"

# Get database stats
curl -b cookies.txt http://localhost:8000/api/admin/db-stats | jq
```

**Response includes:**
```json
{
  "database": {
    "total_size": "15 GB",
    "total_bytes": 16106127360,
    "connection_info": {
      "total_connections": 25,
      "active_connections": 3,
      "idle_connections": 22
    }
  },
  "records": {
    "users": 800,
    "visits": 14523000,
    "visits_archive": 45231000,
    "total_visits": 59754000
  },
  "activity": {
    "oldest_visit": "2024-01-17T10:30:00Z",
    "newest_visit": "2025-01-17T14:25:00Z",
    "visits_last_24h": 165000,
    "visits_last_7d": 1200000,
    "active_users_24h": 750
  },
  "indexes": [...],
  "slow_queries": [...]
}
```

### Manual Monitoring Queries

**Check database size:**
```sql
SELECT pg_size_pretty(pg_database_size('browser_reporter')) as db_size;
```

**Check table sizes:**
```sql
SELECT
    tablename,
    pg_size_pretty(pg_total_relation_size(tablename::text)) as total_size,
    pg_size_pretty(pg_relation_size(tablename::text)) as table_size,
    pg_size_pretty(pg_total_relation_size(tablename::text) - pg_relation_size(tablename::text)) as index_size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(tablename::text) DESC;
```

**Check index usage:**
```sql
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan as times_used,
    pg_size_pretty(pg_relation_size(indexrelid)) as index_size
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;
```

**Find slow queries (requires pg_stat_statements):**
```sql
SELECT
    substring(query, 1, 100) as query_preview,
    calls,
    mean_exec_time,
    total_exec_time
FROM pg_stat_statements
WHERE mean_exec_time > 1000  -- Queries averaging > 1 second
ORDER BY mean_exec_time DESC
LIMIT 20;
```

**Check dead tuples (vacuum needed?):**
```sql
SELECT
    schemaname,
    tablename,
    n_live_tup as live_rows,
    n_dead_tup as dead_rows,
    ROUND(100 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) as dead_percentage,
    last_vacuum,
    last_autovacuum
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY n_dead_tup DESC;
```

### Alerts & Thresholds

**Recommended alert thresholds:**

| Metric | Warning | Critical |
|--------|---------|----------|
| Database size | > 50GB | > 100GB |
| Dead tuples % | > 10% | > 20% |
| Visits table size | > 20GB | > 30GB |
| Connection count | > 80 | > 95 |
| Slow query avg time | > 2s | > 5s |
| Disk space free | < 20GB | < 10GB |

---

## Maintenance

### Routine Maintenance Tasks

**Daily (automated via cron):**
- Database backup (2:00 AM)
- ANALYZE (3:00 AM) - updates statistics

**Weekly (automated via cron):**
- VACUUM + ANALYZE (Sunday 2:00 AM) - reclaims space

**Monthly (automated via cron):**
- Data retention (1st at 1:00 AM) - archive old data
- Optional: VACUUM FULL (1st at 4:00 AM) - deep clean

### Manual Maintenance

**Quick statistics update:**
```bash
./backend/scripts/maintenance.sh --analyze-only
```

**Standard maintenance (VACUUM + ANALYZE):**
```bash
./backend/scripts/maintenance.sh
```

**Deep clean (locks tables, use during maintenance window):**
```bash
./backend/scripts/maintenance.sh --vacuum-full
```

**Rebuild indexes (if bloated):**
```bash
./backend/scripts/maintenance.sh --reindex
```

**Full maintenance (monthly recommended):**
```bash
./backend/scripts/maintenance.sh --vacuum-full --reindex
```

### When to Run Maintenance

**ANALYZE (safe anytime):**
- After bulk inserts
- After large deletions
- When queries become slow

**VACUUM (safe, minimal impact):**
- Weekly during low-traffic
- After data retention runs
- When dead tuples > 10%

**VACUUM FULL (locks tables!):**
- Only during maintenance windows
- When database is significantly bloated
- After major data cleanup

**REINDEX (locks indexes):**
- Quarterly or when index bloat detected
- After VACUUM FULL
- When index usage drops

---

## Troubleshooting

### Common Issues

#### Slow Queries

**Symptom:** Dashboard takes > 5 seconds to load

**Diagnosis:**
```bash
# Check if indexes exist
docker exec browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter \
  -c "SELECT indexname FROM pg_indexes WHERE tablename='visits' AND indexname LIKE 'idx_%';"

# Check query plan
docker exec browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter \
  -c "EXPLAIN ANALYZE SELECT * FROM visits WHERE user_id = 1 ORDER BY visit_time DESC LIMIT 50;"
```

**Solutions:**
1. Apply performance indexes: `python3 backend/migrations/apply_migration.py`
2. Run ANALYZE: `./backend/scripts/maintenance.sh --analyze-only`
3. Check for missing indexes in query plan (look for "Seq Scan")

#### Database Size Growing Rapidly

**Symptom:** Database > 50GB after a few months

**Diagnosis:**
```sql
SELECT
    tablename,
    pg_size_pretty(pg_total_relation_size(tablename::text)) as size,
    (SELECT COUNT(*) FROM visits) as visit_count
FROM pg_tables
WHERE tablename = 'visits';
```

**Solutions:**
1. Run data retention: `python3 backend/scripts/data_retention.py`
2. Verify retention policy: Check oldest visit date
3. Run VACUUM FULL: `./backend/scripts/maintenance.sh --vacuum-full`

#### Connection Pool Exhausted

**Symptom:** "Too many connections" or slow response times

**Diagnosis:**
```sql
SELECT count(*), state FROM pg_stat_activity
WHERE datname='browser_reporter'
GROUP BY state;
```

**Solutions:**
1. Increase pool_size in `backend/database.py`
2. Increase max_connections in `docker-compose.yml`
3. Check for connection leaks (connections stuck in 'idle')

#### Backup Failed

**Symptom:** Backup script reports error

**Diagnosis:**
```bash
# Check if container is running
docker compose ps

# Check disk space
df -h /backups

# Test manual backup
docker exec browserreporterconsole-db-1 pg_dump -U browser_reporter browser_reporter > test.sql
```

**Solutions:**
1. Verify container is running: `docker compose up -d`
2. Check disk space: Need 2-3x database size free
3. Verify permissions on backup directory

#### Migration Failed

**Symptom:** Index creation fails or hangs

**Diagnosis:**
```sql
-- Check for long-running index creation
SELECT * FROM pg_stat_activity WHERE query LIKE '%CREATE INDEX%';

-- Check for locks
SELECT * FROM pg_locks WHERE NOT granted;
```

**Solutions:**
1. Use CONCURRENTLY option (already in migrations)
2. Run during low-traffic period
3. Check disk space (indexes need 20-30% of table size)
4. Kill hanging queries if needed

---

## Performance Tuning Reference

### PostgreSQL Configuration

**Current settings** (from `docker-compose.yml`):

```yaml
# Connection & Memory
max_connections: 100
shared_buffers: 256MB
effective_cache_size: 1GB
work_mem: 4MB

# Query Planner
random_page_cost: 1.1           # SSD-optimized
effective_io_concurrency: 200   # Parallel I/O operations

# WAL & Checkpoints
checkpoint_completion_target: 0.9
wal_buffers: 16MB
min_wal_size: 1GB
max_wal_size: 4GB

# Monitoring
shared_preload_libraries: pg_stat_statements
log_min_duration_statement: 1000  # Log queries > 1s
```

### Tuning for Different Scales

**Small deployment (< 100 users):**
- pool_size: 10
- shared_buffers: 128MB
- work_mem: 2MB

**Medium deployment (100-500 users) - CURRENT:**
- pool_size: 20
- shared_buffers: 256MB
- work_mem: 4MB

**Large deployment (500-1000 users):**
- pool_size: 30
- shared_buffers: 512MB
- work_mem: 8MB
- max_connections: 150

---

## Quick Reference Commands

```bash
# Apply migrations
python3 backend/migrations/apply_migration.py

# Backup database
./backend/scripts/backup_database.sh

# Run maintenance
./backend/scripts/maintenance.sh

# Data retention
python3 backend/scripts/data_retention.py

# Restart with new config
docker compose down && docker compose up -d

# View logs
docker compose logs -f backend
docker compose logs -f db

# Database shell
docker exec -it browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter

# Check database size
docker exec browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter \
  -c "SELECT pg_size_pretty(pg_database_size('browser_reporter'));"

# Monitor connections
docker exec browserreporterconsole-db-1 psql -U browser_reporter -d browser_reporter \
  -c "SELECT count(*), state FROM pg_stat_activity WHERE datname='browser_reporter' GROUP BY state;"
```

---

## Additional Resources

- [PostgreSQL Performance Tuning Guide](https://wiki.postgresql.org/wiki/Performance_Optimization)
- [pg_stat_statements Documentation](https://www.postgresql.org/docs/15/pgstatstatements.html)
- [SQLAlchemy Async Documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

---

**Last Updated:** 2025-01-17
**Database Version:** PostgreSQL 15
**Optimized For:** 800 users, 160K visits/day, 90-day retention
