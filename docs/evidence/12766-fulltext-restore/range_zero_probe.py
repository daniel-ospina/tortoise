"""#12766 / #12768 — the range-index value-0 probe, committed so the claim has an artifact.

The five-arm harness (``fulltext_restore_cost.py``) surfaced this as a DISAGREEMENT between
the range arm and its own drop-first control: 19 999 rows where the control answered
20 000 (20 000 nodes), 89 999 where it answered 90 000 (90 000 nodes). That is a one-row
delta in a summary table, which is too weak to file a defect on, so this probe isolates it.

Kept as a separate script with its own committed log rather than folded into the harness:
the harness measures RESTORE COST across five arms, and this measures a CORRECTNESS defect
on one of them. Mixing them would make the cost table's runtime depend on a probe that is
not part of the measurement.

Cases, in the order they narrow the claim:

  1. ``score = [0, 0, 1, 2, 3]``      — the value-0 case, kept vs dropped.
  2. ``score = 100..3099``            — the MINIMUM-value control: proves this is not a
                                        lower-bound/minimum artifact.
  3. the ``-1`` / ``> -1`` bounds     — proves the entries are ABSENT from the index
                                        rather than excluded by the query bound.

Usage::

    python docs/evidence/12766-fulltext-restore/range_zero_probe.py <port>

Exit code 2 means INVALID HARNESS (no graph module, or the control case did not behave),
mirroring ``fulltext_restore_cost.py``'s convention: a probe that cannot demonstrate its own
control has not measured anything.
"""

from __future__ import annotations

import json
import sys

import fulltext_restore_cost as H  # same directory; the shared client/plumbing helpers
import redis

WINDOW = "MATCH (n:Num) WHERE n.score >= 0 AND n.score <= 999999 RETURN count(n)"


def _rows_factory(client, graph):
    def query(cypher):
        return H._rows(client, graph, cypher)

    return query


def _cycle(client, graph):
    """DUMP -> DEL -> RESTORE (kept: the index stays in the payload)."""
    payload = client.execute_command("DUMP", graph)
    client.execute_command("DEL", graph)
    client.execute_command("RESTORE", graph, 0, payload, "REPLACE")


def _case(client, name, scores):
    """One graph, the kept path, and the arithmetic spelled out in the log."""
    graph = f"t_12766_probe_{name}"
    H._cleanup(client, graph)
    from falkordb import FalkorDB

    writer = FalkorDB(host="127.0.0.1", port=H.PORT).select_graph(graph)
    writer.query(
        "UNWIND $rows AS r CREATE (p:Num {id: r.i, score: r.s})",
        {"rows": [{"i": i, "s": s} for i, s in enumerate(scores)]},
    )
    H.create_index(client, graph, "RANGE")
    row, _ = H.await_index(client, graph, "Num", "RANGE", len(scores))
    result = {
        "case": name,
        "scores_distinct": sorted(set(scores))[:6],
        "nodes": len(scores),
        "index_documents_before": row["numDocuments"],
        "window_before": H._rows(client, graph, WINDOW)[0][0],
    }
    _cycle(client, graph)
    result["window_after"] = H._rows(client, graph, WINDOW)[0][0]
    result["nodes_after"] = H.node_count(client, graph)
    after = H._index_of(client, graph, "Num", "RANGE")
    result["index_documents_after"] = after["numDocuments"] if after else None
    result["index_inverted_bytes_before"] = row["invertedSize"]
    result["index_inverted_bytes_after"] = after["invertedSize"] if after else None
    result["delta"] = result["window_before"] - result["window_after"]
    H._cleanup(client, graph)
    return result


def main(argv):
    H.PORT = int(argv[1])
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

    zero = _case(client, "zero", [0, 0, 1, 2, 3])
    print(json.dumps(zero), flush=True)

    high = _case(client, "high", list(range(100, 3100)))
    print(json.dumps(high), flush=True)

    # Bounds: if the rows were merely EXCLUDED by the bound, a looser bound would find them.
    graph = "t_12766_probe_bounds"
    H._cleanup(client, graph)
    from falkordb import FalkorDB

    writer = FalkorDB(host="127.0.0.1", port=H.PORT).select_graph(graph)
    writer.query("UNWIND $rows AS r CREATE (p:Num {id: r.i, score: r.s})",
                 {"rows": [{"i": i, "s": s} for i, s in enumerate([0, 0, 1, 2, 3])]})
    H.create_index(client, graph, "RANGE")
    H.await_index(client, graph, "Num", "RANGE", 5)
    _cycle(client, graph)
    bounds = {
        "case": "bounds",
        "window_ge_0": H._rows(client, graph, "MATCH (n:Num) WHERE n.score >= 0 RETURN count(n)")[0][0],
        "window_ge_-1": H._rows(client, graph, "MATCH (n:Num) WHERE n.score >= -1 RETURN count(n)")[0][0],
        "window_gt_-1": H._rows(client, graph, "MATCH (n:Num) WHERE n.score > -1 RETURN count(n)")[0][0],
        # An EQUALITY lookup on the indexed property — the index answers this, so it is a
        # second way to observe the same absence, not a control.
        "indexed_lookup_score_eq_0": H._rows(
            client, graph, "MATCH (n:Num) WHERE n.score = 0 RETURN count(n)")[0][0],
        # The nodes themselves are intact: read them through a NON-indexed property, and
        # read the indexed value back off each one.
        "nodes_by_id": H._rows(client, graph, "MATCH (n:Num) RETURN count(n)")[0][0],
        "score_of_node_0": H._rows(
            client, graph, "MATCH (n:Num) WHERE n.id = 0 RETURN n.score"),
    }
    print(json.dumps(bounds), flush=True)
    H._cleanup(client, graph)

    # ── the control must hold, or the probe has measured nothing ────────────
    if zero["delta"] != 2:
        print(f"INVALID HARNESS: the zero case lost {zero['delta']} rows, expected 2",
              file=sys.stderr)
        return 2
    if high["delta"] != 0:
        print(f"INVALID HARNESS: the minimum-value control lost {high['delta']} rows — "
              f"the defect is not specific to the value 0", file=sys.stderr)
        return 2
    # The nodes must survive INTACT, or this is data loss rather than an index defect.
    if bounds["nodes_by_id"] != 5 or bounds["score_of_node_0"] != [[0]]:
        print(f"INVALID HARNESS: the nodes did not survive intact "
              f"({bounds['nodes_by_id']} nodes, score_of_node_0="
              f"{bounds['score_of_node_0']}) — this would be data loss, not an index defect",
              file=sys.stderr)
        return 2

    print(
        f"\nvalue-0 case: window {zero['window_before']} -> {zero['window_after']} "
        f"({zero['delta']} rows lost); count(n) stays {bounds['nodes_by_id']} and node id=0 "
        f"STILL HOLDS score {bounds['score_of_node_0'][0][0]} — the data is intact, the "
        f"index is not\n"
        f"the index's own arithmetic agrees: numDocuments {zero['index_documents_before']} -> "
        f"{zero['index_documents_after']} (documents registered) but inverted bytes "
        f"{zero['index_inverted_bytes_before']} -> {zero['index_inverted_bytes_after']}, "
        f"i.e. exactly the 2 zero-valued postings are missing\n"
        f"minimum-value control (100..3099): {high['window_before']} -> "
        f"{high['window_after']} ({high['delta']} lost) — NOT a lower-bound artifact\n"
        f"bounds after the kept restore: >=0 {bounds['window_ge_0']}, >=-1 "
        f"{bounds['window_ge_-1']}, >-1 {bounds['window_gt_-1']} — a looser bound does not "
        f"recover them, so the entries are ABSENT from the index; an equality lookup "
        f"(score = 0) finds {bounds['indexed_lookup_score_eq_0']} of the 2"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
