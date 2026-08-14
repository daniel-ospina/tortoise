#!/usr/bin/env bash
# tortoise-client acceptance gate (#526 §Acceptance / issue P3 smoke).
#
# Proves the thin-client boundary in a CLEAN venv with ONLY tortoise-client
# installed:
#   0. wheel content is a WHITELIST — only the client module set ships
#      (rejects package-form leaks like tortoise/projection/__init__.py and
#      ANY new engine module — not just a flat-module denylist)
#   1. import tortoise.mcp_client works (driver present)
#   2. import tortoise.sdk / tortoise.projection RAISES ImportError (no engine)
#   3. pip list carries NO engine deps (falkordb / falkordblite / numpy /
#      scipy / fastapi) — direct OR installed
#
# The import checks run from a NEUTRAL temp dir with `python -I` (isolated
# mode) so `import tortoise.*` resolves to the installed WHEEL, never to the
# repo's engine tree (running from the repo root put CWD at sys.path[0] and
# shadowed the venv wheel, making the gate vacuous — PR #1313 conf 88). An
# explicit `__file__` assertion pins every import to the venv site-packages
# as a second guard.
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
WHEEL="$(cd "$(dirname "$WHEEL")" && pwd)/$(basename "$WHEEL")"

VENV="$(mktemp -d)"
NEUTRAL="$(mktemp -d)"   # neutral CWD for import checks — never the repo root
trap 'rm -rf "$VENV" "$NEUTRAL"' EXIT
python3.12 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip 2>/dev/null
"$VENV/bin/pip" install --quiet "$WHEEL"

echo "== Gate 0: wheel content — whitelist (thin driver only) =="
ALLOWED_TORTOISE="__init__.py mcp_client.py config.py exceptions.py"
ALLOWED_TORTOISE_CLIENT="__init__.py __main__.py cli.py"
BAD_ENTRIES=()
while IFS= read -r entry; do
    case "$entry" in
        tortoise/*)
            mod="${entry#tortoise/}"
            if [[ " $ALLOWED_TORTOISE " != *" $mod "* ]]; then
                BAD_ENTRIES+=("$entry")
            fi
            ;;
        tortoise_client/*)
            mod="${entry#tortoise_client/}"
            if [[ " $ALLOWED_TORTOISE_CLIENT " != *" $mod "* ]]; then
                BAD_ENTRIES+=("$entry")
            fi
            ;;
    esac
done < <(unzip -Z1 "$WHEEL" | grep -E '^(tortoise|tortoise_client)/' | sort)
if [[ ${#BAD_ENTRIES[@]} -gt 0 ]]; then
    echo "FAIL wheel ships entries outside the client allowlist:" >&2
    printf '  - %s\n' "${BAD_ENTRIES[@]}" >&2
    exit 1
fi
echo "OK  wheel whitelist — tortoise/{__init__,mcp_client,config,exceptions}.py + tortoise_client shim only"

echo "== Gate 1: driver import (neutral cwd, isolated mode, venv wheel) =="
cd "$NEUTRAL"
VENV="$VENV" "$VENV/bin/python" -I -c "
import os
import tortoise.mcp_client as m
assert m.__file__.startswith(os.environ['VENV']), f'not from venv wheel: {m.__file__}'
print('OK  import tortoise.mcp_client ->', m.__file__)
"
VENV="$VENV" "$VENV/bin/python" -I -c "
import os
import tortoise_client as c
assert c.__file__.startswith(os.environ['VENV']), f'not from venv wheel: {c.__file__}'
print('OK  import tortoise_client ->', c.__version__)
"

echo "== Gate 2: engine modules must NOT import =="
for mod in tortoise.sdk tortoise.projection; do
    if "$VENV/bin/python" -I -c "import $mod" 2>/dev/null; then
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
