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

# Pre-warm the embedding model at startup so the first request doesn't hit
# a cold-start timeout (issue #160). If the pre-downloaded model can't load
# from /app/model, fail fast — silently degraded embeddings are worse than
# a crash that Fly.io restarts.
#
# EmbeddingModel._LOAD_TIMEOUT_S (30s) gates this; the model lives on the
# container filesystem (~90MB), so loading is pure local I/O and should
# complete in <5s. If it doesn't, something is broken (missing file, OOM,
# corrupted cache) and we want the deploy to roll back.
if python3 -c "
from tortoise.embeddings import EmbeddingModel
m = EmbeddingModel.get()
assert m is not None, 'Embedding model failed to load'
print('embeddings: model pre-warmed OK')
" 2>&1; then
    echo "tortoise: embedding model ready"
else
    echo "tortoise: FATAL — embedding model pre-warm failed. Check /app/model cache." >&2
    exit 1
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
