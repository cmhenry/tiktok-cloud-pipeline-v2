# Task Notes: Storage Infrastructure Sprint

## Current Status

**SPRINT START** - Beginning S3 + local volume infrastructure changes.

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1 (S3 Utils + Config) | NOT STARTED | Foundation module |
| Phase 2 (Transfer Worker) | NOT STARTED | S3 upload integration |
| Phase 3 (Unpack Worker) | NOT STARTED | S3 pull + batch tracking |
| Phase 4 (GPU Worker) | NOT STARTED | S3 upload + cleanup |
| Phase 5 (Ansible Infra) | NOT STARTED | Volumes, models, credentials |
| Phase 6 (Health Checks) | NOT STARTED | S3 + batch monitoring |

## Project Files Summary

```
Existing (from previous sprint):
  config.py           - Central configuration (needs S3 additions)
  utils.py            - Logging, Redis client, archive detection
  db.py               - PostgreSQL connection pool and CRUD
  schema.sql          - Database tables, indexes, views
  transfer_sounds.py  - AWS transfer (needs S3 upload)
  unpack_worker.py    - Archive extraction (needs S3 pull)
  gpu_worker.py       - WhisperX + CoPE-A (needs S3 upload + cleanup)

New files this sprint:
  s3_utils.py         - S3 client operations
  
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
*(To be filled during implementation)*

### Phase 2 Notes
*(To be filled during implementation)*

### Phase 3 Notes
*(To be filled during implementation)*

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