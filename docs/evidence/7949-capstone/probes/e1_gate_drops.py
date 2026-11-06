"""E1's mechanism, measured: does the v4 value gate drop ANY `^the <X>` Object?

Why this exists. E1's bar is written over a class (`^the <X>` `:Object` nodes)
that one pass either acts on or does not. `capstone_measure.py` can report the
SHARE a run produced, but a share cannot say whether the v4 selection path
touched it: two runs of the same arm differ by more than the bar's whole width
(measured 12.0% and 54.0% on the same corpus). This driver answers the
deterministic question instead, with ZERO model calls: apply the EXACT predicate
the S2.2 pass applies (`tortoise.value_gate.mechanical_verdict`, called from
`tortoise/vet_gate.py` at the `TORTOISE_VALUE_GATE` seam) to the candidate text
a run actually wrote, and count the drops.

⚠️ **`--sections` is not a convenience, it is the pass condition (#11201).**
`#10996` made the predicate default-ON at the fleet door, and its scope's Step 4
pass condition is written over the sections that pass JUDGES — `entities`,
`events`, `points` (`vet_gate.SECTIONS`) — not over Objects alone:

    "0 drops over points+events, or every non-zero drop named with its text and
     accepted by a reviewer as a `#2453` incidental. A non-zero, non-incidental
     count blocks the flip."

An Objects-only reading cannot evaluate that sentence, and the harm it hides is
concrete: a claim that is nothing but a reference is discarded, and no survivor
is left to reference the entity it named, so Rule 2 has nothing to preserve —
one Discard, two nodes gone (`tests/test_9889_capstone_probes.py::
test_the_mechanical_predicate_drops_a_claim_and_its_entity_together`). The
default is therefore EVERY section the pass judges, and `SECTION_LABELS`' keys
are pinned to `vet_gate.SECTIONS` by
`test_e1_gate_drops_defaults_to_every_section_the_pass_judges`, so the
instrument cannot silently cover less than the pass does.

⛔ **The graph must come from a run that pinned the predicate OFF.** `apply_vet`
REMOVES what the predicate discards before the payload is written
(`tortoise/extractor_v2.py`, the `_vet_gate.apply_vet(...)` call in
`_run_vet_pass`), so a run with `TORTOISE_VALUE_GATE` ON removes the points and
events it discards before the write — a census over such a graph under-counts
drops, and any `0` read off it is vacuous rather than reassuring. (An `:Object`
that `apply_vet` Rule 2 downgraded is the exception and DOES survive, so the
objects half is not vacuous in the same way — it is still the wrong arm to
measure, because the population is post-pass.) Since `#10996` "unset" IS the ON
arm, the OFF arm has to be pinned explicitly — that is what
`capstone_measure.measured_arm()` does (`ARM_OFF` sets `TORTOISE_VALUE_GATE` and
`TORTOISE_S2_DECISION_SEAM` to an explicit `"0"`). The
record STATES this precondition as `arm_precondition` and names its `graph`;
the authoritative witness is that run's `# arm:` readback in its own log
(`capstone_measure.arm_header` writes all four switches on every run), which this
probe cannot see — so the field is a warning attached to the number, never a
guarantee about it.

The positive controls are not decoration. A probe that reports "0 drops" without
showing that the predicate CAN drop would be indistinguishable from one whose
predicate is dead code, so two known identifier-only names (`#4118`, a path) and
the owner's must-keep (`the admin-merge rail`) are run through the same call and
printed beside the count.

Usage::

    export TORTOISE_DB_URI='docker://:<pw>@127.0.0.1:<port>/<graph>'
    python docs/evidence/7949-capstone/probes/e1_gate_drops.py \\
        --graph capstone_measure_<head8>_<epoch> --out /tmp/e1-gate.json

    # the Step-4 pass condition (default: every section the pass judges):
    python docs/evidence/7949-capstone/probes/e1_gate_drops.py \\
        --graph capstone_on_<head8>_<epoch> --sections objects,points,events \\
        --out e1-gate-drops-sections.json

Exit status: 0 when the measurement was taken (whatever the number is — a gate
that drops nothing is a RESULT, not a failure); 2 when no selected section
yields any candidate text in the graph, because a zero-denominator reading of
"0 drops" is not a result; 2 when the graph yields no `:Object` name, because the
legacy census keys are published on every run and `0 of 0 Objects` is that same
zero-denominator reading; 2 when `--sections` names a section (or nothing) this
instrument does not census.

⛔ **A refusal writes NO artifact** (#11242 review round 2). The `--out` file is
written only after the measurement is taken and every guard has passed, so an
exit-2 run leaves the path untouched — which also means an `--out` file that
EXISTS after a refused run is the PREVIOUS run's, not this one's. Check the exit
status first, then `coverage_complete`; the sibling `e1_paired_arm.py` states the
same contract ("a refusal writes no record").

⚠️ A section that yielded ZERO candidates is reported `measured: false` (and
named in `unmeasured_sections`), never as "0 drops": an empty section is a zero
denominator for that half of the pass condition, and `coverage_complete` says so
in the record, and the Step-4 headline below is then `null` rather than a `0`.

⛔ **THE PASS CONDITION'S NUMBER IS `step4_points_events_drops`.** `#10996` Step 4
is written over points+events, not over Objects. Read `coverage_complete` first,
then that field. **`drops_total` is the LEGACY Objects-only count** that
`e1_e2_scores.py` validates; reading it as the pass condition is a false PASS
whenever the Objects are clean and the claims are not — the exact shape `#11201`
was filed about.

⚠️ What a 0-drop RESULT does NOT say. `vet_gate.apply_vet` Rule 2 downgrades a
discard whose Object is still REFERENCED by a statement, and every audited Object
in the frozen corpus is wired to the statement it was minted from — so this
driver measures the PREDICATE, not the pass's net effect (the control that
separates the two is `e1_e2_probe.discriminating_control`, `E1-E2.md` limit 6).
It also does not separate every population member from a pass candidate: the
census excludes turn rows (`is_episodic`) and operator Points (`is_operator`), but
`extractor_v2._mint_endpoint` writes an unresolved operator endpoint as a
`pointKind: statement` Point carrying the model's own reference text with NO
distinguishing property — so an identifier-only minted ref still appears in the
`points` population as a drop the pass never made. That is an OVER-count (it can block the flip, never falsely pass
it), and it cannot be removed post hoc.
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

_PROBES = Path(__file__).resolve().parent
if str(_PROBES) not in sys.path:
    sys.path.insert(0, str(_PROBES))

from capstone_measure import graph_uri  # noqa: E402

#: Two names the predicate MUST drop and one it MUST keep. If the first pair
#: stops dropping, the predicate is dead and the zero below means nothing.
CONTROLS = ("#4118", "a/b/c.py", "the admin-merge rail")

#: The candidate sections this instrument censuses, each mapped to the graph
#: label(s) its candidates land on. ⛔ The KEYS are this instrument's section
#: names; `PASS_SECTION_OF` maps them onto `vet_gate.SECTIONS`, and that mapping
#: is what makes "the instrument covers the pass" checkable rather than
#: asserted. Sections follow the pass's own order (`vet_gate.SECTIONS`: entities,
#: events, points).
#:
#: ⚠️ ``:GraphEvent`` is deliberately NOT here: it is the claim-transition journal
#: store, created with ``seq``/``ts``/``type``/``event_id``/``payload`` and NO
#: ``name``/``content`` (`tortoise/event_store.py`'s ``CREATE (e:GraphEvent
#: {...})``), so it can contribute no candidate text — querying it would
#: full-scan the journal and always yield nothing. `capstone_measure.census`
#: still counts it (``census()["graph_events"]``) as a population fact, but THIS
#: instrument no longer calls `census` at all: it is not a candidate class the
#: predicate judges (#11242 review round 1 — the citation named a call this file
#: does not make).
SECTION_LABELS: dict[str, tuple[str, ...]] = {
    "objects": ("Object",),
    "events": ("Event",),
    "points": ("Point",),
}

#: Instrument section -> the section name `vet_gate.SECTIONS` uses. A section the
#: pass judges with NO entry here is the blind spot #11201 exists to close, and
#: `test_e1_gate_drops_defaults_to_every_section_the_pass_judges` reds on it.
PASS_SECTION_OF: dict[str, str] = {
    "objects": "entities",
    "events": "events",
    "points": "points",
}

#: EVERY section the pass judges — the default, because an Objects-only default
#: is exactly the reading that cannot evaluate the scope's pass condition.
DEFAULT_SECTIONS: tuple[str, ...] = tuple(PASS_SECTION_OF)


#: Per-label Cypher for the candidate population. ⛔ `:Point` is FILTERED: turn
#: rows (`is_episodic`) and operator Points (`is_operator`) are written outside
#: selection, so counting them would inflate the denominator AND let a graph whose
#: extraction produced no claims stamp `points.measured: true` — the same
#: composition `capstone_measure.census` partitions on. Pinned by
#: `tests/test_9889_capstone_probes.py::test_the_points_census_excludes_the_classes_the_pass_never_saw`.
CANDIDATE_CYPHER: dict[str, str] = {
    "Point": ("MATCH (n:Point) "
              "WHERE NOT coalesce(n.is_episodic, false) "
              "AND NOT coalesce(n.is_operator, false) "
              "RETURN n.name, n.content"),
}


def _text_of(label: str, name: object, content: object) -> str:
    """The CANDIDATE text of one graph node — a mirror rule, stated per class.

    This is NOT `vet_gate._item_text` (which is ``name or content`` for every
    section, because it reads the pre-write embed item). On a GRAPH the class
    decides where the candidate text lives: an `:Object` carries its identity in
    ``name``, an `:Event`/`:Point` carries its claim in ``content``. A nameless
    `:Object` therefore contributes NOTHING here rather than leaking claim text
    into ``object_names``, which downstream (`e1_e2_scores.py`) reads as a census
    of NAMES. Pinned by `tests/test_9889_capstone_probes.py::
    test_the_probe_reads_the_candidate_text_per_node_class`.

    ⚠️ Consequence, stated so it is not a silent difference: an entity item that
    carried only ``content`` and no ``name`` is judged by the pass and NOT
    counted here. Extraction names its entities in practice, so this is a
    disclosed residual rather than a measured class.
    """
    first = name if label == "Object" else content
    return str(first or "").strip()


def section_population(uri: str) -> dict[str, list[str]]:
    """Every candidate text per section, read from the graph — ONE graph seam.

    Kept as a single function so a test can stub the whole graph side (the
    assertions in `tests/test_9889_capstone_probes.py` are DB-free), and so the
    section list has exactly ONE definition to pin.
    """
    from tortoise.projection import FalkorProjection

    projection = FalkorProjection.from_uri(uri)
    population: dict[str, list[str]] = {}
    for section, labels in SECTION_LABELS.items():
        texts: list[str] = []
        for label in labels:
            rows = projection.g.query(
                CANDIDATE_CYPHER.get(
                    label, f"MATCH (n:{label}) RETURN n.name, n.content")
            ).result_set
            for name, content in rows:
                text = _text_of(label, name, content)
                if text:
                    texts.append(text)
        population[section] = texts
    return population


def measure(graph: str, sections: tuple[str, ...] = DEFAULT_SECTIONS) -> dict:
    from tortoise import value_gate
    from tortoise.definite_description_backfill import (
        is_bare_definite_description,
    )

    base = os.environ.get("TORTOISE_DB_URI") or ""
    if not base.strip():
        raise SystemExit("REFUSED: TORTOISE_DB_URI is not set")
    unknown = [s for s in sections if s not in SECTION_LABELS]
    if unknown or not sections:
        print(f"REFUSED: --sections names {unknown or 'nothing'} — this "
              f"instrument censuses {sorted(SECTION_LABELS)}")
        raise SystemExit(2)
    population = section_population(graph_uri(base, graph))

    def verdicts(texts: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for text in texts:
            verdict = value_gate.mechanical_verdict(text)
            if verdict is not None:
                found[text] = verdict["rule_id"]
        return found

    per_section: dict[str, dict] = {}
    for section in sections:
        texts = list(population.get(section) or [])
        drops = verdicts(texts)
        per_section[section] = {
            "candidates": len(texts),
            "measured": bool(texts),
            # NO zero for an unmeasured section — not on the console and not in
            # the artifact: a `drops_total: 0` here is a number a consumer can
            # pick up, which is the false PASS this probe exists to prevent.
            "drops": drops if texts else None,
            "drops_total": len(drops) if texts else None,
        }

    # The Objects half is read even when it is not requested: `object_names` /
    # `bare_definite` are this record's legacy keys and a downstream validator
    # (`e1_e2_scores.py`) reads an EMPTY `object_names` as an edit rather than a
    # producer shape, so "objects not requested" must not manufacture one.
    names = list(population.get("objects") or [])
    bare = [name for name in names if is_bare_definite_description(name)]
    # UNCONDITIONAL: `names` is already read for the legacy keys, so a
    # `--sections points,events` run must not publish `drops_total: 0` over an
    # Objects population it never measured — `e1_e2_scores.py` reads these keys
    # and would report "0 of N Objects" as a measurement.
    drops = verdicts(names)
    controls = {name: value_gate.mechanical_verdict(name) for name in CONTROLS}
    alive = (controls["#4118"] is not None
             and controls["a/b/c.py"] is not None
             and controls["the admin-merge rail"] is None)
    # Coverage is judged against EVERY section the PASS judges, not against what
    # this invocation asked for: `--sections objects` must not be able to stamp
    # `coverage_complete: true` on the very Objects-only reading #11201 condemns.
    pass_sections = list(PASS_SECTION_OF)
    # `objects` is censused UNCONDITIONALLY (the legacy keys), so it is measured
    # whether or not it was requested — otherwise `--sections points,events`
    # publishes an objects measurement AND calls objects unmeasured.
    measured = {s: bool(b["measured"]) for s, b in per_section.items()}
    measured.setdefault("objects", bool(names))
    unmeasured = [section for section in pass_sections
                  if not measured.get(section)]
    return {
        "graph": graph,
        "arm_precondition": (
            "the graph must be written by a run that pinned the predicate OFF "
            "(TORTOISE_VALUE_GATE=0): apply_vet removes the points/events it "
            "discards before the write, so such a graph under-counts drops and "
            "any 0 read off it is vacuous (a Rule-2-downgraded Object survives, "
            "so the objects half is post-pass rather than empty) (#11201)"),
        "sections_requested": list(sections),
        "sections": per_section,
        "coverage_complete": not unmeasured,
        "unmeasured_sections": unmeasured,
        "sections_not_requested": [s for s in pass_sections
                                   if s not in per_section],
        "objects": len(names),
        "bare_definite": len(bare),
        "bare_definite_share": (len(bare) / len(names)) if names else None,
        # The LEGACY objects keys follow the same rule as the per-section blocks:
        # an empty objects census is a zero denominator, so it publishes NO drop
        # number. Leaving `0` here while the record says `unmeasured_sections:
        # ["objects"]` was the last place a zero could be picked up.
        "value_gate_drops": drops if names else None,
        "drops_total": len(drops) if names else None,
        "drops_among_bare_definite": ([n for n in bare if n in drops]
                                       if names else None),
        "controls": controls,
        "predicate_alive": alive,
        "object_names": names,
        "candidates_total": (sum(b["candidates"] for b in per_section.values())
                             + (0 if "objects" in per_section else len(names))),
        # The objects census is measured UNCONDITIONALLY, so it is part of the
        # aggregate even when it was not requested — otherwise the record says
        # `coverage_complete: true` beside a `drops_across_sections` smaller than
        # the `drops_total` on the same line.
        # ⛔ `None` when ANY section in the record is unmeasured — the same
        # rule as the headline above, because `b["drops_total"] or 0` folds an
        # explicit unmeasured `None` into a pickable `0`, which is the zero this
        # probe exists to stop printing (review cycle 6, `#11242`).
        "drops_across_sections": (
            None if any(not b["measured"] for b in per_section.values())
            else sum((b["drops_total"] or 0) for b in per_section.values())
            + (0 if "objects" in per_section else len(drops))),
        # ⛔ THE PASS CONDITION'S OWN NUMBER (#10996 Step 4: "0 drops over
        # points+events"). `drops_total` above is the LEGACY Objects-only count,
        # and taking it for the pass condition is a false PASS whenever the
        # Objects are clean and the claims are not. `None` — never a 0 — when a
        # half is unmeasured: a sum's default would manufacture exactly the
        # measured-looking zero this review round removed one level down.
        "step4_points_events_drops": (
            None if any(s in unmeasured for s in ("points", "events"))
            else (per_section["points"]["drops_total"] or 0)
            + (per_section["events"]["drops_total"] or 0)),
    }


def _sections_line(record: dict) -> str:
    parts = []
    for section, block in record["sections"].items():
        if not block["measured"]:
            # NO DROP COUNT: the `0` printed is the measured CANDIDATE count, and
            # a `drops=0` beside `UNMEASURED` would be a zero a reader can pick
            # up — stopping that is this instrument's job.
            parts.append(f"{section}=0/UNMEASURED")
            continue
        parts.append(f"{section}={block['candidates']}"
                     f"(drops={block['drops_total']})")
    return "sections: " + " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--sections", default=",".join(DEFAULT_SECTIONS),
        help="comma-separated candidate sections to census — default is EVERY "
             f"section the S2.2 pass judges ({','.join(DEFAULT_SECTIONS)}); "
             "an Objects-only reading cannot evaluate #10996's Step-4 pass "
             "condition (#11201)")
    args = parser.parse_args(argv)
    sections = tuple(part.strip() for part in args.sections.split(",")
                     if part.strip())
    record = measure(args.graph, sections)
    print(f"objects={record['objects']} bare_definite={record['bare_definite']} "
          f"share={record['bare_definite_share']}")
    print(_sections_line(record))
    if record["drops_total"] is None:
        print("value_gate drops: UNMEASURED (the objects census is empty — a 0 "
              "here would be a zero-denominator reading); "
              f"STEP-4 (points+events) drops: "
              f"{record['step4_points_events_drops']} "
              f"coverage_complete={record['coverage_complete']}")
    else:
        print(f"value_gate drops: {record['drops_total']} "
              f"(among bare_definite: {len(record['drops_among_bare_definite'])}) "
              f"controls alive={record['predicate_alive']} "
              f"STEP-4 (points+events) drops: "
              f"{record['step4_points_events_drops']} "
              f"across sections: {record['drops_across_sections']} "
              f"coverage_complete={record['coverage_complete']}")
    if not any(block["candidates"] for block in record["sections"].values()):
        print("REFUSED: no selected section yields candidate text — 0 drops "
              "would be a zero-denominator reading, not a result")
        return 2
    if not record["objects"]:
        # ⛔ RESTORED, not relaxed: the legacy keys (`objects`, `bare_definite`,
        # `value_gate_drops`, `drops_total`) are published on EVERY run and
        # `e1_e2_scores.py` reads them as a census, so a candidate-bearing graph
        # with no `:Object` name would publish "0 of 0 Objects". Widening the
        # denominator to the pass's sections made this guard reachable in a new
        # way; it must not become the hole it replaced.
        print("REFUSED: the graph yields no Object names — `0 of 0 Objects` is "
              "a zero-denominator reading, not a result (the legacy census keys "
              "are published on every run)")
        return 2
    if not record["predicate_alive"]:
        print("REFUSED: the positive controls no longer drop — the predicate is "
              "dead, so the drop count measures nothing")
        return 2
    # ⛔ WRITTEN LAST, after every refusal (#11242 review round 1, measured). An
    # artifact on disk is what a later reader SCORES — `--out` used to be written
    # before these guards, so a REFUSED run (exit 2, "not a result") left
    # `step4_points_events_drops` on disk beside its `REFUSED` line. Measured at
    # `0e523239a5`: objects=0 + points/events populated → exit 2 AND an artifact
    # reading `step4_points_events_drops: 0` with `coverage_complete: false` — the
    # pass condition's own number, pickable, from a run that measured nothing.
    Path(args.out).write_text(json.dumps(record, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
