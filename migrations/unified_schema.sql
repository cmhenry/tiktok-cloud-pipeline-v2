-- =============================================================================
-- UNIFIED SCHEMA: Audio Processing Pipeline + RA Content Review App
-- =============================================================================
--
-- This schema integrates two systems:
-- 1. Audio Processing Pipeline - ingestion, transcription, classification
-- 2. RA Content Review App - research assistant workflow and labeling
--
-- Data ownership:
-- - Pipeline owns: audio_files, pipeline_transcripts, pipeline_classifications
-- - RA App owns: transcripts (content entity), users, queue_*, etc.
-- - Bridge: pipeline populates RA app's transcripts table via sync function
--
-- Run this on a fresh database or carefully merge with existing RA app data.
-- =============================================================================

-- =============================================================================
-- SECTION 1: PIPELINE TABLES
-- Owned by audio processing pipeline, written by workers
-- =============================================================================

-- Main audio files table - tracks all ingested audio
CREATE TABLE IF NOT EXISTS audio_files (
    id SERIAL PRIMARY KEY,
    
    -- File identification
    original_filename TEXT NOT NULL,
    opus_path TEXT NOT NULL UNIQUE,
    archive_source TEXT,
    
    -- Audio metadata
    duration_seconds FLOAT,
    file_size_bytes INTEGER,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP,
    
    -- Processing status: pending, transcribed, classified, synced, failed
    status TEXT DEFAULT 'pending',
    
    -- Link to RA app's transcripts table (populated after sync)
    meta_id VARCHAR(255) UNIQUE
);

COMMENT ON TABLE audio_files IS 'Pipeline: Tracks all audio files from ingestion through processing';
COMMENT ON COLUMN audio_files.meta_id IS 'Foreign key to transcripts table, populated by sync process';

-- Pipeline transcripts - WhisperX output
CREATE TABLE IF NOT EXISTS pipeline_transcripts (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER NOT NULL REFERENCES audio_files(id) ON DELETE CASCADE,
    
    -- WhisperX output
    transcript_text TEXT,
    language VARCHAR(20),
    confidence FLOAT,
    
    -- Word-level timestamps (optional, for future use)
    word_timestamps JSONB,
    
    created_at TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE pipeline_transcripts IS 'Pipeline: WhisperX transcription output';

-- Pipeline classifications - CoPE-A output  
CREATE TABLE IF NOT EXISTS pipeline_classifications (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER NOT NULL REFERENCES audio_files(id) ON DELETE CASCADE,
    
    -- CoPE-A output
    flagged BOOLEAN NOT NULL,
    flag_score FLOAT,          -- Raw model score (0-1)
    flag_category TEXT,        -- Category if flagged
    
    -- Raw model output (for debugging/analysis)
    raw_output JSONB,
    
    created_at TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE pipeline_classifications IS 'Pipeline: CoPE-A harmful content classification output';

-- Pipeline indexes
CREATE INDEX IF NOT EXISTS idx_audio_files_status ON audio_files(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audio_files_archive ON audio_files(archive_source);
CREATE INDEX IF NOT EXISTS idx_audio_files_meta_id ON audio_files(meta_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_transcripts_audio ON pipeline_transcripts(audio_file_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_classifications_audio ON pipeline_classifications(audio_file_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_classifications_flagged ON pipeline_classifications(flagged, created_at DESC) 
    WHERE flagged = true;


-- =============================================================================
-- SECTION 2: RA APP TABLES
-- Owned by Flask app, managed by Flask-Migrate
-- =============================================================================

-- Users table - RA accounts
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    full_name VARCHAR(100),
    role VARCHAR(20) DEFAULT 'researcher',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP,
    
    CONSTRAINT users_role_check CHECK (role IN ('admin', 'researcher', 'supervisor'))
);

COMMENT ON TABLE users IS 'RA App: User accounts for research assistants and admins';

-- Devices table - physical phones for reporting
CREATE TABLE IF NOT EXISTS devices (
    device_id SERIAL PRIMARY KEY,
    device_label VARCHAR(50) NOT NULL UNIQUE,
    region VARCHAR(10) NOT NULL,
    country VARCHAR(10),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE devices IS 'RA App: Physical devices (phones) used for reporting content';

-- Creators table - content creator tracking
CREATE TABLE IF NOT EXISTS creators (
    creator_id SERIAL PRIMARY KEY,
    author_uniqueid VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE creators IS 'RA App: Content creators for tracking and analysis';

-- Transcripts table - CENTRAL ENTITY for RA workflow
-- This is the main table the RA app works with
CREATE TABLE IF NOT EXISTS transcripts (
    meta_id VARCHAR(255) PRIMARY KEY,
    
    -- Date components (from original pipeline)
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    day INTEGER NOT NULL CHECK (day >= 1 AND day <= 31),
    
    -- Content
    transcript TEXT NOT NULL,
    video_url TEXT,
    creator_id INTEGER REFERENCES creators(creator_id) ON DELETE SET NULL,
    
    -- Classification score (from pipeline)
    label_1_pred NUMERIC(10,9),
    
    -- Metadata
    country VARCHAR(50),
    lang VARCHAR(20),
    
    -- Queue workflow status
    queue_status VARCHAR(20) DEFAULT 'unqueued',
    
    -- Report tracking
    first_report_date DATE,
    first_report_device_id INTEGER REFERENCES devices(device_id),
    total_report_count INTEGER DEFAULT 0,
    
    -- Review status
    review_status VARCHAR(30) DEFAULT 'unreviewed',
    is_reportable BOOLEAN,
    confidence_level VARCHAR(10),
    
    -- Experiment assignment
    in_study BOOLEAN DEFAULT false,
    treatment_assignment VARCHAR(10),
    report_region VARCHAR(10),
    
    -- Pipeline link (for traceability)
    audio_file_id INTEGER REFERENCES audio_files(id),
    
    created_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT chk_transcripts_queue_status CHECK (
        queue_status IN ('unqueued', 'queued', 'in_progress', 'completed', 'excluded')
    )
);

COMMENT ON TABLE transcripts IS 'RA App: Central content entity - populated from pipeline, used for RA workflow';
COMMENT ON COLUMN transcripts.audio_file_id IS 'Link back to pipeline audio_files table for traceability';

-- Experiment configuration
CREATE TABLE IF NOT EXISTS experiment_config (
    config_id SERIAL PRIMARY KEY,
    config_name VARCHAR(100) NOT NULL,
    queue_size_per_ra INTEGER DEFAULT 25 CHECK (queue_size_per_ra > 0),
    label_threshold NUMERIC(10,9) DEFAULT 0.70 CHECK (label_threshold >= 0 AND label_threshold <= 1),
    treatment_split NUMERIC(3,2) DEFAULT 0.50 CHECK (treatment_split >= 0 AND treatment_split <= 1),
    stratify_by_country BOOLEAN DEFAULT true,
    stratify_by_language BOOLEAN DEFAULT true,
    min_items_per_stratum INTEGER DEFAULT 3 CHECK (min_items_per_stratum >= 0),
    is_active BOOLEAN DEFAULT false,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    created_by_user_id INTEGER REFERENCES users(user_id)
);

COMMENT ON TABLE experiment_config IS 'RA App: Admin-configurable experiment parameters';

-- Queue batches - tracks each queue generation
CREATE TABLE IF NOT EXISTS queue_batches (
    batch_id SERIAL PRIMARY KEY,
    generation_date DATE NOT NULL,
    generation_timestamp TIMESTAMP DEFAULT NOW(),
    config_id INTEGER REFERENCES experiment_config(config_id),
    total_items_generated INTEGER,
    items_per_ra JSONB,
    status VARCHAR(20) DEFAULT 'generated',
    error_log TEXT,
    generated_by_user_id INTEGER REFERENCES users(user_id),
    
    CONSTRAINT queue_batches_status_check CHECK (
        status IN ('generated', 'active', 'completed', 'failed', 'cancelled')
    )
);

COMMENT ON TABLE queue_batches IS 'RA App: Tracks each queue generation event';

-- Queue assignments - what's assigned to whom
CREATE TABLE IF NOT EXISTS queue_assignments (
    assignment_id SERIAL PRIMARY KEY,
    meta_id VARCHAR(255) REFERENCES transcripts(meta_id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    
    -- Experiment tracking
    treatment_round INTEGER DEFAULT 1 CHECK (treatment_round IN (1, 2)),
    treatment_group VARCHAR(50),
    assigned_treatment BOOLEAN,
    
    -- Status workflow
    status VARCHAR(20) DEFAULT 'pending',
    assigned_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    
    -- Batch tracking
    batch_id INTEGER REFERENCES queue_batches(batch_id),
    stratum_info JSONB,
    label_1_pred NUMERIC(10,9),
    
    -- Assignment type
    assignment_type VARCHAR(20) DEFAULT 'new_post',
    device_id INTEGER REFERENCES devices(device_id),
    
    -- Locking (prevent concurrent edits)
    locked_at TIMESTAMP,
    lock_expires_at TIMESTAMP,
    last_language VARCHAR(10),
    
    CONSTRAINT queue_assignments_status_check CHECK (
        status IN ('pending', 'in_progress', 'completed', 'skipped')
    )
);

COMMENT ON TABLE queue_assignments IS 'RA App: Assignment of content to research assistants';

-- User classifications - RA labeling decisions
CREATE TABLE IF NOT EXISTS user_classifications (
    classification_id SERIAL PRIMARY KEY,
    meta_id VARCHAR(255) REFERENCES transcripts(meta_id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    treatment_round INTEGER NOT NULL CHECK (treatment_round IN (1, 2)),
    classification_data JSONB,
    notes TEXT,
    confidence_level VARCHAR(10),
    classified_at TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE user_classifications IS 'RA App: RA labeling decisions and notes';

-- Backlog - posts needing re-reports
CREATE TABLE IF NOT EXISTS backlog (
    backlog_id SERIAL PRIMARY KEY,
    meta_id VARCHAR(255) UNIQUE REFERENCES transcripts(meta_id) ON DELETE CASCADE,
    first_report_date DATE NOT NULL,
    first_report_device_id INTEGER REFERENCES devices(device_id),
    report_count INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT true,
    added_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    removal_reason VARCHAR(20),
    last_report_date DATE NOT NULL,
    report_details JSONB DEFAULT '[]'
);

COMMENT ON TABLE backlog IS 'RA App: Posts requiring re-reports from different devices';

-- Second review queue
CREATE TABLE IF NOT EXISTS second_review_queue (
    review_id SERIAL PRIMARY KEY,
    meta_id VARCHAR(255) REFERENCES transcripts(meta_id) ON DELETE CASCADE,
    first_reviewer_id INTEGER REFERENCES users(user_id),
    first_review_at TIMESTAMP DEFAULT NOW(),
    second_reviewer_id INTEGER REFERENCES users(user_id),
    assigned_at TIMESTAMP,
    completed_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'pending'
);

COMMENT ON TABLE second_review_queue IS 'RA App: Low-confidence reviews awaiting second opinion';

-- RA availability
CREATE TABLE IF NOT EXISTS ra_availability (
    availability_id SERIAL PRIMARY KEY,
    ra_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
    work_date DATE NOT NULL,
    expected_hours NUMERIC(4,2) DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_by INTEGER REFERENCES users(user_id)
);

COMMENT ON TABLE ra_availability IS 'RA App: Daily availability hours for RAs';

-- RA device assignments
CREATE TABLE IF NOT EXISTS ra_devices (
    ra_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
    device_id INTEGER REFERENCES devices(device_id) ON DELETE CASCADE,
    assigned_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (ra_id, device_id)
);

COMMENT ON TABLE ra_devices IS 'RA App: Which RAs have which devices';

-- RA App indexes
CREATE INDEX IF NOT EXISTS idx_transcripts_queue_status ON transcripts(queue_status);
CREATE INDEX IF NOT EXISTS idx_transcripts_label_pred ON transcripts(label_1_pred DESC) WHERE label_1_pred IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_transcripts_review_status ON transcripts(review_status);
CREATE INDEX IF NOT EXISTS idx_transcripts_in_study ON transcripts(in_study) WHERE in_study = true;
CREATE INDEX IF NOT EXISTS idx_queue_assignments_user_status ON queue_assignments(user_id, status);
CREATE INDEX IF NOT EXISTS idx_queue_assignments_batch ON queue_assignments(batch_id);
CREATE INDEX IF NOT EXISTS idx_user_classifications_meta ON user_classifications(meta_id);
CREATE INDEX IF NOT EXISTS idx_backlog_active ON backlog(is_active) WHERE is_active = true;


-- =============================================================================
-- SECTION 3: INTEGRATION VIEWS AND FUNCTIONS
-- Bridge between pipeline and RA app
-- =============================================================================

-- View: Pipeline items ready for RA review (not yet synced to transcripts)
CREATE OR REPLACE VIEW pipeline_ready_for_sync AS
SELECT 
    af.id as audio_file_id,
    af.original_filename,
    af.opus_path,
    af.created_at,
    pt.transcript_text,
    pt.language,
    pt.confidence as whisper_confidence,
    pc.flagged,
    pc.flag_score,
    pc.flag_category
FROM audio_files af
JOIN pipeline_transcripts pt ON pt.audio_file_id = af.id
JOIN pipeline_classifications pc ON pc.audio_file_id = af.id
WHERE af.status = 'classified'
  AND af.meta_id IS NULL  -- Not yet synced
  AND pc.flagged = true   -- Only flagged items go to RA review
ORDER BY pc.flag_score DESC;

COMMENT ON VIEW pipeline_ready_for_sync IS 'Pipeline items classified as flagged, ready to sync to RA app';

-- View: RA queue - flagged items from last 24 hours ready for review
CREATE OR REPLACE VIEW ra_queue AS
SELECT 
    t.meta_id,
    t.transcript,
    t.label_1_pred,
    t.country,
    t.lang,
    t.video_url,
    t.created_at,
    af.opus_path,
    af.duration_seconds
FROM transcripts t
LEFT JOIN audio_files af ON af.meta_id = t.meta_id
WHERE t.queue_status = 'unqueued'
  AND t.label_1_pred >= 0.70  -- Threshold from experiment_config
  AND t.created_at > NOW() - INTERVAL '24 hours'
ORDER BY t.label_1_pred DESC;

COMMENT ON VIEW ra_queue IS 'Content ready for RA queue assignment';

-- View: Daily processing stats
CREATE OR REPLACE VIEW daily_pipeline_stats AS
SELECT
    DATE(created_at) as date,
    COUNT(*) as total_files,
    COUNT(*) FILTER (WHERE status = 'pending') as pending,
    COUNT(*) FILTER (WHERE status = 'transcribed') as transcribed,
    COUNT(*) FILTER (WHERE status = 'classified') as classified,
    COUNT(*) FILTER (WHERE status = 'synced') as synced,
    COUNT(*) FILTER (WHERE status = 'failed') as failed
FROM audio_files
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY DATE(created_at)
ORDER BY date DESC;

COMMENT ON VIEW daily_pipeline_stats IS 'Daily pipeline processing statistics';

-- Function: Sync pipeline items to RA app transcripts table
-- Call this periodically (e.g., every 5 minutes) to move flagged items to RA workflow
CREATE OR REPLACE FUNCTION sync_pipeline_to_transcripts(batch_size INTEGER DEFAULT 100)
RETURNS TABLE (
    synced_count INTEGER,
    meta_ids TEXT[]
) AS $$
DECLARE
    v_synced_count INTEGER := 0;
    v_meta_ids TEXT[] := '{}';
    v_record RECORD;
    v_meta_id TEXT;
BEGIN
    FOR v_record IN 
        SELECT * FROM pipeline_ready_for_sync
        LIMIT batch_size
    LOOP
        -- Generate meta_id from original filename (strip extension, use as ID)
        -- Adjust this logic based on your actual filename format
        v_meta_id := regexp_replace(v_record.original_filename, '\.[^.]+$', '');
        
        -- Insert into transcripts (RA app's central table)
        INSERT INTO transcripts (
            meta_id,
            year,
            month, 
            day,
            transcript,
            label_1_pred,
            lang,
            queue_status,
            audio_file_id,
            created_at
        ) VALUES (
            v_meta_id,
            EXTRACT(YEAR FROM v_record.created_at)::INTEGER,
            EXTRACT(MONTH FROM v_record.created_at)::INTEGER,
            EXTRACT(DAY FROM v_record.created_at)::INTEGER,
            v_record.transcript_text,
            v_record.flag_score,
            v_record.language,
            'unqueued',
            v_record.audio_file_id,
            v_record.created_at
        )
        ON CONFLICT (meta_id) DO NOTHING;  -- Skip if already exists
        
        -- Update audio_files with meta_id and mark as synced
        UPDATE audio_files 
        SET meta_id = v_meta_id, 
            status = 'synced'
        WHERE id = v_record.audio_file_id;
        
        v_synced_count := v_synced_count + 1;
        v_meta_ids := array_append(v_meta_ids, v_meta_id);
    END LOOP;
    
    RETURN QUERY SELECT v_synced_count, v_meta_ids;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION sync_pipeline_to_transcripts IS 'Sync flagged pipeline items to RA app transcripts table';

-- Function: Check if we have enough flagged content for RA window
CREATE OR REPLACE FUNCTION check_ra_target(target_count INTEGER DEFAULT 200)
RETURNS TABLE (
    current_count BIGINT,
    target INTEGER,
    target_met BOOLEAN,
    pipeline_pending BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT COUNT(*) FROM ra_queue) as current_count,
        target_count as target,
        (SELECT COUNT(*) FROM ra_queue) >= target_count as target_met,
        (SELECT COUNT(*) FROM pipeline_ready_for_sync) as pipeline_pending;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION check_ra_target IS 'Check if we have enough flagged content for RA daily target';


-- =============================================================================
-- SECTION 4: PERMISSIONS
-- =============================================================================

-- Create roles if they don't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pipeline_user') THEN
        CREATE ROLE pipeline_user WITH LOGIN PASSWORD 'changeme';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ra_app_user') THEN
        CREATE ROLE ra_app_user WITH LOGIN PASSWORD 'changeme';
    END IF;
END
$$;

-- Pipeline user: full access to pipeline tables, read/insert on transcripts
GRANT ALL ON audio_files, pipeline_transcripts, pipeline_classifications TO pipeline_user;
GRANT ALL ON SEQUENCE audio_files_id_seq, pipeline_transcripts_id_seq, pipeline_classifications_id_seq TO pipeline_user;
GRANT SELECT, INSERT, UPDATE ON transcripts TO pipeline_user;
GRANT EXECUTE ON FUNCTION sync_pipeline_to_transcripts TO pipeline_user;

-- RA app user: full access to RA tables, read on pipeline tables
GRANT ALL ON users, devices, creators, transcripts, experiment_config, queue_batches, 
    queue_assignments, user_classifications, backlog, second_review_queue, 
    ra_availability, ra_devices TO ra_app_user;
GRANT ALL ON SEQUENCE users_user_id_seq, devices_device_id_seq, creators_creator_id_seq,
    experiment_config_config_id_seq, queue_batches_batch_id_seq, queue_assignments_assignment_id_seq,
    user_classifications_classification_id_seq, backlog_backlog_id_seq, second_review_queue_review_id_seq,
    ra_availability_availability_id_seq TO ra_app_user;
GRANT SELECT ON audio_files, pipeline_transcripts, pipeline_classifications TO ra_app_user;
GRANT SELECT ON pipeline_ready_for_sync, ra_queue, daily_pipeline_stats TO ra_app_user;
GRANT EXECUTE ON FUNCTION check_ra_target TO ra_app_user;