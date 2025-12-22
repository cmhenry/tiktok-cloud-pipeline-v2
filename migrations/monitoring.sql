-- Audio Pipeline Monitoring Queries
-- Run these on the coordinator Postgres to check pipeline health

-- =============================================================================
-- DAILY HEALTH CHECK (run before noon to ensure RA has data)
-- =============================================================================

-- 1. Flagged content count (should be ≥200 by noon)
SELECT
    COUNT(*) as flagged_count,
    MIN(af.created_at) as earliest,
    MAX(af.created_at) as latest
FROM audio_files af
JOIN classifications c ON c.audio_file_id = af.id
WHERE c.flagged = true
  AND af.created_at > NOW() - INTERVAL '24 hours';

-- 2. Processing totals by status
SELECT
    status,
    COUNT(*) as count
FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY status
ORDER BY count DESC;

-- 3. Processing rate (files per hour)
SELECT
    DATE_TRUNC('hour', created_at) as hour,
    COUNT(*) as processed
FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY DATE_TRUNC('hour', created_at)
ORDER BY hour DESC
LIMIT 12;

-- =============================================================================
-- RA QUEUE STATUS
-- =============================================================================

-- 4. Items ready for RA review
SELECT COUNT(*) as ra_queue_size
FROM ra_queue;

-- 5. Top flagged categories
SELECT
    c.flag_category,
    COUNT(*) as count,
    ROUND(AVG(c.flag_score)::numeric, 3) as avg_score
FROM audio_files af
JOIN classifications c ON c.audio_file_id = af.id
WHERE c.flagged = true
  AND af.created_at > NOW() - INTERVAL '24 hours'
GROUP BY c.flag_category
ORDER BY count DESC;

-- =============================================================================
-- ERROR TRACKING
-- =============================================================================

-- 6. Failed files count
SELECT COUNT(*) as failed_count
FROM audio_files
WHERE status = 'failed'
  AND created_at > NOW() - INTERVAL '24 hours';

-- 7. Recent failures (for debugging)
SELECT
    id,
    original_filename,
    archive_source,
    created_at
FROM audio_files
WHERE status = 'failed'
  AND created_at > NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC
LIMIT 20;

-- =============================================================================
-- ARCHIVE PROCESSING
-- =============================================================================

-- 8. Archives processed today (by source)
SELECT
    archive_source,
    COUNT(*) as file_count
FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY archive_source
ORDER BY file_count DESC
LIMIT 20;

-- =============================================================================
-- STORAGE/CAPACITY
-- =============================================================================

-- 9. Data volume today
SELECT
    SUM(file_size_bytes) / (1024.0 * 1024 * 1024) as total_gb,
    AVG(file_size_bytes) / 1024.0 as avg_kb,
    AVG(duration_seconds) as avg_duration_sec
FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours';

-- 10. Total database size
SELECT
    pg_size_pretty(pg_database_size('audio_pipeline')) as db_size;

-- =============================================================================
-- TRANSCRIPT STATS
-- =============================================================================

-- 11. Language distribution
SELECT
    t.language,
    COUNT(*) as count
FROM transcripts t
JOIN audio_files af ON af.id = t.audio_file_id
WHERE af.created_at > NOW() - INTERVAL '24 hours'
GROUP BY t.language
ORDER BY count DESC;

-- 12. Average transcript length
SELECT
    AVG(LENGTH(transcript_text)) as avg_chars,
    MAX(LENGTH(transcript_text)) as max_chars
FROM transcripts t
JOIN audio_files af ON af.id = t.audio_file_id
WHERE af.created_at > NOW() - INTERVAL '24 hours';
