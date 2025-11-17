#!/bin/bash
################################################################################
# Database Maintenance Script for BrowserReporterConsole
#
# Performs routine database maintenance tasks:
# - VACUUM: Reclaims storage space from deleted records
# - ANALYZE: Updates table statistics for query optimization
# - REINDEX: Rebuilds indexes if they become bloated
# - Checks for dead tuples and bloat
#
# Usage:
#   ./maintenance.sh [--vacuum-full] [--reindex]
#
# Options:
#   --vacuum-full    Perform VACUUM FULL (locks table, use during maintenance window)
#   --reindex        Rebuild all indexes (slow, use if indexes are bloated)
#   --analyze-only   Only update statistics, skip vacuum
################################################################################

set -e
set -u

# Configuration
CONTAINER_NAME="browserreporterconsole-db-1"
DB_NAME="browser_reporter"
DB_USER="browser_reporter"

# Parse command line arguments
VACUUM_FULL=false
REINDEX=false
ANALYZE_ONLY=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --vacuum-full)
            VACUUM_FULL=true
            shift
            ;;
        --reindex)
            REINDEX=true
            shift
            ;;
        --analyze-only)
            ANALYZE_ONLY=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --vacuum-full    Perform VACUUM FULL (reclaims maximum space, locks tables)"
            echo "  --reindex        Rebuild indexes (slow, use if indexes are bloated)"
            echo "  --analyze-only   Only update statistics, skip vacuum"
            echo ""
            echo "Examples:"
            echo "  $0                      # Standard maintenance (VACUUM + ANALYZE)"
            echo "  $0 --analyze-only       # Quick statistics update"
            echo "  $0 --vacuum-full        # Deep clean (requires maintenance window)"
            echo "  $0 --vacuum-full --reindex  # Full maintenance (very slow)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Execute SQL command
exec_sql() {
    local sql="$1"
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "$sql"
}

# Execute SQL and return output
exec_sql_quiet() {
    local sql="$1"
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "$sql"
}

# Check if container is running
check_container() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_error "PostgreSQL container '${CONTAINER_NAME}' is not running"
        exit 1
    fi
    log_info "PostgreSQL container is running"
}

# Show database statistics before maintenance
show_pre_maintenance_stats() {
    log_step "Database statistics BEFORE maintenance"

    echo ""
    log_info "Table sizes:"
    exec_sql "
        SELECT
            tablename,
            pg_size_pretty(pg_total_relation_size(tablename::text)) as total_size,
            pg_size_pretty(pg_relation_size(tablename::text)) as table_size,
            pg_size_pretty(pg_total_relation_size(tablename::text) - pg_relation_size(tablename::text)) as index_size
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY pg_total_relation_size(tablename::text) DESC;
    "

    echo ""
    log_info "Dead tuples (indicates vacuum needed):"
    exec_sql "
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
    "
}

# Analyze tables to update statistics
analyze_tables() {
    log_step "Running ANALYZE to update table statistics..."

    local tables=("users" "visits" "dashboard_users")

    # Check if archive table exists
    if exec_sql_quiet "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'visits_archive');" | grep -q "t"; then
        tables+=("visits_archive")
    fi

    for table in "${tables[@]}"; do
        log_info "Analyzing table: $table"
        if exec_sql "ANALYZE $table;" > /dev/null 2>&1; then
            log_info "  ✓ $table analyzed"
        else
            log_warn "  ✗ $table analysis failed"
        fi
    done

    log_info "✓ ANALYZE completed"
}

# Vacuum tables to reclaim space
vacuum_tables() {
    if [ "$ANALYZE_ONLY" = true ]; then
        log_info "Skipping VACUUM (--analyze-only specified)"
        return
    fi

    if [ "$VACUUM_FULL" = true ]; then
        log_step "Running VACUUM FULL (this will lock tables)..."
        log_warn "Tables will be locked during VACUUM FULL - avoid during business hours"
        sleep 2
    else
        log_step "Running VACUUM to reclaim space..."
    fi

    local tables=("users" "visits" "dashboard_users")

    # Check if archive table exists
    if exec_sql_quiet "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'visits_archive');" | grep -q "t"; then
        tables+=("visits_archive")
    fi

    for table in "${tables[@]}"; do
        if [ "$VACUUM_FULL" = true ]; then
            log_info "VACUUM FULL on $table (this may take a while)..."
            if exec_sql "VACUUM FULL $table;" > /dev/null 2>&1; then
                log_info "  ✓ $table vacuum full completed"
            else
                log_warn "  ✗ $table vacuum full failed"
            fi
        else
            log_info "Vacuuming table: $table"
            if exec_sql "VACUUM $table;" > /dev/null 2>&1; then
                log_info "  ✓ $table vacuumed"
            else
                log_warn "  ✗ $table vacuum failed"
            fi
        fi
    done

    log_info "✓ VACUUM completed"
}

# Reindex tables if requested
reindex_tables() {
    if [ "$REINDEX" = false ]; then
        return
    fi

    log_step "Rebuilding indexes (REINDEX)..."
    log_warn "This may take a long time for large tables"

    local tables=("users" "visits" "dashboard_users")

    # Check if archive table exists
    if exec_sql_quiet "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'visits_archive');" | grep -q "t"; then
        tables+=("visits_archive")
    fi

    for table in "${tables[@]}"; do
        log_info "Reindexing table: $table"
        if exec_sql "REINDEX TABLE CONCURRENTLY $table;" > /dev/null 2>&1; then
            log_info "  ✓ $table reindexed"
        else
            # CONCURRENTLY might fail on some PostgreSQL versions, try without
            log_warn "  CONCURRENTLY failed, trying regular REINDEX..."
            if exec_sql "REINDEX TABLE $table;" > /dev/null 2>&1; then
                log_info "  ✓ $table reindexed"
            else
                log_warn "  ✗ $table reindex failed"
            fi
        fi
    done

    log_info "✓ REINDEX completed"
}

# Check for index bloat
check_index_bloat() {
    log_step "Checking for index bloat..."

    echo ""
    log_info "Index bloat estimation (>20% may indicate need for REINDEX):"

    exec_sql "
        SELECT
            schemaname,
            tablename,
            indexname,
            pg_size_pretty(pg_relation_size(indexrelid)) as index_size,
            idx_scan as times_used,
            CASE
                WHEN idx_scan = 0 THEN 'UNUSED'
                WHEN idx_scan < 100 THEN 'LOW USAGE'
                ELSE 'OK'
            END as usage_status
        FROM pg_stat_user_indexes
        WHERE schemaname = 'public'
        ORDER BY pg_relation_size(indexrelid) DESC
        LIMIT 20;
    "

    echo ""
}

# Show database statistics after maintenance
show_post_maintenance_stats() {
    log_step "Database statistics AFTER maintenance"

    echo ""
    log_info "Database total size:"
    exec_sql "SELECT pg_size_pretty(pg_database_size('$DB_NAME')) as database_size;"

    echo ""
    log_info "Table sizes:"
    exec_sql "
        SELECT
            tablename,
            pg_size_pretty(pg_total_relation_size(tablename::text)) as total_size,
            pg_size_pretty(pg_relation_size(tablename::text)) as table_size,
            pg_size_pretty(pg_total_relation_size(tablename::text) - pg_relation_size(tablename::text)) as index_size
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY pg_total_relation_size(tablename::text) DESC;
    "

    echo ""
    log_info "Dead tuples after maintenance:"
    exec_sql "
        SELECT
            schemaname,
            tablename,
            n_live_tup as live_rows,
            n_dead_tup as dead_rows,
            ROUND(100 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) as dead_percentage
        FROM pg_stat_user_tables
        WHERE schemaname = 'public'
        ORDER BY n_dead_tup DESC;
    "
}

# Main execution
main() {
    echo "================================================================================"
    echo " BrowserReporterConsole Database Maintenance"
    echo " $(date '+%Y-%m-%d %H:%M:%S')"
    echo "================================================================================"
    echo ""

    if [ "$VACUUM_FULL" = true ]; then
        log_warn "Running with VACUUM FULL - tables will be locked"
    fi
    if [ "$REINDEX" = true ]; then
        log_warn "Running with REINDEX - this will take time"
    fi
    if [ "$ANALYZE_ONLY" = true ]; then
        log_info "Running in analyze-only mode"
    fi

    echo ""

    # Pre-flight checks
    check_container

    # Show before stats
    show_pre_maintenance_stats

    echo ""
    echo "================================================================================"
    echo " MAINTENANCE TASKS"
    echo "================================================================================"
    echo ""

    # Record start time
    start_time=$(date +%s)

    # Run maintenance tasks
    analyze_tables
    echo ""

    vacuum_tables
    echo ""

    reindex_tables
    echo ""

    check_index_bloat

    # Show after stats
    echo ""
    echo "================================================================================"
    echo " POST-MAINTENANCE STATISTICS"
    echo "================================================================================"
    echo ""

    show_post_maintenance_stats

    # Calculate duration
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    minutes=$((duration / 60))
    seconds=$((duration % 60))

    echo ""
    echo "================================================================================"
    log_info "Maintenance completed in ${minutes}m ${seconds}s"
    echo "================================================================================"
}

# Run main function
main
