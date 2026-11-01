"""#10137 — read the census cells out of a run REPORT or a run CHECKPOINT.

The derivation is the runner's own (``measurement_posture.
summarize_belief_activation``) + the state function the report uses
(``belief_layer_state``) — this is NOT a second implementation, which is what
makes a ``PARTIAL`` computed from a checkpoint publishable: the cells are the
ones the report would carry.

    PYTHONPATH=. python \
      docs/evidence/10137-corpus-ep-census/probes/census.py <report.json|checkpoint.json>

Also prints the POINT-level coverage the question-level ratio cannot show:
``points_with_ep / (points_with_ep + points_unmeasured)``. The per-question
ratio answers "did the belief layer participate on this question at all"; the
point ratio answers "on what share of the graph". A run can read 24/24 on the
first and 9% on the second, and the second is the honest denominator for
"how much of this answer came from our epistemic layer".

⛔ It is a **FLOOR on claim coverage, NOT claim coverage** (#10137 review, P2).
The denominator is ``_COUNT_POINTS_TOTAL`` — every non-operator ``Point`` — and
the v2 ingest also writes episodic turn points and session-transcript chunks
(``ingest_v2.py:611-618``, ``:637-643``), which are not claims and can never
carry EP state, while the numerator can only ever be claims. A claim-only
denominator does not exist as a cell, so the key is published as
``point_coverage`` — a name that asserts only what was measured. (It shipped
as ``claim_coverage`` until that review: a machine-readable key asserting a
denominator the bundle's own README denies.)

The per-question block is published only when the input CARRIES the manifests
(``outcome.belief_activation``): an input with none reports
``per_question: null`` plus ``per_question_source`` saying so, never rows of
nulls — a null ``ep_ran`` must not be readable as a measured one. A PARTIAL
population publishes its rows AND ``per_question_unread``, the count that says
how many questions inside them were never measured (the same disclosure
``<key>_unread`` makes for the summed cells).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))

# No suppression directive here on purpose: ruff's E402 deliberately EXEMPTS an
# import that follows a `sys.path` modification (ruff PR #9047, v0.2.0), so a
# no-suppression-needed `E402` marker would be UNUSED and RUF100 reds the lint job.
from tools.longmem_eval import measurement_posture as mp


def _census_unread(manifest: object) -> bool:
    """Is this manifest's graph census UNREAD (absent, fail-open, or ``None``)?

    One rule for all four cells, taken from ``measurement_posture`` rather than
    re-derived here: a key in ``_UNKNOWN_PER_MANIFEST_KEYS`` is UNKNOWN when
    absent or ``None`` — AND by the module's own coercion, which refuses a
    ``bool`` and any non-numeric value (``_optional_int``: "a bool is not a
    count"). ``value is None`` alone was not the same predicate (#10137
    review, P3): a manifest carrying ``operators: true`` read as READ here and
    UNREAD for ``cells.operators_unread``, which is the disagreement this
    function exists to end. A manifest that is not a dict at all (no manifest)
    is unread by definition.
    """
    if not isinstance(manifest, dict):
        return True
    return any(mp._optional_int(manifest.get(k)) is None
               for k in mp._UNKNOWN_PER_MANIFEST_KEYS)


def cells(path: str) -> dict:
    doc = json.loads(pathlib.Path(path).read_text())
    outcomes = doc.get("outcomes") or []
    if "outcomes" in doc and "methodology" in doc:            # a report
        posture = mp.of_report(doc)
        if posture is None or posture.get("activation") is None:
            raise SystemExit(
                f"{path}: this report records NO measurement posture "
                "(methodology.measurement_posture absent or activation null) — "
                "there is no census to read, which is a state, not a zero")
        summary = posture["activation"]
        source = "report"
        # ``n_failed`` is ``len(failures)`` on a report (``report.py:1898``,
        # ``:2793``) and the list itself rides the report too
        # (``report.py:2792``). Read BOTH: the list gives the ids the union
        # below needs, the count is the authority when a list was pruned.
        reported_failed = doc.get("n_failed")
        failures = [f for f in (doc.get("failures") or []) if isinstance(f, dict)]
    else:                                                     # a checkpoint
        # ``requested`` is the caller's resolved ARM, never inferred from the
        # outcomes — but a checkpoint carries it in its fingerprint
        # (``activate_beliefs``, run.py:1742), so read it when it is there
        # rather than asserting True: a checkpoint written under a different
        # arm must not be reported as an armed run.
        fp = doc.get("fingerprint") or {}
        if not isinstance(fp, dict) or "activate_beliefs" not in fp:
            raise SystemExit(
                f"{path}: checkpoint carries no ``activate_beliefs`` "
                "fingerprint — the arm is UNKNOWN, not True")
        requested = bool(fp.get("activate_beliefs"))
        summary = mp.summarize_belief_activation(outcomes, requested=requested)
        source = "checkpoint"
        # The checkpoint's own failure list (``run.py:2797`` writes
        # ``"failures": failures`` at the top level). It is read HERE and not
        # left to the reader (#10137 review, P2): ``summarize_belief_activation``
        # only ever sees ``outcomes``, so a checkpoint from a CRASHED run — the
        # normal case for this probe, whose own bundle came from one — would
        # otherwise publish ``questions_with_ep / questions = N/N = 1.00`` and
        # ``activated`` over the survivors, with the ablation invisible. The
        # ratio's denominator is still the outcomes (that is the runner's own
        # aggregate); ``questions_in_run`` below is what makes the difference
        # between the two READABLE.
        failures = [f for f in (doc.get("failures") or []) if isinstance(f, dict)]
        # The checkpoint's list IS the authority here (``run.py:2797``); it is
        # never short of the truth, so no separate count is carried.
        reported_failed = None

    # EVERY question the run covered: the ratio's own denominator PLUS the
    # questions it excludes. The failure ids are counted as DISTINCT ids over
    # the two lists — never ``len(outcomes) + len(failures)`` (#10137 review,
    # P2). The overlap is a documented real shape, not a hypothetical: a qid
    # completed by one worker and failed by another lands in BOTH lists
    # (``report.py:1407-1410``), and ``_merge_checkpoint`` unions the two
    # independently, tombstones being present only when the writer knew it was
    # a ``--retry-failed`` re-attempt (``run.py:2699-2729``). Summing the
    # lengths published a 2-question population as a 3-question run, and the
    # gap the README calls "the disclosure" hid it — the gap was
    # ``questions_failed`` BY CONSTRUCTION.
    #
    # ``questions_failed`` is therefore the count of failures that actually
    # ABLATE the ratio: a qid that failed and then produced an outcome counts
    # once, as an outcome (it was recovered, and it is in the denominator).
    #
    # ⛔ The report arm's own ``n_questions`` is NOT used as the total: it is
    # ``len(outcomes)`` (``report.py:1106`` -> ``:2376``), so overriding with it
    # silently dropped the failure population in the arm that produces the
    # published artifact — F3 fixed nothing there (#10137 review, P2).
    outcome_ids = {o.get("question_id") for o in outcomes
                   if isinstance(o, dict) and o.get("question_id")}
    failed_ids = {f.get("question_id") for f in failures if f.get("question_id")}
    # A reported count larger than the id set is a failure population whose
    # ENTRY list the input pruned: those questions cannot be matched against an
    # outcome, and undercounting the ablation is the one direction that
    # resurrects ``N/N = 1.00``, so the shortfall is kept.
    #
    # ⛔ ``isinstance(x, int)`` is TRUE for a JSON ``true``, so the bool is
    # excluded explicitly — the same rule ``_optional_int`` applies to the
    # manifest cells below ("a bool is not a count"). ``n_failed: true`` used to
    # fabricate one ablated question (#10137 review, P3).
    unlisted = 0
    if (isinstance(reported_failed, int)
            and not isinstance(reported_failed, bool)):
        unlisted = max(reported_failed - len(failed_ids), 0)
    # The ablation is the LARGER of the two witnesses, never their SUM (#10137
    # review, P3): a failure list that repeats a qid is not a pruned list —
    # ``n_failed`` counts ENTRIES (``report.py:2793``) while ``failed_ids`` is
    # distinct ids, so adding the shortfall to the id count published a phantom
    # question for one duplicated entry. Both witnesses bound the same
    # quantity from below, so ``max`` is the honest combination.
    questions_failed = max(len(failed_ids - outcome_ids), unlisted)
    # DERIVED from the published denominator plus that ablation — never a
    # second, independent count of "how many questions". ``summary["questions"]``
    # is the runner's own ``len(rows)`` (``measurement_posture.py:339``), i.e.
    # ROWS, while the id sets above are distinct non-empty ids; an input with a
    # duplicate or absent ``question_id`` therefore made the two disagree and
    # could publish a total SMALLER than the ratio's own denominator (#10137
    # review, P3 — reachable from hand-built/foreign JSON only: ``run.py``'s
    # loader builds both lists dict-by-qid, and the vendored ``report.py`` grades
    # per-qid precisely because duplicate outcome entries are a documented shape
    # there, ``:1407-1412``). The fallback is ``len(rows)`` — the SAME basis the
    # runner's own count uses, never the id set, which is the count that
    # disagreed (#10137 review, P3). Adding the ablation to the denominator makes
    # ``questions_in_run - questions == questions_failed`` hold BY CONSTRUCTION
    # for every input, which is what the disclosure claims.
    reported_questions = summary.get("questions")
    denominator = (reported_questions
                   if isinstance(reported_questions, int)
                   and not isinstance(reported_questions, bool)
                   else len(rows))
    n = denominator + questions_failed

    # NOT ``or 0``: an ABSENT cell is UNKNOWN, and coercing it would publish a
    # fabricated "measured zero" — the rule ``measurement_posture`` states for
    # the summed cells (:387-390) applies to the headline pair too, so a cell a
    # different code version never emitted reads as unread, never as zero.
    q = summary.get("questions")
    with_ep = summary.get("questions_with_ep")
    covered = summary.get("points_with_ep")
    unmeasured = summary.get("points_unmeasured")
    claim_ratio = None
    if isinstance(covered, int) and isinstance(unmeasured, int) \
            and (covered + unmeasured):
        claim_ratio = covered / (covered + unmeasured)

    rows = [o for o in outcomes if isinstance(o, dict)]
    manifests = [o for o in rows if isinstance(o.get("belief_activation"), dict)]
    # A manifest PRESENT is not a census READ (#10137 review, P2). The
    # producer's fail-open shape (``{"ep_ran": False, "error": ...}`` — the one
    # ``measurement_posture`` documents at :85-94) carries a manifest and NO
    # census cells, so counting manifests published ``per_question_unread: 0``
    # over rows whose ``operators``/``points_with_ep`` are all ``null``, while
    # the SAME input's summed cells published ``points_with_ep_unread: >0`` —
    # two disclosures of one fact, disagreeing. Count the rows whose census was
    # NOT READ, by the summed cells' own rule: a key in
    # ``_UNKNOWN_PER_MANIFEST_KEYS`` is UNKNOWN when absent or ``None``. The
    # private constant and ``_optional_int`` are imported deliberately — they
    # are the ONE definition, and a change there must red this probe loudly
    # rather than drift from it.
    per_question_unread = sum(
        1 for o in rows if _census_unread(o.get("belief_activation")))
    if not manifests:
        # ``not manifests``, NOT ``rows and not manifests`` (#10137 review, P3):
        # an empty population has no manifest either, and the ``else`` branch
        # would then claim ``per_question_source: "outcome.belief_activation"``
        # — provenance from an input with no outcomes, one boundary over from
        # the fabricated zero this guard exists to prevent.
        # See the note on ``per_question`` below: an input with no manifest
        # anywhere has NO per-question census, and 24 rows of nulls would
        # read as a measured-and-null question. Publish the absence.
        per_question: list[dict] | None = None
        per_question_source = (
            "absent — no outcome in this input carries a "
            "belief_activation manifest (a non-armed run, or a report written "
            "before the projection carried it)")
    else:
        per_question = [{
            "question_id": o.get("question_id"),
            "question_type": o.get("question_type"),
            "operators": (o.get("belief_activation") or {}).get("operators"),
            "factor_edges": (o.get("belief_activation") or {}).get("factor_edges"),
            "points_with_ep": (o.get("belief_activation") or {}).get("points_with_ep"),
            "points_unmeasured": (o.get("belief_activation") or {}).get("points_unmeasured"),
            "points_off_neutral": (o.get("belief_activation") or {}).get("points_off_neutral"),
            "points_moved_off_neutral":
                (o.get("belief_activation") or {}).get("points_moved_off_neutral"),
            "ep_ran": (o.get("belief_activation") or {}).get("ep_ran"),
            "no_op_reason": (o.get("belief_activation") or {}).get("no_op_reason"),
            # A fail-open manifest's own diagnosis. Without it the row is
            # indistinguishable from a measured-and-null one except through
            # ``per_question_unread``; with it the reason the census is missing
            # is readable per question (#10137 review, P2).
            "error": (o.get("belief_activation") or {}).get("error"),
        } for o in rows]
        per_question_source = "outcome.belief_activation"

    return {
        "source": source,
        "questions_in_run": n,
        "questions_failed": questions_failed,
        "questions": q,
        "questions_with_ep": with_ep,
        "questions_with_ep_ratio": (
            (with_ep / q) if isinstance(with_ep, int) and isinstance(q, int)
            and q else None),
        # A FLOOR on claim coverage, not claim coverage (see the module
        # docstring); the name says only what the denominator is.
        "point_coverage": {
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
            "factor_edges_unread", "points_with_ep",
            # #10137 review (P2): the ``*_unread`` companions are the ONLY
            # thing that says how partial ``points_with_ep`` /
            # ``points_unmeasured`` are (``measurement_posture.
            # _UNKNOWN_PER_MANIFEST_KEYS`` publishes one per key). Publishing
            # the sum without them lets a census read on 1 of 24 questions
            # read exactly like a complete one.
            "points_with_ep_unread", "points_unmeasured",
            "points_unmeasured_unread",
            # ...and the SAME rule for the two contribution sums (#10137
            # review, P3): both are partial sums when their bucket census is
            # UNKNOWN, and ``measurement_posture`` publishes one ``_unread``
            # companion per key (:530-543). Without them a partial 185 is
            # indistinguishable from a complete one — and 185 is the headline
            # "0.67% moved" the artifact rests on.
            "points_off_neutral", "points_off_neutral_unread",
            "points_moved_off_neutral", "points_moved_off_neutral_unread",
            "ep_ran", "failed")},
        "inert_by_reason": summary.get("inert_by_reason"),
        "inert_questions": summary.get("inert_questions"),
        "by_question_type": summary.get("by_question_type"),
        "errors": summary.get("errors"),
        "completed": [{
            "question_id": o.get("question_id"),
            "question_type": o.get("question_type"),
        } for o in outcomes if isinstance(o, dict)],
        # The run's OWN per-question cells, so the artifact can be checked
        # question by question instead of only in aggregate.
        #
        # FAIL CLOSED (#10137 review, P2). ``o["belief_activation"]`` is only
        # carried by outcomes whose shape included it — the checkpoint write
        # always does (``run.py:5115-5116``), the report's projection does
        # since this review. On an input that carries NO manifest, emitting one
        # row per outcome would publish 24 rows of nulls under an aggregate
        # that still looks right: a null ``ep_ran`` /
        # ``points_with_ep`` would be indistinguishable from a measured one, and
        # the loss would be silent. So publish the ABSENCE and where it comes
        # from (the ``absent()`` discipline: a census nobody read is not a zero,
        # which is the same rule ``measurement_posture`` applies to summed
        # cells). A PARTIAL manifest population is not an absence — the rows
        # exist and ``per_question_unread`` says how many questions inside them
        # were never measured, exactly like ``<key>_unread``.
        "per_question_source": per_question_source,
        "per_question_unread": per_question_unread,
        "per_question": per_question,
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
