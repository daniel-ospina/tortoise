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


#: `graph_overview()` paths that describe the CONNECTION, the installed KIND
#: VOCABULARY, a QUERY PARAMETER, or a DIAGNOSTIC CODE — never stored content, and
#: therefore legitimately non-empty on a brand-new graph.
#:   · `namespaces`      — the installed packs' kind vocabulary (`kind_count` 4–32)
#:   · `status.connected` — a flag
#:   · `stale.limit`     — the page size the overview asks for; reads 50 on an
#:                         EMPTY graph, and counting it refused every real run in
#:                         the first cut of this walk (measured)
#:   · `audit.exit_code` — the audit check's own exit status, not a population.
#:                         Its CONTENT signals (`node_count`, `edge_count`,
#:                         `summary.total_issues`) are counted on their own; scoring
#:                         this too reported a diagnostic code as "content" and
#:                         refused a legitimate run with the wrong reason.
#:                         ⚠️ CONSTRUCTED case, not a measurement: the receipt's own
#:                         `empty_before` carries `exit_code: 0` and no committed
#:                         artifact carries `3`. An earlier revision of this note
#:                         said "measured", which an artifact check falsified; the
#:                         falsifier drives the constructed case instead.
#: Matched by PATH SUBTREE (`p == key or p.startswith(key + '.')`), so a field that
#: later becomes an object does not silently start refusing every run.
_NOT_CONTENT = ("namespaces", "status.connected", "stale.limit", "audit.exit_code")


def _not_content(path: str) -> bool:
    return any(path == key or path.startswith(key + ".") for key in _NOT_CONTENT)


def occupancy(before) -> dict:
    """Every field of a `graph_overview()` reply that holds CONTENT.

    ⛔ THE ISOLATION PREMISE IS ENFORCED ON THE WHOLE REPLY, NOT ON TWO FIELDS
    (#14691 review, 5th cycle, item (d)). The guard below used to read only
    `taxonomy` and `structure.total`; a mutated `empty_before` carrying
    `status.total_entities=500`, `audit.node_count=900`, `audit.edge_count=1200`
    and a `pointkinds` list of a real product — a graph populated by every other
    measure the same reply carries — passed the guard AND the bundle's own pin.
    One of the two readers is now a walk over everything the overview says, so
    the refusal cannot be satisfied by the two fields that happened to be zero in
    the one graph observed.

    Pure (no SDK, no I/O) so the bundle's falsifier drives it with crafted
    overviews instead of trusting the `if` statement beside it to still be there.
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


#: The sections an overview must CARRY before its emptiness can be judged.
_OVERVIEW_KEYS = ("taxonomy", "structure", "status")


def _unmeasured_sections(before) -> list:
    """The required sections `before` does not MEASURE — absent, null, or empty.

    ⛔ A SECTION THAT IS PRESENT BUT EMPTY IS NOT A MEASUREMENT (#14691 review,
    8th cycle, P3). The guard in `refuse_if_not_fresh` used to test the KEYS
    alone — `set(_OVERVIEW_KEYS) <= set(before)` — so
    `{"taxonomy": {}, "structure": {"total": 0}, "status": None}` and the same
    with `"status": {}` were ACCEPTED: the reply carried the section and measured
    nothing, which is "absent is not empty" one level down. The real reply's
    `status` is `{"connected": true, "counts": {...}, "total_entities": 0}`
    (`out/ingest-receipt.json` → `empty_before.status`), so a null or empty
    section is never a legitimate shape — it is a carried-but-unmeasured field,
    and the freshness premise cannot rest on a field nobody read.
    """
    if not isinstance(before, dict):
        return list(_OVERVIEW_KEYS)
    return [key for key in _OVERVIEW_KEYS
            if not isinstance(before.get(key), dict) or not before.get(key)]


def refuse_if_not_fresh(before, graph: str = GRAPH) -> None:
    """Raise SystemExit unless `before` describes an EMPTY graph.

    ⛔ THE RAISE IS THE GUARD, so it is a FUNCTION and not an `if` inside
    `main()` (#14691 review, 5th cycle, N7). Measured: `if contamination:` →
    `if False:` left every test green, because the suite could only drive the
    PREDICATE (`occupancy`) and never the refusal that acts on it — a guard whose
    enforcement can be deleted while its evidence stays measurable. A falsifier
    can now call this and observe the SystemExit itself.
    """
    # ⛔ AN ABSENT OVERVIEW IS NOT A FRESH GRAPH (#14691 review, 6th round,
    # P3-7). `(before or {})` made `None` and `{}` walk to "no contamination" and
    # the run proceeded — a premise established by a measurement that never
    # happened. A refusal needs the measurement, so an unreadable overview is a
    # REFUSAL, not a pass. And CARRIED is not MEASURED: a present-but-null or
    # present-but-empty section is a field nobody read (#14691 review, 8th cycle,
    # P3 — `{"taxonomy": {}, "structure": {"total": 0}, "status": None}` was
    # ACCEPTED by the key-presence guard).
    if unmeasured := _unmeasured_sections(before):
        raise SystemExit(
            "REFUSING TO MEASURE A GRAPH THAT IS NOT FRESH: "
            f"{graph} returned no measurement for {unmeasured} — a section that is "
            "absent, null or empty is CARRIED, not measured, so the emptiness of "
            "the graph could not be established at all. The isolation premise is a "
            "MEASUREMENT, not an assumption.")
    occupied = {k: v for k, v in ((before or {}).get("taxonomy") or {}).items() if v}
    structure_total = ((before or {}).get("structure") or {}).get("total") or 0
    contamination = occupancy(before)
    if contamination:
        raise SystemExit(
            "REFUSING TO MEASURE A GRAPH THAT IS NOT FRESH: "
            f"{graph} already holds {occupied or 'no taxonomy rows'} "
            f"(structure.total={structure_total}); the overview also reports "
            f"content at {contamination}. T6's isolation premise is an "
            "empty graph; point TORTOISE_DB_URI/graph_name at a fresh one "
            "rather than reporting a contaminated run.")


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

    # ── Confirm empty BEFORE any write — and REFUSE if it is not ─────
    # ⛔ RECORDING the premise is not ENFORCING it (#14691 review, 2nd cycle).
    # An earlier revision assigned `receipt["empty_before"]` and returned, so a
    # run against a POPULATED graph exited 0 and produced a bundle whose every
    # A1/A2/A3 number silently describes a graph that was not fresh — the one
    # premise the whole measurement rests on (REPORT.md §2 quotes
    # `Point 0 Event 0 Subject 0 Object 0 Source 0`). Measured: replacing
    # `empty_before` with a populated overview left the bundle's own test suite
    # green and this script exit 0, because nothing read the field.
    # The taxonomy counts are the total entity population; `structure.total`
    # covers the operator/gate layer. Either being non-zero means this is not a
    # fresh graph, so the run REFUSES rather than measuring the wrong thing.
    before = sdk.graph_overview()
    receipt["empty_before"] = before
    print("=== graph_overview BEFORE any write ===")
    print(json.dumps(before, indent=2, default=str)[:4000])
    # The premise is ENFORCED, not merely recorded — in `refuse_if_not_fresh`,
    # which the bundle's falsifier drives directly (see its docstring).
    refuse_if_not_fresh(before)

    # ── The graph's installed packs: the kind vocabulary this run may use ──
    # Recorded, not assumed: T6 assertion 1 asks whether each structure arrives
    # with the RIGHT entity type, and the answer is only interpretable against
    # the vocabulary the graph actually installs (#5163 gates undeclared kinds).
    try:
        receipt["installed_packs"] = sdk.graph_overview("namespaces")
    except Exception as exc:
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
