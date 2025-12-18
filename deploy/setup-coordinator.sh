#!/bin/bash
# Coordinator VM Setup Script
# Run as root or with sudo on the coordinator VM
# This VM runs Redis, Postgres, and optionally a reporting dashboard

set -e

echo "=== Audio Pipeline Coordinator Setup ==="

# -----------------------------------------------------------------------------
# 1. System packages
# -----------------------------------------------------------------------------
echo "[1/6] Installing system packages..."
apt-get update
apt-get install -y redis-server postgresql postgresql-contrib

# -----------------------------------------------------------------------------
# 2. Redis Configuration
# -----------------------------------------------------------------------------
echo "[2/6] Configuring Redis..."

# Backup original config
cp /etc/redis/redis.conf /etc/redis/redis.conf.backup

# Configure Redis for network access
cat > /etc/redis/redis.conf.d/pipeline.conf <<EOF
# Audio pipeline Redis config
bind 0.0.0.0
maxmemory 4gb
maxmemory-policy allkeys-lru
# Optional: require password
# requirepass your_redis_password
EOF

# Create conf.d directory if it doesn't exist
mkdir -p /etc/redis/redis.conf.d

# Add include directive to main config if not present
if ! grep -q "include /etc/redis/redis.conf.d" /etc/redis/redis.conf; then
    echo "include /etc/redis/redis.conf.d/*.conf" >> /etc/redis/redis.conf
fi

systemctl restart redis-server
systemctl enable redis-server

# -----------------------------------------------------------------------------
# 3. PostgreSQL Configuration
# -----------------------------------------------------------------------------
echo "[3/6] Configuring PostgreSQL..."

# Create database and user
sudo -u postgres psql <<EOF
CREATE USER pipeline WITH PASSWORD 'changeme_pipeline_password';
CREATE DATABASE audio_pipeline OWNER pipeline;
GRANT ALL PRIVILEGES ON DATABASE audio_pipeline TO pipeline;
EOF

# Allow network connections (edit pg_hba.conf)
PG_VERSION=$(ls /etc/postgresql/)
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"

# Add line for internal network (adjust 10.0.0.0/24 to your subnet)
if ! grep -q "10.0.0.0/24" "$PG_HBA"; then
    echo "host    audio_pipeline  pipeline    10.0.0.0/24    md5" >> "$PG_HBA"
fi

# Listen on all interfaces
PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" "$PG_CONF"

systemctl restart postgresql
systemctl enable postgresql

# -----------------------------------------------------------------------------
# 4. Run Schema Migration
# -----------------------------------------------------------------------------
echo "[4/6] Running database schema migration..."

# Copy schema file to coordinator (assumes it's in current dir or specify path)
if [ -f "/opt/pipeline/schema.sql" ]; then
    sudo -u postgres psql -d audio_pipeline -f /opt/pipeline/schema.sql
    echo "Schema applied successfully"
else
    echo "WARNING: schema.sql not found at /opt/pipeline/schema.sql"
    echo "Copy schema.sql to /opt/pipeline/ and run:"
    echo "  sudo -u postgres psql -d audio_pipeline -f /opt/pipeline/schema.sql"
fi

# -----------------------------------------------------------------------------
# 5. Mount Shared Volume
# -----------------------------------------------------------------------------
echo "[5/6] Setting up shared volume mount..."

# Create mount point
mkdir -p /mnt/data

# Check if already mounted
if ! mountpoint -q /mnt/data; then
    # Get volume device (commonly /dev/sdb or /dev/nvme1n1)
    echo "NOTE: Shared volume not mounted."
    echo "Mount the volume manually with:"
    echo "  sudo mount /dev/YOUR_DEVICE /mnt/data"
    echo ""
    echo "For persistent mount, add to /etc/fstab:"
    echo "  /dev/YOUR_DEVICE  /mnt/data  ext4  defaults,nofail  0  2"
fi

# Create pipeline directories
mkdir -p /mnt/data/{incoming,unpacked,audio,processed}
chown -R ubuntu:ubuntu /mnt/data

# -----------------------------------------------------------------------------
# 6. Create Environment File
# -----------------------------------------------------------------------------
echo "[6/6] Creating environment file..."

mkdir -p /opt/pipeline
cat > /opt/pipeline/.env <<EOF
# Coordinator VM environment
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=audio_pipeline
POSTGRES_USER=pipeline
POSTGRES_PASSWORD=changeme_pipeline_password
VOLUME_ROOT=/mnt/data
EOF

chmod 600 /opt/pipeline/.env

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Services:"
echo "  Redis:      $(systemctl is-active redis-server)"
echo "  PostgreSQL: $(systemctl is-active postgresql)"
echo ""
echo "Next steps:"
echo "  1. Update /opt/pipeline/.env with secure password"
echo "  2. Mount shared volume: sudo mount /dev/YOUR_DEVICE /mnt/data"
echo "  3. Apply schema if not done: sudo -u postgres psql -d audio_pipeline -f /opt/pipeline/schema.sql"
echo "  4. Distribute coordinator IP to worker VMs (for REDIS_HOST, POSTGRES_HOST)"
echo ""
echo "Test connections:"
echo "  redis-cli ping"
echo "  psql -h localhost -U pipeline -d audio_pipeline -c 'SELECT 1'"
