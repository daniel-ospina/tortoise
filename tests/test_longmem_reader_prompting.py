"""Reader prompting tests for the LongMemEval weak categories — preference
(single-session-preference) and temporal (temporal-reasoning) (#1366).

The categories fail at the READER, not retrieval (recall@10 0.86): the
generic system prompt licenses hedging ("say that you do not know") and
carries zero per-question-type guidance. These tests lock:

  1. the plumbing — question_type reaches the reader (was: never forwarded),
  2. the prompt content — temporal/preference instructions are present in
     the system prompt exactly for their question types,
  3. the behavior — a prompt-faithful reader computes "N days ago" from the
     rendered dates (temporal) and commits to the user's stated option
     (preference) instead of hedging — mirroring the issue's documented
     failure mode, judged by MockJudge.

Fully offline: embedded FalkorDBLite, mock judge, fake recording models, no
API keys, no dataset download.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK  # noqa: E402, F401, I001, RUF100

from tools.longmem_eval.judge import MockJudge  # noqa: E402, RUF100
from tools.longmem_eval.reader import (  # noqa: E402, RUF100
    LLMReader, MockReader, _SYSTEM_PROMPT, _ABSTRACTION_FRAGMENT,
    _TYPE_FRAGMENTS, system_prompt_for,
)
from tools.longmem_eval.retrieve import render_context  # noqa: E402, RUF100
from tools.longmem_eval.run import run_evaluation  # noqa: E402, RUF100

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


# ── Fixture questions of the two weak types (dataset schema, mini-style) ──

def _temporal_question() -> dict:
    """Official shape: Current Date header + session dates make "how many
    days ago" answerable from the evidence turn alone."""
    return {
        "question_id": "pt_temporal_001",
        "question_type": "temporal-reasoning",
        "question": "How many days ago did Ava tell you she adopted a dog?",
        "answer": "3 days ago",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2025-06-12"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "user", "content": "I adopted a dog three days ago.",
             "has_answer": True},
            {"role": "assistant", "content": "Congratulations! What is its name?"},
        ]],
    }


def _preference_question() -> dict:
    """Official shape: the user states an implicit preference between two
    options; the answer is the preferred option."""
    return {
        "question_id": "pt_preference_001",
        "question_type": "single-session-preference",
        "question": "Which restaurant does Ava prefer for Italian food, "
                    "Bella Napoli or Trattoria Roma?",
        "answer": "Trattoria Roma",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "user", "content": "Bella Napoli is fine but I prefer "
             "Trattoria Roma for Italian food.", "has_answer": True},
            {"role": "assistant", "content": "Great choice, I will book it."},
        ]],
    }


# ── Recording doubles ──────────────────────────────────────────────────────

class _RecordingReader(MockReader):
    """Captures the kwargs run_evaluation passes to reader.answer."""

    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    def answer(self, **kw):
        self.calls.append(dict(kw))
        return super().answer(**kw)


class _RecordingModel:
    """Captures the (system, user) prompt LLMReader sends to the model."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return "placeholder answer"


# ── 1. Plumbing: question_type reaches the reader ─────────────────────────

def test_question_type_reaches_reader(tmp_path):
    """run_evaluation must forward each question's type to reader.answer —
    without it the reader cannot apply type-specific reasoning (P1 #1366)."""
    reader = _RecordingReader()
    run_evaluation(
        [_temporal_question(), _preference_question()],
        reader=reader, judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path),
    )
    assert len(reader.calls) == 2
    assert reader.calls[0]["question_type"] == "temporal-reasoning"
    assert reader.calls[1]["question_type"] == "single-session-preference"


def test_reader_protocol_accepts_question_type():
    """The Reader protocol + MockReader accept question_type (backward
    compatible: default None for existing direct callers)."""
    hyp = MockReader().answer(
        context_hits=[{"id": "x", "content": "hi", "has_answer": True}],
        question="q", question_type="temporal-reasoning")
    assert hyp == "hi"
    # no question_type → same behavior (old callers unaffected)
    assert MockReader().answer(
        context_hits=[{"id": "x", "content": "hi", "has_answer": True}],
        question="q") == "hi"


# ── 2. Prompt content: type-specific instructions ─────────────────────────

def test_temporal_prompt_contains_date_reasoning_instructions():
    r = LLMReader(_RecordingModel(), model_id="fake")
    r.answer(context_hits=[{"id": "x", "content": "hi", "has_answer": True}],
             question="q", question_date="2025-06-15",
             question_type="temporal-reasoning")
    system = r._model.calls[0][0]
    assert "temporal" in system.lower()
    assert "Current Date" in system
    assert "days" in system  # commit to a day count, off-by-one acceptable
    # the hedge license is scoped to genuinely-lacking context, never for
    # dated evidence: the temporal fragment's OWN commit phrasing pins it
    # ('do not hedge' also appears in the generic prompt — not a
    # discriminator, review finding #1762)
    assert "Commit to a specific numeric answer" in system
    assert "do not hedge or refuse when the dated evidence" in system


def test_preference_prompt_contains_option_commitment_instructions():
    r = LLMReader(_RecordingModel(), model_id="fake")
    r.answer(context_hits=[{"id": "x", "content": "hi", "has_answer": True}],
             question="q", question_type="single-session-preference")
    system = r._model.calls[0][0]
    assert "preference" in system.lower()
    assert "option" in system.lower()
    # the preference fragment's OWN commit phrasing pins the option
    # commitment ('do not hedge' also appears in the generic prompt — not
    # a discriminator, review finding #1762)
    assert "commit to the specific option" in system


def test_temporal_prompt_preserves_abstention_license():
    """The real S split has temporal _abs questions (dated context, missing
    event) where the reader MUST abstain — the commit-first temporal fragment
    must keep the abstention path open (reviewer P2 #1366)."""
    r = LLMReader(_RecordingModel(), model_id="fake")
    r.answer(context_hits=[{"id": "x", "content": "hi", "has_answer": True}],
             question="q", question_date="2025-06-15",
             question_type="temporal-reasoning")
    system = r._model.calls[0][0]
    # commit-first for evidence-present — the temporal fragment's OWN
    # phrasing ('commit' also appears in the A1 clause — not a
    # discriminator, review finding #1762)
    assert "Commit to a specific numeric answer" in system
    assert "no dated evidence" in system.lower()  # … but abstain when the
    # event is absent from the dated context (judge requires markers)


def test_untouched_types_keep_generic_prompt():
    """A2 (#1547): knowledge-update and multi-session now get their own
    fragments — the remaining generic types (single-session-user /
    -assistant) are what prove unknown/untouched types stay generic."""
    r = LLMReader(_RecordingModel(), model_id="fake")
    for qt in ("single-session-user", "single-session-assistant"):
        r.answer(context_hits=[{"id": "x", "content": "hi",
                                "has_answer": True}],
                 question="q", question_type=qt)
        system = r._model.calls[-1][0]
        # the generic hardened prompt + the universal A1 clause — NO
        # type-specific fragment for untouched types. Exact equality (not
        # startswith — that tautology held unconditionally): a leaked
        # temporal/preference/KU/MSR fragment would break this assert
        # (exact equality also implies the supersed/aggregate vocabulary
        # stays out — those substring checks were dead weight).
        assert system == _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT


def test_type_fragments_append_after_universal_clause():
    """#1762 review: the prompt assembly ORDER is pinned — the universal
    A1 clause comes BEFORE the type fragment for touched types (the
    type-specific instruction must land last, most salient). A reorder
    that appended A1 after the fragment would pass every substring assert,
    so exact-equality pins both touched types."""
    assert system_prompt_for("temporal-reasoning") == \
        _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT + \
        _TYPE_FRAGMENTS["temporal-reasoning"]
    assert system_prompt_for("single-session-preference") == \
        _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT + \
        _TYPE_FRAGMENTS["single-session-preference"]


# ── 3. Behavior: the improved prompt fixes the hedge (red→green) ──────────

class _PromptFaithfulModel:
    """Fake model that behaves like the real reader's documented failure
    mode: it hedges ("I do not know") UNLESS the system prompt carries the
    type-specific instruction — proving the prompt, not retrieval, is what
    unlocks the correct answer."""

    def complete(self, *, system: str, user: str) -> str:
        if "temporal" in system.lower():
            # parse "Current Date: YYYY-MM-DD" and the session date
            # annotation, compute the day delta — exactly what the reader is
            # instructed to do
            cur = re.search(r"Current Date: (\d{4}-\d{2}-\d{2})", user)
            sess = re.search(r"session date (\d{4}-\d{2}-\d{2})", user)
            if cur and sess:
                d0 = __import__("datetime").date.fromisoformat(cur.group(1))
                d1 = __import__("datetime").date.fromisoformat(sess.group(1))
                delta = (d0 - d1).days
                return f"{delta} days ago"
        if "preference" in system.lower():
            # commit to the user's stated preferred option (the turn content
            # embeds it; MockJudge keys on containment of the golden answer)
            m = re.search(r"prefer\w* ([A-Za-z ]+?) (?:over|for)", user)
            if m:
                return m.group(1).strip().title()
        return "I do not know; the history does not mention it."


def test_temporal_question_answered_from_dates_end_to_end(tmp_path):
    """Full loop (ingest → retrieve → reader → MockJudge) with a
    prompt-faithful reader: the temporal fragment makes it compute
    "3 days ago" from Current Date vs session date (off-by-one tolerated by
    the official judge); without the fragment it would hedge and fail
    (red leg proves the fragment is what unlocks the answer)."""
    class _FragmentlessTemporalModel(_PromptFaithfulModel):
        def complete(self, *, system, user):
            # strip the type fragment so the fake takes the hedge branch
            return super().complete(
                system=system.replace("TEMPORAL REASONING INSTRUCTIONS", "X"),
                user=user)
    pre = run_evaluation(
        [_temporal_question()], reader=LLMReader(_FragmentlessTemporalModel(),
                                                 model_id="pre-fragment"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: hedges without it
    reader = LLMReader(_PromptFaithfulModel(), model_id="prompt-faithful")
    outcomes, report = run_evaluation(
        [_temporal_question()], reader=reader, judge=MockJudge(),
        ks=(5,), top_k=20, split="s", work_dir=str(tmp_path),
    )
    assert len(outcomes) == 1
    assert outcomes[0]["question_id"] == "pt_temporal_001"
    assert outcomes[0]["label"] is True
    assert "3 days ago" in outcomes[0]["hypothesis"]
    assert report["accuracy"]["per_category"]["Temporal Reasoning"]["accuracy"] == 1.0


def test_preference_question_answered_from_option_end_to_end(tmp_path):
    """Full loop with a prompt-faithful reader: the preference fragment
    makes it commit to the user's stated option; without it it would hedge
    and fail (red leg proves the fragment is what unlocks the answer)."""
    class _FragmentlessPreferenceModel(_PromptFaithfulModel):
        def complete(self, *, system, user):
            # strip the type fragment so the fake takes the hedge branch
            # ('preference' appears in both cases — strip both)
            return super().complete(
                system=system.replace("PREFERENCE", "X").replace(
                    "preference", "X"),
                user=user)
    pre = run_evaluation(
        [_preference_question()],
        reader=LLMReader(_FragmentlessPreferenceModel(),
                         model_id="pre-fragment"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: hedges without it
    reader = LLMReader(_PromptFaithfulModel(), model_id="prompt-faithful")
    outcomes, report = run_evaluation(
        [_preference_question()], reader=reader, judge=MockJudge(),
        ks=(5,), top_k=20, split="s", work_dir=str(tmp_path),
    )
    assert len(outcomes) == 1
    assert outcomes[0]["question_id"] == "pt_preference_001"
    assert outcomes[0]["label"] is True
    assert "Trattoria Roma" in outcomes[0]["hypothesis"]
    # single-session-preference rolls up under the paper's Information
    # Extraction category (report.PAPER_CATEGORY)
    acc = report["accuracy"]["per_category"]["Information Extraction"]
    assert acc["accuracy"] == 1.0


# ── 4. Pipeline guard: mini fixture stays green with the plumbing ─────────

def test_mini_pipeline_still_green_with_question_type(tmp_path):
    """The 5-question committed mini fixture still passes end-to-end with
    the question_type plumbing in place (no regression on other types)."""
    instances = json.loads(MINI.read_text(encoding="utf-8"))
    outcomes, report = run_evaluation(
        instances, reader=MockReader(), judge=MockJudge(),
        ks=(5, 10, 20), top_k=20, split="s", work_dir=str(tmp_path),
    )
    assert len(outcomes) == 5
    assert report["accuracy"]["overall"] >= 0.4
    assert report["accuracy"]["per_category"]["Information Extraction"]["accuracy"] == 1.0


# ── 5. render_context shape the reader relies on (regression) ─────────────

def test_render_context_carries_dates_for_temporal_reader():
    hits = [
        {"id": "x", "content": "[user] I adopted a dog three days ago.",
         "lme_session_index": 0, "session_date": "2025-06-12"},
    ]
    text = render_context(hits, question_date="2025-06-15")
    assert "Current Date: 2025-06-15" in text
    assert "session date 2025-06-12" in text
    # the prompt-faithful reader's date math works off this rendering
    m = _PromptFaithfulModel()
    out = m.complete(system="temporal reasoning", user=text)
    assert out == "3 days ago"


# ── 6. A1: partial-knowledge abstention clause (#1546, tightened #1762) ──

_ABS_QUESTION = {
    "question_id": "pt_abs_001_abs",
    "question_type": "single-session-user",
    "question": "What is Ava's favorite color?",
    "answer": "The user never mentioned a favorite color in the provided history.",
    "question_date": "2025-06-15",
    "haystack_session_ids": ["sess-1"],
    "haystack_dates": ["2025-06-10"],
    "answer_session_ids": [],
    "haystack_sessions": [[
        {"role": "user", "content": "I really like my new bicycle.",
         "has_answer": False},
        {"role": "assistant", "content": "Tell me more about it."},
    ]],
}

ALL_TYPES = ("single-session-user", "single-session-assistant",
             "single-session-preference", "multi-session",
             "temporal-reasoning", "knowledge-update")


def test_abstention_clause_present_for_all_question_types():
    """A1 is universal — abstention questions are indistinguishable by type
    (the _abs marker lives only in question_id, which never reaches the
    reader), so every question must carry the clause."""
    for t in (None, *ALL_TYPES):
        sys_prompt = system_prompt_for(t)
        assert "PARTIAL-KNOWLEDGE ABSTENTION" in sys_prompt, t
        assert "explicitly state" in sys_prompt.lower(), t
        assert "IS present" in sys_prompt, t


def test_abstention_clause_keeps_commit_side_guard():
    """A1 must not re-license the #1366 hedge: when the exact fact IS
    present the reader must answer directly, never abstain. #1762: the
    guard is now mid-sentence ('answer directly and concretely with it.
    Do NOT abstain…') so the marker assert is case-insensitive — the
    guard itself is unchanged in intent. ('exact information' alone is
    the literal-match phrase that caused the #1762 over-abstention, so it
    is NOT asserted as the discriminator — the commit directive is.)"""
    clause = _ABSTRACTION_FRAGMENT
    assert "do not abstain" in clause.lower()
    assert "answer directly and concretely with it" in clause.lower()


def test_abstention_clause_commits_on_present_value_any_phrasing():
    """#1762: the A1 commit test is the asked VALUE, not the wording — a
    present-but-differently-phrased value is the answer, never 'related
    information' (pilot finding #3, 6f9b354f: evidence_recall@20 = 1.0 yet
    the reader answered 'does not mention repainting…')."""
    clause = _ABSTRACTION_FRAGMENT
    low = clause.lower()
    assert "not whether it echoes the question's wording" in low
    assert "in any phrasing, it is the answer" in low
    # the hedge formulation is forbidden when the value IS the answer
    # (pilot 8a137a7f: the gold string sat inside the hedge)
    assert "forbidden when x is the answer" in low
    assert "never frame the answer value" in low
    # overshoot guard (#1762 review finding): a mention is only the
    # answer when the context states it as the fact — negated/rejected/
    # hypothetical mentions must not be committed to
    assert "negated, rejected, or hypothetical" in low
    assert "do not weaken your answer" in low


def test_abstention_clause_scopes_abstention_to_true_gaps():
    """#1762: abstention is reserved for genuine evidence gaps (vacuity) —
    the decoy guard and the evidence-backed abstention branch stay intact."""
    clause = _ABSTRACTION_FRAGMENT
    low = clause.lower()
    assert "genuinely absent" in low          # vacuity-only abstention
    assert "do not commit to a near-miss decoy" in low
    assert "explicitly state that the asked information is absent" in low
    assert "is present" in low                # states what IS present
    assert "contains nothing related" in low  # terminal vacuity branch


def test_abstention_clause_never_keyed_on_abs():
    """No prompt/fragment text references the _abs convention — the clause
    is evidence-derived by construction. (The universal-section identity —
    an abstention question gets the identical prompt as its non-abstention
    twin — is covered by test_untouched_types_keep_generic_prompt, which
    asserts the same equality for the abstention-questions' raw type.)"""
    all_text = _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT + \
        "".join(_TYPE_FRAGMENTS.values())
    assert "_abs" not in all_text


class _EvidenceBackedAbstainingModel:
    """Proves the clause is what unlocks the behavior: WITHOUT the A1 marker
    in the system prompt this fake commits to the decoy (the pre-A1 failure
    mode — MockJudge scores it wrong); WITH it, it states the related fact
    AND the absence (evidence-backed abstention — MockJudge scores it
    right)."""

    _TURN = re.compile(r"\[user\] (.*?)(?:\[assistant\]|\[session|$)", re.S)

    def complete(self, *, system: str, user: str) -> str:
        m = self._TURN.search(user)
        decoy = m.group(1).strip() if m else "some related information"
        if "PARTIAL-KNOWLEDGE ABSTENTION" in system:
            return (f"The memory mentions {decoy}, but it does not contain "
                    "the asked information.")
        return decoy  # pre-A1: commits to the near-miss decoy


def test_clean_abstention_evidence_backed_end_to_end(tmp_path):
    """A1: the reader abstains cleanly on an absent fact — stating what IS
    present and that the asked info is absent — judged correct by MockJudge
    (existing marker: 'does not contain')."""
    reader = LLMReader(_EvidenceBackedAbstainingModel(), model_id="a1-faithful")
    outcomes, _ = run_evaluation(
        [_ABS_QUESTION], reader=reader, judge=MockJudge(), ks=(5,),
        top_k=20, split="s", work_dir=str(tmp_path),
    )
    assert outcomes[0]["label"] is True
    hyp = outcomes[0]["hypothesis"]
    assert "bicycle" in hyp            # states what IS present
    assert "does not contain" in hyp   # … and that the asked info is absent


def test_decoy_commit_negative(tmp_path):
    """Owned negative (E2E-7): related-but-not-target fact in context must
    NOT be committed to. Without the clause the fake commits the decoy and
    MockJudge returns False; with the clause it abstains evidence-backed."""
    class _PreA1Model(_EvidenceBackedAbstainingModel):
        def complete(self, *, system, user):
            # strip the A1 marker so the fake takes the pre-A1 branch
            return super().complete(
                system=system.replace("PARTIAL-KNOWLEDGE ABSTENTION", "X"),
                user=user)
    pre = run_evaluation(
        [_ABS_QUESTION], reader=LLMReader(_PreA1Model(), model_id="pre-a1"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: decoy commit is wrong
    post = run_evaluation(
        [_ABS_QUESTION], reader=LLMReader(_EvidenceBackedAbstainingModel(),
                                          model_id="a1-faithful"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True          # green: clause present
    hyp = post[0][0]["hypothesis"]
    assert "bicycle" in hyp   # evidence-backed, not a content-free hedge
    assert "does not contain" in hyp


# ── #1762: commitment calibration on full evidence (pilot finding #3) ─────
# The V3 pilot's 4/4 fresh failures were reader over-abstention: the asked
# VALUE was fully in context yet the reader hedged ('does not mention…' /
# gold string inside a 'mentions X but does not contain…' hedge). These
# lock the tightening: full-evidence contexts commit; the hedge formulation
# never carries the answer value; genuine gaps still abstain (covered by
# test_clean_abstention_evidence_backed_end_to_end above).


def _overabstention_question() -> dict:
    """Mirrors pilot 6f9b354f: the asked value ('a lighter shade of gray')
    is fully in the retrieved context, but the context does not echo the
    question's verb ('repaint') — the pre-#1762 clause read that as a
    literal-match miss and abstained."""
    return {
        "question_id": "pt_commit_001",
        "question_type": "single-session-user",
        "question": "What color did Ava repaint her bedroom walls?",
        "answer": "a lighter shade of gray",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "user", "content": "I painted the walls a lighter "
             "shade of gray for a calming effect.", "has_answer": True},
            {"role": "assistant", "content": "That sounds relaxing."},
        ]],
    }


class _CommitOnPresentValueModel:
    """Fake reproducing the pilot's over-abstention: the asked VALUE is in
    context but phrased differently from the question, and the reader
    hedges. The green branch is CONTEXT-READING — it parses the value out
    of the rendered context (proving the retrieval→render pipeline
    delivered it), so the hypothesis assertions are not tautological; the
    red branch simulates the pre-#1762 hedge (pilot 6f9b354f's 'lighter
    gray walls' paraphrase, chosen so it does NOT embed the golden
    string)."""

    def complete(self, *, system: str, user: str) -> str:
        if "forbidden when X is the answer" not in system:
            # pre-#1762: the differently-phrased value is treated as
            # related information, so the reader over-abstains
            return ("The memory mentions lighter gray walls, but it does "
                    "not contain the asked information.")
        # #1762: the value in context IS the answer — commit directly.
        # Context-reading on purpose: if the rendered context ever stops
        # carrying the value (retrieval/render regression), the regex
        # misses and the fake falls back to the pre-#1762 hedge — the
        # assertions fail loudly instead of passing on a hardcoded gold.
        m = re.search(r"painted the walls (.+?) for ", user)
        if not m:
            return ("The memory mentions lighter gray walls, but it does "
                    "not contain the asked information.")
        return m.group(1).strip()


def test_present_value_commits_end_to_end(tmp_path):
    """#1762 red→green: on FULL evidence the reader commits instead of
    over-abstaining. Without the tightening the fake reproduces the pilot's
    hedge and MockJudge scores it wrong; with it the reader answers with
    the value and passes."""
    class _PreCommitModel(_CommitOnPresentValueModel):
        def complete(self, *, system, user):
            # strip the #1762 tightening so the fake takes the pre-#1762
            # branch (the code now ships the tightened clause)
            return super().complete(
                system=system.replace("forbidden when X is the answer", "X"),
                user=user)
    pre = run_evaluation(
        [_overabstention_question()],
        reader=LLMReader(_PreCommitModel(), model_id="pre-1762"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False          # red: over-abstention
    post = run_evaluation(
        [_overabstention_question()],
        reader=LLMReader(_CommitOnPresentValueModel(), model_id="post-1762"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True          # green: commits
    hyp = post[0][0]["hypothesis"]
    # value alone — no decoy bleed, no hedge framing (#1762 review:
    # 'do not weaken your answer with unrelated material')
    assert hyp == "a lighter shade of gray"
    assert "does not contain" not in hyp


_NON_ANSWER_MENTIONS = {
    "negated": "I never painted the walls gray — that color was never "
                "part of the plan.",
    "rejected": "Gray was ruled out for the walls — I would not want it.",
    "hypothetical": "If I ever painted the walls gray, it would be too "
                     "dark for the room.",
}


def _non_answer_mention_question(turn: str) -> dict:
    """Overshoot guard (#1762 review finding): the value string ('gray')
    appears in the context but is NOT the fact (negated / rejected /
    hypothetical) — the tightened 'in any phrasing' commit rule must not
    apply to a mention that is not the answer. Framed as an abstention
    question so MockJudge scores the evidence-backed abstention."""
    return {
        "question_id": "pt_commit_003_abs",
        "question_type": "single-session-user",
        "question": "What color did Ava repaint her bedroom walls?",
        "answer": "The user never mentioned repainting the bedroom walls.",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": [],
        "haystack_sessions": [[
            {"role": "user", "content": turn, "has_answer": False},
        ]],
    }


class _NonAnswerMentionModel:
    """Overshoot guard fake: with the guard sentence the reader abstains
    on a value mention that is negated/rejected/hypothetical
    (context-reading: it finds the mention pattern in the rendered
    context); without it, the 'in any phrasing' commit rule commits to the
    non-answer value — the hallucination the #1762 calibration must not
    introduce."""

    _NON_ANSWER = re.compile(
        r"never painted the walls|ruled out for the walls|"
        r"If I ever painted the walls")

    def complete(self, *, system: str, user: str) -> str:
        # gate on the OPERATIVE rule ('must not be committed to'), not the
        # topic phrase — a rule-weakening that keeps 'negated, rejected,
        # or hypothetical' must flip this branch red (review #1762)
        guarded = ("must not be committed to" in system
                   and self._NON_ANSWER.search(user))
        if guarded:
            return ("The memory does not mention repainting the bedroom "
                    "walls.")
        return "gray"  # pre-guard: commits to the non-answer value


@pytest.mark.parametrize("turn",
                         [pytest.param(v, id=k)
                          for k, v in _NON_ANSWER_MENTIONS.items()])
def test_non_answer_mention_not_committed(tmp_path, turn):
    """#1762 overshoot guard red→green: a value that appears in the
    context but is negated/rejected/hypothetical is NOT the answer — the
    reader abstains, never commits (the tightening must not license
    hallucination)."""
    class _PreGuardModel(_NonAnswerMentionModel):
        def complete(self, *, system, user):
            # strip the guard sentence so the fake takes the pre-guard
            # branch (commits to the non-answer value)
            return super().complete(
                system=system.replace(
                    "A mere mention is not the answer: a negated, "
                    "rejected, or hypothetical mention, or a different "
                    "value for the asked attribute, does not answer the "
                    "question and must not be committed to.", "X"),
                user=user)
    pre = run_evaluation(
        [_non_answer_mention_question(turn)],
        reader=LLMReader(_PreGuardModel(), model_id="pre-guard"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False    # red: commits the non-answer
    post = run_evaluation(
        [_non_answer_mention_question(turn)],
        reader=LLMReader(_NonAnswerMentionModel(), model_id="post-guard"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True    # green: evidence-backed
    hyp = post[0][0]["hypothesis"]        # abstention
    assert "does not mention" in hyp
    assert "gray" not in hyp.lower()      # no commit to the non-answer


def test_fake_commit_branch_keys_on_operative_rule():
    """Test-sensitivity guard (#1762 review): the fake's commit branch must
    key on the OPERATIVE rule, not mere marker presence. Mutating the rule
    (removing the hedge ban) while keeping the rest of the clause flips the
    fake to its pre-#1762 hedge branch — so a semantic regression that
    preserves the pinned phrases would be caught by the e2e tests."""
    user = ("[user] I painted the walls a lighter shade of gray for a "
            "calming effect.")
    mutated = system_prompt_for("single-session-user").replace(
        "forbidden when X is the answer", "allowed when X is the answer")
    assert "does not contain" in _CommitOnPresentValueModel().complete(
        system=mutated, user=user)             # rule removed → hedges
    assert "a lighter shade of gray" in _CommitOnPresentValueModel().complete(
        system=system_prompt_for("single-session-user"), user=user)


def test_guard_branch_keys_on_operative_rule():
    """Test-sensitivity guard (#1762 review): the overshoot-guard fake must
    key on the operative rule ('must not be committed to'), not the topic
    phrase — mutating the rule flips the fake to its pre-guard commit, so a
    rule-weakening regression fails loudly instead of passing."""
    user = ("[user] I never painted the walls gray — that color was never "
            "part of the plan.")
    mutated = system_prompt_for("single-session-user").replace(
        "must not be committed to.", "may be committed to.")
    assert "gray" in _NonAnswerMentionModel().complete(
        system=mutated, user=user)             # rule weakened → commits
    assert "does not mention" in _NonAnswerMentionModel().complete(
        system=system_prompt_for("single-session-user"), user=user)


def _vacuity_question() -> dict:
    """Terminal vacuity branch (#1762 review finding): the context
    contains NOTHING related to the asked attribute — the reader must
    abstain rather than fabricate. Framed as an abstention question."""
    return {
        "question_id": "pt_commit_004_abs",
        "question_type": "single-session-user",
        "question": "What color did Ava repaint her bedroom walls?",
        "answer": "The user never mentioned repainting the bedroom walls.",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": [],
        "haystack_sessions": [[
            {"role": "user", "content": "I just bought a new bicycle.",
             "has_answer": False},
        ]],
    }


class _VacuityModel:
    """Terminal-branch fake — CONTEXT-READING: abstains on a genuinely
    empty context (nothing related), commits to the asked value when the
    context states it (control: not unconditional abstention), fabricates
    without the clause."""

    def complete(self, *, system: str, user: str) -> str:
        if "PARTIAL-KNOWLEDGE ABSTENTION" not in system:
            return "gray"  # pre-A1: fabricates a value from nothing
        m = re.search(r"painted the walls (.+?) for ", user)
        if m:
            return m.group(1).strip()  # value present → commit (control)
        return ("The memory does not mention repainting the bedroom "
                "walls.")


def test_empty_context_abstains_not_fabricates(tmp_path):
    """#1762 terminal vacuity branch: with NOTHING related in context the
    reader abstains ('does not mention') — never fabricates a value. Note:
    the clause's literal terminal phrasing ('the asked information is
    absent') is not in MockJudge's marker list (judge.py is outside this
    change's files), so the fake uses the marker-compatible 'does not
    mention' formulation the judge scores."""
    class _PreClauseModel(_VacuityModel):
        def complete(self, *, system, user):
            # strip the A1 marker so the fake takes the pre-A1 branch
            return super().complete(
                system=system.replace("PARTIAL-KNOWLEDGE ABSTENTION", "X"),
                user=user)
    pre = run_evaluation(
        [_vacuity_question()], reader=LLMReader(_PreClauseModel(),
                                                model_id="pre-a1"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False       # red: fabrication
    post = run_evaluation(
        [_vacuity_question()], reader=LLMReader(_VacuityModel(),
                                                model_id="vacuity"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True       # green: abstains
    hyp = post[0][0]["hypothesis"]
    assert "does not mention" in hyp
    assert "gray" not in hyp.lower()         # no fabricated value
    # control: the fake is not unconditionally abstaining — with the value
    # in the rendered context it commits (the green leg depends on the
    # context being empty, not on the clause marker alone)
    ctrl = _VacuityModel().complete(
        system=system_prompt_for("single-session-user"),
        user="[user] I painted the walls a lighter shade of gray for a "
             "calming effect.")
    assert ctrl == "a lighter shade of gray"


def _near_miss_question() -> dict:
    """Same-attribute near-miss (E2E-7 intent, code review #1768): the
    context ADDRESSES the asked attribute with a DIFFERENT value ('muted
    blue') — the asked value is absent, so the reader must abstain
    evidence-backed, never commit the near-miss. The vacuity-only license
    ('nothing addresses the attribute') would deadlock the commit/abstain
    decision and push a commit of the near-miss value."""
    return {
        "question_id": "pt_commit_006_abs",
        "question_type": "single-session-user",
        "question": "What color did Ava repaint her bedroom walls?",
        "answer": "The user never mentioned repainting the bedroom walls.",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": [],
        "haystack_sessions": [[
            {"role": "user", "content": "I painted the walls muted blue "
             "— a calming tone.", "has_answer": False},
        ]],
    }


class _NearMissModel:
    """Near-miss license fake: with the corrected license (near-miss
    information is abstention-licensed) the reader abstains on a
    same-attribute different-value context; with the vacuity-only license
    it commits the near-miss value (the confident-wrong class)."""

    def complete(self, *, system: str, user: str) -> str:
        del user
        if "holds related or near-miss information" in system:
            return ("The memory mentions muted blue walls, but it does not "
                    "contain the asked information.")
        return "muted blue"  # vacuity-only license: commits the near-miss


def test_near_miss_same_attribute_abstains(tmp_path):
    """#1762 review (E2E-7 intent): a DIFFERENT value for the asked
    attribute in context is not the answer — the reader abstains
    evidence-backed, never commits the near-miss."""
    class _PreLicenseModel(_NearMissModel):
        def complete(self, *, system, user):
            # strip the corrected license so the fake takes the
            # vacuity-only branch (commits the near-miss)
            return super().complete(
                system=system.replace(
                    "Abstain when the asked value is genuinely absent — "
                    "whether the context is empty, unrelated, or holds "
                    "related or near-miss information", "X"),
                user=user)
    pre = run_evaluation(
        [_near_miss_question()], reader=LLMReader(_PreLicenseModel(),
                                                  model_id="pre-license"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert pre[0][0]["label"] is False       # red: commits the near-miss
    post = run_evaluation(
        [_near_miss_question()], reader=LLMReader(_NearMissModel(),
                                                  model_id="post-license"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert post[0][0]["label"] is True       # green: evidence-backed
    hyp = post[0][0]["hypothesis"]           # abstention
    assert "muted blue" in hyp               # states what IS present
    assert "does not contain" in hyp         # … and that asked is absent


def _mixed_mention_question() -> dict:
    """Commit + overshoot guard interaction (#1762 review): the context
    holds BOTH a negated mention of the value AND the affirmative fact —
    the reader must commit to the affirmative fact, not let the negation
    trigger abstention (the over-abstention class this issue fixes)."""
    return {
        "question_id": "pt_commit_005",
        "question_type": "single-session-user",
        "question": "What color did Ava repaint her bedroom walls?",
        "answer": "a lighter shade of gray",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "user", "content": "I never painted the walls gray "
             "— that was never the plan.", "has_answer": False},
            {"role": "user", "content": "I painted the walls a lighter "
             "shade of gray for a calming effect.", "has_answer": True},
        ]],
    }


def test_mixed_negated_and_affirmative_commits(tmp_path):
    """#1762: when the context carries BOTH a negated mention and the
    affirmative fact, the reader commits to the fact — the negation must
    not trigger abstention. Uses the context-reading
    _CommitOnPresentValueModel: its regex matches the affirmative 'painted
    the walls <value> for ' phrase, so the hypothesis is the value read
    from the rendered context."""
    reader = LLMReader(_CommitOnPresentValueModel(), model_id="commit")
    outcomes, _ = run_evaluation(
        [_mixed_mention_question()], reader=reader, judge=MockJudge(),
        ks=(5,), top_k=20, split="s", work_dir=str(tmp_path))
    assert outcomes[0]["label"] is True
    hyp = outcomes[0]["hypothesis"]
    assert hyp == "a lighter shade of gray"
    assert "does not contain" not in hyp
    assert "does not mention" not in hyp


def _hedge_embedding_question() -> dict:
    """Mirrors pilot 8a137a7f: the gold string ('Philips LED bulb') is in
    the context and inside the hypothesis, but framed as a hedge — the
    official judge rejects that under the subset rule ('if the response
    only contains a subset of the information required by the answer')."""
    return {
        "question_id": "pt_commit_002",
        "question_type": "single-session-user",
        "question": "Which bulb did Ava replace in her bedside lamp?",
        "answer": "Philips LED bulb",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sess-1"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": ["sess-1"],
        "haystack_sessions": [[
            {"role": "assistant", "content": "Did you replace the bulb?",
             "has_answer": False},
            {"role": "user", "content": "Yes, with the Philips LED bulb.",
             "has_answer": True},
        ]],
    }


class _HedgeEmbeddingModel:
    """Fake reproducing pilot 8a137a7f: the gold string sits INSIDE the
    hedge ('mentions a Philips LED bulb… but it does not contain any
    information about replacing it'). The green branch is CONTEXT-READING
    (parses the bulb value out of the rendered context); the red branch is
    the pre-#1762 hedge with the gold embedded."""

    def complete(self, *, system: str, user: str) -> str:
        if "forbidden when X is the answer" not in system:
            return ("The memory mentions a Philips LED bulb in your "
                    "bedside lamp, but it does not contain any information "
                    "about replacing it.")
        # #1762: state the value as the answer, never as a hedge.
        # Context-reading on purpose: if the rendered context ever stops
        # carrying the bulb value, the regex misses and the fake falls
        # back to the pre-#1762 hedge — the assertions fail loudly.
        m = re.search(r"with the (.+?)\.", user)
        if not m:
            return ("The memory mentions a Philips LED bulb in your "
                    "bedside lamp, but it does not contain any information "
                    "about replacing it.")
        return m.group(1).strip()


def test_hedge_never_embeds_the_answer_value(tmp_path):
    """#1762: the 'mentions X but does not contain the asked information'
    formulation must not be used when X is the answer (pilot 8a137a7f —
    the official judge rejects that under its subset rule). MockJudge uses
    plain containment (the embedded gold would pass it — the subset rule
    is judge-side, outside this change's files), so the discriminating
    assertion is the hypothesis TEXT: the red branch wraps the answer in
    the hedge, the green branch states it directly."""
    class _PreHedgeModel(_HedgeEmbeddingModel):
        def complete(self, *, system, user):
            # strip the #1762 tightening so the fake takes the pre-#1762
            # branch (gold string inside the hedge)
            return super().complete(
                system=system.replace("forbidden when X is the answer", "X"),
                user=user)
    pre = run_evaluation(
        [_hedge_embedding_question()],
        reader=LLMReader(_PreHedgeModel(), model_id="pre-1762"),
        judge=MockJudge(), ks=(5,), top_k=20, split="s",
        work_dir=str(tmp_path))
    assert "does not contain" in pre[0][0]["hypothesis"]  # red: hedge
    reader = LLMReader(_HedgeEmbeddingModel(), model_id="hedge-faithful")
    outcomes, _ = run_evaluation(
        [_hedge_embedding_question()], reader=reader, judge=MockJudge(),
        ks=(5,), top_k=20, split="s", work_dir=str(tmp_path))
    hyp = outcomes[0]["hypothesis"]
    assert "Philips LED bulb" in hyp
    assert "does not contain" not in hyp   # green: no hedge framing
    assert outcomes[0]["label"] is True


def test_abs_never_crosses_reader_call_site(tmp_path):
    """E2E-7: the reader call site receives question_type only — no
    abstention kwarg, no question_id, and the reader-visible context text
    never contains the _abs marker. (Raw hit session_id provenance may
    carry the qid under deterministic ingest — that is metadata, not a
    flag, and is not rendered; the assertion targets prompt text + shape.)"""
    reader = _RecordingReader()
    run_evaluation([_ABS_QUESTION], reader=reader, judge=MockJudge(),
                   ks=(5,), top_k=20, split="s", work_dir=str(tmp_path))
    call = reader.calls[0]
    assert set(call) == {"context_hits", "question", "question_date",
                         "question_type"}
    assert "abstention" not in call and "question_id" not in call
    assert call["question_type"] == "single-session-user"  # raw type, never
    # "abstention"/None-for-abs
    text = render_context(call["context_hits"],
                          question_date=call["question_date"])
    assert "_abs" not in text and "pt_abs_001_abs" not in text
