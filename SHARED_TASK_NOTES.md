# Task Notes for Next Iteration

## Current Status
- Phase 1 (Shared Infrastructure): COMPLETE
- Phase 2 (Database Migration): Pending deployment (ops task)
- Phase 3 (Transfer Worker): COMPLETE
- Phase 4 (Unpack Worker): COMPLETE

## What Was Done This Iteration
Created `unpack_worker.py` with all required features:
- Main loop consuming from Redis `list:unpack` queue
- Content-based archive detection (handles mislabeled `.tar.gz` that are actually plain tar)
- Parallel MP3→Opus conversion using ProcessPoolExecutor (4 workers)
- Audio duration detection via ffprobe
- Bulk DB inserts via `bulk_insert_audio_files`
- Queues items for GPU worker via Redis `list:transcribe`
- Cleanup of temp extraction directories
- Archive moved to processed directory after completion
- Error handling with failed archives pushed to `list:failed` queue

## Next Steps (in order)

### 1. Phase 5: GPU Worker (HIGH PRIORITY)
Create `gpu_worker.py`:
- WhisperX for transcription (large-v2 model)
- Gemma-2-9B + CoPE-A LoRA for classification (8-bit quantization)
- Batch collection from Redis `list:transcribe` queue
- DB writes for transcripts and classifications
- See SHARED_TASK_LIST.md Phase 5.1 for full outline

Key implementation details:
- Both models must fit in 24GB VRAM - use 8-bit quantization
- CoPE-A JSON responses may be malformed - handle parsing errors
- Process batches of 32 files
- Update audio status to "flagged" or "transcribed" based on classification

### 2. Phase 6: Deployment
- Systemd service files for unpack-worker and gpu-worker
- Coordinator/worker VM setup scripts
- Monitoring queries

## Dependencies
```bash
pip install redis psycopg2-binary python-magic
# GPU workers also need: whisperx transformers peft bitsandbytes
```

## Testing Notes
- Unpack worker: `python -m py_compile unpack_worker.py` (verified working)
- Full integration requires Redis running on coordinator VM
- Test unpack worker with a sample tar archive containing MP3s
