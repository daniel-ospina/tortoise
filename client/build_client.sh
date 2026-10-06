#!/usr/bin/env bash
# Build the tortoise-client distribution (#526) — Prefect `prefect-client`
# pattern: stage the client subset of the `tortoise` package into a temp
# tree, overlay the client-only shim files, then build.
#
# Shared modules (mcp_client.py, config.py, exceptions.py,
# status_vocabulary.py) are COPIED from the canonical repo tree (tortoise/)
# at build time — single source of truth, zero drift. The `cp` lines below
# ARE that source: client/shared_modules.sh derives the module set from them
# for the CI `client` path gate and the wheel allowlists (#3805), so a
# new shared module is added HERE and nowhere else. The engine modules
# (sdk.py, projection, ep, ...) are never staged, so the wheel contains the
# thin driver only.
#
# Usage:
#   client/build_client.sh [OUT_DIR]     # default OUT_DIR = <repo>/dist-client
#
# Requires: python3 (>=3.12) with `build` OR `pip` available.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/dist-client}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# --- Stage the client tree -------------------------------------------------
mkdir -p "$STAGE/tortoise" "$STAGE/tortoise_client"

# Canonical shared modules — copied, never edited (drift guard: the copied
# file is byte-identical to the repo's, verified by the import/CI gate).
cp "$REPO_ROOT/tortoise/mcp_client.py" "$STAGE/tortoise/mcp_client.py"
cp "$REPO_ROOT/tortoise/config.py"     "$STAGE/tortoise/config.py"
cp "$REPO_ROOT/tortoise/exceptions.py" "$STAGE/tortoise/exceptions.py"
# #3805: the recorded status vocabulary — the ONE declaration both client
# surfaces import (the thin probe and the S9 skill-wiring client).
cp "$REPO_ROOT/tortoise/status_vocabulary.py" "$STAGE/tortoise/status_vocabulary.py"

# Client-only shim files (checked into client/ — NOT copies of the engine's
# __init__, which imports redislite).
cp "$SCRIPT_DIR/tortoise/__init__.py"          "$STAGE/tortoise/__init__.py"
cp "$SCRIPT_DIR/tortoise_client/__init__.py"   "$STAGE/tortoise_client/__init__.py"
cp "$SCRIPT_DIR/tortoise_client/cli.py"        "$STAGE/tortoise_client/cli.py"
cp "$SCRIPT_DIR/tortoise_client/__main__.py"   "$STAGE/tortoise_client/__main__.py"

cp "$SCRIPT_DIR/pyproject.toml" "$STAGE/pyproject.toml"
cp "$SCRIPT_DIR/README.md"      "$STAGE/README.md"
cp "$SCRIPT_DIR/LICENSE"        "$STAGE/LICENSE"

# --- Build -----------------------------------------------------------------
cd "$STAGE"
mkdir -p "$OUT_DIR"
if python3 -m build --version >/dev/null 2>&1; then
    python3 -m build --outdir "$OUT_DIR"
else
    echo "python3 -m build not available — falling back to 'pip wheel' (wheel only, no sdist)"
    python3 -m pip wheel . -w "$OUT_DIR" --no-deps --quiet
fi

echo "tortoise-client built -> $OUT_DIR"
ls -la "$OUT_DIR"/*.whl "$OUT_DIR"/*.tar.gz 2>/dev/null || ls -la "$OUT_DIR"
