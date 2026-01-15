#!/bin/bash
# Pipeline Health Check Script
# Run on coordinator VM to check overall system health

set -e

# Configuration
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_DB="${POSTGRES_DB:-audio_pipeline}"
POSTGRES_USER="${POSTGRES_USER:-pipeline}"

echo "=== Audio Pipeline Health Check ==="
echo "Time: $(date)"
echo ""

# -----------------------------------------------------------------------------
# Redis Queue Depths
# -----------------------------------------------------------------------------
echo "--- Redis Queues ---"
UNPACK_QUEUE=$(redis-cli -h "$REDIS_HOST" LLEN list:unpack 2>/dev/null || echo "ERROR")
TRANSCRIBE_QUEUE=$(redis-cli -h "$REDIS_HOST" LLEN list:transcribe 2>/dev/null || echo "ERROR")
FAILED_QUEUE=$(redis-cli -h "$REDIS_HOST" LLEN list:failed 2>/dev/null || echo "ERROR")

printf "  %-20s %s\n" "Unpack queue:" "$UNPACK_QUEUE"
printf "  %-20s %s\n" "Transcribe queue:" "$TRANSCRIBE_QUEUE"
printf "  %-20s %s\n" "Failed queue:" "$FAILED_QUEUE"
echo ""

# Warning thresholds
if [ "$UNPACK_QUEUE" != "ERROR" ] && [ "$UNPACK_QUEUE" -gt 100 ]; then
    echo "  WARNING: Unpack queue backed up (>100)"
fi
if [ "$TRANSCRIBE_QUEUE" != "ERROR" ] && [ "$TRANSCRIBE_QUEUE" -gt 1000 ]; then
    echo "  WARNING: Transcribe queue backed up (>1000)"
fi

# -----------------------------------------------------------------------------
# Database Stats
# -----------------------------------------------------------------------------
echo "--- Database Stats (last 24h) ---"

psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "
SELECT
    'Processed:',
    COUNT(*)
FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours';
" 2>/dev/null || echo "  ERROR: Cannot connect to PostgreSQL"

psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "
SELECT
    'Flagged:',
    COUNT(*)
FROM audio_files af
JOIN classifications c ON c.audio_file_id = af.id
WHERE c.flagged = true
  AND af.created_at > NOW() - INTERVAL '24 hours';
" 2>/dev/null

psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "
SELECT
    'Failed:',
    COUNT(*)
FROM audio_files
WHERE status = 'failed'
  AND created_at > NOW() - INTERVAL '24 hours';
" 2>/dev/null

psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "
SELECT
    'RA Queue:',
    COUNT(*)
FROM ra_queue;
" 2>/dev/null

echo ""

# -----------------------------------------------------------------------------
# Status Breakdown
# -----------------------------------------------------------------------------
echo "--- Status Breakdown (last 24h) ---"
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT status, COUNT(*) as count
FROM audio_files
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY status
ORDER BY count DESC;
" 2>/dev/null

# -----------------------------------------------------------------------------
# Processing Rate
# -----------------------------------------------------------------------------
echo "--- Processing Rate (last 6 hours) ---"
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT
    TO_CHAR(DATE_TRUNC('hour', created_at), 'HH24:MI') as hour,
    COUNT(*) as files
FROM audio_files
WHERE created_at > NOW() - INTERVAL '6 hours'
GROUP BY DATE_TRUNC('hour', created_at)
ORDER BY DATE_TRUNC('hour', created_at) DESC;
" 2>/dev/null

# -----------------------------------------------------------------------------
# Worker Services Status (if on coordinator with systemd)
# -----------------------------------------------------------------------------
echo "--- Worker Services ---"
for svc in transfer-worker unpack-worker gpu-worker; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "  $svc: RUNNING"
    elif systemctl list-unit-files | grep -q "$svc" 2>/dev/null; then
        echo "  $svc: STOPPED"
    else
        echo "  $svc: NOT INSTALLED (may be on worker VM)"
    fi
done
echo ""

# -----------------------------------------------------------------------------
# S3 Connectivity Check
# -----------------------------------------------------------------------------
echo "--- S3 Connectivity ---"
if [ -n "$S3_ENDPOINT" ] && [ -n "$S3_ACCESS_KEY" ] && [ -n "$S3_SECRET_KEY" ]; then
    PIPELINE_DIR="${PIPELINE_DIR:-/opt/pipeline}"
    PIPELINE_VENV="${PIPELINE_VENV:-$PIPELINE_DIR/venv}"
    if [ -f "$PIPELINE_VENV/bin/python" ]; then
        S3_STATUS=$("$PIPELINE_VENV/bin/python" -c "
import sys
sys.path.insert(0, '$PIPELINE_DIR')
from src.s3_utils import check_s3_connection
print('OK' if check_s3_connection() else 'FAIL')
" 2>/dev/null || echo "ERROR")
        echo "  S3 Connection: $S3_STATUS"
    else
        echo "  S3 Connection: SKIPPED (venv not found)"
    fi
else
    echo "  S3 Connection: SKIPPED (credentials not set)"
fi
echo ""

# -----------------------------------------------------------------------------
# Batch Tracking (Active Batches in Redis)
# -----------------------------------------------------------------------------
echo "--- Active Batches ---"
BATCH_KEYS=$(redis-cli -h "$REDIS_HOST" KEYS "batch:*:total" 2>/dev/null | wc -l | tr -d ' ')
echo "  Active batches: $BATCH_KEYS"

if [ "$BATCH_KEYS" != "0" ] && [ "$BATCH_KEYS" != "ERROR" ]; then
    echo "  Batch details:"
    redis-cli -h "$REDIS_HOST" KEYS "batch:*:total" 2>/dev/null | head -5 | while read -r key; do
        if [ -n "$key" ]; then
            BATCH_ID=$(echo "$key" | sed 's/batch:\(.*\):total/\1/')
            TOTAL=$(redis-cli -h "$REDIS_HOST" GET "$key" 2>/dev/null || echo "?")
            PROCESSED=$(redis-cli -h "$REDIS_HOST" GET "batch:$BATCH_ID:processed" 2>/dev/null || echo "?")
            echo "    - $BATCH_ID: $PROCESSED / $TOTAL"
        fi
    done
fi
echo ""

# -----------------------------------------------------------------------------
# Scratch Space (GPU Workers)
# -----------------------------------------------------------------------------
SCRATCH_ROOT="${SCRATCH_ROOT:-/data/scratch}"
if [ -d "$SCRATCH_ROOT" ]; then
    echo "--- Scratch Space ---"
    SCRATCH_USAGE=$(du -sh "$SCRATCH_ROOT" 2>/dev/null | cut -f1)
    SCRATCH_DIRS=$(find "$SCRATCH_ROOT" -maxdepth 1 -type d 2>/dev/null | wc -l)
    SCRATCH_DIRS=$((SCRATCH_DIRS - 1))  # Exclude parent
    OLD_DIRS=$(find "$SCRATCH_ROOT" -maxdepth 1 -type d -mmin +120 2>/dev/null | wc -l)
    OLD_DIRS=$((OLD_DIRS - 1))  # Exclude parent if old

    echo "  Total usage: $SCRATCH_USAGE"
    echo "  Active directories: $SCRATCH_DIRS"
    echo "  Old directories (>2h): $OLD_DIRS"

    if [ "$OLD_DIRS" -gt 0 ]; then
        echo "  WARNING: Old scratch directories detected - may indicate stuck batches"
    fi
    echo ""
fi

# -----------------------------------------------------------------------------
# Noon Target Check
# -----------------------------------------------------------------------------
HOUR=$(date +%H)
if [ "$HOUR" -ge 10 ] && [ "$HOUR" -lt 12 ]; then
    FLAGGED=$(psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -c "
        SELECT COUNT(*)
        FROM audio_files af
        JOIN classifications c ON c.audio_file_id = af.id
        WHERE c.flagged = true
          AND af.created_at > NOW() - INTERVAL '24 hours';
    " 2>/dev/null | tr -d ' ')

    if [ -n "$FLAGGED" ] && [ "$FLAGGED" -lt 200 ]; then
        echo "!!! ALERT: Only $FLAGGED flagged items. Target is 200 by noon !!!"
    fi
fi

echo "=== End Health Check ==="
