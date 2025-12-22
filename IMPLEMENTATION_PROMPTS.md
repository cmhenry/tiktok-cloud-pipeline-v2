# Implementation Prompts: Storage Infrastructure Changes

These prompts implement the S3 object storage and local Cinder volume architecture changes.

---

## Prompt 1: S3 Utilities Module

**Context**: We're adding S3-compatible object storage to eliminate the shared volume bottleneck. Archives will be uploaded by the transfer worker, pulled by GPU workers, and processed Opus files will be uploaded back to S3 for long-term storage.

**Requirements**:
1. Create `s3_utils.py` in the audio-pipeline repo root
2. Use boto3 with S3v4 signature (required for OpenStack Swift compatibility)
3. All config from environment variables: `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`

**Functions needed**:
- `get_s3_client()` → returns configured boto3 S3 client
- `upload_archive(local_path: Path, batch_id: str) -> str` → uploads to `archives/{batch_id}.tar`, returns S3 key
- `download_archive(s3_key: str, batch_id: str) -> Path` → downloads to `/data/scratch/{batch_id}/archive.tar`, returns local path
- `upload_opus(local_path: Path, audio_id: int, date_str: str) -> str` → uploads to `processed/{date}/{audio_id}.opus`, returns S3 key
- `cleanup_scratch(batch_id: str)` → removes `/data/scratch/{batch_id}/` directory

**S3 key structure**:
```
{bucket}/
├── archives/{batch_id}.tar           # Incoming archives
└── processed/{date}/{audio_id}.opus  # Preserved opus files
```

**Interface reference**:
```python
from botocore.config import Config
config=Config(signature_version='s3v4')
```

**Notes**: 
- `download_archive` should create the scratch directory if it doesn't exist
- Scratch root should come from config (env var `SCRATCH_ROOT`, default `/data/scratch`)
- Consider multipart upload for large archives (>100MB)
- `upload_opus` date format: `YYYY-MM-DD`

---

## Prompt 2: Update Configuration

**Context**: Adding S3 configuration to existing config.py pattern.

**Requirements**:
1. Add S3 config section to `config.py`
2. Add `SCRATCH_ROOT` and `MODELS_ROOT` paths for GPU worker local storage

**Config values needed**:
```
S3_ENDPOINT (required)
S3_ACCESS_KEY (required)
S3_SECRET_KEY (required)
S3_BUCKET (required)
SCRATCH_ROOT (default: /data/scratch)
MODELS_ROOT (default: /data/models)
```

**Existing pattern**: Check current config.py for the existing pattern (likely reads from os.environ with defaults).

---

## Prompt 3: Update Transfer Worker for S3 Upload

**Context**: Transfer worker currently pulls tar archives from AWS EC2 and places them on shared storage. Now it should upload to S3 and enqueue a job with the S3 key.

**Requirements**:
1. After successful SCP transfer from EC2, upload the archive to S3
2. Enqueue job to `queue:unpack` with new payload format
3. Delete local archive after successful S3 upload (transfer worker doesn't need to keep it)

**Current flow** (preserve):
- SCP from EC2 to local temp location
- (existing) push to `list:unpack` queue

**New flow**:
- SCP from EC2 to local temp location
- Upload to S3 using `s3_utils.upload_archive()`
- Push to `queue:unpack` with JSON payload:
  ```json
  {
    "batch_id": "<batch_id>",
    "s3_key": "archives/<batch_id>.tar",
    "file_count": <count if known>,
    "transferred_at": "<ISO timestamp>"
  }
  ```
- Delete local temp file

**Notes**: 
- Review existing transfer_worker.py to understand current implementation
- Batch ID generation should remain unchanged
- Queue name may be `list:unpack` or `queue:unpack` - match existing convention

---

## Prompt 4: Update Unpack Worker for S3 Pull + Batch Tracking

**Context**: Unpack worker currently reads tar archives from shared storage path. Now it should pull from S3 to local scratch, process, and set up batch tracking for GPU workers.

**Requirements**:
1. Parse new job payload format (JSON with s3_key)
2. Download archive from S3 to scratch directory
3. Extract and process in scratch directory
4. **Set up batch tracking keys in Redis for GPU worker completion detection**
5. After successful processing, clean up only the archive file (keep opus)

**Current flow** (understand first):
- Pop job from `list:unpack` (currently a path?)
- Extract tar
- Convert MP3 → Opus
- Push to `list:transcribe`

**New flow**:
- Pop job from `queue:unpack` (JSON payload)
- Download archive: `s3_utils.download_archive(job['s3_key'], job['batch_id'])`
- Extract tar to same scratch directory
- Convert MP3 → Opus (files stay in scratch)
- **Set batch tracking keys**:
  ```python
  redis.set(f"batch:{batch_id}:total", len(opus_files))
  redis.set(f"batch:{batch_id}:processed", 0)
  redis.set(f"batch:{batch_id}:s3_key", job['s3_key'])
  ```
- Push to `queue:transcribe` with batch_id in payload:
  ```json
  {
    "batch_id": "20250622-143052",
    "audio_id": null,
    "opus_path": "/data/scratch/20250622-143052/file001.opus",
    "original_filename": "file001.mp3"
  }
  ```
- Delete archive.tar from scratch (keep opus files for GPU worker)

**Notes**:
- Scratch directory: `/data/scratch/{batch_id}/`
- Extracted files go into the same batch scratch directory
- Opus files will be at paths like `/data/scratch/{batch_id}/*.opus`
- `audio_id` is null here - GPU worker assigns it when inserting to DB

---

## Prompt 5: Update GPU Worker for S3 Upload + Batch Cleanup

**Context**: GPU worker processes audio files. After successful transcription/classification:
1. Upload the processed Opus file to S3 for long-term storage
2. Track batch progress with Redis atomic counters
3. When batch completes, clean up the scratch directory

**Requirements**:

1. **Opus upload to S3**: After successful processing, upload opus to `processed/{date}/{audio_id}.opus`
2. **Batch tracking**: Use Redis INCR for atomic progress counting
3. **Batch completion**: When `processed >= total`, clean up scratch
4. **Schema update**: Add `s3_opus_path` column to `audio_files` table

**Redis batch tracking keys** (set by unpack worker):
```
batch:{batch_id}:total     = 847   # Total files in batch
batch:{batch_id}:processed = 0     # Incremented by GPU worker
batch:{batch_id}:s3_key    = "archives/..."  # Source archive reference
```

**GPU worker flow**:
```python
def process_item(item):
    # ... transcribe + classify (existing) ...
    
    # Upload opus to S3
    s3_opus_key = upload_opus(opus_path, audio_id, date_str)
    update_audio_s3_path(audio_id, s3_opus_key)
    
    # Atomic increment
    processed = redis.incr(f"batch:{batch_id}:processed")
    total = int(redis.get(f"batch:{batch_id}:total") or 0)
    
    # Only one worker sees completion
    if processed >= total:
        cleanup_scratch(batch_id)
        # Clean up Redis keys
```

**Race condition handling**: Redis INCR is atomic - only one GPU worker will see the condition `processed >= total` as true.

**Schema addition**:
```sql
ALTER TABLE audio_files ADD COLUMN s3_opus_path TEXT;
```

**Edge cases to handle**:
- Batch already cleaned (scratch dir doesn't exist) - idempotent cleanup
- Partial failures - some files fail, batch never "completes"
- Consider: failed items decrement total? Or separate failed counter?

---

## Prompt 6: Ansible - GPU Worker Volume Tasks

**Context**: Each GPU VM needs a 100GB Cinder volume mounted at `/data` with `models/` and `scratch/` directories.

**Requirements**:
1. Create `roles/gpu_worker/tasks/volume.yml`
2. Include from main.yml
3. Handle volume at `/dev/vdb` (standard Cinder attachment point)

**Tasks needed**:
1. Create ext4 filesystem on /dev/vdb (idempotent - skip if already formatted)
2. Mount at /data (persistent via /etc/fstab)
3. Create /data/models and /data/scratch directories
4. Set ownership to app user

**Ansible modules**:
- `filesystem` - for creating ext4
- `mount` - for mounting with state: mounted
- `file` - for creating directories

**Variables expected**:
- `app_user` - owner of directories (from group_vars)

---

## Prompt 7: Ansible - Model Sync Tasks

**Context**: Models (~50GB) need to be synced from orchestrator to each GPU worker's local volume.

**Requirements**:
1. Create `roles/gpu_worker/tasks/models.yml`
2. Include from main.yml (after volume.yml)
3. Sync models from orchestrator to /data/models/

**Approach options**:

Option A - Ansible synchronize (rsync):
```yaml
- name: Sync models from orchestrator
  synchronize:
    src: "{{ model_source_path }}/"
    dest: /data/models/
    mode: pull
  delegate_to: "{{ inventory_hostname }}"
```

Option B - Use orchestrator as rsync source:
- Requires orchestrator to have models mounted
- More complex delegation

**Variables needed**:
- `model_source_path` - path on orchestrator where models live
- Consider: `model_source_host` if not orchestrator

**Notes**:
- This is slow (~50GB), should only run on initial setup or explicit sync
- Consider adding a tag like `models` to skip during normal deploys
- Add a standalone playbook `playbooks/sync-models.yml` for manual resync

---

## Prompt 8: Ansible - S3 Configuration Variables

**Context**: S3 credentials need to be added to vault and exposed as environment variables to workers.

**Requirements**:
1. Add to `group_vars/vault.yml` (encrypted):
   - `vault_s3_access_key`
   - `vault_s3_secret_key`

2. Add to `group_vars/all.yml` or `group_vars/gpu_workers.yml`:
   - `s3_endpoint`
   - `s3_bucket`

3. Update worker service templates to include S3 env vars

**Worker service environment** (systemd):
```ini
Environment=S3_ENDPOINT={{ s3_endpoint }}
Environment=S3_ACCESS_KEY={{ vault_s3_access_key }}
Environment=S3_SECRET_KEY={{ vault_s3_secret_key }}
Environment=S3_BUCKET={{ s3_bucket }}
Environment=SCRATCH_ROOT=/data/scratch
Environment=MODELS_ROOT=/data/models
```

**Files to update**:
- `group_vars/vault.yml` - add secrets
- `group_vars/all.yml` - add non-secret S3 config
- `roles/gpu_worker/templates/gpu-worker.service.j2` - add env vars
- `roles/gpu_worker/templates/unpack-worker.service.j2` - add env vars
- `roles/transfer/templates/transfer-worker.service.j2` - add env vars

---

## Prompt 9: Ansible - Setup New Worker Playbook Update

**Context**: `playbooks/setup-new-worker.yml` needs to include volume setup and model sync for new GPU workers.

**Requirements**:
1. Review existing setup-new-worker.yml
2. Ensure it includes the new volume.yml and models.yml tasks
3. Should work with `--limit gpu-04` for single new worker

**Expected flow**:
1. Base system setup (packages, users)
2. GPU drivers / CUDA
3. **Volume setup** (format, mount, directories)
4. **Model sync** (from orchestrator)
5. Pipeline code deployment
6. Service configuration and start

**Notes**:
- Volume setup should be idempotent (safe to re-run)
- Model sync is slow - consider making it optional via tag

---

## Prompt 10: Ansible - Sync Models Playbook

**Context**: Standalone playbook for re-syncing models to GPU workers (after model updates).

**Requirements**:
1. Create `playbooks/sync-models.yml`
2. Should sync models from source to all GPU workers (or subset with --limit)
3. Should restart workers after sync to pick up new models

**Playbook structure**:
```yaml
- name: Sync models to GPU workers
  hosts: gpu_workers
  tasks:
    - name: Sync models
      # ... synchronize task
    
    - name: Restart workers to load new models
      systemd:
        name: "{{ item }}"
        state: restarted
      loop:
        - gpu-worker
        - unpack-worker
```

---

## Implementation Order

Suggested order for implementation:

1. **Prompt 1** (s3_utils.py) - Foundation, can test independently
2. **Prompt 2** (config.py) - Quick addition
3. **Prompt 6** (Ansible volume.yml) - Infrastructure prep
4. **Prompt 8** (Ansible S3 vars) - Configuration prep
5. **Prompt 7** (Ansible models.yml) - Model sync tasks
6. **Prompt 9** (setup-new-worker update) - Wire up Ansible
7. **Prompt 10** (sync-models playbook) - Utility playbook
8. **Prompt 3** (transfer_worker) - First code change
9. **Prompt 4** (unpack_worker) - Second code change
10. **Prompt 5** (GPU worker cleanup) - Final code change

This order allows:
- Testing S3 utilities independently
- Preparing infrastructure before code changes
- Incremental deployment (can deploy infra, then code)