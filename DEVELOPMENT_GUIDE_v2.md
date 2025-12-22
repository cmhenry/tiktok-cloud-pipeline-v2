# Development Guide: Audio Pipeline + RA App

This guide covers the two parallel development streams for the content moderation research infrastructure:

1. **Audio Processing Pipeline** — ingestion, transcription, classification
2. **RA Flask App** — research assistant content review interface

Both are deployed via Ansible to a shared infrastructure.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Storage Infrastructure](#storage-infrastructure)
3. [Development Environment Setup](#development-environment-setup)
4. [Stream 1: Audio Processing Pipeline](#stream-1-audio-processing-pipeline)
5. [Stream 2: RA Flask App](#stream-2-ra-flask-app)
6. [Database Schema Integration](#database-schema-integration)
7. [Ansible Deployment](#ansible-deployment)
8. [Common Operations](#common-operations)
9. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                          S3-Compatible Object Storage                             │
│                       (archives/{batch_id}.tar)                                   │
└──────────────────────────────────────────────────────────────────────────────────┘
        ▲                                   │
        │ upload                            │ pull
        │                                   ▼
┌───────┴───────┐             ┌────────────────────────────────────────────────────┐
│   Transfer    │             │              GPU Workers (×N)                       │
│    Worker     │             │                                                     │
│               │             │   ┌─────────────────────────────────────────────┐   │
│  • SCP from   │             │   │ Local 100GB Cinder Volume (/data)           │   │
│    AWS EC2    │             │   │ ├── models/     (~50GB WhisperX, Gemma-2-9B)│   │
│  • Upload to  │             │   │ └── scratch/    (~40GB archive processing)  │   │
│    S3         │             │   └─────────────────────────────────────────────┘   │
│  • Queue job  │             │                                                     │
│    w/ S3 key  │             │   ┌──────────┐          ┌─────────────────────┐     │
└───────────────┘             │   │ Unpack   │    ───►  │   GPU Worker        │     │
                              │   │ Worker   │          │                     │     │
                              │   │          │          │  • WhisperX         │     │
                              │   │ • S3 pull│          │  • CoPE-A (Gemma)   │     │
                              │   │ • tar    │          │  • Results → PG     │     │
                              │   │ • ffmpeg │          │  • Cleanup scratch  │     │
                              │   └──────────┘          └─────────────────────┘     │
                              └────────────────────────────────────────────────────┘
                                                    │
                                                    ▼ results
                              ┌────────────────────────────────────────────────────┐
                              │              Orchestrator VM                        │
                              │                                                     │
                              │   ┌─────────┐   ┌──────────┐   ┌─────────────────┐  │
                              │   │  Redis  │   │ Postgres │   │    RA App       │  │
                              │   │ (queues)│   │ (results)│   │    (Flask)      │  │
                              │   └─────────┘   └──────────┘   └─────────────────┘  │
                              │                                                     │
                              │   No local audio storage - coordination only        │
                              └────────────────────────────────────────────────────┘
```

**Data Flow:**
1. Transfer worker pulls `.tar` archives from AWS EC2 → uploads to S3 → enqueues job with S3 key to `queue:unpack`
2. GPU VM unpack worker pulls archive from S3 to `/data/scratch/{batch_id}/` → extracts, converts MP3→Opus → enqueues to `queue:transcribe`
3. GPU worker transcribes + classifies using models from `/data/models/` → writes to Postgres → cleans up scratch
4. RA App queries Postgres for flagged content → presents to research assistants

---

## Storage Infrastructure

### Design Principles

Two storage changes eliminate the orchestrator VM as a file-serving bottleneck:

1. **Models**: Replicated to local Cinder volumes on each GPU VM during provisioning
2. **Audio**: S3-compatible object storage; workers pull archives to local scratch space

### GPU VM Volume Layout

Each GPU VM has a single 100GB Cinder volume mounted at `/data`:

```
/data
├── models/      # ~50GB - WhisperX, Gemma-2-9B (synced during provisioning)
└── scratch/     # ~40GB - archive download, extraction, processing
```

### S3 Audio Flow

```
EC2 → Transfer Worker → S3 (archives/{batch_id}.tar)
                              │
                    Redis job enqueued with S3 key
                              │
                    GPU Worker pulls to /data/scratch/{batch_id}/
                              │
                    Process → Results to Postgres → Cleanup scratch
```

### S3 Configuration

Environment variables for S3-compatible storage:

```bash
S3_ENDPOINT=https://swift.example.edu:8080
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_BUCKET=audio-archives
```

### Orchestrator VM Changes

The orchestrator no longer mounts the audio storage volume. It runs only:
- Redis (queue coordination)
- Postgres (results storage)
- RA Flask App (content review interface)

Model source volume is still mounted for syncing to GPU VMs during provisioning.

---

## Development Environment Setup

### Prerequisites

```bash
# Local machine
pip install ansible

# Verify SSH access to all VMs
ssh ubuntu@<orchestrator-ip>
ssh ubuntu@<gpu-worker-ip>
```

### Clone Repositories

```bash
# Ansible configuration (this project)
git clone <ansible-repo> ~/infrastructure

# Audio pipeline code
git clone <pipeline-repo> ~/audio-pipeline

# RA Flask app
git clone <ra-app-repo> ~/ra-app
```

### Configure Ansible

```bash
cd ~/infrastructure

# Update inventory with your VM IPs
vim inventory/production.yml

# Set sensitive values (database passwords, S3 credentials, etc.)
ansible-vault create group_vars/vault.yml
# Add:
#   vault_postgres_password: "your-secure-password"
#   vault_ra_app_secret_key: "your-secret-key"
#   vault_s3_access_key: "your-s3-access-key"
#   vault_s3_secret_key: "your-s3-secret-key"

# Test connectivity
ansible all -m ping
```

---

## Stream 1: Audio Processing Pipeline

### Repository Structure

```
audio-pipeline/
├── config.py              # Central configuration (reads from env)
├── utils.py               # Shared utilities
├── db.py                  # Database operations
├── s3_utils.py            # S3 client utilities (NEW)
├── workers/
│   ├── __init__.py
│   ├── transfer_worker.py # AWS → S3 transfer
│   ├── unpack_worker.py   # S3 pull + tar extraction + ffmpeg
│   └── gpu_worker.py      # WhisperX + CoPE-A
├── requirements.txt
└── requirements-gpu.txt   # GPU-specific deps (torch, whisperx, etc.)
```

### S3 Utilities Module

```python
# s3_utils.py
import os
import boto3
from botocore.config import Config
from pathlib import Path

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

def download_archive(s3_key: str, batch_id: str) -> Path:
    """Download archive from S3 to local scratch."""
    scratch_dir = Path("/data/scratch") / batch_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    
    local_path = scratch_dir / "archive.tar"
    client = get_s3_client()
    client.download_file(os.environ['S3_BUCKET'], s3_key, str(local_path))
    return local_path

def cleanup_scratch(batch_id: str):
    """Remove batch scratch directory after processing."""
    import shutil
    scratch_dir = Path("/data/scratch") / batch_id
    shutil.rmtree(scratch_dir, ignore_errors=True)
```

### Transfer Worker Job Payload

```python
# After uploading to S3
job = {
    "batch_id": batch_id,
    "s3_key": f"archives/{batch_id}.tar",
    "file_count": file_count,  # if known
    "transferred_at": datetime.utcnow().isoformat()
}
redis_client.rpush("queue:unpack", json.dumps(job))
```

### Local Development

```bash
cd ~/audio-pipeline

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install boto3  # For S3 support

# For GPU development (on a machine with CUDA):
pip install -r requirements-gpu.txt

# Set environment variables for local testing
export REDIS_HOST=localhost
export POSTGRES_HOST=localhost
export POSTGRES_DB=audio_pipeline

# S3 configuration (local testing with MinIO or similar)
export S3_ENDPOINT=http://localhost:9000
export S3_ACCESS_KEY=minioadmin
export S3_SECRET_KEY=minioadmin
export S3_BUCKET=audio-archives

# Local scratch directory (simulates /data on GPU VMs)
export SCRATCH_ROOT=/tmp/scratch
export MODELS_ROOT=/tmp/models
```

### Testing Individual Workers

```bash
# Test transfer worker (uploads to S3)
python -m workers.transfer_worker

# Test unpack worker (pulls from S3)
redis-cli LPUSH queue:unpack '{"batch_id": "test-001", "s3_key": "archives/test-001.tar"}'
python -m workers.unpack_worker

# Test GPU worker with sample audio
redis-cli LPUSH queue:transcribe '{"audio_id": 1, "opus_path": "/data/scratch/test-001/audio.opus"}'
python -m workers.gpu_worker
```

### Key Development Tasks

| Task | File(s) | Notes |
|------|---------|-------|
| Add new queue | `config.py`, worker files | Add to REDIS.QUEUES, update workers |
| Change audio format | `unpack_worker.py` | Modify ffmpeg command |
| Update classification prompt | `gpu_worker.py` | Modify CoPE-A prompt template |
| Add new DB field | `db.py`, `schema.sql` | Add column, update insert functions |
| Modify S3 key format | `s3_utils.py`, workers | Update key generation pattern |

### Deployment

```bash
# Deploy code changes to all GPU workers
cd ~/infrastructure
ansible-playbook playbooks/deploy-pipeline.yml

# Deploy to specific workers only
ansible-playbook playbooks/deploy-pipeline.yml --limit gpu-01,gpu-02

# Check logs after deployment
ansible gpu_workers -a "journalctl -u gpu-worker -n 20 --no-pager"
```

---

## Stream 2: RA Flask App

### Repository Structure

```
ra-app/
├── app/
│   ├── __init__.py        # Flask app factory
│   ├── models.py          # SQLAlchemy models
│   ├── views/
│   │   ├── auth.py        # Login/logout
│   │   ├── queue.py       # Assignment queue
│   │   └── review.py      # Content review interface
│   ├── templates/
│   └── static/
├── migrations/            # Flask-Migrate database migrations
├── config.py
├── requirements.txt
└── gunicorn.conf.py
```

### Local Development

```bash
cd ~/ra-app

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set environment variables
export FLASK_APP=app
export FLASK_ENV=development
export DATABASE_URL=postgresql://pipeline:password@localhost/audio_pipeline
export SECRET_KEY=dev-secret-key

# Run development server
flask run --port 5000

# Or with auto-reload:
flask run --debug
```

### Database Migrations

The RA app shares the database with the pipeline but owns its own tables (users, assignments, labels).

```bash
# Create a new migration after model changes
flask db migrate -m "Add assignment status field"

# Apply migrations locally
flask db upgrade

# View migration history
flask db history
```

### Key Development Tasks

| Task | File(s) | Notes |
|------|---------|-------|
| Add new page | `views/`, `templates/` | Create view + template |
| Change queue logic | `views/queue.py` | Modify assignment algorithm |
| Add user field | `models.py`, then migrate | Update model, create migration |
| Change labeling codebook | `views/review.py`, templates | Update form + options |

### Deployment

```bash
cd ~/infrastructure

# Deploy RA app changes
ansible-playbook playbooks/deploy-ra-app.yml

# This will:
# 1. Pull latest code
# 2. Install new dependencies
# 3. Run database migrations
# 4. Restart gunicorn
```

---

## Database Schema Integration

### Ownership Model

The pipeline and RA app share a Postgres database but own different tables:

**Pipeline owns (read/write):**
- `audio_files` — processed audio metadata
- `transcripts` — WhisperX output
- `classifications` — CoPE-A results

**RA App owns (read/write):**
- `ra_users` — research assistant accounts
- `ra_assignments` — who reviews what
- `ra_labels` — review decisions

**RA App reads (read-only):**
- `audio_files`, `transcripts`, `classifications` — to display content
- `ra_queue` view — pre-filtered flagged content

### Schema Migration Strategy

1. **Pipeline schema changes** → edit `roles/orchestrator/files/schema.sql`, redeploy
2. **RA App schema changes** → use Flask-Migrate in the ra-app repo

### Adding a Shared Field

If the RA app needs a field from the pipeline (e.g., `audio_files.reviewed_at`):

1. Add to `schema.sql` in Ansible
2. Add to `db.py` insert/update functions in pipeline
3. Add to SQLAlchemy model in RA app (read-only)
4. Deploy both: `ansible-playbook playbooks/site.yml --limit orchestrator`

---

## Ansible Deployment

### Role Structure

```
roles/
├── common/              # Shared packages, users, firewall
├── orchestrator/        # Redis, Postgres, RA app
│   └── files/
│       └── schema.sql
├── gpu_worker/          # GPU drivers, CUDA, worker services
│   └── tasks/
│       ├── main.yml
│       ├── volume.yml   # Cinder volume setup
│       └── models.yml   # Model sync from source
└── transfer/            # Transfer worker service
```

### GPU Worker Volume Setup (Ansible Tasks)

```yaml
# roles/gpu_worker/tasks/volume.yml

# Format and mount volume (assumes /dev/vdb)
- name: Create filesystem on data volume
  filesystem:
    fstype: ext4
    dev: /dev/vdb

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
  loop:
    - /data/models
    - /data/scratch

# Sync models from orchestrator (one-time or on deployment)
- name: Sync models from source
  synchronize:
    src: "{{ model_source_path }}/"
    dest: /data/models/
    mode: pull
  delegate_to: "{{ inventory_hostname }}"
```

### Playbook Reference

| Playbook | Use Case |
|----------|----------|
| `site.yml` | Full deployment (first time or major changes) |
| `deploy-pipeline.yml` | Quick pipeline code deploy |
| `deploy-ra-app.yml` | Quick RA app deploy |
| `health-check.yml` | Check status of all services |
| `setup-new-worker.yml` | Add new GPU VM to fleet (includes volume setup) |
| `sync-models.yml` | Re-sync models to GPU workers |

### Common Commands

```bash
cd ~/infrastructure

# Full deployment (first time)
ansible-playbook playbooks/site.yml

# Quick code deploy
ansible-playbook playbooks/deploy-pipeline.yml
ansible-playbook playbooks/deploy-ra-app.yml

# Health check
ansible-playbook playbooks/health-check.yml

# Ad-hoc commands
ansible gpu_workers -a "systemctl status gpu-worker"
ansible gpu_workers -a "nvidia-smi"
ansible gpu_workers -a "df -h /data"  # Check volume usage
ansible orchestrator -a "redis-cli LLEN queue:transcribe"

# Check S3 connectivity
ansible gpu_workers -a "python3 -c \"import boto3; print('S3 OK')\""

# Restart all GPU workers
ansible gpu_workers -m systemd -a "name=gpu-worker state=restarted" --become

# View logs
ansible gpu-01 -a "journalctl -u gpu-worker -n 50 --no-pager"

# Sync models to specific worker
ansible-playbook playbooks/sync-models.yml --limit gpu-01
```

### Adding a New GPU Worker

1. Provision VM with GPU and attach 100GB Cinder volume
2. Add to inventory:
   ```yaml
   # inventory/production.yml
   gpu_workers:
     hosts:
       # ... existing
       gpu-04:
         ansible_host: 10.0.1.4
   ```
3. Run setup (includes volume formatting and model sync):
   ```bash
   ansible-playbook playbooks/setup-new-worker.yml --limit gpu-04
   ```

### Secrets Management

```bash
# View encrypted vars
ansible-vault view group_vars/vault.yml

# Edit encrypted vars (add S3 credentials)
ansible-vault edit group_vars/vault.yml

# Contents should include:
#   vault_postgres_password: "..."
#   vault_ra_app_secret_key: "..."
#   vault_s3_access_key: "..."
#   vault_s3_secret_key: "..."

# Run playbook with vault password
ansible-playbook playbooks/site.yml --ask-vault-pass
```

---

## Common Operations

### Daily Health Check

```bash
# Run automated health check
ansible-playbook playbooks/health-check.yml

# Quick queue check
ansible orchestrator -a "redis-cli LLEN queue:unpack"
ansible orchestrator -a "redis-cli LLEN queue:transcribe"

# Check S3 bucket status
ansible transfer -a "aws s3 ls s3://audio-archives/archives/ --endpoint-url \$S3_ENDPOINT | wc -l"

# Check scratch usage on GPU workers
ansible gpu_workers -a "du -sh /data/scratch/*" --ignore-errors

# Check flagged count for RA window
ansible orchestrator -a "psql -d audio_pipeline -c \"SELECT COUNT(*) FROM ra_queue\""
```

### Scaling Up for High Volume

```bash
# Add new GPU workers to inventory (ensure Cinder volumes attached), then:
ansible-playbook playbooks/setup-new-worker.yml --limit gpu-04,gpu-05,gpu-06

# Verify all workers are processing
ansible-playbook playbooks/health-check.yml
```

### Emergency: Stop All Workers

```bash
ansible gpu_workers -m systemd -a "name=gpu-worker state=stopped" --become
ansible gpu_workers -m systemd -a "name=unpack-worker state=stopped" --become
ansible transfer -m systemd -a "name=transfer-worker state=stopped" --become
```

### Cleanup Scratch Space

```bash
# If scratch directories are filling up
ansible gpu_workers -a "find /data/scratch -type d -mtime +1 -exec rm -rf {} +" --become
```

### Rollback Deployment

```bash
# Deploy previous version
ansible-playbook playbooks/deploy-pipeline.yml -e "pipeline_version=v1.2.3"

# Or specific commit
ansible-playbook playbooks/deploy-pipeline.yml -e "pipeline_version=abc123def"
```

---

## Troubleshooting

### GPU Worker Won't Start

```bash
# Check logs
ansible gpu-01 -a "journalctl -u gpu-worker -n 100 --no-pager"

# Common issues:
# - CUDA out of memory: reduce BATCH_SIZE in group_vars/gpu_workers.yml
# - Model not found: check /data/models/ exists and has correct files
# - S3 connection failed: verify S3_ENDPOINT and credentials
# - Import error: reinstall requirements

# SSH in and debug
ssh ubuntu@<gpu-01-ip>
cd /opt/audio-pipeline
source venv/bin/activate
python -c "import torch; print(torch.cuda.is_available())"

# Check model files exist
ls -la /data/models/
```

### S3 Connection Issues

```bash
# Test S3 connectivity from transfer worker
ansible transfer -a "python3 -c \"
from s3_utils import get_s3_client
client = get_s3_client()
print(client.list_buckets())
\""

# Test from GPU worker
ansible gpu-01 -a "python3 -c \"
import boto3
from botocore.config import Config
import os
client = boto3.client('s3',
    endpoint_url=os.environ.get('S3_ENDPOINT'),
    aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
    aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
    config=Config(signature_version='s3v4'))
print(client.list_objects_v2(Bucket=os.environ.get('S3_BUCKET'), MaxKeys=1))
\""

# Common issues:
# - Wrong endpoint URL (check https vs http, port)
# - Signature version mismatch (s3v4 required for Swift)
# - Bucket doesn't exist
# - Network/firewall blocking access
```

### Queue Building Up

```bash
# Check which stage is slow
ansible orchestrator -a "redis-cli LLEN queue:unpack"      # Transfer → Unpack
ansible orchestrator -a "redis-cli LLEN queue:transcribe"  # Unpack → GPU

# If queue:unpack is large, check S3 download speed
ansible gpu-01 -a "time aws s3 cp s3://audio-archives/archives/test.tar /tmp/test.tar --endpoint-url \$S3_ENDPOINT"

# If queue:transcribe is large, GPU workers are bottleneck
# Add more GPU workers or check for errors

# Check for failed items
ansible orchestrator -a "redis-cli LLEN queue:failed"
```

### Scratch Space Full

```bash
# Check usage
ansible gpu_workers -a "df -h /data"
ansible gpu_workers -a "du -sh /data/scratch/* 2>/dev/null | head -20"

# Check for stuck batches (old directories)
ansible gpu_workers -a "find /data/scratch -maxdepth 1 -type d -mtime +1"

# Clean up old scratch (careful in production!)
ansible gpu_workers -a "find /data/scratch -type d -mtime +1 -exec rm -rf {} +" --become
```

### Database Connection Issues

```bash
# Test from worker
ansible gpu-01 -a "psql -h <orchestrator-ip> -U pipeline -d audio_pipeline -c 'SELECT 1'"

# Check pg_hba.conf allows connections
ansible orchestrator -a "cat /etc/postgresql/14/main/pg_hba.conf"

# Check PostgreSQL is listening
ansible orchestrator -a "ss -tlnp | grep 5432"
```

### RA App Not Loading

```bash
# Check service status
ansible orchestrator -a "systemctl status ra-app"

# Check logs
ansible orchestrator -a "tail -50 /opt/ra-app/logs/error.log"

# Test gunicorn manually
ssh ubuntu@<orchestrator-ip>
cd /opt/ra-app
source venv/bin/activate
gunicorn --bind 0.0.0.0:8000 "app:create_app()"
```

---

## Development Workflow Summary

### Pipeline Changes

```bash
# 1. Develop locally
cd ~/audio-pipeline
# make changes, test locally with MinIO for S3

# 2. Commit and push
git add -A && git commit -m "Feature: improved batching"
git push origin main

# 3. Deploy to staging
cd ~/infrastructure
ansible-playbook -i inventory/staging.yml playbooks/deploy-pipeline.yml

# 4. Test staging
ansible-playbook -i inventory/staging.yml playbooks/health-check.yml

# 5. Deploy to production
ansible-playbook playbooks/deploy-pipeline.yml
```

### RA App Changes

```bash
# 1. Develop locally
cd ~/ra-app
flask run --debug
# make changes, test locally

# 2. If model changes, create migration
flask db migrate -m "Description"
flask db upgrade  # test locally

# 3. Commit and push
git add -A && git commit -m "Feature: new review interface"
git push origin main

# 4. Deploy (includes migration)
cd ~/infrastructure
ansible-playbook playbooks/deploy-ra-app.yml
```

---

## Getting Help

- **Pipeline issues**: Check GPU worker logs first (`journalctl -u gpu-worker`)
- **S3 issues**: Verify endpoint, credentials, bucket exists
- **RA App issues**: Check gunicorn logs (`/opt/ra-app/logs/error.log`)
- **Ansible issues**: Run with `-vvv` for verbose output
- **Database issues**: Connect directly with `psql` to debug queries

For Claude Code sessions, share:
1. The specific error message
2. Which component is affected (pipeline/RA app/Ansible/S3)
3. Recent changes made
4. Output of `ansible-playbook playbooks/health-check.yml`