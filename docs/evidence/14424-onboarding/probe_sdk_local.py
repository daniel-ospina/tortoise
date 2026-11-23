"""#14424 — the SDK door: write one Point, read it back, find it by search.

Reproduces the UNGATED half of the onboarding criterion on whatever tree the
caller runs it from (no extractor, no deploy, no hosted credential):

    export HOME=$(mktemp -d)   # ONE home for both lines: a second `mktemp -d`
                               # would point the probe at a DB `init` never made
    uv run python -m tortoise init          # embedded graph
    uv run python docs/evidence/14424-onboarding/probe_sdk_local.py

Prints one JSON document: per-step wall clock in milliseconds, the created
node verbatim, the by-id read, and the search hit that names the marker. Exit
0 only when the search RETURNS the item that was just written — a green write
plus an empty search is exactly the failure this probe exists to catch.
"""
from __future__ import annotations

import json
import sys
import time

MARKER = "The onboarding probe marker is ZQ-14424-ORCHID (local SDK door)."
QUERY = "ZQ-14424-ORCHID"


def main() -> int:
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK()
    steps: list[dict] = []

    t = time.monotonic()
    created = sdk.create_entity("point", MARKER, pointKind="statement")
    node = created.get("node") or created
    steps.append({
        "step": "create_entity",
        "ms": round((time.monotonic() - t) * 1000, 1),
        "response": json.loads(json.dumps(created, default=str)),
    })

    point_id = node.get("id")
    if not point_id:  # the shape a verifier must not trust alone — sdk.py:27220
        print(json.dumps({"error": "create returned no node.id", "steps": steps}, indent=1))
        return 1

    t = time.monotonic()
    read_back = sdk.get_entity(point_id)
    steps.append({
        "step": "get_entity",
        "ms": round((time.monotonic() - t) * 1000, 1),
        "id": read_back.get("id"),
        "content": read_back.get("content"),
        "empty_answer": not bool(read_back),
    })

    t = time.monotonic()
    hits = sdk.search_knowledge(QUERY, limit=5)
    steps.append({
        "step": "search_knowledge",
        "ms": round((time.monotonic() - t) * 1000, 1),
        "query": QUERY,
        "hits": json.loads(json.dumps(hits, default=str)),
    })

    found = [
        h for h in (hits if isinstance(hits, list) else [])
        if h.get("id") == point_id or MARKER in (h.get("content") or "")
    ]
    out = {
        "marker": MARKER,
        "point_id": point_id,
        "read_back_ok": read_back.get("content") == MARKER,
        "search_found_it": bool(found),
        "steps": steps,
    }
    print(json.dumps(out, indent=1, default=str))
    return 0 if (out["read_back_ok"] and out["search_found_it"]) else 1


if __name__ == "__main__":
    sys.exit(main())
