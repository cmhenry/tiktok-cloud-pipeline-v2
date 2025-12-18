# Task Notes for Next Iteration

## Current Status
Phase 1 (Shared Infrastructure) is COMPLETE:
- `config.py` - Central configuration with env vars
- `utils.py` - Logging, Redis client, archive detection, file ops
- `db.py` - PostgreSQL connection pool and CRUD operations
- `schema.sql` - Database schema with indexes and views

## Next Steps (in order)

### 1. Phase 2: Run Database Migration
- Deploy `schema.sql` to coordinator VM PostgreSQL
- Test connection from worker VMs

### 2. Phase 3: Transfer Worker Integration
Modify `transfer_sounds.py` to use shared config:
- Import from `config.py` instead of hardcoded values
- Add Redis queue push after successful transfer (line ~514)
- Keep existing transfer logic intact
- Key changes outlined in SHARED_TASK_LIST.md Phase 3.1

### 3. Phase 4: Unpack Worker
Create `unpack_worker.py`:
- Content-based archive detection (tar vs gzip - files are often mislabeled)
- Parallel ffmpeg MP3→Opus conversion
- Bulk DB inserts
- Queue items for GPU worker

## Dependencies to Install
```bash
pip install redis psycopg2-binary python-magic
# GPU workers also need: whisperx transformers peft bitsandbytes
```

## Testing Notes
- Test `utils.py` archive detection with sample tar files
- Test `db.py` connection pool with coordinator VM
- Redis connection can be tested locally with `redis-cli ping`
