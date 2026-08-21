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

import pytest  # noqa: F401

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
    # dated evidence: the type fragment tells the reader to commit
    assert "do not hedge" in system


def test_preference_prompt_contains_option_commitment_instructions():
    r = LLMReader(_RecordingModel(), model_id="fake")
    r.answer(context_hits=[{"id": "x", "content": "hi", "has_answer": True}],
             question="q", question_type="single-session-preference")
    system = r._model.calls[0][0]
    assert "preference" in system.lower()
    assert "option" in system.lower()
    # no hedge license when the preference is in context
    assert "do not hedge" in system


def test_temporal_prompt_preserves_abstention_license():
    """The real S split has temporal _abs questions (dated context, missing
    event) where the reader MUST abstain — the commit-first temporal fragment
    must keep the abstention path open (reviewer P2 #1366)."""
    r = LLMReader(_RecordingModel(), model_id="fake")
    r.answer(context_hits=[{"id": "x", "content": "hi", "has_answer": True}],
             question="q", question_date="2025-06-15",
             question_type="temporal-reasoning")
    system = r._model.calls[0][0]
    assert "commit" in system.lower()  # commit-first for evidence-present
    assert "dated evidence" in system.lower()  # … but abstain when the
    # event is absent from the dated context (judge requires markers)


def test_other_types_keep_generic_prompt():
    r = LLMReader(_RecordingModel(), model_id="fake")
    r.answer(context_hits=[{"id": "x", "content": "hi", "has_answer": True}],
             question="q", question_type="knowledge-update")
    system = r._model.calls[0][0]
    # the generic hardened prompt (no type-specific fragment for KU)
    assert "knowledge-update" not in system
    assert "temporal" not in system.lower()
    assert "preference" not in system.lower()
    assert system == _SYSTEM_PROMPT or system.startswith(_SYSTEM_PROMPT)


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
    the official judge); without the fragment it would hedge and fail."""
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
    and fail."""
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


# ── 6. A1: partial-knowledge abstention clause (#1546) ────────────────────

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
    present the reader must answer directly, never abstain."""
    clause = _ABSTRACTION_FRAGMENT
    assert "do NOT abstain" in clause
    assert "exact information" in clause


def test_abstention_clause_never_keyed_on_abs():
    """No prompt/fragment text references the _abs convention — the clause
    is evidence-derived by construction."""
    all_text = _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT + \
        "".join(_TYPE_FRAGMENTS.values())
    assert "_abs" not in all_text
    # an abstention question gets the identical universal section as its
    # non-abstention twin of the same type
    assert system_prompt_for("single-session-user") == \
        _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT


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
