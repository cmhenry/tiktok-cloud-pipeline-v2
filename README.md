# Audio Processing Pipeline

A distributed audio processing pipeline for content moderation research. Ingests archives of MP3 audio files, transcribes them with WhisperX, classifies transcripts with CoPE-A, and stores results in PostgreSQL for research assistant review.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         S3-Compatible Object Storage                             │
│                    archives/{batch_id}.tar    processed/{date}/{id}.opus         │
└─────────────────────────────────────────────────────────────────────────────────┘
        ▲                          │                           ▲
        │ upload                   │ download                  │ upload
        │                          ▼                           │
┌───────┴───────┐       ┌─────────────────────────────────────┴───────────────────┐
│   Transfer    │       │                GPU Workers (×N)                          │
│    Worker     │       │                                                          │
│               │       │   ┌─────────────────────────────────────────────────┐    │
│  • SCP from   │       │   │ Unpack Worker         GPU Worker                │    │
│    AWS EC2    │       │   │ • S3 pull             • WhisperX transcription  │    │
│  • Upload to  │       │   │ • tar extraction      • CoPE-A classification   │    │
│    S3         │       │   │ • MP3 → Opus          • Results → PostgreSQL    │    │
│  • Queue job  │       │   │ • Queue jobs          • Opus → S3               │    │
│    (Redis)    │       │   └─────────────────────────────────────────────────┘    │
└───────────────┘       │                                                          │
                        │   Local Storage: /data/scratch (processing)              │
                        │                  /data/models (WhisperX, Gemma-2-9B)     │
                        └──────────────────────────────────────────────────────────┘
                                                    │
                                                    ▼ results
                        ┌──────────────────────────────────────────────────────────┐
                        │                    Orchestrator VM                        │
                        │                                                          │
                        │   ┌─────────┐   ┌──────────┐   ┌───────────────────┐     │
                        │   │  Redis  │   │ Postgres │   │     RA App        │     │
                        │   │ (queues)│   │ (results)│   │ (review interface)│     │
                        │   └─────────┘   └──────────┘   └───────────────────┘     │
                        └──────────────────────────────────────────────────────────┘
```

## Design Goals & Evaluation

### Target Requirements

| Requirement | Target | Current Status |
|-------------|--------|----------------|
| Daily throughput | 20-30 GB | ✅ **Supported** |
| GPU workers | Up to 10 × L4 | ✅ **Supported** |
| Archive formats | .tar, .tar.gz | ✅ **Implemented** |
| Audio conversion | MP3 → Opus | ✅ **Implemented** |
| Transcription | WhisperX large-v2 | ✅ **Implemented** |
| Classification | CoPE-A (Gemma-2-9B) | ✅ **Implemented** |
| Storage | PostgreSQL + S3 | ✅ **Implemented** |
| Parquet metadata | Link via meta_id | ⚠️ **Not implemented** |

### Throughput Analysis

**At 20-30 GB/day (~5,000-7,500 files at 4MB average):**

| Component | Capacity | Requirement | Headroom |
|-----------|----------|-------------|----------|
| Unpack Worker (1×) | 200-400 files/hr | ~300 files/hr | ✅ 1.3× |
| GPU Worker (1× L4) | 150-250 files/hr | — | — |
| GPU Workers (10× L4) | 1,500-2,500 files/hr | ~300 files/hr | ✅ 5-8× |
| S3 bandwidth | >1 Gbps available | ~2.3 Mbps needed | ✅ 400× |
| PostgreSQL | 50,000+ writes/hr | ~2,000 writes/hr | ✅ 25× |
| Redis | 100,000+ ops/sec | ~500 ops/sec | ✅ 200× |

**Conclusion**: Architecture comfortably handles 20-30 GB/day with significant headroom. The bottleneck is GPU transcription (WhisperX), which scales horizontally with additional workers.

### Architecture Strengths

1. **S3-based distribution** — Eliminates shared storage bottleneck; each worker independently fetches archives
2. **Horizontal scaling** — Add GPU workers without configuration changes
3. **Batch tracking** — Redis atomic counters ensure reliable cleanup
4. **Fault isolation** — Failed jobs don't block the queue; scratch cleanup is per-batch

### Known Limitations

#### Critical: Parquet Metadata Not Implemented

Archives contain `.parquet` files with metadata linked to MP3s via `meta_id` column. **This is currently ignored.**

**Current behavior**: Only MP3 files are extracted; parquet files are skipped.

**Required behavior**: Parse parquet, match `meta_id` to MP3 filename stem, store metadata with audio record.

See [Implementation Plan: Parquet Support](#implementation-plan-parquet-support) below.

#### Other Issues

| Issue | Impact | Mitigation |
|-------|--------|------------|
| No S3 upload retry | Transient failures lose opus files | Add exponential backoff |
| Failed batches leak scratch | Disk fills if jobs fail | Hourly cron cleanup (implemented) |
| No integrity check on conversion | Corrupt opus undetected | Add duration validation |

## Data Flow

### Queue Structure (Redis)

```
list:unpack      Transfer → Unpack     {"batch_id", "s3_key", "original_filename", "transferred_at"}
list:transcribe  Unpack → GPU          {"batch_id", "opus_path", "original_filename"}
list:failed      Any → Dead letter     {"original_job", "error", "worker", "timestamp"}

batch:{id}:total      Set by unpack worker (file count)
batch:{id}:processed  Incremented by GPU worker
batch:{id}:s3_key     Archive S3 key for reference
```

### Processing Stages

```
1. Transfer Worker
   └─ SCP from EC2 → Local staging → S3 upload → Redis enqueue → Cleanup local

2. Unpack Worker (runs on GPU VM)
   └─ S3 download → Extract tar → Convert MP3→Opus → Set batch counters → Enqueue jobs

3. GPU Worker (runs on GPU VM)
   └─ Transcribe (WhisperX) → Classify (CoPE-A) → Insert DB → Upload opus to S3
   └─ Increment batch counter → If complete: cleanup scratch + Redis keys
```

## Database Schema

```sql
-- Core tables
audio_files (id, original_filename, opus_path, s3_opus_path, archive_source,
             duration_seconds, file_size_bytes, status, created_at, processed_at)

transcripts (id, audio_file_id FK, transcript_text, language, confidence, created_at)

classifications (id, audio_file_id FK, flagged, flag_score, flag_category, created_at)

-- Views
ra_queue        Flagged items from last 24h for research assistant review
daily_stats     7-day status breakdown for monitoring
```

## Deployment

### Prerequisites

- Orchestrator VM: Redis, PostgreSQL, (optionally) RA Flask App
- GPU Workers: Nvidia L4 GPU, 100GB Cinder volume mounted at `/data`
- S3-compatible storage: OpenStack Swift or AWS S3
- Network: Workers can reach orchestrator (Redis, PostgreSQL) and S3

### Quick Start

```bash
# 1. Clone repository
git clone <repo> && cd tiktok-cloud-pipeline-v2

# 2. Configure Ansible
cd ansible
cp group_vars/vault.yml.example group_vars/vault.yml
# Edit vault.yml with your secrets
ansible-vault encrypt group_vars/vault.yml

# Edit inventory with your IPs
vim inventory/production.yml

# 3. Deploy
ansible-playbook playbooks/site.yml --ask-vault-pass

# 4. Verify
ansible-playbook playbooks/health-check.yml
```

### Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | 10.0.0.1 | Redis server address |
| `POSTGRES_HOST` | 10.0.0.1 | PostgreSQL server address |
| `S3_ENDPOINT` | — | S3-compatible endpoint URL |
| `S3_BUCKET` | audio-pipeline | Bucket for archives and processed files |
| `SCRATCH_ROOT` | /data/scratch | Local processing directory |
| `MODELS_ROOT` | /data/models | ML model storage |
| `WHISPERX_MODEL` | large-v2 | WhisperX model size |
| `GPU_BATCH_SIZE` | 32 | Items to collect before processing |

See `.env.example` for complete list.

### Ansible Playbooks

| Playbook | Purpose |
|----------|---------|
| `site.yml` | Full deployment (all roles) |
| `deploy-pipeline.yml` | Quick code update (git pull, restart) |
| `setup-new-worker.yml` | Provision new GPU VM |
| `sync-models.yml` | Re-sync models to workers |
| `health-check.yml` | System health verification |

## Operations

### Health Check

```bash
# Quick check
./deploy/check-health.sh

# Full Ansible check
ansible-playbook ansible/playbooks/health-check.yml

# Manual checks
redis-cli LLEN list:unpack          # Pending unpack jobs
redis-cli LLEN list:transcribe      # Pending transcription jobs
redis-cli LLEN list:failed          # Failed jobs
redis-cli KEYS "batch:*:total"      # Active batches
```

### Monitoring Queries

```sql
-- Files processed in last 24 hours
SELECT COUNT(*) FROM audio_files WHERE created_at > NOW() - INTERVAL '24 hours';

-- Status breakdown
SELECT status, COUNT(*) FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours' GROUP BY status;

-- Flagged content for RA review
SELECT COUNT(*) FROM ra_queue;

-- Processing rate by hour
SELECT DATE_TRUNC('hour', created_at) as hour, COUNT(*)
FROM audio_files WHERE created_at > NOW() - INTERVAL '6 hours'
GROUP BY 1 ORDER BY 1 DESC;
```

### Scaling

**Add GPU workers:**
1. Provision VM with L4 GPU
2. Attach 100GB Cinder volume as `/dev/vdb`
3. Add to `ansible/inventory/production.yml`
4. Run: `ansible-playbook playbooks/setup-new-worker.yml --limit gpu-0X`

**Scale down:**
- Stop worker services; in-flight jobs return to queue after timeout
- Remove from inventory for future deploys

### Troubleshooting

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| Queue building up | GPU workers stopped/slow | Check `journalctl -u gpu-worker` |
| Scratch space full | Failed batches not cleaned | Run `find /data/scratch -mmin +180 -exec rm -rf {}` |
| S3 upload errors | Network/credentials | Verify S3_ENDPOINT, S3_ACCESS_KEY |
| CUDA out of memory | Batch size too large | Reduce `GPU_BATCH_SIZE` |
| Models not found | Sync incomplete | Run `ansible-playbook playbooks/sync-models.yml` |

## Implementation Plan: Parquet Support

### Overview

Archives contain parquet files with metadata. The `meta_id` column links to MP3 filenames (stem match: `audio123.mp3` → `meta_id='audio123'`).

### Required Changes

#### 1. Add dependency

```bash
# requirements.txt
pyarrow>=14.0.0
```

#### 2. Extend database schema

```sql
-- migrations/add_parquet_metadata.sql
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS parquet_metadata JSONB;
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS metadata_source TEXT;

CREATE INDEX IF NOT EXISTS idx_audio_parquet_metadata
ON audio_files USING GIN (parquet_metadata);
```

#### 3. Update unpack worker

```python
# src/unpack_worker.py - after extraction, before conversion

import pyarrow.parquet as pq

def load_parquet_metadata(scratch_dir: Path) -> dict:
    """Load all parquet files and build meta_id -> row lookup."""
    metadata = {}
    for pq_file in scratch_dir.rglob("*.parquet"):
        table = pq.read_table(pq_file)
        df = table.to_pandas()
        for _, row in df.iterrows():
            meta_id = row.get('meta_id')
            if meta_id:
                metadata[str(meta_id)] = row.to_dict()
    return metadata

# In process_job(), after extraction:
parquet_metadata = load_parquet_metadata(scratch_dir)

# When queuing transcription jobs:
for opus_path in opus_files:
    stem = opus_path.stem  # e.g., "audio123"
    job = {
        "batch_id": batch_id,
        "opus_path": str(opus_path),
        "original_filename": stem + ".mp3",
        "metadata": parquet_metadata.get(stem, {})  # NEW
    }
```

#### 4. Update GPU worker

```python
# src/gpu_worker.py - in process_item()

metadata = item.get("metadata", {})

# After insert_audio_file():
if metadata:
    update_audio_metadata(audio_id, metadata)

# src/db.py - new function
def update_audio_metadata(audio_id: int, metadata: dict):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE audio_files
                SET parquet_metadata = %s
                WHERE id = %s
            """, (Json(metadata), audio_id))
```

### Verification

1. Process archive containing parquet + MP3s
2. Query: `SELECT id, parquet_metadata FROM audio_files WHERE parquet_metadata IS NOT NULL`
3. Verify metadata fields match parquet content

## Project Structure

```
tiktok-cloud-pipeline-v2/
├── src/
│   ├── config.py              # Configuration management
│   ├── db.py                  # PostgreSQL operations
│   ├── s3_utils.py            # S3 client operations
│   ├── utils.py               # Logging, Redis, utilities
│   ├── transfer_sounds.py     # Transfer worker
│   ├── unpack_worker.py       # Unpack worker
│   ├── gpu_worker.py          # GPU worker
│   └── test_pipeline.py       # Test harness
├── ansible/
│   ├── inventory/             # Host definitions
│   ├── group_vars/            # Variables and secrets
│   ├── roles/                 # Ansible roles
│   └── playbooks/             # Deployment playbooks
├── deploy/
│   ├── systemd/               # Service definitions
│   ├── setup-worker.sh        # VM provisioning
│   └── check-health.sh        # Health check script
├── migrations/
│   └── schema.sql             # Database schema
├── requirements.txt           # Python dependencies
└── .env.example               # Environment template
```

## Development

### Local Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start MinIO for local S3
docker run -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"

# Configure environment
cp .env.example .env
# Edit .env with local settings

# Run tests
python src/test_pipeline.py /path/to/test.tar --skip-gpu
```

### Testing

```bash
# Single archive test (no GPU)
python src/test_pipeline.py archive.tar.gz --skip-gpu --keep-files

# Full pipeline test (requires GPU)
python src/test_pipeline.py archive.tar.gz --output-dir ./test_output

# Integration test
redis-cli LPUSH list:unpack '{"batch_id":"test","s3_key":"archives/test.tar"}'
python -m src.unpack_worker  # Watch for processing
```

## License

Internal research use only.
