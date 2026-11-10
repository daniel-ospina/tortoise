#!/usr/bin/env python3
"""#10976 — the LICENCE's staleness bound: a stale per-population report, measured.

THE DEFECT THIS MEASURES
------------------------
`tortoise/cache_eviction.assess()` licenses `plan["evictable"][i]["nodes"]`, and
`tortoise/cache_evictor.py` spends that number directly as its runaway cap
(`licence_remaining`). The staleness guard on that licence used to be ONLY a comparison
against the graph's **whole** node total (`node_total`, `MATCH (n) RETURN count(n)`) — a
report claiming more nodes than the graph holds is demoted, and everything under it passes.

That denominator is the wrong population. A `turn` report (the only population with a
parity instrument) claiming 20,000 nodes passes on a graph holding 25,000 nodes in total
of which only **500** are turns: 500 < 20,000 < 25,000. The licence then caps a sweep at
**40x** the population it is meant to bound.

⚠️ Nothing unserved can be deleted because of this — gate 3 asks the record per node,
immediately before each delete. What is weakened is the property the cap exists for:
*"evicting more nodes than the instrument measured is evicting nodes nothing proved."*

WHAT THIS PROBE DOES
--------------------
Mints its OWN graph (`licence_probe_<rand>`), seeds 25,000 `:Point` nodes of which 500
carry `is_episodic = true` (the turn population), writes a CLEAN parity report claiming
20,000 turns, and runs the real `assess()` over a declared byte ceiling it is over.

⛔ The report is stamped FRESH (`measured_at`, now). Staleness has TWO independent bounds
and this probe measures the SIZE one: an undated report would be demoted by the AGE bound
(`PARITY_REPORT_MAX_AGE_ENV`, #11720 — the sibling fix that landed first) before the
population bound was ever consulted, so the run would report a refusal that proves nothing
about the guard this file is evidence for.

    uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"
    uv run python docs/evidence/10976-licence/repro_licence.py \
        --uri "$TORTOISE_DB_URI" --out /tmp/licence.json

BOTH ARMS AT ONE HEAD (`--without-population-bound`)
---------------------------------------------------
`before.log` was first captured by running PRE-FIX code, which is why it printed a licence
of four keys: the pre-fix tree also predated #11720's `measured_at` / `age_seconds` /
`max_age_seconds`. Once #11720 landed on `main` and this branch was rebased onto it, those
two logs could no longer have come from one head, and the README's "the two differ only in
whether `assess()` applies the population bound" became false — a claim/evidence mismatch
(PR #11709 review, P2).

`--without-population-bound` re-captures the pre-fix arm AT THIS HEAD, so the claim is true
again and both logs are produced by one probe at one commit. It makes the population read
answer the report's OWN claim (`CLAIM`), which is precisely the state the pre-fix code was
in for this comparison: no demotion from that arm. It is NOT a claim that the graph holds
20,000 turns — the graph holds 500, as the log states on its own line — and it does not
touch the whole-graph check, which still passes here (20,000 < 25,000) exactly as it did
pre-fix. The seam is named, printed in the log, and recorded in the JSON artifact's
`population_bound` field, so no reader can mistake which arm they are holding.

Exit codes, keyed on the LICENCE: 0 no licence was issued (the bound fired), 4 a
licence WAS issued (the defect is still present). ⚠️ Not 2 — argparse exits 2 on a usage error, and a
mistyped command must not read as the defect arm.

Expected WITH the population bound (`POPULATION_RESIDENT_COUNT`, #10976):
``verdict: REFUSED``, ``plan["evictable"] == []`` — the report claims more turn nodes than
the graph holds. Expected WITHOUT it (the pre-fix code): ``verdict: PLAN`` with a licence
of **20000**, i.e. 40x the 500-node population.

READ-ONLY on anything but the throwaway graph it names, which it drops.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tortoise.cache_eviction import (
    PARITY_INSTRUMENTS,
    assess,
    load_parity_report,
)
from tortoise.projection import resolve_db_endpoint

#: Ordinary `:Point` nodes — resident, NOT in the turn population.
POINTS = 24_500
#: The turn population: `is_episodic = true` (the recorded delete predicate, #10017).
TURNS = 500
#: What the stale report claims the population holds. Below the graph's 25,000-node
#: total (so the whole-graph guard passes) and 40x the real population (so the
#: population bound refuses it).
CLAIM = 20_000
#: Nodes per CREATE — one statement per batch, so the probe does not issue 25,000
#: round trips against a single-threaded server.
BATCH = 1_000
#: A declared byte ceiling the instance is certainly over, so the assessment is not
#: `CLEAN` (the byte state this probe is not measuring).
CEILING = 1_000_000


def _seed(graph) -> None:
    for start in range(0, POINTS, BATCH):
        n = min(BATCH, POINTS - start)
        graph.query(
            "UNWIND range(0, $n - 1) AS i "
            "CREATE (:Point {seq: $start + i, content: 'filler'})",
            {"n": n, "start": start},
        )
    for start in range(0, TURNS, BATCH):
        n = min(BATCH, TURNS - start)
        graph.query(
            "UNWIND range(0, $n - 1) AS i "
            "CREATE (:Point {id: 's_t' + ($start + i), is_episodic: true, "
            "         seq: $start + i, content: 'turn'})",
            {"n": n, "start": start},
        )


def run(uri: str, *, out: Path | None = None,
        population_bound: bool = True) -> dict:
    from falkordb import FalkorDB

    endpoint = resolve_db_endpoint(uri)
    db = FalkorDB(host=endpoint.host, port=endpoint.port,
                  username=endpoint.username, password=endpoint.password,
                  ssl=endpoint.ssl)
    # ⛔ The census CLI refuses a `--graph` that disagrees with the URI's graph, and this
    # probe keeps the same discipline in-process: measure the graph the handle opened.
    name = f"licence_probe_{secrets.token_hex(4)}"
    graph = db.select_graph(name)
    payload: dict = {"issue": 10976, "graph": name}
    try:
        _seed(graph)
        # ⛔ The BASELINE numbers are read directly, not through the module under test:
        # the probe must be able to state the graph's shape on a build where the bound
        # does not exist, or the before/after columns would come from two different
        # scripts and neither could be trusted.
        payload["graph_nodes_total"] = int(
            graph.query("MATCH (n) RETURN count(n)").result_set[0][0])
        payload["turn_population_resident"] = int(
            graph.query("MATCH (t:Point) WHERE t.is_episodic = true "
                        "RETURN count(t)").result_set[0][0])

        report_path = Path(f"/tmp/{name}-parity.json")
        report_path.write_text(json.dumps({
            "instrument": PARITY_INSTRUMENTS["turn"],
            "graph": name,
            # ⛔ STAMPED FRESH, so the AGE bound cannot be what refuses this report.
            # The probe exists to measure the POPULATION bound, and an undated artifact
            # would be demoted by `assess`'s staleness arm (#11720) before the population
            # count was read — a refusal that proves nothing about the guard under test.
            "measured_at": datetime.now(UTC).isoformat().replace(
                "+00:00", "Z"),
            "population": {"episodic_graph_nodes": CLAIM},
            "dry_run": {"would_delete_turn_nodes": CLAIM},
            "verdict": "CLEAN",
        }), encoding="utf-8")
        payload["report_claim"] = CLAIM
        payload["report"] = str(report_path)
        payload["population_bound"] = population_bound

        report = load_parity_report(str(report_path), graph_name=name)
        payload["report_proves"] = report.proves()

        # ⛔ THE PRE-FIX ARM, at THIS head. Patching the module global is deliberate and
        # is the only way the two logs can come from one commit: `assess()` resolves
        # `population_resident_count` from its own module globals at call time, so
        # answering `CLAIM` here reproduces exactly the pre-fix state for this comparison
        # — the bound cannot fire — while the freshness arms (#11720) and the whole-graph
        # check still run unmodified. Unregistering the population instead would NOT
        # reproduce it: an unregistered population is a REFUSAL (R3), a different behaviour
        # that did not exist pre-fix.
        original_count = None
        if not population_bound:
            import tortoise.cache_eviction as _ce

            original_count = _ce.population_resident_count
            _ce.population_resident_count = (
                lambda _graph, _population: CLAIM)
        try:
            assessment = assess(db.connection, graph, name, ceiling_bytes=CEILING,
                                parity_reports={PARITY_INSTRUMENTS["turn"]: report})
        finally:
            if original_count is not None:
                _ce.population_resident_count = original_count
        payload["verdict"] = assessment.verdict
        payload["evictable"] = assessment.plan.get("evictable", [])
        payload["licence_nodes"] = [
            int(entry.get("nodes") or 0) for entry in payload["evictable"]
        ]
        payload["licence_x_population"] = (
            payload["licence_nodes"][0] / payload["turn_population_resident"]
            if payload["licence_nodes"] and payload["turn_population_resident"] else None
        )
        payload["problems"] = list(assessment.problems)
        payload["resident_bytes"] = assessment.state.resident_bytes
        payload["bound_bytes"] = assessment.state.bound_bytes
        report_path.unlink(missing_ok=True)
    finally:
        try:
            graph.delete()
        except Exception as exc:
            print(f"(could not drop throwaway graph {name}: {exc})", file=sys.stderr)

    if out is not None:
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uri", required=True,
                        help="a throwaway FalkorDB URI (tools/test_lane.py uri prints one)")
    parser.add_argument("--out", type=Path, help="write the full JSON artifact here")
    parser.add_argument(
        "--without-population-bound", action="store_false",
        dest="population_bound", default=True,
        help="capture the PRE-FIX arm at this head: make the population read answer the "
             "report's own claim, so the population bound cannot fire. Records "
             "population_bound=false in the artifact. The whole-graph check and the "
             "freshness arms (#11720) still run — this reproduces what the pre-fix code "
             "did for THIS comparison, it does not disable the guard's other bounds.")
    args = parser.parse_args(argv)

    payload = run(args.uri, out=args.out, population_bound=args.population_bound)
    print(f"graph                          {payload['graph']}")
    print(f"population bound               "
          f"{'ON (this branch)' if payload['population_bound'] else 'OFF (pre-fix arm)'}")
    print(f"MATCH (n) RETURN count(n)      {payload['graph_nodes_total']}")
    print(f"turn population (is_episodic)  {payload['turn_population_resident']}")
    print(f"stale report claims            {payload['report_claim']} "
          f"(CLEAN, proves={payload['report_proves']})")
    print(f"ceiling                        {payload['bound_bytes']} B declared "
          f"(resident {payload['resident_bytes']} B)")
    print(f"verdict                        {payload['verdict']}")
    print(f"licence                        {payload['evictable'] or 'none'}")
    if payload["licence_x_population"] is not None:
        print(f"licence / population           {payload['licence_x_population']:.0f}x")
    for problem in payload["problems"]:
        print(f"  · {problem}")
    # ⛔ KEYED ON THE LICENCE, NOT THE VERDICT, and not 2. The verdict is the census's
    # BYTE state, so a cache under its byte ceiling answers `CLEAN` while the licence is
    # still refused by the population bound — keying on the verdict would report that
    # healthy refusal as the defect. And argparse exits 2 on a usage error, so a mistyped
    # command must not be readable as either arm.
    return 0 if not payload["evictable"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
