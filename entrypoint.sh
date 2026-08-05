#!/bin/bash
# ── Tortoise Hosted API Entrypoint ───────────────────────────────
# Constructs TORTOISE_DB_URI from FALKORDB_PASSWORD at runtime.
# This avoids hardcoding secrets in fly.toml or the Docker image.
#
# Design: if FALKORDB_PASSWORD is set (via fly secrets) and
# TORTOISE_DB_URI is empty, auto-construct the connection string
# to the falkordb-tortoise sidecar on Fly.io's private network.
# Otherwise, pass through (local dev with embedded redislite).
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

if [ -n "${FALKORDB_PASSWORD:-}" ] && [ -z "${TORTOISE_DB_URI:-}" ]; then
    export TORTOISE_DB_URI="redis://:${FALKORDB_PASSWORD}@falkordb-tortoise.internal:6379/tortoise"
    echo "tortoise: constructed TORTOISE_DB_URI → falkordb-tortoise.internal:6379/tortoise"
elif [ -z "${TORTOISE_DB_URI:-}" ]; then
    if [ -n "${FLY_APP_NAME:-}" ]; then
        # Production but no DB: the SDK will catch this and crash with a clear message
        echo "tortoise: WARNING — FALKORDB_PASSWORD not set, TORTOISE_DB_URI empty. SDK will refuse to start." >&2
    else
        echo "tortoise: using embedded redislite (local/dev mode)" >&2
    fi
fi

exec "$@"
