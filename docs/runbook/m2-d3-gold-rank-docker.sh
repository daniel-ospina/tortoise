#!/usr/bin/env bash
# Fresh gold-turn rank diagnostic on the CURRENT head (post-#4158), docker lane.
set -u
HUB="/Users/danielospina/Documents/GitHub/tortoise"
cd "$HUB" || exit 1
export TORTOISE_ASK_SHAPE_DB_URI="docker://:falkordb@localhost:6379/tortoise_test_matrix"
echo "=== gold-rank start $(date -u +%H:%M:%SZ) load=$(sysctl -n vm.loadavg | tr -d '{}') ==="
nice -n 19 uv run --no-sync python -P /tmp/askshape-m2/gold_rank_docker.py \
  /tmp/askshape-m2/w7a-gold-rank-2026-09-21.json
echo "=== gold-rank rc=$? done $(date -u +%H:%M:%SZ) ==="
