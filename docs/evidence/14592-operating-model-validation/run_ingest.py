#!/usr/bin/env python
"""#14592 Phase D — step 1: write the fresh corpus into a fresh graph.

METHOD RULE (the constraint this lane is judged by): every read and write here
goes through the **Tortoise SDK**. Nothing in this file opens a socket, builds a
Cypher string, or reads a record store directly. `uv run` is NOT used — this
box's main checkout venv is used with PYTHONPATH pinned at the worktree (see
run.sh), because the worktree venv cannot be built offline.

The SDK door used is the public one a customer uses:

    TortoiseSDK.mine_knowledge_from_session(conversation=[...], session_id=...)

which `tool_registry` maps the MCP tool `tortoise_mine_knowledge_from_session`
straight onto (`sdk_method="mine_knowledge_from_session"`), and which routes to
`TortoiseSDK._capture_session` -> `_extract_session_v2` -> the extractor-v4
5-stage pipeline (S1 story -> S2 map -> S3 real-backend search -> S4 gap review
-> S5 embed) in `tortoise/extractor_v2.py`.

Records, per document and in total:
  * wall-clock ack time for the capture call
  * the FULL result envelope the product returned, verbatim (JSON)
  * any usage/cost block the product reported (the "one token budget" the
    brief asks us to report)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

GRAPH = "xv14592_operating_model"


def main() -> int:
    from tortoise.sdk import TortoiseSDK

    corpus = json.loads((HERE / "corpus" / "corpus.json").read_text())

    # ── Isolate: a fresh, clearly-namespaced graph ────────────────────
    # `graph_name` is the data-plane tenancy seam, so this graph is its own
    # graph on the lane's private container — never `live-v5`, never the
    # owner's dogfood graph (recorded decision (b) in the brief).
    sdk = TortoiseSDK(graph_name=GRAPH, event_log_path=str(OUT / "events.jsonl"))

    receipt: dict = {
        "graph_name": GRAPH,
        "db_uri": os.environ.get("TORTOISE_DB_URI", "").split("@")[-1],
        "corpus": str((HERE / "corpus" / "corpus.json").relative_to(HERE)),
        "documents": [],
    }

    # ── Confirm empty BEFORE any write ────────────────────────────────
    before = sdk.graph_overview()
    receipt["empty_before"] = before
    print("=== graph_overview BEFORE any write ===")
    print(json.dumps(before, indent=2, default=str)[:4000])

    # ── Ingest through the product ────────────────────────────────────
    for doc in corpus["documents"]:
        t0 = time.monotonic()
        row: dict = {
            "doc_id": doc["doc_id"],
            "date": doc["date"],
            "title": doc["title"],
            "turns": len(doc["turns"]),
        }
        try:
            res = sdk.mine_knowledge_from_session(
                conversation=doc["turns"],
                session_id=doc["doc_id"],
                agent_name="14592-validation",
            )
            row["ok"] = True
            row["ack_seconds"] = round(time.monotonic() - t0, 2)
            row["result"] = res
        except Exception as exc:  # the reason IS the finding — never mask it
            row["ok"] = False
            row["ack_seconds"] = round(time.monotonic() - t0, 2)
            row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["documents"].append(row)
        print(f"\n=== {doc['doc_id']} ({row['ack_seconds']}s, ok={row['ok']}) ===")
        print(json.dumps({k: v for k, v in row.items() if k != "result"},
                         indent=2, default=str)[:2000])
        if row.get("result"):
            print(json.dumps(row["result"], indent=2, default=str)[:6000])

    after = sdk.graph_overview()
    receipt["graph_overview_after"] = after
    print("\n=== graph_overview AFTER ingest ===")
    print(json.dumps(after, indent=2, default=str)[:6000])

    sdk.close()
    (OUT / "ingest-receipt.json").write_text(
        json.dumps(receipt, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'ingest-receipt.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
