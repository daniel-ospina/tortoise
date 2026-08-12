"""Extraction-quality measurement core (epic #909 slice 8a, #960).

Implements plan §6.3's `compute_metrics` + `kappa` over tests/eval/types.py.
Skeleton lineage: tests/aries_extraction.py (load_sample / evaluate /
run_variant — the fuzzy-matching + per-run aggregation patterns), as
specified in plan §8.3 slice 8.

Metric semantics (all documented; matching plan §6.3, spec §6, research-r8):

- Per-class P/R/F1: decision / event / claim / process / mitigation SEPARATE
  — NEVER blended (spec §6; base rates differ wildly). Matching is fuzzy
  (normalized containment + word-overlap, the aries pattern) within the same
  window, anchored on edu_index with a fuzzy text fallback when indices
  shift. Per-class N rule (plan §6.3, DE2E-3): a window contributes to a
  class's N iff it contains ≥1 gold item of that class; class window-N < 12
  → the class is SKIPPED (p/r/f1 = NaN) and flagged via per_class_n.
- layer_correct (R1, the headline semantic gate): among gold non-"none"
  items, the fraction ROUTED to the right layer — gold ∈ {decision, event,
  claim} must be a pred point-class; gold process must NOT be a pred
  point-class (process items never hit the graph). Per-class rates carry the
  fine class-distinction signal; layer_correct is the routing gate.
- atomicity (R2): among matched gold-atomic items (gold.atomicity True), the
  fraction whose matched pred item is also atomic — gold matching, not
  self-report (§6.3). Gold-compound items (atomicity False) are excluded.
- citation_correctness (R4, semantic): among matched items with a gold
  source_ref, the fraction whose pred source_ref is present AND fuzzy-matches
  (quote/span backs the claim). Items without a gold source_ref are not
  gold-checkable → excluded.
- kind_correctness (R6): among matched items with a gold kind, the fraction
  with pred.kind == gold.kind (closed pack vocab). Out-of-vocab pred kinds
  are auto-wrong when `kind_vocab` is supplied; missing pred kind is wrong.
- entity_p_r (R6 / A12): item-level entity recall/precision over
  entity-bearing labels (class ∈ {decision, event, claim}) — recall = gold
  items found, precision = pred items matched (class-agnostic: R6 entity
  extraction is "did the entity-bearing item get extracted at all").
- empty_rate (A7): the fraction of PRED windows with zero entity-bearing
  items (the degenerate-empty input; band 20-40% — thresholds.yaml A7).
- ece (A13): expected calibration error over pred labels with confidence —
  `confidences` kwarg keyed by (window_id, edu_index); 0.1 bins (the
  telemetry confidence_histogram shape). Correctness = class match vs the
  gold label at the same EDU. NaN when no confidences are supplied.
- mitigation_recall (R9 / DE2E-11): gold MITIGATES relations matched by the
  canonical edge identity (target "[X→A]") against pred MITIGATES. NaN when
  the mitigation class is skipped (no gold mitigations or window-N < 12).
  The canonical probe (X IMPL A; Z MITIGATES [X→A]; Y IMPL Z) is the
  deterministic fixture (spec §6).
- process_routing (R3): among gold process items, the fraction the pred did
  NOT emit as a point-class (pred ∉ {decision, event, claim}) — process
  decisions never hit the graph. Reported at any N; the band (≥0.95/<0.80)
  is warn-only until n ≥ 20 (research-r8, class rarity).
- min_signal (spec §5.8/§6 — the degenerate-empty defense): per window type,
  ALL pred windows of that type must emit ≥ N events (operational floor
  default 1, design 0 — tools/min_signal.py DEFAULT_MIN_EVENTS; floors
  configurable via the `min_events` kwarg). Window types come from the
  `window_types` kwarg (window_id → design|operational); untyped windows
  default to operational (fail-closed). min_signal = {window_type: passed}.
- r1r3_conjunction (spec §6 — THE decision-class test): on the eval's window
  set (callers pass the meta-discussion-mixed fixture set, N ≥ 30),
  decisions_fp = pred decisions whose gold label at the same EDU is not a
  decision (meta-discussion / recommendation / misroute); decisions_fp_rate
  = fp / pred decisions; band: pass ≤ 5% on N ≥ 30, fail > 5% on N ≥ 30,
  watch N < 30 (thresholds.yaml R8 r1r3_decisions_fp row, DE2E-3).

kappa() re-exports tools/kappa.py (merged slice-1 tooling, #945) as a module
level alias — NOT duplicated here. The plan §6.3 declares `kappa() -> float`;
tools.kappa returns None when the two label sets share no EDU (degenerate/
disjoint labeling) — reconciled: None propagates, and the DE2E-1 gate treats
None as NOT_GREEN (documented in tools/kappa.py; consumers must handle None).
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tests.eval.types import (  # noqa: E402
    CLASS_VOCAB,
    Label,
    MetricsReport,
    RelationLabel,
    Window,
)
from tools import kappa as _tools_kappa  # noqa: E402
from tools.min_signal import min_signal_check  # noqa: E402

# ── Band constants (research-r8 / plan §6.3 — watch-gates, not powered tests) ──
CLASS_N_MIN = 12  # per-class gold window-N below this → skip + flag (plan §6.3)
BAND_N_ACCEPT = 30  # the pass band requires N ≥ 30 windows (research-r8)
DECISIONS_FP_BAND = 0.05  # R1∧R3 conjunction: decisions-FP ≤ 5% on N ≥ 30 (DE2E-3)
PROCESS_ROUTING_WARN_N = 20  # R3 band is warn-only until n ≥ 20 (research-r8)

POINT_CLASSES = ("decision", "event", "claim")  # classes that hit the graph as points
EVAL_CLASSES = ("decision", "event", "claim", "process")  # per-class eval set

_FUZZY_MIN = 0.6  # word-overlap floor for a fuzzy match (aries_extraction pattern)


# ── Fuzzy matching (aries_extraction.py lineage) ────────────────────────────

def _norm(text: str) -> str:
    t = re.sub(r"[^\w\s]", "", (text or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _fuzzy_score(a: str, b: str) -> float:
    """Containment/word-overlap score in [0, 1] (0 = no overlap)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    words = set(na.split())
    if not words:
        return 0.0
    return len(words & set(nb.split())) / len(words)


def _fuzzy_match(a: str, b: str, min_overlap: float = _FUZZY_MIN) -> bool:
    return _fuzzy_score(a, b) >= min_overlap


def _window_texts(win: Window) -> dict[int, str]:
    return {i: t for i, t in enumerate(win.edus)}


def _match_items(
    gold_labels: list[Label],
    pred_labels: list[Label],
    gold_texts: dict[int, str],
    pred_texts: dict[int, str],
) -> tuple[list[tuple[Label, Label]], list[Label], list[Label]]:
    """Match gold→pred labels within one window (one label per EDU, first wins).

    Pass 1 anchors on edu_index (aligned labeling — the common case). Pass 2
    greedily fuzzy-matches the remainder by edu text (shifted labeling).

    Returns (pairs, gold_fn, pred_fp): pairs = matched (gold, pred); gold_fn =
    gold items with no pred match; pred_fp = pred items matched to no gold.
    """
    gold_by_idx: dict[int, Label] = {}
    for g in gold_labels:
        gold_by_idx.setdefault(g.edu_index, g)
    pred_by_idx: dict[int, Label] = {}
    for p in pred_labels:
        pred_by_idx.setdefault(p.edu_index, p)

    pairs: list[tuple[Label, Label]] = []
    used_pred: set[int] = set()
    for gidx, g in gold_by_idx.items():
        p = pred_by_idx.get(gidx)
        if p is not None:
            pairs.append((g, p))
            used_pred.add(gidx)

    matched_gold_idx = {g.edu_index for g, _ in pairs}
    matched_pred_idx = set(used_pred)
    remaining_gold = [g for idx, g in gold_by_idx.items() if idx not in matched_gold_idx]
    remaining_pred = [
        p for idx, p in pred_by_idx.items() if idx not in matched_pred_idx
    ]

    for g in remaining_gold:
        g_text = gold_texts.get(g.edu_index, "")
        best: Label | None = None
        best_score = 0.0
        for p in remaining_pred:
            p_text = pred_texts.get(p.edu_index, "")
            score = _fuzzy_score(g_text, p_text)
            if score > best_score:
                best, best_score = p, score
        if best is not None and best_score >= _FUZZY_MIN:
            pairs.append((g, best))
            remaining_pred.remove(best)

    matched_gold = {g.edu_index for g, _ in pairs}
    matched_pred = {p.edu_index for _, p in pairs}
    gold_fn = [g for idx, g in gold_by_idx.items() if idx not in matched_gold]
    pred_fp = [p for idx, p in pred_by_idx.items() if idx not in matched_pred]
    return pairs, gold_fn, pred_fp


# ── Validation ──────────────────────────────────────────────────────────────

def _validate(gold: list[Window], pred: list[Window]) -> None:
    if not gold or not pred:
        raise ValueError("compute_metrics: gold and pred must be non-empty")
    gids = [w.window_id for w in gold]
    if len(set(gids)) != len(gids):
        raise ValueError("compute_metrics: duplicate window_id in gold")
    pids = [w.window_id for w in pred]
    if len(set(pids)) != len(pids):
        raise ValueError("compute_metrics: duplicate window_id in pred")
    if len(gids) != len(pids) or set(gids) != set(pids):
        raise ValueError(
            "compute_metrics: gold and pred windows must match by window_id"
        )
    for w in gold:
        if w.gold_labels is None:
            raise ValueError(f"compute_metrics: gold window {w.window_id!r} has no labels")
        for label in w.gold_labels:
            if label.class_ not in CLASS_VOCAB:
                raise ValueError(
                    f"compute_metrics: gold label {label.edu_index} in "
                    f"{w.window_id!r} has unknown class {label.class_!r}"
                )
    for w in pred:
        for label in w.gold_labels or []:
            if label.class_ not in CLASS_VOCAB:
                raise ValueError(
                    f"compute_metrics: pred label {label.edu_index} in "
                    f"{w.window_id!r} has unknown class {label.class_!r}"
                )


# ── Public API ──────────────────────────────────────────────────────────────

# kappa: true re-export of tools/kappa.py (#945) — the SAME function object,
# not a copy (plan §8.3 slice 8: reuse the gate tooling). Returns None when
# the label sets share no EDU (degenerate/disjoint labeling) — the DE2E-1
# gate treats None as NOT_GREEN (tools/kappa.py convention, reconciled for
# the plan §6.3 signature).
kappa = _tools_kappa.kappa


def compute_metrics(
    gold: list[Window],
    pred: list[Window],
    *,
    kind_vocab: set[str] | None = None,
    window_types: dict[str, str] | None = None,
    min_events: dict[str, int] | None = None,
    confidences: dict[tuple[str, int], float] | None = None,
) -> MetricsReport:
    """Compute the full extraction-quality report over paired gold/pred windows.

    Windows pair by window_id (gold and pred must contain the same set).
    `gold` windows carry the gold labels; `pred` windows carry the model's
    predicted labels in their gold_labels field (plan §6.3 convention).

    Auxiliary inputs (all optional, all documented above):
      kind_vocab    — closed pack-kind vocab; pred kinds outside it are
                      auto-wrong for kind_correctness (R6 out-of-vocab rule)
      window_types  — {window_id: "design"|"operational"} for min_signal
                      (default: operational — fail-closed degenerate-empty)
      min_events    — per-window-type event floors for min_signal
                      (default: operational=1, design=0 — tools/min_signal)
      confidences   — {("window_id", edu_index): confidence} for ECE (A13),
                      in [0, 1]; absent → ece = NaN
    """
    _validate(gold, pred)
    gold_by_id = {w.window_id: w for w in gold}
    pred_by_id = {w.window_id: w for w in pred}
    # One label per EDU (documented convention): dedup first-wins ONCE, so every
    # metric (per-class, entity, mitigation, ECE, min-signal) sees the same set.
    gold_labels = {wid: _dedup(w.gold_labels or []) for wid, w in gold_by_id.items()}
    pred_labels = {wid: _dedup(w.gold_labels or []) for wid, w in pred_by_id.items()}

    # 1. Item-level matching per window.
    pairs_by_window: dict[str, list[tuple[Label, Label]]] = {}
    gold_fn_by_window: dict[str, list[Label]] = {}
    pred_fp_by_window: dict[str, list[Label]] = {}
    for wid, gw in gold_by_id.items():
        pw = pred_by_id[wid]
        pairs, gfn, pfp = _match_items(
            gold_labels[wid],
            pred_labels[wid],
            _window_texts(gw),
            _window_texts(pw),
        )
        pairs_by_window[wid] = pairs
        gold_fn_by_window[wid] = gfn
        pred_fp_by_window[wid] = pfp

    # 2. Per-class contingency (never blended) + the window-N rule.
    gold_windows_n: dict[str, int] = {c: 0 for c in EVAL_CLASSES}
    for wid in gold_by_id:
        present = {label.class_ for label in gold_labels[wid]}
        for c in EVAL_CLASSES:
            if c in present:
                gold_windows_n[c] += 1

    stats = {c: {"tp": 0, "fp": 0, "fn": 0} for c in EVAL_CLASSES}
    for pairs in pairs_by_window.values():
        for g, p in pairs:
            gc, pc = g.class_, p.class_
            if gc in EVAL_CLASSES and pc in EVAL_CLASSES:
                if gc == pc:
                    stats[gc]["tp"] += 1
                else:
                    stats[gc]["fn"] += 1
                    stats[pc]["fp"] += 1
            elif gc in EVAL_CLASSES:
                stats[gc]["fn"] += 1  # pred said nothing for a gold point
            elif pc in EVAL_CLASSES:
                stats[pc]["fp"] += 1  # pred claimed a point where gold said none
    for gfn in gold_fn_by_window.values():
        for g in gfn:
            stats[g.class_]["fn"] += 1
    for pfp in pred_fp_by_window.values():
        for p in pfp:
            stats[p.class_]["fp"] += 1

    per_class: dict[str, dict[str, float]] = {}
    per_class_n: dict[str, int] = {}
    for c in EVAL_CLASSES:
        per_class_n[c] = gold_windows_n[c]
        if gold_windows_n[c] < CLASS_N_MIN:
            # Per-class N rule (plan §6.3): skip + flag via per_class_n.
            per_class[c] = {"p": float("nan"), "r": float("nan"), "f1": float("nan")}
            continue
        tp, fp, fn = stats[c]["tp"], stats[c]["fp"], stats[c]["fn"]
        p = tp / (tp + fp) if (tp + fp) else float("nan")
        r = tp / (tp + fn) if (tp + fn) else float("nan")
        f1 = 2 * p * r / (p + r) if tp else 0.0  # no recall → F1 floored to 0
        per_class[c] = {"p": p, "r": r, "f1": f1}

    # 3. Mitigation class (R9 — relation-level, DE2E-11 canonical probe).
    gold_mit, pred_mit = _mitigation_lists(gold_labels, pred_labels)
    per_class_n["mitigation"] = sum(
        1 for wid in gold_by_id if _labels_have_mitigation(gold_labels[wid])
    )
    mit_tp = _match_mitigations(gold_mit, pred_mit)
    mit_fp = len(pred_mit) - mit_tp
    mit_fn = len(gold_mit) - mit_tp
    if per_class_n["mitigation"] < CLASS_N_MIN:
        per_class["mitigation"] = {"p": float("nan"), "r": float("nan"), "f1": float("nan")}
        mitigation_recall = float("nan")
    else:
        p = mit_tp / (mit_tp + mit_fp) if (mit_tp + mit_fp) else float("nan")
        r = mit_tp / (mit_tp + mit_fn) if (mit_tp + mit_fn) else float("nan")
        f1 = 2 * p * r / (p + r) if mit_tp else 0.0
        per_class["mitigation"] = {"p": p, "r": r, "f1": f1}
        mitigation_recall = r

    # 4. Routing + property rates (computed on the matched pairs).
    pair_map_by_window = {
        wid: {id(g): p for g, p in pairs} for wid, pairs in pairs_by_window.items()
    }
    layer_denom, layer_ok = 0, 0
    atomicity_denom, atomicity_ok = 0, 0
    citation_denom, citation_ok = 0, 0
    kind_denom, kind_ok = 0, 0
    for wid, gw in gold_by_id.items():
        p_by_g = pair_map_by_window[wid]
        for g in gold_labels[wid]:
            p = p_by_g.get(id(g))
            if g.class_ == "none":
                continue
            layer_denom += 1
            routed_ok = (g.class_ in POINT_CLASSES) == (
                p is not None and p.class_ in POINT_CLASSES
            )
            if routed_ok:
                layer_ok += 1
            if p is None:
                continue
            if g.atomicity:
                atomicity_denom += 1
                if p.atomicity:
                    atomicity_ok += 1
            if g.source_ref is not None:
                citation_denom += 1
                if (
                    p.source_ref is not None
                    and _fuzzy_match(p.source_ref, g.source_ref)
                ):
                    citation_ok += 1
            if g.kind is not None:
                kind_denom += 1
                if (
                    p.kind is not None
                    and p.kind == g.kind
                    and (kind_vocab is None or p.kind in kind_vocab)
                ):
                    kind_ok += 1

    layer_correct = _rate(layer_ok, layer_denom)
    atomicity = _rate(atomicity_ok, atomicity_denom)
    citation_correctness = _rate(citation_ok, citation_denom)
    kind_correctness = _rate(kind_ok, kind_denom)

    # 5. Entity P/R (R6 / A12) — class-agnostic item-level.
    ent_gold = sum(
        sum(1 for g in gold_labels[wid] if g.class_ in POINT_CLASSES)
        for wid in gold_by_id
    )
    ent_pred = sum(
        sum(1 for p in pred_labels[wid] if p.class_ in POINT_CLASSES)
        for wid in pred_by_id
    )
    ent_matched = sum(
        sum(1 for g, p in pairs if g.class_ in POINT_CLASSES and p.class_ in POINT_CLASSES)
        for pairs in pairs_by_window.values()
    )
    entity_p_r = (_rate(ent_matched, ent_pred), _rate(ent_matched, ent_gold))

    # 6. Empty rate (A7) — fraction of pred windows with no point-class items.
    empty_windows = sum(
        1
        for wid in pred_by_id
        if not any(label.class_ in POINT_CLASSES for label in pred_labels[wid])
    )
    empty_rate = _rate(empty_windows, len(pred_by_id))

    # 7. ECE (A13) — 0.1 bins over comparable confident EDUs.
    ece = _expected_calibration_error(gold_labels, pred_labels, confidences or {})

    # 8. Minimum-signal (spec §5.8/§6) — per window type, ALL windows pass.
    wt_of = window_types or {}
    min_signal: dict[str, dict] = {}
    for wid, pw in pred_by_id.items():
        wt = wt_of.get(wid, "operational")
        if wt not in ("design", "operational"):
            raise ValueError(
                f"compute_metrics: unknown window_type {wt!r} for {wid!r} "
                f"— must be 'design' or 'operational'"
            )
        entry = min_signal.setdefault(wt, {"passed": True, "n_windows": 0})
        entry["n_windows"] += 1
        result = min_signal_check(
            pred_labels[wid],
            wt,
            min_events.get(wt) if min_events else None,
        )
        if not result.passed:
            entry["passed"] = False
    min_signal_report = {wt: entry["passed"] for wt, entry in min_signal.items()}

    # 9. Process routing (R3) — gold process items never hit the graph.
    process_denom, process_ok = 0, 0
    for wid, gw in gold_by_id.items():
        p_by_g = pair_map_by_window[wid]
        for g in gold_labels[wid]:
            if g.class_ != "process":
                continue
            process_denom += 1
            p = p_by_g.get(id(g))
            if p is None or p.class_ not in POINT_CLASSES:
                process_ok += 1
    process_routing = _rate(process_ok, process_denom)

    # 10. R1∧R3 conjunction (spec §6 — THE decision-class test).
    r1r3_conjunction = _r1r3_conjunction(gold_labels, pred_labels)

    return MetricsReport(
        per_class=per_class,
        per_class_n=per_class_n,
        layer_correct=layer_correct,
        atomicity=atomicity,
        citation_correctness=citation_correctness,
        kind_correctness=kind_correctness,
        entity_p_r=entity_p_r,
        empty_rate=empty_rate,
        ece=ece,
        mitigation_recall=mitigation_recall,
        min_signal=min_signal_report,
        r1r3_conjunction=r1r3_conjunction,
        process_routing=process_routing,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _dedup(labels: list[Label]) -> list[Label]:
    """One label per EDU (documented convention): first label at an index wins."""
    by_idx: dict[int, Label] = {}
    for label in labels:
        by_idx.setdefault(label.edu_index, label)
    return list(by_idx.values())


def _rate(ok: int, total: int) -> float:
    return ok / total if total else float("nan")


def _labels_have_mitigation(labels: list[Label]) -> bool:
    return any(
        rel.type == "MITIGATES"
        for label in labels
        for rel in label.relations
    )


def _mitigation_lists(
    gold_labels: dict[str, list[Label]],
    pred_labels: dict[str, list[Label]],
) -> tuple[list[tuple[str, int | None, str | int | None]], list[tuple[str, int | None, str | int | None]]]:
    """(window_id, source, target) per MITIGATES relation, gold and pred."""

    def collect(labels_by_id: dict[str, list[Label]]) -> list[tuple[str, int | None, str | int | None]]:
        out = []
        for wid, labels in labels_by_id.items():
            for label in labels:
                for rel in label.relations:
                    if rel.type == "MITIGATES":
                        out.append((wid, rel.source, rel.target))
        return out

    return collect(gold_labels), collect(pred_labels)


def _match_mitigations(
    gold_mit: list[tuple[str, int | None, str | int | None]],
    pred_mit: list[tuple[str, int | None, str | int | None]],
) -> int:
    """Gold mitigations matched by pred — keyed on the canonical edge identity.

    A gold MITIGATES is matched iff a pred MITIGATES targets the same edge
    identity (window_id, target "[X→A]") — the deterministic probe key
    (DE2E-11; source-EDU shifts do not break the identity match).
    """
    pred_by_target: dict[tuple[str, str | int | None], int] = {}
    for wid, _src, tgt in pred_mit:
        pred_by_target[(wid, tgt)] = pred_by_target.get((wid, tgt), 0) + 1
    matched = 0
    for wid, _src, tgt in gold_mit:
        if pred_by_target.get((wid, tgt), 0) > 0:
            matched += 1
            pred_by_target[(wid, tgt)] -= 1
    return matched


def _expected_calibration_error(
    gold_labels: dict[str, list[Label]],
    pred_labels: dict[str, list[Label]],
    confidences: dict[tuple[str, int], float],
) -> float:
    """ECE over comparable confident EDUs (gold + pred label at the same EDU).

    10 bins of width 0.1 (the telemetry confidence_histogram shape, §6.1).
    Correctness = pred class == gold class. NaN when no comparable EDUs carry
    a confidence. Out-of-range confidences are clamped to [0, 1].
    """
    if not confidences:
        return float("nan")
    n_bin = [0] * 10
    acc_bin = [0] * 10
    conf_bin = [0.0] * 10
    n_total = 0
    for (wid, idx), conf in confidences.items():
        if conf is None:
            continue
        conf = max(0.0, min(1.0, conf))  # out-of-range → clamped to [0, 1]
        if wid not in gold_labels or wid not in pred_labels:
            continue
        g = next((l for l in gold_labels[wid] if l.edu_index == idx), None)
        p = next((l for l in pred_labels[wid] if l.edu_index == idx), None)
        if g is None or p is None:
            continue
        b = min(9, max(0, int(conf * 10)))
        n_bin[b] += 1
        acc_bin[b] += 1 if g.class_ == p.class_ else 0
        conf_bin[b] += conf
        n_total += 1
    if n_total == 0:
        return float("nan")
    ece = 0.0
    for b in range(10):
        if n_bin[b] == 0:
            continue
        acc = acc_bin[b] / n_bin[b]
        conf = conf_bin[b] / n_bin[b]
        ece += (n_bin[b] / n_total) * abs(acc - conf)
    return ece


def _r1r3_conjunction(
    gold_labels: dict[str, list[Label]],
    pred_labels: dict[str, list[Label]],
) -> dict[str, float | str]:
    """R1∧R3 conjunction stats — THE decision-class test (spec §6).

    decisions_fp: pred decisions whose gold label at the same EDU is not a
    decision (meta-discussion "we decided the extractor should support X",
    recommendations, misroutes — FM2; an EDU with NO gold label also counts
    as FP). Band: pass ≤ 5% on N ≥ 30; fail > 5% on N ≥ 30; watch N < 30
    or when no decisions were predicted (thresholds.yaml R8 row, DE2E-3).
    """
    pred_decisions = 0
    decisions_fp = 0
    for wid, pred_wlabels in pred_labels.items():
        gold_by_idx = {label.edu_index: label for label in gold_labels[wid]}
        for p in pred_wlabels:
            if p.class_ != "decision":
                continue
            pred_decisions += 1
            g = gold_by_idx.get(p.edu_index)
            if g is None or g.class_ != "decision":
                decisions_fp += 1
    n_windows = len(pred_labels)
    rate = decisions_fp / pred_decisions if pred_decisions else float("nan")
    if pred_decisions == 0:
        # No decision-class signal to evaluate: the conjunction test cannot
        # fire. NOT a vacuous pass (an extractor that never emits decisions is
        # broken on the recall side — caught by per-class decision recall and
        # the minimum-signal/empty-rate guards); not a fail either (zero FPs).
        band = "watch"
    elif n_windows < BAND_N_ACCEPT:
        band = "watch"
    elif rate <= DECISIONS_FP_BAND:
        band = "pass"
    else:
        band = "fail"
    return {
        "decisions_fp_rate": rate,
        "decisions_fp": float(decisions_fp),
        "n_decisions_pred": float(pred_decisions),
        "n_windows": float(n_windows),
        "band": band,
    }
