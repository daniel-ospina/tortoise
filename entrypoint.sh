#!/bin/bash
# ── Tortoise Hosted API Entrypoint ───────────────────────────────
# Resolves TORTOISE_DB_URI at runtime. Priority:
#   1. TORTOISE_DB_URI (already set — explicit override)
#   2. FALKORDB_CLOUD_URI (FalkorDB Cloud managed instance — production)
#   3. FALKORDB_PASSWORD (self-hosted falkordb-tortoise sidecar fallback)
#   4. embedded redislite (local/dev)
#
# FalkorDB Cloud (managed) provides AOF durability, automated backups,
# and multi-tenancy — the production default. The self-hosted sidecar
# is a dev/fallback only.
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

if [ -n "${TORTOISE_DB_URI:-}" ]; then
    echo "tortoise: using explicit TORTOISE_DB_URI"
elif [ -n "${FALKORDB_CLOUD_URI:-}" ]; then
    export TORTOISE_DB_URI="${FALKORDB_CLOUD_URI}"
    echo "tortoise: using FalkorDB Cloud (managed) → ${TORTOISE_DB_URI}"
elif [ -n "${FALKORDB_PASSWORD:-}" ]; then
    export TORTOISE_DB_URI="redis://:${FALKORDB_PASSWORD}@falkordb-tortoise.internal:6379/tortoise"
    echo "tortoise: using falkordb-tortoise sidecar (dev/fallback)"
elif [ -n "${FLY_APP_NAME:-}" ]; then
    echo "tortoise: WARNING — no DB configured. SDK will refuse to start." >&2
else
    echo "tortoise: using embedded redislite (local/dev mode)" >&2
fi

exec "$@"
