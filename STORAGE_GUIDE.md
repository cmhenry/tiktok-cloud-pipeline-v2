# Storage Infrastructure Guide

## Overview

Two storage changes to eliminate orchestration VM as file-serving bottleneck:

1. **Models**: Replicate to local Cinder volumes on each GPU VM
2. **Audio**: Use S3-compatible object storage; workers pull archives to local scratch

---

## Volume Setup (Per GPU VM)

Single 100GB Cinder volume mounted at `/data`:

```
/data
├── models/      # ~50GB - WhisperX, Gemma-2-9B
└── scratch/     # ~40GB - archive download, extraction, processing
```

### Ansible Tasks

```yaml
# Format and mount volume (assumes /dev/vdb)
- name: Create filesystem
  filesystem:
    fstype: ext4
    dev: /dev/vdb

- name: Mount data volume
  mount:
    path: /data
    src: /dev/vdb
    fstype: ext4
    state: mounted

- name: Create directories
  file:
    path: "{{ item }}"
    state: directory
  loop:
    - /data/models
    - /data/scratch

# Sync models from source (one-time or on deployment)
- name: Sync models
  synchronize:
    src: "{{ model_source_path }}/"
    dest: /data/models/
    mode: pull
```

---

## S3 Audio Flow

```
EC2 → Transfer Worker → S3 (archives/{batch_id}.tar)
                              ↓
                    Redis job enqueued with S3 key
                              ↓
                    GPU Worker pulls to /data/scratch/{batch_id}/
                              ↓
                    Process → Results to Postgres → Cleanup scratch
```

---

## Transfer Worker S3 Modification

### Requirements

- Upload archive directly to S3 after transfer from EC2
- S3 key format: `archives/{batch_id}.tar`
- Enqueue job with S3 key reference

### Interface

```python
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

def upload_archive(local_path: Path, batch_id: str) -> str:
    """Upload archive to S3, return S3 key."""
    s3_key = f"archives/{batch_id}.tar"
    client = get_s3_client()
    client.upload_file(str(local_path), os.environ['S3_BUCKET'], s3_key)
    return s3_key
```

### Job Payload

```python
job = {
    "batch_id": batch_id,
    "s3_key": f"archives/{batch_id}.tar",
    "file_count": file_count,  # if known
    "transferred_at": datetime.utcnow().isoformat()
}
redis_client.rpush("queue:unpack", json.dumps(job))
```

---

## GPU Worker S3 Pull

### Interface

```python
def download_archive(s3_key: str, batch_id: str) -> Path:
    """Download archive from S3 to local scratch."""
    scratch_dir = Path("/data/scratch") / batch_id
    scratch_dir.mkdir(exist_ok=True)
    
    local_path = scratch_dir / "archive.tar"
    client = get_s3_client()
    client.download_file(os.environ['S3_BUCKET'], s3_key, str(local_path))
    return local_path

def cleanup_scratch(batch_id: str):
    """Remove batch scratch directory after processing."""
    scratch_dir = Path("/data/scratch") / batch_id
    shutil.rmtree(scratch_dir, ignore_errors=True)
```

---

## Configuration

Environment variables for S3:

```bash
S3_ENDPOINT=https://swift.example.edu:8080
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_BUCKET=audio-archives
```

Add to existing config management pattern.

---

## Orchestration VM Changes

- No longer mounts audio storage volume
- Runs Redis, Postgres, coordinator services only
- Model source volume still mounted for syncing to GPU VMs during provisioning