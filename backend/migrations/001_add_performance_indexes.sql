-- Migration: Add Critical Performance Indexes
-- Purpose: Optimize queries for 800+ users with high volume visit data
-- Expected improvement: 80-90% reduction in query time
-- Run with: CONCURRENTLY to avoid table locking

-- ============================================================================
-- VISITS TABLE INDEXES
-- ============================================================================

-- Index 1: Composite index for user analytics queries
-- Used by: /api/reports/all, /api/reports/user/{username}
-- Supports: Filtering by user_id and sorting by visit_time DESC
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_visits_user_time
ON visits (user_id, visit_time DESC);

-- Index 2: Time-based index for date range filtering
-- Used by: All endpoints with date filtering
-- Supports: WHERE visit_time > ? ORDER BY visit_time DESC
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_visits_time_desc
ON visits (visit_time DESC);

-- Index 3: Full-text search index
-- Used by: /api/reports/search
-- Supports: Full-text search on URL and title content
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_visits_search_vector
ON visits USING gin (search_vector);

-- Index 4: URL pattern search index
-- Used by: /api/reports/search (fallback when search_vector is empty)
-- Supports: ILIKE queries on URL field with trigram matching
-- Note: Requires pg_trgm extension
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_visits_url_gin
ON visits USING gin (url gin_trgm_ops);

-- Index 5: Title pattern search index
-- Used by: Search queries on title field
-- Supports: ILIKE queries on title field
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_visits_title_gin
ON visits USING gin (title gin_trgm_ops);

-- ============================================================================
-- USERS TABLE INDEXES
-- ============================================================================

-- Index 6: Homegroup filtering index (if not already present)
-- Used by: /api/reports/all with homegroup filter
-- Supports: WHERE homegroup = ?
-- Note: This may already exist from models.py, but we ensure it's here
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_homegroup
ON users (homegroup) WHERE homegroup IS NOT NULL;

-- Index 7: Last seen optimization
-- Used by: Active user reports
-- Supports: Sorting and filtering by last_seen_at
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_last_seen
ON users (last_seen_at DESC) WHERE last_seen_at IS NOT NULL;

-- ============================================================================
-- COMPOSITE INDEXES FOR COMMON QUERY PATTERNS
-- ============================================================================

-- Index 8: Combined time + URL for search result sorting
-- Used by: Search queries that need to sort by relevance and time
-- Supports: WHERE visit_time > ? ORDER BY visit_time DESC with URL filtering
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_visits_time_url
ON visits (visit_time DESC, url) WHERE url IS NOT NULL;

-- ============================================================================
-- VERIFY INDEX CREATION
-- ============================================================================

-- Query to verify all indexes were created successfully
-- Run this manually after migration to confirm:
--
-- SELECT
--     schemaname,
--     tablename,
--     indexname,
--     indexdef
-- FROM pg_indexes
-- WHERE schemaname = 'public'
--   AND tablename IN ('visits', 'users')
--   AND indexname LIKE 'idx_%'
-- ORDER BY tablename, indexname;
