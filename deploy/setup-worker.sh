#!/bin/bash
# Worker VM Setup Script
# Run as root or with sudo on each worker VM
# These VMs run the unpack and GPU workers

set -e

echo "=== Audio Pipeline Worker Setup ==="

# Configuration - UPDATE THESE
COORDINATOR_IP="${COORDINATOR_IP:-10.0.0.1}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-changeme_pipeline_password}"

# -----------------------------------------------------------------------------
# 1. System packages
# -----------------------------------------------------------------------------
echo "[1/6] Installing system packages..."
apt-get update
apt-get install -y python3-venv python3-pip ffmpeg git

# -----------------------------------------------------------------------------
# 2. GPU Drivers (if GPU worker)
# -----------------------------------------------------------------------------
echo "[2/6] Checking GPU setup..."

if lspci | grep -i nvidia > /dev/null 2>&1; then
    echo "NVIDIA GPU detected"

    # Check if drivers are installed
    if ! command -v nvidia-smi &> /dev/null; then
        echo "WARNING: NVIDIA drivers not installed."
        echo "Install with: sudo apt install nvidia-driver-535"
        echo "Or use cloud provider's GPU image"
    else
        nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    fi
else
    echo "No NVIDIA GPU detected (transfer VM or CPU-only worker)"
fi

# -----------------------------------------------------------------------------
# 3. Mount Shared Volume
# -----------------------------------------------------------------------------
echo "[3/6] Setting up shared volume mount..."

mkdir -p /mnt/data

if ! mountpoint -q /mnt/data; then
    echo "NOTE: Shared volume not mounted."
    echo "Mount the volume manually with:"
    echo "  sudo mount /dev/YOUR_DEVICE /mnt/data"
    echo ""
    echo "For persistent mount, add to /etc/fstab:"
    echo "  /dev/YOUR_DEVICE  /mnt/data  ext4  defaults,nofail  0  2"
fi

# -----------------------------------------------------------------------------
# 4. Python Environment
# -----------------------------------------------------------------------------
echo "[4/6] Setting up Python environment..."

mkdir -p /opt/pipeline
python3 -m venv /opt/pipeline/venv

source /opt/pipeline/venv/bin/activate

# Base dependencies (all workers)
pip install --upgrade pip
pip install redis psycopg2-binary python-magic

# GPU worker dependencies (only if GPU present)
if command -v nvidia-smi &> /dev/null; then
    echo "Installing GPU dependencies..."
    pip install torch --index-url https://download.pytorch.org/whl/cu118
    pip install whisperx transformers peft bitsandbytes accelerate

    # Note: whisperx may need additional setup
    echo ""
    echo "NOTE: WhisperX requires ctranslate2. If issues occur, try:"
    echo "  pip install ctranslate2"
fi

# -----------------------------------------------------------------------------
# 5. Deploy Worker Code
# -----------------------------------------------------------------------------
echo "[5/6] Deploying worker code..."

# Copy worker files (assumes they're in current directory or specify path)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for file in config.py utils.py db.py unpack_worker.py gpu_worker.py transfer_sounds.py; do
    if [ -f "$SCRIPT_DIR/../$file" ]; then
        cp "$SCRIPT_DIR/../$file" /opt/pipeline/
        echo "  Copied $file"
    elif [ -f "/tmp/pipeline/$file" ]; then
        cp "/tmp/pipeline/$file" /opt/pipeline/
        echo "  Copied $file from /tmp/pipeline/"
    else
        echo "  WARNING: $file not found"
    fi
done

# Create environment file
cat > /opt/pipeline/.env <<EOF
REDIS_HOST=${COORDINATOR_IP}
REDIS_PORT=6379
POSTGRES_HOST=${COORDINATOR_IP}
POSTGRES_PORT=5432
POSTGRES_DB=audio_pipeline
POSTGRES_USER=pipeline
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
VOLUME_ROOT=/mnt/data
EOF

chmod 600 /opt/pipeline/.env
chown -R ubuntu:ubuntu /opt/pipeline

# -----------------------------------------------------------------------------
# 6. Install Systemd Services
# -----------------------------------------------------------------------------
echo "[6/6] Installing systemd services..."

# Copy service files
if [ -f "$SCRIPT_DIR/systemd/unpack-worker.service" ]; then
    cp "$SCRIPT_DIR/systemd/unpack-worker.service" /etc/systemd/system/
    echo "  Installed unpack-worker.service"
fi

if [ -f "$SCRIPT_DIR/systemd/gpu-worker.service" ]; then
    cp "$SCRIPT_DIR/systemd/gpu-worker.service" /etc/systemd/system/
    echo "  Installed gpu-worker.service"
fi

if [ -f "$SCRIPT_DIR/systemd/transfer-worker.service" ]; then
    cp "$SCRIPT_DIR/systemd/transfer-worker.service" /etc/systemd/system/
    echo "  Installed transfer-worker.service"
fi

# Update service files with coordinator IP
for svc in /etc/systemd/system/*-worker.service; do
    if [ -f "$svc" ]; then
        sed -i "s/REDIS_HOST=10.0.0.1/REDIS_HOST=${COORDINATOR_IP}/" "$svc"
        sed -i "s/POSTGRES_HOST=10.0.0.1/POSTGRES_HOST=${COORDINATOR_IP}/" "$svc"
    fi
done

systemctl daemon-reload

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Python venv: /opt/pipeline/venv"
echo "Worker code: /opt/pipeline/"
echo "Config:      /opt/pipeline/.env"
echo ""
echo "Next steps:"
echo "  1. Mount shared volume: sudo mount /dev/YOUR_DEVICE /mnt/data"
echo "  2. Update /opt/pipeline/.env with correct coordinator IP and password"
echo "  3. For GPU workers, ensure /models/cope-a-lora contains the LoRA adapter"
echo ""
echo "Start workers:"
echo "  # On GPU worker VMs:"
echo "  sudo systemctl enable --now unpack-worker"
echo "  sudo systemctl enable --now gpu-worker"
echo ""
echo "  # On transfer VM:"
echo "  sudo systemctl enable --now transfer-worker"
echo ""
echo "Monitor logs:"
echo "  journalctl -u unpack-worker -f"
echo "  journalctl -u gpu-worker -f"
echo "  journalctl -u transfer-worker -f"
echo ""
echo "Test connectivity:"
echo "  redis-cli -h ${COORDINATOR_IP} ping"
echo "  psql -h ${COORDINATOR_IP} -U pipeline -d audio_pipeline -c 'SELECT 1'"
