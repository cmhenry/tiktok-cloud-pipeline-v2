# Audio Processing Pipeline

A distributed audio processing pipeline for content moderation research. Ingests archives of MP3 audio files with parquet metadata, transcribes with WhisperX, classifies with CoPE-A, and stores results in PostgreSQL.

## Quick Start

### Prerequisites

- **Orchestrator VM**: Redis, PostgreSQL
- **GPU Workers**: Nvidia L4 GPU, 100GB storage at `/data`
- **S3 Storage**: OpenStack Swift or AWS S3
- **Network**: Workers can reach Redis, PostgreSQL, and S3

### 1. Clone and Configure

```bash
git clone <repo> && cd tiktok-cloud-pipeline-v2

# Copy environment template
cp .env.example .env
```

Edit `.env` with your configuration:

```bash
# Required settings
REDIS_HOST=10.0.0.1
POSTGRES_HOST=10.0.0.1
POSTGRES_DB=audio_pipeline
POSTGRES_USER=pipeline
POSTGRES_PASSWORD=<your-password>

S3_ENDPOINT=https://swift.example.com
S3_BUCKET=audio-pipeline
S3_ACCESS_KEY=<your-key>
S3_SECRET_KEY=<your-secret>
```

### 2. Set Up Database

```bash
# On orchestrator VM
sudo -u postgres createdb audio_pipeline
sudo -u postgres psql -d audio_pipeline -f migrations/schema.sql

# For existing databases, run migration
psql -h 10.0.0.1 -U pipeline -d audio_pipeline -f migrations/002_add_parquet_metadata.sql
```

### 3. Install Dependencies

```bash
# On each worker VM
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Install ffmpeg for audio conversion
sudo apt-get install ffmpeg
```

### 4. Download Models

```bash
# WhisperX downloads automatically on first run
# CoPE-A adapter must be placed at configured path
mkdir -p /data/models/cope-a
# Copy your CoPE-A LoRA adapter to /data/models/cope-a/
```

### 5. Start Workers

```bash
# On GPU VMs - run both workers
source venv/bin/activate

# Terminal 1: Unpack worker (handles archive extraction)
python -m src.unpack_worker

# Terminal 2: GPU worker (handles transcription/classification)
python -m src.gpu_worker
```

Or use systemd (recommended for production):

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now unpack-worker gpu-worker
```

## Usage

### Processing Archives

Archives are processed through a three-stage pipeline:

```
Transfer → Unpack → GPU Processing → Results in PostgreSQL
```

**Queue a job manually:**

```bash
# Upload archive to S3 first
aws s3 cp my-archive.tar.gz s3://audio-pipeline/archives/ --endpoint-url $S3_ENDPOINT

# Queue for processing
redis-cli LPUSH list:unpack '{
  "batch_id": "batch-001",
  "s3_key": "archives/my-archive.tar.gz",
  "original_filename": "my-archive.tar.gz"
}'
```

**Use the transfer worker** (for automated ingestion from remote source):

```bash
python -m src.transfer_sounds
```

### Monitoring Progress

```bash
# Check queue depths
redis-cli LLEN list:unpack       # Pending extraction
redis-cli LLEN list:transcribe   # Pending GPU processing
redis-cli LLEN list:failed       # Failed jobs

# Check active batches
redis-cli KEYS "batch:*:total"

# View batch progress
redis-cli GET batch:batch-001:total
redis-cli GET batch:batch-001:processed
```

### Querying Results

```sql
-- Files processed today
SELECT COUNT(*) FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours';

-- Status breakdown
SELECT status, COUNT(*) FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY status;

-- Flagged content for review
SELECT * FROM ra_queue LIMIT 10;

-- Query by country/language (from parquet metadata)
SELECT id, original_filename, parquet_lang, parquet_country
FROM audio_files
WHERE parquet_country = 'US' AND parquet_lang = 'en';

-- Query parquet metadata fields
SELECT id, parquet_metadata->>'author_uniqueid' as author,
       (parquet_metadata->>'stats_playcount')::int as plays
FROM audio_files
WHERE parquet_metadata IS NOT NULL
ORDER BY plays DESC LIMIT 10;
```

### Health Check

```bash
./deploy/check-health.sh
```

Or manually:

```bash
# Redis connectivity
redis-cli ping

# PostgreSQL connectivity
psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT 1"

# S3 connectivity
aws s3 ls s3://$S3_BUCKET/ --endpoint-url $S3_ENDPOINT

# Check worker logs
journalctl -u unpack-worker -f
journalctl -u gpu-worker -f
```

## Architecture

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
│  • Queue job  │       │   │ • Parquet parsing     • Opus → S3               │    │
│    (Redis)    │       │   │ • Queue jobs          • Metadata storage        │    │
└───────────────┘       │   └─────────────────────────────────────────────────┘    │
                        └──────────────────────────────────────────────────────────┘
                                                    │
                                                    ▼ results
                        ┌──────────────────────────────────────────────────────────┐
                        │                    Orchestrator VM                        │
                        │   ┌─────────┐   ┌──────────┐   ┌───────────────────┐     │
                        │   │  Redis  │   │ Postgres │   │     RA App        │     │
                        │   │ (queues)│   │ (results)│   │ (review interface)│     │
                        │   └─────────┘   └──────────┘   └───────────────────┘     │
                        └──────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Transfer Worker** pulls archives from source, uploads to S3, queues unpack job
2. **Unpack Worker** downloads archive, extracts MP3s, parses parquet metadata, converts to Opus, queues GPU jobs
3. **GPU Worker** transcribes audio, classifies content, stores results in PostgreSQL, uploads opus to S3

### Queue Structure

| Queue | Producer | Consumer | Payload |
|-------|----------|----------|---------|
| `list:unpack` | Transfer | Unpack | `{batch_id, s3_key, original_filename}` |
| `list:transcribe` | Unpack | GPU | `{batch_id, opus_path, original_filename, parquet_metadata}` |
| `list:failed` | Any | Manual | `{original_job, error, failed_at}` |

### Database Schema

```sql
audio_files (
    id, original_filename, opus_path, s3_opus_path,
    archive_source, duration_seconds, file_size_bytes,
    status, created_at, processed_at,
    parquet_lang, parquet_country, parquet_metadata  -- from parquet files
)

transcripts (id, audio_file_id, transcript_text, language, confidence)

classifications (id, audio_file_id, flagged, flag_score, flag_category)
```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `localhost` | Redis server address |
| `REDIS_PORT` | `6379` | Redis port |
| `POSTGRES_HOST` | `localhost` | PostgreSQL server |
| `POSTGRES_DB` | `audio_pipeline` | Database name |
| `S3_ENDPOINT` | — | S3-compatible endpoint URL |
| `S3_BUCKET` | `audio-pipeline` | Bucket for archives/processed |
| `SCRATCH_ROOT` | `/data/scratch` | Local processing directory |
| `MODELS_ROOT` | `/data/models` | ML model storage |
| `WHISPERX_MODEL` | `large-v2` | WhisperX model size |
| `COPE_MODEL` | `google/gemma-2-9b-it` | Base model for CoPE-A |
| `COPE_ADAPTER` | `/data/models/cope-a` | CoPE-A LoRA adapter path |
| `GPU_BATCH_SIZE` | `32` | Items per GPU batch |
| `FFMPEG_WORKERS` | `4` | Parallel conversion processes |
| `OPUS_BITRATE` | `32k` | Opus encoding bitrate |

## Ansible Deployment (Production)

For automated multi-VM deployment:

```bash
cd ansible

# Configure inventory
cp inventory/production.yml.example inventory/production.yml
vim inventory/production.yml  # Add your host IPs

# Configure secrets
cp group_vars/vault.yml.example group_vars/vault.yml
vim group_vars/vault.yml  # Add passwords, keys
ansible-vault encrypt group_vars/vault.yml

# Deploy everything
ansible-playbook playbooks/site.yml --ask-vault-pass

# Or deploy specific components
ansible-playbook playbooks/deploy-pipeline.yml  # Code update only
ansible-playbook playbooks/setup-new-worker.yml --limit gpu-05  # New worker
ansible-playbook playbooks/health-check.yml  # Verify deployment
```

## Scaling

**Add GPU workers:**

1. Provision VM with L4 GPU and 100GB volume
2. Add to Ansible inventory under `gpu_workers`
3. Run: `ansible-playbook playbooks/setup-new-worker.yml --limit new-worker`

**Throughput capacity (per worker):**

| Component | Capacity |
|-----------|----------|
| Unpack Worker | 200-400 files/hour |
| GPU Worker (L4) | 150-250 files/hour |

With 10 GPU workers: ~1,500-2,500 files/hour (5-8× headroom for 20-30 GB/day target).

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| Queue building up | Workers stopped | `systemctl status gpu-worker` |
| Scratch space full | Failed batch cleanup | `find /data/scratch -mmin +180 -delete` |
| S3 errors | Credentials/network | Check `S3_ENDPOINT`, `S3_ACCESS_KEY` |
| CUDA OOM | Batch too large | Reduce `GPU_BATCH_SIZE` |
| No parquet metadata | Missing files | Verify archives contain `.parquet` files |

## Project Structure

```
tiktok-cloud-pipeline-v2/
├── src/
│   ├── config.py           # Configuration management
│   ├── db.py               # PostgreSQL operations
│   ├── s3_utils.py         # S3 client operations
│   ├── utils.py            # Logging, Redis, utilities
│   ├── transfer_sounds.py  # Transfer worker
│   ├── unpack_worker.py    # Unpack worker (parquet parsing)
│   └── gpu_worker.py       # GPU worker (WhisperX + CoPE-A)
├── ansible/                # Ansible deployment
├── deploy/                 # Systemd services, scripts
├── migrations/             # Database schema
├── requirements.txt        # Python dependencies
└── .env.example            # Environment template
```

## License

Internal research use only.
