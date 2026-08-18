"""Unit tests for tools/kappa.py + tools/min_signal.py — epic #909 slice 1a (#945).

Covers: κ math on hand-computed fixtures, nothing-verdict agreement, per-class
agreement, the DE2E-1 gate decision semantics (green / middle-band not-green /
rubric revision), the degenerate-empty minimum-signal assertion (DE2E-1 neg
(b)), and the gate report shape.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools import kappa as kp
from tools import min_signal
from tools.judge_harness import Label, LabeledWindow


def L(idx: int, cls: str) -> Label:
    return Label(edu_index=idx, class_=cls)


def window(labels: list[Label], *, window_id: str = "w1", judge: str = "a",
           window_type: str = "design", n_edus: int | None = None) -> LabeledWindow:
    return LabeledWindow(
        window_id=window_id, window_type=window_type, judge=judge,
        n_edus=n_edus if n_edus is not None else len(labels), labels=labels,
    )


# ── Cohen's κ: hand-computed fixtures ───────────────────────────────────────

def test_kappa_hand_computed_fixture():
    """4-EDU fixture, hand-computed: po=0.5, pe=0.25, κ=(0.5-0.25)/0.75=1/3.

    Judge A: [decision, event, claim, none]
    Judge B: [decision, claim,  claim, event]
    """
    a = [L(0, "decision"), L(1, "event"), L(2, "claim"), L(3, "none")]
    b = [L(0, "decision"), L(1, "claim"), L(2, "claim"), L(3, "event")]
    assert kp.kappa(a, b) == pytest.approx(1 / 3)


def test_kappa_perfect_agreement():
    a = [L(0, "decision"), L(1, "event"), L(2, "none")]
    assert kp.kappa(a, [L(0, "decision"), L(1, "event"), L(2, "none")]) == 1.0


def test_kappa_no_agreement_beyond_chance():
    """po = pe = 0.5 → κ = 0.0 (agreement is exactly chance)."""
    a = [L(0, "decision"), L(1, "decision"), L(2, "event"), L(3, "event")]
    b = [L(0, "decision"), L(1, "event"), L(2, "decision"), L(3, "event")]
    assert kp.kappa(a, b) == 0.0


def test_kappa_single_category_convention():
    """Both judges use one category: identical verdicts → κ = 1.0 (never NaN)."""
    a = [L(0, "none"), L(1, "none")]
    b = [L(0, "none"), L(1, "none")]
    assert kp.kappa(a, b) == 1.0


def test_compare_incomplete_labeling_blocks_green():
    """A judge that labels a subset of EDUs with perfect agreement must NOT
    produce GREEN (review P2, PR #975 — incomplete labeling is gating)."""
    a = {"window_type": "operational", "incomplete": False, "labels": [
        {"edu_index": 0, "class": "event"}, {"edu_index": 1, "class": "event"}]}
    b = {"window_type": "operational", "incomplete": True, "labels": [
        {"edu_index": 0, "class": "event"}]}
    r = kp.compare(a, b)
    assert r["gate"]["verdict"] == "NOT_GREEN"
    assert "incomplete" in r["gate"]["reason"]


def test_kappa_single_category_disagreeing_branch():
    """Agreement exactly at chance with asymmetric margins → κ = 0.0.

    (Review P2, PR #975: the pe == 1.0 NaN-guard branch is mathematically
    unreachable — pe == 1.0 forces po == 1.0 — the guard in kappa() is
    defensive.)"""
    a = [L(0, "none"), L(1, "none")]
    b = [L(0, "none"), L(1, "claim")]
    k = kp.kappa(a, b)
    assert k == 0.0
    assert k == k  # not NaN


def test_kappa_no_overlap_is_none():
    assert kp.kappa([L(0, "claim"), L(1, "claim")], [L(2, "claim")]) is None


def test_kappa_uses_intersection_only():
    """EDUs labeled by only one judge do not participate."""
    a = [L(0, "decision"), L(1, "decision"), L(2, "event")]
    b = [L(0, "decision"), L(1, "event")]
    # Over the intersection {0, 1}: po=0.5, pe=0.5 → κ = 0.0
    assert kp.kappa(a, b) == 0.0


def test_kappa_middle_band_fixture_computed():
    """2000-EDU fixture with equal 0.7/0.3 margins: po=0.811, pe=0.58, κ=0.55.

    both d: 1211, both e: 411, A=d/B=e: 189, A=e/B=d: 189.
    """
    a_labels, b_labels = [], []
    for i in range(2000):
        if i < 1211:
            a_labels.append(L(i, "decision")); b_labels.append(L(i, "decision"))
        elif i < 1622:
            a_labels.append(L(i, "event")); b_labels.append(L(i, "event"))
        elif i < 1811:
            a_labels.append(L(i, "decision")); b_labels.append(L(i, "event"))
        else:
            a_labels.append(L(i, "event")); b_labels.append(L(i, "decision"))
    k = kp.kappa(a_labels, b_labels)
    assert k == pytest.approx(0.55, abs=1e-9)
    assert 0.50 <= k < 0.60  # the middle band


# ── Shared po/pe core (cohens_kappa) — single source for both gates ──────────

def test_cohens_kappa_shared_core_hand_computed():
    """The shared core reproduces the pair-label runner's hand-computed
    matrices — the same po/pe math, vocab-agnostic (a/c here stand in for
    any category vocabulary)."""
    assert kp.cohens_kappa(["a", "b", "c", "a"], ["a", "b", "c", "a"]) == pytest.approx(1.0)
    # A=[a,a,c,c], B=[c,c,a,a]: po=0, pe=0.5 → κ = -1.0
    assert kp.cohens_kappa(["a", "a", "c", "c"], ["c", "c", "a", "a"]) == pytest.approx(-1.0)
    # A=[a,a,c,c], B=[a,c,a,c]: po=0.5, pe=0.5 → κ = 0.0
    assert kp.cohens_kappa(["a", "a", "c", "c"], ["a", "c", "a", "c"]) == pytest.approx(0.0)
    # A=[a,a,c], B=[a,c,c]: po=2/3; pe=(2/3·1/3)+(1/3·2/3)=4/9 → κ = 0.4
    assert kp.cohens_kappa(["a", "a", "c"], ["a", "c", "c"]) == pytest.approx(0.4)
    # Single-category judges: pe == 1.0 → κ = 1.0, never NaN
    assert kp.cohens_kappa(["a", "a", "a"], ["a", "a", "a"]) == pytest.approx(1.0)


def test_cohens_kappa_rejects_empty_and_misaligned():
    """The κ formula is undefined over 0 units; misalignment would silently
    compute garbage — both must raise."""
    with pytest.raises(ValueError):
        kp.cohens_kappa([], [])
    with pytest.raises(ValueError):
        kp.cohens_kappa(["a"], ["a", "b"])


def test_kappa_delegates_to_shared_core():
    """tools.kappa.kappa() (intersection window contract) aligns the common
    EDUs' classes and delegates the MATH to cohens_kappa — same result."""
    a = [L(0, "decision"), L(1, "event"), L(2, "claim"), L(3, "none")]
    b = [L(0, "decision"), L(1, "claim"), L(2, "claim"), L(3, "event")]
    assert kp.kappa(a, b) == kp.cohens_kappa(
        ["decision", "event", "claim", "none"],
        ["decision", "claim", "claim", "event"],
    ) == pytest.approx(1 / 3)


# ── Nothing-verdict agreement ───────────────────────────────────────────────

def test_nothing_agreement_jaccard():
    a = [L(0, "none"), L(3, "none")]
    b = [L(0, "none"), L(3, "none"), L(5, "none")]
    agreement, counts = kp.nothing_agreement(a, b)
    assert agreement == pytest.approx(2 / 3)
    assert counts == {"both": 2, "a_only": 0, "b_only": 1}


def test_nothing_agreement_same_edus():
    a = [L(1, "none"), L(2, "claim")]
    b = [L(1, "none"), L(2, "event")]
    agreement, counts = kp.nothing_agreement(a, b)
    assert agreement == 1.0
    assert counts == {"both": 1, "a_only": 0, "b_only": 0}


def test_nothing_agreement_both_empty_is_vacuous():
    a = [L(0, "decision")]
    b = [L(0, "event")]
    agreement, counts = kp.nothing_agreement(a, b)
    assert agreement == 1.0
    assert counts == {"both": 0, "a_only": 0, "b_only": 0}


def test_nothing_agreement_disjoint():
    agreement, _ = kp.nothing_agreement([L(1, "none")], [L(2, "none")])
    assert agreement == 0.0


# ── Per-class agreement (recorded, no v1 threshold — DE2E-1) ────────────────

def test_per_class_agreement_intersection_base():
    """Totals are restricted to the SHARED EDU set (same basis as kappa)."""
    a = [L(0, "event"), L(1, "event")]
    b = [L(0, "event")]
    result = kp.per_class_agreement(a, b)
    assert result["event"] == 1.0  # perfect agreement on every shared EDU


def test_per_class_agreement_specific_agreement():
    """idx0: d/d · idx1: e/d · idx2: n/n · idx3: d/e

    d: 2·1/(2+2)=0.5 · e: 2·0/(1+1)=0.0 · n: 2·1/(1+1)=1.0
    """
    a = [L(0, "decision"), L(1, "event"), L(2, "none"), L(3, "decision")]
    b = [L(0, "decision"), L(1, "decision"), L(2, "none"), L(3, "event")]
    result = kp.per_class_agreement(a, b)
    assert result["decision"] == pytest.approx(0.5)
    assert result["event"] == 0.0
    assert result["none"] == 1.0
    assert "process" not in result  # absent class → not reported


# ── Gate decision semantics (plan DE2E-1) ───────────────────────────────────

def test_gate_green():
    decision = kp.gate_decision(0.80, 1.0, min_signal_passed=True)
    assert decision.verdict == "GREEN"


def test_gate_revise_below_050():
    """κ < 0.50 → REVISE (rubric revision) — regardless of other signals."""
    decision = kp.gate_decision(0.40, 1.0, min_signal_passed=True)
    assert decision.verdict == "REVISE"
    assert "rubric" in decision.reason.lower()


def test_gate_middle_band_not_green():
    """0.50 ≤ κ < 0.60 → NOT green — expand labeling (DE2E-1 assertion)."""
    decision = kp.gate_decision(0.55, 1.0, min_signal_passed=True)
    assert decision.verdict == "NOT_GREEN"
    assert "expand labeling" in decision.reason


def test_gate_middle_band_not_green_at_050():
    assert kp.gate_decision(0.50, 1.0).verdict == "NOT_GREEN"


def test_gate_green_at_060():
    assert kp.gate_decision(0.60, 1.0, min_signal_passed=True).verdict == "GREEN"


def test_gate_nothing_verdict_disagreement_blocks_green():
    decision = kp.gate_decision(0.70, 0.5, min_signal_passed=True)
    assert decision.verdict == "NOT_GREEN"
    assert "nothing-verdict" in decision.reason


def test_gate_min_signal_failure_blocks_green():
    """DE2E-1 neg (b): operational window that should emit events → not green."""
    decision = kp.gate_decision(0.70, 1.0, min_signal_passed=False)
    assert decision.verdict == "NOT_GREEN"
    assert "minimum-signal" in decision.reason


def test_gate_null_kappa_not_green():
    decision = kp.gate_decision(None, 1.0)
    assert decision.verdict == "NOT_GREEN"


# ── Minimum-signal assertion (tools/min_signal.py) ──────────────────────────

def test_min_signal_operational_floor_default_one():
    result = min_signal.min_signal_check([L(0, "claim")], "operational")
    assert result.required == 1 and result.emitted == 0 and result.passed is False
    result = min_signal.min_signal_check([L(0, "event")], "operational")
    assert result.passed is True


def test_min_signal_operational_configurable_floor():
    labels = [L(0, "event"), L(1, "event"), L(2, "claim")]
    assert min_signal.min_signal_check(labels, "operational", min_events=2).passed is True
    assert min_signal.min_signal_check(labels, "operational", min_events=3).passed is False


def test_min_signal_design_has_no_floor():
    result = min_signal.min_signal_check([L(0, "claim")], "design")
    assert result.required == 0 and result.passed is True


def test_min_signal_unknown_window_type():
    with pytest.raises(ValueError):
        min_signal.min_signal_check([], "strategy")


def test_min_signal_accepts_dict_labels():
    labels = [{"edu_index": 0, "class": "event"}]
    assert min_signal.min_signal_check(labels, "operational").passed is True


# ── Full gate report (compare) ──────────────────────────────────────────────

def test_compare_report_shape():
    a = window([L(0, "decision"), L(1, "event")], judge="owner")
    b = window([L(0, "decision"), L(1, "claim")], judge="frontier")
    report = kp.compare(a, b)
    assert set(report) == {
        "window_a", "window_b", "n_compared", "kappa", "nothing_agreement",
        "nothing", "per_class_agreement", "min_signal", "gate",
    }
    assert report["n_compared"] == 2
    # po=0.5, pe=0.25 → κ = (0.5-0.25)/0.75 = 1/3
    assert report["kappa"] == pytest.approx(1 / 3)
    assert report["gate"]["verdict"] in ("GREEN", "NOT_GREEN", "REVISE")
    # Window type is inferred from the windows (design, floor 0) → evaluated.
    assert report["min_signal"]["a"]["passed"] is True
    assert report["min_signal"]["b"]["passed"] is True


def test_compare_operational_min_signal_fails_gate():
    """DE2E-1 neg (b): both judges agree on nothing → window #2 not green.

    Both judges label the same EDUs "none"/"claim" (zero events): κ = 1.0
    and nothing-verdict agreement = 1.0, but the operational minimum-signal
    assertion fails → NOT_GREEN (the gate is blocked by the degenerate-empty
    guard, not by agreement).
    """
    a = window([L(0, "none"), L(1, "claim")], window_type="operational", judge="owner")
    b = window([L(0, "none"), L(1, "claim")], window_type="operational", judge="frontier")
    report = kp.compare(a, b, window_type="operational")
    assert report["kappa"] == 1.0
    assert report["min_signal"]["a"]["passed"] is False
    assert report["min_signal"]["b"]["passed"] is False
    assert report["gate"]["verdict"] == "NOT_GREEN"
    assert "minimum-signal" in report["gate"]["reason"]


def test_compare_operational_with_events_is_green():
    a = window([L(0, "event"), L(1, "decision")], window_type="operational", judge="owner")
    b = window([L(0, "event"), L(1, "decision")], window_type="operational", judge="frontier")
    report = kp.compare(a, b, window_type="operational")
    assert report["min_signal"]["a"]["passed"] is True
    assert report["kappa"] == 1.0
    assert report["gate"]["verdict"] == "GREEN"


def test_compare_operational_inferred_from_windows():
    """window_type is read from the labeled windows when not passed."""
    a = window([L(0, "event"), L(1, "decision")], window_type="operational", judge="owner")
    b = window([L(0, "event"), L(1, "decision")], window_type="operational", judge="frontier")
    report = kp.compare(a, b)  # no explicit window_type
    assert report["min_signal"]["a"]["passed"] is True
    assert report["gate"]["verdict"] == "GREEN"


def test_compare_operational_one_judge_sees_events():
    """DE2E-1 neg (b) fails only when BOTH judges agree on nothing — a
    judge-specific miss surfaces through κ, not min-signal."""
    a = window([L(0, "event"), L(1, "claim")], window_type="operational", judge="owner")
    b = window([L(0, "none"), L(1, "claim")], window_type="operational", judge="frontier")
    report = kp.compare(a, b, window_type="operational")
    assert report["min_signal"]["a"]["passed"] is True
    assert report["min_signal"]["b"]["passed"] is False
    # Overall min-signal passes (the window DID emit events); κ = 1/3 < 0.50
    # → REVISE on agreement, not on min-signal.
    assert "minimum-signal" not in report["gate"]["reason"]
    assert report["gate"]["verdict"] == "REVISE"


def test_compare_fail_closed_without_window_type():
    """GREEN is never emitted when the min-signal assertion was not evaluated."""
    raw = {
        "window_id": "w", "judge": "a", "n_edus": 2, "degenerate": False,
        "incomplete": False,  # no window_type key
        "labels": [{"edu_index": 0, "class": "decision"},
                   {"edu_index": 1, "class": "decision"}],
    }
    report = kp.compare(raw, dict(raw))
    assert report["kappa"] == 1.0
    assert report["min_signal"] is None
    assert report["gate"]["verdict"] == "NOT_GREEN"
    assert "minimum-signal" in report["gate"]["reason"]


def test_compare_window_type_conflict():
    a = {"window_id": "a", "window_type": "design", "labels": []}
    b = {"window_id": "b", "window_type": "operational", "labels": []}
    with pytest.raises(kp.KappaError, match="disagree on window_type"):
        kp.compare(a, b)


def test_compare_design_window_no_event_floor():
    a = window([L(0, "claim")], window_type="design")
    b = window([L(0, "event")], window_type="design")
    report = kp.compare(a, b, window_type="design")
    assert report["min_signal"]["a"]["passed"] is True
    assert report["min_signal"]["b"]["passed"] is True


def test_compare_accepts_harness_json_dicts():
    """compare() must consume the raw judge_harness output (no dataclasses)."""
    raw_a = {
        "window_id": "w1", "window_type": "design", "judge": "owner",
        "n_edus": 2, "degenerate": False, "incomplete": False,
        "labels": [
            {"edu_index": 0, "class": "decision", "kind": None,
             "atomicity": True, "source_ref": None, "relations": []},
            {"edu_index": 1, "class": "claim", "kind": None,
             "atomicity": True, "source_ref": None,
             "relations": [{"type": "IMPL", "source": 0, "target": 1,
                            "bias": None}]},
        ],
    }
    raw_b = json.loads(json.dumps(raw_a).replace('"claim"', '"event"', 1))
    report = kp.compare(raw_a, raw_b)
    assert report["n_compared"] == 2
    # po=0.5, pe=0.25 → κ = 1/3 < 0.50 → REVISE (rubric revision)
    assert report["kappa"] == pytest.approx(1 / 3)
    assert report["gate"]["verdict"] == "REVISE"


# ── CLI ─────────────────────────────────────────────────────────────────────

def _write_window(tmp_path, name: str, window_type: str, labels: list[dict],
                  judge: str) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({
        "window_id": "w2", "window_type": window_type, "judge": judge,
        "n_edus": len(labels), "degenerate": False, "incomplete": False,
        "labels": labels,
    }))
    return path


def test_kappa_cli_report_and_green_exit(tmp_path, capsys):
    labels = [{"edu_index": 0, "class": "event", "kind": None, "atomicity": True,
               "source_ref": None, "relations": []},
              {"edu_index": 1, "class": "decision", "kind": None,
               "atomicity": True, "source_ref": None, "relations": []}]
    a = _write_window(tmp_path, "a.json", "operational", labels, "owner")
    b = _write_window(tmp_path, "b.json", "operational", labels, "frontier")
    code = kp.main(["--judge-a", str(a), "--judge-b", str(b),
                    "--window-type", "operational"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["kappa"] == 1.0
    assert report["gate"]["verdict"] == "GREEN"


def test_kappa_cli_strict_exit_on_not_green(tmp_path, capsys):
    a = _write_window(tmp_path, "a.json", "design",
                      [{"edu_index": 0, "class": "decision", "kind": None,
                        "atomicity": True, "source_ref": None, "relations": []}],
                      "owner")
    b = _write_window(tmp_path, "b.json", "design",
                      [{"edu_index": 0, "class": "event", "kind": None,
                        "atomicity": True, "source_ref": None, "relations": []}],
                      "frontier")
    # Default: computation succeeds → exit 0 (verdict is a documented decision).
    assert kp.main(["--judge-a", str(a), "--judge-b", str(b)]) == 0
    # --strict: NOT_GREEN/REVISE → exit 2 (CI enforcement).
    assert kp.main(["--judge-a", str(a), "--judge-b", str(b), "--strict"]) == 2


def test_kappa_cli_malformed_labels_clean_error(tmp_path, capsys):
    """Malformed label JSON → clean 'kappa: error' + exit 1 (no traceback)."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "window_id": "w", "window_type": "design", "labels":
        [{"edu_index": 0}],  # missing 'class'
    }))
    good = _write_window(tmp_path, "good.json", "design",
                         [{"edu_index": 0, "class": "claim", "kind": None,
                           "atomicity": True, "source_ref": None,
                           "relations": []}], "owner")
    code = kp.main(["--judge-a", str(bad), "--judge-b", str(good)])
    assert code == 1
    assert "kappa: error:" in capsys.readouterr().err


def test_kappa_cli_duplicate_edu_rejected(tmp_path, capsys):
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({
        "window_id": "w", "window_type": "design", "labels": [
            {"edu_index": 0, "class": "decision"},
            {"edu_index": 0, "class": "event"},
        ],
    }))
    good = _write_window(tmp_path, "good.json", "design",
                         [{"edu_index": 0, "class": "claim", "kind": None,
                           "atomicity": True, "source_ref": None,
                           "relations": []}], "owner")
    assert kp.main(["--judge-a", str(dup), "--judge-b", str(good)]) == 1


def test_min_signal_cli(tmp_path, capsys):
    path = _write_window(tmp_path, "w.json", "operational",
                         [{"edu_index": 0, "class": "claim", "kind": None,
                           "atomicity": True, "source_ref": None,
                           "relations": []}], "owner")
    # Operational window with zero events → assertion FAILED → exit 2.
    assert min_signal.main(["--labels", str(path), "--window-type",
                            "operational"]) == 2
    assert "passed" in capsys.readouterr().out
    # Design windows have no floor → exit 0.
    assert min_signal.main(["--labels", str(path), "--window-type", "design"]) == 0
    # Operational error (missing file) → exit 1.
    assert min_signal.main(["--labels", str(tmp_path / "nope.json"),
                            "--window-type", "operational"]) == 1
