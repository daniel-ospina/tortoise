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
  OFF label — refuses (exit **2**, writing no record) when the run's `:Object`
  denominator is under E1's frozen `denominator_floors.e1_objects`, reads that
  floor out of the pre-registration rather than typing it, and refuses before
  any model call when the backend cannot extract.
* `e2a_value_units` refuses when injecting every lost unit's verbatim anchor
  moves the survival counter by nothing (an inert counter), and its
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

def _fake_census(object_names: list[str]):
    def census(uri: str) -> dict:
        assert "/" in uri, f"a graph URI was expected, got {uri!r}"
        return {"object_names": list(object_names)}
    return census


def _run_gate_drops(monkeypatch, tmp_path, *, object_names: list[str],
                    verdict=None) -> int:
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/tortoise_test")
    monkeypatch.setattr(e1_gate_drops, "census", _fake_census(object_names))
    if verdict is not None:
        import tortoise.value_gate as value_gate
        monkeypatch.setattr(value_gate, "mechanical_verdict", verdict)
    return e1_gate_drops.main(["--graph", "g", "--out", str(tmp_path / "o.json")])


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
    """Zero Object names is a missing denominator, never "0 of 0 drops passed"."""
    exit_code = _run_gate_drops(monkeypatch, tmp_path, object_names=[])

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "no Object names" in out
    assert "objects=0" in out


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
    """The record must NAME every switch, so a reader sees what was consulted."""
    for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
        monkeypatch.delenv(key, raising=False)

    arm = e1_paired_arm.off_arm_readback()

    assert set(arm) == {*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY}
    assert set(arm.values()) == {None}


def test_the_off_arm_accepts_an_explicit_zero(monkeypatch):
    """`TORTOISE_VET=0` IS the OFF arm — refusing it would refuse the run it wants."""
    for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
        monkeypatch.setenv(key, "0")

    arm = e1_paired_arm.off_arm_readback()

    assert set(arm.values()) == {"0"}


# ── e1_paired_arm: the before-share is refused when E1's own record refuses it ─

def _fake_arm_census(object_names: list[str]):
    def census(uri: str) -> dict:
        assert "/" in uri, f"a graph URI was expected, got {uri!r}"
        return {"object_names": list(object_names), "label_counts": {}}
    return census


def _run_paired_arm(monkeypatch, tmp_path, *, object_names: list[str],
                    ok: bool = True, floor: int = 30,
                    ready: tuple[bool, str] = (True, "ok")) -> tuple[int, list]:
    """Drive `main()` itself — the run, the record and the refusals.

    Pre-fix, no case in this module called `e1_paired_arm.main` at all, so the
    DB refusal, the run, the record shape, the failed-session exit and the
    denominator refusal below all shipped untested.
    """
    for key in (*e1_paired_arm.ARM_KEYS, e1_paired_arm.SEAM_KEY):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/tortoise_test")
    monkeypatch.setattr(e1_paired_arm, "backend_ready", lambda: ready)
    monkeypatch.setattr(e1_paired_arm, "object_floor", lambda: floor)
    monkeypatch.setattr(e1_paired_arm, "census", _fake_arm_census(object_names))
    runs: list[tuple[str, list[str]]] = []

    def fake_run(graph: str, sessions: list[str]) -> list[dict]:
        runs.append((graph, sessions))
        return [{"session_id": "wp01_quarry_debug", "ok": ok,
                 "extracted": True, "elapsed_s": 0.5}]

    monkeypatch.setattr(e1_paired_arm, "run_corpus", fake_run)
    exit_code = e1_paired_arm.main(
        ["--graph", "g", "--out", str(tmp_path / "off.json")])
    return exit_code, runs


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
    exit_code, runs = _run_paired_arm(
        monkeypatch, tmp_path,
        object_names=["the deploy rail", *[f"item {n}" for n in range(29)]])

    assert exit_code == 0
    assert len(runs) == 1, "one pass over the pre-registered corpus, not two"
    record = json.loads((tmp_path / "off.json").read_text())
    assert record["objects"] == 30
    assert record["bare_definite"] == 1
    assert record["share"] == 1 / 30
    assert record["object_floor"] == 30, (
        "the record must name the floor that gated it, so a reader can see the "
        "number was produced above one")


def test_the_paired_arm_still_reports_a_failed_session(monkeypatch, tmp_path):
    """A measured-but-failed arm is exit 1, distinct from the floor refusal."""
    exit_code, _ = _run_paired_arm(
        monkeypatch, tmp_path,
        object_names=["the deploy rail", *[f"item {n}" for n in range(29)]],
        ok=False)

    assert exit_code == 1
    assert (tmp_path / "off.json").exists()


def test_the_paired_arm_refuses_a_keyless_backend_before_the_run(
        monkeypatch, tmp_path):
    """`backend_ready` is a precondition of this run, not of `capstone_measure`'s.

    A keyless capture extracts nothing (#3892), so the arm would measure the
    ABSENCE of a key while printing an OFF share. It must stop before the model
    call — and before any graph write — rather than one layer down.
    """
    import pytest

    with pytest.raises(SystemExit) as caught:
        _run_paired_arm(
            monkeypatch, tmp_path,
            object_names=["the deploy rail"],
            ready=(False, "DEEPSEEK_API_KEY is not set — a keyless capture "
                           "extracts nothing"))

    assert "DEEPSEEK_API_KEY" in str(caught.value)
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


def _graded(before_survived: int, after_survived: int):
    """A `grade` stub whose two readings differ ONLY by the injection."""
    def grade(graph: str, *, inject_anchor: bool) -> dict:
        return {"graph": graph,
                "injected_anchor": inject_anchor,
                "value_units": 14,
                "value_survived": after_survived if inject_anchor
                else before_survived}
    return grade


def test_the_falsifier_refuses_an_inert_counter(monkeypatch, tmp_path, capsys):
    """`--inject-anchor` moving nothing means the counter cannot see the anchor."""
    monkeypatch.setattr(e2a_value_units, "grade", _graded(1, 1))

    exit_code = e2a_value_units.main(
        ["--graph", "g", "--out", str(tmp_path / "e2a.json"), "--inject-anchor"])

    assert exit_code == 2
    assert "inert" in capsys.readouterr().out


def test_the_falsifier_reports_the_units_it_recovered(monkeypatch, tmp_path,
                                                      capsys):
    monkeypatch.setattr(e2a_value_units, "grade", _graded(1, 14))

    exit_code = e2a_value_units.main(
        ["--graph", "g", "--out", str(tmp_path / "e2a.json"), "--inject-anchor"])

    assert exit_code == 0
    record = json.loads((tmp_path / "e2a.json").read_text())
    assert record["as_written"]["value_survived"] == 1
    assert record["with_anchor"]["value_survived"] == 14
    assert "recovered 13 of lost 13" in capsys.readouterr().out


def test_a_clean_run_is_not_refused_by_the_falsifier(monkeypatch, tmp_path):
    """14/14 as-written leaves nothing to inject: the refusal must not misfire."""
    monkeypatch.setattr(e2a_value_units, "grade", _graded(14, 14))

    exit_code = e2a_value_units.main(
        ["--graph", "g", "--out", str(tmp_path / "e2a.json"), "--inject-anchor"])

    assert exit_code == 0
