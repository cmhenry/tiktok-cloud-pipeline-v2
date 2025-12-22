# Task Notes: Storage Infrastructure Sprint

## Current Status

**SPRINT START** - Beginning S3 + local volume infrastructure changes.

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1 (S3 Utils + Config) | COMPLETE | s3_utils.py, config.py updated, boto3 added |
| Phase 2 (Transfer Worker) | COMPLETE | S3 upload, JSON job payload, temp staging |
| Phase 3 (Unpack Worker) | COMPLETE | S3 pull, batch tracking, scratch directory |
| Phase 4 (GPU Worker) | NOT STARTED | S3 upload + cleanup |
| Phase 5 (Ansible Infra) | NOT STARTED | Volumes, models, credentials |
| Phase 6 (Health Checks) | NOT STARTED | S3 + batch monitoring |

## Project Files Summary

```
Existing (from previous sprint):
  config.py           - Central configuration (S3 + LOCAL sections added)
  utils.py            - Logging, Redis client, archive detection
  db.py               - PostgreSQL connection pool and CRUD
  schema.sql          - Database tables, indexes, views
  transfer_sounds.py  - AWS transfer (needs S3 upload)
  unpack_worker.py    - Archive extraction (needs S3 pull)
  gpu_worker.py       - WhisperX + CoPE-A (needs S3 upload + cleanup)

New files this sprint:
  s3_utils.py         - S3 client operations [DONE]

Ansible additions:
  roles/gpu_worker/tasks/volume.yml  - Cinder volume setup
  roles/gpu_worker/tasks/models.yml  - Model sync tasks
  playbooks/sync-models.yml          - Standalone model sync
```

## Architecture Decisions

### Opus File Preservation
- **Decision**: Upload processed Opus files to S3 `processed/{date}/{audio_id}.opus`
- **Rationale**: Eliminates need for shared storage, enables long-term retention with S3 lifecycle policies
- **Alternative considered**: Large shared Cinder volume (rejected - reintroduces bottleneck)

### Batch Completion Tracking
- **Decision**: Redis atomic counters for tracking batch progress
- **Implementation**: 
  - Unpack worker sets `batch:{id}:total`
  - GPU worker increments `batch:{id}:processed`
  - When `processed >= total`, cleanup triggers
- **Rationale**: Atomic INCR is safe for concurrent workers; only one worker sees the completion condition

### Source Archive Retention
- **Decision**: Keep source archives in S3 after batch completion (configurable)
- **Rationale**: Enables reprocessing if needed; S3 lifecycle can auto-delete after N days

## Implementation Notes

### Phase 1 Notes

**Completed 2025-12-22**

Files created/modified:
- `src/s3_utils.py` - New S3 client module with all operations
- `src/config.py` - Added S3 and LOCAL configuration sections
- `requirements.txt` - Added boto3>=1.34.0

Implementation details:
- S3 client is cached globally for reuse (`_s3_client` singleton)
- `upload_archive()` uses multipart upload for files >100MB with progress logging
- `download_archive()` creates scratch directory automatically
- `cleanup_scratch()` is idempotent (no error if directory missing)
- Added `check_s3_connection()` helper for health checks
- Added `get_archive_size()` helper for file verification
- All functions use shared logger from utils.py for consistency

Configuration added:
```python
S3 = {
    "ENDPOINT": os.getenv("S3_ENDPOINT"),
    "ACCESS_KEY": os.getenv("S3_ACCESS_KEY"),
    "SECRET_KEY": os.getenv("S3_SECRET_KEY"),
    "BUCKET": os.getenv("S3_BUCKET", "audio-pipeline"),
    "ARCHIVE_PREFIX": "archives/",
    "PROCESSED_PREFIX": "processed/",
}

LOCAL = {
    "SCRATCH_ROOT": Path(os.getenv("SCRATCH_ROOT", "/data/scratch")),
    "MODELS_ROOT": Path(os.getenv("MODELS_ROOT", "/data/models")),
}
```

Testing: Can test locally with MinIO using env vars from IMPLEMENTATION_PROMPTS.md

### Phase 2 Notes

**Completed 2025-12-22**

Files modified:
- `src/transfer_sounds.py` - S3 upload integration

Key changes:
1. **Imports added**: `s3_utils.upload_archive`, `datetime`, `uuid`, `tempfile`, `Path`
2. **Removed**: `PATHS` import (no longer using shared volume)
3. **New staging dir**: Uses `tempfile.gettempdir()/transfer_staging` instead of shared volume
4. **Batch ID format**: `YYYYMMDD-HHMMSS-{6-char-uuid}` (e.g., `20250622-143052-a1b2c3`)

Flow changes:
```
BEFORE: SCP → shared volume → push path string to Redis
AFTER:  SCP → temp staging → S3 upload → push JSON to Redis → cleanup temp
```

New job payload format:
```json
{
    "batch_id": "20250622-143052-a1b2c3",
    "s3_key": "archives/20250622-143052-a1b2c3.tar",
    "original_filename": "sound_export_2025.tar",
    "transferred_at": "2025-06-22T14:30:52Z"
}
```

Error handling:
- If S3 upload fails, job is NOT enqueued
- Local staging file is NOT deleted on S3 failure (enables retry)
- Cleanup failure is logged as warning but doesn't block

Note: `file_count` field omitted from payload (not available at transfer time).
Unpack worker can count files after extraction if needed.

### Phase 3 Notes

**Completed 2025-12-22**

Files modified:
- `src/unpack_worker.py` - Complete rewrite of processing logic for S3 flow

Key changes:
1. **Imports updated**: Removed `PATHS`, `shutil`, `bulk_insert_audio_files`; Added `s3_utils.download_archive`, `s3_utils.cleanup_scratch`, `LOCAL`
2. **New `process_job()` function**: Replaces `process_archive()`, handles S3-based flow
3. **Scratch directory**: All work done in `LOCAL["SCRATCH_ROOT"]/{batch_id}/`
4. **DB insert removed**: GPU worker now handles DB insertion (Phase 4)

Flow changes:
```
BEFORE: Pop path string → extract to UNPACKED_DIR → convert to AUDIO_DIR → DB insert → queue with audio_id
AFTER:  Pop JSON → S3 download → extract to scratch → convert in scratch → batch tracking → queue with batch_id
```

New transcription job format (queue:transcribe):
```json
{
    "batch_id": "20250622-143052-a1b2c3",
    "opus_path": "/data/scratch/20250622-143052-a1b2c3/audio001.opus",
    "original_filename": "audio001.mp3"
}
```

Batch tracking keys set:
```
batch:{batch_id}:total     = N      # Number of opus files
batch:{batch_id}:processed = 0      # Incremented by GPU worker
batch:{batch_id}:s3_key    = "..."  # For reference/cleanup
```

Cleanup behavior:
- Archive.tar deleted after extraction (saves space)
- MP3 files deleted after conversion (saves space)
- Opus files KEPT in scratch for GPU worker
- On failure: entire scratch directory cleaned up

Note: `audio_id` removed from transcription job - GPU worker will:
1. Insert to DB (gets audio_id)
2. Upload opus to S3
3. Update DB with S3 path

### Phase 4 Notes
*(To be filled during implementation)*

### Phase 5 Notes
*(To be filled during implementation)*

### Phase 6 Notes
*(To be filled during implementation)*

## Questions / Blockers

- [ ] Confirm S3 endpoint URL and credentials available
- [ ] Confirm Cinder volume attachment point (`/dev/vdb` assumed)
- [ ] Confirm model source location on orchestrator for sync
- [ ] S3 lifecycle policy for archive retention (7 days suggested)

## Testing Checklist

- [ ] S3 utils work with MinIO locally
- [ ] Transfer worker uploads to S3 successfully
- [ ] Unpack worker pulls from S3 to scratch
- [ ] GPU worker uploads opus to S3
- [ ] Batch completion triggers cleanup
- [ ] Concurrent workers handle same batch correctly
- [ ] Ansible volume setup is idempotent
- [ ] Model sync completes successfully (~50GB)

## What Remains After This Sprint

1. **Production deployment** - Run Ansible playbooks on actual infrastructure
2. **End-to-end integration test** - Full flow from EC2 to RA queue
3. **Monitoring setup** - Alerts for stuck batches, S3 errors
4. **Documentation update** - Update DEVELOPMENT_GUIDE.md with new architecture