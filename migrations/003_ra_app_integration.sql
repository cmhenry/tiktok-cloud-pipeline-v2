-- Migration: Add pipeline tables to existing RA App database
--
-- This migration adds the audio processing pipeline tables to an existing
-- RA App database WITHOUT modifying any RA App tables (transcripts, users, etc.)
--
-- Safe to run on existing RA App DB - creates new tables only.
-- Idempotent - can be run multiple times safely.
--
-- Usage:
--   psql -h 10.0.0.1 -U transcript_user -d ra_app -f 003_ra_app_integration.sql

-- ============================================================================
-- PIPELINE TABLES
-- ============================================================================

-- Pipeline tracking table (main table for processed audio files)
CREATE TABLE IF NOT EXISTS audio_files (
    id SERIAL PRIMARY KEY,
    original_filename TEXT NOT NULL,
    opus_path TEXT NOT NULL UNIQUE,
    s3_opus_path TEXT,                -- S3 key for long-term storage
    archive_source TEXT,              -- batch_id from source archive
    duration_seconds FLOAT,
    file_size_bytes INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP,
    status TEXT DEFAULT 'pending',    -- pending, transcribed, flagged, synced, failed
    -- Parquet metadata (from TikTok archives)
    parquet_lang TEXT,
    parquet_country TEXT,
    parquet_metadata JSONB,
    -- Sync tracking for RA App integration
    synced_to_ra BOOLEAN DEFAULT FALSE,
    synced_at TIMESTAMP
);

-- Pipeline transcripts (separate from RA App's transcripts table)
CREATE TABLE IF NOT EXISTS pipeline_transcripts (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER REFERENCES audio_files(id) ON DELETE CASCADE,
    transcript_text TEXT,
    language TEXT,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Pipeline classifications (CoPE-A model output)
CREATE TABLE IF NOT EXISTS pipeline_classifications (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER REFERENCES audio_files(id) ON DELETE CASCADE,
    flagged BOOLEAN NOT NULL,
    flag_score FLOAT,
    flag_category TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Status + created_at for processing queue queries
CREATE INDEX IF NOT EXISTS idx_audio_status_created ON audio_files(status, created_at DESC);

-- Archive source for tracking which archives have been processed
CREATE INDEX IF NOT EXISTS idx_audio_archive ON audio_files(archive_source);

-- Sync tracking - efficiently find unsynced flagged items
CREATE INDEX IF NOT EXISTS idx_audio_synced ON audio_files(synced_to_ra) WHERE synced_to_ra = FALSE;

-- Parquet metadata indexes for filtering
CREATE INDEX IF NOT EXISTS idx_audio_parquet_lang ON audio_files(parquet_lang);
CREATE INDEX IF NOT EXISTS idx_audio_parquet_country ON audio_files(parquet_country);
CREATE INDEX IF NOT EXISTS idx_audio_parquet_metadata ON audio_files USING GIN (parquet_metadata);

-- Pipeline table indexes
CREATE INDEX IF NOT EXISTS idx_pipeline_transcripts_audio_id ON pipeline_transcripts(audio_file_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_classifications_audio_id ON pipeline_classifications(audio_file_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_classifications_flagged ON pipeline_classifications(flagged, flag_score DESC)
    WHERE flagged = true;

-- ============================================================================
-- SYNC VIEW - Items ready for sync to RA App
-- ============================================================================

CREATE OR REPLACE VIEW pipeline_sync_candidates AS
SELECT
    af.id AS audio_file_id,
    af.parquet_metadata->>'meta_id' AS meta_id,
    pt.transcript_text AS transcript,
    pc.flag_score AS label_1_pred,
    af.parquet_country AS country,
    af.parquet_lang AS lang,
    af.parquet_metadata->>'author_uniqueid' AS author_uniqueid,
    EXTRACT(YEAR FROM af.created_at)::INTEGER AS year,
    EXTRACT(MONTH FROM af.created_at)::INTEGER AS month,
    EXTRACT(DAY FROM af.created_at)::INTEGER AS day,
    af.created_at
FROM audio_files af
JOIN pipeline_transcripts pt ON pt.audio_file_id = af.id
JOIN pipeline_classifications pc ON pc.audio_file_id = af.id
WHERE pc.flagged = TRUE
  AND af.synced_to_ra = FALSE
  AND af.parquet_metadata->>'meta_id' IS NOT NULL;

-- ============================================================================
-- SYNC FUNCTION - Sync flagged items to RA App transcripts table
-- ============================================================================

CREATE OR REPLACE FUNCTION sync_pipeline_to_ra(
    score_threshold NUMERIC DEFAULT 0.7,
    batch_limit INTEGER DEFAULT 100
)
RETURNS TABLE (
    synced_count INTEGER,
    skipped_existing INTEGER,
    errors INTEGER
) AS $$
DECLARE
    v_synced INTEGER := 0;
    v_skipped INTEGER := 0;
    v_errors INTEGER := 0;
    v_candidate RECORD;
    v_creator_id INTEGER;
BEGIN
    FOR v_candidate IN
        SELECT * FROM pipeline_sync_candidates
        WHERE label_1_pred >= score_threshold
        ORDER BY label_1_pred DESC
        LIMIT batch_limit
    LOOP
        BEGIN
            -- Skip if meta_id already exists in RA App transcripts
            IF EXISTS (SELECT 1 FROM transcripts WHERE meta_id = v_candidate.meta_id) THEN
                v_skipped := v_skipped + 1;
                -- Still mark as synced to avoid re-processing
                UPDATE audio_files SET synced_to_ra = TRUE, synced_at = NOW()
                WHERE id = v_candidate.audio_file_id;
                CONTINUE;
            END IF;

            -- Get or create creator in RA App creators table
            IF v_candidate.author_uniqueid IS NOT NULL THEN
                INSERT INTO creators (author_uniqueid)
                VALUES (v_candidate.author_uniqueid)
                ON CONFLICT (author_uniqueid) DO NOTHING;

                SELECT creator_id INTO v_creator_id
                FROM creators WHERE author_uniqueid = v_candidate.author_uniqueid;
            ELSE
                v_creator_id := NULL;
            END IF;

            -- Insert into RA App transcripts table
            INSERT INTO transcripts (
                meta_id,
                year,
                month,
                day,
                transcript,
                label_1_pred,
                country,
                lang,
                creator_id,
                queue_status,
                review_status,
                created_at
            ) VALUES (
                v_candidate.meta_id,
                v_candidate.year,
                v_candidate.month,
                v_candidate.day,
                v_candidate.transcript,
                v_candidate.label_1_pred,
                v_candidate.country,
                v_candidate.lang,
                v_creator_id,
                'unqueued',
                'unreviewed',
                v_candidate.created_at
            );

            -- Mark as synced in pipeline table
            UPDATE audio_files SET synced_to_ra = TRUE, synced_at = NOW()
            WHERE id = v_candidate.audio_file_id;

            v_synced := v_synced + 1;

        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors + 1;
            RAISE WARNING 'Error syncing meta_id %: %', v_candidate.meta_id, SQLERRM;
        END;
    END LOOP;

    RETURN QUERY SELECT v_synced, v_skipped, v_errors;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Check pipeline processing stats
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

-- Check sync candidates above threshold
CREATE OR REPLACE FUNCTION check_sync_candidates(score_threshold NUMERIC DEFAULT 0.7)
RETURNS TABLE (
    total_candidates BIGINT,
    above_threshold BIGINT,
    by_country JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*) as total_candidates,
        COUNT(*) FILTER (WHERE label_1_pred >= score_threshold) as above_threshold,
        jsonb_object_agg(
            COALESCE(country, 'unknown'),
            cnt
        ) as by_country
    FROM (
        SELECT country, COUNT(*) as cnt
        FROM pipeline_sync_candidates
        WHERE label_1_pred >= score_threshold
        GROUP BY country
    ) sub, (
        SELECT COUNT(*) as total_candidates,
               COUNT(*) FILTER (WHERE label_1_pred >= score_threshold) as above_threshold
        FROM pipeline_sync_candidates
    ) totals;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- GRANTS - Allow RA App user to access pipeline tables
-- ============================================================================

-- Grant permissions to transcript_user (RA App database user)
DO $$
BEGIN
    -- Check if transcript_user role exists before granting
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'transcript_user') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON audio_files TO transcript_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON pipeline_transcripts TO transcript_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON pipeline_classifications TO transcript_user;
        GRANT USAGE, SELECT ON SEQUENCE audio_files_id_seq TO transcript_user;
        GRANT USAGE, SELECT ON SEQUENCE pipeline_transcripts_id_seq TO transcript_user;
        GRANT USAGE, SELECT ON SEQUENCE pipeline_classifications_id_seq TO transcript_user;
        RAISE NOTICE 'Granted permissions to transcript_user';
    ELSE
        RAISE NOTICE 'transcript_user role does not exist, skipping grants';
    END IF;
END $$;

-- ============================================================================
-- VERIFICATION
-- ============================================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pipeline_transcripts')
       AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'pipeline_classifications')
       AND EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'audio_files')
    THEN
        RAISE NOTICE 'Migration successful: All pipeline tables created';
        RAISE NOTICE 'Tables: audio_files, pipeline_transcripts, pipeline_classifications';
        RAISE NOTICE 'Views: pipeline_sync_candidates';
        RAISE NOTICE 'Functions: sync_pipeline_to_ra(), check_pipeline_stats(), check_sync_candidates()';
    ELSE
        RAISE EXCEPTION 'Migration failed: One or more pipeline tables not created';
    END IF;
END $$;
