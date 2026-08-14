#!/usr/bin/env bash
# tortoise-client acceptance gate (#526 §Acceptance / issue P3 smoke).
#
# Proves the thin-client boundary in a CLEAN venv with ONLY tortoise-client
# installed:
#   1. import tortoise.mcp_client works (driver present)
#   2. import tortoise.sdk / tortoise.projection RAISES ImportError (no engine)
#   3. pip show / pip list carries NO engine deps (falkordb / falkordblite /
#      numpy / scipy / fastapi) — direct OR installed
#
# Usage:
#   client/verify_client.sh [WHEEL_PATH]     # default: dist-client/*.whl
#
# Requires: python3 >= 3.12. Network access (pip install from PyPI).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WHEEL="${1:-$(ls "$REPO_ROOT"/dist-client/*.whl 2>/dev/null | head -1)}"

if [[ -z "$WHEEL" || ! -f "$WHEEL" ]]; then
    echo "no wheel found — build first: client/build_client.sh" >&2
    exit 2
fi

VENV="$(mktemp -d)"
trap 'rm -rf "$VENV"' EXIT
python3.12 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip 2>/dev/null
"$VENV/bin/pip" install --quiet "$WHEEL"

echo "== Gate 1: driver import =="
"$VENV/bin/python" -c "import tortoise.mcp_client as m; print('OK  import tortoise.mcp_client ->', m.__file__)"
"$VENV/bin/python" -c "import tortoise_client as c; print('OK  import tortoise_client ->', c.__version__)"

echo "== Gate 2: engine modules must NOT import =="
for mod in tortoise.sdk tortoise.projection; do
    if "$VENV/bin/python" -c "import $mod" 2>/dev/null; then
        echo "FAIL $mod is importable in a client-only env — engine leaked into the client dist" >&2
        exit 1
    fi
    echo "OK  import $mod raises ImportError"
done

echo "== Gate 3: engine deps must NOT be installed =="
BANNED=(falkordb falkordblite numpy scipy fastapi)
VIOLATIONS=()
for dep in "${BANNED[@]}"; do
    if "$VENV/bin/pip" list 2>/dev/null | grep -qi "^${dep}[[:space:]=]"; then
        VIOLATIONS+=("$dep")
    fi
done
if [[ ${#VIOLATIONS[@]} -gt 0 ]]; then
    echo "FAIL engine deps installed by tortoise-client: ${VIOLATIONS[*]}" >&2
    "$VENV/bin/pip" list 2>/dev/null
    exit 1
fi
echo "OK  no engine deps installed (falkordb/falkordblite/numpy/scipy/fastapi absent)"

echo
echo "✅ tortoise-client acceptance gate PASSED: $WHEEL"
echo "    deps installed:"; "$VENV/bin/pip" list --format=freeze 2>/dev/null | grep -v "^pip=\|^setuptools="
