#!/bin/bash
# Coordinator Diagnostic Script
# Run on the coordinator VM to verify Redis and PostgreSQL are correctly configured.
# Exit code: 0 if all checks pass, 1 if any check fails.

set -u

FAIL_COUNT=0

check() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "[OK]   $label"
    else
        echo "[FAIL] $label"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 1
    fi
}

echo "=== Coordinator Service Diagnostics ==="
echo ""

# ---- Redis ----
echo "--- Redis ---"

check "Redis service is active" systemctl is-active --quiet redis-server || \
    echo "       Fix: sudo systemctl start redis-server"

# Check Redis is listening on a non-loopback address
if ss -tlnp sport = :6379 | grep -qE '0\.0\.0\.0|::'; then
    echo "[OK]   Redis listening on all interfaces (0.0.0.0 / ::)"
else
    echo "[FAIL] Redis is NOT listening on a network interface"
    echo "       Fix: Ensure 'bind 0.0.0.0' is in the Redis config and restart."
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# Show effective bind directives and warn about conflicts
echo "       Effective bind directives:"
BIND_LINES=$(grep -rh '^\s*bind ' /etc/redis/ 2>/dev/null)
if [ -n "$BIND_LINES" ]; then
    echo "$BIND_LINES" | sed 's/^/         /'
    BIND_COUNT=$(echo "$BIND_LINES" | wc -l)
    if [ "$BIND_COUNT" -gt 1 ]; then
        echo "[FAIL] Multiple bind directives found — the last one wins and may"
        echo "       override your intended setting. Comment out the bind in the"
        echo "       main redis.conf so only pipeline.conf controls binding."
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
else
    echo "         (none found — defaults apply)"
fi

echo ""

# ---- PostgreSQL ----
echo "--- PostgreSQL ---"

check "PostgreSQL service is active" systemctl is-active --quiet postgresql || \
    echo "       Fix: sudo systemctl start postgresql"

# Check PostgreSQL is listening on a non-loopback address
if ss -tlnp sport = :5432 | grep -qE '0\.0\.0\.0|::'; then
    echo "[OK]   PostgreSQL listening on all interfaces (0.0.0.0 / ::)"
else
    echo "[FAIL] PostgreSQL is NOT listening on a network interface"
    echo "       Fix: Set listen_addresses = '*' in postgresql.conf and restart."
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# Show listen_addresses setting
echo "       listen_addresses = $(sudo -u postgres psql -tAc "SHOW listen_addresses" 2>/dev/null || echo '(unable to query)')"

# Show pg_hba.conf rules for transcript_user
echo "       pg_hba.conf rules for transcript_user:"
PG_VERSION=$(ls /etc/postgresql/ 2>/dev/null | head -1)
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
if [ -f "$PG_HBA" ]; then
    grep 'transcript_user' "$PG_HBA" | sed 's/^/         /'
    if ! grep -q 'transcript_user' "$PG_HBA"; then
        echo "         (no rules found — workers will be rejected)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
else
    echo "         (pg_hba.conf not found at $PG_HBA)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

echo ""

# ---- Summary ----
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "=== All checks passed ==="
    exit 0
else
    echo "=== $FAIL_COUNT check(s) failed ==="
    exit 1
fi
