# Storage Infrastructure: S3 + Local Volumes

## Project Overview

Eliminate the shared cloud volume bottleneck by migrating to S3 object storage for audio archives and local Cinder volumes on GPU workers for models and scratch space.

**Goals:**
- Remove orchestrator VM as file-serving bottleneck
- Enable horizontal scaling of GPU workers without shared storage contention
- Preserve processed Opus files in S3 for historical access
- Maintain batch-level tracking for reliable scratch cleanup

**Architecture Change:**
```
BEFORE: EC2 → Transfer → Shared Volume → Unpack → Shared Volume → GPU Worker
AFTER:  EC2 → Transfer → S3 (archives) → GPU Worker (local scratch) → S3 (processed)
```

**Storage Layout:**

| Location | Purpose | Size |
|----------|---------|------|
| S3 `archives/` | Incoming tar archives | ~1TB/day, 7-day retention |
| S3 `processed/` | Preserved Opus files | Multi-TB, long-term |
| `/data/models/` | WhisperX, Gemma-2-9B | ~50GB per GPU VM |
| `/data/scratch/` | Temporary processing | ~40GB per GPU VM |

**S3 Key Structure:**
```
audio-pipeline/
├── archives/{batch_id}.tar           # Incoming archives (transfer worker uploads)
└── processed/{date}/{audio_id}.opus  # Preserved opus (GPU worker uploads after success)
```

## Current Status

- [x] S3 utilities module
- [x] Configuration updates
- [ ] Transfer worker S3 upload
- [ ] Unpack worker S3 pull
- [ ] GPU worker S3 upload + scratch cleanup
- [ ] Ansible: GPU volume tasks
- [ ] Ansible: Model sync tasks
- [ ] Ansible: S3 credentials
- [ ] Ansible: Playbook updates

---

## Phase 1: S3 Utilities Module

### 1.1 S3 Client Module

```python
# s3_utils.py
import os
import shutil
from pathlib import Path
from datetime import datetime
import boto3
from botocore.config import Config

def get_s3_client():
    """S3 client configured for OpenStack Swift/S3-compatible endpoint."""
    return boto3.client(
        's3',
        endpoint_url=os.environ['S3_ENDPOINT'],
        aws_access_key_id=os.environ['S3_ACCESS_KEY'],
        aws_secret_access_key=os.environ['S3_SECRET_KEY'],
        config=Config(signature_version='s3v4')
    )

# Archive operations (transfer worker)
def upload_archive(local_path: Path, batch_id: str) -> str:
    """Upload tar archive to S3. Returns S3 key."""
    s3_key = f"archives/{batch_id}.tar"
    # Upload with multipart for large files
    pass

# Archive operations (unpack worker)  
def download_archive(s3_key: str, batch_id: str) -> Path:
    """Download archive from S3 to local scratch. Returns local path."""
    # Download to /data/scratch/{batch_id}/archive.tar
    pass

# Processed file operations (GPU worker)
def upload_opus(local_path: Path, audio_id: int, date: str) -> str:
    """Upload processed opus file to S3. Returns S3 key."""
    s3_key = f"processed/{date}/{audio_id}.opus"
    pass

def delete_archive(s3_key: str):
    """Delete archive from S3 after successful batch processing (optional)."""
    pass

# Scratch management
def cleanup_scratch(batch_id: str):
    """Remove batch scratch directory after processing complete."""
    scratch_dir = Path(os.environ.get('SCRATCH_ROOT', '/data/scratch')) / batch_id
    shutil.rmtree(scratch_dir, ignore_errors=True)
```

### 1.2 Configuration Updates

Add to `config.py`:

```python
# S3 Configuration
S3 = {
    "ENDPOINT": os.getenv("S3_ENDPOINT"),
    "ACCESS_KEY": os.getenv("S3_ACCESS_KEY"),
    "SECRET_KEY": os.getenv("S3_SECRET_KEY"),
    "BUCKET": os.getenv("S3_BUCKET", "audio-pipeline"),
    "ARCHIVE_PREFIX": "archives/",
    "PROCESSED_PREFIX": "processed/",
}

# Local storage (GPU workers)
LOCAL = {
    "SCRATCH_ROOT": Path(os.getenv("SCRATCH_ROOT", "/data/scratch")),
    "MODELS_ROOT": Path(os.getenv("MODELS_ROOT", "/data/models")),
}
```

### 1.3 Deliverables
- [x] `s3_utils.py` - S3 client with all operations
- [x] `config.py` updates - S3 and local storage config
- [x] `requirements.txt` - Added boto3 dependency
- [ ] Unit tests for S3 operations (mock or MinIO)

---

## Phase 2: Transfer Worker Updates

### 2.1 S3 Upload Integration

Modify `transfer_sounds.py` to upload archives to S3 instead of shared volume:

```python
# After successful SCP from EC2:
from s3_utils import upload_archive

# Current: move to shared volume
# shutil.move(local_tar, PATHS["INCOMING_DIR"] / tar_name)

# New: upload to S3
s3_key = upload_archive(local_tar, batch_id)

# Enqueue job with S3 key
job = {
    "batch_id": batch_id,
    "s3_key": s3_key,
    "file_count": file_count,
    "transferred_at": datetime.utcnow().isoformat(),
}
redis_client.rpush(REDIS["QUEUES"]["UNPACK"], json.dumps(job))

# Delete local temp file
local_tar.unlink()
```

### 2.2 Job Payload Format

```json
{
    "batch_id": "20250622-143052-a1b2c3",
    "s3_key": "archives/20250622-143052-a1b2c3.tar",
    "file_count": 847,
    "transferred_at": "2025-06-22T14:30:52Z"
}
```

### 2.3 Deliverables
- [ ] Update `transfer_sounds.py` with S3 upload
- [ ] New job payload format with s3_key
- [ ] Remove shared volume path references
- [ ] Verify temp file cleanup after upload

---

## Phase 3: Unpack Worker Updates

### 3.1 S3 Pull Integration

Modify `unpack_worker.py` to pull archives from S3:

```python
from s3_utils import download_archive

def process_job(job: dict):
    batch_id = job["batch_id"]
    s3_key = job["s3_key"]
    
    # Download archive to scratch
    archive_path = download_archive(s3_key, batch_id)
    scratch_dir = archive_path.parent  # /data/scratch/{batch_id}/
    
    # Extract tar (existing logic, new location)
    extract_tar(archive_path, scratch_dir)
    
    # Convert MP3 → Opus (existing logic, new location)
    opus_files = convert_to_opus(scratch_dir)
    
    # Enqueue transcription jobs with batch tracking
    for opus_path in opus_files:
        job = {
            "batch_id": batch_id,
            "opus_path": str(opus_path),
            "original_filename": opus_path.stem + ".mp3",
        }
        redis_client.rpush(REDIS["QUEUES"]["TRANSCRIBE"], json.dumps(job))
    
    # Track batch size for completion detection
    redis_client.set(f"batch:{batch_id}:total", len(opus_files))
    redis_client.set(f"batch:{batch_id}:processed", 0)
    
    # Delete archive file from scratch (keep opus files)
    archive_path.unlink()
```

### 3.2 Batch Tracking Keys

```
batch:{batch_id}:total     = 847   # Set by unpack worker
batch:{batch_id}:processed = 0     # Incremented by GPU worker
batch:{batch_id}:s3_key    = "archives/..."  # For cleanup reference
```

### 3.3 Deliverables
- [ ] Update `unpack_worker.py` with S3 download
- [ ] Batch tracking keys in Redis
- [ ] Remove shared volume path references
- [ ] Extraction to scratch directory

---

## Phase 4: GPU Worker Updates

### 4.1 Opus Upload + Batch Completion

Modify `gpu_worker.py` to upload opus files and track batch completion:

```python
from s3_utils import upload_opus, cleanup_scratch

def process_item(self, item: dict):
    batch_id = item["batch_id"]
    opus_path = Path(item["opus_path"])
    
    try:
        # Existing: transcribe + classify
        transcript = self.transcribe(str(opus_path))
        classification = self.classify(transcript["text"])
        
        # Insert to DB (existing)
        audio_id = insert_audio_file(...)
        insert_transcript(audio_id, ...)
        insert_classification(audio_id, ...)
        
        # NEW: Upload opus to S3 processed storage
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        s3_opus_key = upload_opus(opus_path, audio_id, date_str)
        
        # Update audio_files record with S3 path
        update_audio_s3_path(audio_id, s3_opus_key)
        
        # Track batch progress
        processed = redis_client.incr(f"batch:{batch_id}:processed")
        total = int(redis_client.get(f"batch:{batch_id}:total") or 0)
        
        if processed >= total:
            self.complete_batch(batch_id)
            
    except Exception as e:
        # ... error handling
        pass

def complete_batch(self, batch_id: str):
    """Called when all items in batch are processed."""
    logger.info(f"Batch {batch_id} complete, cleaning up scratch")
    
    # Clean up scratch directory
    cleanup_scratch(batch_id)
    
    # Clean up Redis keys
    redis_client.delete(f"batch:{batch_id}:total")
    redis_client.delete(f"batch:{batch_id}:processed")
    redis_client.delete(f"batch:{batch_id}:s3_key")
    
    # Optional: delete source archive from S3
    # s3_key = redis_client.get(f"batch:{batch_id}:s3_key")
    # delete_archive(s3_key)
```

### 4.2 Schema Update

Add S3 path column to audio_files:

```sql
ALTER TABLE audio_files ADD COLUMN s3_opus_path TEXT;
-- Replaces local opus_path for new records
```

### 4.3 Race Condition Handling

Multiple GPU workers may process same batch. Use Redis INCR for atomic counting:

```python
# Atomic increment - returns new value
processed = redis_client.incr(f"batch:{batch_id}:processed")

# Only one worker will see processed == total
if processed >= total:
    self.complete_batch(batch_id)
```

### 4.4 Deliverables
- [ ] Update `gpu_worker.py` with S3 opus upload
- [ ] Batch completion detection with Redis atomic counters
- [ ] Scratch cleanup on batch completion
- [ ] Schema update for s3_opus_path
- [ ] Handle edge cases (batch already cleaned, partial failures)

---

## Phase 5: Ansible Infrastructure

### 5.1 GPU Worker Volume Tasks

```yaml
# roles/gpu_worker/tasks/volume.yml

- name: Check if filesystem exists
  command: blkid /dev/vdb
  register: blkid_result
  failed_when: false
  changed_when: false

- name: Create filesystem on data volume
  filesystem:
    fstype: ext4
    dev: /dev/vdb
  when: blkid_result.rc != 0

- name: Mount data volume
  mount:
    path: /data
    src: /dev/vdb
    fstype: ext4
    state: mounted

- name: Create data directories
  file:
    path: "{{ item }}"
    state: directory
    owner: "{{ app_user }}"
    group: "{{ app_user }}"
    mode: '0755'
  loop:
    - /data/models
    - /data/scratch
```

### 5.2 Model Sync Tasks

```yaml
# roles/gpu_worker/tasks/models.yml

- name: Sync WhisperX model
  synchronize:
    src: "{{ model_source_host }}:{{ whisperx_model_path }}/"
    dest: /data/models/whisperx/
    mode: pull
  delegate_to: "{{ inventory_hostname }}"
  tags: [models]

- name: Sync Gemma + CoPE-A adapter
  synchronize:
    src: "{{ model_source_host }}:{{ gemma_model_path }}/"
    dest: /data/models/gemma/
    mode: pull
  delegate_to: "{{ inventory_hostname }}"
  tags: [models]
```

### 5.3 S3 Credentials

```yaml
# group_vars/vault.yml (encrypted)
vault_s3_access_key: "ACCESS_KEY_HERE"
vault_s3_secret_key: "SECRET_KEY_HERE"

# group_vars/all.yml
s3_endpoint: "https://swift.example.edu:8080"
s3_bucket: "audio-pipeline"

# Worker service environment
s3_access_key: "{{ vault_s3_access_key }}"
s3_secret_key: "{{ vault_s3_secret_key }}"
```

### 5.4 Service Template Updates

```ini
# roles/gpu_worker/templates/gpu-worker.service.j2
[Service]
# ... existing ...
Environment=S3_ENDPOINT={{ s3_endpoint }}
Environment=S3_ACCESS_KEY={{ s3_access_key }}
Environment=S3_SECRET_KEY={{ s3_secret_key }}
Environment=S3_BUCKET={{ s3_bucket }}
Environment=SCRATCH_ROOT=/data/scratch
Environment=MODELS_ROOT=/data/models
```

### 5.5 Playbook Updates

```yaml
# playbooks/setup-new-worker.yml
- name: Setup new GPU worker
  hosts: gpu_workers
  roles:
    - common
    - role: gpu_worker
      tags: [gpu]
  tasks:
    - include_tasks: roles/gpu_worker/tasks/volume.yml
      tags: [volume]
    - include_tasks: roles/gpu_worker/tasks/models.yml
      tags: [models]
```

```yaml
# playbooks/sync-models.yml
- name: Sync models to GPU workers
  hosts: gpu_workers
  tasks:
    - include_tasks: roles/gpu_worker/tasks/models.yml
    - name: Restart workers
      systemd:
        name: "{{ item }}"
        state: restarted
      loop:
        - gpu-worker
        - unpack-worker
```

### 5.6 Deliverables
- [ ] `roles/gpu_worker/tasks/volume.yml`
- [ ] `roles/gpu_worker/tasks/models.yml`
- [ ] `group_vars/vault.yml` - S3 credentials
- [ ] `group_vars/all.yml` - S3 endpoint/bucket
- [ ] Update service templates with S3 env vars
- [ ] `playbooks/setup-new-worker.yml` updates
- [ ] `playbooks/sync-models.yml` - standalone model sync

---

## Phase 6: Health Checks + Monitoring

### 6.1 S3 Connectivity Checks

```bash
# Check S3 from transfer worker
python3 -c "from s3_utils import get_s3_client; print(get_s3_client().list_buckets())"

# Check S3 from GPU worker  
aws s3 ls s3://${S3_BUCKET}/archives/ --endpoint-url ${S3_ENDPOINT} | head -5
```

### 6.2 Scratch Space Monitoring

```bash
# Check scratch usage per worker
ansible gpu_workers -a "du -sh /data/scratch/* 2>/dev/null | tail -10"

# Find old scratch directories (may indicate stuck batches)
ansible gpu_workers -a "find /data/scratch -maxdepth 1 -type d -mmin +120"
```

### 6.3 Batch Tracking Monitoring

```bash
# Check active batches
redis-cli KEYS "batch:*:total"

# Check specific batch progress
redis-cli MGET batch:20250622-143052:total batch:20250622-143052:processed
```

### 6.4 Deliverables
- [ ] Update `deploy/check-health.sh` with S3 checks
- [ ] Add scratch monitoring queries
- [ ] Add batch tracking monitoring
- [ ] Alert on stuck batches (>2 hours incomplete)

---

## Notes for Implementation

**Implementation order:**
1. Phase 1 (s3_utils, config) - Foundation, can test with MinIO locally
2. Phase 5 (Ansible infra) - Prepare infrastructure before code deploy
3. Phase 2 (transfer worker) - First code change, starts populating S3
4. Phase 3 (unpack worker) - Second code change
5. Phase 4 (GPU worker) - Final code change, enables full flow
6. Phase 6 (health checks) - Operational readiness

**Testing strategy:**
- Use MinIO locally to test S3 operations
- Test batch completion logic with small test batches (3-5 files)
- Verify scratch cleanup triggers correctly
- Test concurrent GPU workers processing same batch

**Key decisions made:**
- Opus files preserved in S3 `processed/{date}/{audio_id}.opus`
- Batch-level scratch cleanup (not age-based) for reliability
- Redis atomic counters for batch completion tracking
- Source archives retained in S3 (optional deletion after batch complete)

**Gotchas:**
- S3v4 signature required for OpenStack Swift
- Multipart upload for large tar files (>100MB)
- Redis INCR is atomic - safe for concurrent workers
- Scratch cleanup should be idempotent (directory may not exist)