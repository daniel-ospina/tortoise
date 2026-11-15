"""#12766 — does the index DEFINITION travel inside the graph key's payload?

The five-arm harness restores under the **same** graph name it dumped, so it cannot tell
"the definition travelled in the payload" from "the engine kept the index definition in its
registry across the ``DEL``". #7846 hit the same confound and settled it with a DIFFERENT-name
restore (``docs/evidence/7846-indexed-graph-restore/payload_carriage.py``) and so does this.

  DUMP <src>  ->  DEL <src>  ->  RESTORE <dst>  (a name the payload has never carried)

If the index exists, is populated, and ANSWERS under ``<dst>``, the definition came out of
the payload. If it is absent or empty, the same-name result in the main harness was registry
retention and the main record must say so.

⛔ **The source is deleted FIRST, deliberately, and this is not a style choice.** #8270 is a
SIGSEGV of the whole instance on graph module 42006 (the pin) when an INDEXED payload is
restored under a name that differs from the payload's own graph name **while the source graph
still exists**. #7846 isolated it: ``kept`` (source present) dies; ``deleted`` (source gone
first) survives. This probe takes the ``deleted`` variant — which also happens to be the
variant the measured cycle performs anyway (``DUMP`` -> ``DEL`` -> ``RESTORE``), so the
carriage question is asked of the same operation the harness times.

Usage::

    python docs/evidence/12766-fulltext-restore/index_carriage_probe.py <port> [nodes]

Exit 3 means **NOT REPRODUCED** — the index did not survive a cross-name restore, which is a
FINDING (the same-name arm is registry retention), not a harness fault. Exit 2 stays
INVALID HARNESS: the index was not populated before the cycle, so nothing was measured.
"""

from __future__ import annotations

import json
import sys
import uuid

import fulltext_restore_cost as H
import redis

DEFAULT_NODES = 5000


def _carriage(client, kind, nodes):
    """DUMP -> DEL -> RESTORE under a NEW name, for one index kind."""
    src = f"t_12766_car_src_{uuid.uuid4().hex[:8]}"
    dst = f"t_12766_car_dst_{uuid.uuid4().hex[:8]}"
    label = "Doc" if kind == "FULLTEXT" else "Num"
    try:
        H.build(client, src, nodes)
        H.create_index(client, src, kind)
        row, _ = H.await_index(client, src, label, kind, nodes)
        # ⛔ `await_index` already refuses below this bound, so the comparison is not a second
        # gate (the same construct was DELETED from fulltext_restore_cost.py for that reason) —
        # it is kept here only to name which half failed in the error message.
        if (row["numDocuments"] or 0) < nodes:
            H._invalid_harness(
                f"the {kind} index on the SOURCE is not populated "
                f"({row['numDocuments']} of {nodes}) — nothing to carry"
            )
        source = {
            "label": label, "status": row["status"],
            "documents": row["numDocuments"], "terms": row["numTerms"],
            "inverted_bytes": row["invertedSize"],
        }
        if kind == "FULLTEXT":
            source["hits"] = H.fts_hits(client, src)
        else:
            source["rows"] = H.range_hits(client, src, nodes)
        # ⛔ The SOURCE must ANSWER, not merely be registered: #12768 is exactly the case where
        # `numDocuments` is right and the postings are short, so a source that registers N
        # documents and answers nothing has nothing meaningful to carry and no reading is taken.
        if (source.get("hits", source.get("rows")) or 0) <= 0:
            H._invalid_harness(
                f"the {kind} index on the SOURCE registers {row['numDocuments']} documents but "
                f"answers nothing — nothing measurable to carry"
            )

        payload = client.execute_command("DUMP", src)
        client.execute_command("DEL", src)
        # ⛔ source is GONE before the cross-name restore (#8270's `deleted` variant).
        client.execute_command("RESTORE", dst, 0, payload, "REPLACE")

        after = H._index_of(client, dst, label, kind)
        result = {
            "kind": kind, "nodes": nodes,
            "payload_bytes": len(payload),
            "source": source,
            "target": {
                "status": after["status"] if after else "ABSENT",
                "documents": after["numDocuments"] if after else None,
                "terms": after["numTerms"] if after else None,
                "inverted_bytes": after["invertedSize"] if after else None,
            },
            "target_nodes": H.node_count(client, dst),
            "source_still_present": H.node_count(client, src),
        }
        if after is not None:
            if kind == "FULLTEXT":
                result["target"]["hits"] = H.fts_hits(client, dst)
            else:
                result["target"]["rows"] = H.range_hits(client, dst, nodes)
        result["verdict"] = (
            "CARRIED-BY-THE-GRAPH-KEY"
            if after is not None
            and (after["numDocuments"] or 0) >= nodes
            # ⛔ AND ANSWERING. A target that registers every document while answering
            # nothing is H3 (the index data did not survive) — the exact failure this whole
            # probe exists to be able to report. Registration alone would mint
            # CARRIED-BY-THE-GRAPH-KEY over an empty index, i.e. a false PASS.
            # ⚠️ This is UNCONDITIONAL for FULLTEXT. For RANGE it is best-effort: `range_hits`
            # is a predicate count, and whether the planner consults the range index or answers
            # the predicate with a label scan is the engine's choice (README §6), so a
            # registered-but-empty RANGE index whose query fell to a scan would still pass.
            # #12768 is what that gap looks like from the other side, and it is disclosed
            # rather than papered over.
            and (result["target"].get("hits", result["target"].get("rows")) or 0) > 0
            else "NOT-CARRIED (the same-name arm was registry retention)"
        )
        return result
    finally:
        H._cleanup(client, src, dst)


def main(argv):
    H.PORT = int(argv[1])
    nodes = int(argv[2]) if len(argv) > 2 else DEFAULT_NODES
    client = redis.Redis(
        host="127.0.0.1", port=H.PORT, decode_responses=False,
        socket_timeout=H.CLIENT_TIMEOUT_S, socket_connect_timeout=10.0,
    )
    header = H.engine_header(client)
    H.ENGINE.update(header["start"])
    print(json.dumps(header), flush=True)
    if H.ENGINE.get("graph_module") is None:
        print("no graph module on this server", file=sys.stderr)
        return 2

    results = []
    for kind in ("FULLTEXT", "RANGE"):
        result = _carriage(client, kind, nodes)
        results.append(result)
        print(json.dumps(result), flush=True)

    print()
    for r in results:
        t = r["target"]
        answered = t.get("hits", t.get("rows"))
        print(
            f"{r['kind']:8s}: source {r['source']['status']} "
            f"({r['source']['documents']} docs, {r['source']['terms']} terms, "
            f"{r['source']['inverted_bytes']} inverted bytes) -> target {t['status']} "
            f"({t['documents']} docs, {t['terms']} terms, {t['inverted_bytes']} bytes, "
            f"answered {answered}) under a DIFFERENT name; "
            f"source graph gone ({r['source_still_present']})"
        )

    missing = [r["kind"] for r in results if r["verdict"] != "CARRIED-BY-THE-GRAPH-KEY"]
    if missing:
        print(
            f"\nNOT REPRODUCED: {', '.join(missing)} did not survive a cross-name restore — "
            f"the same-name arm is registry retention, not payload carriage",
            file=sys.stderr,
        )
        return 3
    print("\nVERDICT: the index DEFINITION travels inside the graph key's payload — a target "
          "graph that has never seen it comes back with the index populated and answering.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
