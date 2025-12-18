# Task Notes for Next Iteration

## Current Status
- Phase 1 (Shared Infrastructure): COMPLETE
- Phase 2 (Database Migration): Pending deployment (ops task)
- Phase 3 (Transfer Worker): COMPLETE
- Phase 4 (Unpack Worker): COMPLETE
- Phase 5 (GPU Worker): COMPLETE
- Phase 6 (Deployment): COMPLETE

## What Was Done This Iteration
Created all Phase 6 deployment artifacts in `deploy/`:

**Systemd service files** (`deploy/systemd/`):
- `transfer-worker.service` - For transfer VM
- `unpack-worker.service` - For GPU worker VMs
- `gpu-worker.service` - For GPU worker VMs (300s startup timeout for model loading)

**Setup scripts** (`deploy/`):
- `setup-coordinator.sh` - Installs Redis, Postgres, creates DB/user, runs schema
- `setup-worker.sh` - Mounts volume, creates venv, installs deps, deploys services

**Monitoring** (`deploy/`):
- `monitoring.sql` - 12 SQL queries for health checks, RA queue, errors, rates
- `check-health.sh` - Combined script checking Redis queues + DB stats + noon target

## Next Steps

### Integration Testing
All code is written. Next iteration should focus on testing with actual infrastructure:

1. **Deploy coordinator VM**
   - Run `deploy/setup-coordinator.sh`
   - Verify Redis and Postgres are accessible from worker subnet

2. **Deploy one worker VM**
   - Run `deploy/setup-worker.sh COORDINATOR_IP=<ip>`
   - Test unpack worker with a sample tar archive
   - Test GPU worker with sample audio

3. **End-to-end test**
   - Drop tar file in /mnt/data/incoming
   - Push path to `list:unpack` queue
   - Verify file flows through entire pipeline

### Optional Enhancements (lower priority)
- Prometheus/Grafana metrics export
- Slack/email alerts for noon target miss
- Web dashboard for RA queue

## File Structure
```
deploy/
  systemd/
    transfer-worker.service
    unpack-worker.service
    gpu-worker.service
  setup-coordinator.sh
  setup-worker.sh
  monitoring.sql
  check-health.sh
```

## Testing Notes
- All workers syntax-verified with `python -m py_compile *.py`
- Integration test requires actual VMs with:
  - Redis on coordinator
  - Postgres with schema
  - Shared volume at /mnt/data
  - GPU (24GB VRAM) for gpu_worker
  - CoPE-A LoRA adapter at /models/cope-a-lora
