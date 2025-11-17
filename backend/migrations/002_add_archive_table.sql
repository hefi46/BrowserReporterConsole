-- Migration: Create Archive Table for Historical Data
-- Purpose: Implement 90-day data retention policy
-- Expected improvement: Maintain < 15GB active database size

-- ============================================================================
-- ARCHIVE TABLE CREATION
-- ============================================================================

-- Create archive table with identical structure to visits table
CREATE TABLE IF NOT EXISTS visits_archive (
    id BIGINT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    computer_name VARCHAR,
    url TEXT,
    title TEXT,
    visit_time TIMESTAMP WITH TIME ZONE NOT NULL,
    inserted_at TIMESTAMP WITH TIME ZONE,
    search_vector TSVECTOR,
    archived_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Add comment for documentation
COMMENT ON TABLE visits_archive IS 'Historical visits older than 90 days, kept for compliance/auditing purposes';

-- ============================================================================
-- MINIMAL INDEXES FOR ARCHIVE TABLE
-- ============================================================================

-- Primary key index (already created by PRIMARY KEY constraint)
-- Only add essential indexes for occasional historical queries

-- Index for time-based queries (when did this URL get archived?)
CREATE INDEX IF NOT EXISTS idx_visits_archive_time
ON visits_archive (visit_time DESC);

-- Index for user-based historical lookups
CREATE INDEX IF NOT EXISTS idx_visits_archive_user
ON visits_archive (user_id);

-- Index for archived_at (useful for archival management)
CREATE INDEX IF NOT EXISTS idx_visits_archive_archived_at
ON visits_archive (archived_at DESC);

-- ============================================================================
-- STORAGE OPTIMIZATION
-- ============================================================================

-- Set storage type for large text fields to use TOAST compression
ALTER TABLE visits_archive
    ALTER COLUMN url SET STORAGE EXTERNAL,
    ALTER COLUMN title SET STORAGE EXTERNAL;

-- ============================================================================
-- VERIFY ARCHIVE TABLE
-- ============================================================================

-- Query to verify archive table structure:
--
-- SELECT
--     column_name,
--     data_type,
--     is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'visits_archive'
-- ORDER BY ordinal_position;
