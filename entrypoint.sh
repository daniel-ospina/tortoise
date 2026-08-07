#!/bin/bash
# ── Tortoise Hosted API Entrypoint ───────────────────────────────
# Resolves TORTOISE_DB_URI at runtime. Priority:
#   1. TORTOISE_DB_URI (already set — explicit override)
#   2. FALKORDB_CLOUD_URI (FalkorDB Cloud managed instance — production)
#   3. embedded redislite (local/dev)
#
# FalkorDB Cloud (managed) is the ONLY production database — it provides
# AOF durability, automated backups, and multi-tenancy. The former
# self-hosted falkordb-tortoise sidecar (AOF off, no backups) caused the
# 2026-08-05 data-loss incident and has been removed. If the cloud URI is
# missing in production, fail loudly rather than silently degrading.
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

# Embedding model cache — pre-downloaded at build time (Dockerfile ENV).
# Export defensively so it propagates even if the entrypoint is run outside
# the container image.
export HF_HOME="${HF_HOME:-/app/model}"
export SENTENCE_TRANSFORMERS_HOME="${SENTENCE_TRANSFORMERS_HOME:-/app/model}"

# Fast-fail if the pre-downloaded model cache is missing (code-review P3, #160)
# — the build-time bake in Dockerfile.hosted is the ONLY source; a missing
# cache means a broken image, not a retryable condition.
if [ ! -d "${HF_HOME}/models--sentence-transformers--all-MiniLM-L6-v2" ]; then
    echo "tortoise: FATAL — embedding model cache not found at ${HF_HOME}" >&2
    echo "tortoise: expected ${HF_HOME}/models--sentence-transformers--all-MiniLM-L6-v2" >&2
    exit 1
fi

# Embedding model pre-warm moved into the app: the FastAPI lifespan
# (tortoise/hosted_api.py _lifespan) spawns a daemon-thread pre-warm, so
# uvicorn binds 0.0.0.0:8000 IMMEDIATELY and /health passes on cold start.
#
# Previous behavior (fail-fast blocking pre-warm, #160) crashed deploys:
# EmbeddingModel._LOAD_TIMEOUT_S (30s) is shorter than a cold 2-core/2GB VM
# needs to import torch + sentence-transformers + load the 90MB model, so
# get() returned None, the assert failed, the entrypoint exited 1, uvicorn
# never started, and the Fly health check timed out (issue #545).
#
# Embeddings are OPTIONAL — FTS + structural RRF work without them
# (embeddings.py docstring). The model cache existence check above still
# catches a broken image (missing bake) cheaply.
_IS_SERVER=0
for _arg in "$@"; do
    if echo "$_arg" | grep -q "uvicorn"; then
        _IS_SERVER=1
        break
    fi
done

if [ "$_IS_SERVER" = "1" ]; then
    echo "tortoise: uvicorn server — embedding pre-warm runs in-app (non-blocking, degraded-but-alive)"
else
    echo "tortoise: skipping embedding pre-warm (non-server command: release check)"
fi

if [ -n "${TORTOISE_DB_URI:-}" ]; then
    echo "tortoise: using explicit TORTOISE_DB_URI"
elif [ -n "${FALKORDB_CLOUD_URI:-}" ]; then
    export TORTOISE_DB_URI="${FALKORDB_CLOUD_URI}"
    echo "tortoise: using FalkorDB Cloud (managed) → ${TORTOISE_DB_URI}"
elif [ -n "${FLY_APP_NAME:-}" ]; then
    echo "tortoise: FATAL — FALKORDB_CLOUD_URI not set in production. Refusing to start with no durable DB." >&2
    exit 1
else
    echo "tortoise: using embedded redislite (local/dev mode)" >&2
fi

exec "$@"
