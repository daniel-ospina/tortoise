#!/usr/bin/env python
"""Measure what a `:GraphEvent` row ACTUALLY costs a FalkorDB graph (#13094).

WHY THIS EXISTS. `docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md` §5.1 is explicit
that the residency half of its own census is **UNMEASURED**: `#13083` measured the
PAYLOAD per node exactly (12,768 B over 67 `:GraphEvent` rows) and measured nothing
about what those rows cost the SERVER, because the only per-graph figure it trusted
was run-dependent drift. `#13094` needs the other number — *does moving this shape
out of the graph buy anything?* — so it is measured here rather than asserted.

WHAT IS MEASURED, and what the number means:

* `GRAPH.MEMORY USAGE <graph>` (per-graph, `total_graph_sz_mb`) — integer MB, far
  too coarse for a test-sized population, so it is reported but NOT used for the
  per-node slope;
* `INFO memory` `used_memory` at THREE populations — the slope between them is the
  per-node cost, and the slope is what is quoted. A single delta from an empty
  graph would fold in the two indexes' fixed cost and report a constant as a
  slope, which is the #13083 §5.1 error in the other direction.

The SCHEMA IS PRODUCTION'S *intent*: the three DDL statements below are `event_store.
ensure_event_schema`'s (`tortoise/event_store.py:410`, `:415`, `:421`), with ONE
divergence the production path cannot avoid — `:415` sends the module command
`GRAPH.CONSTRAINT CREATE …` through `g.query()`, which does not parse it, so the
constraint is never created in production at all; here it is issued the module way
(`execute_command`) against THIS probe graph, which is the only form that runs. The
measurement therefore covers the `event_id` index, the `seq` index and the unique
constraint when this script RUNS IT. ⚠️ THE TWO COMMITTED JSONs BESIDE IT PREDATE
THIS CORRECTION: both record the constraint REFUSED (`schema[1].installed == false`,
the graph-key-slot defect below), so their slopes were measured WITHOUT it —
re-run before quoting a coverage claim from them.
A measurement against an un-indexed graph would understate the
residency of the exact shape this issue is about.

The PAYLOAD is the census's own size: `#13083` §4 measured 12,768 B over 67
`:GraphEvent` nodes ≈ 190 B each, which is the `PointAdded`-with-actor shape below.

READ-ONLY AGAINST PRODUCTION — this script never runs there. It REQUIRES
`TORTOISE_DB_URI` naming its OWN throwaway graph and deletes that graph at the end;
`--keep` leaves it for inspection.

Usage:

    uv run python docs/evidence/13094-graphevent/measure_graphevent_residency.py \
        --graph probe_13094_measure --sizes 20000 40000 60000 [--keep]

⚠️ Run it on a lane container (`tools/test_lane.py run -- <noop>`), never against a
tenant graph: it writes N synthetic `:GraphEvent` rows.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

GRAPH_PREFIX = "probe_13094"

#: `event_store.ensure_event_schema`'s three statements (`:410`, `:415`, `:421`), each
#: with HOW it must be issued. ⛔ The two `CREATE INDEX` spellings are production's
#: verbatim; the module command is NOT — it is re-keyed to the probe graph's own key
#: below, because the graph slot is what production gets wrong. The measurement is
#: meaningless without them — see the module docstring.
#:
#: ⚠️ The SECOND one is NOT Cypher and cannot go down `graph.query()`: `GRAPH.
#: CONSTRAINT` is a module command, and `ensure_event_schema` sends it through
#: `g.query()` inside a `try/except` that only logs (its own comment: *"FalkorDB
#: #664 crash reports on string-property unique constraints"*). So in production the
#: constraint is NEVER created and the store runs without it — which is why the
#: script records per-statement whether it was installed or refused instead of
#: claiming a schema it may not have. It therefore issues the module command the way
#: the module command is actually issued, against its own probe graph.
SCHEMA_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("cypher", "CREATE INDEX FOR (n:GraphEvent) ON (n.event_id)"),
    # ⛔ THE TOKEN AFTER `CREATE` IS THE **GRAPH KEY**, NOT A CONSTRAINT NAME.
    # `event_store.py:415` writes `GRAPH.CONSTRAINT CREATE tortoise_ge_uid …` and
    # sends it through `g.query()`, which cannot parse a module command at all, so
    # that spelling never reaches a server that could interpret it. Issued the way
    # the module command DOES run — `execute_command` — the same tokens put
    # `tortoise_ge_uid` in the graph slot and MINT THAT GRAPH, which carries no
    # `event_id` index; the `missing supporting exact-match index` this script used
    # to record was that freshly made graph failing its own precondition, NOT a
    # server limit on an indexed graph (#14183 review round 9, measured). With the
    # probe graph's own key here, an immediate constraint on an indexed graph is
    # ACCEPTED (`PENDING`) on `falkordb-server:v4.20.6`, empty and 20k-node graphs
    # alike; there is no asynchronous-index race to allow for.
    ("command", "GRAPH.CONSTRAINT CREATE {graph} UNIQUE NODE GraphEvent "
                "PROPERTIES 1 event_id"),
    ("cypher", "CREATE INDEX FOR (n:GraphEvent) ON (n.seq)"),
)

def command_tokens(statement: str, graph: str) -> list[str]:
    """The module command's argv, with the probe graph's key substituted as ONE token.

    ⛔ `GRAPH.CONSTRAINT CREATE <arg> …` takes the GRAPH KEY where a reader expects a
    constraint name, so the key is substituted into the template's own token list
    rather than re-split out of a formatted string — a graph key carrying whitespace
    would otherwise shift every following argument by one (#14183 review round 9,
    finding 6).
    """
    return [graph if token == "{graph}" else token for token in statement.split()]


PAYLOAD = json.dumps({
    "id": "01J8ZK3M4N5P6Q7R8S9T0V1W2X",
    "pointKind": "claim",
    "actor_user_id": "usr_01HZX",
})


def _payload(width: int | None) -> str:
    """The measured payload, padded (or not) to `width` bytes.

    `#13083` §4 measured **12,768 B over 67** `:GraphEvent` nodes ≈ **190 B each**
    on real tenant data, so `190` is the faithful figure and the default. The
    unpadded shape above is 88 B — the two are both reported so the slope can be
    read against the payload it was measured with, rather than assumed
    payload-independent.
    """
    if width is None or width <= len(PAYLOAD):
        return PAYLOAD
    return PAYLOAD[:-1] + ("x" * (width - len(PAYLOAD))) + "}"
TS = "2026-11-21T07:40:00.123456+00:00"
BATCH = 500


def _connection(uri: str):
    from falkordb import FalkorDB

    return FalkorDB.from_url(uri.replace("docker://", "redis://"))


def _used_memory(db) -> int:
    """The server's `used_memory`, in bytes. The only figure with byte resolution."""
    return int(db.connection.execute_command("INFO", "memory")["used_memory"])


def _graph_usage(db, graph: str) -> dict:
    """`GRAPH.MEMORY USAGE` as a dict. Reported, never used for the slope (MB)."""
    flat = db.connection.execute_command("GRAPH.MEMORY", "USAGE", graph)
    return dict(zip(flat[0::2], flat[1::2], strict=True))


def _create_batch(g, start: int, stop: int, payload: str) -> None:
    rows = [
        {
            "seq": i + 1,
            "ts": TS,
            "type": "PointAdded",
            "event_id": f"01J8ZK3M4N5P6Q7R8S9T{i:07d}",
            "payload": payload,
        }
        for i in range(start, stop)
    ]
    g.query(
        "UNWIND $rows AS r CREATE (:GraphEvent {seq:r.seq, ts:r.ts, "
        "type:r.type, event_id:r.event_id, payload:r.payload})",
        params={"rows": rows},
    )


def measure(uri: str, graph: str, sizes: list[int], keep: bool,
            payload_bytes: int | None = 190) -> dict:
    db = _connection(uri)
    if not graph.startswith(GRAPH_PREFIX):
        # exit 2, the code `tools/graphevent_home.py` uses for "could not run" —
        # a REFUSAL is not a measurement, and `1` is reserved for a produced
        # verdict in the sibling verb.
        print(f"refusing to write synthetic :GraphEvent rows into {graph!r}: the "
              f"name must start with {GRAPH_PREFIX!r} so a production graph can "
              f"never be measured by accident", file=sys.stderr)
        raise SystemExit(2)
    with contextlib.suppress(Exception):
        # an absent graph is the desired state
        db.connection.execute_command("GRAPH.DELETE", graph)
    g = db.select_graph(graph)
    g.query("CREATE (n:Warmup {i:0})")  # materialise the key before reading usage
    baseline_used = _used_memory(db)
    applied = []
    for kind, statement in SCHEMA_STATEMENTS:
        # a `command` statement carries the probe graph's key, so the module command
        # addresses the graph it measured instead of minting one named after what
        # looks like a constraint name.
        issued = statement.format(graph=graph)
        try:
            if kind == "cypher":
                g.query(issued)
            else:
                db.connection.execute_command(*command_tokens(statement, graph))
            applied.append({"statement": issued, "installed": True})
        except Exception as exc:  # the same tolerance production has
            applied.append({"statement": issued, "installed": False,
                            "refused": f"{type(exc).__name__}: {exc}"})
    g.query("MATCH (n:Warmup) DETACH DELETE n")
    after_schema = _used_memory(db)
    per_graph_after_schema = _graph_usage(db, graph)

    payload = _payload(payload_bytes)
    points = []
    created = 0
    for target in sizes:
        while created < target:
            stop = min(created + BATCH, target)
            _create_batch(g, created, stop, payload)
            created = stop
        points.append({"nodes": created, "used_memory": _used_memory(db)})

    out = {
        "graph": graph,
        "payload_bytes_per_event": len(payload),
        "schema": applied,
        # ⛔ A RUN WHOSE SCHEMA WAS REFUSED IS NOT A MEASUREMENT OF PRODUCTION'S
        # SCHEMA (#14183 review round 9, finding 6): the earlier cuts recorded the
        # refusal and published the slope anyway, which is how a false `why` reached
        # the census. `main()` exits 2 on this flag instead.
        "schema_complete": all(entry["installed"] for entry in applied),
        "used_memory_baseline": baseline_used,
        "used_memory_after_schema": after_schema,
        "schema_bytes": after_schema - baseline_used,
        "graph_memory_usage_after_schema": per_graph_after_schema,
        "points": points,
        "slope_bytes_per_node": _slope(points),
        "graph_memory_usage_final": _graph_usage(db, graph),
    }
    if not keep:
        db.connection.execute_command("GRAPH.DELETE", graph)
    return out


def _slope(points: list[dict]) -> float | None:
    """Bytes per node from the LAST two populations — the marginal cost.

    Two neighbouring populations, not the first and the last: the graph's own
    fixed cost (key tables, the two indexes above, and the unique constraint where
    the server accepts it) is paid once and would otherwise be divided into every
    node and reported as if it recurred. The marginal figure is the one that answers "does removing these
    rows give the memory back?".
    """
    if len(points) < 2:
        return None
    a, b = points[-2], points[-1]
    if b["nodes"] <= a["nodes"]:
        return None
    return (b["used_memory"] - a["used_memory"]) / (b["nodes"] - a["nodes"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", default=f"{GRAPH_PREFIX}_measure")
    parser.add_argument("--sizes", type=int, nargs="+", default=[20000, 40000, 60000])
    parser.add_argument("--payload-bytes", type=int, default=190,
                        help="pad the synthetic payload to this width (default 190, "
                             "#13083 §4's measured average)")
    parser.add_argument("--keep", action="store_true",
                        help="leave the measurement graph in place")
    args = parser.parse_args(argv)
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        print("TORTOISE_DB_URI is not set — this measurement needs its own graph",
              file=sys.stderr)
        return 2
    result = measure(uri, args.graph, args.sizes, args.keep, args.payload_bytes)
    print(json.dumps(result, indent=2))
    if not result["schema_complete"]:
        refused = [entry["statement"] for entry in result["schema"]
                   if not entry["installed"]]
        print("\nREFUSED: the schema statement(s) below did not install, so this run's "
              "population does not carry the schema it claims and its slope must NOT be "
              "quoted. 2 is \"could not run\", never a measurement:\n  "
              + "\n  ".join(refused), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
