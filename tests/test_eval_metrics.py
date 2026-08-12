"""Eval measurement core tests (epic #909 slice 8a, #960).

Covers the plan §6.3 contract + verification checklist:
- per-class P/R/F1 on seeded fixtures, NEVER blended (DE2E-3)
- the per-class window-N rule (N < 12 → skip + flag; N = 12 boundary)
- layer-correct / atomicity / citation / kind / entity / empty-rate / ECE /
  mitigation-recall / process-routing / min-signal / R1∧R3 conjunction
- kappa reuse (tools/kappa.py — DE2E-1 agreement math, hand-computed; the
  SAME function object, not a copy)
- thresholds.yaml loads + validates (A1-A22 + R8 rows + band semantics +
  reconciliation notes, W-6) and its band rows drive pass/fail/watch
  verdicts on a report (report → thresholds coupling)
- the real gold seeds load and feed compute_metrics
  (tests/gold/0323_excerpt.json, tests/gold_standard.json,
  tests/eval_results_v2.json)

Pure unit tests: no DB, no network, no model calls.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.eval import metrics as eval_metrics  # noqa: E402
from tests.eval.metrics import (  # noqa: E402
    BAND_N_ACCEPT,
    DECISIONS_FP_BAND,
    compute_metrics,
    kappa,
)
from tests.eval.types import Label, MetricsReport, RelationLabel, Window  # noqa: E402
from tools import kappa as tools_kappa  # noqa: E402


# ── Fixture helpers ─────────────────────────────────────────────────────────

# Distinctive per-INDEX content phrases: the EDU text is the same regardless of
# who labels it (gold/pred), so a misclassification at the same index still
# matches (same text), while a shifted index resolves by text in pass 2.
_CONTENT = [
    "we decided to ship alpha",
    "we fixed the beta bug",
    "the gamma costs five dollars",
    "we will handle delta now",
    "epsilon is small talk filler",
]


def _label(
    edu_index: int,
    class_: str,
    kind: str | None = None,
    atomicity: bool = True,
    source_ref: str | None = None,
    relations: list[RelationLabel] | None = None,
) -> Label:
    return Label(
        edu_index,
        class_,
        kind=kind,
        atomicity=atomicity,
        source_ref=source_ref,
        relations=relations or [],
    )


def _window(
    window_id: str,
    gold_spec: list[tuple],
    pred_spec: list[tuple] | None = None,
    *,
    session_id: str = "s1",
    texts: dict[int, str] | None = None,
) -> tuple[Window, Window]:
    """Build a (gold, pred) Window pair sharing one transcript.

    Spec entries: (edu_index, class_, **label_kwargs). Edu texts are per-INDEX
    (identical for gold and pred at the same EDU); pass `texts` to override
    (used by the shifted-index tests).
    """

    def build(spec: list[tuple] | None) -> list[Label] | None:
        if spec is None:
            return None
        labels = []
        for item in spec:
            idx, cls = item[0], item[1]
            kwargs = item[2] if len(item) > 2 else {}
            labels.append(Label(idx, cls, **kwargs))
        return labels

    gold_labels = build(gold_spec)
    pred_labels = build(pred_spec)
    if texts is None:
        all_idx = sorted(
            {l.edu_index for l in (gold_labels or [])}
            | {l.edu_index for l in (pred_labels or [])}
        )
        texts = {i: f"{_CONTENT[i % len(_CONTENT)]} in {window_id}" for i in all_idx}
    edus = [texts[i] for i in range(max(texts, default=-1) + 1)]
    return (
        Window(session_id, window_id, edus, gold_labels),
        Window(session_id, window_id, edus, pred_labels),
    )


def _gen(count: int, gold_spec_fn, pred_spec_fn, prefix: str = "w"):
    """Generate `count` paired windows from per-index spec functions."""
    gold_wins, pred_wins = [], []
    for i in range(count):
        g, p = _window(f"{prefix}{i}", gold_spec_fn(i), pred_spec_fn(i))
        gold_wins.append(g)
        pred_wins.append(p)
    return gold_wins, pred_wins


# ── Per-class P/R/F1 (DE2E-3) ───────────────────────────────────────────────

def test_per_class_never_blended():
    """Decisions/events/claims are separate — never a blended number (DE2E-3)."""
    # 12 windows per class (the N-rule boundary): each has 1 gold decision,
    # 1 gold event, 1 gold claim. Pred: decisions + claims always right;
    # events only on even-index windows (50% event recall).
    gold, pred = [], []
    for i in range(12):
        g, p = _window(
            f"nb{i}",
            [(0, "decision", {"kind": "decision"}),
             (1, "event", {"kind": "event"}),
             (2, "claim", {"kind": "cost"})],
            [(0, "decision", {"kind": "decision"}),
             (2, "claim", {"kind": "cost"})],
        )
        if i % 2 == 0:
            p.gold_labels.append(_label(1, "event", kind="event"))
        gold.append(g)
        pred.append(p)

    report = compute_metrics(gold, pred)
    pc = report.per_class
    assert pc["decision"]["p"] == pytest.approx(1.0)
    assert pc["decision"]["r"] == pytest.approx(1.0)
    assert pc["decision"]["f1"] == pytest.approx(1.0)
    assert pc["event"]["p"] == pytest.approx(1.0)
    assert pc["event"]["r"] == pytest.approx(0.5)
    assert pc["event"]["f1"] == pytest.approx(2 * 0.5 / 1.5)  # 0.6667
    assert pc["claim"]["f1"] == pytest.approx(1.0)

    # Never blended: a single macro recall over all point classes differs
    # from every per-class rate (spec §6: base rates differ wildly).
    blended_recall = (12 + 6 + 12) / 36
    assert blended_recall == pytest.approx(30 / 36)
    for cls in ("decision", "event", "claim"):
        assert pc[cls]["r"] != pytest.approx(blended_recall)
    assert pc["event"]["f1"] != pytest.approx(pc["decision"]["f1"])


def test_per_class_n_rule_skip_and_flag():
    """Class window-N < 12 → skipped: NaN rates + per_class_n flag (plan §6.3)."""
    gold, pred = _window(
        "nr1",
        [(0, "decision", {"kind": "decision"})],
        [(0, "decision", {"kind": "decision"})],
    )
    report = compute_metrics([gold], [pred])
    assert report.per_class_n["decision"] == 1
    assert math.isnan(report.per_class["decision"]["p"])
    assert math.isnan(report.per_class["decision"]["r"])
    assert math.isnan(report.per_class["decision"]["f1"])
    # A class with no gold presence anywhere is N=0 — skipped as well.
    assert report.per_class_n["process"] == 0
    assert math.isnan(report.per_class["process"]["f1"])


def test_per_class_n_rule_eleven_is_skip():
    """N = 11 (one below the floor) → still skipped + flagged (boundary)."""
    gold, pred = _gen(11, lambda i: [(0, "decision")], lambda i: [(0, "decision")], prefix="n11")
    report = compute_metrics(gold, pred)
    assert report.per_class_n["decision"] == 11
    assert math.isnan(report.per_class["decision"]["f1"])


# ── Routing + property rates ────────────────────────────────────────────────

def test_layer_correct_routing():
    """R1 layer-correct: gold points must be pred points; gold process must not."""
    gold, pred = _window(
        "lc1",
        [(0, "decision"), (1, "process"), (2, "none")],
        [(0, "decision"), (1, "claim"), (2, "none")],
    )
    report = compute_metrics([gold], [pred])
    assert report.layer_correct == pytest.approx(0.5)  # process misrouted to a point


def test_atomicity_gold_matching():
    """R2 atomicity over matched gold-atomic items — gold matching, not self-report.

    The gold-compound item (atomicity=False) is EXCLUDED from the denominator;
    a self-report implementation counting pred atomicity on all items would
    score 2/3 instead of 0.5 — the fixture discriminates the two semantics.
    """
    gold, pred = _window(
        "at1",
        [(0, "decision", {"atomicity": True}),
         (1, "decision", {"atomicity": True}),
         (2, "decision", {"atomicity": False})],  # gold compound — excluded
        [(0, "decision", {"atomicity": True}),
         (1, "decision", {"atomicity": False}),
         (2, "decision", {"atomicity": True})],
    )
    report = compute_metrics([gold], [pred])
    assert report.atomicity == pytest.approx(0.5)  # 1/2 gold-atomic, not 2/3


def test_citation_correctness():
    """R4 citation-correctness: quote/span backs the claim (source_ref present + matches)."""
    # Window 1: identical ref (ok) vs missing ref (not ok) → 0.5.
    g1, p1 = _window(
        "ci1",
        [(0, "claim", {"source_ref": "ref-abc"}), (1, "claim", {"source_ref": "ref-xyz"})],
        [(0, "claim", {"source_ref": "ref-abc"}), (1, "claim", {"source_ref": None})],
    )
    assert compute_metrics([g1], [p1]).citation_correctness == pytest.approx(0.5)
    # Window 2: wrong-but-present ref (not ok); gold item WITHOUT a source_ref
    # is not gold-checkable → excluded from the denominator.
    g2, p2 = _window(
        "ci2",
        [(0, "claim", {"source_ref": "ref-abc"}),
         (1, "claim", {"source_ref": "ref-xyz"}),
         (2, "claim")],  # no gold source_ref — excluded
        [(0, "claim", {"source_ref": "ref-abc"}),
         (1, "claim", {"source_ref": "ref-zzz"}),  # present but wrong
         (2, "claim", {"source_ref": "ref-anything"})],
    )
    assert compute_metrics([g2], [p2]).citation_correctness == pytest.approx(0.5)  # 1/2


def test_kind_correctness_and_oov():
    """R6 kind-correctness: pred kind must equal gold kind (closed vocab)."""
    gold, pred = _window(
        "ki1",
        [(0, "claim", {"kind": "cost"}), (1, "claim", {"kind": "timeline"})],
        [(0, "claim", {"kind": "cost"}), (1, "claim", {"kind": "bogus-kind"})],
    )
    assert compute_metrics([gold], [pred]).kind_correctness == pytest.approx(0.5)

    # Discriminating OOV case: an exact kind match is auto-WRONG when the
    # kind is not in the closed vocab (out-of-vocab kind = auto FP, R6).
    g2, p2 = _window("ki2", [(0, "claim", {"kind": "cost"})], [(0, "claim", {"kind": "cost"})])
    assert compute_metrics([g2], [p2], kind_vocab={"cost", "timeline"}).kind_correctness == pytest.approx(1.0)
    assert compute_metrics([g2], [p2], kind_vocab={"timeline"}).kind_correctness == pytest.approx(0.0)
    assert compute_metrics([g2], [p2]).kind_correctness == pytest.approx(1.0)  # no vocab → exact match ok

    # Missing pred kind (None) → wrong (distinct branch from wrong-kind).
    g3, p3 = _window("ki3", [(0, "claim", {"kind": "cost"})], [(0, "claim", {"kind": None})])
    assert compute_metrics([g3], [p3]).kind_correctness == pytest.approx(0.0)


def test_entity_p_r():
    """R6/A12 entity P/R: item-level, class-agnostic (did the item get extracted)."""
    gold, pred = _window(
        "en1",
        [(0, "decision"), (1, "event"), (2, "event")],
        [(0, "decision"), (2, "claim")],  # event@1 lost; event@2 found as a claim
    )
    report = compute_metrics([gold], [pred])
    precision, recall = report.entity_p_r
    assert precision == pytest.approx(1.0)  # 2 matched / 2 pred points
    assert recall == pytest.approx(2 / 3)  # 2 matched / 3 gold points


def test_empty_rate():
    """A7 empty-rate: fraction of pred windows with zero point-class items."""
    g0, p0 = _window("e0", [(0, "claim")], [(0, "claim")])
    g1, p1 = _window("e1", [(0, "claim")], [(0, "claim")])
    g2, p2 = _window("e2", [(0, "claim")], [])  # degenerate-empty pred window
    report = compute_metrics([g0, g1, g2], [p0, p1, p2])
    assert report.empty_rate == pytest.approx(1 / 3)


def test_empty_rate_all_empty_and_entity_nan():
    """All pred windows empty → empty_rate 1.0; entity precision undefined (NaN)."""
    g0, p0 = _window("ee0", [(0, "claim")], [])
    g1, p1 = _window("ee1", [(0, "claim")], [])
    report = compute_metrics([g0, g1], [p0, p1])
    assert report.empty_rate == pytest.approx(1.0)
    assert math.isnan(report.entity_p_r[0])  # no pred points → precision undefined
    assert report.entity_p_r[1] == pytest.approx(0.0)  # recall 0


def test_ece_hand_computed():
    """A13 ECE over 0.1 bins with supplied confidences (hand-computed)."""
    gold, pred = _window(
        "ec1",
        [(0, "decision"), (1, "claim")],
        [(0, "decision"), (1, "event")],
    )
    confidences = {("ec1", 0): 0.9, ("ec1", 1): 0.6}
    report = compute_metrics([gold], [pred], confidences=confidences)
    # bin9: n=1 acc=1.0 conf=0.9 → 0.5·0.1 ; bin6: n=1 acc=0.0 conf=0.6 → 0.5·0.6
    assert report.ece == pytest.approx(0.5 * 0.1 + 0.5 * 0.6)
    # NaN when no confidences are supplied (c_cal comes from telemetry).
    assert math.isnan(compute_metrics([gold], [pred]).ece)


def test_ece_same_bin_none_and_clamp():
    """ECE weighting, None-confidence skip, and out-of-range clamping (both edges)."""
    gold, pred = _window(
        "eb1",
        [(0, "decision"), (1, "claim"), (2, "claim"), (3, "event"), (4, "decision")],
        [(0, "decision"), (1, "event"), (2, "claim"), (3, "event"), (4, "decision")],
    )
    conf = {
        ("eb1", 0): 0.6, ("eb1", 1): 0.6, ("eb1", 2): None,
        ("eb1", 3): 1.5,  # clamped to bin 9 + value 1.0 → dev 0
        ("eb1", 4): -0.2,  # clamped to bin 0 + value 0.0 (correct) → dev 0
    }
    report = compute_metrics([gold], [pred], confidences=conf)
    # bin6 (0.6, 0.6): n=2 acc=0.5 conf=0.6 → (2/4)·0.1 = 0.05
    # bin9 (1.5→1.0): correct at conf 1.0 → 0
    # bin0 (-0.2→0.0): correct at clamped conf 0.0 → (1/4)·|1.0-0.0| = 0.25
    assert report.ece == pytest.approx(0.30)


# ── Mitigation recall (R9 / DE2E-11 canonical probe) ────────────────────────

def test_mitigation_canonical_probe_recall():
    """R9 canonical probe (spec §6): X IMPL A; Z MITIGATES [X→A]; Y IMPL Z."""
    # The canonical probe shape, one window (12 copies → mitigation N = 12).
    def gold_spec(i):
        return [
            (0, "claim", {"kind": "option", "relations": [RelationLabel("IMPL", 0, 1)]}),
            (1, "claim", {"kind": "option"}),
            (2, "claim", {"kind": "price", "relations": [RelationLabel.mitigates(2, 0, 1, 0.3)]}),
            (3, "claim", {"kind": "market", "relations": [RelationLabel("IMPL", 3, 2)]}),
        ]

    def pred_spec(i):
        return list(gold_spec(i))  # the MITIGATES edge is the recall target

    gold, pred = _gen(12, gold_spec, pred_spec, prefix="mp")
    report = compute_metrics(gold, pred)
    assert report.per_class_n["mitigation"] == 12
    assert report.mitigation_recall == pytest.approx(1.0)
    assert report.per_class["mitigation"]["f1"] == pytest.approx(1.0)


def test_mitigation_recall_partial_and_n_rule():
    """Mitigation recall drops with missed edges; N < 12 → skip + NaN (DE2E-11)."""
    def gold_spec(i):
        return [
            (0, "claim", {"relations": [RelationLabel("IMPL", 0, 1)]}),
            (1, "claim"),
            (2, "claim", {"relations": [RelationLabel.mitigates(2, 0, 1, 0.3)]}),
        ]

    def pred_missing_spec(i):
        # Windows 0-2 miss the MITIGATES edge (deep-miss side of DE2E-11).
        if i < 3:
            return [(0, "claim"), (1, "claim"), (2, "claim")]
        return gold_spec(i)

    gold, pred = _gen(12, gold_spec, pred_missing_spec, prefix="mr")
    report = compute_metrics(gold, pred)
    assert report.per_class_n["mitigation"] == 12
    assert report.mitigation_recall == pytest.approx(9 / 12)

    # N rule: 1 window with gold mitigations → skipped → NaN.
    g, p = _window("mn1", gold_spec(0), gold_spec(0))
    report1 = compute_metrics([g], [p])
    assert report1.per_class_n["mitigation"] == 1
    assert math.isnan(report1.mitigation_recall)
    # No gold mitigations anywhere → N=0 → NaN (never a false 0.0).
    g2, p2 = _window("mn2", [(0, "claim")], [(0, "claim")])
    assert math.isnan(compute_metrics([g2], [p2]).mitigation_recall)


def test_mitigation_spurious_edge_and_source_shift():
    """Mitigation FP (spurious edge) + source-shift robustness of the identity match."""
    def gold_spec(i):
        return [
            (0, "claim", {"relations": [RelationLabel("IMPL", 0, 1)]}),
            (1, "claim"),
            (2, "claim", {"relations": [RelationLabel.mitigates(2, 0, 1, 0.3)]}),
        ]

    def pred_spec(i):
        if i == 0:
            # Deep-miss: the MITIGATES edge is absent entirely.
            return [(0, "claim"), (1, "claim"), (2, "claim")]
        if i == 1:
            # Source-EDU shifted (5 instead of 2) — the canonical edge identity
            # (target "[0→1]") still matches (DE2E-11 identity matching).
            return [
                (0, "claim", {"relations": [RelationLabel("IMPL", 0, 1)]}),
                (1, "claim"),
                (2, "claim", {"relations": [RelationLabel("MITIGATES", 5, "[0→1]", 0.3)]}),
            ]
        if i == 11:
            # Spurious extra edge on a different target → mitigation FP.
            return [
                (0, "claim", {"relations": [RelationLabel("IMPL", 0, 1)]}),
                (1, "claim"),
                (2, "claim", {"relations": [
                    RelationLabel.mitigates(2, 0, 1, 0.3),
                    RelationLabel.mitigates(2, 9, 8, 0.4),  # invented edge
                ]}),
            ]
        return gold_spec(i)

    gold, pred = _gen(12, gold_spec, pred_spec, prefix="ms")
    report = compute_metrics(gold, pred)
    assert report.per_class_n["mitigation"] == 12
    assert report.mitigation_recall == pytest.approx(11 / 12)  # window 0 missed
    assert report.per_class["mitigation"]["p"] == pytest.approx(11 / 12)  # spurious FP
    assert report.per_class["mitigation"]["f1"] == pytest.approx(11 / 12)


# ── Process routing + minimum signal ────────────────────────────────────────

def test_process_routing():
    """R3: gold process items never hit the graph (pred ∉ point classes)."""
    gold, pred = _window(
        "pr1",
        [(0, "process"), (1, "process"), (2, "process")],
        [(0, "none"), (1, "claim")],  # @2 absent — also correctly not-graphed
    )
    report = compute_metrics([gold], [pred])
    assert report.process_routing == pytest.approx(2 / 3)


def test_min_signal_per_window_type():
    """Minimum-signal (spec §5.8/§6): operational ≥1 event, design floor 0."""
    # Operational window with an event → passes.
    g0, p0 = _window("ms0", [(0, "event")], [(0, "event")])
    # Operational window with no events → degenerate-empty → fails.
    g1, p1 = _window("ms1", [(0, "claim")], [(0, "claim")])
    # Design window with no events → floor 0 → passes.
    g2, p2 = _window("ms2", [(0, "claim")], [(0, "claim")])

    report = compute_metrics(
        [g0, g1, g2],
        [p0, p1, p2],
        window_types={"ms0": "operational", "ms1": "operational", "ms2": "design"},
    )
    assert report.min_signal == {"operational": False, "design": True}

    # Default (no window_types) is fail-closed operational.
    assert compute_metrics([g1], [p1]).min_signal == {"operational": False}

    # Configurable event floor (issue target: N configurable).
    report_floor = compute_metrics([g0], [p0], min_events={"operational": 2})
    assert report_floor.min_signal == {"operational": False}


# ── R1∧R3 conjunction (spec §6 — THE decision-class test) ───────────────────

def _meta_mixed_set(count: int, pred_misclassifies: bool):
    """Meta-discussion-mixed fixture set (FM2): real decision + meta-discussion.

    Each window: edu0 a real decision ("we decided X"), edu1 meta-discussion
    ("we decided the extractor should support Y" — gold: process, must NOT be
    a decision). pred optionally misclassifies the meta-discussion as a
    decision (the R1∧R3 conjunction FP).
    """
    def gold_spec(i):
        return [(0, "decision", {"kind": "decision"}), (1, "process")]

    def pred_spec(i):
        edu1 = "decision" if pred_misclassifies else "process"
        return [(0, "decision", {"kind": "decision"}), (1, edu1)]

    return _gen(count, gold_spec, pred_spec, prefix="r3")


def test_r1r3_conjunction_band():
    """R1∧R3 decisions-FP ≤5% on N≥30; watch below N=30 (DE2E-3 Layer-2 band)."""
    gold, pred = _meta_mixed_set(BAND_N_ACCEPT, pred_misclassifies=True)
    report = compute_metrics(gold, pred)
    conj = report.r1r3_conjunction
    assert conj["n_windows"] == BAND_N_ACCEPT
    assert conj["n_decisions_pred"] == 2 * BAND_N_ACCEPT
    assert conj["decisions_fp"] == BAND_N_ACCEPT  # every meta-discussion misrouted
    assert conj["decisions_fp_rate"] == pytest.approx(0.5)
    assert conj["band"] == "fail"
    # Per-class process values at real N (never-blended view of the same set):
    # all gold process items were matched to pred decisions → recall 0, f1 0.
    assert report.per_class_n["process"] == BAND_N_ACCEPT
    assert report.per_class["process"]["r"] == pytest.approx(0.0)
    assert report.per_class["process"]["f1"] == pytest.approx(0.0)

    gold_ok, pred_ok = _meta_mixed_set(BAND_N_ACCEPT, pred_misclassifies=False)
    report_ok = compute_metrics(gold_ok, pred_ok)
    conj_ok = report_ok.r1r3_conjunction
    assert conj_ok["decisions_fp_rate"] == pytest.approx(0.0)
    assert conj_ok["band"] == "pass"
    assert report_ok.per_class["process"]["f1"] == pytest.approx(1.0)  # clean routing

    gold_w, pred_w = _meta_mixed_set(5, pred_misclassifies=True)
    conj_w = compute_metrics(gold_w, pred_w).r1r3_conjunction
    assert conj_w["band"] == "watch"  # N < 30 — the band cannot fire


def test_r1r3_watch_at_29_windows():
    """N = 29 (one below the accept floor) → watch, not pass/fail (boundary)."""
    gold, pred = _meta_mixed_set(29, pred_misclassifies=True)
    conj = compute_metrics(gold, pred).r1r3_conjunction
    assert conj["n_windows"] == 29
    assert conj["band"] == "watch"


def test_r1r3_zero_decisions_watch():
    """Zero pred decisions → the conjunction test cannot fire → watch (not fail).

    Not a vacuous pass (an extractor that never emits decisions is broken on
    the recall side — caught by per-class recall + min-signal/empty-rate), and
    not a fail either (zero FPs): the band is un-evaluable.
    """
    def gold_spec(i):
        return [(0, "decision", {"kind": "decision"}), (1, "process")]

    def pred_spec(i):
        return [(0, "process"), (1, "process")]

    gold, pred = _gen(BAND_N_ACCEPT, gold_spec, pred_spec, prefix="zd")
    conj = compute_metrics(gold, pred).r1r3_conjunction
    assert conj["n_windows"] == BAND_N_ACCEPT
    assert math.isnan(conj["decisions_fp_rate"])
    assert conj["decisions_fp"] == pytest.approx(0.0)
    assert conj["band"] == "watch"


# ── Fuzzy matching (Pass 2 — shifted indices) ───────────────────────────────

def test_fuzzy_matching_on_shifted_indices():
    """Pass-2 fuzzy fallback: a +1-shifted pred (same text, new index) still matches."""
    # Same content at a shifted index → pass-2 fuzzy match succeeds (entity 1.0).
    g_shift, p_shift = _window(
        "fs1",
        [(0, "decision")],
        [(1, "decision")],
        texts={0: "the decision about shipping", 1: "the decision about shipping"},
    )
    report = compute_metrics([g_shift], [p_shift])
    assert report.entity_p_r == (pytest.approx(1.0), pytest.approx(1.0))
    assert report.layer_correct == pytest.approx(1.0)

    # Genuinely different content → no fuzzy match (distinctive texts).
    g_no, p_no = _window(
        "fs2",
        [(0, "decision")],
        [(1, "event")],
        texts={0: "the decision about shipping", 1: "the event about bugfixing"},
    )
    report_no = compute_metrics([g_no], [p_no])
    assert report_no.entity_p_r == (pytest.approx(0.0), pytest.approx(0.0))
    assert report_no.layer_correct == pytest.approx(0.0)


def test_shifted_multi_label_window():
    """Pass-1 is text-verified: a shared-index shift never mispairs (bug-deep P1)."""
    # Pred transcript has a preamble + shifted: pred@0 is new content, pred@1 is
    # gold@0's text, pred@2 is gold@1's text. Pass-1 defers text-mismatched
    # index pairs; pass-2 re-pairs the true correspondence by text.
    texts = {
        0: "preamble filler",
        1: "we decided to ship alpha",
        2: "we fixed the beta bug",
    }
    gold, pred = _window(
        "sh1",
        [(1, "decision"), (2, "event")],
        [(0, "none"), (1, "decision"), (2, "event")],
        texts=texts,
    )
    report = compute_metrics([gold], [pred])
    # gold decision@0 ↔ pred decision@1 and gold event@1 ↔ pred event@2 by text.
    assert report.entity_p_r == (pytest.approx(1.0), pytest.approx(1.0))
    assert report.layer_correct == pytest.approx(1.0)

    # The same shift must not flip r1r3 to 100% FP: the shifted decision is the
    # gold decision (matched pair), not a false decision.
    golds, preds = [], []
    for i in range(30):
        g, p = _window(
            f"sh2-{i}",
            [(0, "decision")],
            [(1, "decision")],
            texts={0: "we decided to ship alpha", 1: "we decided to ship alpha"},
        )
        golds.append(g)
        preds.append(p)
    conj = compute_metrics(golds, preds).r1r3_conjunction
    assert conj["n_windows"] == 30
    assert conj["decisions_fp"] == pytest.approx(0.0)
    assert conj["band"] == "pass"  # consistent with entity/layer scoring the shift as perfect


def test_unmatched_none_labels_do_not_crash():
    """Unmatched 'none' labels carry no per-class signal — no KeyError (P0 fix)."""
    # gold labels a "none" EDU the pred omitted; pred labels a "none" EDU the
    # gold did not label — both land in gold_fn/pred_fp with class "none".
    gold, pred = _window(
        "nz1",
        [(0, "claim"), (1, "none")],
        [(0, "claim"), (2, "none")],
    )
    report = compute_metrics([gold], [pred])
    assert report.entity_p_r == (pytest.approx(1.0), pytest.approx(1.0))
    assert report.layer_correct == pytest.approx(1.0)
    assert report.empty_rate == pytest.approx(0.0)


def test_r1r3_unlabeled_edu_decision_is_fp():
    """A pred decision on an EDU with NO gold label counts as an FP (g is None)."""
    def gold_spec(i):
        return [(0, "decision", {"kind": "decision"})]

    def pred_spec(i):
        spec = [(0, "decision", {"kind": "decision"})]
        if i < 3:
            spec.append((1, "decision"))  # edu1 has NO gold label → FP
        return spec

    gold, pred = _gen(BAND_N_ACCEPT, gold_spec, pred_spec, prefix="uf")
    conj = compute_metrics(gold, pred).r1r3_conjunction
    assert conj["n_windows"] == BAND_N_ACCEPT
    assert conj["decisions_fp"] == pytest.approx(3.0)  # 3 unlabeled-EDU decisions
    assert conj["n_decisions_pred"] == pytest.approx(33.0)
    assert conj["decisions_fp_rate"] == pytest.approx(3 / 33)
    assert conj["band"] == "fail"


# ── kappa reuse (DE2E-1) ────────────────────────────────────────────────────

def test_kappa_reuses_tools_kappa():
    """κ agreement math on a hand-computed fixture (DE2E-1) + SAME function reuse."""
    a = [
        _label(0, "decision"),
        _label(1, "decision"),
        _label(2, "event"),
        _label(3, "none"),
    ]
    b = [
        _label(0, "decision"),
        _label(1, "decision"),
        _label(2, "event"),
        _label(3, "claim"),
    ]
    # po = 3/4 = 0.75; pe = .25 + .0625 = 0.3125; κ = (0.75-.3125)/.6875 = 0.6364
    assert kappa(a, b) == pytest.approx(0.6363636364)
    # True re-export: the SAME function object as tools/kappa.py, not a copy.
    assert eval_metrics.kappa is tools_kappa.kappa

    # Disjoint labeling → None (degenerate; DE2E-1 treats None as NOT_GREEN).
    assert kappa([_label(0, "decision")], [_label(9, "decision")]) is None
    # The consumer contract: None propagates to the DE2E-1 gate as NOT_GREEN
    # (tools/kappa.py gate_decision — degenerate/disjoint labeling).
    gate = tools_kappa.gate_decision(None, 1.0)
    assert gate.verdict == "NOT_GREEN"
    assert "no overlapping labels" in gate.reason


# ── thresholds.yaml reconciliation (W-6) ────────────────────────────────────

THRESHOLDS_PATH = _REPO_ROOT / "tests" / "extraction_eval" / "thresholds.yaml"
A_ROW_RE = re.compile(r"#\s*A(\d+)(?!\d)")


def _a_rows_present(text: str) -> set[int]:
    return {int(m.group(1)) for m in A_ROW_RE.finditer(text)}


def test_thresholds_yaml_loads_and_has_all_a_rows():
    """The ONE reconciled file: loads, A1-A22 complete (W-6)."""
    assert THRESHOLDS_PATH.exists(), "reconcile the EXISTING file — no second file"
    data = yaml.safe_load(THRESHOLDS_PATH.read_text())
    assert set(data) >= {"gold_version", "extractor_version", "band_semantics", "standards"}
    s = data["standards"]
    assert "regression_delta_f1" in s  # CI regression guard preserved

    text = THRESHOLDS_PATH.read_text()
    rows = _a_rows_present(text)
    missing = set(range(1, 23)) - rows
    assert not missing, f"A-rows missing from thresholds.yaml: {sorted(missing)}"

    # The comment scan alone is not enough: every A-row KEY must exist in the
    # parsed standards dict (a stale comment for a deleted row must fail).
    expected_a_keys = {
        "point_precision_raw", "point_recall_raw", "point_f1_raw", "point_precision_live",
        "fn_rate_hv", "fn_rate_live_judge", "empty_rate", "empty_rate_hv", "fp_session_rate",
        "per_kind_f1", "nand_precision", "entity_precision", "entity_recall",
        "ece", "live_floor_compliance", "amplification", "amplification_vs_regex",
        "nodes_per_session", "keep_ratio", "cost_per_session", "frontier_calls",
        "provenance_coverage", "judge_agreement_kappa",
    }
    assert expected_a_keys <= s.keys(), sorted(expected_a_keys - s.keys())
    assert "amplification_vs_regex" in s  # A22 — the row that was missing from the file

    # Existing A-row values unchanged (unchanged semantics, plan §6.3) —
    # key-wise so benign field additions don't break the test.
    assert s["point_precision_raw"]["target"] == 0.65 and s["point_precision_raw"]["block"] == 0.55  # A1
    assert s["point_recall_raw"]["target"] == 0.70 and s["point_recall_raw"]["block"] == 0.60  # A2
    assert s["point_precision_live"]["target"] == 0.80 and s["point_precision_live"]["block"] == 0.70  # A4
    assert s["empty_rate"]["lo"] == 0.20 and s["empty_rate"]["hi"] == 0.40  # A7
    assert s["keep_ratio"]["failclose_ratio"] == 0.40  # A17
    assert s["nodes_per_session"]["hard_ceiling"] == 50  # A16
    assert s["ece"]["target"] == 0.10 and s["ece"]["block"] == 0.15  # A13
    # A12 — the reconciled R8 entity P/R rows (same numbers, NOT duplicated).
    assert s["entity_precision"]["target"] == 0.80 and s["entity_precision"]["block"] == 0.65
    assert s["entity_recall"]["target"] == 0.65 and s["entity_recall"]["block"] == 0.50
    assert s["amplification_vs_regex"]["regex_baseline"] == 1.6
    assert s["amplification_vs_regex"]["block_ratio"] == 5.0

    # ONE authoritative file — no second thresholds file anywhere in tests/.
    assert len(list((_REPO_ROOT / "tests").rglob("thresholds*.yaml"))) == 1


def test_thresholds_yaml_r8_rows_and_band_semantics():
    """R8 rows present with target/block + N + band semantics (research-r8)."""
    data = yaml.safe_load(THRESHOLDS_PATH.read_text())
    s = data["standards"]
    text = THRESHOLDS_PATH.read_text()

    r8 = {
        "layer_correct": (0.90, 0.80),
        "atomicity": (0.85, 0.70),
        "kind_correctness": (0.90, 0.80),
        "citation_correctness": (0.90, 0.80),
        "mitigation_recall": (0.75, 0.60),
        "process_routing": (0.95, 0.80),
    }
    for row, (target, block) in r8.items():
        assert row in s, f"R8 row {row!r} missing"
        assert s[row]["target"] == target
        assert s[row]["block"] == block
        assert s[row]["n_accept"] == 30 and s[row]["n_block"] == 12
    assert s["r1r3_decisions_fp"]["target"] == 0.05  # ≤5% band
    assert s["r1r3_decisions_fp"]["block"] == 0.05  # one-sided max-direction row
    assert s["r1r3_decisions_fp"]["n_accept"] == 30
    assert s["r1r3_decisions_fp"]["n_block"] == 30  # the band needs N≥30 (DE2E-3)
    assert s["r1r3_decisions_fp"]["direction"] == "max"

    # Band semantics: pass ≥ target N≥30 / fail < block N≥12 / watch between;
    # live floor rolling N=20 exception (research-r8).
    band = data["band_semantics"]
    assert "N>=30" in band and "N>=12" in band and "N=20" in band

    # Reconciliation notes (plan §6.3): A7/A17 per-session vs 4-week/×3-window,
    # mitigation block PROVISIONAL, no duplicated rows.
    assert "PROVISIONAL" in text or "provisional" in text
    assert "block_provisional" in s["mitigation_recall"]
    assert s["process_routing"]["warn_only_until"] == 20
    assert "reconciled" in text and "NOT duplicated" in text


def test_band_semantics_applied_to_report():
    """The reconciled yaml rows drive pass/fail/watch verdicts (W-6 coupling).

    The band application for the non-r1r3 rows lands in the CI eval workflow
    (epic slice 8c, #962); this test pins the band semantics documented in the
    file so the future production gate cannot drift from the reconciled rows.
    """
    s = yaml.safe_load(THRESHOLDS_PATH.read_text())["standards"]

    def verdict(rate, row, n):
        """Apply the file's band semantics: pass ≥ target on N≥30; fail < block on N≥12; between = watch."""
        warn_until = row.get("warn_only_until")
        if warn_until is not None and n < warn_until:
            return "watch"
        if n >= row.get("n_accept", 30) and rate >= row["target"]:
            return "pass"
        if n >= row.get("n_block", 12) and rate < row["block"]:
            return "fail"
        return "watch"

    # layer-correct (0.90/0.80): block below 0.80 at N≥12, pass ≥0.90 at N≥30.
    assert verdict(0.50, s["layer_correct"], 30) == "fail"
    assert verdict(0.95, s["layer_correct"], 30) == "pass"
    assert verdict(0.85, s["layer_correct"], 30) == "watch"  # between
    assert verdict(0.50, s["layer_correct"], 11) == "watch"  # N<12 → no verdict
    # atomicity (0.85/0.70): N≥12 block band fires at 0.50; N<30 cannot pass.
    assert verdict(0.50, s["atomicity"], 12) == "fail"
    assert verdict(0.90, s["atomicity"], 30) == "pass"
    assert verdict(0.90, s["atomicity"], 20) == "watch"
    assert verdict(0.90, s["atomicity"], 12) == "watch"  # good rate, N<30
    # process-routing: warn-only until n≥20 (class rarity).
    assert verdict(0.50, s["process_routing"], 15) == "watch"
    assert verdict(0.50, s["process_routing"], 20) == "fail"  # n>=20 → block-capable
    assert verdict(0.50, s["process_routing"], 25) == "fail"
    # mitigation recall: block PROVISIONAL but the band applies.
    assert verdict(0.90, s["mitigation_recall"], 30) == "pass"
    assert verdict(0.65, s["mitigation_recall"], 30) == "watch"  # between 0.60-0.75
    assert verdict(0.55, s["mitigation_recall"], 30) == "fail"  # < 0.60 block
    # entity P/R (A12 rows): precision 0.80/0.65, recall 0.65/0.50.
    assert verdict(0.85, s["entity_precision"], 30) == "pass"
    assert verdict(0.70, s["entity_precision"], 30) == "watch"
    assert verdict(0.60, s["entity_precision"], 30) == "fail"
    assert verdict(0.70, s["entity_recall"], 30) == "pass"
    assert verdict(0.45, s["entity_recall"], 30) == "fail"

    # One-sided (max-direction, lower-is-better) rows: ECE ≤0.10 / >0.15 and
    # r1r3 decisions-FP ≤5% — the pass boundary is INCLUSIVE (≤ target); the
    # r1r3 fail band also needs N ≥ 30 (n_block: 30 — DE2E-3 Layer-2 band).
    def verdict_max(rate, row, n):
        warn_until = row.get("warn_only_until")
        if warn_until is not None and n < warn_until:
            return "watch"
        if n >= row.get("n_accept", 30) and rate <= row["target"]:
            return "pass"
        if n >= row.get("n_block", 12) and rate > row.get("block", row["target"]):
            return "fail"
        return "watch"

    assert verdict_max(0.08, s["ece"], 30) == "pass"
    assert verdict_max(0.12, s["ece"], 30) == "watch"
    assert verdict_max(0.20, s["ece"], 30) == "fail"
    assert verdict_max(0.05, s["r1r3_decisions_fp"], 30) == "pass"  # exactly ≤ 5%
    assert verdict_max(0.0501, s["r1r3_decisions_fp"], 30) == "fail"
    assert verdict_max(0.05, s["r1r3_decisions_fp"], 29) == "watch"
    assert verdict_max(0.50, s["r1r3_decisions_fp"], 20) == "watch"  # N<30 → no fail

    # A real report coupled to the yaml: an atomicity 0.5 report at N=1 window
    # → watch (the N rule gates the band — rate_n carries the true support).
    gold, pred = _window(
        "bd1",
        [(0, "decision", {"atomicity": True}), (1, "decision", {"atomicity": True})],
        [(0, "decision", {"atomicity": True}), (1, "decision", {"atomicity": False})],
    )
    report = compute_metrics([gold], [pred])
    assert report.atomicity == pytest.approx(0.5)
    assert verdict(report.atomicity, s["atomicity"], report.rate_n["atomicity"]) == "watch"

    # Real mitigation report at N=12 with recall 0.583 (< 0.60 block) → FAIL
    # (block band fires at N≥12 — the DE2E-11 per-class N rule is satisfied).
    def mit_spec(i):
        return [
            (0, "claim", {"relations": [RelationLabel("IMPL", 0, 1)]}),
            (1, "claim"),
            (2, "claim", {"relations": [RelationLabel.mitigates(2, 0, 1, 0.3)]}),
        ]

    def mit_pred_miss5(i):
        return [(0, "claim"), (1, "claim"), (2, "claim")] if i < 5 else mit_spec(i)

    gold_m, pred_m = _gen(12, mit_spec, mit_pred_miss5, prefix="bm")
    report_m = compute_metrics(gold_m, pred_m)
    assert report_m.mitigation_recall == pytest.approx(7 / 12)
    assert verdict(
        report_m.mitigation_recall, s["mitigation_recall"], report_m.per_class_n["mitigation"]
    ) == "fail"


def test_r1r3_constants_match_thresholds_yaml():
    """The in-code R1∧R3 band cannot drift from the reconciled yaml row."""
    row = yaml.safe_load(THRESHOLDS_PATH.read_text())["standards"]["r1r3_decisions_fp"]
    assert DECISIONS_FP_BAND == row["target"]
    assert DECISIONS_FP_BAND == row["block"]
    assert BAND_N_ACCEPT == row["n_accept"]
    assert BAND_N_ACCEPT == row["n_block"]
    assert row["direction"] == "max"


# ── Real gold seeds (plan W-6: 0323_excerpt, gold_standard, eval_results_v2) ─

def _window_from_0323(drop_indices: set[int] | None = None) -> Window:
    seed = json.loads((_REPO_ROOT / "tests" / "gold" / "0323_excerpt.json").read_text())
    drop = set(drop_indices or ())
    labels = [
        _label(i, "claim", kind="claim") for i in seed["points_keep"] if i not in drop
    ]
    labels += [_label(i, "none") for i in seed["drop"]]
    by_idx = {l.edu_index: l for l in labels}
    for op in seed.get("operators", []):
        src = by_idx.get(op["src"])
        if src is not None:
            src.relations.append(RelationLabel("IMPL", op["src"], op["dst"]))
    return Window("0323", "w-0323", seed["utterances"], labels)


def test_0323_seed_feeds_metrics():
    """The real 0323 seed runs through compute_metrics (gold vs a losing pred)."""
    gold_win = _window_from_0323()
    # Pred loses 4 of the kept points (extraction loss) but stays non-empty.
    lost = set(gold_win.gold_labels[i].edu_index for i in range(4))
    pred_win = _window_from_0323(drop_indices=lost)
    # 0323 is a design-style session (no events) — explicit window type.
    report = compute_metrics(
        [gold_win], [pred_win], window_types={"w-0323": "design"}
    )
    assert isinstance(report, MetricsReport)
    # Per-class N rule fires on the real seed: 1 window → claim skipped + flagged.
    assert report.per_class_n["claim"] == 1
    assert math.isnan(report.per_class["claim"]["f1"])
    assert report.per_class_n["mitigation"] == 0
    assert math.isnan(report.mitigation_recall)
    # Pred lost points → layer-correct < 1; still emits points → empty_rate 0.
    assert 0.0 < report.layer_correct < 1.0
    assert report.empty_rate == pytest.approx(0.0)
    assert report.min_signal == {"design": True}
    assert report.r1r3_conjunction["n_windows"] == 1
    assert report.r1r3_conjunction["band"] == "watch"
    # Fail-closed default: without a window type the operational floor applies
    # (no events in this transcript) → the degenerate-empty guard fires.
    report_default = compute_metrics([gold_win], [pred_win])
    assert report_default.min_signal == {"operational": False}


def test_gold_standard_seed_feeds_metrics():
    """gold_standard.json converts AND runs through compute_metrics (23 claims)."""
    gs = json.loads((_REPO_ROOT / "tests" / "gold_standard.json").read_text())
    edus = [u["text"] for u in gs["utterances"]]
    gold_labels = [
        _label(u["id"] - 1, "claim" if u["is_claim"] else "none",
               kind="claim" if u["is_claim"] else None)
        for u in gs["utterances"]
    ]
    assert len(gold_labels) == 30
    # The is_claim flags are authoritative: 23 claims / 7 non-claims (the seed's
    # own stats block says 24/6 — stale; the flags win).
    assert sum(1 for l in gold_labels if l.class_ == "claim") == 23
    assert sum(1 for l in gold_labels if l.class_ == "none") == 7

    # Pred loses 5 of the 23 claims (extraction loss) → metrics fire.
    pred_labels, dropped = [], 0
    for l in gold_labels:
        if l.class_ == "claim" and dropped < 5:
            pred_labels.append(_label(l.edu_index, "none"))
            dropped += 1
        else:
            pred_labels.append(_label(l.edu_index, l.class_, kind=l.kind))
    pred_win = Window("gold-standard", "w-gs", edus, pred_labels)
    report = compute_metrics(
        [Window("gold-standard", "w-gs", edus, gold_labels)],
        [pred_win],
        window_types={"w-gs": "design"},
    )
    assert report.per_class_n["claim"] == 1  # N rule on the real seed
    assert math.isnan(report.per_class["claim"]["f1"])
    assert 0.0 < report.layer_correct < 1.0  # 18/23 routed
    assert report.empty_rate == pytest.approx(0.0)
    assert report.min_signal == {"design": True}
    assert report.r1r3_conjunction["band"] == "watch"


def test_eval_results_v2_seed_loads():
    """eval_results_v2.json loads; aggregates are internally consistent."""
    results = json.loads((_REPO_ROOT / "tests" / "eval_results_v2.json").read_text())
    assert isinstance(results, list) and results
    ok = [r for r in results if "tp" in r]
    assert any(r["model"] == "deepseek-flash" for r in ok)
    flash = next(r for r in ok if r["model"] == "deepseek-flash")
    tp, fp, fn = flash["tp"], flash["fp"], flash["fn"]
    # Recompute from the counts — catches an internally inconsistent seed.
    assert flash["precision"] == pytest.approx(tp / (tp + fp))
    assert flash["recall"] == pytest.approx(tp / (tp + fn))
    assert flash["f1"] == pytest.approx(2 * tp / (2 * tp + fp + fn))
    # Model entries that errored carry "error" (seed realism: not all models run).
    assert any("error" in r for r in results)

    # Discriminating recompute: a nonzero-fp/fn entry catches wrong formulas
    # (the flash row has fp=fn=0 → any formula yields 1.0).
    synthetic = {"tp": 2, "fp": 1, "fn": 1}
    tp, fp, fn = synthetic["tp"], synthetic["fp"], synthetic["fn"]
    assert tp / (tp + fp) == pytest.approx(2 / 3)
    assert tp / (tp + fn) == pytest.approx(2 / 3)
    assert 2 * tp / (2 * tp + fp + fn) == pytest.approx(4 / 6)


# ── Validation negatives ────────────────────────────────────────────────────

def test_compute_metrics_validation():
    g0, p0 = _window("v0", [(0, "claim")], [(0, "claim")])
    g1, p1 = _window("v1", [(0, "claim")], [(0, "claim")])
    # Mismatched window sets (both directions).
    with pytest.raises(ValueError):
        compute_metrics([g0, g1], [p0])
    with pytest.raises(ValueError):
        compute_metrics([g0], [p0, p1])
    with pytest.raises(ValueError):
        compute_metrics([g0], [])
    # Duplicate window ids (gold side AND pred side — count-checked, not set-checked).
    with pytest.raises(ValueError):
        compute_metrics([g0, g0], [p0, p0])
    with pytest.raises(ValueError):
        compute_metrics([g0, g1], [p0, p0, p1])
    with pytest.raises(ValueError):
        compute_metrics([g0, g1, g0], [p0, p1, p1])
    # Unknown class (built directly — the fixture builder needs a real class).
    bad_win = Window("s1", "v2", ["edu"], None)
    bad_gold = Window("s1", "v2", ["edu"], [_label(0, "decision")])
    bad_pred = Window("s1", "v2", ["edu"], [Label(0, "bogus-class")])
    with pytest.raises(ValueError):
        compute_metrics([bad_gold], [bad_pred])
    # Gold window without labels (paired pred so only THIS path can raise).
    no_labels = Window("s1", "v3", ["edu"], None)
    no_labels_pred = Window("s1", "v3", ["edu"], [_label(0, "claim")])
    with pytest.raises(ValueError):
        compute_metrics([no_labels], [no_labels_pred])
    # Unknown window_type in the window_types kwarg.
    with pytest.raises(ValueError):
        compute_metrics([g0], [p0], window_types={"v0": "bogus"})
    # window_types referencing an unknown window id.
    with pytest.raises(ValueError):
        compute_metrics([g0], [p0], window_types={"v0": "operational", "nope": "design"})
    # min_events keys that are not window types.
    with pytest.raises(ValueError):
        compute_metrics([g0], [p0], min_events={"bogus": 3})
    # Empty eval sets.
    with pytest.raises(ValueError):
        compute_metrics([], [p0])


def test_pred_unlabeled_window_is_empty():
    """A pred window with gold_labels=None is an unlabeled (empty) window — intended.

    The extractor emitted nothing: it counts toward empty_rate and the
    degenerate-empty min-signal guard, and never raises.
    """
    g, _ = _window("pu1", [(0, "claim")], [(0, "claim")])
    pred_none = Window("s1", "pu1", ["we decided to ship item 0"], None)
    report = compute_metrics([g], [pred_none])
    assert report.empty_rate == pytest.approx(1.0)
    assert report.min_signal == {"operational": False}  # degenerate-empty fires


def test_duplicate_edu_index_rejected():
    """Duplicate labels at one edu_index are malformed — one verdict per EDU.

    Mirrors tools/judge_harness.py (hard error on duplicate indices): the
    extractor/rubric labels each EDU exactly once, so duplicates are rejected
    in validation rather than silently order-dependent.
    """
    gold, pred = _window("du1", [(0, "claim")], [(0, "claim")])
    pred.gold_labels.append(_label(0, "decision"))  # duplicate index
    with pytest.raises(ValueError):
        compute_metrics([gold], [pred])
    bad_rel = _window("du2", [(0, "claim")], [(0, "claim")])
    bad_rel[1].gold_labels[0].relations.append(RelationLabel("BOGUS", 0, 1))
    with pytest.raises(ValueError):
        compute_metrics([bad_rel[0]], [bad_rel[1]])
