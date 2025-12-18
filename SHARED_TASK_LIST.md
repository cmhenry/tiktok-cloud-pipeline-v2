# Audio Processing Pipeline for Content Moderation

## Project Overview

Build a distributed audio processing pipeline for content moderation research. The system ingests tar archives of audio files from AWS, processes them through transcription (WhisperX) and classification (CoPE-A), and surfaces flagged content for research assistant review.

**Goals:**
- Ingest up to 1TB of audio data daily from AWS EC2
- Convert MP3 → Opus for efficient storage on 20TB shared volume
- Transcribe audio and classify for harmful content
- Surface ≥200 flagged posts daily by noon for RA review window (12pm-4pm)
- Run 24/7 with graceful failure handling

**Infrastructure:**
- Coordinator VM: 4 vCPU, 16GB RAM (Redis, Postgres, reporting app)
- Worker VMs (×3): 8 vCPU, 32GB RAM, L4 GPU 24GB VRAM (unpack + transcribe + classify)
- Transfer VM: 8 vCPU, 32GB RAM (runs modified transfer script)
- 20TB shared cloud volume mounted at `/mnt/data`

**Processing math:**
- ~2M files ingested daily, 10% flag rate → 200K flagged
- Need ~40K processed to get 200 reportable (10% flag × 5% reportable)
- 60-second average audio → ~500-800 files/hour/GPU
- 3 GPUs × 24 hours × 500 files = 36K files/day ✓

## Current Status

- [x] Transfer script from collaborator (transfer_sounds.py)
- [x] Architecture design complete
- [x] Shared infrastructure (config, utils, db, schema)
- [x] Database schema and migrations
- [x] Transfer worker integration
- [x] Unpack worker (tar extraction, MP3→Opus)
- [x] GPU worker (WhisperX + CoPE-A)
- [x] Deployment and systemd services

---

## Phase 1: Shared Infrastructure

### 1.1 Configuration Module

Central config consumed by all workers:

```python
# config.py
import os
from pathlib import Path

# Paths - shared cloud volume
VOLUME_ROOT = Path(os.getenv("VOLUME_ROOT", "/mnt/data"))
PATHS = {
    "INCOMING_DIR": VOLUME_ROOT / "incoming",      # Landing zone for tar archives
    "UNPACKED_DIR": VOLUME_ROOT / "unpacked",      # Temp extraction directory  
    "AUDIO_DIR": VOLUME_ROOT / "audio",            # Final opus files by date
    "PROCESSED_DIR": VOLUME_ROOT / "processed",    # Completed archive logs
}

# Redis
REDIS = {
    "HOST": os.getenv("REDIS_HOST", "10.0.0.1"),
    "PORT": int(os.getenv("REDIS_PORT", 6379)),
    "QUEUES": {
        "UNPACK": "list:unpack",
        "TRANSCRIBE": "list:transcribe", 
        "FAILED": "list:failed",
    },
}

# Postgres
POSTGRES = {
    "HOST": os.getenv("POSTGRES_HOST", "10.0.0.1"),
    "PORT": int(os.getenv("POSTGRES_PORT", 5432)),
    "DATABASE": os.getenv("POSTGRES_DB", "audio_pipeline"),
    "USER": os.getenv("POSTGRES_USER", "pipeline"),
    "PASSWORD": os.getenv("POSTGRES_PASSWORD"),
}

# AWS transfer settings
AWS = {
    "HOST": "tt-zrh",                              # SSH config alias
    "SOURCE_DIR": "/mnt/hub/export/sound",
    "SSH_CONFIG_FILE": Path.home() / ".ssh/ssh_config",
    "FILE_LATENCY_MIN": 10,                        # Only grab files >10 min old
    "TRANSFER_BATCH": 50,                          # Max files per cycle
    "POLL_INTERVAL": 60,                           # Seconds between polls
    "SECURE_TRANSFER": True,                       # Verify file sizes
    "DELETE_AFTER": False,                         # Flip True once validated
}

# Transfer lock settings
TRANSFER_LOCKS = {
    "DIR": Path.home() / "transfer_locks",
    "TIMEOUT_MIN": 60,
}

# Processing settings
PROCESSING = {
    "BATCH_SIZE": 32,
    "WHISPERX_MODEL": "large-v2",
    "COPE_MODEL": "google/gemma-2-9b-it",
    "COPE_ADAPTER": Path("/models/cope-a-lora"),
    "FFMPEG_WORKERS": 4,                           # Parallel conversions
    "OPUS_BITRATE": "48k",
}
```

### 1.2 Utilities Module

```python
# utils.py
import logging
import redis
import magic  # python-magic for content detection

def setup_logger(name: str, log_dir: Path = None) -> logging.Logger:
    """Consistent logging format across workers."""
    # Format: timestamp | level | worker | message
    pass

def get_redis_client() -> redis.Redis:
    """Redis connection with retry logic."""
    pass

def detect_archive_type(path: Path) -> str:
    """Content-based detection using magic bytes.
    Returns: 'tar', 'gzip', 'tar.gz', or 'unknown'
    Important: our .tar.gz files are often actually uncompressed tar!
    """
    pass

def safe_move(src: Path, dst: Path) -> bool:
    """Atomic move with verification."""
    pass
```

### 1.3 Database Module

```python
# db.py
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool

_pool = None

def get_db_pool():
    """Initialize connection pool."""
    pass

@contextmanager
def get_connection():
    """Get connection from pool."""
    pass

def insert_audio_file(
    original_filename: str,
    opus_path: str, 
    archive_source: str,
    duration_seconds: float,
    file_size_bytes: int,
) -> int:
    """Insert audio file record, return id."""
    pass

def bulk_insert_audio_files(records: list[dict]) -> list[int]:
    """Batch insert, return ids."""
    pass

def insert_transcript(audio_id: int, text: str, language: str, confidence: float):
    pass

def insert_classification(audio_id: int, flagged: bool, score: float, category: str):
    pass

def update_audio_status(audio_id: int, status: str):
    pass

def get_pending_flagged(limit: int = 100) -> list[dict]:
    """For RA queue - flagged items awaiting review."""
    pass
```

### 1.4 Deliverables
- [ ] `config.py` - Central configuration
- [ ] `utils.py` - Logging, Redis client, archive detection, file ops
- [ ] `db.py` - Connection pool and CRUD operations
- [ ] `schema.sql` - Database schema with indexes
- [ ] Ensure all paths created on startup

---

## Phase 2: Database Schema

### 2.1 Schema Design

```sql
-- schema.sql

CREATE TABLE audio_files (
    id SERIAL PRIMARY KEY,
    original_filename TEXT NOT NULL,
    opus_path TEXT NOT NULL UNIQUE,
    archive_source TEXT,
    duration_seconds FLOAT,
    file_size_bytes INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP,
    status TEXT DEFAULT 'pending'  -- pending, transcribed, flagged, reviewed, failed
);

CREATE TABLE transcripts (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER REFERENCES audio_files(id) ON DELETE CASCADE,
    transcript_text TEXT,
    language TEXT,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE classifications (
    id SERIAL PRIMARY KEY,
    audio_file_id INTEGER REFERENCES audio_files(id) ON DELETE CASCADE,
    flagged BOOLEAN NOT NULL,
    flag_score FLOAT,
    flag_category TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_audio_status_created ON audio_files(status, created_at DESC);
CREATE INDEX idx_audio_archive ON audio_files(archive_source);
CREATE INDEX idx_classifications_flagged ON classifications(flagged, created_at DESC);

-- RA queue view: flagged items from last 24 hours
CREATE VIEW ra_queue AS
SELECT 
    af.id,
    af.original_filename,
    af.opus_path,
    t.transcript_text,
    c.flag_score,
    c.flag_category,
    af.created_at
FROM audio_files af
JOIN transcripts t ON t.audio_file_id = af.id
JOIN classifications c ON c.audio_file_id = af.id
WHERE c.flagged = true
  AND af.status = 'flagged'
  AND af.created_at > NOW() - INTERVAL '24 hours'
ORDER BY c.flag_score DESC;
```

### 2.2 Deliverables
- [ ] `schema.sql` - Tables, indexes, views
- [ ] Migration script or instructions
- [ ] Verify indexes support RA queue queries efficiently

---

## Phase 3: Transfer Worker

### 3.1 Modifications to Existing Script

Minimal changes to `transfer_sounds.py`:

```python
# At top of file, replace hardcoded config:
from config import AWS, TRANSFER_LOCKS, REDIS, PATHS

DEST_FOLDER = str(PATHS["INCOMING_DIR"])
SOURCE_FOLDER = AWS["SOURCE_DIR"]
SSH_CONFIG_FILE = str(AWS["SSH_CONFIG_FILE"])
TRANSFER_LOCK_FOLDER = str(TRANSFER_LOCKS["DIR"])

# Update Redis connection:
redis_client = redis.Redis(
    host=REDIS["HOST"], 
    port=REDIS["PORT"], 
    decode_responses=True
)

# After successful transfer (around line 515), add queue push:
if transfer_result and redis_client:
    archive_path = f"{DEST_FOLDER}/{os.path.basename(source_file)}"
    redis_client.lpush(REDIS["QUEUES"]["UNPACK"], archive_path)
    log_message(f"Queued for unpacking: {archive_path}", logger)
    files_queued += 1
```

### 3.2 Deliverables
- [ ] Modified `transfer_worker.py` using shared config
- [ ] Test Redis queue integration
- [ ] Verify size verification still works
- [ ] Add queued count to logging

---

## Phase 4: CPU Worker (Unpack + Convert)

### 4.1 Unpack Worker

Runs on GPU VMs alongside GPU worker (separate process):

```python
# unpack_worker.py
import tarfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from datetime import datetime
import subprocess
import json

from config import PATHS, REDIS, PROCESSING
from utils import setup_logger, get_redis_client, detect_archive_type
from db import bulk_insert_audio_files

logger = setup_logger("unpack_worker")

def convert_mp3_to_opus(mp3_path: Path, opus_path: Path) -> bool:
    """Convert single file with ffmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", str(mp3_path),
        "-c:a", "libopus", "-b:a", PROCESSING["OPUS_BITRATE"],
        str(opus_path)
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0

def process_archive(archive_path: str):
    """Extract archive, convert files, queue for transcription."""
    archive_path = Path(archive_path)
    archive_name = archive_path.stem
    
    # 1. Create temp extraction dir
    extract_dir = PATHS["UNPACKED_DIR"] / archive_name
    extract_dir.mkdir(parents=True, exist_ok=True)
    
    # 2. Detect type and extract (handle mislabeled archives)
    archive_type = detect_archive_type(archive_path)
    # ... extraction logic based on type
    
    # 3. Find all MP3s
    mp3_files = list(extract_dir.rglob("*.mp3"))
    
    # 4. Parallel conversion
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir = PATHS["AUDIO_DIR"] / today
    output_dir.mkdir(parents=True, exist_ok=True)
    
    records = []
    with ProcessPoolExecutor(max_workers=PROCESSING["FFMPEG_WORKERS"]) as executor:
        # ... convert files, collect records
        pass
    
    # 5. Bulk insert to DB
    audio_ids = bulk_insert_audio_files(records)
    
    # 6. Queue for transcription
    redis_client = get_redis_client()
    for audio_id, record in zip(audio_ids, records):
        msg = json.dumps({
            "audio_id": audio_id,
            "opus_path": record["opus_path"],
            "original_filename": record["original_filename"],
        })
        redis_client.lpush(REDIS["QUEUES"]["TRANSCRIBE"], msg)
    
    # 7. Cleanup
    shutil.rmtree(extract_dir)
    # Move archive to processed or delete

def main():
    """Main loop - block on Redis queue."""
    redis_client = get_redis_client()
    logger.info("Unpack worker started, waiting for archives...")
    
    while True:
        _, archive_path = redis_client.brpop(REDIS["QUEUES"]["UNPACK"])
        try:
            process_archive(archive_path)
        except Exception as e:
            logger.error(f"Failed processing {archive_path}: {e}")
            redis_client.lpush(REDIS["QUEUES"]["FAILED"], archive_path)

if __name__ == "__main__":
    main()
```

### 4.2 Deliverables
- [ ] `unpack_worker.py` - Main worker script
- [ ] Content-based archive detection (tar vs gzip)
- [ ] Parallel ffmpeg conversion with ProcessPoolExecutor
- [ ] Bulk DB inserts for efficiency
- [ ] JSON messages to transcribe queue
- [ ] Error handling - don't stop on single file failures
- [ ] Cleanup temp directories after processing

---

## Phase 5: GPU Worker (Transcribe + Classify)

### 5.1 GPU Worker

Runs on GPU VMs, handles both WhisperX and CoPE-A:

```python
# gpu_worker.py
import torch
import whisperx
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel
import json

from config import REDIS, PROCESSING, POSTGRES
from utils import setup_logger, get_redis_client
from db import get_db_pool, insert_transcript, insert_classification, update_audio_status

logger = setup_logger("gpu_worker")

class GPUWorker:
    def __init__(self):
        self.device = "cuda"
        self.whisper_model = None
        self.cope_model = None
        self.cope_tokenizer = None
        
    def initialize_models(self):
        """Load models at startup."""
        logger.info("Loading WhisperX...")
        self.whisper_model = whisperx.load_model(
            PROCESSING["WHISPERX_MODEL"],
            self.device,
            compute_type="float16"
        )
        
        logger.info("Loading Gemma + CoPE-A LoRA...")
        bnb_config = BitsAndBytesConfig(load_in_8bit=True)
        base_model = AutoModelForCausalLM.from_pretrained(
            PROCESSING["COPE_MODEL"],
            quantization_config=bnb_config,
            device_map="auto",
        )
        self.cope_model = PeftModel.from_pretrained(
            base_model, 
            PROCESSING["COPE_ADAPTER"]
        )
        self.cope_tokenizer = AutoTokenizer.from_pretrained(PROCESSING["COPE_MODEL"])
        
        # Log VRAM usage
        allocated = torch.cuda.memory_allocated() / 1024**3
        logger.info(f"Models loaded. VRAM used: {allocated:.1f}GB")
    
    def transcribe(self, audio_path: str) -> dict:
        """Transcribe single audio file."""
        audio = whisperx.load_audio(audio_path)
        result = self.whisper_model.transcribe(audio, batch_size=16)
        return {
            "text": result["segments"][0]["text"] if result["segments"] else "",
            "language": result.get("language", "unknown"),
            "confidence": 0.0,  # Extract from segments if available
        }
    
    def classify(self, transcript: str) -> dict:
        """Classify transcript with CoPE-A."""
        prompt = f"""Analyze this transcript for harmful content.

Transcript: "{transcript}"

Respond with JSON only: {{"flagged": true/false, "score": 0.0-1.0, "category": "category or null"}}"""
        
        inputs = self.cope_tokenizer(prompt, return_tensors="pt").to(self.device)
        outputs = self.cope_model.generate(**inputs, max_new_tokens=100)
        response = self.cope_tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        # Parse JSON from response
        # ... handle malformed responses
        return {"flagged": False, "score": 0.0, "category": None}
    
    def process_batch(self, items: list[dict]):
        """Process batch of audio files."""
        for item in items:
            try:
                # Transcribe
                transcript = self.transcribe(item["opus_path"])
                insert_transcript(
                    item["audio_id"],
                    transcript["text"],
                    transcript["language"],
                    transcript["confidence"],
                )
                
                # Classify
                classification = self.classify(transcript["text"])
                insert_classification(
                    item["audio_id"],
                    classification["flagged"],
                    classification["score"],
                    classification["category"],
                )
                
                # Update status
                status = "flagged" if classification["flagged"] else "transcribed"
                update_audio_status(item["audio_id"], status)
                
            except Exception as e:
                logger.error(f"Failed processing {item['audio_id']}: {e}")
                update_audio_status(item["audio_id"], "failed")
    
    def run(self):
        """Main loop - collect batches from queue."""
        redis_client = get_redis_client()
        logger.info("GPU worker started, waiting for audio files...")
        
        while True:
            batch = []
            
            # Collect batch
            while len(batch) < PROCESSING["BATCH_SIZE"]:
                result = redis_client.brpop(
                    REDIS["QUEUES"]["TRANSCRIBE"], 
                    timeout=5
                )
                if result is None:
                    break  # Timeout, process what we have
                _, msg = result
                batch.append(json.loads(msg))
            
            if batch:
                logger.info(f"Processing batch of {len(batch)} files")
                self.process_batch(batch)


def main():
    worker = GPUWorker()
    worker.initialize_models()
    worker.run()

if __name__ == "__main__":
    main()
```

### 5.2 Deliverables
- [ ] `gpu_worker.py` - Main GPU worker script
- [ ] WhisperX initialization with large-v2 model
- [ ] Gemma-2-9B with 8-bit quantization + LoRA adapter loading
- [ ] Batch collection from Redis queue
- [ ] Transcription with language detection
- [ ] Classification with JSON parsing
- [ ] DB writes for transcripts and classifications
- [ ] VRAM monitoring and logging
- [ ] Error handling per-file (don't crash on failures)

---

## Phase 6: Deployment

### 6.1 Coordinator VM Setup

```bash
# Redis
sudo apt install redis-server
sudo systemctl enable redis-server

# Configure Redis for network access
# /etc/redis/redis.conf:
#   bind 0.0.0.0
#   maxmemory 4gb
#   maxmemory-policy allkeys-lru

# PostgreSQL  
sudo apt install postgresql postgresql-contrib
sudo -u postgres createdb audio_pipeline
sudo -u postgres psql -d audio_pipeline -f schema.sql

# Mount shared volume
sudo mount /dev/sdb /mnt/data
mkdir -p /mnt/data/{incoming,unpacked,audio,processed}
```

### 6.2 Worker VM Setup

```bash
# Mount shared volume
sudo mount /dev/sdb /mnt/data

# Python environment
python -m venv /opt/pipeline/venv
source /opt/pipeline/venv/bin/activate
pip install redis psycopg2-binary python-magic whisperx transformers peft bitsandbytes

# Systemd services
# /etc/systemd/system/unpack-worker.service
# /etc/systemd/system/gpu-worker.service
```

### 6.3 Systemd Service Files

```ini
# /etc/systemd/system/gpu-worker.service
[Unit]
Description=GPU Worker (Transcription + Classification)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/pipeline
Environment=REDIS_HOST=10.0.0.1
Environment=POSTGRES_HOST=10.0.0.1
ExecStart=/opt/pipeline/venv/bin/python gpu_worker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 6.4 Monitoring

```sql
-- Daily health check (run before noon)
SELECT 
    COUNT(*) FILTER (WHERE c.flagged = true) as flagged_count,
    COUNT(*) as total_processed
FROM audio_files af
JOIN classifications c ON c.audio_file_id = af.id
WHERE af.created_at > NOW() - INTERVAL '24 hours';

-- Queue depths
-- redis-cli LLEN list:unpack
-- redis-cli LLEN list:transcribe
```

### 6.5 Deliverables
- [ ] Coordinator setup script/instructions
- [ ] Worker setup script/instructions
- [ ] Systemd service files for all workers
- [ ] Monitoring queries and scripts
- [ ] Environment variable documentation

---

## Notes for Implementation

**Start with Phase 1** (shared infrastructure). This is foundation for everything else. Key files:
- `config.py` 
- `utils.py`
- `db.py`
- `schema.sql`

**Then Phase 2** (schema) - run migration on coordinator.

**Then Phase 3** (transfer worker) - minimal modifications to existing script.

**Then Phase 4** (unpack worker) - can test independently with sample tar files.

**Finally Phase 5** (GPU worker) - requires GPU VM, test with sample audio files first.

**Key gotchas:**
- Tar archives may be mislabeled (`.tar.gz` but actually uncompressed) - use content detection
- Both WhisperX and Gemma-2-9B need to fit in 24GB VRAM - use 8-bit quantization
- CoPE-A JSON responses may be malformed - handle parsing errors gracefully
- Run unpack worker and GPU worker as separate processes on same VM