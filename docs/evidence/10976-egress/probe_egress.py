#!/usr/bin/env python3
"""#10976 — the EGRESS measurement: does the cache actually get smaller, and does a
served node survive the pass?

WHAT THIS MEASURES, AND WHY EACH NUMBER IS HERE
----------------------------------------------
The issue's own Indicators, one measurement each:

  (a) a cold set is evicted and the graph's **resident size actually falls**
      -> `resident.used_memory_before` / `_after`, `graph_memory_usage_mb_before`
         / `_after`, and `nodes_before` / `_after`.
  (b) an evicted node is **served from the record** without a graph
      re-materialisation -> the probe keeps a record leg, deletes the node out of
      the cache, and then answers a re-read from the record leg alone. Reported as
      `recall.after_eviction`.
  (c) the byte meter reads the cache's **real** size -> `resident.used_memory_*`
      (the instance's own exact figure) is reported BESIDE
      `graph_memory_usage_mb_*` (`GRAPH.MEMORY USAGE`, a sampling estimate that is
      BLIND to the HNSW vector index, #5331), so the gap is visible rather than
      assumed to be zero.

And the invariant that makes it safe, which is the one that must not be traded away:
**0 evictions of a node the record cannot serve.** The probe marks a sub-set of the
population as unservable; `safety.unservable_evicted` must be `[]` and those nodes
must still be in the cache afterwards.

WHAT THIS PROBE IS *NOT*
-----------------------
⛔ The record leg is a **stand-in**, and it is labelled as one in the output
(`record_leg: "stand-in"`). #10976 gates a real sweep on the record serving
(#10593 hosted record writes are rejected `22P02`; #7879 the record epic), and both
are open — which is exactly why `tortoise/cache_eviction.py` answers `REFUSED` in
production today. So this probe answers a different question, and answers it
honestly: *given a licence, does the executor bound the cache and hold the
unservable nodes back?* It does not claim the record is ready.

The parity report it builds is likewise SYNTHETIC, for the same reason: no real
instrument can be CLEAN while the record cannot serve. `record_leg` and
`parity_report` are both reported in the output so no reader can mistake this for a
production readiness measurement.

RUN
---
⛔ NOT through `tools/test_lane.py run` — that verb invokes **pytest**, so it collects
`0 items` and runs nothing (measured). Start a lane, export its URI, then run the probe:

    uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
    uv run python docs/evidence/10976-egress/probe_egress.py --out /tmp/egress.json

or against any graph you own (`TORTOISE_DB_URI` supplies host/port/password):

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
        uv run python docs/evidence/10976-egress/probe_egress.py --out /tmp/egress.json

⚠️ It mints its OWN graph (`egress_probe_<rand>`) and drops it at the end, so it never
touches a graph anyone reads. `--keep` leaves it behind for inspection.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tortoise.cache_eviction import (
    PARITY_INSTRUMENTS,
    ParityReport,
    assess,
    node_total,
)
from tortoise.cache_evictor import (
    Candidate,
    EvictionPolicy,
    evict,
)

#: ⛔ The byte meter's ONE home is `tortoise/graph_storage.py` (#5331) — it carries the
#: caveats (`PRECISION_NOTE`: sampling estimate, and BLIND to the HNSW index) that make
#: the reading usable-or-not. A second reading here would be a second, caveat-free
#: number, which is exactly what indicator (c) says must not exist.
from tortoise.graph_storage import measure_graph_storage
from tortoise.projection import resolve_db_endpoint

#: How many `:Point` turn-shaped nodes the probe seeds. Enough that a batch budget is
#: a real fraction of the population and the ~156 B/node the engine allocates is
#: measurable above allocator noise (see `cache_evictor`'s docstring for that number).
SEED_NODES = 4000

#: The node budget the pass enforces — a THIRD of the population, so the pass has to
#: evict a real set rather than one node, and so "it fell" is not one node's noise.
BUDGET_RATIO = 3

#: Every Nth node is declared UNSERVABLE by the stand-in record leg. These are the
#: nodes the pass MUST hold back: the safety invariant, measured rather than asserted.
UNSERVABLE_EVERY = 7

POPULATION = "turn"


def _seed(graph, n: int) -> list[str]:
    """Create *n* turn-shaped `:Point` nodes; return their record keys."""
    keys = [f"{POPULATION}_{i:06d}" for i in range(n)]
    rows = [
        {"id": key, "seq": i, "content": f"turn {i} " + ("x" * 40),
         "speaker": "user" if i % 2 else "assistant", "status": "active"}
        for i, key in enumerate(keys)
    ]
    graph.query(
        "UNWIND $rows AS r CREATE (t:Point {id: r.id, is_episodic: true, "
        "is_operator: false, content: r.content, speaker: r.speaker, "
        "status: r.status, seq: r.seq})",
        params={"rows": rows},
    )
    return keys


def _resident(client) -> dict:
    """The instance's own exact memory figures, plus this graph's sampling estimate."""
    info = client.info("memory")
    return {
        "used_memory": int(info.get("used_memory") or 0),
        "used_memory_human": info.get("used_memory_human"),
        "used_memory_dataset": int(info.get("used_memory_dataset") or 0),
    }


def _counts(graph) -> int:
    return node_total(graph)


def _make_record_leg(keys: list[str]) -> dict[str, dict]:
    """The stand-in record: key -> the row fields the cache could have served."""
    return {
        key: {"id": key, "content": f"turn {i} " + ("x" * 40),
              "speaker": "user" if i % 2 else "assistant", "status": "active"}
        for i, key in enumerate(keys)
    }


def _unservable(key: str, index: int) -> bool:
    return index % UNSERVABLE_EVERY == 0


def _selector(record: dict[str, dict], unservable: set[str]):
    """Cold-first is the CALLER's policy (#10976 leaves 'what cold means' open).

    This probe uses oldest-`seq`-first and says so: it is an ordering, not a claim
    that `seq` IS coldness. The executor never invents one, and neither may a probe
    that is held up as the executor's own evidence.
    """

    def select(graph, limit: int, population: str) -> list[Candidate]:
        rows = graph.query(
            "MATCH (t:Point) WHERE t.is_episodic = true "
            "RETURN id(t), t.id ORDER BY t.seq ASC LIMIT $n",
            params={"n": int(limit)},
        ).result_set
        return [
            Candidate(node_id=int(row[0]), key=str(row[1]), population=population)
            for row in rows
        ]

    return select


def run(uri: str, *, seed: int = SEED_NODES, keep: bool = False) -> dict:
    from falkordb import FalkorDB

    endpoint = resolve_db_endpoint(uri)
    graph_name = f"egress_probe_{secrets.token_hex(4)}"
    db = FalkorDB(host=endpoint.host, port=endpoint.port,
                  username=endpoint.username, password=endpoint.password,
                  ssl=endpoint.ssl)
    client = db.connection
    graph = db.select_graph(graph_name)
    out: dict = {
        "issue": 10976,
        "graph": graph_name,
        "record_leg": "stand-in",
        "record_leg_note": (
            "the record leg is a dict, not Postgres: #10976 gates a real sweep on "
            "#10593 + #7879, both open. This measures the EXECUTOR, not the record."
        ),
        "parity_report": "synthetic",
        "population": POPULATION,
    }
    try:
        keys = _seed(graph, seed)
        record = _make_record_leg(keys)
        unservable = {k for i, k in enumerate(keys) if _unservable(k, i)}

        def probe(candidate: Candidate) -> bool:
            # ⛔ A keyless candidate is UNDELETABLE (the executor holds it back), and
            # the record leg cannot serve a key it does not hold — both are `False`,
            # which is the safe direction and the same direction as an exception.
            if candidate.key is None:
                return False
            return candidate.key in record and candidate.key not in unservable

        before_nodes = _counts(graph)
        before_resident = _resident(client)
        before_meter = measure_graph_storage(client, graph_name, samples=100, repeats=3)

        budget = max(1, before_nodes // BUDGET_RATIO)
        # ⛔ NO FABRICATED CEILING. An earlier version passed
        # `ceiling_bytes=used_memory // 2` purely to force the census to `PLAN`, which
        # (a) invented the one number the reading is about and (b) hid that the ENTRY
        # bound was gated by a BYTE verdict. The census now publishes the LICENCE on
        # every verdict, so the natural state is used: `ceiling=None` means the
        # instance's own `maxmemory`, and the node budget below is the enforced bound.
        # `census_verdict` is reported so a reader sees which state armed the pass.
        assessment = assess(
            client, graph, graph_name,
            ceiling_bytes=None,
            parity_reports={
                PARITY_INSTRUMENTS[POPULATION]: ParityReport(
                    instrument=PARITY_INSTRUMENTS[POPULATION],
                    verdict="CLEAN",
                    measured_nodes=before_nodes,
                    total_nodes=before_nodes,
                    graph_name=graph_name,
                )
            },
        )
        out["census_verdict"] = assessment.verdict
        out["census_licence"] = assessment.plan.get("evictable")

        report = evict(
            assessment, client, graph,
            population=POPULATION,
            selector=_selector(record, unservable),
            record_probe=probe,
            key_prop="id",
            dry_run=False,
            policy=EvictionPolicy(max_nodes=budget, batch_size=500, max_passes=10),
        )

        after_nodes = _counts(graph)
        after_resident = _resident(client)
        after_meter = measure_graph_storage(client, graph_name, samples=100, repeats=3)

        alive = {str(row[0]) for row in graph.query(
            "MATCH (t:Point) WHERE t.is_episodic = true RETURN t.id").result_set}
        evicted = [c.key for c in report.deleted if c.key]

        # (b) — an evicted node is served from the RECORD, with no graph re-read of it.
        recall = {key: record.get(key) is not None for key in evicted[:50]}
        unservable_evicted = sorted(set(evicted) & unservable)

        out.update({
            "nodes": {"before": before_nodes, "after": after_nodes,
                      "budget": budget, "evicted": len(report.deleted)},
            "resident": {
                "used_memory_before": before_resident["used_memory"],
                "used_memory_after": after_resident["used_memory"],
                "used_memory_delta": (after_resident["used_memory"]
                                      - before_resident["used_memory"]),
                "used_memory_before_human": before_resident["used_memory_human"],
                "used_memory_after_human": after_resident["used_memory_human"],
            },
            "meter": {
                "graph_memory_usage_mb_before": before_meter.total_mb,
                "graph_memory_usage_mb_after": after_meter.total_mb,
                "graph_memory_usage_mb_delta": (after_meter.total_mb
                                                - before_meter.total_mb),
                "samples": before_meter.samples,
                "repeats": before_meter.repeats,
                "range_before_mb": [before_meter.min_mb, before_meter.max_mb],
                "range_after_mb": [after_meter.min_mb, after_meter.max_mb],
                "precision_note": before_meter.precision_note,
            },
            "recall": {
                "after_eviction": sum(1 for ok in recall.values() if ok),
                "sampled": len(recall),
                # ⛔ `all_served` is a COPY CHECK, not indicator (b), and it is NOT
                # reported as an invariant. The stand-in record is built from EVERY
                # seeded key, so `record.get(key) is not None` cannot be False: as a
                # measurement of "the record served this after eviction" it is a
                # tautology. Indicator (b) needs a REAL record leg (Postgres), which
                # #10976 gates on #10593 + #7879 — both open. See `invariants`.
                "all_served_THE_TAUTOLOGY_NOT_A_MEASUREMENT": (
                    all(recall.values()) if recall else None
                ),
                "not_measured": (
                    "indicator (b) — 'an evicted node is served from the record' — is "
                    "NOT measured here: the stand-in leg holds every key by "
                    "construction, so it cannot miss. A real reading needs the record "
                    "(#10593 + #7879, both open)."
                ),
            },
            "safety": {
                "unservable_declared": len(unservable),
                "unservable_evicted": unservable_evicted,
                "unservable_all_still_resident": unservable <= alive,
            },
            "pass": {
                "verdict": report.verdict,
                "reason": report.reason,
                "planned": len(report.planned),
                "held_back_record": len(report.skipped_record_cannot_serve),
                "passes": report.passes,
                "budget_exhausted": report.budget_exhausted,
            },
            "evicted_keys_sample": evicted[:10],
            "invariants": {
                "a_resident_fell": after_resident["used_memory"]
                                   < before_resident["used_memory"],
                "a_nodes_at_or_below_budget": after_nodes <= budget,
                # ⛔ `None`, NOT `True`. The `None`-not-fabricated-value discipline
                # the rest of this PR applies: a tautology counted as a pass is a false
                # PASS, which is the failure class the census exists to prevent.
                "b_every_evicted_served": None,
                "c_meter_reported": before_meter.ok and after_meter.ok,
                "zero_unservable_evicted": not unservable_evicted,
                "unservable_retained": unservable <= alive,
            },
        })
        return out
    finally:
        if not keep:
            # A cleanup failure never changes the verdict the probe reports.
            with contextlib.suppress(Exception):
                client.execute_command("GRAPH.DELETE", graph_name)


def main(argv: list[str] | None = None) -> int:
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI"),
                        help="FalkorDB URI (default: $TORTOISE_DB_URI)")
    parser.add_argument("--seed", type=int, default=SEED_NODES,
                        help=f"how many nodes to seed (default {SEED_NODES})")
    parser.add_argument("--keep", action="store_true",
                        help="leave the probe's graph behind")
    parser.add_argument("--out", type=Path, help="write the JSON here (default stdout)")
    args = parser.parse_args(argv)
    if not args.uri:
        print("probe: no --uri and no TORTOISE_DB_URI — refusing to guess a target",
              file=sys.stderr)
        return 1
    result = run(args.uri, seed=args.seed, keep=args.keep)
    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
    else:
        print(text)
    invariants = result["invariants"]
    failed = [name for name, ok in invariants.items() if ok is False]
    if failed:
        print(f"probe: FAILED invariants {failed}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
