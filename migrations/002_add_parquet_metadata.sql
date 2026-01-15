-- Migration: Add parquet metadata support
-- Run this on existing databases to add parquet metadata columns
--
-- Usage:
--   psql -h 10.0.0.1 -U pipeline -d audio_pipeline -f 002_add_parquet_metadata.sql

-- Add parquet metadata columns to audio_files
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS parquet_lang TEXT;
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS parquet_country TEXT;
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS parquet_metadata JSONB;

-- Create indexes for parquet fields
CREATE INDEX IF NOT EXISTS idx_audio_parquet_lang ON audio_files(parquet_lang);
CREATE INDEX IF NOT EXISTS idx_audio_parquet_country ON audio_files(parquet_country);
CREATE INDEX IF NOT EXISTS idx_audio_parquet_metadata ON audio_files USING GIN (parquet_metadata);

-- Update ra_queue view to include parquet metadata
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

-- Verify migration
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'audio_files' AND column_name = 'parquet_metadata'
    ) THEN
        RAISE NOTICE 'Migration successful: parquet_metadata column exists';
    ELSE
        RAISE EXCEPTION 'Migration failed: parquet_metadata column not created';
    END IF;
END $$;
