#!/usr/bin/env bash
# Test-isolation lint (#221): forbid whole-graph DETACH DELETE on shared graphs.
#
# conftest gives every test its own graph name, so DETACH DELETE wipes only
# the test's own data. This lint is defense-in-depth — it flags NEW sites
# that capture TORTOISE_DB_URI at MODULE level (bypassing the per-test
# fixture) instead of reading it at call time.
#
# Usage: bash .ci-checks/check-test-isolation.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
VIOLATIONS=0

echo "== check: no MODULE-LEVEL TORTOISE_DB_URI captures =="
while IFS=: read -r file line content; do
  case "$file" in
    tests/conftest.py) continue ;;
  esac
  echo "  $file:$line: $content"
  VIOLATIONS=$((VIOLATIONS + 1))
done < <(grep -rnE "^(URI|_URI|URI=|_URI=).*os\.environ\.get\(\"TORTOISE_DB_URI\"" tests/ || true)

if [ "$VIOLATIONS" -gt 0 ]; then
  echo ""
  echo "⛔ $VIOLATIONS module-level URI capture(s) found — these bypass the"
  echo "   per-test graph fixture and leak shared state (#221)."
  echo "   Fix: read TORTOISE_DB_URI at call time inside a fixture/function."
  exit 1
fi
echo "✅ No module-level URI captures."
