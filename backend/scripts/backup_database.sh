#!/bin/bash
################################################################################
# Database Backup Script for BrowserGuardianConsole
#
# This script creates daily backups of the PostgreSQL database
# Keeps backups for 7 days by default
#
# Usage:
#   ./backup_database.sh [backup_directory] [retention_days]
#
# Example:
#   ./backup_database.sh /backups/postgres 7
################################################################################

set -e  # Exit on error
set -u  # Exit on undefined variable

# Configuration
BACKUP_DIR="${1:-/backups/postgres}"
RETENTION_DAYS="${2:-7}"
DATE=$(date +%Y%m%d_%H%M%S)
CONTAINER_NAME="browserguardianconsole-db-1"
DB_NAME="browser_guardian"
DB_USER="browser_guardian"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Verify Docker container is running
check_container() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_error "PostgreSQL container '${CONTAINER_NAME}' is not running"
        log_info "Start the container with: docker compose up -d"
        exit 1
    fi
    log_info "PostgreSQL container is running"
}

# Create backup directory if it doesn't exist
create_backup_dir() {
    if [ ! -d "$BACKUP_DIR" ]; then
        log_info "Creating backup directory: $BACKUP_DIR"
        mkdir -p "$BACKUP_DIR"
    fi
}

# Perform the database backup
perform_backup() {
    local backup_file="$BACKUP_DIR/backup_${DATE}.sql"
    local compressed_file="${backup_file}.gz"

    log_info "Starting database backup..."
    log_info "Database: $DB_NAME"
    log_info "Output: $compressed_file"

    # Execute pg_dump via docker exec and compress on the fly
    if docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$compressed_file"; then
        local file_size=$(du -h "$compressed_file" | cut -f1)
        log_info "✓ Backup completed successfully"
        log_info "  File: $(basename $compressed_file)"
        log_info "  Size: $file_size"
        return 0
    else
        log_error "Backup failed!"
        return 1
    fi
}

# Clean up old backups
cleanup_old_backups() {
    log_info "Cleaning up backups older than ${RETENTION_DAYS} days..."

    local deleted_count=0
    while IFS= read -r -d '' file; do
        log_info "  Deleting: $(basename "$file")"
        rm -f "$file"
        ((deleted_count++))
    done < <(find "$BACKUP_DIR" -name "backup_*.sql.gz" -type f -mtime +${RETENTION_DAYS} -print0)

    if [ $deleted_count -gt 0 ]; then
        log_info "✓ Deleted $deleted_count old backup(s)"
    else
        log_info "No old backups to delete"
    fi
}

# List current backups
list_backups() {
    log_info "Current backups in $BACKUP_DIR:"

    if ls "$BACKUP_DIR"/backup_*.sql.gz 1> /dev/null 2>&1; then
        echo ""
        printf "%-40s %10s %20s\n" "Filename" "Size" "Date"
        echo "------------------------------------------------------------------------"

        for file in "$BACKUP_DIR"/backup_*.sql.gz; do
            if [ -f "$file" ]; then
                local filename=$(basename "$file")
                local filesize=$(du -h "$file" | cut -f1)
                local filedate=$(stat -c %y "$file" | cut -d'.' -f1)
                printf "%-40s %10s %20s\n" "$filename" "$filesize" "$filedate"
            fi
        done
        echo ""
    else
        log_warn "  No backups found"
    fi
}

# Verify backup integrity
verify_backup() {
    local latest_backup=$(ls -t "$BACKUP_DIR"/backup_*.sql.gz 2>/dev/null | head -n1)

    if [ -z "$latest_backup" ]; then
        log_warn "No backup file found to verify"
        return 1
    fi

    log_info "Verifying backup integrity..."

    if gzip -t "$latest_backup" 2>/dev/null; then
        log_info "✓ Backup file is valid (gzip test passed)"
        return 0
    else
        log_error "Backup file is corrupted!"
        return 1
    fi
}

# Calculate total backup size
calculate_total_size() {
    if ls "$BACKUP_DIR"/backup_*.sql.gz 1> /dev/null 2>&1; then
        local total_size=$(du -sh "$BACKUP_DIR" | cut -f1)
        log_info "Total backup storage used: $total_size"
    fi
}

# Main execution
main() {
    echo "================================================================================"
    echo " BrowserGuardianConsole Database Backup"
    echo " $(date '+%Y-%m-%d %H:%M:%S')"
    echo "================================================================================"
    echo ""

    # Pre-flight checks
    check_container
    create_backup_dir

    # Perform backup
    if perform_backup; then
        echo ""
        verify_backup
        echo ""
        cleanup_old_backups
        echo ""
        list_backups
        calculate_total_size
        echo ""
        log_info "Backup process completed successfully!"
        echo "================================================================================"
        exit 0
    else
        log_error "Backup process failed!"
        echo "================================================================================"
        exit 1
    fi
}

# Handle script arguments
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    echo "Usage: $0 [backup_directory] [retention_days]"
    echo ""
    echo "Arguments:"
    echo "  backup_directory   Directory to store backups (default: /backups/postgres)"
    echo "  retention_days     Number of days to keep backups (default: 7)"
    echo ""
    echo "Examples:"
    echo "  $0                           # Use defaults"
    echo "  $0 /my/backup/path           # Custom directory"
    echo "  $0 /my/backup/path 14        # Custom directory and retention"
    echo ""
    echo "Restore a backup:"
    echo "  gunzip -c /backups/postgres/backup_YYYYMMDD_HHMMSS.sql.gz | \\"
    echo "    docker exec -i browserguardianconsole-db-1 psql -U browser_guardian -d browser_guardian"
    exit 0
fi

# Run main function
main
