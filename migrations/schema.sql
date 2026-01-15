-- Audio Processing Pipeline - Database Schema
--
-- Run this on the coordinator VM:
--   sudo -u postgres psql -d audio_pipeline -f schema.sql
--
-- Or with custom database:
--   psql -h 10.0.0.1 -U pipeline -d audio_pipeline -f schema.sql

-- Drop existing tables if recreating (comment out in production)
-- DROP TABLE IF EXISTS classifications CASCADE;
-- DROP TABLE IF EXISTS transcripts CASCADE;
-- DROP TABLE IF EXISTS audio_files CASCADE;
-- DROP VIEW IF EXISTS ra_queue;

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
    status TEXT DEFAULT 'pending'  -- pending, transcribed, flagged, reviewed, failed
);

-- Migration: Add s3_opus_path column if upgrading from previous schema
-- ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS s3_opus_path TEXT;

-- Parquet metadata columns (for TikTok metadata from archives)
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS parquet_lang TEXT;
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS parquet_country TEXT;
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS parquet_metadata JSONB;

-- Transcript storage
CREATE TABLE IF NOT EXISTS transcripts (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER REFERENCES audio_files(id) ON DELETE CASCADE,
    transcript_text TEXT,
    language TEXT,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Classification results
CREATE TABLE IF NOT EXISTS classifications (
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

-- Flagged items for RA queue - partial index for efficiency
CREATE INDEX IF NOT EXISTS idx_classifications_flagged ON classifications(flagged, created_at DESC)
    WHERE flagged = true;

-- Audio file ID lookups for transcripts and classifications
CREATE INDEX IF NOT EXISTS idx_transcripts_audio_id ON transcripts(audio_file_id);
CREATE INDEX IF NOT EXISTS idx_classifications_audio_id ON classifications(audio_file_id);

-- Parquet metadata indexes
CREATE INDEX IF NOT EXISTS idx_audio_parquet_lang ON audio_files(parquet_lang);
CREATE INDEX IF NOT EXISTS idx_audio_parquet_country ON audio_files(parquet_country);
CREATE INDEX IF NOT EXISTS idx_audio_parquet_metadata ON audio_files USING GIN (parquet_metadata);

-- RA queue view: flagged items from last 24 hours
-- Used by the reporting app for research assistant review
CREATE OR REPLACE VIEW ra_queue AS
SELECT
    af.id,
    af.original_filename,
    af.opus_path,
    af.s3_opus_path,
    af.parquet_lang,
    af.parquet_country,
    af.parquet_metadata,
    t.transcript_text,
    c.flag_score,
    c.flag_category,
    af.created_at
FROM audio_files af
JOIN transcripts t ON t.audio_file_id = af.id
JOIN classifications c ON c.audio_file_id = af.id
WHERE c.flagged = true
  AND af.status = 'flagged'
  AND af.created_at > NOW() - INTERVAL '24 hours'
ORDER BY c.flag_score DESC;

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

-- Helper function to check if we have enough flagged content for RA window
CREATE OR REPLACE FUNCTION check_ra_target(target_count INTEGER DEFAULT 200)
RETURNS TABLE (
    current_count BIGINT,
    target INTEGER,
    target_met BOOLEAN
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*) as current_count,
        target_count as target,
        COUNT(*) >= target_count as target_met
    FROM ra_queue;
END;
$$ LANGUAGE plpgsql;
