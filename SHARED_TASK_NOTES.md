# Task Notes for Next Iteration

## Current Status
- Phase 1 (Shared Infrastructure): COMPLETE
- Phase 2 (Database Migration): Pending deployment (ops task)
- Phase 3 (Transfer Worker): COMPLETE
- Phase 4 (Unpack Worker): COMPLETE
- Phase 5 (GPU Worker): COMPLETE

## What Was Done This Iteration
Created `gpu_worker.py` with all required features:
- WhisperX large-v2 transcription with float16 compute
- Gemma-2-9B + CoPE-A LoRA with 8-bit quantization (fits 24GB VRAM)
- Batch collection from Redis `list:transcribe` queue (batch size 32)
- Robust JSON parsing for malformed CoPE-A responses
- DB writes: transcripts, classifications, status updates
- VRAM monitoring logged after model load and periodically
- Per-item error handling (failures don't crash the batch)
- Status set to "flagged" or "transcribed" based on classification result

## Next Steps (in order)

### 1. Phase 6: Deployment (HIGH PRIORITY)
Create systemd service files and setup scripts:

**Service files needed:**
- `/etc/systemd/system/unpack-worker.service`
- `/etc/systemd/system/gpu-worker.service`
- `/etc/systemd/system/transfer-worker.service`

**Setup scripts needed:**
- Coordinator VM setup (Redis, Postgres, schema migration)
- Worker VM setup (mount volume, Python venv, GPU deps)

**Monitoring:**
- SQL queries for daily health check
- Redis queue depth monitoring

See SHARED_TASK_LIST.md Phase 6 for full outline.

## Dependencies
```bash
# All workers
pip install redis psycopg2-binary python-magic

# GPU worker additional deps
pip install whisperx transformers peft bitsandbytes

# Note: whisperx may require additional setup for ctranslate2
```

## Testing Notes
- All workers syntax-verified with `python -m py_compile *.py`
- Full integration requires:
  - Redis running on coordinator VM
  - Postgres with schema deployed
  - Shared volume mounted at /mnt/data
  - GPU with 24GB VRAM for gpu_worker
  - CoPE-A LoRA adapter at /models/cope-a-lora
