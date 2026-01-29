"""
Audio Processing Pipeline - Configuration Module

Central configuration consumed by all workers. Uses environment variables
for deployment-specific values with sensible defaults for development.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file with explicit path search so it works regardless of CWD
_ENV_FILE_LOADED = None
_ENV_SEARCH_PATHS = [
    Path(__file__).resolve().parent.parent / ".env",  # repo root
    Path("/opt/pipeline/.env"),                         # production path
]

for _candidate in _ENV_SEARCH_PATHS:
    if _candidate.is_file():
        load_dotenv(_candidate)
        _ENV_FILE_LOADED = str(_candidate)
        break
else:
    load_dotenv()  # fallback to dotenv default CWD search

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
    "HOST": os.getenv("REDIS_HOST", "172.23.207.33"),
    "PORT": int(os.getenv("REDIS_PORT", "6379")),
    "QUEUES": {
        "UNPACK": "list:unpack",
        "TRANSCRIBE": "list:transcribe",
        "FAILED": "list:failed",
    },
}

# Postgres
POSTGRES = {
    "HOST": os.getenv("POSTGRES_HOST", "172.23.207.33"),
    "PORT": int(os.getenv("POSTGRES_PORT", "5432")),
    "DATABASE": os.getenv("POSTGRES_DB", "transcript_db"),
    "USER": os.getenv("POSTGRES_USER", "transcript_user"),
    "PASSWORD": os.getenv("POSTGRES_PASSWORD", "transcript_pass"),
}

# AWS transfer settings
AWS = {
    "HOST": "tt-zrh",                              # SSH config alias
    "SOURCE_DIR": "/mnt/hub/export/sound",
    "SSH_CONFIG_FILE": Path.home() / ".ssh" / "ssh_config",
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

# S3 Configuration (OpenStack Swift / S3-compatible storage)
S3 = {
    "ENDPOINT": os.getenv("S3_ENDPOINT"),
    "ACCESS_KEY": os.getenv("S3_ACCESS_KEY"),
    "SECRET_KEY": os.getenv("S3_SECRET_KEY"),
    "BUCKET": os.getenv("S3_BUCKET", "audio-pipeline"),
    "ARCHIVE_PREFIX": "archives/",
    "PROCESSED_PREFIX": "processed/",
}

# Local Storage - GPU workers (Cinder volumes)
LOCAL = {
    "SCRATCH_ROOT": Path(os.getenv("SCRATCH_ROOT", "/data/scratch")),
    "MODELS_ROOT": Path(os.getenv("MODELS_ROOT", "/mnt/models")),
}

# Processing settings
PROCESSING = {
    "BATCH_SIZE": int(os.getenv("GPU_BATCH_SIZE", "32")),
    "WHISPERX_MODEL": os.getenv("WHISPERX_MODEL", "large-v2"),
    "COPE_MODEL": os.getenv("COPE_MODEL", "/mnt/models/gemma-2-9b"),
    "COPE_ADAPTER": Path(os.getenv("COPE_ADAPTER", "/mnt/models/cope-a-adapter")),
    "COPE_POLICY": Path(__file__).parent / "tiktok_policy.txt",
    "FFMPEG_WORKERS": int(os.getenv("FFMPEG_WORKERS", "4")),
    "OPUS_BITRATE": os.getenv("OPUS_BITRATE", "16k"),
}

# Logging settings
LOGGING = {
    "DIR": Path(os.getenv("LOG_DIR", "/home/ubuntu/log/pipeline")),
    "FORMAT": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    "DATE_FORMAT": "%Y-%m-%d %H:%M:%S",
}


def ensure_paths_exist():
    """Create all required directories on startup."""
    for path in PATHS.values():
        path.mkdir(parents=True, exist_ok=True)

    TRANSFER_LOCKS["DIR"].mkdir(parents=True, exist_ok=True)
    LOGGING["DIR"].mkdir(parents=True, exist_ok=True)


def get_postgres_dsn() -> str:
    """Get PostgreSQL connection string."""
    return (
        f"host={POSTGRES['HOST']} "
        f"port={POSTGRES['PORT']} "
        f"dbname={POSTGRES['DATABASE']} "
        f"user={POSTGRES['USER']} "
        f"password={POSTGRES['PASSWORD']}"
    )
