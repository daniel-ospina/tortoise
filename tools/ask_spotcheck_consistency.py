#!/usr/bin/env python3
"""Issue #2071 step 5/6 (I2): consistency harness — spot-check semantic
verdicts vs the graded eval's semantic verdicts on the shared population.

Both lanes call the SAME judge (``build_judge()`` → ``LLMJudge``, official
gpt-4o anscheck templates, temperature 0) with the SAME
(question_type, question, answer, hypothesis, abstention) inputs, so
agreement is expected BY CONSTRUCTION — this harness MEASURES it per
question on the 21-question spot-check composition (the shared
population). The graded-eval leg is the exact judge call the eval harness
makes (``tools/longmem_eval/run.py`` judge call site, ``run_evaluation``)
— the two lanes' judge calls are byte-identical; only the ``_abs`` marker
short-circuit in ``ask_spotcheck._grade`` (deterministic, precedes the
judge call) can differ. A second leg measures CI parity: the key-free
MockJudge (containment) verdict vs the semantic verdict on the same
answers — the expected-class flips are the point of the #2071 owner
decision.

Divergence policy (owner decision 2026-08-31, scoping package):
  (i)   the semantic judge over-credits a factually-wrong answer =
        BLOCKING finding (detected when the answer provider marks a
        hypothesis ``known_wrong`` and the semantic verdict is True);
  (ii)  the temporal off-by-one class (the official template forbids
        penalizing off-by-one day counts; containment penalized it — the
        bug being fixed) = recorded finding, NOT a defect;
  (iii) ``_abs`` marker-vocabulary divergence (the marker short-circuit
        vs the semantic abstention template) = recorded finding.

Offline (default): runs with the scripted fake judge (``ScriptedSemanticJudge``)
+ the curated answer set — the pinned expectation, agreement 1.0, exit 0.
Live (``--live``): runs with the real ``build_judge()`` (official gpt-4o)
— requires a judge provider key (the ask_spotcheck fail-fast contract);
answers come from ``--answers`` (JSON: qid → {answer, abstained,
known_wrong?}) or the curated set. Pending keys locally: the live path is
documented, not run.

Example:
    uv run python tools/ask_spotcheck_consistency.py            # offline (fake judge + curated answers)
    uv run python tools/ask_spotcheck_consistency.py --live --answers answers.json   # key-gated
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.ask_spotcheck import (  # noqa: E402
    _grade,
    _load_composition,
    _require_judge_key,
)
from tools.longmem_eval.judge import (  # noqa: E402
    MockJudge,
    ScriptedSemanticJudge,
    build_judge,
    is_abstention,
)

# ── The 3 SSP long-gold questions (d6233ab6 79w / 1d4e3b97 68w / b0479f84
# 63w) — the defect class the owner decision fixes. ────────────────────────
LONG_GOLD_QIDS = ("d6233ab6", "1d4e3b97", "b0479f84")

# Curated, human-verified-correct paraphrased answers for the long-gold
# questions — INJECTED directly to isolate the judge from reader capability
# (the current default reader cannot produce a correct answer for b0479f84
# — #2069 model class — or 1d4e3b97 — #2070 retrieval class). Each is a
# correct paraphrase of the rubric gold: it draws on the user's stated
# preferences/experiences the rubric requires, in fresh wording that no
# lexical bar would ever match (verified against the rubric golds in the
# committed composition fixture).
CURATED_ANSWERS: dict[str, dict] = {
    "d6233ab6": {
        "answer": ("Attending the reunion sounds like a good idea — you "
                   "loved your high school days on the debate team and in "
                   "advanced placement courses, and it would let you "
                   "reconnect with old friends and revisit subjects you "
                   "enjoyed like history and economics."),
        "abstained": False,
    },
    "1d4e3b97": {
        "answer": ("The improvement likely comes from the chain and "
                   "cassette we replaced on your bike, plus your new Garmin "
                   "bike computer — a fresh drivetrain and better ride data "
                   "can both make your Sunday group rides feel smoother and "
                   "faster."),
        "abstained": False,
    },
    "b0479f84": {
        "answer": ("Since you enjoyed 'Our Planet', 'Free Solo', and "
                   "'Tiger King', I'd recommend nature and adventure "
                   "documentaries with a similar style and theme — like "
                   "'Planet Earth II' for sweeping nature footage and "
                   "'The Alpinist' for climbing."),
        "abstained": False,
    },
}

# Scripted semantic-judge rules for the OFFLINE run (the pinned
# expectation): the curated long-gold paraphrases → True, the canonical
# ``_abs`` abstention → True, everything else → False. A reword of the
# curated answers flips the fake loudly.
OFFLINE_RULES = (
    ("loved your high school days on the debate team", True),
    ("chain and cassette we replaced on your bike", True),
    ("similar style and theme", True),
    ("absent from the context", True),
)


def _default_answers(questions: list[dict]) -> dict[str, dict]:
    """Curated answer set over the whole composition: the 3 long-gold
    paraphrases; the verbatim gold for every other question; a canonical
    marker-compatible abstention for the ``_abs`` questions."""
    answers: dict[str, dict] = {}
    for q in questions:
        qid = q["question_id"]
        if qid in CURATED_ANSWERS:
            answers[qid] = CURATED_ANSWERS[qid]
        elif "_abs" in qid:
            answers[qid] = {
                "answer": "The asked information is absent from the context.",
                "abstained": True,
            }
        else:
            answers[qid] = {
                "answer": q.get("answer") or "",
                "abstained": False,
            }
    return answers


def _load_answers(path: str | None, questions: list[dict]) -> dict[str, dict]:
    if path:
        with open(path) as f:
            data = json.load(f)
        return {qid: {"answer": rec["answer"], "abstained": rec.get("abstained", False),
                      "known_wrong": rec.get("known_wrong", False)}
                for qid, rec in data.items()}
    return _default_answers(questions)


def _offline_judge(questions: list[dict]) -> ScriptedSemanticJudge:
    """The offline pinned fake: the curated long-gold paraphrase rules +
    the ``_abs`` marker rule (OFFLINE_RULES) plus a verbatim-credit rule
    for every short-gold question (a verbatim gold contains the correct
    answer — the real semantic judge would credit it; the fake is pinned
    to the same). The long-gold questions are NOT given verbatim-credit
    rules — their pinned answers are the curated paraphrases, and the
    containment-vs-semantic flip on them (containment False, semantic
    True) is exactly the defect class the #2071 owner decision fixes."""
    rules = list(OFFLINE_RULES)
    for q in questions:
        qid = q.get("question_id") or ""
        gold = q.get("answer") or ""
        if qid in LONG_GOLD_QIDS or not gold:
            continue
        needle = " ".join(str(gold).lower().split())
        if needle:
            rules.append((needle, True))
    return ScriptedSemanticJudge(rules=rules)


def run_consistency(questions: list[dict], *, judge, answers: dict[str, dict]) -> dict:
    """Per-question comparison of the three verdict legs:

    * ``spot_ok`` — the spot-check semantic verdict (``ask_spotcheck._grade``
      with the SAME judge: ``_abs`` marker short-circuit first, else the
      judge call);
    * ``eval_ok`` — the graded-eval verdict (the exact judge call the eval
      harness makes, ``run.py`` judge call site);
    * ``containment_ok`` — the key-free CI substitute (MockJudge containment)
      — the CI-parity leg.

    Returns the report dict: per-question records, lane agreement, CI
    parity agreement, classified findings (with the blocking flag for the
    over-credit class (i)).
    """
    records = []
    findings = []
    lane_agree = 0
    ci_agree = 0
    for q in questions:
        qid = q["question_id"]
        qtype = q.get("question_type") or ""
        abstention = is_abstention(qid)
        hyp = answers.get(qid, {}).get("answer") or ""
        abstained = answers.get(qid, {}).get("abstained", False)
        known_wrong = answers.get(qid, {}).get("known_wrong", False)

        result = {"abstained": abstained, "answer": hyp, "question_type": qtype}
        spot_ok, _note, _kind = _grade(q, result, judge)
        # The graded-eval verdict: the exact judge call run_evaluation makes.
        eval_ok = judge.judge(
            question_type=qtype, question=q.get("question") or "",
            answer=q.get("answer") or "", hypothesis=hyp,
            abstention=abstention)
        containment_ok = MockJudge().judge(
            question_type=qtype, question=q.get("question") or "",
            answer=q.get("answer") or "", hypothesis=hyp,
            abstention=abstention)

        lane_agree += int(spot_ok == eval_ok)
        ci_agree += int(containment_ok == eval_ok)

        # Classify findings (divergence policy, owner decision 2026-08-31).
        if known_wrong and eval_ok:
            findings.append({
                "question_id": qid, "class": "overcredit", "blocking": True,
                "note": "semantic judge over-credits a factually-wrong "
                        "answer (known_wrong hypothesis graded True) — "
                        "BLOCKING per divergence policy (i)"})
        if spot_ok != eval_ok:
            cls = "abs-marker-lane" if abstention else "structural-lane"
            findings.append({
                "question_id": qid, "class": cls,
                "blocking": not abstention,
                "note": f"spot-check ({spot_ok}) vs graded-eval ({eval_ok}) "
                        f"differ; {'_abs marker short-circuit — recorded finding (iii)' if abstention else 'unexpected — investigate'}"})
        if containment_ok != eval_ok:
            if abstention:
                cls, note = "abs-marker", ("containment marker path vs "
                    "semantic abstention template differ — recorded "
                    "finding (iii)")
            elif qtype == "temporal-reasoning":
                cls, note = "temporal-off-by-one", (
                    "containment penalized it; the official template "
                    "forbids penalizing off-by-one day counts — the bug "
                    "being fixed; recorded finding (ii), NOT a defect")
            elif qid in LONG_GOLD_QIDS:
                cls, note = "long-gold-unreachable", (
                    "containment is structurally unreachable for this "
                    "rubric-style long gold; semantic is benchmark-correct "
                    "— the point of the #2071 owner decision")
            else:
                cls, note = "containment-unexpected", (
                    "containment and semantic differ on a short gold — "
                    "recorded for review")
            findings.append({
                "question_id": qid, "class": cls, "blocking": False,
                "note": note})

        records.append({
            "question_id": qid, "question_type": qtype,
            "spot_ok": spot_ok, "eval_ok": eval_ok,
            "containment_ok": containment_ok, "hypothesis": hyp,
        })

    n = len(questions)
    return {
        "n": n,
        "lane_agreement": lane_agree / n if n else 0.0,
        "ci_parity_agreement": ci_agree / n if n else 0.0,
        "records": records,
        "findings": findings,
        "blocking": any(f["blocking"] for f in findings),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="I2 consistency harness (issue #2071 step 5): "
                    "spot-check semantic vs graded-eval semantic verdicts "
                    "on the shared population + CI (MockJudge) parity")
    ap.add_argument("--fixture", default=None,
                    help="composition fixture path (default: committed "
                         "fixture / TORTOISE_SPOTCHECK_FIXTURE / /tmp)")
    ap.add_argument("--answers", default=None,
                    help="JSON qid → {answer, abstained, known_wrong?}; "
                         "default: curated answers (long-gold paraphrases + "
                         "verbatim short golds + _abs abstentions)")
    ap.add_argument("--live", action="store_true",
                    help="use the real build_judge() (official gpt-4o) — "
                         "requires a judge provider key (fail-fast "
                         "contract); default: scripted fake judge (offline)")
    args = ap.parse_args(argv)

    questions = _load_composition(args.fixture)
    answers = _load_answers(args.answers, questions)

    if args.live:
        try:
            key_env = _require_judge_key()
        except (RuntimeError, ValueError) as exc:
            print(f"ask_spotcheck_consistency: {exc}", file=sys.stderr)
            return 2
        judge = build_judge()
        judge_label = f"live {judge.model_id} ({key_env})"
    else:
        judge = _offline_judge(questions)
        judge_label = "scripted-semantic (offline fake)"

    report = run_consistency(questions, judge=judge, answers=answers)
    print(f"consistency: {report['n']} questions, judge={judge_label}")
    print(f"  lane agreement (spot-check vs graded-eval semantic): "
          f"{report['lane_agreement']:.2f} (expected 1.0 by construction)")
    print(f"  CI parity (containment vs semantic): "
          f"{report['ci_parity_agreement']:.2f}")
    for f in report["findings"]:
        print(f"  finding [{f['class']}] {f['question_id']} "
              f"{'BLOCKING' if f['blocking'] else 'recorded'}: {f['note']}")
    if report["blocking"]:
        print("RESULT: BLOCKING findings — over-credit class detected")
        return 1
    if report["lane_agreement"] < 1.0:
        print("RESULT: lane divergence — investigate (structural-lane)")
        return 1
    print("RESULT: OK — lane agreement 1.0, no blocking findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
