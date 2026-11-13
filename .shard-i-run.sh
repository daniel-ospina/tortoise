#!/usr/bin/env bash
# Reproduce python-ci.yml's `test (i)` shard locally — the lane's OWN step
# (queue-is-the-blocker ruling, agent-infra#2936).
# Gating invocation copied from .github/workflows/python-ci.yml:1687.
set -o pipefail
cd "$(dirname "$0")" || exit 1
read -r -a FILES < /tmp/shard-i-files.txt
exec env FALKORDB_CONTAINER_MEMORY=8g uv run python tools/test_lane.py run -- \
  "${FILES[@]}" \
  -n 4 --dist loadscope -q --timeout=300 \
  -m 'not track_b and not live and not integration' \
  --maxfail=20 -r fEs
