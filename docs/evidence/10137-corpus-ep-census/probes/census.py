"""#10137 — read the census cells out of a run REPORT or a run CHECKPOINT.

The derivation is the runner's own (``measurement_posture.
summarize_belief_activation``) + the state function the report uses
(``belief_layer_state``) — this is NOT a second implementation, which is what
makes a ``PARTIAL`` computed from a checkpoint publishable: the cells are the
ones the report would carry.

    PYTHONPATH=. python \
      docs/evidence/10137-corpus-ep-census/probes/census.py <report.json|checkpoint.json>

Also prints the CLAIM-LEVEL coverage the question-level ratio cannot show:
``points_with_ep / (points_with_ep + points_unmeasured)``. The per-question
ratio answers "did the belief layer participate on this question at all"; the
claim ratio answers "on what share of claims". A run can read 24/24 on the
first and 9% on the second, and the second is the honest denominator for
"how much of this answer came from our epistemic layer".
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

from tools.longmem_eval import measurement_posture as mp  # noqa: E402


def cells(path: str) -> dict:
    doc = json.loads(pathlib.Path(path).read_text())
    if "outcomes" in doc and "methodology" in doc:            # a report
        summary = mp.of_report(doc)["activation"]
        source, n = "report", doc.get("n_questions")
        outcomes = doc.get("outcomes") or []
    else:                                                     # a checkpoint
        outcomes = doc.get("outcomes") or []
        summary = mp.summarize_belief_activation(outcomes, requested=True)
        source, n = "checkpoint", len(outcomes)

    q = summary.get("questions") or 0
    with_ep = summary.get("questions_with_ep") or 0
    covered = summary.get("points_with_ep")
    unmeasured = summary.get("points_unmeasured")
    claim_ratio = None
    if isinstance(covered, int) and isinstance(unmeasured, int) \
            and (covered + unmeasured):
        claim_ratio = covered / (covered + unmeasured)
    return {
        "source": source,
        "questions_in_run": n,
        "questions": q,
        "questions_with_ep": with_ep,
        "questions_with_ep_ratio": (with_ep / q) if q else None,
        "claim_coverage": {
            "points_with_ep": covered,
            "points_unmeasured": unmeasured,
            "ratio": claim_ratio,
        },
        "belief_layer": mp.belief_layer_state(
            requested=bool(summary.get("requested")), activation=summary),
        "cells": {k: summary.get(k) for k in (
            "measured", "questions_inert", "questions_neutral",
            "questions_off_neutral", "questions_contribution_unknown",
            "operators", "operators_unread", "factor_edges",
            "factor_edges_unread", "points_with_ep", "points_unmeasured",
            "points_off_neutral", "points_moved_off_neutral",
            "ep_ran", "failed")},
        "inert_by_reason": summary.get("inert_by_reason"),
        "inert_questions": summary.get("inert_questions"),
        "by_question_type": summary.get("by_question_type"),
        "errors": summary.get("errors"),
        "completed": [{
            "question_id": o.get("question_id"),
            "question_type": o.get("question_type"),
        } for o in outcomes if isinstance(o, dict)],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = cells(args.path)
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
