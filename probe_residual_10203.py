"""Probe P3-1 (#10203 review round 13): is the ADD door's stale alias visible
to `check_consistency`, and can any door retract it once it exists?

Run on a live lane:
  TORTOISE_DB_URI='docker://:@127.0.0.1:PORT/g' .venv/bin/python probe_residual_10203.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tortoise.consistency import check_consistency
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

URI = os.environ["TORTOISE_DB_URI"]


def main() -> None:
    tmp = tempfile.mkdtemp()
    log_path = os.path.join(tmp, "j.jsonl")
    proj = FalkorProjection.from_uri(
        URI, graph_name=f"test_identprobe_{uuid.uuid4().hex[:8]}")
    try:
        events = [
            {"type": "PointAdded", "point": {
                "id": "p1", "content": "error ERR-4032 on deploy",
                "kind": "statement"}},
            # the #4042 "later same-file creation" shape: content replaced,
            # payload carries the E3 default spelling (no keys of its own).
            {"type": "PointAdded", "point": {
                "id": "p1", "content": "deploy is clean again",
                "kind": "statement", "search_keys": []}},
        ]
        log = EventLog(log_path)
        for ev in events:
            log.append(ev)
            proj.apply(ev)

        rows = proj.query(
            "MATCH (n:Point {id:'p1'}) RETURN n.content, n.search_keys"
        ).result_set
        print("NODE:", rows)

        result = check_consistency(log_path, proj)
        print("CHECK ok=", result["ok"], "divergence=", result["divergence"],
              "hash_match=", result["hash_match"])
        print("DIVERGENT POINTS:", result.get("divergent_points"))

        # Can a content edit retract it? (the only door that retracts today)
        proj.apply({"type": "PointRevised", "id": "p1",
                    "new_content": "deploy is clean again (edited)"})
        log.append({"type": "PointRevised", "id": "p1",
                    "new_content": "deploy is clean again (edited)"})
        rows = proj.query(
            "MATCH (n:Point {id:'p1'}) RETURN n.content, n.search_keys"
        ).result_set
        print("AFTER REVISE:", rows)
        result2 = check_consistency(log_path, proj)
        print("CHECK2 ok=", result2["ok"], "divergence=", result2["divergence"])
        print("CHECK2 DIVERGENT:", result2.get("divergent_points"))
    finally:
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.close()


if __name__ == "__main__":
    main()
