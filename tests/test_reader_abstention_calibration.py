"""#2027 regression tests — generic-baseline Phase-1 presence-commit.

The product reader over-abstained on the GENERIC baseline (the ask lane's
detector returned ``None`` on 20/21 QA spot-check questions): Phase 1 of
the universal A1 clause only demonstrably licensed committing when a type
fragment's reasoning instructions engaged, so when the question matched no
recognized category the reader fell through to the Phase-2 abstention
branch even though the asked value was present in the context — 10/21
false abstentions, QA spot-check 0.43 < 0.8 (issue #2027, the #1987 gate-d
blocker). Evidence probes confirmed the material reached the reader:
``d6233ab6`` wrote "It mentions nostalgic high school experiences (debate
team, AP economics), but..." THEN abstained; ``gpt4_8279ba02`` quoted the
smoker-purchase session yet abstained instead of computing the days.

These tests lock the #2027 calibration:

  * Phase 1 fires on PRESENT EVIDENCE regardless of fragment engagement —
    a category-independent presence-commit rule (no type fragment needed);
  * derived answers (elapsed time, counts, totals, ordering) commit from
    the dated events/facts in context — scoped to the ASKED SUBJECT's
    events actually being present (the false-commit guard);
  * Phase 2 abstains only on genuine absence — never merely because no
    special instructions were attached to the question.

The two known false-abstention evidence shapes (``d6233ab6`` preference-
synthesis, ``gpt4_8279ba02`` temporal day-count) are regression fixtures
with context-reading fakes gating on the operative sentences (the #1775
pattern) so a reword fails loudly instead of silently flipping the fake.

Note on the red→green legs (test-review #2013): the fakes are COMPLIANT-
MODEL fakes — they mechanically execute the pinned rule, so each leg
verifies the END-TO-END PIPELINE wiring (retrieval → context assembly →
question-type plumbing → the exact operative sentence reaching the
reader) with a model that obeys the rule; they do NOT verify that a real
LLM follows the clause (that is the key-gated real-model probe in
tests/test_longmem_reader_prompting.py, and the benchmark's strong-reader
job). A reword that keeps the substrings but inverts semantics is caught
by the content pins in the tests above; the behavioral legs pin the
wiring.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval.judge import MockJudge  # noqa: E402, RUF100
from tools.longmem_eval.reader import LLMReader  # noqa: E402, RUF100
from tools.longmem_eval.run import run_evaluation  # noqa: E402, RUF100
from tortoise.reader import (  # noqa: E402, RUF100
    _ABSTRACTION_FRAGMENT,
    _SYSTEM_PROMPT,
    _TYPE_FRAGMENTS,
    system_prompt_for,
)

# ── Prompt-content pins: the generic-baseline Phase-1 presence-commit ─────

def test_generic_baseline_presence_commit_pinned():
    """#2027: Phase 1 must fire on present evidence WITHOUT a type fragment
    — the clause must (a) state that the presence test applies to every
    question whether or not it matches a recognized category, (b) license
    derived/computed answers from dated evidence, (c) license synthesized
    answers from stated preferences, and (d) scope the derived-commit to
    the asked subject's events actually being present (the false-commit
    guard). Pinned substrings so a reword that drops the rule fails."""
    clause = _ABSTRACTION_FRAGMENT
    low = clause.lower()
    # (a) category independence — no fragment → still commit on present value
    assert "whether or not it matches a recognized category" in low
    assert "absence of category instructions" in low
    assert "never a reason to abstain" in low
    # (b) derived-answer commit (gpt4_8279ba02 shape: the day count is not
    # literally written — it is computable from the dated purchase)
    assert "asks for a derived value" in low
    assert "elapsed time or day count" in low
    assert "commit to the computed value" in low
    assert "not literally written" in low
    # the license spans the full derived enumeration (orders, counts,
    # totals, prices, differences) — a reword that drops any of them
    # fails here, not just the day-count branch
    assert "a date or order" in low
    assert "a count" in low
    assert "a total" in low
    assert "a price" in low
    assert "a difference" in low
    # (c) synthesis commit (d6233ab6 shape: preference-shaped answers draw
    # on present experiences/signals instead of abstaining)
    assert "asks what the user prefers" in low
    assert "synthesized rather than quoted" in low
    # (d) false-commit guard: derived-commit scoped to the asked subject
    assert "commit only when the events or facts the question asks about" in low


def test_phase2_abstains_only_on_genuine_absence_never_instruction_gap():
    """#2027 root cause: 'no type fragment matched' must NOT default to
    abstain. Phase 2 fires only on genuine absence — never merely because
    the question matched no category or carried no special instructions.
    The two-phase structural order (#1775) is preserved."""
    clause = _ABSTRACTION_FRAGMENT
    low = clause.lower()
    assert "never merely because" in low
    assert "matched no category or carried no special instructions" in low
    assert clause.index("PHASE 1") < clause.index("PHASE 2")
    assert "only when phase 1 found no affirmative statement" in low


def test_generic_prompt_carries_rule_and_no_type_fragment():
    """The GENERIC baseline (``system_prompt_for(None)`` — what the ask
    lane sends when the detector returns None) must carry the presence-commit
    rule and no type-fragment reasoning instructions (assembly unchanged)."""
    generic = system_prompt_for(None)
    assert generic == _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT
    assert "asks for a derived value" in generic
    for marker in ("TEMPORAL REASONING INSTRUCTIONS",
                   "PREFERENCE INSTRUCTIONS",
                   "KNOWLEDGE-UPDATE INSTRUCTIONS",
                   "MULTI-SESSION REASONING INSTRUCTIONS"):
        assert marker not in generic, marker


def test_type_fragment_paths_keep_the_rule():
    """The rule is in the UNIVERSAL clause — every type-tailored prompt
    (when a fragment does engage) still carries the presence-commit rule."""
    for t in _TYPE_FRAGMENTS:
        assert "asks for a derived value" in system_prompt_for(t), t


# ── Regression fixture 1 (gpt4_8279ba02): derived day-count commits ───────

def _smoker_question() -> dict:
    """#2027 gpt4_8279ba02 shape (generic baseline, detected=None): the
    smoker-purchase session date IS in the rendered context; the day count
    is computable, not literally written. The pre-#2027 reader abstained
    ('it does not state how many days')."""
    return {
        "question_id": "pt_calib_001_smoker",
        "question_type": None,  # the generic baseline — no fragment engages
        "question": "How many days ago did I buy a smoker?",
        "answer": "10 days ago",
        "question_date": "2023-03-25",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2023-03-15"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "user", "content": "I just got a smoker today!",
             "has_answer": True},
            {"role": "assistant", "content": "Nice! What are you planning "
             "to smoke first?"},
        ]],
    }


class _DerivedCommitModel:
    """Context-reading fake for the derived-answer class (#2027): commits
    the computed day count (session date vs Current Date) ONLY when the
    derived-value sentence is in the system prompt AND the purchase
    evidence is in the rendered context; otherwise it reproduces the
    recorded pre-#2027 abstention ('does not state how many days'). A
    reword that drops the operative sentence flips the fake red; a
    retrieval/render regression (evidence missing from the context) also
    flips it red (the regex misses)."""

    _DATE = re.compile(r"session date (\d{4}-\d{2}-\d{2})")
    _CUR = re.compile(r"Current Date: (\d{4}-\d{2}-\d{2})")

    def complete(self, *, system: str, user: str) -> str:
        m = self._DATE.search(user)
        cur = self._CUR.search(user)
        if (m and cur and "asks for a derived value" in system
                and re.search(r"smoker", user)):
            days = (date.fromisoformat(cur.group(1))
                    - date.fromisoformat(m.group(1))).days
            return f"{days} days ago"
        return ("The memory does not contain the asked information — it "
                "does not state how many days ago I bought a smoker.")


def test_derived_value_commits_on_generic_baseline(tmp_path):
    """#2027 red→green: the gpt4_8279ba02 shape — present dated evidence,
    derived answer — commits on the GENERIC baseline (question_type=None,
    no temporal fragment). Red leg: without the derived-value sentence the
    fake reproduces the recorded abstention and MockJudge scores it wrong
    (a non-_abs abstention hedge is not a correct answer); green leg: the
    rule present → the computed day count, judged correct."""
    q = _smoker_question()

    class _PreRuleModel(_DerivedCommitModel):
        def complete(self, *, system, user):
            # strip the #2027 operative sentence → pre-fix branch
            return super().complete(
                system=system.replace("asks for a derived value", "X"),
                user=user)

    pre = run_evaluation(
        [q], reader=LLMReader(_PreRuleModel(), model_id="pre-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: the false abstention
    assert "does not state how many days" in pre[0][0]["hypothesis"].lower()

    post = run_evaluation(
        [q], reader=LLMReader(_DerivedCommitModel(), model_id="post-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True          # green: commits the value
    assert post[0][0]["hypothesis"] == "10 days ago"


# ── Regression fixture 2 (d6233ab6): preference-synthesis commits ─────────

def _reunion_question() -> dict:
    """#2027 d6233ab6 shape (generic baseline, detected=None): the question
    asks whether attending the high school reunion is a good idea; the
    context states the user's relevant experiences (debate team, advanced
    placement courses). The pre-#2027 reader wrote 'It mentions nostalgic
    high school experiences (debate team, AP economics), but...' THEN
    abstained — the preference-shaped answer must be synthesized, not
    quoted."""
    return {
        "question_id": "pt_calib_002_reunion",
        "question_type": None,  # the generic baseline
        "question": "Do you think it would be a good idea to attend my "
                    "high school reunion?",
        "answer": ("A good idea — you enjoyed your high school experiences "
                   "like the debate team and advanced placement courses"),
        "question_date": "2023-05-30",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2023-05-25"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "user", "content": "I loved being on the debate team "
             "and taking advanced placement courses in high school.",
             "has_answer": True},
        ]],
    }


class _SynthesisCommitModel:
    """Context-reading fake for the preference-synthesis class (#2027):
    commits a synthesis DRAWN FROM the rendered context — the experiences
    are parsed out of the evidence turn, so the committed answer is
    derived from what the reader actually saw (a render regression that
    drops or rewords the signals flips the fake to the recorded pre-#2027
    hedge-abstention and the assertions fail loudly). The green branch
    fires only when the category-independent commit sentence is in the
    system prompt AND the signals are in the rendered context; otherwise
    it reproduces the recorded pre-#2027 hedge-abstention ('mentions
    nostalgic high school experiences... but does not contain')."""

    _CATEGORY_INDEPENDENT = (
        "whether or not it matches a recognized category")
    _EXPERIENCES = re.compile(
        r"I loved being on (.+?) and taking (.+?) in high school")

    def complete(self, *, system: str, user: str) -> str:
        m = self._EXPERIENCES.search(user)
        if m and self._CATEGORY_INDEPENDENT in system:
            return (f"A good idea — you enjoyed your high school "
                    f"experiences like {m.group(1).strip()} and "
                    f"{m.group(2).strip()}")
        return ("It mentions nostalgic high school experiences (debate "
                "team, AP economics), but it does not contain the asked "
                "information.")


def test_preference_synthesis_commits_on_generic_baseline(tmp_path):
    """#2027 red→green: the d6233ab6 shape — preference-shaped question on
    the generic baseline with the relevant signals present — commits a
    synthesis instead of abstaining. Red leg: the hedge-abstention is
    wrong; green leg: the synthesis drawn from the present signals is
    judged correct."""
    q = _reunion_question()

    class _PreRuleModel(_SynthesisCommitModel):
        def complete(self, *, system, user):
            return super().complete(
                system=system.replace(
                    "whether or not it matches a recognized category", "X"),
                user=user)

    pre = run_evaluation(
        [q], reader=LLMReader(_PreRuleModel(), model_id="pre-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: the false abstention
    assert "does not contain the asked" in pre[0][0]["hypothesis"].lower()

    post = run_evaluation(
        [q], reader=LLMReader(_SynthesisCommitModel(), model_id="post-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True          # green: the synthesis
    assert "debate team" in post[0][0]["hypothesis"].lower()


# ── Root-cause behavioral pin: no instruction gap licenses abstention ────

def test_no_instruction_gap_abstention_on_present_value(tmp_path):
    """#2027 root cause, behavioral: 'the question matched no category or
    carried no special instructions' must never alone license abstention —
    the Phase-2 qualifier ('never merely because…') is what keeps the
    instruction gap out. Red→green on the smoker shape (present dated
    evidence, question_type=None): strip the qualifier and the fake
    reproduces the recorded pre-#2027 abstention; with it, it commits the
    computed day count."""
    q = _smoker_question()

    class _InstructionGapModel(_DerivedCommitModel):
        def complete(self, *, system, user):
            if "never merely because" not in system:
                return ("The memory does not contain the asked information "
                        "— it does not state how many days ago I bought a "
                        "smoker.")
            return super().complete(system=system, user=user)

    class _PreQualifierModel(_InstructionGapModel):
        def complete(self, *, system, user):
            # strip the Phase-2 qualifier so the fake takes the
            # pre-#2027 instruction-gap branch
            return super().complete(
                system=system.replace("never merely because", "X"),
                user=user)

    pre = run_evaluation(
        [q], reader=LLMReader(_PreQualifierModel(), model_id="pre-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: the false abstention
    assert "does not state how many days" in pre[0][0]["hypothesis"].lower()

    post = run_evaluation(
        [q], reader=LLMReader(_InstructionGapModel(), model_id="post-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True          # green: commits the value
    assert post[0][0]["hypothesis"] == "10 days ago"


# ── Derived-enumeration behavioral pin: count/total class (0100672e) ─────

def _mug_total_question() -> dict:
    """0100672e-class shape (runbook, post-fix working): 'how much were
    the mugs' → a total computed from the per-item price and quantity in
    context; generic baseline (question_type=None), derived license."""
    return {
        "question_id": "pt_calib_004_total",
        "question_type": None,
        "question": "How much did the mugs cost in total?",
        "answer": "$60",
        "question_date": "2023-05-30",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2023-05-20"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "user", "content": "I bought five mugs at $12 each "
             "for the office.", "has_answer": True},
        ]],
    }


class _TotalCommitModel:
    """Count/total derived fake (#2027): computes the total from the
    per-item price and quantity parsed out of the rendered context; red
    (no derived-value license) reproduces the recorded abstention. A
    render regression that drops the priced turn flips the fake red (the
    regex misses)."""

    _PRICE = re.compile(r"five mugs at \$?(\d+) each")

    def complete(self, *, system: str, user: str) -> str:
        m = self._PRICE.search(user)
        if m and "asks for a derived value" in system:
            return f"${int(m.group(1)) * 5}"
        return ("The memory does not contain the asked information — it "
                "does not state the total cost.")


def test_total_value_commits_on_generic_baseline(tmp_path):
    """#2027 derived-enumeration red→green (count/total class): a total
    computed from rendered per-item prices commits on the generic
    baseline. Red leg: without the derived-value sentence the fake
    reproduces the abstention register (MockJudge containment → wrong);
    green leg: the license present → the computed total, judged correct."""
    q = _mug_total_question()

    class _PreRuleModel(_TotalCommitModel):
        def complete(self, *, system, user):
            return super().complete(
                system=system.replace("asks for a derived value", "X"),
                user=user)

    pre = run_evaluation(
        [q], reader=LLMReader(_PreRuleModel(), model_id="pre-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: the false abstention
    assert "does not state the total" in pre[0][0]["hypothesis"].lower()

    post = run_evaluation(
        [q], reader=LLMReader(_TotalCommitModel(), model_id="post-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True          # green: commits the total
    assert post[0][0]["hypothesis"] == "$60"

# ── False-commit guard: derived-commit scoped to the asked subject ────────

def _bus_fare_abs_question() -> dict:
    """09ba9854_abs shape guard (#2027 indicator 3): the asked value (the
    bus fare) is genuinely ABSENT from the haystack — only taxi prices are
    present. The pre-#2027 graded-_abs false-commit derived taxi-vs-bus
    savings from taxi prices; the #2027 derived-commit license must NOT
    re-license that class (commit only when the events/facts the question
    asks about are in the context)."""
    return {
        "question_id": "pt_calib_003_abs",
        "question_type": None,
        "question": "What is the bus fare downtown?",
        "answer": "The information provided is not enough.",
        "question_date": "2023-05-30",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2023-05-20"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "user", "content": "Taxis downtown cost $15, and I "
             "take the train sometimes.", "has_answer": False},
        ]],
    }


class _ScopedDerivedModel:
    """Scoped derived-commit fake (#2027): commits the asked value only
    when its evidence is actually in the rendered context; otherwise the
    SCOPING GUARD sentence licenses the evidence-backed abstention
    (Phase 2 intact). Gates the abstention branch on the #2027 guard
    itself — a reword that drops 'commit only when the events or facts
    the question asks about' flips the fake to the red branch: it derives
    a savings figure from the present taxi price while the asked bus fare
    is absent (the recorded 09ba9854_abs false-commit class — pre-#2027
    the reader computed taxi-vs-bus savings from taxi prices)."""

    _FARE = re.compile(r"bus fare is (\$?\d+)")
    _TAXI = re.compile(r"Taxis downtown cost \$?(\d+)")
    _SCOPING_GUARD = (
        "commit only when the events or facts the question asks about")

    def complete(self, *, system: str, user: str) -> str:
        m = self._FARE.search(user)
        if m and "asks for a derived value" in system:
            return m.group(1).strip()          # present → commit (control)
        taxi = self._TAXI.search(user)
        if taxi and self._SCOPING_GUARD not in system:
            # guard absent → the pre-#2027 derived false-commit class:
            # taxi-vs-bus savings computed from the present taxi price
            return (f"derived taxi savings of ${taxi.group(1)} minus the "
                    "bus fare")
        # evidence-backed abstention in the compressed Phase-2 canonical
        # phrasing — 'absent from the context' is a NEW #2027 judge
        # marker, exercised end-to-end here (J5 behavioral coverage)
        return ("The memory mentions taxi prices, but the asked bus "
                "fare is absent from the context.")


def test_derived_commit_still_abstains_on_absent_subject(tmp_path):
    """#2027 control: the derived-commit license is scoped — when the
    events/facts the question asks about are NOT in the context, the
    scoping guard keeps Phase 2's evidence-backed abstention (abstention
    markers → MockJudge True on an _abs question). Red leg: with the
    #2027 guard stripped, the fake derives a savings figure from the
    present taxi price — the recorded 09ba9854_abs false-commit class the
    guard must not re-license."""
    q = _bus_fare_abs_question()

    class _PreScopeModel(_ScopedDerivedModel):
        def complete(self, *, system, user):
            # strip the #2027 scoping guard so the fake takes the
            # pre-#2027 branch (derives the taxi-vs-bus false-commit)
            return super().complete(
                system=system.replace(
                    "commit only when the events or facts the question "
                    "asks about", "X"),
                user=user)

    pre = run_evaluation(
        [q], reader=LLMReader(_PreScopeModel(), model_id="pre-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: the false-commit
    assert "savings" in pre[0][0]["hypothesis"].lower()
    assert "taxi" in pre[0][0]["hypothesis"].lower()

    post = run_evaluation(
        [q], reader=LLMReader(_ScopedDerivedModel(), model_id="post-2027"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True          # green: evidence-backed
    # the compressed Phase-2 canonical phrasing — a NEW #2027 judge
    # marker ('absent from the context') scored end-to-end on the _abs
    # question (J5: correct abstentions score correct with the new
    # vocabulary)
    assert "absent from the context" in post[0][0]["hypothesis"].lower()
    assert "taxi" in post[0][0]["hypothesis"].lower()
    # control: not unconditionally abstaining — asked value present → commit.
    # Through LLMReader.answer (not a bare complete() call) so the control
    # exercises the product path — render_context → build_reader_user_message
    # → system_prompt_for → the fake (test-review #2013: a hand-assembled
    # complete() call only unit-tests the fake).
    ctrl = LLMReader(_ScopedDerivedModel(), model_id="ctrl").answer(
        context_hits=[{"content": "the bus fare is $3 downtown",
                       "session_date": "2023-05-20",
                       "lme_session_index": 0}],
        question="How much is the bus fare downtown?",
        question_date="2023-05-25", question_type=None)
    assert ctrl == "$3"


# ── Product abstained-label pin: clause-scoped (P2) ──────────────────────

def test_abstained_label_clause_scoped_pin():
    """P2 clause-scoped tightening of ``_looks_abstained`` (the product
    label, distinct from the judge's whole-answer match): the abstention
    must be the answer's OPERATIVE clause, not a trailing qualifier.

    * The #2027 canonical abstention forms — whole-answer ('asked
      information is absent', 'does not contain', 'absent from the
      context') AND the Phase-2 template shape '[related facts], but the
      asked value is absent' (the taxi fixture's trailing 'absent from
      the context' clause) — MUST stay abstained (no regression).
    * A committed answer with a trailing confidence qualifier ('…though I
      do not know if it changed', 'the context does not mention his age')
      is NOT abstained — the exact hedge class #2027 fought.
    """
    from tortoise.reader import _looks_abstained
    # #2027 canonical abstentions → abstained (whole-answer + trailing
    # Phase-2 template forms)
    for abstention in (
        "The asked information is absent.",
        "The memory does not contain the asked information.",
        "The asked bus fare is absent from the context.",
        "The memory does not contain the asked information — it does "
        "not state how many days ago I bought a smoker.",
        # the Phase-2 template: 'mentioning the related facts found in
        # the memory if any' — the abstention trails the premise clause
        "The memory mentions taxi prices, but the asked bus fare is "
        "absent from the context.",
        "It mentions nostalgic high school experiences (debate team, AP "
        "economics), but it does not contain the asked information.",
        "No turns mention the smoker; the information is absent from "
        "the context.",
        # cycle-2 pins: genuine whole-answer abstentions WITHOUT the
        # "asked"/"absent" anchor — a later-clause marker as the answer's
        # operative (final) clause (flat refusal / asked-subject reference)
        # MUST label abstained (the pre-cycle-2 clause-scope rule missed
        # these — false-negative class, P2).
        "The answer: I don't know.",
        "I searched the memory. I cannot answer.",
        "The gym schedule is Monday. I do not know.",
        "I remember the trip. I don't know the date.",
        "The memory mentions a new bicycle, but it does not contain the "
        "color.",
    ):
        assert _looks_abstained(abstention) is True, abstention
    # committed answers with a trailing qualifier → NOT abstained
    for committed in (
        "The gym schedule is Monday, though I do not know if it "
        "changed.",
        "The move date is Monday, though I'm not sure about the move "
        "date.",
        "He was born in 1978, though the context does not mention his "
        "age.",
        "I bought the smoker 10 days ago, though I do not know the "
        "exact hour.",
        # cycle-2 pins: committing answers whose later clause carries an
        # abstention marker — MUST stay NOT abstained (the #2027
        # committing-hedge class; a "though"-hedge or a possessive-
        # attribute qualifier never labels abstained even when it is the
        # final clause).
        "The gym schedule is Monday and Wednesday, though I do not know "
        "if it changed since",
        "Golden Retriever — Max's breed. The context does not mention "
        "his age.",
    ):
        assert _looks_abstained(committed) is False, committed
