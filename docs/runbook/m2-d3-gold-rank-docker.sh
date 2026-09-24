#!/usr/bin/env bash
# Fresh gold-turn rank diagnostic on the CURRENT head (post-#4158), docker lane.
# Runs the COMMITTED driver next to this script (not a /tmp copy) and propagates
# its exit status, so a failed run is not read as success.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT="${1:-$SCRIPT_DIR/w7a-gold-rank-2026-09-21.json}"
cd "$REPO_ROOT" || exit 1
export TORTOISE_ASK_SHAPE_DB_URI="docker://:falkordb@localhost:6379/tortoise_test_matrix"
echo "=== gold-rank start $(date -u +%H:%M:%SZ) load=$(sysctl -n vm.loadavg | tr -d '{}') ==="
nice -n 19 uv run --no-sync python -P "$SCRIPT_DIR/m2-d3-gold-rank-docker.py" "$OUT"
rc=$?
echo "=== gold-rank rc=$rc done $(date -u +%H:%M:%SZ) ==="
exit "$rc"
