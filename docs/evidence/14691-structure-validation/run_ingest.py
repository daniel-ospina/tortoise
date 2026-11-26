#!/usr/bin/env python
"""#14691 T6 — step 1: write the structure corpus into a fresh private graph.

METHOD RULE (the constraint this lane is judged by): every write here goes
through the **Tortoise SDK**, over the public door a customer uses:

    TortoiseSDK.mine_knowledge_from_session(conversation=[...], session_id=...)

`tool_registry` maps the MCP tool `tortoise_mine_knowledge_from_session` straight
onto that method (`sdk_method="mine_knowledge_from_session"`), which routes to
`_capture_session` -> `_extract_session_v2` -> the extractor-v4 5-stage pipeline
(S1 story -> S2 map -> S3 search -> S4 gap review -> S5 embed + relation write).

Nothing in this file opens a socket, builds a Cypher string, or reads a record
store. `uv run` is not used because this box cannot build the worktree venv with
the `embeddings` extra offline (#14606 §9.1); `run.sh` pins `PYTHONPATH` at the
worktree and `TORTOISE_VENV` at a venv that carries the runtime deps, so
`import tortoise` resolves to THIS worktree's code — which is what T6 measures.

Records, per document and in total:
  * wall-clock ack time for the capture call
  * the FULL result envelope the product returned, verbatim (JSON), including
    `relation_write.edges` (the edges the product says it wrote), `warnings[]`
    (the product's own reports about its extraction) and `stats.llm` (tokens)
  * corpus size in characters, so the amplification ratio is computable
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

GRAPH = "xv14691_business_structure"


def corpus_chars(corpus: dict) -> int:
    return sum(len(turn.get("content") or "")
               for doc in corpus["documents"] for turn in doc["turns"])


def verdict(receipt: dict) -> int:
    """0 only when every document's capture SUCCEEDED.

    A receipt with no document at all is not a run (#14606's review finding: the
    phase that produces every later phase's input must not report success over a
    corpus it never wrote).
    """
    rows = receipt.get("documents") or []
    return 0 if rows and all(row.get("ok") is True for row in rows) else 1


def main() -> int:
    from tortoise.sdk import TortoiseSDK

    corpus = json.loads((HERE / "corpus" / "structure-corpus.json").read_text())

    # ── Isolate: a fresh, namespaced graph ───────────────────────────
    # `graph_name` is the data-plane tenancy seam, so this is its own graph on
    # this lane's private container — never `live-v5`, never the owner's
    # dogfood graph (recorded decision (d)).
    sdk = TortoiseSDK(graph_name=GRAPH,
                      event_log_path=str(OUT / "events.jsonl"))

    receipt: dict = {
        "graph_name": GRAPH,
        "db_uri": os.environ.get("TORTOISE_DB_URI", "").split("@")[-1],
        "corpus": str((HERE / "corpus" / "structure-corpus.json").relative_to(HERE)),
        "corpus_chars": corpus_chars(corpus),
        "documents": [],
    }

    # ── Confirm empty BEFORE any write ───────────────────────────────
    before = sdk.graph_overview()
    receipt["empty_before"] = before
    print("=== graph_overview BEFORE any write ===")
    print(json.dumps(before, indent=2, default=str)[:4000])

    # ── The graph's installed packs: the kind vocabulary this run may use ──
    # Recorded, not assumed: T6 assertion 1 asks whether each structure arrives
    # with the RIGHT entity type, and the answer is only interpretable against
    # the vocabulary the graph actually installs (#5163 gates undeclared kinds).
    try:
        receipt["installed_packs"] = sdk.graph_overview("namespaces")
    except Exception as exc:  # noqa: BLE001 — a read that fails is recorded
        receipt["installed_packs"] = {"error": f"{type(exc).__name__}: {exc}"}
    print("\n=== installed packs ===")
    print(json.dumps(receipt["installed_packs"], indent=2, default=str)[:3000])

    # ── Ingest through the product ───────────────────────────────────
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
                agent_name="14691-t6-structure",
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
            print(json.dumps(row["result"], indent=2, default=str)[:8000])

    after = sdk.graph_overview()
    receipt["graph_overview_after"] = after
    print("\n=== graph_overview AFTER ingest ===")
    print(json.dumps(after, indent=2, default=str)[:6000])

    sdk.close()
    (OUT / "ingest-receipt.json").write_text(
        json.dumps(receipt, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'ingest-receipt.json'}")
    code = verdict(receipt)
    if code:
        failed = [row["doc_id"] for row in receipt["documents"]
                  if row.get("ok") is not True]
        print(f"!! ingest NOT OK: {len(failed)} capture(s) failed "
              f"({', '.join(failed) or 'no documents'}) — the receipt is committed "
              "alongside a non-zero exit on purpose: the contradiction has to be "
              "visible, and nothing here may guess at a retry")
    return code


if __name__ == "__main__":
    sys.exit(main())
