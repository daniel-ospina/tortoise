"""#9889 — the capstone's three measurement probes, and their FALSIFIERS.

`docs/evidence/7949-capstone/probes/` carries three additive probes that make
#7949's E1 and E2 rows *producible* instead of refused (`e1_paired_arm.py` — the
OFF arm E1's bar is written over; `e1_gate_drops.py` — E1's mechanism, measured
deterministically; `e2a_value_units.py` — the frozen 14-unit denominator).

The value this module pins is not "the probes run". Every probe here reports a
number, and a probe whose counter is stuck reports a number too — so what is
pinned is each probe's **falsifier**: the condition under which it stops being
allowed to call its own output a measurement.

* `e1_gate_drops` exits **2** when its positive controls stop dropping (the
  predicate is dead, so "0 drops" measures nothing) and **2** when the graph
  yields no Object names (a zero denominator must never print as "0 drops").
* `e1_paired_arm` refuses, measuring nothing, when ANY switch that arms the
  passes is already set — including `TORTOISE_S2_DECISION_SEAM`, which arms the
  same pass through the arbiter and so would produce an ON population under an
  OFF label — refuses before any model call when the backend cannot extract or
  when the checkout's corpus is not the pre-registration's frozen one (G1: "a bar
  is a threshold AND its corpus", #9076), and refuses (exit **2**, writing no
  record) when the run's own population is one E1's record refuses: a zero
  denominator, fewer `:Object` nodes than the frozen
  `denominator_floors.e1_objects`, or a before-share under the frozen
  `denominator_floors.e1_min_before_share`. It reads both floors out of the
  pre-registration rather than typing them, and its record carries the
  `fixtures_hash` and `head` the arm was taken at.
* `e2a_value_units` refuses when injecting every lost unit's verbatim anchor
  moves the survival counter by nothing (an inert counter), refuses before any
  reading when no searchable backend is configured or when the graph holds no
  memory-level Points at all (an ABSENT population would otherwise score as a
  confident `0 of 14`, and its own falsifier would certify it), and its
  `--check-denominator` reads §3.3's denominator off the gold rather than
  typing it.

Every assertion is DB-free, network-free and LLM-free: the probe modules are
stdlib at import time, the two shipped instruments used as oracles
(`value_gate.mechanical_verdict`, `grading.strict_survival_counts`) are pure
functions, and every graph read is stubbed at the seam the probe calls.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_PROBES = _REPO / "docs/evidence/7949-capstone/probes"
if str(_PROBES) not in sys.path:
    sys.path.insert(0, str(_PROBES))


def _load(name: str):
    """Import a probe module by PATH — the bundle has no `__init__.py`."""
    spec = _ilu.spec_from_file_location(f"probe_{name}", _PROBES / f"{name}.py")
    assert spec and spec.loader, f"{name}.py is not importable from {_PROBES}"
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e1_gate_drops = _load("e1_gate_drops")
e1_paired_arm = _load("e1_paired_arm")
e2a_value_units = _load("e2a_value_units")


# ── e1_gate_drops: the two refusals, and the measurement between them ───────

def _fake_population(**sections: list[str]):
    """Stub the ONE graph seam: the section -> candidate-text population."""

    def section_population(uri: str) -> dict[str, list[str]]:
        assert "/" in uri, f"a graph URI was expected, got {uri!r}"
        return {name: list(texts) for name, texts in sections.items()}

    return section_population


def _run_gate_drops(monkeypatch, tmp_path, *, object_names: list[str] | None = None,
                    population: dict[str, list[str]] | None = None,
                    sections: str | None = None,
                    verdict=None) -> int:
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/tortoise_test")
    stub = population if population is not None else {"objects": list(object_names or [])}
    monkeypatch.setattr(e1_gate_drops, "section_population", _fake_population(**stub))
    if verdict is not None:
        import tortoise.value_gate as value_gate
        monkeypatch.setattr(value_gate, "mechanical_verdict", verdict)
    argv = ["--graph", "g", "--out", str(tmp_path / "o.json")]
    if sections is not None:
        argv += ["--sections", sections]
    return e1_gate_drops.main(argv)


def _measure(monkeypatch, tmp_path, *, population: dict[str, list[str]],
             verdict=None, sections: str | None = None) -> tuple[int, dict]:
    """Run the instrument and return `(exit_code, record)`."""
    exit_code = _run_gate_drops(monkeypatch, tmp_path, population=population,
                                verdict=verdict, sections=sections)
    return exit_code, json.loads((tmp_path / "o.json").read_text())


def test_e1_gate_drops_refuses_when_the_positive_controls_stop_dropping(
        monkeypatch, tmp_path, capsys):
    """A dead predicate must not be able to report "0 drops" as a result."""
    exit_code = _run_gate_drops(
        monkeypatch, tmp_path,
        object_names=["the admin-merge rail", "#4118"],
        verdict=lambda text: None)

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "predicate is dead" in out
    assert "value_gate drops: 0" in out, (
        "the zero is still printed — the refusal is what stops it being a RESULT")


def test_e1_gate_drops_refuses_a_zero_denominator(monkeypatch, tmp_path, capsys):
    """No candidate text in ANY section is a missing denominator, never a pass.

    Widened from Objects to the whole selection in #11201: with `points` and
    `events` selectable, a graph that yields nothing anywhere is still the same
    zero-denominator reading the probe has always refused.
    """
    exit_code = _run_gate_drops(monkeypatch, tmp_path, object_names=[])

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "no selected section yields candidate text" in out
    assert "objects=0" in out


def test_e1_gate_drops_refuses_an_unknown_section_with_exit_2(monkeypatch, tmp_path):
    """A misspelt `--sections` is a REFUSAL (2), not a traceback or a silent skip."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/tortoise_test")
    monkeypatch.setattr(e1_gate_drops, "section_population",
                        _fake_population(objects=["the deploy rail"], points=[],
                                         events=[], bogus=[]))

    with pytest.raises(SystemExit) as caught:
        e1_gate_drops.main(["--graph", "g", "--sections", "objects,bogus",
                            "--out", str(tmp_path / "o.json")])

    assert caught.value.code == 2


def test_e1_gate_drops_coverage_is_judged_over_the_pass_not_the_request(
        monkeypatch, tmp_path):
    """`--sections objects` must not stamp `coverage_complete: true` (#11201).

    The flag exists to stop an Objects-only reading from passing as the whole
    Step-4 condition; scoping it to the REQUEST re-introduces exactly that.
    """
    exit_code, record = _measure(
        monkeypatch, tmp_path, population={"objects": ["the deploy rail"]},
        sections="objects")

    assert exit_code == 0
    assert record["coverage_complete"] is False
    assert record["unmeasured_sections"] == ["events", "points"]
    assert record["sections_not_requested"] == ["events", "points"]
    # The aggregate is NOT asserted here: with `sections="objects"` the
    # unconditional-objects branch contributes 0 either way, so any `>=` between
    # the aggregates holds with or without the code it would claim to pin
    # (review cycle 6). `test_e1_gate_drops_aggregates_include_the_unconditional_
    # objects_census` is the test that actually reds on that mutant.
    assert record["sections"] == {"objects": record["sections"]["objects"]}, (
        "only the requested section is measured — the rest are disclosed, not invented")
    assert "TORTOISE_VALUE_GATE=0" in record["arm_precondition"], (
        "the record must state that the graph has to come from a gate-OFF run")


def test_e1_gate_drops_measures_when_both_halves_are_alive(
        monkeypatch, tmp_path):
    """The controls alive + a non-empty graph = a measurement, whatever it says."""
    exit_code = _run_gate_drops(
        monkeypatch, tmp_path,
        object_names=["the admin-merge rail", "#4118", "the deploy rail"])

    assert exit_code == 0
    record = json.loads((tmp_path / "o.json").read_text())
    assert record["objects"] == 3
    assert record["bare_definite"] == 2
    assert record["drops_total"] == 1, "#4118 is the identifier-only name"
    assert record["drops_among_bare_definite"] == []
    assert record["predicate_alive"] is True


def test_e1_gate_drops_controls_are_the_real_predicates_behaviour():
    """Pinned against the SHIPPED predicate: the controls must still mean this.

    If `value_gate` stops dropping `#4118`/`a/b/c.py` — or starts dropping the
    owner's must-keep `the admin-merge rail` — every "0 drops" the probe reports
    is a statement about a dead or over-broad predicate, and the refusal above
    fires. This assertion is what makes that refusal reachable rather than
    theoretical.
    """
    from tortoise import value_gate

    verdicts = {name: value_gate.mechanical_verdict(name)
                for name in e1_gate_drops.CONTROLS}

    assert verdicts["#4118"] is not None
    assert verdicts["a/b/c.py"] is not None
    assert verdicts["the admin-merge rail"] is None


# ── e1_gate_drops: the SECTIONS the pass judges, not just its Objects ───────

def test_e1_gate_drops_accepts_the_sections_flag_the_eval_lane_needs(
        monkeypatch, tmp_path, capsys):
    """`--sections` is the Step-4 pass condition's own switch (#10996, #11201).

    Without it the instrument can only census `:Object` names, so the scope's
    "0 drops over points+events, or each named and accepted" is not evaluable —
    the precise gap a default-on flip must never ship with.
    """
    exit_code = _run_gate_drops(
        monkeypatch, tmp_path,
        population={"objects": ["the deploy rail"],
                    "points": ["#4118", "timeout is not on macOS"],
                    "events": ["the lease landed"]},
        sections="objects,points,events")

    assert exit_code == 0
    record = json.loads((tmp_path / "o.json").read_text())
    assert set(record["sections"]) == {"objects", "points", "events"}
    assert record["sections"]["points"]["candidates"] == 2
    assert record["sections"]["points"]["drops_total"] == 1
    assert set(record["sections"]["points"]["drops"]) == {"#4118"}
    assert record["sections"]["objects"]["drops_total"] == 0
    assert record["sections"]["events"]["drops_total"] == 0
    assert record["coverage_complete"] is True
    out = capsys.readouterr().out
    assert "points=2" in out, "the per-section population must be printed, not only Objects"


def test_e1_gate_drops_defaults_to_every_section_the_pass_judges(
        monkeypatch, tmp_path):
    """The DEFAULT must be the pass's coverage — Objects-only is the bug (#11201).

    Pinned to `vet_gate.SECTIONS`, which is the authority for what the pass
    iterates: if a section is added there and not here, this reds instead of the
    instrument silently measuring less than the pass judges.
    """
    from tortoise import vet_gate

    pass_sections = {section for section, _f, _k, _fam in vet_gate.SECTIONS}
    assert pass_sections == {"entities", "events", "points"}, (
        "the pass's section table moved — re-derive the instrument's coverage")

    _, recorded = _measure(monkeypatch, tmp_path, population={
        "objects": ["the deploy rail"], "points": ["the lease fix"], "events": ["the retro"]})

    assert set(recorded["sections"]) == set(e1_gate_drops.DEFAULT_SECTIONS)
    assert set(e1_gate_drops.DEFAULT_SECTIONS) == set(e1_gate_drops.SECTION_LABELS)
    assert (set(e1_gate_drops.PASS_SECTION_OF.values())
            == pass_sections), (
        "every section the pass judges needs an instrument section mapped to it — "
        "an unmapped pass section is the blind spot #11201 exists to close")


def test_e1_gate_drops_discloses_an_empty_section_instead_of_zero(monkeypatch, tmp_path,
                                                                  capsys):
    """A section with no candidates is UNMEASURED, never "0 drops" (#11201).

    `points+events` on a graph that wrote no events is not a clean pass
    condition; it is a zero denominator, and printing `0` over it is the same
    class of false PASS the probe's Object-side refusal already forbids.
    """
    exit_code = _run_gate_drops(
        monkeypatch, tmp_path,
        population={"objects": ["the deploy rail"], "points": ["#4118"], "events": []})

    assert exit_code == 0
    record = json.loads((tmp_path / "o.json").read_text())
    assert record["sections"]["events"]["measured"] is False
    assert record["sections"]["events"]["drops"] is None, (
        "an unmeasured section must carry NO drop dict — a `{}` is a zero result")
    assert record["sections"]["events"]["drops_total"] is None, (
        "an unmeasured section must carry NO drop count, in the artifact as "
        "well as on the console")
    assert record["coverage_complete"] is False
    assert record["unmeasured_sections"] == ["events"]
    assert record["step4_points_events_drops"] is None, (
        "a missing half makes the Step-4 number UNTAKEN (null), never 0 — a "
        "sum's default is the same false zero one level up")
    assert record["drops_across_sections"] is None, (
        "the same rule one field over: `drops_total or 0` folds an unmeasured "
        "section into a pickable 0 (review cycle 6)")
    out = capsys.readouterr().out
    assert "events=0/UNMEASURED" in out


def test_e1_gate_drops_publishes_the_pass_conditions_own_number(monkeypatch, tmp_path):
    """The Step-4 number is points+events, in its OWN field (#11201).

    `drops_total` is the LEGACY Objects-only count. A reader who takes it for the
    pass condition reads a false PASS — coverage complete, `drops_total: 0`,
    while the `points` the condition is written over dropped N.
    """
    exit_code, record = _measure(monkeypatch, tmp_path, population={
        "objects": ["the deploy rail"],
        "points": ["#4118", "the lease fix"],
        "events": ["closes #4118"],
    })

    assert exit_code == 0
    assert record["sections"]["points"]["drops_total"] == 1
    assert record["sections"]["events"]["drops_total"] == 1
    assert record["step4_points_events_drops"] == 2, (
        "the pass condition's number is points+events drops, published as its own "
        "field so the Objects-only count cannot be read for it")
    assert record["drops_total"] == 0, (
        "the legacy Objects-only field keeps its meaning (and its name)")


def test_e1_gate_drops_still_refuses_a_graph_with_no_objects(monkeypatch, tmp_path,
                                                             capsys):
    """Widening the denominator must not drop the Objects guard.

    The legacy census keys are published on EVERY run and `e1_e2_scores.py` reads
    them as a census, so a candidate-bearing graph with no `:Object` name would
    publish "0 of 0 Objects" — the reading this probe has always refused. A
    refusal writes NO artifact: an `--out` file beside a `REFUSED` line is what a
    later reader scores, and this record's `step4_points_events_drops` would be a
    measured-looking `0` for the pass condition (#11242 review round 1).
    """
    exit_code = _run_gate_drops(monkeypatch, tmp_path, population={
        "objects": [], "points": ["the lease fix"], "events": ["the retro"]})

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "no Object names" in out
    assert "value_gate drops: UNMEASURED" in out
    assert not (tmp_path / "o.json").exists(), (
        "a refused run measured nothing — leaving `--out` on disk publishes "
        "`step4_points_events_drops: 0` for a run that refused")


def test_e1_gate_drops_measure_keeps_the_legacy_keys_null_on_an_empty_objects_census(
        monkeypatch):
    """The None-not-0 rule, pinned at `measure()` — where no refusal hides it.

    `main()` now refuses a graph with no `:Object` name, so the legacy keys'
    `None` state is unreachable THROUGH it. It is still reachable through
    `measure()` (a caller, or a future main), and that is where the rule that an
    empty census publishes NO drop number has to hold: `drops if names else None`
    becoming `len(drops) if names else 0` reds this and nothing else.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/tortoise_test")
    monkeypatch.setattr(
        e1_gate_drops, "section_population",
        _fake_population(objects=[], points=["the lease fix"],
                         events=["the retro landed"]))
    record = e1_gate_drops.measure("g", ("objects", "points", "events"))

    assert record["value_gate_drops"] is None
    assert record["drops_total"] is None
    assert record["drops_among_bare_definite"] is None
    assert record["unmeasured_sections"] == ["objects"]
    assert record["coverage_complete"] is False


def test_e1_gate_drops_point_drops_come_from_the_predicate_not_a_constant(
        monkeypatch, tmp_path):
    """MUTATION PROOF, taken at `measure()` — where no refusal can mask it.

    A per-section counter that does not read `mechanical_verdict` would report
    the same numbers whatever the predicate does — the shape this whole issue is
    about. Through `main()` a dead predicate now REFUSES (exit 2) and writes no
    record, so the count can only be pinned one layer down: `measure()` has no
    refusal, and there the same population yields **0** drops under a dead
    predicate and **2** under the live one. Measured at #11242 review round 2: a
    counter hard-coded to `2` reds the `== 0` assertion, and a counter that
    counts candidates instead of drops reds it too (2 candidates vs 0 drops).
    """
    population = {"objects": ["the deploy rail"], "points": ["#4118", "a/b/c.py"],
                  "events": ["the retro"]}

    _, live = _measure(monkeypatch, tmp_path, population=population)
    assert live["sections"]["points"]["drops_total"] == 2

    monkeypatch.setattr(e1_gate_drops, "section_population",
                        _fake_population(**population))
    import tortoise.value_gate as value_gate
    monkeypatch.setattr(value_gate, "mechanical_verdict", lambda text: None)
    dead = e1_gate_drops.measure("g", ("objects", "points", "events"))
    assert dead["sections"]["points"]["drops_total"] == 0, (
        "the point counter did not follow the predicate — it is a constant or a "
        "candidate count, not a drop count")

    # …and the refusal `main()` performs on that same dead predicate is a pin of
    # its own: a dead predicate must exit 2, never report 0 through a counter
    # that never read it.
    dead_exit = _run_gate_drops(monkeypatch, tmp_path, population=population,
                                verdict=lambda text: None)
    assert dead_exit == 2, (
        "a dead predicate must refuse, not report 0 through a counter that "
        "never read it")


def _vet_and_apply(embed: dict) -> tuple[dict, dict, dict, list[str]]:
    """Run the real S2.2 pass and apply its verdicts; DB-free, model-free."""
    from tortoise import vet_gate

    armed = vet_gate.vet_candidates(embed)
    kept, warnings = vet_gate.apply_vet(embed, armed["decisions"])
    return armed, kept, armed["stats"]["mechanical"], warnings


def test_the_mechanical_predicate_drops_a_claim_and_its_entity_together(monkeypatch):
    """THE BLIND SPOT, reproduced — and its counterfactual, so the shield is real.

    `apply_vet` Rule 2 keeps a discarded entity only while a SURVIVING candidate
    references it, and the reference surfaces are `about_entities`/`slots` — NOT
    the claim's `content` (`vet_gate._referenced_entity_names`). So when the only
    candidate that references an entity is an identifier-only *claim*, that claim
    is discarded, nothing surviving references the entity, and the entity goes
    with it. Two nodes leave the payload for one session; an Objects-only census
    counts one of them, which is `#11201`'s gap.

    The second half is the CONTROL, and it is what makes the first half a
    mechanism rather than a coincidence: swap in a claim that survives and still
    references the entity and Rule 2 keeps it (`referential integrity`). Without
    that pair, a fixture whose claim referenced nothing would assert the same
    "entity gone" for the wrong reason — the defect this test shipped with, caught
    in review.
    """
    # PINNED, not ambient: `TORTOISE_VALUE_GATE=0` is a documented value a lane
    # may have exported, and this test exists BECAUSE the default arms the gate.
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")

    # A — the ONLY referrer is an identifier-only claim, which the predicate drops.
    armed, kept, mechanical, warnings = _vet_and_apply({
        "entities": [{"name": "#4118"}],
        "points": [{"content": "closes #4118", "about_entities": ["#4118"]},
                   {"content": "the lease fix"}],
        "events": [],
    })
    assert mechanical["fired"] == 2, mechanical
    assert sorted(d["text"] for d in armed["decisions"].values()
                  if d["outcome"] == "DISCARD") == ["#4118", "closes #4118"]
    assert [e["name"] for e in kept["entities"]] == [], (
        "the entity the discarded claim alone referenced must be gone — that is "
        "the memory-loss direction the point/event rate measures")
    assert [p["content"] for p in kept["points"]] == ["the lease fix"]
    assert not any("referential integrity" in w for w in warnings), (
        "Rule 2 must NOT fire: the only referrer was discarded with it")

    # B — the control: a SURVIVING referrer shields the same entity.
    _, kept_b, mechanical_b, warnings_b = _vet_and_apply({
        "entities": [{"name": "#4118"}],
        "points": [{"content": "the lease-batch fix", "about_entities": ["#4118"]}],
        "events": [],
    })
    assert mechanical_b["fired"] == 1, mechanical_b
    assert [e["name"] for e in kept_b["entities"]] == ["#4118"], (
        "Rule 2 keeps a discarded entity that a surviving candidate references")
    assert any("referential integrity" in w for w in warnings_b), (
        "the downgrade must be recorded, not silent")


def test_e1_gate_drops_aggregates_include_the_unconditional_objects_census(
        monkeypatch, tmp_path):
    """`--sections points,events` must NOT publish aggregates that omit objects.

    The objects half is censused whether or not it is requested (its legacy keys
    are), so it is part of `candidates_total`/`drops_across_sections` too —
    otherwise `coverage_complete: true` could sit beside a
    `drops_across_sections` smaller than the `drops_total` on the same line, and
    the `else` branch that fixes it would have no test at all.
    """
    exit_code, record = _measure(
        monkeypatch, tmp_path,
        population={"objects": ["#4118", "a/b/c.py", "the deploy rail"],
                    "points": ["closes #4118", "the lease fix"],
                    "events": ["the retro", "#99"]},
        sections="points,events")

    assert exit_code == 0
    assert set(record["sections"]) == {"points", "events"}, (
        "only the requested sections are censused")
    assert record["candidates_total"] == 7, record["candidates_total"]
    assert record["drops_across_sections"] == 4, record["drops_across_sections"]
    assert record["drops_across_sections"] > record["drops_total"]


def test_e1_gate_drops_refuses_a_request_that_yields_no_candidates(
        monkeypatch, tmp_path, capsys):
    """A selected-empty request is a zero denominator even when objects exist.

    `candidates_total` counts the unconditional objects census, so guarding the
    refusal on it would let `--sections points,events` exit 0 over two empty
    sections — "measured" for exactly the reading Step 4 is written over.
    """
    exit_code = _run_gate_drops(
        monkeypatch, tmp_path,
        population={"objects": ["#4118", "the deploy rail"],
                    "points": [], "events": []},
        sections="points,events")

    assert exit_code == 2
    assert "no selected section yields candidate text" in capsys.readouterr().out


def test_the_points_census_excludes_the_classes_the_pass_never_saw():
    """A `:Point` is not automatically a VET candidate — the query must say so.

    Turn rows (`is_episodic`) and operator Points (`is_operator`) are written
    outside selection, so without the filter a graph whose extraction produced
    NO claims still stamps `points.measured: true` and `coverage_complete: true`
    — a completeness claim over a section the pass never populated. Pinned at the
    query, because that is where the composition decision lives.
    """
    point_cypher = " ".join(e1_gate_drops.CANDIDATE_CYPHER["Point"].split())

    assert ("WHERE NOT coalesce(n.is_episodic, false) "
            "AND NOT coalesce(n.is_operator, false)") in point_cypher, (
        "the conjunction is the pin: an `OR` re-admits every turn row, and a "
        "token-presence assertion cannot tell the filter from its negation")


def test_the_probe_reads_the_candidate_text_per_node_class():
    """`_text_of` is the probe's own MIRROR rule, and it had no pin at all.

    `:Object` carries its identity in `name` (the census reads `n.name` and an
    empty name is skipped), while `:Point`/`:Event` carry the claim in `content`.
    The order differs by node class; that is a graph fact, not the pass's
    `_item_text` rule, and this test exists so the difference is stated rather
    than claimed in a docstring.
    """
    text_of = e1_gate_drops._text_of

    assert text_of("Object", "the deploy rail", None) == "the deploy rail"
    assert text_of("Object", None, "the lease fix") == "", (
        "a nameless Object must not put claim text into `object_names`")
    assert text_of("Point", "ignored", "the lease fix") == "the lease fix"
    assert text_of("Event", None, "the retro landed") == "the retro landed"
    assert text_of("Point", None, None) == ""


# ── e1_paired_arm: a mislabelled arm must be worse than no arm ──────────────

def test_the_off_arm_refuses_every_switch_that_arms_the_passes(monkeypatch):
    """Each switch alone, because the refusal is per switch, not one lumped check."""
    import pytest

    for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
        for value in ("1", "true"):
            monkeypatch.setenv(key, value)
            with pytest.raises(SystemExit) as caught:
                e1_paired_arm.off_arm_readback()
            assert key in str(caught.value), (key, value)
            monkeypatch.delenv(key)


def test_the_off_arm_reads_back_all_four_switches(monkeypatch):
    """The record must NAME every switch, so a reader sees what was consulted.

    `#10996`: the OFF arm sets ``TORTOISE_VALUE_GATE=0`` EXPLICITLY — the other
    three stay absent, whose unset value is still OFF. The value gate is the one
    switch whose ABSENT value is the ON arm, so it is asserted here as ``"0"``
    and not as ``None``; the pin below is what keeps that distinction alive.
    """
    for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "0")

    arm = e1_paired_arm.off_arm_readback()

    assert set(arm) == {*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY}
    assert arm["TORTOISE_VALUE_GATE"] == "0"
    assert {k: v for k, v in arm.items() if k != "TORTOISE_VALUE_GATE"} == \
        dict.fromkeys([k for k in arm if k != "TORTOISE_VALUE_GATE"])


def test_the_off_arm_PINS_a_DEFAULT_ON_switch_left_unset(monkeypatch):
    """`#10996` review P1: an unset default-ON switch read as the OFF arm.

    Measured before the fix: with only the four keys cleared,
    ``value_gate.value_gate_enabled()`` was ``True`` inside an arm this driver
    labelled OFF, so the pre-registered CONTROL was scored with the `#4899`
    predicate armed — a fabricated before/after pair, silently. The driver now
    PINS the switch to `0` and reads it back (a set, truthy value is still
    refused above), so the label matches the resolved state.
    """
    from tortoise import value_gate

    assert e1_paired_arm.DEFAULT_ON_KEYS == ("TORTOISE_VALUE_GATE",)
    for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
        monkeypatch.delenv(key, raising=False)
    assert value_gate.value_gate_enabled() is True  # the default we must pin away

    arm = e1_paired_arm.off_arm_readback()

    assert arm["TORTOISE_VALUE_GATE"] == "0", arm
    assert value_gate.value_gate_enabled() is False, (
        "the arm is labelled OFF while the resolved predicate is ON — the "
        "fabricated-pair defect this pin exists for")


def test_the_off_arm_accepts_an_explicit_zero(monkeypatch):
    """`TORTOISE_VET=0` IS the OFF arm — refusing it would refuse the run it wants."""
    for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
        monkeypatch.setenv(key, "0")

    arm = e1_paired_arm.off_arm_readback()

    assert set(arm.values()) == {"0"}


def test_the_off_arm_accepts_every_OFF_spelling_the_pipeline_honours(monkeypatch):
    """The refusal is an ALLOW-LIST, not a deny-list of the spellings I typed.

    Pre-fix, `off_arm_readback` computed "is this switch armed?" as
    ``v not in (None, "", "0")``, so every OTHER OFF spelling the pipeline
    honours — `false`, `no`, `off`, `FALSE` — was REFUSED as "the shell armed
    this arm" and the run could not be taken at all. That is the deny-list class
    `#10610` removed from the scorer (`_ARM_OFF_STATES` -> `_TRUTHY`), surviving
    in the driver that PRODUCES the arm the scorer reads.

    Asserted on the RESOLVED predicate as well as the spelling: the claim is that
    these values are the OFF arm, and only `value_gate` can confirm it.
    """
    from tortoise import value_gate

    off_spellings = ("0", "false", "no", "off", "FALSE", "Off", " ", "")
    for spelled in off_spellings:
        for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
            monkeypatch.setenv(key, spelled)
        arm = e1_paired_arm.off_arm_readback()
        assert value_gate.value_gate_enabled() is False, (
            f"{spelled!r} is an OFF spelling the pipeline honours, so the arm is "
            f"OFF and the driver must not refuse it: {arm}")

    # …and the other direction still fires: a truthy value IS refused.
    for truthy in ("1", "true", "yes", "on", "TRUE"):
        for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
            monkeypatch.setenv(key, "0")
        monkeypatch.setenv("TORTOISE_VALUE_GATE", truthy)
        with pytest.raises(SystemExit, match="REFUSED"):
            e1_paired_arm.off_arm_readback()


# ── e1_paired_arm: the before-share is refused when E1's own record refuses it ─

def _fake_arm_census(object_names: list[str]):
    def census(uri: str) -> dict:
        assert "/" in uri, f"a graph URI was expected, got {uri!r}"
        return {"object_names": list(object_names), "label_counts": {}}
    return census


def _prereg_record(**floor_overrides) -> dict:
    """The REAL frozen record, with the floors under test moved.

    The probe must READ its floors; a stub that returned a bare
    `denominator_floors` dict would let a probe that types its own number pass.
    """
    record = json.loads(
        (_REPO / "docs/evidence/7949-capstone/preregistration.json").read_text())
    record["denominator_floors"] = {**record["denominator_floors"],
                                    **floor_overrides}
    return record


def _run_paired_arm(monkeypatch, tmp_path, *, object_names: list[str],
                    ok: bool = True, floor: int = 30,
                    share_floor: float = 0.2,
                    ready: tuple[bool, str] = (True, "ok"),
                    corpus_sha: str | None = "sha256:frozen",
                    runs: list | None = None) -> tuple[int, list]:
    """Drive `main()` itself — the run, the record and the refusals.

    Pre-fix, no case in this module called `e1_paired_arm.main` at all, so the
    DB refusal, the run, the record shape, the failed-session exit and the
    denominator refusal below all shipped untested.

    `corpus_sha=None` leaves the SHIPPED `corpus_pin` in place, so G1 itself
    decides — the only way to prove the corpus refusal is the real gate.
    """
    for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/tortoise_test")
    monkeypatch.setattr(e1_paired_arm, "backend_ready", lambda: ready)
    monkeypatch.setattr(e1_paired_arm, "resolve_head", lambda: "a" * 40)
    monkeypatch.setattr(
        e1_paired_arm, "read_preregistration",
        lambda sha: _prereg_record(e1_objects=floor,
                                   e1_min_before_share=share_floor))
    if corpus_sha is not None:
        monkeypatch.setattr(e1_paired_arm, "corpus_pin", lambda prereg: corpus_sha)
    monkeypatch.setattr(e1_paired_arm, "census", _fake_arm_census(object_names))
    recorded_runs = runs if runs is not None else []

    def fake_run(graph: str, sessions: list[str]) -> list[dict]:
        recorded_runs.append((graph, sessions))
        return [{"session_id": "wp01_quarry_debug", "ok": ok,
                 "extracted": True, "elapsed_s": 0.5}]

    monkeypatch.setattr(e1_paired_arm, "run_corpus", fake_run)
    exit_code = e1_paired_arm.main(
        ["--graph", "g", "--out", str(tmp_path / "off.json")])
    return exit_code, recorded_runs


def _measured_arm(monkeypatch, tmp_path, *, ok: bool = True) -> tuple[int, list]:
    """A run above BOTH frozen floors: 30 Objects, 7 of them bare-definite.

    7/30 = 0.2333 clears `e1_min_before_share` (0.20) — the population floor and
    the share floor are separate refusals, and a case for one must not trip the
    other.
    """
    return _run_paired_arm(
        monkeypatch, tmp_path, ok=ok,
        object_names=[f"the rail {n}" for n in range(7)]
        + [f"item {n}" for n in range(23)])


def test_the_paired_arm_refuses_a_denominator_under_the_frozen_floor(
        monkeypatch, tmp_path, capsys):
    """12 Objects is not a before-share: the record itself REFUSES that reading.

    E1's bar is a SHARE, and the frozen record floors its denominator at 30
    Objects (`denominator_floors.e1_objects`, applied by
    `capstone_measure.e1_outcome`). Emitting the share anyway would hand the
    next reader a decidable-looking pair out of a run the record calls
    undecidable — on the metric this PR itself measures as noise-dominated.
    """
    exit_code, _ = _run_paired_arm(
        monkeypatch, tmp_path,
        object_names=[f"the rail {n}" for n in range(12)])

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "12 :Object nodes" in out
    assert "denominator_floors.e1_objects = 30" in out, (
        "the refusal must cite the frozen floor it applied, not a bare number")
    assert not (tmp_path / "off.json").exists(), (
        "a refused number must leave no artifact behind — a record IS what a "
        "later reader scores")


def test_the_paired_arm_refuses_a_zero_denominator(monkeypatch, tmp_path,
                                                   capsys):
    """#9889 acceptance item 2, applied to the arm E1 is actually scored on."""
    exit_code, _ = _run_paired_arm(monkeypatch, tmp_path, object_names=[])

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "no :Object nodes" in out
    assert "objects=0" in out
    assert not (tmp_path / "off.json").exists()


def test_the_paired_arm_measures_at_the_floor(monkeypatch, tmp_path):
    """Exactly the floor is a measurement — the refusal must not overreach."""
    exit_code, runs = _measured_arm(monkeypatch, tmp_path)

    assert exit_code == 0
    assert len(runs) == 1, "one pass over the pre-registered corpus, not two"
    record = json.loads((tmp_path / "off.json").read_text())
    assert record["objects"] == 30
    assert record["bare_definite"] == 7
    assert record["share"] == 7 / 30
    assert record["object_floor"] == 30, (
        "the record must name the floor that gated it, so a reader can see the "
        "number was produced above one")
    assert record["min_before_share"] == 0.2, (
        "the record must name BOTH floors, or a reader cannot tell which one "
        "the share cleared")
    assert record["head"] == "a" * 40
    assert record["fixtures_hash"] == "sha256:frozen", (
        "the record must pin the corpus it was taken on (plus the head): a "
        "share whose population no artifact names cannot be checked against "
        "the frozen corpus (#9076)")


def test_the_paired_arm_refuses_a_before_share_under_the_frozen_minimum(
        monkeypatch, tmp_path, capsys):
    """`e1_min_before_share` — E1's SECOND registered refusal, applied.

    The record refuses E1 when the CONTROL arm's share is under 0.20 ("a share
    metric needs a non-trivial base"). This probe produces exactly that number,
    so emitting it above a denominator floor but under this one would hand a
    reader a before-share the record itself calls REFUSED.
    """
    exit_code, _ = _run_paired_arm(
        monkeypatch, tmp_path,
        object_names=[f"the rail {n}" for n in range(5)]
        + [f"item {n}" for n in range(25)])  # 5/30 = 0.1667 < 0.20

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "0.166667" in out
    assert "denominator_floors.e1_min_before_share = 0.2" in out, (
        "the refusal must cite the frozen floor it applied, not a bare number")
    assert not (tmp_path / "off.json").exists(), (
        "a refused number must leave no artifact behind")


def test_the_paired_arm_measures_a_share_above_the_frozen_minimum(
        monkeypatch, tmp_path):
    """Negative control: clearing both floors is a measurement, exit 0."""
    exit_code, _ = _run_paired_arm(
        monkeypatch, tmp_path,
        object_names=[f"the rail {n}" for n in range(6)]
        + [f"item {n}" for n in range(24)])  # 6/30 = 0.20 — exactly the floor

    assert exit_code == 0
    assert json.loads((tmp_path / "off.json").read_text())["share"] == 0.2


def test_the_paired_arm_refuses_a_corpus_that_is_not_the_frozen_one(
        monkeypatch, tmp_path):
    """G1: a bar is a threshold AND its corpus (#9076) — reuse the shipped gate.

    `capstone_measure.check_corpus` is what `capstone_measure.main` refuses with,
    so the probe imports and calls IT rather than re-deriving the comparison. The
    checkout's corpus is stubbed to a foreign hash, which is the only way to make
    the real gate speak: pre-fix, nothing compared the corpus at all, so the
    before-share shipped with no statement about the population it was taken on.
    """
    import pytest

    from tests.eval.write_path import corpus as corpus_module

    monkeypatch.setattr(corpus_module, "compute_fixtures_hash",
                        lambda: "sha256:" + "0" * 64)
    runs: list = []

    with pytest.raises(SystemExit) as caught:
        _run_paired_arm(monkeypatch, tmp_path, corpus_sha=None, runs=runs,
                        object_names=[f"the rail {n}" for n in range(7)]
                        + [f"item {n}" for n in range(23)])

    assert "REFUSED" in str(caught.value)
    assert "corpus" in str(caught.value)
    assert runs == [], (
        "the corpus must be checked BEFORE the model calls — a mismatched "
        "corpus makes the whole run a measurement of another population")
    assert not (tmp_path / "off.json").exists()


def test_the_min_before_share_is_read_out_of_the_frozen_record_not_typed(
        monkeypatch):
    """A floor typed into the probe is a floor nobody froze (also for 0.20)."""
    monkeypatch.setattr(e1_paired_arm, "read_preregistration",
                        lambda sha: _prereg_record(e1_min_before_share=0.55))
    monkeypatch.setattr(e1_paired_arm, "resolve_head", lambda: "0" * 40)

    assert e1_paired_arm.min_before_share() == 0.55, (
        "the share floor must MOVE with the record")


def test_the_min_before_share_refuses_an_unreadable_floor(monkeypatch, tmp_path):
    """Absent/unreadable is REFUSED, never "no floor stated" (#9513) — in `main()` too.

    #10610 review at `2d9e8608` (P3): this case drove the HELPER only, and the
    production path refused one helper earlier. `parse_bars` reads
    `e1_min_before_share` too (this PR added it), so `main()`'s map-backed
    `object_floor()` raised a bare `ValueError` for exactly the shapes the
    `REFUSED:` message below exists for — leaving that message dead code in
    `main()` and the run an operator saw indistinguishable from a crash. A pin
    whose stated subject is the production path has to RUN it.

    Non-vacuity: at the pre-fix head `main()` propagates `ValueError` out of
    `object_floor` (measured), so the `SystemExit` expectation on the run reds.
    """
    import pytest

    monkeypatch.setattr(e1_paired_arm, "read_preregistration",
                        lambda sha: _prereg_record(e1_min_before_share="0.20"))
    monkeypatch.setattr(e1_paired_arm, "resolve_head", lambda: "0" * 40)

    with pytest.raises(SystemExit) as caught:
        e1_paired_arm.min_before_share()

    assert "e1_min_before_share" in str(caught.value)

    # …and the RUN prints it, because a refusal a caller cannot read off the
    # invocation is not the refusal this probe documents.
    with pytest.raises(SystemExit) as refused:
        _run_paired_arm(monkeypatch, tmp_path, share_floor="0.20",
                        object_names=["the rail 1"])
    assert refused.value.code, refused.value
    assert "REFUSED" in str(refused.value), refused.value
    assert "e1_min_before_share" in str(refused.value), refused.value
    assert not (tmp_path / "off.json").exists(), (
        "a refused run writes no record — the record's presence is what tells a "
        "refusal apart from a failed session")


def test_the_paired_arm_still_reports_a_failed_session(monkeypatch, tmp_path):
    """A measured-but-failed arm is exit 1, distinct from every refusal."""
    exit_code, _ = _measured_arm(monkeypatch, tmp_path, ok=False)

    assert exit_code == 1
    assert (tmp_path / "off.json").exists(), (
        "the RECORD is what tells this exit 1 apart from a refusal's exit 1")


def test_the_paired_arm_refuses_a_keyless_backend_before_the_run(
        monkeypatch, tmp_path):
    """`backend_ready` is a precondition of this run, not of `capstone_measure`'s.

    A keyless capture extracts nothing (#3892), so the arm would measure the
    ABSENCE of a key while printing an OFF share. It must stop before the model
    call — and before any graph write — rather than one layer down.
    """
    import pytest

    runs: list = []
    with pytest.raises(SystemExit) as caught:
        _run_paired_arm(
            monkeypatch, tmp_path, runs=runs,
            object_names=["the deploy rail"],
            ready=(False, "DEEPSEEK_API_KEY is not set — a keyless capture "
                           "extracts nothing"))

    assert "DEEPSEEK_API_KEY" in str(caught.value)
    assert runs == []
    assert not (tmp_path / "off.json").exists()


def test_the_object_floor_is_read_out_of_the_frozen_record_not_typed(
        monkeypatch):
    """A floor typed into the probe is a floor nobody froze."""
    prereg_path = _REPO / "docs/evidence/7949-capstone/preregistration.json"
    prereg = json.loads(prereg_path.read_text())
    moved = json.loads(json.dumps(prereg))
    moved["denominator_floors"]["e1_objects"] = 42
    monkeypatch.setattr(e1_paired_arm, "read_preregistration", lambda sha: moved)
    monkeypatch.setattr(e1_paired_arm, "resolve_head", lambda: "0" * 40)

    assert e1_paired_arm.object_floor() == 42, (
        "the floor must MOVE with the record — a constant here would let a lane "
        "move E1's floor by editing the probe instead of the pre-registration")
    assert prereg["denominator_floors"]["e1_objects"] == 30, (
        "the frozen floor these cases apply is 30 Objects; if the record moved, "
        "re-measure rather than re-typing this assertion")


# ── e2a_value_units: the frozen denominator and the anchor direction ────────

def test_the_frozen_denominator_is_fourteen_read_off_the_gold(capsys):
    """§3.3's denominator, recomputed from the corpus — never typed here."""
    assert e2a_value_units.frozen_denominator() == 14

    assert e2a_value_units.main(["--graph", "unused", "--out", "unused",
                                 "--check-denominator"]) == 0
    assert "frozen value-bearing denominator: 14" in capsys.readouterr().out


def test_the_two_exclusions_are_what_takes_the_rule_from_16_to_14():
    """The exclusions are a registered judgement, so their EFFECT is pinned."""
    from tests.eval.write_path.corpus import session_ids

    kept = e2a_value_units.value_bearing_ids
    total_with_exclusions = 0
    total_without = 0
    for session_id in session_ids():
        gold = json.loads(e2a_value_units.gold_path(session_id).read_text())
        units = gold["planted_units"]
        total_with_exclusions += len(kept(units))
        total_without += len([
            unit["id"] for unit in units
            if any(ch.isdigit() for ch in (unit.get("verbatim_anchor") or ""))
        ])

    assert total_with_exclusions == 14
    assert total_without == 16


def test_an_anchor_bearing_point_is_what_the_strict_counter_observes():
    """THE DIRECTION PIN — the shipped predicate, over the shipped Point shape.

    `--inject-anchor` claims that adding a Point carrying the unit's
    `verbatim_anchor` makes the unit strictly survive. That claim is only worth
    measuring if the counter moves on the anchor and on nothing else, so both
    directions are asserted here against `grading.strict_survival_counts`: the
    same unit is NOT survived when the anchor is absent from the Points and IS
    survived when it is present with the provenance and EP update the unit
    requires.
    """
    from tests.eval.write_path import grading

    gold = {
        "salient_units": [{
            "id": "wp05_u_02",
            "verbatim_anchor": "40 rows",
            "survival": {"provenance_required": True, "ep_update_required": True},
        }],
    }
    without_anchor = [{"point_id": "p1", "content": "a batch of rows",
                       "provenance_present": True, "ep_updated": True}]
    with_anchor = [*without_anchor,
                   {"point_id": "SYNTH_1", "content": "40 rows",
                    "provenance_present": True, "ep_updated": True}]

    lost = grading.strict_survival_counts(gold, without_anchor, [])
    injected = grading.strict_survival_counts(gold, with_anchor, [])

    assert lost["survived_ids"] == []
    assert injected["survived_ids"] == ["wp05_u_02"]
    assert e2a_value_units.value_bearing_ids(
        [{"id": "wp05_u_02", "verbatim_anchor": "40 rows"}]) == ["wp05_u_02"]


def _graded(before_survived: int, after_survived: int, *,
            memory_points: int = 12, strict_total: int = 16):
    """A `grade` stub whose two readings differ ONLY by the injection.

    `memory_points` is the run's per-session CENSUS (`runner.snapshot_session`'s
    own snapshot size) — the population the number is scored over. 0 is the
    absent-graph reading the probe must refuse rather than score.

    `strict_all` is the STRICT reading over all planted units (the shipped
    `grading.strict_survival_counts` shape), and it differs between the two
    passes — as it does in a real run, where the injected anchors make units
    strictly survive. The two numbers are what tells the two blocks apart.
    """
    def grade(graph: str, *, inject_anchor: bool) -> dict:
        survived = after_survived if inject_anchor else before_survived
        row = {"memory_points": memory_points,
               "strict_all": f"{survived}/{strict_total}"}
        return {"graph": graph,
                "injected_anchor": inject_anchor,
                "value_units": 14,
                "value_survived": survived,
                "per_session": {
                    "wp01_quarry_debug": dict(row),
                    "wp05_retro_writeup": dict(row),
                }}
    return grade


def _run_e2a(monkeypatch, tmp_path, *, before: int = 1, after: int = 14,
             memory_points: int = 12, inject: bool = True,
             uri: str | None = "docker://:@127.0.0.1:1/tortoise_test") -> int:
    """Drive `main()` — the backend/population refusals and the falsifier."""
    if uri is None:
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_DB_URI", uri)
    monkeypatch.setattr(
        e2a_value_units, "grade",
        _graded(before, after, memory_points=memory_points))
    argv = ["--graph", "g", "--out", str(tmp_path / "e2a.json")]
    if inject:
        argv.append("--inject-anchor")
    return e2a_value_units.main(argv)


def test_the_e2a_probe_refuses_a_run_with_no_backend(monkeypatch, tmp_path,
                                                    capsys):
    """No URI = the EMBEDDED fallback = `0 of 14` measured over an empty graph.

    `resolve_db_path()` "raises nothing — falls through to the default on
    empty/whitespace env values", so a shell that forgot the export would score an
    empty embedded graph as a confident zero. Refused before any read.
    """
    exit_code = _run_e2a(monkeypatch, tmp_path, uri=None)

    assert exit_code == 2
    assert "TORTOISE_DB_URI is not set" in capsys.readouterr().out
    assert not (tmp_path / "e2a.json").exists()


def test_the_e2a_probe_refuses_an_absent_population(monkeypatch, tmp_path,
                                                    capsys):
    """Zero memory-level Points is `0 of 14` over NOTHING, not a regression.

    A graph name that was never written reads empty rather than raising, so this
    is the only thing that separates a mistyped/reaped graph from a pipeline that
    lost every unit — and the falsifier alone cannot: on an empty graph it
    recovers all 14 injected anchors and prints a GREEN pair beside the zero.
    """
    exit_code = _run_e2a(monkeypatch, tmp_path, before=0, after=14,
                         memory_points=0)

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "absent" in out.lower()
    assert "recovered" not in out, (
        "the falsifier's green line must not print over an unmeasured run")
    assert not (tmp_path / "e2a.json").exists()


def test_the_e2a_probe_refuses_an_absent_population_as_written(
        monkeypatch, tmp_path, capsys):
    """The same refusal on the plain path (no `--inject-anchor`)."""
    exit_code = _run_e2a(monkeypatch, tmp_path, before=0, inject=False,
                         memory_points=0)

    assert exit_code == 2
    assert "no memory-level Points" in capsys.readouterr().out
    assert not (tmp_path / "e2a.json").exists()


def test_the_e2a_probe_measures_a_populated_graph(monkeypatch, tmp_path,
                                                  capsys):
    """Negative control: a real population is a measurement, exit 0."""
    exit_code = _run_e2a(monkeypatch, tmp_path, before=1, after=14,
                         memory_points=12)

    assert exit_code == 0
    assert "recovered 13 of lost 13" in capsys.readouterr().out
    assert (tmp_path / "e2a.json").exists()


def test_the_falsifier_refuses_an_inert_counter(monkeypatch, tmp_path, capsys):
    """`--inject-anchor` moving nothing means the counter cannot see the anchor."""
    exit_code = _run_e2a(monkeypatch, tmp_path, before=1, after=1)

    assert exit_code == 2
    assert "inert" in capsys.readouterr().out


def test_the_falsifier_reports_the_units_it_recovered(monkeypatch, tmp_path,
                                                      capsys):
    exit_code = _run_e2a(monkeypatch, tmp_path, before=1, after=14)

    assert exit_code == 0
    record = json.loads((tmp_path / "e2a.json").read_text())
    assert record["as_written"]["value_survived"] == 1
    assert record["with_anchor"]["value_survived"] == 14
    assert "recovered 13 of lost 13" in capsys.readouterr().out


def test_a_clean_run_is_not_refused_by_the_falsifier(monkeypatch, tmp_path):
    """14/14 as-written leaves nothing to inject: the refusal must not misfire."""
    exit_code = _run_e2a(monkeypatch, tmp_path, before=14, after=14)

    assert exit_code == 0


def test_the_falsifier_labels_each_block_with_its_own_strict_reading(
        monkeypatch, tmp_path):
    """`with_anchor.strict_all` must be the INJECTED reading, not the input one.

    The record exists so both readings of the same graph sit in one artifact
    (`as_written` / `with_anchor`), which makes the label on each read as the
    whole claim. Pre-fix, `strict_all` was sourced from the PRE-injection count
    on both passes — so the `with_anchor` block reported the very number the
    injection was supposed to move, and a reader parsing it as "what strict
    survival became with the anchors injected" read the wrong condition. The
    pre-injection figure now keeps its own key, so neither reading is lost.

    This drives the REAL `grade()` (only its two I/O seams are stubbed), so the
    injection loop and the shipped `strict_survival_counts` both run: a gold of
    two units, one anchor-bearing, over Points holding neither anchor. Strict
    survival is 0 of 2 as written and 1 of 2 once the anchor is injected.
    """
    import tortoise.sdk as sdk_module
    from tests.eval.write_path import corpus, runner

    gold = {
        "planted_units": [
            {"id": "wp01_u_01", "verbatim_anchor": "40 rows"},
            {"id": "wp01_u_02", "verbatim_anchor": "the batch"},
        ],
        "salient_units": [
            {"id": "wp01_u_01", "verbatim_anchor": "40 rows",
             "survival": {"provenance_required": True,
                          "ep_update_required": True}},
            {"id": "wp01_u_02", "verbatim_anchor": "the batch",
             "survival": {"provenance_required": True,
                          "ep_update_required": True}},
        ],
    }
    gold_file = tmp_path / "wp01_quarry_debug.gold.json"
    gold_file.write_text(json.dumps(gold))
    monkeypatch.setattr(e2a_value_units, "gold_path", lambda sid: gold_file)
    monkeypatch.setattr(corpus, "session_ids", lambda: ["wp01_quarry_debug"])
    monkeypatch.setattr(sdk_module, "TortoiseSDK", lambda graph_name: object())
    monkeypatch.setattr(runner, "snapshot_session", lambda sdk, sid: {
        "points": [{"point_id": "p1", "content": "a batch of rows",
                    "provenance_present": True, "ep_updated": True}],
        "rephrase_edges": [],
    })

    injected = e2a_value_units.grade("g", inject_anchor=True)
    as_written = e2a_value_units.grade("g", inject_anchor=False)

    row = injected["per_session"]["wp01_quarry_debug"]
    assert row["strict_all"] == "1/2", (
        "the with_anchor block must carry the INJECTED strict reading; 0/2 is "
        "the number the injection moved and must not stand in for it")
    assert row["strict_all_as_written"] == "0/2", (
        "the pre-injection reading must stay legible under its own key")
    assert injected["value_survived"] == 1

    # The plain path observes ONE condition, so it must not grow a second label
    # — and its `strict_all` is its own number, not a carried-over default.
    plain = as_written["per_session"]["wp01_quarry_debug"]
    assert plain["strict_all"] == "0/2"
    assert "strict_all_as_written" not in plain, (
        "the as-written pass has nothing to disambiguate; a second label there "
        "would invite a parser to read them as two conditions")


# ── the probes' cites into the record they measure (#10525 round 11 P3) ──────

def test_no_probe_cites_the_record_by_absolute_line():
    """A `PRE-REGISTRATION.md:<line>` cite in a probe RE-STALES on the next insertion.

    #10525 review round 11 P3, MEASURED: `e1_paired_arm.py` cited
    `PRE-REGISTRATION.md:278-280` for E1's absolute refusal. This branch's own §0/§7 insertions
    pushed the quoted sentence to 289-290, where :278-280 hold §3.1's E4/E5 table rows — so a reader
    following the cite landed on a different criterion's threshold. Nothing redded: the probes are
    outside every ref pin (`_CITING_TEST_FILE`'s `path:N` surface and the #5095 continuation table
    both stop at `tests/`), which is why it drifted in the one direction nobody reads.

    A SECTION cite cannot re-stale (the tree grows, headings do not move), so the repair is the
    cite form — and this is the pin that keeps it that way. It is structural rather than an anchor
    table on purpose: an anchor table is a second list that goes stale itself, and the property
    wanted here is "no absolute line into the record", which is decidable without one.

    Non-vacuity: at the pre-fix head `e1_paired_arm.py` carried `PRE-REGISTRATION.md:278-280`, so
    this reds with that file and line named.
    """
    import re
    offenders: list[str] = []
    for path in sorted(_PROBES.glob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"PRE-REGISTRATION\.md:\d", line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, (
        "a probe cites the record by absolute line, which this branch has already moved once — "
        "cite the SECTION (`PRE-REGISTRATION.md` §3.2) instead: " + "; ".join(offenders)
    )


def test_the_section_the_e1_probe_cites_really_carries_the_sentence_it_quotes():
    """The other half: the §3.2 cite must land on the refusal `e1_paired_arm` quotes.

    A section cite that names the WRONG section is the same defect one level up (the reader lands
    somewhere plausible and concludes the rule is gone), and a section cite is cheap enough that
    nothing forces it to be right. So the sentence is re-derived from the record's own §3.2 body
    here, and the probe is checked for quoting it verbatim.

    Non-vacuity: at the pre-fix head the probe quoted the sentence but cited `:278-280` (inside
    §3.1), so the `§3.2` assertion below reds on it.
    """
    record = (_REPO / "docs/evidence/7949-capstone/PRE-REGISTRATION.md").read_text()
    start = record.index("### 3.2 E1")
    # normalised: the record is hard-wrapped, so the sentence spans three source lines and a
    # whitespace-sensitive comparison would red on a re-wrap rather than on a moved rule.
    body = " ".join(record[start:].split("\n### ", 1)[0].split())
    quote = ("if the **control arm's** share is `< 0.20`, the corpus cannot measure E1 "
             "(a share metric needs a non-trivial base) and the criterion is **REFUSED**")
    assert quote in body, (
        "§3.2 no longer carries the absolute refusal `e1_paired_arm` applies — re-derive the "
        "probe's floor against whatever section now states it"
    )
    doc = (Path(e1_paired_arm.__file__)).read_text()
    assert "§3.2" in doc, (
        "the E1 probe must cite the SECTION it reads its floor from; `PRE-REGISTRATION.md` §3.2 "
        "is where the refusal lives"
    )
    assert " ".join(quote.split()) in " ".join(doc.split()), (
        "the probe's quoted refusal must be the one §3.2 states, not a paraphrase of it"
    )


def test_the_arm_drivers_truthiness_copy_is_the_repos_contract():
    """The mirrored vocabulary in the PRODUCER must be `tortoise.env_truthy`'s.

    Review cycle 4 (P3), measured not asserted: `off_arm_readback`'s refusal now
    runs on `_is_truthy`, and widening the repo's `TRUTHY` by `"y"` left the
    scorer's pinned mirror RED but this file's copy GREEN — the driver then
    accepted `TORTOISE_VALUE_GATE=y` as the OFF arm (`_is_truthy("y")` False in
    the copy, `is_truthy("y")` True in the pipeline). For
    `TORTOISE_S2_DECISION_SEAM` that is a billable call inside an arm this file
    declares OFF, which is the exact concern `DEFAULT_ON_KEYS`' comment raises.

    `tests/test_7949_capstone_measure.py::test_the_arm_vocabulary_is_the_repos_
    truthiness_contract` holds the SCORER's copy to the contract; this holds the
    DRIVER's, because a second unpinned mirror is how the two directions of one
    comparison drift apart.
    """
    from tortoise.env_truthy import FALSY, TRUTHY, is_truthy

    assert set(e1_paired_arm._TRUTHY) == set(TRUTHY), (
        e1_paired_arm._TRUTHY, sorted(TRUTHY))
    for sample in [*sorted(TRUTHY), *sorted(FALSY), "off", "OFF", " off ", "",
                   "  ", None, "disabled", "true", "TRUE", "1", "0", 1, 0]:
        assert e1_paired_arm._is_truthy(sample) == is_truthy(sample), (
            f"the arm driver reads {sample!r} as truthy="
            f"{e1_paired_arm._is_truthy(sample)} but the repo's contract says "
            f"{is_truthy(sample)}")
