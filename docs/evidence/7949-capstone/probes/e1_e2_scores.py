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

* **E1** is `after_share <= multiplier x before_share` over ALL `:Object` nodes
  written by the run, floored at 30 Objects. BOTH numbers come out of the
  pre-registration by `capstone_measure.parse_bars` — the multiplier (0.50 today)
  from `criteria[E1].threshold`, the two floors from `denominator_floors` — so
  this record cannot score against a bar the record did not freeze. The before
  arm exists in TWO independent realizations (two OFF runs, because a single one
  cannot show whether a difference is the arm or the model) and the after arm in
  one (the ON run).
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
is missing, when a threshold cannot be parsed out of the frozen record, when the
E2a record grades a denominator other than the frozen **14** (a bar printed over
an unfrozen population), when the E2a falsifier did not reach the frozen
denominator (an inert counter), when the ON arm and the E2a counts come from
different graphs (two populations in one record), when the falsifier's SCORED
`with_anchor` block is not the very run the E2a record was taken on — the same
head is not the same run, because a graph is `capstone_on_<head>_<stamp>`, and
naming the head is not enough — when a `with_anchor` block is PRESENT but EMPTY
(a record that never wrote its counter is not the flat shape), when its
`as_written` block carries no graph (an
absent provenance is not an inherited one), sits on another run, or reads counts
that are not the E2a record's (that block IS the E2a reading), when the
value-gate positive controls are dead, when a before arm's `arm` block is
absent, PRESENT but EMPTY, or missing one of its four switches (a key that was
never written is not a key that was written as unset), when the TWO before
artifacts name the SAME graph (two copies of one run are one realization, not
two — and `decidable` is the SET of pairings, so two copies of one reading can
only ever agree and would report a DECIDED E1 off the replication the design
requires and never got), when a before artifact carries NO graph at all (an
absent run cannot be told apart from a duplicated one), when the after arm's own
`# arm:` readback does not show the two measured passes ON or does not show the
two switches that must stay OFF, or when a before/after share is absent —
because a score computed over an unproven counter is the exact failure mode this
bundle exists to stop.
"""
from __future__ import annotations

import argparse
import importlib.util
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
# The switches `e1_paired_arm.py` forces OFF for a before arm and refuses to run over
# when they are already set (#9889 acceptance item 4). It is FOUR keys, not the three
# the acceptance list names: `TORTOISE_S2_DECISION_SEAM` arms S2.2 *through the
# arbiter*, so `_vet_enabled` reads True even against an explicit `TORTOISE_VET=0`
# (`e1_paired_arm.SEAM_KEY`'s own comment, and #5087). The driver re-reads all four
# from each artifact's own `arm` block, because it is the driver that puts the number
# in a scored row.
_ARM_VARS = ("TORTOISE_VET", "TORTOISE_CLASSIFY_LATER", "TORTOISE_VALUE_GATE",
             "TORTOISE_S2_DECISION_SEAM")
_ARM_OFF_STATES = ("none", "null", "", "0", "false", "unset", "<unset>")
# The measured arm is the mirror image: it must have been ON. `capstone_measure`
# writes that readback onto the log this driver already parses (`# arm: …`), so
# nothing about the after side needs to be assumed either.
_ARM_ON_VARS = ("TORTOISE_VET", "TORTOISE_CLASSIFY_LATER")
# …and the same arm PINS two switches OFF: `capstone_measure.ARM_OFF`, whose own
# comment says the value gate "also switches S2.2 on … an ambient value would
# silently change the arm between two runs of the same probe" and the seam "would
# arm a network call and a spend inside an arm this file declares OFF". Checking
# only the ON half accepts a log measured with `TORTOISE_VALUE_GATE=1` — the
# value-gate arm `E1-E2.md` measures separately — as the ON half of E1's pair.
# Kept as a literal rather than an import because this module is loaded before
# `capstone_measure`; `test_the_arm_key_sets_are_the_producers` holds the two
# definitions equal, so they cannot drift apart silently.
_ARM_OFF_VARS = ("TORTOISE_VALUE_GATE", "TORTOISE_S2_DECISION_SEAM")


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


#: The bundle's ONE bar reader, loaded from the sibling probe by path.
#:
#: E1's multiplier, E1b's 90, E2a's `13 of 14` and E2b's 0.05 are read out of
#: `preregistration.json` by `capstone_measure.parse_bars`, whose docstring states
#: the rule this driver obeys: *"a bar typed into this file is a bar nobody froze,
#: so a lane could move a threshold by editing the probe"*. This driver emits the
#: artifact BOTH capstone rows cite, so a second reading of the same bars would be
#: a second place a threshold can move — and two readers of one frozen number can
#: only disagree; they can never both be right. An amendment ("a NEW linked record,
#: never an edit to the frozen one") then moves this record with the probe that
#: scores it. Loaded by FILE PATH because `probes/` is not a package and this module
#: is executed as a script.
_BARS_READER: dict = {}


def _parse_bars(prereg: dict) -> dict:
    if not _BARS_READER:
        path = Path(__file__).with_name("capstone_measure.py")
        spec = importlib.util.spec_from_file_location(
            "_capstone_measure_bars", path)
        if spec is None or spec.loader is None:  # pragma: no cover - absent file
            raise _refuse(f"cannot load the bar reader at {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _BARS_READER["module"] = module
    # An unparseable threshold is a `ValueError` in the reader (G0's refusal
    # contract); here it is this driver's refusal — exit 2, not a traceback.
    try:
        return _BARS_READER["module"].parse_bars(prereg)
    except ValueError as exc:
        raise _refuse(f"{PREREG}'s criteria cannot be parsed: {exc}") from exc


def frozen_denominators(repo: Path) -> dict:
    """The frozen floors, the frozen denominator AND the frozen bars.

    Every one is READ from the pre-registration. A bar typed into this file would
    be a bar nobody froze: amend E2a's threshold in the record and the ROW would
    move while the artifact the row cites kept the old one — the row and its
    evidence disagreeing is the exact class this bundle exists to refuse. The bars
    come from `capstone_measure.parse_bars`, the bundle's ONE reader, so this
    driver and the probe that scores the row cannot read two thresholds.
    """
    prereg = _read_json(repo / PREREG)
    floors = prereg.get("denominator_floors") or {}
    for key in ("e1_objects", "e1_min_before_share", "e2b_surviving_units"):
        if key not in floors:
            raise _refuse(f"{PREREG} carries no denominator_floors.{key}")
    corpus = prereg.get("corpus") or {}
    if not corpus.get("fixtures_hash"):
        raise _refuse(f"{PREREG} carries no corpus.fixtures_hash")
    value_bearing = corpus.get("value_bearing_units")
    if not isinstance(value_bearing, int) or isinstance(value_bearing, bool):
        raise _refuse(f"{PREREG} carries no integer corpus.value_bearing_units "
                      "— E2a's denominator cannot be checked against the record")
    bars = _parse_bars(prereg)
    # The record freezes E2a's denominator in TWO places — `corpus.value_bearing_units`
    # (the population §3.3 derives) and `criteria[E2a]`'s `of` (the bar's denominator).
    # A run that printed one while scoring the other would be reporting a bar nothing
    # froze, so their disagreement is a refusal here rather than a number later.
    if bars["E2a"]["of"] != value_bearing:
        raise _refuse(f"{PREREG}'s criteria[E2a] bar is 'of "
                      f"{bars['E2a']['of']}' but corpus.value_bearing_units is "
                      f"{value_bearing} — two frozen statements about E2a's "
                      "denominator disagree, so no bar can be printed over it")
    return {
        "e1_objects": floors["e1_objects"],
        "e1_min_before_share": floors["e1_min_before_share"],
        "e2b_surviving_units": floors["e2b_surviving_units"],
        "fixtures_hash": corpus["fixtures_hash"],
        "e2a_units": value_bearing,
        "bars": bars,
    }


def read_off_arms(repo: Path, floors: dict) -> list[dict]:
    """The before arm, per realization, with BOTH floors applied to each.

    A share under `e1_min_before_share` is a control arm too flat to measure E1,
    and an Object count under `e1_objects` is a denominator too small to move a
    share — both are REFUSED by the probe that produced them. Asserted again
    here, because this driver is what puts the number in a scored row.

    The THIRD refusal the probe makes is asserted here too, and it is the one
    that decides whether the row means anything at all: the arm must have been
    OFF. `e1_paired_arm.py` refuses to run when any of the FOUR variables is
    already set (#9889 acceptance item 4), and it writes the env it read back
    into `arm` — so the record CARRIES the proof. Collecting that field without
    reading it (which this function did) let two ON realizations be scored as
    "before" against an ON "after", producing a DECIDED `FAIL` from a comparison
    that measured nothing: the inert counter #9889's own O/I/T forbids. A
    missing `arm` is refused rather than inherited, on the same rule the
    falsifier's `as_written` block is refused on — an absent provenance is not a
    default one. An EMPTY block (or one missing a switch) is refused on the
    sibling half of that rule: `arm.get(var)` on a key that was never written
    returns `None`, which stringifies to `"none"` — a token of the OFF
    vocabulary — so a block that wrote nothing would certify the arm it is
    supposed to prove.

    The FOURTH refusal is the one that decides whether the before arm is a
    *replication*: the two artifacts must not name the same graph. `#9889`'s
    design needs two INDEPENDENT realizations, and the aggregate only refuses
    (`decidable` is the SET of pairings) when they can disagree — two copies of
    one OFF run pair identically, so `verdicts` is a singleton, `decidable` is
    True, and the row prints a DECIDED E1/PASS/FAIL off the replication that
    never happened. That is pseudoreplication (Hurlbert 1984: replicates must be
    statistically independent), and it is reachable without misusing any record:
    `e1_paired_arm.py` takes the graph from `--graph` and mints nothing, so
    re-running the OFF arm onto the same graph yields two artifacts that satisfy
    every other check here. A before artifact with NO graph is refused on the
    same rule rather than left to the head-substring check in `score()`, which
    reports a missing run as a head MISMATCH and cannot see a duplicate at all.
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
        arm = record.get("arm")
        if not isinstance(arm, dict):
            raise _refuse(f"{name} carries no `arm` block, so nothing in it shows "
                          "the passes were OFF — a before arm's provenance cannot "
                          "be assumed")
        for var in _ARM_VARS:
            # PRESENCE first, then value. `arm.get(var)` on a block that never
            # wrote the key returns `None`, which stringifies to "none" — a token
            # of the OFF vocabulary — so an EMPTY (or partially-keyed) block would
            # certify an arm nothing in it establishes. That is the same
            # fail-open the falsifier's `with_anchor` block is refused on
            # ("an empty one is not the flat shape"), applied to the field that
            # says which population a share came from: a key that was never
            # written is not a key that was written as unset.
            if var not in arm:
                raise _refuse(
                    f"{name}'s `arm` block carries no {var}, so it does not show "
                    "the passes were OFF — a missing key is not an unset one, "
                    "and a before arm's provenance cannot be assumed")
            state = arm.get(var)
            if str(state).strip().lower() not in _ARM_OFF_STATES:
                raise _refuse(
                    f"{name} was measured with {var}={state!r}, so it is not a "
                    "before arm — scoring it as one compares a treatment against "
                    "itself and still reports a decided E1")
        graph = str(record.get("graph") or "").strip()
        if not graph:
            raise _refuse(
                f"{name} carries no graph, so the run it was taken on cannot be "
                "told apart from a COPY of the other before artifact's run — an "
                "absent run is not a distinct realization, and two copies of one "
                "realization can only agree (see `read_off_arms`'s docstring)")
        arms.append({
            "artifact": f"{EVIDENCE / name}",
            "graph": graph,
            "objects": objects,
            "bare_definite": record.get("bare_definite"),
            "share": share,
            "arm": arm,
        })
    # …and the two realizations must BE two runs. Checked over the arms as a map so
    # a third artifact cannot slip a duplicate past a hand-written pairwise test.
    seen: dict[str, str] = {}
    for arm in arms:
        first = seen.setdefault(arm["graph"], arm["artifact"])
        if first != arm["artifact"]:
            raise _refuse(
                f"{arm['artifact']} and {first} were both taken on graph "
                f"{arm['graph']!r} — two copies of ONE run are one realization, "
                "not two, and `decidable` is the SET of pairings: identical "
                "copies can only agree, so the row would report a DECIDED E1 "
                "off the replication the design requires and never got")
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
    graph = _grep(text, r"^# graph: (\S+)", what="# graph")
    corpus = _grep(text, r"^# corpus: \S+ fixtures_hash=sha256:(\S+)",
                   what="# corpus fixtures_hash")
    fused, atomic = _grep(text, r"^e2b outcome=\S+ fused=(\d+/\d+)",
                          what="e2b fused").split("/")
    # The after arm's OWN readback, off the `# arm:` line `capstone_measure` writes
    # from `os.environ` after forcing the passes. E1's bar compares an ON run
    # against an OFF one, so an after arm whose treatment was never enabled is not
    # a "before/after pair" at all — it is two control arms, and the bar would be
    # scored over a treatment the run never applied. Read, not assumed: the log
    # carries the proof, so a driver that skips it is choosing not to look.
    after_arm_line = _grep(text, r"^# arm: (.+?)(?:\s+\(|$)",
                           what="# arm")
    after_arm = {}
    for token in after_arm_line.split():
        if "=" in token:
            key, _, value = token.partition("=")
            after_arm[key] = value
    for var in _ARM_ON_VARS:
        if str(after_arm.get(var)).strip().lower() in _ARM_OFF_STATES:
            raise _refuse(
                f"{EVIDENCE / ON_ARM_LOG}'s arm readback has {var}="
                f"{after_arm.get(var)!r}, so the measured passes were not ON — "
                "an after arm that never enabled the treatment cannot be the "
                "ON half of a before/after pair")
    # …and the other direction. The measured arm is two keys ON and TWO OFF
    # (`capstone_measure.ARM_OFF`), so a log measured with the value gate armed is
    # a DIFFERENT arm — the one `E1-E2.md` reports separately — and crediting its
    # after-share to E1 scores a bar over a treatment this driver's arm never
    # declared. Presence is required here for the same reason as on the before
    # side: an absent key stringifies to "none" and would read as consent.
    for var in _ARM_OFF_VARS:
        if var not in after_arm:
            raise _refuse(
                f"{EVIDENCE / ON_ARM_LOG}'s arm readback carries no {var}, so it "
                "does not show the switch was pinned OFF — a missing key is not "
                "an unset one, and the measured arm is only the ON half of a "
                "before/after pair while its OFF switches are declared")
        if str(after_arm.get(var)).strip().lower() not in _ARM_OFF_STATES:
            raise _refuse(
                f"{EVIDENCE / ON_ARM_LOG}'s arm readback has {var}="
                f"{after_arm.get(var)!r}, but the measured arm pins it OFF — "
                "that is a different arm (the value-gate/seam pass), and "
                "crediting its after share to E1 would score a treatment this "
                "pair never declared")
    return {
        "artifact": f"{EVIDENCE / ON_ARM_LOG}",
        "head": head,
        "graph": graph,
        "arm": after_arm,
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

    `multiplier` is the frozen `E1.multiplier` (0.50 today, PARSED out of
    `criteria[E1].threshold` by ``parse_bars`` — never typed): a pairing PASSES
    when the after share is at or under that fraction of its before share. One
    after arm paired with two before arms can disagree, and when it does the
    metric is NOT decided — reporting the passing pairing as "the" result would be
    picking the answer.
    """
    multiplier = floors["bars"]["E1"]["multiplier"]
    pairings = []
    for arm in before_arms:
        bound = multiplier * arm["share"]
        passed = after["share"] <= bound
        pairings.append({
            "before_artifact": arm["artifact"],
            "before_graph": arm["graph"],
            "before_arm": arm.get("arm"),
            "before_share": arm["share"],
            "before_objects": arm["objects"],
            "before_bare_definite": arm.get("bare_definite"),
            "after_share": after["share"],
            "after_objects": after["objects"],
            "after_bare_definite": after["bare_definite"],
            "bound": round(bound, 6),
            "ratio": round(after["share"] / arm["share"], 6),
            "passed": passed,
        })
    verdicts = {pairing["passed"] for pairing in pairings}
    decidable = len(verdicts) == 1
    below_floor = after["objects"] < floors["e1_objects"]
    outcome = ("REFUSED" if below_floor or not decidable
               else "PASS" if verdicts.pop() else "FAIL")
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
        # `why` narrates the state the record is IN. The floor is checked FIRST
        # because it is a different finding: an after arm under the frozen floor
        # was never scoreable at any n, so saying "the pairings disagree" about it
        # reports a disagreement that did not happen and hides the real cause
        # (the pre-registration's own vocabulary: below_floor is "REFUSED, which
        # is neither a pass nor a fail").
        "why": (f"the after arm has {after['objects']} Objects, under the frozen "
                f"floor of {floors['e1_objects']} — a population that small "
                "cannot decide `after <= multiplier x before`, so the bar is "
                "REFUSED and the pairings are not read as a verdict"
                if below_floor else
                "one after realization paired against two independent before "
                "realizations gives both verdicts, so E1 is not decided at n=1 "
                "per arm — n >= 2 per arm is the next requirement"
                if not decidable else
                "every before realization agrees on the pairing"),
    }


def e2_score(repo: Path, floors: dict) -> dict:
    """E2a (frozen 14-unit denominator) + E2b (fused share) — both halves.

    The falsifier is a REFUSAL gate, not a second opinion: if injecting every
    lost unit's anchor does not move the counter to the frozen denominator, the
    counter is inert and the low number it reports is not a measurement.

    ⛔ The DENOMINATOR is pinned, not merely read: `value_units` comes off the
    record, and the record is refused unless it grades exactly the population the
    pre-registration froze (`corpus.value_bearing_units`, §3.3). The falsifier's
    agreement (below) cannot stand in for that — its two sides both come from the
    record's own population, so they move TOGETHER when the denominator drifts. A
    record grading N ≠ 14 would otherwise be scored `13/N` against a printed bar
    reading `13 of 14`, and (see the capstone row) would carry that N as its own
    denominator.
    """
    record = _read_json(repo / EVIDENCE / E2A_RECORD)
    falsifier = _read_json(repo / EVIDENCE / E2A_FALSIFIER)
    gates = _read_json(repo / EVIDENCE / GATE_DROPS)

    units = record.get("value_units")
    survived = record.get("value_survived")
    if not units or survived is None:
        raise _refuse(f"{E2A_RECORD} carries no value_units/value_survived")
    frozen_units = floors["e2a_units"]
    # The denominator must BE the frozen one. A record that grades 20 units while
    # the pre-registration froze 14 would otherwise score GREEN on a population
    # nobody registered — the false green this bundle exists to refuse.
    if units != frozen_units:
        raise _refuse(f"{E2A_RECORD} grades {units} value-bearing units, but the "
                      f"pre-registration froze {frozen_units} (threshold "
                      f"'>= {floors['bars']['E2a']['required']} of "
                      f"{floors['bars']['E2a']['of']}') — a bar printed over a "
                      "different denominator is a number nothing froze")

    # …and the as-written block IS this record's reading (the bundle's README
    # reads it as `as_written 8/14 -> injected 14/14`), so its COUNTS must equal
    # the E2a record's. Checked here, AFTER the frozen-denominator refusal, so a
    # denominator defect in that block still gets the message that names the
    # pre-registered number — the more specific finding.
    falsifier_as_written = falsifier.get("as_written") or {}
    if "as_written" in falsifier and (
            falsifier_as_written.get("value_units"),
            falsifier_as_written.get("value_survived")) != (units, survived):
        raise _refuse(f"{E2A_FALSIFIER}'s as-written block reads "
                      f"{falsifier_as_written.get('value_survived')} of "
                      f"{falsifier_as_written.get('value_units')} but "
                      f"{E2A_RECORD} reads {survived} of {units} — the "
                      "as-written block IS the E2a reading, so the two cannot "
                      "disagree")

    # "Absent" and "present but empty" are different records: the fallback below
    # is for a flat record that declares no block at all. An EMPTY block is a
    # record that did not write its counter, and inheriting the top-level numbers
    # there would attribute the injected 14/14 to a provenance it does not carry.
    if "with_anchor" in falsifier and not (falsifier.get("with_anchor") or {}):
        raise _refuse(f"{E2A_FALSIFIER}'s with-anchor block is present but "
                      "empty — the injected reading is the block this score is "
                      "taken over, so an empty one is not the flat shape "
                      "(e2_score)")
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

    required = floors["bars"]["E2a"]["required"]
    passed = survived >= required
    return {
        "criterion": "E2",
        "bar": (f"E2a >= {required} of {frozen_units}, E2b fused share <= "
                f"{floors['bars']['E2b']['max_fused_share']}"),
        "e2a": {
            "artifact": f"{EVIDENCE / E2A_RECORD}",
            "falsifier_artifact": f"{EVIDENCE / E2A_FALSIFIER}",
            "graph": record.get("graph"),
            "value_units": units,
            "frozen_units": frozen_units,
            "value_survived": survived,
            "falsifier": {"value_units": injected_units,
                          "value_survived": injected_survived,
                          "graph": with_anchor.get("graph")},
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
    bars = floors["bars"]
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
    # The falsifier's TOP LEVEL is checked here like every other record (it is the
    # field a reader sees first), and BELOW its scored block is checked against
    # the E2a record's own run — removing it from this loop in favour of the block
    # check alone would let a record assert two runs with its headline unchecked.
    for name, record in ((E2A_RECORD, e2a_record),
                         (E2A_FALSIFIER, falsifier_record),
                         (GATE_DROPS, gates_record)):
        # The falsifier may declare its run at the top level or on either of its
        # two blocks; whichever it declares is checked, and a record that
        # declares TWO runs is caught by the headline (the field a reader sees
        # first) or, if the blocks disagree, by the scored-block arms below.
        graph = str(record.get("graph")
                    or ((record.get("with_anchor") or {}).get("graph")
                        if name == E2A_FALSIFIER else "")
                    or ((record.get("as_written") or {}).get("graph")
                        if name == E2A_FALSIFIER else "") or "")
        if after["head"][:9] not in graph:
            raise _refuse(f"{name} was taken on graph {graph!r}, which does not "
                          f"name head {after['head'][:9]}")
    # The AFTER arm's own `# graph:` line, not its head: an ON share from graph A
    # recorded beside E2a/gate-drops counts from graph B at the same head passes
    # every head check above (the head IS equal) while mixing two populations —
    # the exact failure the invariant is stated for, and the half that was only
    # asserted in the comment because the graph line was never parsed.
    if after["graph"] != e2a_record.get("graph"):
        raise _refuse(f"{ON_ARM_LOG} was written on graph {after['graph']!r} but "
                      f"{E2A_RECORD} counts on {e2a_record.get('graph')!r} — the "
                      "after share and the E2a counts come from different runs "
                      "of the corpus, and the head they share cannot tell them "
                      "apart")
    # The falsifier's number is read from `with_anchor` (falling back to the
    # record itself when that block is absent, exactly as `e2_score` reads it),
    # so that is the block whose graph must be checked — a record whose other
    # block names the head proves nothing about the run it was scored on.
    if "with_anchor" in falsifier_record and not (
            falsifier_record.get("with_anchor") or {}):
        raise _refuse(f"{E2A_FALSIFIER}'s with-anchor block is present but "
                      "empty — the injected reading is the block this score is "
                      "taken over, so an empty one is not the flat shape "
                      "(score)")
    falsifier_scored = falsifier_record.get("with_anchor") or falsifier_record
    falsifier_graph = str(falsifier_scored.get("graph") or "")
    if after["head"][:9] not in falsifier_graph:
        raise _refuse(f"{E2A_FALSIFIER}'s scored block was taken on graph "
                      f"{falsifier_graph!r}, which does not name head "
                      f"{after['head'][:9]}")
    # …and naming the head is not enough. A graph name is
    # `capstone_on_<head>_<stamp>`, so TWO runs of the same head are two
    # different graphs: a scored block from the sibling run would certify the
    # E2a counter from a corpus invocation the E2a record was not taken on. The
    # scored block must therefore be the E2a record's OWN graph.
    if falsifier_graph != str(e2a_record.get("graph") or ""):
        raise _refuse(f"{E2A_FALSIFIER}'s scored block is on graph "
                      f"{falsifier_graph!r}, but {E2A_RECORD} was taken on "
                      f"{e2a_record.get('graph')!r} — the same head is not the "
                      "same run, and the falsifier must prove the counter on "
                      "the run it is credited to")
    # …and the HEADLINE too (the field a reader sees first). The head loop above
    # can only refuse a graph that does not NAME the head, so a top level
    # declaring a SIBLING run of the same head would otherwise go unchecked
    # while both blocks were required to be the E2a run.
    headline = str(falsifier_record.get("graph") or "")
    if headline and headline != str(e2a_record.get("graph") or ""):
        raise _refuse(f"{E2A_FALSIFIER}'s top-level graph is {headline!r}, but "
                      f"{E2A_RECORD} was taken on "
                      f"{e2a_record.get('graph')!r} — the record would assert "
                      "two runs, and the same head is not the same run")
    # `as_written` is the reading the E2a record was taken on: same measurement,
    # so the same graph. An ABSENT graph there is not a licence to inherit the
    # scored block's — that would assume the provenance this check exists to
    # prove, and report it as proven.
    as_written_block = falsifier_record.get("as_written")
    as_written_present = "as_written" in falsifier_record
    as_written_graph = str((as_written_block or {}).get("graph") or "")
    if as_written_present and not as_written_graph:
        raise _refuse(f"{E2A_FALSIFIER}'s as-written block carries no graph, so "
                      f"it cannot be shown to be the run {E2A_RECORD} was "
                      "taken on — they are the same measurement")
    if as_written_graph and as_written_graph != str(
            e2a_record.get("graph") or ""):
        raise _refuse(f"{E2A_FALSIFIER}'s as-written block is on graph "
                      f"{as_written_graph!r} but {E2A_RECORD} is on "
                      f"{e2a_record.get('graph')!r} — they are the same "
                      "measurement and cannot come from different runs")
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
        "floor": e2b_floor,
        "artifact": after["artifact"],
    }
    # A population under the frozen floor was never scoreable, so it is REFUSED —
    # not FAIL. `capstone_measure.e2_outcome`, the row builder that consumes this
    # record, returns REFUSED → BLOCKED for the identical operands; recording FAIL
    # here made the artifact say "a scored failure" about the same numbers the row
    # beside it calls unmeasured — the row-and-its-evidence-disagree class this
    # bundle exists to refuse.
    if e2["e2b"]["below_floor"]:
        e2["outcome"] = "REFUSED"
        e2["decidable"] = False
        e2["decided_by"] = "E2b floor"
        e2["why"] = (f"the E2b population is {after['atomic_points']} atomic "
                      f"Points, under the frozen floor of {e2b_floor}, so its "
                      "fused share is REFUSED rather than scored")
    else:
        e2["outcome"] = ("PASS" if (e2["e2a"]["outcome"] == "PASS"
                                    and e2b_share <= bars["E2b"]["max_fused_share"])
                         else "FAIL")
        e2["decided_by"] = ("E2a" if e2["e2a"]["outcome"] != "PASS" else
                            "E2b" if (e2b_share > bars["E2b"]["max_fused_share"])
                            else "both")
    return {
        "schema": "e1-e2-scores/1",
        "corpus_sha256": floors["fixtures_hash"],
        "corpus_pin_source": (f"{PREREG}#corpus.fixtures_hash — "
                              "e1_paired_arm.py refuses to run unless the "
                              "checkout's corpus matches this pin (G1)"),
        "head": after["head"],
        "graph": after["graph"],
        # Both halves of the pair carry their arm readback, so the provenance of
        # the comparison is in the evidence rather than only in the driver that
        # checked it.
        "after_arm": after["arm"],
        "bars_source": (
            f"{PREREG}#criteria + #denominator_floors, parsed by "
            "capstone_measure.parse_bars — the same reader the scoring probe "
            "uses, so this record and that row cannot read two bars"),
        "denominators": {
            "e1_objects": floors["e1_objects"],
            "e1_min_before_share": floors["e1_min_before_share"],
            "e2a_units": floors["e2a_units"],
            "e2b_surviving_units": floors["e2b_surviving_units"],
        },
        "e1": e1,
        "e1b": {
            "bar": f"{bars['E1b']['required']} of {bars['E1b']['of']}",
            "real_facts": after["real_facts"],
            "outcome": ("PASS" if after["real_facts"] >= bars["E1b"]["required"]
                        else "FAIL"),
            "artifact": after["artifact"],
            "note": ("a no-loss CONTROL on the scored arm, not the frozen "
                     "metric — `real_facts` is read from the ON run's own "
                     "`e1b` line, and no OFF-arm record carries it"),
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
