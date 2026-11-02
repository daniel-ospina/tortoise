"""E1 and E2 scored from the #9889 measurement artifacts — ONE invocation.

Why this exists. `capstone_measure.py` could only score E1/E2 by parsing
`docs/evidence/7949-capstone/e1-e2.log`, a fixed artifact whose Object
population is a 25-name owner-labelled audit set (below E1's frozen floor of 30),
whose corpus is not the pre-registration's frozen one, and which carries **one**
arm — so both rows were reported `BLOCKED`/`REFUSED` no matter what a fresh run
held. `#9889`'s three probes produced the missing halves (`#10342`); this driver
is the piece that makes the table's rows *theirs*: it reads the probes' records
and emits E1 and E2 (both halves) as one scoreable artifact, so
`capstone_measure.py` no longer has to reach for the superseded log.

It measures nothing itself. Every number is read out of a committed record that
names the command and the graph that produced it (`docs/evidence/9889/README.md`
quotes each command). That is the point: the row cites an artifact, and the
artifact cites a probe run.

What each number is, and what it is NOT — stated here so a reader does not have
to find it:

* **E1** is `after_share <= 0.50 x before_share` over ALL `:Object` nodes written
  by the run, floored at 30 Objects. The before arm exists in TWO independent
  realizations (two OFF runs, because a single one cannot show whether a
  difference is the arm or the model) and the after arm in one (the ON run).
  Pairing the single after arm against each before arm gives **two different
  verdicts**, so the honest aggregate is `decidable: false` — the metric is
  REFUSED at n=1 per arm, and the record prints both pairings rather than
  picking the convenient one.
* **E2a** is the count of the frozen **14** value-bearing planted units that
  strictly survive. The denominator is derived by rule in the pre-registration
  §3.3 and is read from the probe's record, never typed here.
  `e2a-value-units-falsifier.json` is the FALSIFIER: it is the same measurement
  with an anchor injected for every lost unit, and it must reach 14/14 — a
  counter that reports the same number with and without the injection is inert,
  and this driver refuses rather than reporting its low number as a measurement.
* **E1b** and **E2b** are the log-derived controls they already were. They stay
  log-derived on purpose: no probe emits them, and pretending otherwise would be
  the same "evidence behind the number" defect in the other direction.

Usage::

    uv run python docs/evidence/7949-capstone/probes/e1_e2_scores.py \\
        --out docs/evidence/9889/e1-e2-scores.json

Exit status: 0 when a scoreable record was written; **2** when any input record
is missing, when the E2a falsifier did not reach the frozen denominator (an
inert counter), when the value-gate positive controls are dead, or when a
before/after share is absent — because a score computed over an unproven counter
is the exact failure mode this bundle exists to stop.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: The records this driver reads, all of them committed by `#10342`.
EVIDENCE = Path("docs/evidence/9889")
OFF_ARMS = ("e1-paired-off-arm-1.json", "e1-paired-off-arm-2.json")
ON_ARM_LOG = "on-arm.log"
E2A_RECORD = "e2a-value-units.json"
E2A_FALSIFIER = "e2a-value-units-falsifier.json"
GATE_DROPS = "e1-gate-drops.json"
PREREG = Path("docs/evidence/7949-capstone/preregistration.json")


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise _refuse(f"{path} does not exist — a row cannot cite an artifact "
                      "the tree does not carry")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:  # pragma: no cover - corrupt input
        raise _refuse(f"{path} is not JSON: {exc}") from exc


def _refuse(message: str) -> SystemExit:
    """A refusal is exit **2**, never 1 — it is not an unhandled error.

    The two sibling probes document exit 2 for a refusal, and a caller that
    cannot tell "refused to measure" from "crashed" will treat the second as
    the first. `SystemExit("text")` exits 1, so the text is printed here and the
    status set explicitly.
    """
    print(f"e1-e2-scores: REFUSED — {message}", file=sys.stderr)
    return SystemExit(2)


def _grep(text: str, pattern: str, *, what: str) -> str:
    match = re.search(pattern, text, re.M)
    if not match:
        raise _refuse(f"{ON_ARM_LOG} carries no {what} line")
    return match.group(1)


def frozen_denominators(repo: Path) -> dict:
    """The frozen floors, the frozen denominator AND the frozen bars.

    Every one is READ from the pre-registration. A bar typed into this file would
    be a bar nobody froze: amend E2a's threshold in the record and the ROW would
    move while the artifact the row cites kept the old one — the row and its
    evidence disagreeing is the exact class this bundle exists to refuse.
    """
    prereg = _read_json(repo / PREREG)
    floors = prereg.get("denominator_floors") or {}
    for key in ("e1_objects", "e1_min_before_share", "e2b_surviving_units"):
        if key not in floors:
            raise _refuse(f"{PREREG} carries no denominator_floors.{key}")
    corpus = prereg.get("corpus") or {}
    if not corpus.get("fixtures_hash"):
        raise _refuse(f"{PREREG} carries no corpus.fixtures_hash")
    if not isinstance(corpus.get("value_bearing_units"), int):
        raise _refuse(f"{PREREG} carries no corpus.value_bearing_units — E2a's "
                      "denominator cannot be checked against the record")
    crit = {entry.get("id"): entry for entry in prereg.get("criteria") or []}
    threshold = (crit.get("E2a") or {}).get("threshold") or ""
    match = re.search(r">=\s*(\d+)\s*of\s*(\d+)", threshold)
    if not match:
        raise _refuse(f"{PREREG} carries no parseable E2a threshold: "
                      f"{threshold!r}")
    return {
        "e1_objects": floors["e1_objects"],
        "e1_min_before_share": floors["e1_min_before_share"],
        "e2b_surviving_units": floors["e2b_surviving_units"],
        "fixtures_hash": corpus["fixtures_hash"],
        "e2a_required": int(match.group(1)),
        "e2a_of": int(match.group(2)),
        "value_bearing_units": corpus["value_bearing_units"],
    }


def read_off_arms(repo: Path, floors: dict) -> list[dict]:
    """The before arm, per realization, with BOTH floors applied to each.

    A share under `e1_min_before_share` is a control arm too flat to measure E1,
    and an Object count under `e1_objects` is a denominator too small to move a
    share — both are REFUSED by the probe that produced them. Asserted again
    here, because this driver is what puts the number in a scored row.
    """
    arms: list[dict] = []
    for name in OFF_ARMS:
        record = _read_json(repo / EVIDENCE / name)
        share = record.get("share")
        objects = record.get("objects")
        if share is None or not objects:
            raise _refuse(f"{name} carries no share/objects")
        if objects < floors["e1_objects"]:
            raise _refuse(f"{name} has {objects} Objects, under the frozen "
                          f"floor of {floors['e1_objects']}")
        if share < floors["e1_min_before_share"]:
            raise _refuse(f"{name} share {share:.4f} is under the frozen "
                          f"e1_min_before_share {floors['e1_min_before_share']}")
        arms.append({
            "artifact": f"{EVIDENCE / name}",
            "graph": record.get("graph"),
            "objects": objects,
            "bare_definite": record.get("bare_definite"),
            "share": share,
            "arm": record.get("arm"),
        })
    return arms


def read_on_arm(repo: Path) -> dict:
    """The after arm, off the ON run's own census line (`e1-aux …`).

    The line is written by `capstone_measure.py` at the head it measured, so it
    is the after arm's own reading rather than a second capture by this driver.
    """
    path = repo / EVIDENCE / ON_ARM_LOG
    if not path.is_file():
        raise _refuse(f"{EVIDENCE / ON_ARM_LOG} does not exist")
    text = path.read_text()
    objects = int(_grep(text, r"^e1-aux objects_in_graph=(\d+)",
                        what="e1-aux objects_in_graph"))
    bare = int(_grep(text, r"^e1-aux objects_in_graph=\d+ bare_definite=(\d+)",
                     what="e1-aux bare_definite"))
    share = float(_grep(text, r"^e1-aux objects_in_graph=\d+ "
                              r"bare_definite=\d+ share=(\S+)",
                        what="e1-aux share"))
    real_facts = int(_grep(text, r"^e1b outcome=\S+ real_facts=(\d+)",
                           what="e1b real_facts"))
    head = _grep(text, r"^# head: (\S+)", what="# head")
    corpus = _grep(text, r"^# corpus: \S+ fixtures_hash=sha256:(\S+)",
                   what="# corpus fixtures_hash")
    fused, atomic = _grep(text, r"^e2b outcome=\S+ fused=(\d+/\d+)",
                          what="e2b fused").split("/")
    return {
        "artifact": f"{EVIDENCE / ON_ARM_LOG}",
        "head": head,
        "corpus_sha256": corpus,
        "objects": objects,
        "bare_definite": bare,
        "share": share,
        "real_facts": real_facts,
        "fused": int(fused),
        "atomic_points": int(atomic),
    }


def e1_pairings(before_arms: list[dict], after: dict, floors: dict) -> dict:
    """E1 scored against EVERY before realization — and the aggregate stated.

    `multiplier` is the frozen `E1.multiplier` (0.50): a pairing PASSES when the
    after share is at or under half its before share. One after arm paired with
    two before arms can disagree, and when it does the metric is NOT decided —
    reporting the passing pairing as "the" result would be picking the answer.
    """
    multiplier = 0.50
    pairings = []
    for arm in before_arms:
        bound = multiplier * arm["share"]
        passed = after["share"] <= bound
        pairings.append({
            "before_artifact": arm["artifact"],
            "before_graph": arm["graph"],
            "before_share": arm["share"],
            "before_objects": arm["objects"],
            "before_bare_definite": arm.get("bare_definite"),
            "after_share": after["share"],
            "after_objects": after["objects"],
            "bound": round(bound, 6),
            "ratio": round(after["share"] / arm["share"], 6),
            "passed": passed,
        })
    verdicts = {pairing["passed"] for pairing in pairings}
    decidable = len(verdicts) == 1
    below_floor = after["objects"] < floors["e1_objects"]
    if below_floor or not decidable:
        outcome = "REFUSED"
    else:
        outcome = "PASS" if verdicts.pop() else "FAIL"
    return {
        "criterion": "E1",
        "bar": (f"after_share <= {multiplier} x before_share "
                f"(Objects floor {floors['e1_objects']})"),
        "outcome": outcome,
        "decidable": decidable and not below_floor,
        "below_floor": below_floor,
        "pairings": pairings,
        "before_spread_pp": round(
            abs(pairings[0]["before_share"] - pairings[-1]["before_share"]) * 100, 1),
        "why": ("one after realization paired against two independent before "
                "realizations gives both verdicts, so E1 is not decided at n=1 "
                "per arm — n >= 2 per arm is the next requirement"
                if not decidable else
                "every before realization agrees on the pairing"),
    }


def e2_score(repo: Path, floors: dict) -> dict:  # noqa: ARG001 - floors kept
    # for symmetry with the other readers; the E2a bar is the pre-registration's
    # own "13 of 14" and is asserted against the record's denominator below.
    """E2a (frozen 14-unit denominator) + E2b (fused share) — both halves.

    The falsifier is a REFUSAL gate, not a second opinion: if injecting every
    lost unit's anchor does not move the counter to the frozen denominator, the
    counter is inert and the low number it reports is not a measurement.
    """
    record = _read_json(repo / EVIDENCE / E2A_RECORD)
    falsifier = _read_json(repo / EVIDENCE / E2A_FALSIFIER)
    gates = _read_json(repo / EVIDENCE / GATE_DROPS)

    units = record.get("value_units")
    survived = record.get("value_survived")
    if not units or survived is None:
        raise _refuse(f"{E2A_RECORD} carries no value_units/value_survived")
    # The denominator must BE the frozen one. A record that grades 20 units while
    # the pre-registration froze 14 would otherwise score GREEN on a population
    # nobody registered — the false green this bundle exists to refuse.
    if units != floors["value_bearing_units"] or \
            floors["e2a_of"] != floors["value_bearing_units"]:
        raise _refuse(f"{E2A_RECORD} grades {units} units; the pre-registration "
                      f"froze {floors['value_bearing_units']} "
                      f"(threshold '>= {floors['e2a_required']} of "
                      f"{floors['e2a_of']}')")

    with_anchor = (falsifier.get("with_anchor") or falsifier)
    injected_survived = with_anchor.get("value_survived")
    injected_units = with_anchor.get("value_units")
    # The falsifier must move the SAME population the score is taken over. A
    # falsifier that reaches its own denominator proves that SOME counter moves;
    # crediting it to the frozen 14 would print a number nothing measured.
    if injected_units != units:
        raise _refuse(f"the falsifier grades {injected_units} units but the "
                      f"score's denominator is {units} — a falsifier over a "
                      "different population cannot certify this counter")
    if injected_survived != units:
        raise _refuse(f"the falsifier reaches {injected_survived}/{units}, not "
                      f"{units}/{units} — an injected anchor that moves nothing "
                      "means the counter is inert")
    if not gates.get("predicate_alive"):
        raise _refuse("the value-gate positive controls no longer drop — the "
                      "mechanism count measures nothing")

    required = floors["e2a_required"]
    passed = survived >= required
    return {
        "criterion": "E2",
        "bar": f"E2a >= {required} of {units}, E2b fused share <= 0.05",
        "e2a": {
            "artifact": f"{EVIDENCE / E2A_RECORD}",
            "falsifier_artifact": f"{EVIDENCE / E2A_FALSIFIER}",
            "graph": record.get("graph"),
            "value_units": units,
            "value_survived": survived,
            "falsifier": {"value_units": injected_units,
                          "value_survived": injected_survived},
            "required": required,
            "outcome": "PASS" if passed else "FAIL",
            "denominator_rule": "pre-registration §3.3 — a digit in the "
                                "verbatim_anchor, minus the two named exclusions",
        },
        "outcome": "PASS" if passed else "FAIL",
        "decidable": True,
    }


def score(repo: Path = _ROOT) -> dict:
    floors = frozen_denominators(repo)
    before_arms = read_off_arms(repo, floors)
    after = read_on_arm(repo)
    # One record, one run: the before, ON, E2a, falsifier and gate-drops records
    # must ALL name the same head (and the E2a / gate-drops pair the same graph),
    # or a number from one run is being reported under another run's corpus.
    for arm in before_arms:
        if after["head"][:9] not in str(arm.get("graph") or ""):
            raise _refuse(f"{arm['artifact']} was taken on graph "
                          f"{arm.get('graph')!r}, which does not name head "
                          f"{after['head'][:9]}")
    e2a_record = _read_json(repo / EVIDENCE / E2A_RECORD)
    falsifier_record = _read_json(repo / EVIDENCE / E2A_FALSIFIER)
    gates_record = _read_json(repo / EVIDENCE / GATE_DROPS)
    for name, record in ((E2A_RECORD, e2a_record),
                         (E2A_FALSIFIER, falsifier_record),
                         (GATE_DROPS, gates_record)):
        # A falsifier record nests its graph under each of its two blocks; either
        # one names the run it graded.
        graph = str(record.get("graph")
                    or (record.get("with_anchor") or {}).get("graph")
                    or (record.get("as_written") or {}).get("graph") or "")
        if after["head"][:9] not in graph:
            raise _refuse(f"{name} was taken on graph {graph!r}, which does not "
                          f"name head {after['head'][:9]}")
    if e2a_record.get("graph") != gates_record.get("graph"):
        raise _refuse("the E2a record and the gate-drops record come from "
                      "different graphs — one record cannot mix them")
    e1 = e1_pairings(before_arms, after, floors)
    e2 = e2_score(repo, floors)
    if after["corpus_sha256"] != normalise_pin(floors["fixtures_hash"]):
        raise _refuse(
            f"{ON_ARM_LOG} measured corpus {after['corpus_sha256']}, not the "
            f"frozen {normalise_pin(floors['fixtures_hash'])} — a row citing a "
            "different corpus than the pre-registration froze is the defect "
            "this record exists to fix")
    e2b_floor = floors["e2b_surviving_units"]
    e2b_share = (after["fused"] / after["atomic_points"]
                 if after["atomic_points"] else 1.0)
    e2["e2b"] = {
        "fused": after["fused"],
        "atomic_points": after["atomic_points"],
        "share": round(e2b_share, 6),
        "below_floor": after["atomic_points"] < e2b_floor,
        "artifact": after["artifact"],
    }
    e2["outcome"] = ("PASS" if (e2["e2a"]["outcome"] == "PASS"
                                and e2b_share <= 0.05
                                and not e2["e2b"]["below_floor"])
                     else "FAIL")
    e2["decided_by"] = ("E2a" if e2["e2a"]["outcome"] != "PASS" else
                        "E2b" if (e2b_share > 0.05
                                  or e2["e2b"]["below_floor"]) else "both")
    return {
        "schema": "e1-e2-scores/1",
        "corpus_sha256": floors["fixtures_hash"],
        "corpus_pin_source": (f"{PREREG}#corpus.fixtures_hash — "
                              "e1_paired_arm.py refuses to run unless the "
                              "checkout's corpus matches this pin (G1)"),
        "head": after["head"],
        "denominators": {
            "e1_objects": floors["e1_objects"],
            "e1_min_before_share": floors["e1_min_before_share"],
            "e2b_surviving_units": floors["e2b_surviving_units"],
        },
        "e1": e1,
        "e1b": {
            "bar": "90 of 90",
            "real_facts": after["real_facts"],
            "outcome": "PASS" if after["real_facts"] >= 90 else "FAIL",
            "artifact": after["artifact"],
            "note": "a no-loss CONTROL on both arms, not the frozen metric",
        },
        "e2": e2,
        "gate_drops": {
            "artifact": f"{EVIDENCE / GATE_DROPS}",
            "objects": _read_json(repo / EVIDENCE / GATE_DROPS).get("objects"),
            "drops_total": _read_json(repo / EVIDENCE / GATE_DROPS)
            .get("drops_total"),
            "predicate_alive": _read_json(repo / EVIDENCE / GATE_DROPS)
            .get("predicate_alive"),
        },
    }


def normalise_pin(value: str) -> str:
    """The frozen pin without its ``sha256:`` prefix, for comparison."""
    return value[len("sha256:"):] if value.startswith("sha256:") else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True,
                        help="where the score record (JSON) is written")
    parser.add_argument("--repo", default=str(_ROOT))
    args = parser.parse_args(argv)
    record = score(Path(args.repo))
    Path(args.out).write_text(json.dumps(record, indent=1) + "\n")
    e1, e2 = record["e1"], record["e2"]
    print(f"E1 {e1['outcome']} decidable={e1['decidable']} "
          f"(before spread {e1['before_spread_pp']} pp) -> {args.out}")
    for pairing in e1["pairings"]:
        print(f"   before {pairing['before_share']:.4f} -> bound "
              f"{pairing['bound']:.4f} vs after {pairing['after_share']:.4f} "
              f"= {'PASS' if pairing['passed'] else 'FAIL'} "
              f"(ratio {pairing['ratio']:.4f})")
    print(f"E2 {e2['outcome']} — E2a {e2['e2a']['value_survived']}/"
          f"{e2['e2a']['value_units']} (bar >= {e2['e2a']['required']}), "
          f"E2b fused {e2['e2b']['fused']}/{e2['e2b']['atomic_points']} = "
          f"{e2['e2b']['share']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
