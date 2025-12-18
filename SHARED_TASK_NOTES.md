# Task Notes for Next Iteration

## Current Status
- Phase 1 (Shared Infrastructure): COMPLETE
- Phase 2 (Database Migration): Pending deployment (ops task)
- Phase 3 (Transfer Worker): COMPLETE

## What Was Done This Iteration
Modified `transfer_sounds.py` to use shared config:
- Imports `AWS`, `TRANSFER_LOCKS`, `REDIS`, `PATHS`, `LOGGING` from `config.py`
- Fixed bug on line 430 (invalid `os.path.dirname` call with multiple args)
- Added Redis queue push after successful transfer (pushes to `list:unpack`)
- Verified syntax and imports work correctly

## Next Steps (in order)

### 1. Phase 4: Unpack Worker (HIGH PRIORITY)
Create `unpack_worker.py`:
- Skeleton outline in SHARED_TASK_LIST.md Phase 4.1
- Key features:
  - Content-based archive detection (use `utils.detect_archive_type`)
  - Handle mislabeled `.tar.gz` files that are actually plain tar
  - Parallel ffmpeg MP3→Opus conversion using ProcessPoolExecutor
  - Bulk DB inserts via `db.bulk_insert_audio_files`
  - Queue items for GPU worker via Redis `list:transcribe`
  - Cleanup temp directories after processing

### 2. Phase 5: GPU Worker
Create `gpu_worker.py`:
- WhisperX for transcription
- Gemma-2-9B + CoPE-A LoRA for classification
- See SHARED_TASK_LIST.md Phase 5.1 for full outline

### 3. Phase 6: Deployment
- Systemd service files
- Coordinator/worker VM setup scripts

## Dependencies
```bash
pip install redis psycopg2-binary python-magic
# GPU workers also need: whisperx transformers peft bitsandbytes
```

## Testing Notes
- Transfer worker can be tested with `python -m py_compile transfer_sounds.py`
- Full integration requires Redis running on coordinator VM
