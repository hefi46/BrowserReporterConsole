# Performance Optimization Implementation Guide

## Overview
This guide provides step-by-step technical instructions to optimize the Browser Reporter Console performance from 10-15 second load times to sub-second responses for 600+ users with ~18K daily visits.

---

## 🚨 PHASE 1: CRITICAL DATABASE INDEXES (IMMEDIATE IMPACT)
**Expected Improvement: 80-90% query time reduction**

### 1.1 Add visit_time Index (HIGHEST PRIORITY)
**Impact**: Speeds up all date-range filtering operations

```sql
-- Connect to database
docker exec -it $(docker-compose ps -q db) psql -U browser_reporter -d browser_reporter

-- Create index for visit_time DESC (most common query pattern)
CREATE INDEX CONCURRENTLY idx_visits_visit_time_desc ON visits (visit_time DESC);

-- Verify index creation
\di+ idx_visits_visit_time_desc
```

**Testing**:
```sql
-- Before index performance test
EXPLAIN ANALYZE SELECT * FROM visits WHERE visit_time >= NOW() - INTERVAL '7 days' ORDER BY visit_time DESC LIMIT 50;

-- After index - should show "Index Scan" instead of "Seq Scan"
EXPLAIN ANALYZE SELECT * FROM visits WHERE visit_time >= NOW() - INTERVAL '7 days' ORDER BY visit_time DESC LIMIT 50;
```

### 1.2 Add URL Search Index
**Impact**: Eliminates full table scans for URL searches

```sql
-- Create GIN index for text search on URLs
CREATE INDEX CONCURRENTLY idx_visits_url_gin ON visits USING gin (url gin_trgm_ops);

-- Enable trigram extension if not exists
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

**Testing**:
```sql
-- Test URL search performance
EXPLAIN ANALYZE SELECT * FROM visits WHERE url ILIKE '%google%' LIMIT 50;
```

### 1.3 Add Composite Index for User Details
**Impact**: Optimizes user-specific queries with date filtering

```sql
-- Composite index for user_id + visit_time
CREATE INDEX CONCURRENTLY idx_visits_user_time ON visits (user_id, visit_time DESC);
```

### 1.4 Add Combined Filter Index
**Impact**: Optimizes queries with both date and URL filters

```sql
-- Index for common filter combinations
CREATE INDEX CONCURRENTLY idx_visits_time_url ON visits (visit_time DESC, url);
```

### 1.5 Verify All Indexes
```sql
-- Check index usage statistics
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan as index_scans,
    idx_tup_read as tuples_read,
    idx_tup_fetch as tuples_fetched
FROM pg_stat_user_indexes 
WHERE tablename = 'visits'
ORDER BY idx_scan DESC;
```

---

## 🔍 PHASE 2: FULL-TEXT SEARCH OPTIMIZATION
**Expected Improvement: 95% search time reduction**

### 2.1 Add Search Vector Column to Database
```sql
-- Add tsvector column for full-text search
ALTER TABLE visits ADD COLUMN search_vector tsvector;

-- Create function to update search vector
CREATE OR REPLACE FUNCTION update_visit_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', 
        COALESCE(NEW.url, '') || ' ' || 
        COALESCE(NEW.title, '') || ' ' || 
        COALESCE(NEW.computer_name, '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to auto-update search vector
CREATE TRIGGER trigger_update_visit_search_vector
    BEFORE INSERT OR UPDATE ON visits
    FOR EACH ROW EXECUTE FUNCTION update_visit_search_vector();

-- Populate existing records
UPDATE visits SET search_vector = to_tsvector('english', 
    COALESCE(url, '') || ' ' || 
    COALESCE(title, '') || ' ' || 
    COALESCE(computer_name, '')
);

-- Create GIN index on search vector
CREATE INDEX CONCURRENTLY idx_visits_search_vector ON visits USING gin (search_vector);
```

### 2.2 Update Models (backend/models.py)
Add to the Visit model:
```python
# Add this import at the top
from sqlalchemy.dialects.postgresql import TSVECTOR

# Add this field to Visit class after line 36
search_vector = Column(TSVECTOR)
```

### 2.3 Update Search API Endpoint (backend/main.py)
Replace the search query in `/api/reports/search` endpoint (around line 240):

**OLD CODE** (lines 239-254):
```python
query = (
    select(
        Visit.url,
        Visit.title,
        Visit.visit_time,
        Visit.computer_name,
        User.username,
        User.display_name,
        User.email,
        User.homegroup
    )
    .select_from(Visit)
    .join(User, Visit.user_id == User.id)
    .where(Visit.url.ilike(f"%{url}%"))
)
```

**NEW CODE**:
```python
# Import at top of file
from sqlalchemy import func

# Replace the query with full-text search
query = (
    select(
        Visit.url,
        Visit.title,
        Visit.visit_time,
        Visit.computer_name,
        User.username,
        User.display_name,
        User.email,
        User.homegroup,
        func.ts_rank(Visit.search_vector, func.plainto_tsquery('english', url)).label('rank')
    )
    .select_from(Visit)
    .join(User, Visit.user_id == User.id)
    .where(Visit.search_vector.op('@@')(func.plainto_tsquery('english', url)))
    .order_by(text('rank DESC'))
)
```

### 2.4 Update Count Query for Search
Replace count query (around line 263):

**OLD CODE**:
```python
count_query = (
    select(func.count(Visit.id))
    .select_from(Visit)
    .join(User, Visit.user_id == User.id)
    .where(Visit.url.ilike(f"%{url}%"))
)
```

**NEW CODE**:
```python
count_query = (
    select(func.count(Visit.id))
    .select_from(Visit)
    .join(User, Visit.user_id == User.id)
    .where(Visit.search_vector.op('@@')(func.plainto_tsquery('english', url)))
)
```

---

## ⚡ PHASE 3: FRONTEND OPTIMIZATION
**Expected Improvement: Eliminates UI lag, smoves user experience**

### 3.1 Add Search Debouncing (backend/templates/dashboard.html)
Find the website search input around line 2322 and add debouncing:

**Add this JavaScript function** around line 2300:
```javascript
// Debounce function to prevent excessive API calls
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Debounced search function
const debouncedSearch = debounce(async function() {
    const searchTerm = document.getElementById('websiteSearchInput').value.trim();
    if (searchTerm.length >= 2) { // Only search after 2 characters
        searchCurrentPage = 1;
        await executeWebsiteSearch();
    }
}, 300); // 300ms delay
```

**Update search input event listener** (find around line 2322):
Replace:
```javascript
// OLD - immediate search
document.getElementById('websiteSearchInput').addEventListener('input', searchWebsite);
```

With:
```javascript
// NEW - debounced search
document.getElementById('websiteSearchInput').addEventListener('input', debouncedSearch);
```

### 3.2 Add Loading States for Better UX
Find the `filterDisplayedData` function around line 1491 and add loading indicator:

```javascript
function filterDisplayedData() {
    // Show loading indicator
    const loadingDiv = document.createElement('div');
    loadingDiv.id = 'filterLoading';
    loadingDiv.className = 'text-center p-3';
    loadingDiv.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Filtering...';
    
    const container = document.getElementById('userDetailsContent');
    container.insertBefore(loadingDiv, container.firstChild);
    
    // Use setTimeout to allow UI to update
    setTimeout(() => {
        const searchTerm = document.getElementById('websiteFilter').value.toLowerCase().trim();
        
        if (!originalUserDetails) {
            document.getElementById('filterLoading')?.remove();
            return;
        }

        let filteredData = originalUserDetails;
        
        if (searchTerm) {
            filteredData = originalUserDetails.filter(item => 
                (item.url && item.url.toLowerCase().includes(searchTerm)) ||
                (item.title && item.title.toLowerCase().includes(searchTerm))
            );
        }

        updateUserDetailsDisplay(filteredData);
        document.getElementById('filterLoading')?.remove();
    }, 10);
}
```

### 3.3 Optimize DOM Rendering
Add efficient table rendering function (add around line 1400):

```javascript
// Efficient table rendering using DocumentFragment
function renderTableRows(data) {
    const fragment = document.createDocumentFragment();
    
    data.forEach((item, index) => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${index + 1}</td>
            <td><small class="text-muted">${new Date(item.visit_time).toLocaleString()}</small></td>
            <td>
                <div class="url-container">
                    <a href="${item.url}" target="_blank" rel="noopener" class="url-link">${item.url}</a>
                </div>
            </td>
            <td>${item.title || 'N/A'}</td>
            <td><span class="badge bg-info">${item.computer_name || 'Unknown'}</span></td>
        `;
        fragment.appendChild(row);
    });
    
    return fragment;
}
```

---

## 🚀 PHASE 4: ADVANCED QUERY OPTIMIZATION
**Expected Improvement: Combined queries reduce database load by 50%**

### 4.1 Combine Count and Data Queries Using Window Functions
Update the user details endpoint (backend/main.py around line 360):

**Replace the separate count query** with a combined query:

```python
# Replace lines 360-380 with this optimized version
async def reports_user(username: str, request: Request, days: float | None = None, page: int = 1, page_size: int = 50, db: AsyncSession = Depends(get_db)):
    require_login(request)
    
    # Validate pagination parameters
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 1000:
        page_size = 50
    
    # Get user id
    result = await db.execute(select(User.id).where(User.username == username))
    user_id_row = result.scalar_one_or_none()
    if user_id_row is None:
        raise HTTPException(status_code=404, detail="User not found")
    user_id = user_id_row

    # Combined query with window function for count
    base_query = select(Visit).where(Visit.user_id == user_id)
    if days:
        days_float = float(days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
        base_query = base_query.where(Visit.visit_time >= cutoff)
    
    # Use window function to get count and data in single query
    offset = (page - 1) * page_size
    combined_query = select(
        Visit.id,
        Visit.url,
        Visit.title,
        Visit.visit_time,
        Visit.computer_name,
        func.count().over().label('total_count')
    ).where(Visit.user_id == user_id)
    
    if days:
        days_float = float(days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_float)
        combined_query = combined_query.where(Visit.visit_time >= cutoff)
    
    combined_query = combined_query.order_by(Visit.visit_time.desc()).offset(offset).limit(page_size)
    
    result = await db.execute(combined_query)
    rows = result.all()
    
    if not rows:
        total_count = 0
        visits = []
    else:
        total_count = rows[0].total_count
        visits = rows
    
    # Calculate pagination metadata
    total_pages = (total_count + page_size - 1) // page_size
    has_next = page < total_pages
    has_prev = page > 1

    return {
        "data": [
            {
                "id": visit.id,
                "url": visit.url,
                "title": visit.title,
                "visit_time": visit.visit_time.isoformat(),
                "computer_name": visit.computer_name
            }
            for visit in visits
        ],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_count": total_count,
            "has_next": has_next,
            "has_prev": has_prev
        }
    }
```

### 4.2 Add Query Performance Monitoring
Add this monitoring function to backend/main.py:

```python
import time
from functools import wraps

# Add this decorator for monitoring slow queries
def monitor_query_performance(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        result = await func(*args, **kwargs)
        duration = time.time() - start_time
        
        if duration > 1.0:  # Log queries taking more than 1 second
            print(f"SLOW QUERY WARNING: {func.__name__} took {duration:.2f}s")
        
        return result
    return wrapper

# Apply to slow endpoints
@monitor_query_performance
async def reports_user(...):
    # existing code

@monitor_query_performance  
async def search_website(...):
    # existing code
```

---

## 📊 PERFORMANCE TESTING & VERIFICATION

### Test Performance Improvements
Create this test script as `test_performance.py`:

```python
import asyncio
import time
import aiohttp
import statistics

async def test_endpoint_performance(url, iterations=10):
    """Test endpoint performance"""
    times = []
    
    async with aiohttp.ClientSession() as session:
        for i in range(iterations):
            start = time.time()
            async with session.get(url) as response:
                await response.text()
            duration = time.time() - start
            times.append(duration)
            print(f"Request {i+1}: {duration:.2f}s")
    
    print(f"\nResults for {url}:")
    print(f"Average: {statistics.mean(times):.2f}s")
    print(f"Median: {statistics.median(times):.2f}s")
    print(f"Min: {min(times):.2f}s")
    print(f"Max: {max(times):.2f}s")

# Run performance tests
async def main():
    base_url = "http://localhost:8000"
    
    # Test user details endpoint
    await test_endpoint_performance(f"{base_url}/api/reports/user/testuser?days=7&page=1&page_size=50")
    
    # Test search endpoint  
    await test_endpoint_performance(f"{base_url}/api/reports/search?url=google&page=1&page_size=50")

if __name__ == "__main__":
    asyncio.run(main())
```

### Database Performance Monitoring
Monitor index usage:

```sql
-- Check slow queries
SELECT query, calls, total_time, mean_time, rows
FROM pg_stat_statements 
WHERE mean_time > 1000  -- Queries taking more than 1 second
ORDER BY mean_time DESC
LIMIT 10;

-- Check index efficiency
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes 
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;
```

---

## 🎯 IMPLEMENTATION ORDER & EXPECTED RESULTS

### Phase 1 (Critical - Implement First)
- **Time to implement**: 30 minutes
- **Expected improvement**: 80-90% reduction in query time
- **Risk level**: Low (indexes can be dropped if issues occur)

### Phase 2 (High Impact)
- **Time to implement**: 2-3 hours  
- **Expected improvement**: 95% search performance improvement
- **Risk level**: Medium (requires schema changes)

### Phase 3 (User Experience)
- **Time to implement**: 1-2 hours
- **Expected improvement**: Eliminates UI lag and blocking
- **Risk level**: Low (frontend changes only)

### Phase 4 (Advanced)
- **Time to implement**: 3-4 hours
- **Expected improvement**: 50% reduction in database load
- **Risk level**: Medium (requires endpoint modifications)

## 🔄 ROLLBACK PROCEDURES

### If Performance Gets Worse:
```sql
-- Drop indexes if they cause issues
DROP INDEX CONCURRENTLY idx_visits_visit_time_desc;
DROP INDEX CONCURRENTLY idx_visits_url_gin;
DROP INDEX CONCURRENTLY idx_visits_user_time;
DROP INDEX CONCURRENTLY idx_visits_time_url;
DROP INDEX CONCURRENTLY idx_visits_search_vector;
```

### If Full-Text Search Causes Issues:
```sql
-- Remove search vector column
ALTER TABLE visits DROP COLUMN search_vector;
DROP TRIGGER trigger_update_visit_search_vector ON visits;
DROP FUNCTION update_visit_search_vector();
```

---

**Target Result**: Sub-second response times for all operations with 600+ users and 18K+ daily visits.