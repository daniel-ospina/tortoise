#!/usr/bin/env python3
"""`python tools/embedding_residual_scan.py` — attest an erasure across BOTH stores.

#11642's acceptance asks for erasure that *"spans both stores and is attested by
a residual scan"*. After `#4051` V1 moved the vector index to Postgres, every
vector exists TWICE — the record's `embedding`/`embedding_vec` column and the
graph node's `embedding` property, because writes stay in the graph — so
"erased" is a claim about both, and a delete's return code attests neither.

The scan itself lives in `tortoise/embedding_residual_scan.py`; this is the
operator verb around it. Exit codes are the verdict:

    0  CLEAN        both stores READ, in scope, neither holds a vector
    3  RESIDUE      a store answered and still holds one — a FINDING
    2  UNAVAILABLE  NO VERDICT (a store could not be read, or no graph handle)

⛔ `2` is deliberately not `0`: a scan that could not read a store must never be
reported as "no residue found".

Usage:

    # the whole scope, both stores
    uv run python tools/embedding_residual_scan.py \
        --org o_123 --graph g_abc --graph-uri "$TORTOISE_DB_URI"

    # machine-readable
    uv run python tools/embedding_residual_scan.py --org ... --graph ... --json

⚠️ The GRAPH half needs a live FalkorDB. When `--graph-uri` is absent the graph
half is REFUSED (exit 2) rather than silently reported clean — "I did not look"
is the one answer this tool must never dress up as "nothing there".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.embedding_residual_scan import (  # noqa: E402
    EXIT_CLEAN,
    EXIT_RESIDUE,
    EXIT_UNAVAILABLE,
    scan,
)


def _graph_handle(graph_uri: str | None, graph_name: str):
    """A read handle on the named graph, or ``None`` when it cannot be built.

    ⛔ Returning ``None`` (not raising) is deliberate: the caller turns it into
    the graph half's REFUSAL, so a missing URI is an explicit "not measured"
    rather than an exception an operator might read as a tool bug.
    """
    if not graph_uri:
        return None
    try:
        from falkordb import FalkorDB

        return FalkorDB.from_url(graph_uri).select_graph(graph_name)
    except Exception as e:  # noqa: BLE001, RUF100
        print(f"Warning: no graph handle ({type(e).__name__}: {e}). The graph "
              "half of the scan is NOT measured; the verdict cannot be CLEAN.",
              file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Residual-scan BOTH vector stores for one scope after an "
                    "erasure (#11642). Exit 0 CLEAN / 3 RESIDUE / 2 no verdict.")
    ap.add_argument("--org", required=True, help="Record org_id (the scope)")
    ap.add_argument("--graph", required=True,
                    help="Graph id: the record scope's graph_id AND the graph "
                         "name the graph half is read from")
    ap.add_argument("--record-dsn", default=None,
                    help="Postgres record DSN (default: $TORTOISE_RECORD_DSN)")
    ap.add_argument("--graph-uri", default=None,
                    help="FalkorDB URI for the graph half "
                         "(default: $TORTOISE_DB_URI). Absent → the graph half "
                         "is refused and the verdict is not CLEAN")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args(argv)

    graph_uri = args.graph_uri or os.environ.get("TORTOISE_DB_URI")
    graph = _graph_handle(graph_uri, args.graph)

    outcome = scan(
        org_id=args.org,
        graph_id=args.graph,
        record_dsn=args.record_dsn,
        graph=graph,
    )

    if args.json:
        print(json.dumps(outcome.as_dict(), indent=2, sort_keys=True,
                         default=str))
        return outcome.exit_code

    print(f"scope: org={outcome.org_id} graph={outcome.graph_id}")
    print(f"  record: {outcome.record_vectors!r}"
          + ("" if outcome.record_vectors is None
             else f"  {outcome.record_by_table}"))
    print(f"  graph : {outcome.graph_vectors!r}"
          + ("" if outcome.graph_vectors is None
             else f"  {outcome.graph_by_label}"))
    for refusal in outcome.refusals:
        print(f"  REFUSED — {refusal}", file=sys.stderr)

    verdict = outcome.verdict
    print(f"\nverdict: {verdict}")
    if verdict == "CLEAN":
        print("Both stores were read in scope and neither holds a vector.")
        return EXIT_CLEAN
    if verdict == "RESIDUE":
        print("A store still holds a vector for an erased scope — the erasure "
              "did not reach it. This is a FINDING, not a retry.")
        return EXIT_RESIDUE
    print("NO VERDICT: a store could not be read. This is NOT 'no residue'.",
          file=sys.stderr)
    return EXIT_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())
