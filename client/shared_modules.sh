#!/usr/bin/env bash
# The canonical shared modules staged into the tortoise-client wheel — ONE
# extractor, so that every consumer (the CI `client` path gate, the wheel
# whitelist at ci.yml, and verify_client.sh's Gate 0 allowlist) reads the
# SAME set (#3805; PR #4044 review).
#
# SOURCE OF TRUTH: the `cp "$REPO_ROOT/tortoise/<mod>.py" ...` lines in
# client/build_client.sh — the copies that literally ship in the wheel.
# This script only READS them. Never keep a second copy of the module names
# anywhere: a hand-maintained list is exactly what silently disabled the
# client-build leg when `tortoise/status_vocabulary.py` was staged (#4044).
#
# Only `$REPO_ROOT/tortoise/*` sources count — the `$SCRIPT_DIR/tortoise/*`
# copies are client-only SHIM files that overlay the wheel, not canonical
# engine-tree modules.
#
# Usage:
#   bash client/shared_modules.sh    # one module basename per line, sorted
#
# Fail-closed: an unparseable/empty derivation exits non-zero rather than
# printing nothing — a silent empty set would silently disable every caller.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$SCRIPT_DIR/build_client.sh"

if [[ ! -f "$BUILD" ]]; then
    echo "FAIL shared_modules.sh: $BUILD not found" >&2
    exit 1
fi

SHARED="$(grep -F 'cp "$REPO_ROOT/tortoise/' "$BUILD" \
    | grep -oE '[A-Za-z0-9_]+\.py' | sort -u || true)"

if [[ -z "$SHARED" ]]; then
    echo "FAIL shared_modules.sh: no canonical shared-module copies found in $BUILD" >&2
    echo "      (expected lines of the form: cp \"\$REPO_ROOT/tortoise/<mod>.py\" ...)" >&2
    exit 1
fi

printf '%s\n' "$SHARED"
