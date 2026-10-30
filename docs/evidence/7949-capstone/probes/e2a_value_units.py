"""E2a measured: value-bearing planted units that STRICTLY survive, by id.

Why this exists. E2a's frozen denominator (the **14** value-bearing planted
units) is derived by rule in the pre-registration (§3.3): a planted unit is
value-bearing iff its `verbatim_anchor` contains a digit, minus the two named
exclusions. No committed artifact carried that denominator, so the capstone's
E2 row was scored BLOCKED — "the metric cannot be scored on the artifact that
exists". It can be scored; the artifact just had to be produced. This driver
produces it from a run's OWN graph, using only shipped instruments:

* the survival predicate is `grading.strict_survival_counts` (the same function
  the write-path runner grades with), and
* the Points come from `runner.snapshot_session` (the runner's own graph read),
  so nothing here re-implements a metric or wraps the pipeline.

The two exclusions are named in the file rather than derived: they are argued
in the pre-registration (a status code in a policy rule; a query-pattern name),
and a probe that re-derived them would be re-deciding a registered judgement.
`--check-denominator` prints the rule's total so the two 14s cannot drift apart
unnoticed.

Usage::

    export TORTOISE_DB_URI='docker://:<pw>@127.0.0.1:<port>/<graph>'
    python docs/evidence/7949-capstone/probes/e2a_value_units.py \\
        --graph capstone_measure_<head8>_<epoch> --out /tmp/e2a.json

`--inject-anchor` is the FALSIFIER, not a measurement mode: for every lost
value-bearing unit it injects one synthetic Point carrying that unit's verbatim
anchor. A counter that is inert reports the same number with and without it; a
counter that observes the anchor moves to 14/14. It exists so "1 of 14" can be
shown to be a measurement rather than a broken instrument — so when the injection
fails to recover the lost units the driver REFUSES (exit 2) instead of printing a
small number as if it were an observation.

Two preconditions are refused BEFORE any number is emitted, because an absent
population and a losing pipeline read IDENTICALLY through this counter:

* **no searchable backend** — with no `TORTOISE_DB_URI` the SDK falls back to an
  empty EMBEDDED graph ("Raises nothing — falls through to the default on
  empty/whitespace env values", `tortoise/config.py`), so `python
  e2a_value_units.py --graph <g>` in a shell that forgot the export reports a
  confident `0 of 14` about nothing.
* **an empty population** — a graph name that was never written (a typo, a reaped
  graph) is an empty READ, not an error (a FalkorDB graph IS a key in the
  keyspace; a missing key yields an empty result set), so
  `sum(per_session[*].memory_points) == 0` is REFUSED rather than scored. That is
  #9889 acceptance item 2's rule applied to E2a's population: a zero denominator
  must never print as a result.

Without them the probe's own falsifier CERTIFIES the failure: on an empty graph
`before` is `0/14` and the injection recovers all 14 synthetic anchors, so
`recovered 14 of lost 14` prints green beside a number that measured nothing.

Each per-session row reports the reading of the pass that produced it: on the
`--inject-anchor` pass `strict_all` is the INJECTED count (the one the anchors
moved) and the pre-injection figure is kept beside it as
`strict_all_as_written`, because the record shipped here exists precisely so both
readings are legible in one artifact — a field labelled `with_anchor` that
carries the as-written number is the reading the injection was meant to move,
reported as if it had not moved.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: §3.3's two named exclusions — a status code in a policy rule, and `N+1` as a
#: query-pattern name. Re-derived by the pre-registration's own check.
EXCLUDED_UNIT_IDS = frozenset({
    "wp02_lumen_refactor_u_08",
    "wp04_aurora_perf_u_04",
})


def value_bearing_ids(planted_units: list[dict]) -> list[str]:
    """§3.3's rule: a digit in the anchor, minus the two exclusions."""
    return [
        unit["id"] for unit in planted_units
        if unit.get("id") not in EXCLUDED_UNIT_IDS
        and any(ch.isdigit() for ch in (unit.get("verbatim_anchor") or ""))
    ]


def frozen_denominator() -> int:
    """§3.3's denominator, read off the gold — never typed into this file."""
    from tests.eval.write_path.corpus import session_ids

    total = 0
    for session_id in session_ids():
        gold = json.loads(gold_path(session_id).read_text())
        total += len(value_bearing_ids(gold.get("planted_units") or []))
    return total


def gold_path(session_id: str) -> Path:
    return (_ROOT / "tests" / "eval" / "write_path" / "gold"
            / f"{session_id}.gold.json")


def grade(graph: str, *, inject_anchor: bool) -> dict:
    from tests.eval.write_path import grading, runner
    from tests.eval.write_path.corpus import session_ids
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(graph_name=graph)
    survived_total = 0
    units_total = 0
    per_session: dict[str, dict] = {}
    for session_id in session_ids():
        gold = json.loads(gold_path(session_id).read_text())
        value_ids = value_bearing_ids(gold.get("planted_units") or [])
        snapshot = runner.snapshot_session(sdk, session_id)
        points = list(snapshot["points"])
        as_written = grading.strict_survival_counts(
            gold, points, snapshot["rephrase_edges"])
        survived = [uid for uid in value_ids
                    if uid in set(as_written["survived_ids"])]
        # The counts THIS pass observed: the pre-injection read, replaced by the
        # post-injection one below — so `strict_all` is never the other
        # condition's number (see the module docstring).
        reading = as_written
        if inject_anchor:
            anchors = {unit["id"]: unit.get("verbatim_anchor")
                       for unit in gold.get("planted_units") or []}
            for index, uid in enumerate(v for v in value_ids
                                        if v not in set(survived)):
                points.append({
                    "point_id": f"SYNTH_{session_id}_{index}",
                    "content": anchors.get(uid) or "",
                    "provenance_present": True,
                    "ep_updated": True,
                })
            with_anchor = grading.strict_survival_counts(
                gold, points, snapshot["rephrase_edges"])
            survived = [uid for uid in value_ids
                        if uid in set(with_anchor["survived_ids"])]
            reading = with_anchor
        units_total += len(value_ids)
        survived_total += len(survived)
        row = {
            "memory_points": len(snapshot["points"]),
            "value_ids": value_ids,
            "value_survived": survived,
            "value_lost": [v for v in value_ids if v not in set(survived)],
            "strict_all": f"{reading['survived']}/{reading['total']}",
        }
        if inject_anchor:
            # Both readings in ONE record — so the label on each is the whole
            # point. `strict_all` above is what the injection produced; the
            # number it moved away from keeps its own key rather than
            # disappearing (or, worse, standing in for the injected one).
            row["strict_all_as_written"] = (
                f"{as_written['survived']}/{as_written['total']}")
        per_session[session_id] = row
    return {
        "graph": graph,
        "injected_anchor": inject_anchor,
        "value_units": units_total,
        "value_survived": survived_total,
        "bar": ">= 13 of 14",
        "per_session": per_session,
    }


def backend_refusal() -> str | None:
    """No searchable backend = a reading of an EMPTY graph, not of the pipeline.

    `TortoiseSDK(graph_name=...)` with no URI resolves the EMBEDDED path, and
    `resolve_db_path()` "raises nothing — falls through to the default on
    empty/whitespace env values" (`tortoise/config.py`), so a shell that forgot
    the export opens a brand-new empty graph. Every session then reads zero
    Points and this probe would print `0 of 14` — the same RED a real E2a
    regression produces, which is how a real regression gets dismissed. The
    sibling probe refuses the same way (`e1_gate_drops.py`).
    """
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        return ("REFUSED: TORTOISE_DB_URI is not set — the SDK would silently "
                "fall back to an EMPTY embedded graph, so every session would "
                "read 0 Points and '0 of 14' would measure the absence of a "
                "backend (#3892), not the pipeline")
    return None


def population_refusal(record: dict) -> str | None:
    """The run wrote no memory-level Points = an absent population (#9889).

    E2a's population analogue of `e1_gate_drops`' zero denominator. `grade`
    records `per_session[*].memory_points` from `runner.snapshot_session`'s own
    snapshot, which guards every read with `if eids:`/`if seen:` and returns an
    empty list for a graph that does not exist — so a mistyped or reaped graph
    name is indistinguishable from a pipeline that lost everything UNLESS the
    population is asserted. A reading with no census at all (`per_session`
    absent) is refused too: an unobserved population is not a measured one.
    """
    sessions = record.get("per_session")
    if not isinstance(sessions, dict) or not sessions:
        return ("REFUSED: the reading carries no per-session census — the "
                "population this number would be scored over was never "
                "observed, so it is not a measurement")
    points = sum(int(row.get("memory_points") or 0)
                 for row in sessions.values() if isinstance(row, dict))
    if points == 0:
        return ("REFUSED: the graph holds no memory-level Points for any "
                "session — '0 of 14' over an ABSENT population is a "
                "zero-denominator reading, not a result (a keyless or mistyped "
                "backend reads exactly this way; #9889 acceptance item 2)")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--inject-anchor", action="store_true",
                        help="FALSIFIER: inject one synthetic Point per lost "
                             "value unit, carrying its verbatim anchor")
    parser.add_argument("--check-denominator", action="store_true",
                        help="print §3.3's denominator off the gold and exit")
    args = parser.parse_args(argv)
    if args.check_denominator:
        total = frozen_denominator()
        print(f"frozen value-bearing denominator: {total}")
        return 0 if total == 14 else 2
    refusal = backend_refusal()
    if refusal:
        print(refusal)
        return 2
    if args.inject_anchor:
        # The falsifier is scored AGAINST the as-written run, so both readings
        # come from the same graph in one process — a lost unit that the
        # injection cannot recover means the counter is inert, not that the
        # pipeline lost the value.
        before = grade(args.graph, inject_anchor=False)
        refusal = population_refusal(before)
        if refusal:
            print(refusal)
            return 2
        after = grade(args.graph, inject_anchor=True)
        record = {"as_written": before, "with_anchor": after}
        Path(args.out).write_text(json.dumps(record, indent=1))
        lost = before["value_units"] - before["value_survived"]
        recovered = after["value_survived"] - before["value_survived"]
        print(f"E2a falsifier: as_written {before['value_survived']}/"
              f"{before['value_units']} -> injected {after['value_survived']}/"
              f"{after['value_units']} (recovered {recovered} of lost {lost})")
        if lost and after["value_survived"] <= before["value_survived"]:
            print("REFUSED: injecting the verbatim anchors moved the counter by "
                  "nothing — the survival counter is inert, so the as-written "
                  "number measures the instrument, not the pipeline")
            return 2
        return 0
    record = grade(args.graph, inject_anchor=False)
    refusal = population_refusal(record)
    if refusal:
        print(refusal)
        return 2
    Path(args.out).write_text(json.dumps(record, indent=1))
    print(f"E2a: value units {record['value_survived']}/{record['value_units']} "
          f"(injected={record['injected_anchor']}) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
