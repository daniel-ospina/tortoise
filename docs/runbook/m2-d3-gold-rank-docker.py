#!/usr/bin/env python3
"""Run docs/runbook/w7a_gold_rank_diagnostic.py on the DOCKER substrate.

The committed diagnostic is embedded-only (it builds TortoiseSDK on a
tempfile redislite path). Under fleet load the embedded lane is the known
blocker, so this driver monkeypatches ONLY the store the diagnostic opens:
TortoiseSDK is redirected at the docker FalkorDB (TORTOISE_ASK_SHAPE_DB_URI's
substrate), one run-unique scratch graph per measure() call.

It changes NOTHING about the measurement: same fixture, same five questions,
same CUT=40, same LIMIT=120/LEG_DEPTH=120, same dense/backlog arms, and it
reuses the diagnostic's own measure() verbatim.

Usage: m2-d3-gold-rank-docker.py <out.json>

The repo root is derived from this file's location (docs/runbook/x.py -> repo
root), so the diagnostic and the `tortoise` package are imported from the
worktree/branch this file lives in — NOT from a hardcoded checkout. Scratch
graphs are dropped in a best-effort cleanup.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)

spec = importlib.util.spec_from_file_location(
    "w7a", os.path.join(REPO, "docs/runbook/w7a_gold_rank_diagnostic.py"))
w7a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w7a)

import tortoise.sdk as sdk_mod  # noqa: E402

_real = sdk_mod.TortoiseSDK
# A run-unique token, not the PID: a recycled PID would otherwise reconnect to
# an already-populated scratch graph and seed into stale state.
_run_id = uuid.uuid4().hex[:8]
_created: list[str] = []


def docker_sdk(_path):
    name = f"w7a_rank_{_run_id}_{len(_created)}"
    _created.append(name)
    os.environ["TORTOISE_DB_URI"] = (
        f"docker://:falkordb@localhost:6379/{name}")
    return _real(None)


def _write_graph_list(out_path: str) -> None:
    """Record the scratch graph names beside the receipt (best-effort)."""
    path = os.path.join(os.path.dirname(os.path.abspath(out_path)),
                        "w7a_graphs.txt")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(_created))
    except OSError as e:  # never mask the measurement's exit status
        print(f"w7a-driver: could not write {path}: {e}", file=sys.stderr)


def _drop_graphs() -> None:
    """Best-effort GRAPH.DELETE of every scratch graph this run created."""
    if not _created:
        return
    try:
        sdk = _real(None)
    except Exception as e:  # cleanup never masks the measurement's rc
        print(f"w7a-driver: graph cleanup skipped: {e}", file=sys.stderr)
        return
    try:
        db = sdk._get_proj().db
        failed: list[tuple[str, Exception]] = []
        for name in _created:
            try:
                db.select_graph(name).delete()
            except Exception as e:
                # An absent graph is success; anything else is a real failure
                # that must be surfaced, not silently swallowed, or the
                # run-unique scratch graphs leak forever.
                if not sdk_mod.is_missing_graph_error(e):
                    failed.append((name, e))
        if failed:
            print(
                f"w7a-driver: {len(failed)}/{len(_created)} scratch graphs NOT "
                f"dropped: {[n for n, _ in failed[:3]]}", file=sys.stderr)
    except Exception as e:
        print(f"w7a-driver: graph cleanup incomplete: {e}", file=sys.stderr)
    finally:
        sdk.close()


w7a.TortoiseSDK = docker_sdk

out_path = sys.argv[1] if len(sys.argv) > 1 else ""
rc = 1
try:
    rc = w7a.main()
finally:
    if out_path:
        _write_graph_list(out_path)
    try:
        _drop_graphs()
    except Exception as e:
        print(f"w7a-driver: cleanup error: {e}", file=sys.stderr)
sys.exit(rc)
