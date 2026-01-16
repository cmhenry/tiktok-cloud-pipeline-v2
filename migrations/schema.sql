-- Audio Processing Pipeline - Database Schema
--
-- This schema is for STANDALONE pipeline deployment.
-- For integration with existing RA App database, use 003_ra_app_integration.sql instead.
--
-- Run this on a fresh database:
--   sudo -u postgres psql -d audio_pipeline -f schema.sql
--
-- Or with custom database:
--   psql -h 10.0.0.1 -U pipeline -d audio_pipeline -f schema.sql

-- Drop existing tables if recreating (comment out in production)
-- DROP TABLE IF EXISTS pipeline_classifications CASCADE;
-- DROP TABLE IF EXISTS pipeline_transcripts CASCADE;
-- DROP TABLE IF EXISTS audio_files CASCADE;
-- DROP VIEW IF EXISTS pipeline_flagged_queue;

-- Main audio files table
CREATE TABLE IF NOT EXISTS audio_files (
    id SERIAL PRIMARY KEY,
    original_filename TEXT NOT NULL,
    opus_path TEXT NOT NULL UNIQUE,
    s3_opus_path TEXT,                -- S3 key for long-term storage (processed/{date}/{id}.opus)
    archive_source TEXT,
    duration_seconds FLOAT,
    file_size_bytes INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP,
    status TEXT DEFAULT 'pending',    -- pending, transcribed, flagged, synced, failed
    -- Parquet metadata columns (for TikTok metadata from archives)
    parquet_lang TEXT,
    parquet_country TEXT,
    parquet_metadata JSONB,
    -- Sync tracking for RA App integration
    synced_to_ra BOOLEAN DEFAULT FALSE,
    synced_at TIMESTAMP
);

-- Pipeline transcript storage (renamed to avoid conflict with RA App)
CREATE TABLE IF NOT EXISTS pipeline_transcripts (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER REFERENCES audio_files(id) ON DELETE CASCADE,
    transcript_text TEXT,
    language TEXT,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Pipeline classification results (renamed to avoid conflict with RA App)
CREATE TABLE IF NOT EXISTS pipeline_classifications (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER REFERENCES audio_files(id) ON DELETE CASCADE,
    flagged BOOLEAN NOT NULL,
    flag_score FLOAT,
    flag_category TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for common queries
-- Status + created_at for processing queue queries
CREATE INDEX IF NOT EXISTS idx_audio_status_created ON audio_files(status, created_at DESC);

-- Archive source for tracking which archives have been processed
CREATE INDEX IF NOT EXISTS idx_audio_archive ON audio_files(archive_source);

-- Sync tracking - find unsynced flagged items
CREATE INDEX IF NOT EXISTS idx_audio_synced ON audio_files(synced_to_ra) WHERE synced_to_ra = FALSE;

-- Flagged items for pipeline queue - partial index for efficiency
CREATE INDEX IF NOT EXISTS idx_pipeline_classifications_flagged ON pipeline_classifications(flagged, flag_score DESC)
    WHERE flagged = true;

-- Audio file ID lookups for transcripts and classifications
CREATE INDEX IF NOT EXISTS idx_pipeline_transcripts_audio_id ON pipeline_transcripts(audio_file_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_classifications_audio_id ON pipeline_classifications(audio_file_id);

-- Parquet metadata indexes
CREATE INDEX IF NOT EXISTS idx_audio_parquet_lang ON audio_files(parquet_lang);
CREATE INDEX IF NOT EXISTS idx_audio_parquet_country ON audio_files(parquet_country);
CREATE INDEX IF NOT EXISTS idx_audio_parquet_metadata ON audio_files USING GIN (parquet_metadata);

-- Pipeline flagged queue view: flagged items from last 24 hours (for pipeline monitoring)
CREATE OR REPLACE VIEW pipeline_flagged_queue AS
SELECT
    af.id,
    af.original_filename,
    af.opus_path,
    af.s3_opus_path,
    af.parquet_lang,
    af.parquet_country,
    af.parquet_metadata,
    af.parquet_metadata->>'meta_id' AS meta_id,
    pt.transcript_text,
    pc.flag_score,
    pc.flag_category,
    af.synced_to_ra,
    af.created_at
FROM audio_files af
JOIN pipeline_transcripts pt ON pt.audio_file_id = af.id
JOIN pipeline_classifications pc ON pc.audio_file_id = af.id
WHERE pc.flagged = true
  AND af.status = 'flagged'
  AND af.created_at > NOW() - INTERVAL '24 hours'
ORDER BY pc.flag_score DESC;

-- Daily stats view for monitoring
CREATE OR REPLACE VIEW daily_stats AS
SELECT
    DATE(created_at) as date,
    status,
    COUNT(*) as count
FROM audio_files
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY DATE(created_at), status
ORDER BY date DESC, status;

-- Helper function to check pipeline processing stats
CREATE OR REPLACE FUNCTION check_pipeline_stats()
RETURNS TABLE (
    total_processed BIGINT,
    flagged_count BIGINT,
    synced_count BIGINT,
    pending_sync BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*) as total_processed,
        COUNT(*) FILTER (WHERE status = 'flagged') as flagged_count,
        COUNT(*) FILTER (WHERE synced_to_ra = TRUE) as synced_count,
        COUNT(*) FILTER (WHERE status = 'flagged' AND synced_to_ra = FALSE) as pending_sync
    FROM audio_files
    WHERE created_at > NOW() - INTERVAL '24 hours';
END;
$$ LANGUAGE plpgsql;
