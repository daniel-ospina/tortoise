#!/usr/bin/env python
"""#14691 T1-T5 — step 1: write the noise corpus into a fresh private graph, TWICE.

METHOD RULE (the constraint this lane is judged by): every write goes through the
**Tortoise SDK**, over the public door a customer uses:

    TortoiseSDK.mine_knowledge_from_session(conversation=[...], session_id=...)

Nothing here builds a Cypher string or opens a socket of its own.

WHY TWO PASSES. T1 is *the* test this issue exists for: feed the same input twice
and the graph must NOT grow. So the run is pass 1 (every document, once) followed
by pass 2 (every document again). Pass 2 uses a DISTINCT `session_id` suffix
(`-replay`): a second, independent ingestion of the same words — the shape a user
re-uploads a document in — not an idempotent replay keyed on a session id we
already used. The same-session variant is deliberately NOT measured here and is
recorded as a gap in REPORT.md §6, because it answers a different question
(a retry of one capture) and the owner's failure mode is "previous graphs became
all noise" over repeated ingestion.

A `graph_overview()` snapshot is taken BEFORE and AFTER **every** document, in both
passes, so the per-document delta is measured rather than inferred from the
envelope's own self-report (`doc-06`'s T2 delta exists only at that resolution).

Records, per document and in total: wall-clock ack time, the FULL result envelope
the product returned (JSON) — `points[]` with each point's own `dedup` verdict,
`relation_write` (the edges the product says it wrote, with `predicate`,
`write_path` and `stated_predicate`), `stats` (entities/points/relations/merges/
deletions and `llm` tokens) — and the graph snapshot on both sides of the call.
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

GRAPH = "xv14691_noise"

#: (pass number, session-id suffix). Pass 2 must NOT reuse pass 1's session ids —
#: see the module docstring for why that is a different test.
PASSES = ((1, ""), (2, "-replay"))


def corpus_chars(corpus: dict) -> int:
    return sum(len(turn.get("content") or "")
               for doc in corpus["documents"] for turn in doc["turns"])


#: `graph_overview()` paths that describe the CONNECTION, the installed KIND
#: VOCABULARY, a QUERY PARAMETER, or a DIAGNOSTIC CODE — never stored content, so
#: they are legitimately non-empty on a brand-new graph. Matched by PATH SUBTREE.
_NOT_CONTENT = ("namespaces", "status.connected", "stale.limit", "audit.exit_code")


def _not_content(path: str) -> bool:
    return any(path == key or path.startswith(key + ".") for key in _NOT_CONTENT)


def occupancy(before) -> dict:
    """Every field of a `graph_overview()` reply that holds CONTENT.

    A walk over the WHOLE reply rather than the two fields that happen to be zero
    in one observed graph: the T6 bundle's guard learned this the hard way (a
    mutated overview carrying content in `status.total_entities` / `audit.*`
    passed a two-field check). Pure, so `score_t1_t5.py` can drive it with crafted
    overviews instead of trusting the `if` beside it to still be there.
    """
    found: dict = {}

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            if node and not _not_content(path):
                found[path] = f"{len(node)} rows"
        elif isinstance(node, bool):
            return                  # a flag (connected/clean), not a population
        elif isinstance(node, (int, float)) and node and not _not_content(path):
            found[path] = node

    walk(before, "")
    return found


_OVERVIEW_KEYS = ("taxonomy", "structure", "status")


def _unmeasured_sections(before) -> list:
    """The required sections `before` does not MEASURE — absent, null, or empty.

    A section that is present but empty is CARRIED, not measured: the freshness
    premise cannot rest on a field nobody read (T6 review, 8th cycle, P3).
    """
    if not isinstance(before, dict):
        return list(_OVERVIEW_KEYS)
    return [key for key in _OVERVIEW_KEYS
            if not isinstance(before.get(key), dict) or not before.get(key)]


def refuse_if_not_fresh(before, graph: str = GRAPH) -> None:
    """Raise SystemExit unless `before` describes an EMPTY graph.

    The raise lives in a FUNCTION so the bundle's suite can drive the REFUSAL and
    not merely its predicate.
    """
    if unmeasured := _unmeasured_sections(before):
        raise SystemExit(
            "REFUSING TO MEASURE A GRAPH THAT IS NOT FRESH: "
            f"{graph} returned no measurement for {unmeasured} — a section that is "
            "absent, null or empty is CARRIED, not measured, so the emptiness of "
            "the graph could not be established at all.")
    occupied = {k: v for k, v in ((before or {}).get("taxonomy") or {}).items() if v}
    contamination = occupancy(before)
    if occupied or contamination:
        raise SystemExit(
            "REFUSING TO MEASURE A GRAPH THAT IS NOT FRESH: "
            f"{graph} already holds {occupied or 'no taxonomy rows'}; the overview "
            f"also reports content at {contamination}. T1's premise is an empty "
            "graph; point TORTOISE_DB_URI/graph_name at a fresh one rather than "
            "reporting a contaminated run.")


def _stats(res: dict) -> dict:
    """The envelope's own accounting, or `{}` — never a KeyError mid-run."""
    stats = res.get("stats")
    return stats if isinstance(stats, dict) else {}


def main() -> int:
    from tortoise.sdk import TortoiseSDK

    corpus = json.loads((HERE / "corpus" / "noise-corpus.json").read_text())

    sdk = TortoiseSDK(graph_name=GRAPH, event_log_path=str(OUT / "events.jsonl"))

    receipt: dict = {
        "graph_name": GRAPH,
        "db_uri": os.environ.get("TORTOISE_DB_URI", "").split("@")[-1],
        "corpus": str((HERE / "corpus" / "noise-corpus.json").relative_to(HERE)),
        "corpus_chars": corpus_chars(corpus),
        "passes": [],
    }

    before = sdk.graph_overview()
    receipt["empty_before"] = before
    print("=== graph_overview BEFORE any write ===")
    print(json.dumps(before.get("taxonomy") if isinstance(before, dict) else before,
                     indent=2, default=str)[:2000])
    refuse_if_not_fresh(before)

    try:
        receipt["installed_packs"] = sdk.graph_overview("namespaces")
    except Exception as exc:
        receipt["installed_packs"] = {"error": f"{type(exc).__name__}: {exc}"}
    print("\n=== installed packs ===")
    print(json.dumps(receipt["installed_packs"], indent=2, default=str)[:2500])

    for pass_no, suffix in PASSES:
        record: dict = {
            "pass": pass_no,
            "session_suffix": suffix,
            "overview_before": sdk.graph_overview(),
            "documents": [],
        }
        for doc in corpus["documents"]:
            session_id = f"{doc['doc_id']}{suffix}"
            t0 = time.monotonic()
            row: dict = {
                "doc_id": doc["doc_id"],
                "session_id": session_id,
                "date": doc["date"],
                "title": doc["title"],
                "turns": len(doc["turns"]),
                "overview_before": sdk.graph_overview(),
            }
            try:
                res = sdk.mine_knowledge_from_session(
                    conversation=doc["turns"],
                    session_id=session_id,
                    agent_name=f"14691-t12-noise-p{pass_no}",
                )
                row["ok"] = True
                row["result"] = res
            except Exception as exc:  # the reason IS the finding — never mask it
                row["ok"] = False
                row["error"] = f"{type(exc).__name__}: {exc}"
            row["ack_seconds"] = round(time.monotonic() - t0, 2)
            row["overview_after"] = sdk.graph_overview()
            record["documents"].append(row)

            stats = _stats(row.get("result") or {})
            print(f"\n=== pass {pass_no} · {doc['doc_id']} "
                  f"({row['ack_seconds']}s, ok={row['ok']}) ===")
            print(json.dumps({k: v for k, v in row.items()
                              if k not in ("result", "overview_before",
                                           "overview_after")},
                             indent=2, default=str)[:1200])
            print("stats:", json.dumps({k: stats.get(k) for k in (
                "entities", "points", "events", "relations", "operators",
                "merges", "deletions", "noops")}, default=str))
            if isinstance(stats.get("llm"), dict):
                print("llm:", json.dumps({k: stats["llm"].get(k) for k in (
                    "calls", "prompt_tokens", "completion_tokens",
                    "cost_usd", "calls_without_cost")}, default=str))
            rw = (row.get("result") or {}).get("relation_write") or {}
            print(f"relation_write: written={rw.get('written')} "
                  f"dropped={rw.get('dropped')} folded={rw.get('folded')} "
                  f"edges={len(rw.get('edges') or [])}")
            for warn in ((row.get("result") or {}).get("warnings") or [])[:6]:
                print("  warn:", str(warn)[:160])
        record["overview_after"] = sdk.graph_overview()
        receipt["passes"].append(record)
        print(f"\n### after pass {pass_no}: "
              f"{json.dumps((record['overview_after'] or {}).get('taxonomy'), default=str)}")

    receipt["graph_overview_after"] = sdk.graph_overview()
    sdk.close()

    (OUT / "ingest-receipt.json").write_text(
        json.dumps(receipt, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'ingest-receipt.json'}")

    failed = [f"p{p['pass']}/{d['doc_id']}"
              for p in receipt["passes"] for d in p["documents"]
              if d.get("ok") is not True]
    if failed:
        print(f"!! ingest NOT OK: {len(failed)} capture(s) failed ({', '.join(failed)}) "
              "— the receipt is committed alongside a non-zero exit on purpose; "
              "nothing here retries or guesses")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
