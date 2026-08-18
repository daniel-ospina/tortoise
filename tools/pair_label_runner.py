#!/usr/bin/env python3
"""pair_label_runner — dual-judge pair labeling + inter-judge agreement gate.

The durable recalibration artifact (#1349 T5): TWO DISTINCT LLM judges
(tests/model_adapters.py OpenRouterModel) classify every pair in
tests/fixtures/labeled_pairs.jsonl into IMPLIES | NEAR_DUPLICATE |
UNRELATED. Cohen's κ (the tools/kappa.py po/pe math) over the two label
sets gates the output:

    κ ≥ 0.60 (KAPPA_GREEN)  → GREEN — agreed labels are emitted as final
    κ < 0.60                → NOT_GREEN — disagreement pairs are flagged
                              for OWNER adjudication (an optional third
                              judge --adjudicator breaks the ties first)

Distinct-judge requirement: the two judges must be DIFFERENT models —
same-model judges inflate κ and can make KAPPA_GREEN trivially green.
Enforced in the CLI and in label_pairs().

Single-judge failure (API error or unparseable output) ABORTS the run:
κ over one judge is vacuously 1.0, so partial single-judge labels are
never emitted and no output file is written.

Exit codes (uniform pipeline convention — judge_harness / kappa / min_signal):
0 = report emitted; 1 = operational error; 2 = gate-negative (κ < 0.60,
--strict).

Usage:
    python tools/pair_label_runner.py \
        --pairs tests/fixtures/labeled_pairs.jsonl \
        --judge-a deepseek-v4-pro --judge-b qwen3.8-max \
        --out labels.json
    python tools/pair_label_runner.py ... --adjudicator claude-opus-5
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Make the tools package importable when run directly (python tools/xxx.py).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.kappa import KAPPA_GREEN  # noqa: E402 — single source of truth

#: The pair-label vocabulary shared with the fixture schema.
LABEL_VOCAB = ("IMPLIES", "NEAR_DUPLICATE", "UNRELATED")

#: Distinct default judges (different model families — judge independence).
DEFAULT_JUDGE_A = "deepseek-v4-pro"
DEFAULT_JUDGE_B = "qwen3.8-max"

#: Judge model resolution: MODELS registry key → OpenRouterModel; any other
#: value is passed through as a raw OpenRouter model id.
from tests.model_adapters import MODELS, OpenRouterModel  # noqa: E402


class PairLabelError(ValueError):
    """Invalid inputs or a judge failure — the run must abort."""


def kappa(a_labels: list[str], b_labels: list[str]) -> float:
    """Cohen's κ over two equal-length, index-aligned label sequences.

    Mirrors the tools/kappa.py po/pe formula (plan: "reuse the kappa math
    pattern") on the FULL pair set — pair labeling requires every pair to
    be judged by both judges, so the "intersection" of the window-based
    implementation is the whole sequence here.

    pe == 1.0 (a judge used a single category): κ = 1.0 iff po == 1.0,
    else 0.0 — identical verdicts are perfect agreement, never a NaN.
    """
    if not a_labels or len(a_labels) != len(b_labels):
        raise PairLabelError(
            "kappa requires equal-length non-empty label sequences "
            f"(got {len(a_labels)} vs {len(b_labels)})"
        )
    n = len(a_labels)
    po = sum(1 for a, b in zip(a_labels, b_labels) if a == b) / n
    a_counts = Counter(a_labels)
    b_counts = Counter(b_labels)
    pe = sum((a_counts[c] / n) * (b_counts[c] / n)
             for c in set(a_counts) | set(b_counts))
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def decide(kappa_value: float | None) -> dict:
    """KAPPA_GREEN band semantics (tools/kappa.py): ≥ 0.60 → GREEN."""
    if kappa_value is None:
        return {"verdict": "NOT_GREEN",
                "reason": "no comparable labels — gate not green"}
    if kappa_value >= KAPPA_GREEN:
        return {"verdict": "GREEN",
                "reason": f"kappa {kappa_value:.3f} >= {KAPPA_GREEN}"}
    return {"verdict": "NOT_GREEN",
            "reason": f"kappa {kappa_value:.3f} < {KAPPA_GREEN} — disagreement "
                      "pairs need owner adjudication"}


def _parse_label(text: str, judge_id: str, pair_index: int) -> str:
    """Extract the label from a judge's answer.

    Word-boundary match over the label vocabulary. A bare label (the prompt
    demands "Reply with only the label word") or a free-text answer
    mentioning exactly ONE label token parses; an answer containing
    MULTIPLE label tokens (e.g. "NEAR_DUPLICATE — the passage implies …")
    is AMBIGUOUS and aborts fail-closed — a silently misattributed label
    would corrupt the durable recalibration artifact, so ambiguity is never
    resolved by guesswork.
    """
    import re
    upper = text.upper()
    found = [label for label in LABEL_VOCAB
             if re.search(rf"\b{label}\b", upper)]
    if len(found) == 1:
        return found[0]
    raise PairLabelError(
        f"judge {judge_id!r} returned an ambiguous label for pair "
        f"{pair_index}: {text!r} — found {sorted(found) or 'no label token'}; "
        "aborting (no partial labels emitted)"
    )


def _judge_one(judge: Any, pair: dict, index: int) -> str:
    system = (
        "You are an expert annotator for an epistemic knowledge-graph "
        "engine. Classify the RELATION between two text passages. "
        "Answer with exactly one of the labels:\n"
        "  IMPLIES — passage A entails or paraphrases passage B (same "
        "proposition, possibly different vocabulary)\n"
        "  NEAR_DUPLICATE — A and B are near-identical copies or trivial "
        "rewrites of the same content\n"
        "  UNRELATED — A and B do not express the same proposition\n"
        "Reply with only the label word."
    )
    user = f"PASSAGE A:\n{pair['content_a']}\n\nPASSAGE B:\n{pair['content_b']}"
    raw = judge.complete(system=system, user=user)
    return _parse_label(raw, judge.id, index)


def label_pairs(
    pairs: list[dict],
    judge_a: Any,
    judge_b: Any,
    adjudicator: Any | None = None,
    *,
    out_path: str | None = None,
) -> dict:
    """Run the dual-judge labeling pass and build the gate report.

    judge objects expose ``.id`` and ``.complete(*, system, user) -> str``
    (OpenRouterModel-compatible). Any judge failure aborts with
    PairLabelError — partial output is never written.

    Returns the report dict; when ``out_path`` is given the report is also
    written there (only after a fully successful pass).
    """
    if not pairs:
        raise PairLabelError("empty pair set — nothing to label")
    if judge_a.id == judge_b.id:
        raise PairLabelError(
            f"judges must be DISTINCT models — both are {judge_a.id!r} "
            "(same-model judges inflate kappa)"
        )

    labels_a: list[str] = []
    labels_b: list[str] = []
    try:
        for index, pair in enumerate(pairs):
            labels_a.append(_judge_one(judge_a, pair, index))
            labels_b.append(_judge_one(judge_b, pair, index))
    except Exception as exc:
        if isinstance(exc, PairLabelError):
            raise
        raise PairLabelError(
            f"judge API failure — aborting before any output: {exc}"
        ) from exc

    k = kappa(labels_a, labels_b)
    decision = decide(k)

    disagreements = [i for i, (a, b) in enumerate(zip(labels_a, labels_b))
                     if a != b]

    # Owner adjudication path: an optional third judge labels ONLY the
    # disagreement pairs; the gate verdict still reflects the primary pair.
    adjudicated: dict[int, str] = {}
    adjudicator_used = adjudicator is not None
    if adjudicator is not None and disagreements:
        if adjudicator.id in (judge_a.id, judge_b.id):
            raise PairLabelError(
                f"adjudicator {adjudicator.id!r} must differ from the primary "
                "judges"
            )
        try:
            for i in disagreements:
                adjudicated[i] = _judge_one(adjudicator, pairs[i], i)
        except Exception as exc:
            if isinstance(exc, PairLabelError):
                raise
            raise PairLabelError(
                f"adjudicator API failure — aborting before any output: {exc}"
            ) from exc

    per_pair = []
    for i, pair in enumerate(pairs):
        agreed = labels_a[i] == labels_b[i]
        if agreed:
            final_label = labels_a[i]
            needs_review = False
            is_adjudicated = False
        elif i in adjudicated:
            final_label = adjudicated[i]
            needs_review = False
            is_adjudicated = True
        else:
            final_label = None
            needs_review = True
            is_adjudicated = False
        per_pair.append({
            "index": i,
            "content_a": pair["content_a"],
            "content_b": pair["content_b"],
            "band": pair.get("band"),
            "label": pair.get("label"),  # ground truth (fixture), for the owner
            "judge_a": labels_a[i],
            "judge_b": labels_b[i],
            "agreed": agreed,
            "final_label": final_label,
            "needs_human_review": needs_review,
            "adjudicated": is_adjudicated,
        })

    report = {
        "fixture": str(pairs[0].get("_source", "")),
        "n_pairs": len(pairs),
        "judges": {"a": judge_a.id, "b": judge_b.id,
                   "adjudicator": adjudicator.id if adjudicator else None},
        "kappa": k,
        "verdict": decision["verdict"],
        "decision": decision["reason"],
        "pairs": per_pair,
        "disagreements": disagreements,
        "adjudication": {
            "used": adjudicator_used,
            "n_resolved": len(adjudicated),
            "n_unresolved": len(disagreements) - len(adjudicated),
        },
    }

    if out_path:
        Path(out_path).write_text(json.dumps(report, indent=2) + "\n")
    return report


def _build_judge(model_id: str) -> Any:
    """OpenRouterModel for a MODELS-registry key or a raw OpenRouter id."""
    if model_id in MODELS:
        return MODELS[model_id]()
    return OpenRouterModel(model_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pair_label_runner",
        description="Dual-judge pair labeling + Cohen's κ agreement gate "
                    "(#1349 labeled-pair calibration).",
    )
    parser.add_argument("--pairs", required=True,
                        help="labeled-pairs JSONL (tests/fixtures/labeled_pairs.jsonl)")
    parser.add_argument("--judge-a", default=DEFAULT_JUDGE_A,
                        help=f"judge A model (default {DEFAULT_JUDGE_A})")
    parser.add_argument("--judge-b", default=DEFAULT_JUDGE_B,
                        help=f"judge B model (default {DEFAULT_JUDGE_B})")
    parser.add_argument("--adjudicator", default=None,
                        help="optional third judge that labels disagreement "
                             "pairs (owner adjudication aid)")
    parser.add_argument("--out", default=None,
                        help="write the gate report JSON to this file")
    parser.add_argument("--strict", action="store_true",
                        help="exit 2 when κ < 0.60 (CI enforcement)")
    args = parser.parse_args(argv)

    try:
        pairs = []
        for lineno, line in enumerate(
                Path(args.pairs).read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                pair = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PairLabelError(f"{args.pairs}:{lineno}: invalid JSON: {exc}")
            if not isinstance(pair, dict) or "content_a" not in pair or \
                    "content_b" not in pair:
                raise PairLabelError(
                    f"{args.pairs}:{lineno}: rows need content_a/content_b")
            pair["_source"] = args.pairs
            pairs.append(pair)
        judge_a = _build_judge(args.judge_a)
        judge_b = _build_judge(args.judge_b)
        if judge_a.id == judge_b.id:
            raise PairLabelError(
                f"judges must be DISTINCT models — both resolve to "
                f"{judge_a.id!r} (same-model judges inflate kappa)"
            )
        adjudicator = _build_judge(args.adjudicator) if args.adjudicator else None
        report = label_pairs(pairs, judge_a, judge_b, adjudicator,
                             out_path=args.out)
    except (OSError, PairLabelError) as exc:
        print(f"pair_label_runner: error: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(report, indent=2)
    if not args.out:
        print(payload)

    if args.strict and report["verdict"] != "GREEN":
        print(f"pair_label_runner: gate {report['verdict']} — "
              f"{report['decision']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
