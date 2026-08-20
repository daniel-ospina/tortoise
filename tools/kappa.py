#!/usr/bin/env python3
"""kappa — inter-judge agreement + gate decision for the 2-window validation (epic #909 slice 1a, #945).

Computes Cohen's κ and the nothing-verdict agreement on two label sets
(labeled-window JSON from tools/judge_harness.py), then applies the DE2E-1
gate semantics (plan §7):

    κ ≥ 0.60                                    → GREEN
    0.50 ≤ κ < 0.60 (middle band)               → NOT_GREEN — expand labeling
                                                  to more windows before
                                                  re-evaluating; the owner may
                                                  proceed to adjudication but
                                                  the gate is not satisfied
    κ < 0.50                                    → REVISE — rubric revision;
                                                  the workflow stops and the
                                                  rubric is amended

Green additionally requires the nothing-verdict agreement (both judges said
"nothing" — class "none" — on the same EDUs; Jaccard set agreement, threshold
configurable, default 1.0) and, for operational windows, the minimum-signal
assertion (tools/min_signal.py — DE2E-1 neg (b)). The window type is inferred
from the labeled windows' own window_type field (hard error on conflict); a
GREEN verdict is never emitted when the assertion was not evaluated.

Exit codes (uniform pipeline convention — judge_harness / kappa / min_signal):
0 = success (verdict is a documented GO/REVISE decision, machine-readable in
   the report JSON); 1 = operational error; 2 = gate-negative decision under
   --strict (NOT_GREEN / REVISE).

The verdict is a documented GO/REVISE decision, not a red CI: the script
exits 0 on any successful computation. Pass --strict to exit 2 when the
verdict is not GREEN.

Agreement conventions (documented):
- κ is computed on the class verdicts over the INTERSECTION of labeled EDUs
  (both judges must have labeled the EDU). No overlap → kappa is null and the
  gate is NOT_GREEN (degenerate/disjoint labeling). (The plan §6.3 interface
  declares kappa() -> float; this slice documents the None convention — the
  slice-8 metrics.py implementation must reconcile.)
- pe == 1.0 (a judge used a single category): κ = 1.0 iff po == 1.0, else
  0.0 — identical verdicts are perfect agreement, never a NaN.
- nothing-verdict agreement = |A_none ∩ B_none| / |A_none ∪ B_none|;
  both none-sets empty → 1.0 (vacuously satisfied).
- Per-class agreement (recorded, no v1 threshold — DE2E-1) = specific
  agreement on the SHARED (intersection) EDU set:
  2·|both=c| / (|A=c ∩ common| + |B=c ∩ common|).
- Minimum-signal (DE2E-1 neg (b)): the assertion fails only when BOTH judges
  agree on nothing (both label sets below the floor) — if either judge's set
  emits ≥ N events, the window emitted events and the assertion passes (a
  judge-specific miss surfaces through κ, not min-signal).

Usage:
    python tools/kappa.py --judge-a labels_a.json --judge-b labels_b.json
    python tools/kappa.py --judge-a a.json --judge-b b.json \
        --window-type operational --min-events 1 --out report.json --strict
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any  # noqa: F401

# Make the tools package importable when run directly (python tools/kappa.py).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools import min_signal  # noqa: E402
from tools.judge_harness import CLASS_VOCAB, Label, LabeledWindow, RelationLabel  # noqa: E402

# Gate bands (plan DE2E-1 assertions)
KAPPA_GREEN = 0.60
KAPPA_REVISE = 0.50  # κ < 0.50 → rubric revision; 0.50 ≤ κ < 0.60 → middle band


class KappaError(ValueError):
    """Invalid inputs to the agreement computation."""


@dataclass
class GateDecision:
    verdict: str        # GREEN | NOT_GREEN | REVISE
    reason: str

    def to_json(self) -> dict:
        return {"verdict": self.verdict, "reason": self.reason}


def _indexed(labels: list[Label]) -> dict[int, Label]:
    return {label.edu_index: label for label in labels}


def kappa(a_labels: list[Label], b_labels: list[Label]) -> float | None:
    """Cohen's κ over the class verdicts of the two label sets.

    Only EDUs labeled by BOTH judges participate (the intersection). Returns
    None when the intersection is empty (no comparable verdicts).
    """
    a = _indexed(a_labels)
    b = _indexed(b_labels)
    common = sorted(a.keys() & b.keys())
    if not common:
        return None
    n = len(common)
    po = sum(1 for idx in common if a[idx].class_ == b[idx].class_) / n
    a_counts = Counter(a[idx].class_ for idx in common)
    b_counts = Counter(b[idx].class_ for idx in common)
    pe = sum((a_counts[c] / n) * (b_counts[c] / n) for c in set(a_counts) | set(b_counts))
    if pe == 1.0:
        # Single-category raters: identical verdicts are perfect agreement.
        # pe == 1.0 implies both judges use one identical category over the
        # intersection, which forces po == 1.0 — the else branch is a
        # defensive guard, not a reachable path (review P2, PR #975).
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def nothing_agreement(a_labels: list[Label], b_labels: list[Label]) -> tuple[float, dict[str, int]]:
    """Jaccard agreement on the "nothing" verdicts (class == "none").

    Returns (agreement, counts) where counts = {both, a_only, b_only} over the
    EDUs at least one judge labeled "none". Both empty → 1.0 (vacuous).
    """
    a_none = {label.edu_index for label in a_labels if label.class_ == "none"}
    b_none = {label.edu_index for label in b_labels if label.class_ == "none"}
    both = a_none & b_none
    union = a_none | b_none
    agreement = 1.0 if not union else len(both) / len(union)
    counts = {
        "both": len(both),
        "a_only": len(a_none - b_none),
        "b_only": len(b_none - a_none),
    }
    return agreement, counts


def per_class_agreement(a_labels: list[Label], b_labels: list[Label]) -> dict[str, float]:
    """Specific agreement per class on the SHARED (intersection) EDU set.

    Both the numerator and the totals are restricted to EDUs both judges
    labeled — same unit set as kappa() — so a judge labeling extra EDUs does
    not depress the recorded agreement (recorded for the calibration loop,
    no v1 threshold).
    """
    a = _indexed(a_labels)
    b = _indexed(b_labels)
    common = sorted(a.keys() & b.keys())
    result: dict[str, float] = {}
    for cls in CLASS_VOCAB:
        both = sum(1 for idx in common if a[idx].class_ == cls and b[idx].class_ == cls)
        a_total = sum(1 for idx in common if a[idx].class_ == cls)
        b_total = sum(1 for idx in common if b[idx].class_ == cls)
        if a_total + b_total > 0:
            result[cls] = (2 * both) / (a_total + b_total)
    return result


def gate_decision(
    kappa_value: float | None,
    nothing_agr: float,
    *,
    min_signal_passed: bool | None = None,
    nothing_threshold: float = 1.0,
) -> GateDecision:
    """DE2E-1 gate semantics: GREEN / NOT_GREEN / REVISE with a reason."""
    if kappa_value is None:
        return GateDecision(
            "NOT_GREEN",
            "no overlapping labels to compare (degenerate or disjoint labeling) "
            "— NOT green (DE2E-1)",
        )
    if kappa_value < KAPPA_REVISE:
        return GateDecision(
            "REVISE",
            f"kappa {kappa_value:.3f} < {KAPPA_REVISE} — rubric revision: the "
            "workflow stops and the rubric is amended (DE2E-1)",
        )
    if kappa_value < KAPPA_GREEN:
        return GateDecision(
            "NOT_GREEN",
            f"kappa {kappa_value:.3f} in the middle band "
            f"[{KAPPA_REVISE}, {KAPPA_GREEN}) — NOT green: expand labeling to "
            "more windows before re-evaluating (DE2E-1)",
        )
    if nothing_agr < nothing_threshold:
        return GateDecision(
            "NOT_GREEN",
            f"kappa {kappa_value:.3f} >= {KAPPA_GREEN} but nothing-verdict "
            f"agreement {nothing_agr:.3f} < {nothing_threshold} — NOT green "
            "(DE2E-1)",
        )
    if min_signal_passed is False:
        return GateDecision(
            "NOT_GREEN",
            f"kappa {kappa_value:.3f} >= {KAPPA_GREEN} but the minimum-signal "
            "assertion failed on an operational window — NOT green "
            "(DE2E-1 neg (b))",
        )
    if min_signal_passed is None:
        # Fail closed (review P2, PR #975): GREEN is never emitted when the
        # degenerate-empty guard was not evaluated. compare()'s post-hoc
        # override is defense-in-depth; direct callers get the same contract.
        return GateDecision(
            "NOT_GREEN",
            f"kappa {kappa_value:.3f} >= {KAPPA_GREEN} but the minimum-signal "
            "assertion was NOT evaluated (no window type) — NOT green "
            "(fail-closed, DE2E-1 neg (b))",
        )
    return GateDecision(
        "GREEN",
        f"kappa {kappa_value:.3f} >= {KAPPA_GREEN}, nothing-verdict agreement "
        f"{nothing_agr:.3f} >= {nothing_threshold}, minimum-signal "
        "satisfied — gate green (DE2E-1)",
    )


# ── Window-level comparison (the gate report) ───────────────────────────────

def _labels_of(window: LabeledWindow | dict) -> list[Label]:
    """Accept a LabeledWindow dataclass or a loaded harness JSON dict.

    The dict path validates label shape (mirroring judge_harness.parse_labels)
    and raises KappaError on malformed input instead of crashing with KeyError.
    """
    if isinstance(window, dict):
        raw = window.get("labels", [])
        if not isinstance(raw, list):
            raise KappaError("'labels' must be an array")
        labels: list[Label] = []
        emitted: set[int] = set()
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise KappaError(f"labels[{i}]: not an object")
            if "edu_index" not in item:
                raise KappaError(f"labels[{i}]: missing 'edu_index'")
            idx = item["edu_index"]
            if isinstance(idx, bool) or not isinstance(idx, int):
                raise KappaError(f"labels[{i}]: 'edu_index' must be an int")
            if "class" not in item:
                raise KappaError(f"labels[{i}]: missing 'class'")
            cls = item["class"]
            if not isinstance(cls, str) or cls not in CLASS_VOCAB:
                raise KappaError(
                    f"labels[{i}]: 'class' must be one of {', '.join(CLASS_VOCAB)}"
                )
            if idx in emitted:
                raise KappaError(f"labels[{i}]: duplicate label for edu_index {idx}")
            emitted.add(idx)
            rels = item.get("relations", [])
            if not isinstance(rels, list):
                raise KappaError(f"labels[{i}]: 'relations' must be an array")
            relations: list[RelationLabel] = []
            for j, rel in enumerate(rels):
                if not isinstance(rel, dict) or not isinstance(rel.get("type"), str):
                    raise KappaError(f"labels[{i}].relations[{j}]: missing 'type'")
                relations.append(
                    RelationLabel(
                        type=rel["type"],
                        source=rel.get("source"),
                        target=rel.get("target"),
                        bias=rel.get("bias"),
                    )
                )
            labels.append(
                Label(
                    edu_index=idx,
                    class_=cls,
                    kind=item.get("kind"),
                    atomicity=item.get("atomicity", True),
                    source_ref=item.get("source_ref"),
                    relations=relations,
                )
            )
        return labels
    return list(window.labels)


def _window_meta(window: LabeledWindow | dict) -> dict:
    if isinstance(window, dict):
        return {
            "window_id": window.get("window_id"),
            "judge": window.get("judge"),
            "n_edus": window.get("n_edus"),
            "n_labels": len(window.get("labels", [])),
            "degenerate": bool(window.get("degenerate", False)),
            "incomplete": bool(window.get("incomplete", False)),
        }
    return {
        "window_id": window.window_id,
        "judge": window.judge,
        "n_edus": window.n_edus,
        "n_labels": len(window.labels),
        "degenerate": window.degenerate,
        "incomplete": window.incomplete,
    }


def compare(
    window_a: LabeledWindow | dict,
    window_b: LabeledWindow | dict,
    *,
    window_type: str | None = None,
    min_events: int | None = None,
    nothing_threshold: float = 1.0,
) -> dict:
    """Full gate report for two labeled windows (DE2E-1 steps 3-4).

    The minimum-signal assertion (DE2E-1 neg (b)) is evaluated whenever the
    window type is known: it is inferred from the windows' own window_type
    field when not passed explicitly (conflicting types → KappaError). A
    GREEN verdict is never emitted when the assertion was not evaluated.
    """
    a_labels = _labels_of(window_a)
    b_labels = _labels_of(window_b)

    k = kappa(a_labels, b_labels)
    nothing, nothing_counts = nothing_agreement(a_labels, b_labels)
    per_class = per_class_agreement(a_labels, b_labels)

    # Infer the window type from the labeled windows when not explicit.
    if window_type is None:
        types = []
        for w in (window_a, window_b):
            wt = w.get("window_type") if isinstance(w, dict) else w.window_type
            if wt is not None:
                types.append(wt)
        if types:
            if len(set(types)) > 1:
                raise KappaError(
                    f"windows disagree on window_type ({sorted(set(types))}) — "
                    "pass --window-type explicitly"
                )
            window_type = types[0]

    # Minimum-signal (DE2E-1 neg (b)): fails only when BOTH judges agree on
    # nothing (both label sets below the floor). If either judge's set emits
    # >= N events, the window emitted events and the assertion passes.
    min_signal_results: dict[str, dict] | None = None
    min_signal_passed: bool | None = None
    if window_type is not None or min_events is not None:
        wt = window_type or "operational"
        results = {}
        for name, labels in (("a", a_labels), ("b", b_labels)):
            result = min_signal.min_signal_check(labels, wt, min_events)
            results[name] = result.__dict__
        min_signal_results = results
        min_signal_passed = results["a"]["passed"] or results["b"]["passed"]

    decision = gate_decision(
        k,
        nothing,
        min_signal_passed=min_signal_passed,
        nothing_threshold=nothing_threshold,
    )
    if min_signal_results is None and decision.verdict == "GREEN":
        # Fail closed: never emit GREEN without the degenerate-empty guard.
        decision = GateDecision(
            "NOT_GREEN",
            "kappa and nothing-verdict agreement are green but the "
            "minimum-signal assertion was not evaluated (no window_type on "
            "the windows, none passed) — NOT green (DE2E-1 neg (b))",
        )

    # Fail closed on incomplete labeling (review P2, PR #975): a judge that
    # labels a subset of EDUs with perfect agreement must not produce GREEN —
    # the rubric instructs judges to never omit an EDU.
    if decision.verdict == "GREEN":
        incomplete = []
        for name, w in (("a", window_a), ("b", window_b)):
            flag = w.get("incomplete") if isinstance(w, dict) else w.incomplete
            if flag:
                incomplete.append(name)
        if incomplete:
            decision = GateDecision(
                "NOT_GREEN",
                f"kappa and nothing-verdict agreement are green but labeling "
                f"is incomplete (judge{'s' if len(incomplete) > 1 else ''} "
                f"{', '.join(incomplete)} omitted EDUs) — NOT green "
                "(DE2E-1, incomplete labeling)",
            )

    report = {
        "window_a": _window_meta(window_a),
        "window_b": _window_meta(window_b),
        "n_compared": len(_indexed(a_labels).keys() & _indexed(b_labels).keys()),
        "kappa": k,
        "nothing_agreement": nothing,
        "nothing": nothing_counts,
        "per_class_agreement": per_class,
        "min_signal": min_signal_results,
        "gate": decision.to_json(),
    }
    return report


def load_window(path: str) -> dict:
    """Load a harness labeled-window JSON."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise KappaError(f"{path}: not a JSON object")
    if "labels" not in data:
        raise KappaError(f"{path}: not a labeled-window JSON (missing 'labels')")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kappa",
        description="Inter-judge agreement (Cohen's κ + nothing-verdict) and "
        "the DE2E-1 gate decision for two labeled windows.",
    )
    parser.add_argument("--judge-a", required=True, help="labeled-window JSON "
                        "(judge A, from tools/judge_harness.py)")
    parser.add_argument("--judge-b", required=True, help="labeled-window JSON "
                        "(judge B)")
    parser.add_argument("--window-type", choices=min_signal.WINDOW_TYPES,
                        default=None, help="window type for the minimum-signal "
                        "assertion (skipped when omitted)")
    parser.add_argument("--min-events", type=int, default=None,
                        help="event floor for the minimum-signal assertion "
                        "(default: operational=1, design=0)")
    parser.add_argument("--nothing-threshold", type=float, default=1.0,
                        help="required nothing-verdict agreement for GREEN "
                        "(default: 1.0)")
    parser.add_argument("--out", help="write the gate report JSON to this "
                        "file (default: stdout)")
    parser.add_argument("--strict", action="store_true",
                        help="exit 2 when the verdict is not GREEN (CI "
                        "enforcement; default: exit 0 — the verdict is a "
                        "documented decision, not a red CI)")
    args = parser.parse_args(argv)

    try:
        report = compare(
            load_window(args.judge_a),
            load_window(args.judge_b),
            window_type=args.window_type,
            min_events=args.min_events,
            nothing_threshold=args.nothing_threshold,
        )
    except (OSError, ValueError) as exc:
        print(f"kappa: error: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(report, indent=2)
    try:
        if args.out:
            Path(args.out).write_text(payload + "\n")
        else:
            print(payload)
    except OSError as exc:
        print(f"kappa: error: {exc}", file=sys.stderr)
        return 1

    if args.strict and report["gate"]["verdict"] != "GREEN":
        print(f"kappa: gate {report['gate']['verdict']} — {report['gate']['reason']}",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
