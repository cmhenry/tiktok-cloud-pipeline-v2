#!/bin/bash
# GPU Worker Setup Script
# Run as root after cloning repo to /opt/pipeline
#
# Usage:
#   git clone https://github.com/cmhenry/tiktok-cloud-pipeline-v2 /opt/pipeline
#   cd /opt/pipeline
#   # Edit configuration below
#   sudo ./deploy/setup-worker.sh
#   sudo systemctl enable --now gpu-worker unpack-worker

set -e

# =============================================================================
# 1. CONFIGURATION - Update these values before running
# =============================================================================

# Coordinator services (Redis/Postgres host)
COORDINATOR_IP="${COORDINATOR_IP:-10.0.0.1}"

# Redis
REDIS_HOST="${REDIS_HOST:-$COORDINATOR_IP}"
REDIS_PORT="${REDIS_PORT:-6379}"

# PostgreSQL
POSTGRES_HOST="${POSTGRES_HOST:-$COORDINATOR_IP}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-transcript_db}"
POSTGRES_USER="${POSTGRES_USER:-transcript_user}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-changeme}"

# S3/Object Storage (OpenStack Swift compatible)
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-}"
S3_SECRET_KEY="${S3_SECRET_KEY:-}"
S3_BUCKET="${S3_BUCKET:-audio-pipeline}"

# Processing settings
GPU_BATCH_SIZE="${GPU_BATCH_SIZE:-32}"
WHISPERX_MODEL="${WHISPERX_MODEL:-large-v2}"
FFMPEG_WORKERS="${FFMPEG_WORKERS:-4}"
OPUS_BITRATE="${OPUS_BITRATE:-16k}"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

# Paths (generally don't need to change)
VOLUME_ROOT="/mnt/data"
SCRATCH_ROOT="/data/scratch"
MODELS_ROOT="/mnt/models"
COPE_MODEL="/mnt/models/gemma-2-9b"
COPE_ADAPTER="/mnt/models/cope-a-adapter"
LOG_DIR="/home/ubuntu/log/pipeline"

# =============================================================================
# SCRIPT START - No changes needed below
# =============================================================================

echo "=== GPU Worker Setup ==="
echo ""

# Check for root
if [[ $EUID -ne 0 ]]; then
   echo "ERROR: This script must be run as root (use sudo)"
   exit 1
fi

# Check we're running from the repo
if [[ ! -f "/opt/pipeline/requirements.txt" ]]; then
    echo "ERROR: Script must be run from /opt/pipeline"
    echo "Clone the repo first: git clone <repo> /opt/pipeline"
    exit 1
fi

# =============================================================================
# 2. SYSTEM PACKAGES
# =============================================================================
echo "[1/10] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip ffmpeg git

# =============================================================================
# 3. GPU DRIVER CHECK
# =============================================================================
echo "[2/10] Checking GPU setup..."

if lspci | grep -qi nvidia; then
    echo "  NVIDIA GPU detected"
    if ! command -v nvidia-smi &> /dev/null; then
        echo "  WARNING: NVIDIA drivers not installed"
        echo "  Install with: sudo apt install nvidia-driver-535"
        echo "  Or use cloud provider's GPU image"
        echo ""
        read -p "  Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        echo "  GPU info:"
        nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/    /'
    fi
else
    echo "  No NVIDIA GPU detected"
    echo "  This script is for GPU workers. Exiting."
    exit 1
fi

# =============================================================================
# 4. MOUNT CINDER VOLUME /dev/sdb -> /data
# =============================================================================
echo "[3/10] Setting up Cinder volume..."

# Check if /dev/sdb exists
if [[ ! -b /dev/sdb ]]; then
    echo "  WARNING: /dev/sdb not found"
    echo "  Attach a Cinder volume to this VM and retry"
    echo "  Skipping volume mount..."
else
    # Create mount point
    mkdir -p /data

    # Check if already mounted
    if mountpoint -q /data; then
        echo "  /data already mounted"
    else
        # Check if filesystem exists
        if ! blkid /dev/sdb | grep -q TYPE; then
            echo "  Creating ext4 filesystem on /dev/sdb..."
            mkfs.ext4 -q /dev/sdb
        fi

        # Mount the volume
        echo "  Mounting /dev/sdb to /data..."
        mount /dev/sdb /data

        # Add to fstab if not present
        if ! grep -q "/dev/sdb" /etc/fstab; then
            echo "  Adding to /etc/fstab for persistent mount..."
            echo "/dev/sdb  /data  ext4  defaults,nofail  0  2" >> /etc/fstab
        fi
    fi
    echo "  Volume mounted at /data"
fi

# =============================================================================
# 5. CREATE DIRECTORY STRUCTURE AND SYMLINKS
# =============================================================================
echo "[4/10] Creating directory structure..."

# Local storage directories (on Cinder volume)
mkdir -p /data/scratch
mkdir -p /data/models/gemma-2-9b
mkdir -p /data/models/cope-a-adapter

# Shared volume mount point
mkdir -p /mnt/data

# Create symlink /mnt/models -> /data/models
if [[ -L /mnt/models ]]; then
    rm /mnt/models
fi
if [[ -d /mnt/models ]]; then
    echo "  WARNING: /mnt/models is a directory, not removing"
else
    ln -s /data/models /mnt/models
    echo "  Created symlink /mnt/models -> /data/models"
fi

# Log directory
mkdir -p "$LOG_DIR"
chown -R ubuntu:ubuntu "$LOG_DIR"

echo "  Directory structure:"
echo "    /data/scratch      - Temporary processing files"
echo "    /data/models/      - ML models (Cinder volume)"
echo "    /mnt/models        -> /data/models (symlink)"
echo "    /mnt/data          - Shared volume mount point"

# =============================================================================
# 6. PYTHON VIRTUAL ENVIRONMENT
# =============================================================================
echo "[5/10] Setting up Python environment..."

python3 -m venv /opt/pipeline/venv
source /opt/pipeline/venv/bin/activate

pip install --upgrade pip -q
pip install -r /opt/pipeline/requirements.txt -q

# Install PyTorch with CUDA support
echo "  Installing PyTorch with CUDA..."
pip install torch --index-url https://download.pytorch.org/whl/cu118 -q

echo "  Python environment ready at /opt/pipeline/venv"

# =============================================================================
# 7. GENERATE .env FILE
# =============================================================================
echo "[6/10] Generating .env file..."

cat > /opt/pipeline/.env <<EOF
# GPU Worker Configuration
# Generated by setup-worker.sh on $(date -Iseconds)

# Redis
REDIS_HOST=${REDIS_HOST}
REDIS_PORT=${REDIS_PORT}

# PostgreSQL
POSTGRES_HOST=${POSTGRES_HOST}
POSTGRES_PORT=${POSTGRES_PORT}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# S3/Object Storage
S3_ENDPOINT=${S3_ENDPOINT}
S3_ACCESS_KEY=${S3_ACCESS_KEY}
S3_SECRET_KEY=${S3_SECRET_KEY}
S3_BUCKET=${S3_BUCKET}

# Paths
VOLUME_ROOT=${VOLUME_ROOT}
SCRATCH_ROOT=${SCRATCH_ROOT}
MODELS_ROOT=${MODELS_ROOT}
COPE_MODEL=${COPE_MODEL}
COPE_ADAPTER=${COPE_ADAPTER}
LOG_DIR=${LOG_DIR}

# Processing
WHISPERX_MODEL=${WHISPERX_MODEL}
GPU_BATCH_SIZE=${GPU_BATCH_SIZE}
FFMPEG_WORKERS=${FFMPEG_WORKERS}
OPUS_BITRATE=${OPUS_BITRATE}
CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}
EOF

chmod 600 /opt/pipeline/.env
chown ubuntu:ubuntu /opt/pipeline/.env
echo "  Created /opt/pipeline/.env"

# =============================================================================
# 8. CREATE SYSTEMD SERVICE FILES
# =============================================================================
echo "[7/10] Creating systemd services..."

# GPU Worker Service
cat > /etc/systemd/system/gpu-worker.service <<EOF
[Unit]
Description=GPU Worker - WhisperX transcription and CoPE-A classification
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/pipeline
EnvironmentFile=/opt/pipeline/.env
ExecStart=/opt/pipeline/venv/bin/python -m src.gpu_worker
Restart=always
RestartSec=30

# GPU workers need time to load models
TimeoutStartSec=300

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=gpu-worker

[Install]
WantedBy=multi-user.target
EOF

echo "  Created gpu-worker.service"

# Unpack Worker Service
cat > /etc/systemd/system/unpack-worker.service <<EOF
[Unit]
Description=Unpack Worker - Extracts archives and converts MP3 to Opus
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/pipeline
EnvironmentFile=/opt/pipeline/.env
ExecStart=/opt/pipeline/venv/bin/python -m src.unpack_worker
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=unpack-worker

[Install]
WantedBy=multi-user.target
EOF

echo "  Created unpack-worker.service"

systemctl daemon-reload
echo "  Reloaded systemd"

# =============================================================================
# 9. SCRATCH CLEANUP CRON JOB
# =============================================================================
echo "[8/10] Setting up scratch cleanup cron..."

# Cleanup files older than 24 hours in scratch directory
CRON_FILE="/etc/cron.d/pipeline-scratch-cleanup"

cat > "$CRON_FILE" <<EOF
# Clean up old files in scratch directory (every 6 hours)
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin

0 */6 * * * root find /data/scratch -type f -mmin +1440 -delete 2>/dev/null
EOF
chmod 644 "$CRON_FILE"

echo "  Added cron job to clean scratch files older than 24h"

# =============================================================================
# 10. SET OWNERSHIP
# =============================================================================
echo "[9/10] Setting file ownership..."

chown -R ubuntu:ubuntu /opt/pipeline
chown -R ubuntu:ubuntu /data 2>/dev/null || true
chown -R ubuntu:ubuntu /mnt/data 2>/dev/null || true

# =============================================================================
# 11. VERIFICATION
# =============================================================================
echo "[10/10] Verifying setup..."
echo ""

ERRORS=0

# Check mount
if [[ -b /dev/sdb ]]; then
    if mountpoint -q /data; then
        echo "  [OK] /data mounted"
    else
        echo "  [WARN] /data not mounted"
        ERRORS=$((ERRORS+1))
    fi
fi

# Check symlink
if [[ -L /mnt/models ]]; then
    echo "  [OK] /mnt/models symlink exists"
else
    echo "  [WARN] /mnt/models symlink missing"
    ERRORS=$((ERRORS+1))
fi

# Check Python venv
if [[ -f /opt/pipeline/venv/bin/python ]]; then
    echo "  [OK] Python venv exists"
else
    echo "  [FAIL] Python venv missing"
    ERRORS=$((ERRORS+1))
fi

# Check .env
if [[ -f /opt/pipeline/.env ]]; then
    echo "  [OK] .env file exists"
else
    echo "  [FAIL] .env file missing"
    ERRORS=$((ERRORS+1))
fi

# Check services
if systemctl list-unit-files | grep -q gpu-worker.service; then
    echo "  [OK] gpu-worker.service installed"
else
    echo "  [FAIL] gpu-worker.service not found"
    ERRORS=$((ERRORS+1))
fi

if systemctl list-unit-files | grep -q unpack-worker.service; then
    echo "  [OK] unpack-worker.service installed"
else
    echo "  [FAIL] unpack-worker.service not found"
    ERRORS=$((ERRORS+1))
fi

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Configuration:"
echo "  Python venv:  /opt/pipeline/venv"
echo "  Environment:  /opt/pipeline/.env"
echo "  Logs:         $LOG_DIR"
echo "  Scratch:      $SCRATCH_ROOT"
echo "  Models:       $MODELS_ROOT"
echo ""

if [[ $ERRORS -gt 0 ]]; then
    echo "WARNINGS: $ERRORS issues detected (see above)"
    echo ""
fi

echo "Next steps:"
echo "  1. Download models to /data/models/gemma-2-9b/"
echo "  2. Download adapter to /data/models/cope-a-adapter/"
echo "  3. Mount shared volume: sudo mount <device> /mnt/data"
echo "  4. Verify .env settings: cat /opt/pipeline/.env"
echo ""
echo "Start services:"
echo "  sudo systemctl enable --now gpu-worker unpack-worker"
echo ""
echo "Monitor logs:"
echo "  journalctl -u gpu-worker -f"
echo "  journalctl -u unpack-worker -f"
echo ""
echo "Verify models:"
echo "  ls /mnt/models/gemma-2-9b/*.safetensors"
echo "  ls /mnt/models/cope-a-adapter/"
