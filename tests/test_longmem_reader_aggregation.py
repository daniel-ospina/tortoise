"""A2 (#1547) reader aggregation tests — knowledge-update answer-from-newer
+ date-conditional rule, multi-session aggregation discipline (epic #1509,
Extractor V3).

The two weak categories consume the ontology state the reader context ALREADY
carries (E5 #1537 + #1367/#1353): the ``[SUPERSEDED BY: …]`` /
``[SUPERSES: …]`` markers (CORRECTS edges) and the ``(session date
YYYY-MM-DD)`` annotations (E1). A2 adds ONLY the two type-tailored prompt
fragments — no parallel mechanism, no new fields, no new retrieval path.
V3 point-in-time restore = E5's chain-walk by session date (E2E-9); first-class
``valid_at``/``invalid_at`` windows are E6's, post-baseline, out of scope.

These tests lock:

  1. the prompt content — the KU fragment's supersession vocabulary + both
     date-conditional branches; the MSR fragment's distinct-event /
     no-double-count / reconcile-by-date vocabulary; the A1 abstention
     license stays open in both,
  2. the dispatch — ``knowledge-update`` / ``multi-session`` registered in
     ``_TYPE_FRAGMENTS`` and routed by ``system_prompt_for``,
  3. the behavior — a prompt-faithful reader answers the superseding value
     (current-value), the value valid at the asked date via chain-walk
     (point-in-time, current value as context only), abstains when no
     version covers the asked date, counts a restated fact once, and
     synthesizes distinct decisions across sessions,
  4. the E2E-6/E2E-9 chain end-to-end — a REAL graph on embedded
     FalkorDBLite: production ``supersede_point`` → CORRECTS edge, the
     superseded point co-retrieves (``include_terminal=True``, E5), the
     marker renders, and the prompt-faithful reader answers the newest /
     the historical value.

Fully offline: embedded FalkorDBLite, fake recording/prompt-faithful models,
no API keys, no dataset download.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK  # noqa: E402, F401, I001, RUF100

from tools.longmem_eval.reader import (  # noqa: E402, RUF100
    LLMReader, _ABSTRACTION_FRAGMENT, _KNOWLEDGE_UPDATE_FRAGMENT,
    _MULTI_SESSION_FRAGMENT, _SYSTEM_PROMPT, _TYPE_FRAGMENTS,
    system_prompt_for,
)
from tools.longmem_eval.retrieve import (  # noqa: E402, RUF100
    render_context, retrieve_for_question,
)

_ABSTRACTION = "PARTIAL-KNOWLEDGE ABSTENTION"


# ── Hand-built hit fixtures (the annotated retrieval shape, #1367) ────────

def _superseded_pair() -> list[dict]:
    """Two versions of the gym-schedule fact linked by supersession: session 0
    (2025-06-02) replaced by session 1 (2025-06-16)."""
    return [
        {"id": "gym_old", "content": "gym class is at 6pm on weekdays.",
         "lme_session_index": 0, "session_date": "2025-06-02",
         "superseded_by": {"id": "gym_new",
                           "content_snippet": "gym class is now at 5pm on weekdays."},
         "supersedes": []},
        {"id": "gym_new", "content": "gym class is now at 5pm on weekdays.",
         "lme_session_index": 1, "session_date": "2025-06-16",
         "superseded_by": None,
         "supersedes": [{"id": "gym_old",
                         "content_snippet": "gym class is at 6pm on weekdays."}]},
    ]


def _fresh_sdk(tmp_path) -> TortoiseSDK:
    return TortoiseSDK(str(tmp_path / "lme.db"))


# ── 1. Prompt content locks ───────────────────────────────────────────────

def test_ku_prompt_contains_answer_from_newer_instructions():
    """E2E-6 / review P2: the KU fragment carries the supersession
    vocabulary (ontology terms: subject + supersession edges + session
    date) AND both date-conditional branches — current-value → newest,
    point-in-time → chain-walk, current value as context only."""
    f = _KNOWLEDGE_UPDATE_FRAGMENT
    low = f.lower()
    # ontology terms the reader reasons over
    assert "superseded" in low and "superseding" in low
    assert "newest" in low
    assert "session date" in low
    assert "same subject and attribute" in low
    # current-value branch → answer from the superseding version
    assert "current value" in low
    assert "superseded entries are context only" in low
    # point-in-time branch → chain-walk by session date
    assert "latest on or before the asked date" in low
    assert "walk" in low and "supersession chain" in low
    assert "never as the answer" in low
    # abstention license (A1 #1546 stays open)
    assert "say you do not know" in low


def test_msr_prompt_contains_aggregation_discipline():
    """Issue checklist MSR discipline: distinct events, no double-count,
    reconcile by date — in ontology terms, with the abstention license."""
    f = _MULTI_SESSION_FRAGMENT
    low = f.lower()
    assert "distinct" in low
    assert "once" in low
    assert "double-count" in low
    assert "session date" in low
    assert "reconcile" in low
    assert "superseded by" in low and "superses" in low
    assert "synthesizing" in low and "do not dump every entry" in low
    assert "say you do not know" in low


def test_ku_and_msr_registered_in_type_fragments():
    """A2 scope: both keys registered; dispatch appends the fragment to the
    generic system prompt (the A1 abstention clause is universal)."""
    assert "knowledge-update" in _TYPE_FRAGMENTS
    assert "multi-session" in _TYPE_FRAGMENTS
    assert _TYPE_FRAGMENTS["knowledge-update"] is _KNOWLEDGE_UPDATE_FRAGMENT
    assert _TYPE_FRAGMENTS["multi-session"] is _MULTI_SESSION_FRAGMENT
    ku = system_prompt_for("knowledge-update")
    msr = system_prompt_for("multi-session")
    assert ku == _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT \
        + _KNOWLEDGE_UPDATE_FRAGMENT
    assert msr == _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT \
        + _MULTI_SESSION_FRAGMENT
    # the fragments never key on the _abs convention (evidence-derived only)
    assert "_abs" not in ku and "_abs" not in msr


def test_untouched_types_keep_generic_prompt():
    """Regression guard: single-session-user/-assistant stay generic — no
    supersession/aggregation vocabulary leaks into untouched types."""
    for qt in ("single-session-user", "single-session-assistant"):
        sys_prompt = system_prompt_for(qt)
        assert sys_prompt == _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT, qt
        assert "supersed" not in sys_prompt.lower(), qt
        assert "aggregate" not in sys_prompt.lower(), qt


# ── 2. Behavior: the fragment unlocks the reasoning (prompt-faithful) ─────

_SESSION_DATE = re.compile(r"session date (\d{4}-\d{2}-\d{2})")


class _AggregationFaithfulModel:
    """Fake model that behaves like the real reader INSTRUCTED by the A2
    fragments (mirrors the existing ``_PromptFaithfulModel`` pattern):

    - KU fragment present: (a) current-value question (no date in the
      question) → answer the NEWEST entry by session date (the superseding
      point); (b) point-in-time question (date in the question) → chain-walk:
      the entry whose session date is the latest on/before the asked date,
      with the current value appended ONLY as trailing context; no version
      covers the asked date → abstain.
    - MSR fragment present: dedupe entry values across the dated sessions
      (same fact restated is the SAME event) and join the distinct ones.
    - Anything else → the abstention marker.

    Proves the prompt, not retrieval, unlocks the answer-from-newer /
    aggregation behavior.
    """

    _PREFIX = re.compile(
        r"^\s*(?:\[[^\]]*\]|\(session date [^)]*\))(?:\s|$)")

    def complete(self, *, system: str, user: str) -> str:
        if "KNOWLEDGE-UPDATE INSTRUCTIONS" in system:
            return self._knowledge_update(user)
        if "MULTI-SESSION REASONING INSTRUCTIONS" in system:
            return self._multi_session(user)
        return "I do not know; the history does not mention it."

    def _entries(self, user: str) -> list[tuple[str, str]]:
        """(session_date, content) per dated context block, rendering order."""
        out: list[tuple[str, str]] = []
        for block in user.split("\n\n"):
            md = _SESSION_DATE.search(block)
            if not md:
                continue
            rest = block
            while True:
                pm = self._PREFIX.match(rest)
                if not pm:
                    break
                rest = rest[pm.end():]
            out.append((md.group(1), rest.strip()))
        return out

    @staticmethod
    def _question(user: str) -> str:
        m = re.search(r"Question: (.*)", user)
        return m.group(1).strip() if m else ""

    def _knowledge_update(self, user: str) -> str:
        question = self._question(user)
        entries = self._entries(user)
        if not entries:
            return "I do not know; the history does not mention it."
        asked = re.search(r"(\d{4}-\d{2}-\d{2})", question)
        if asked:
            # point-in-time → chain-walk by session date (E2E-9 V3 mechanism)
            d = date.fromisoformat(asked.group(1))
            valid = [e for e in entries if date.fromisoformat(e[0]) <= d]
            if not valid:
                return "I do not know; the history does not mention it."
            answer = max(valid)[1]
            newest = max(entries)[1]
            if newest != answer:
                # current value as CONTEXT ONLY, never the answer
                answer = f"{answer} (the current value is {newest})"
            return answer
        # current-value → answer from the newest, superseding point
        return max(entries)[1]

    def _multi_session(self, user: str) -> str:
        distinct: list[str] = []
        for _, content in self._entries(user):
            if content not in distinct:
                distinct.append(content)
        if not distinct:
            return "I do not know; the history does not mention it."
        return " ; ".join(distinct)


def test_ku_current_value_answers_superseding_point():
    """E2E-6: a current-value question answers the NEW superseding value —
    the superseded one is context only, never the answer."""
    reader = LLMReader(_AggregationFaithfulModel(), model_id="a2-faithful")
    hyp = reader.answer(context_hits=_superseded_pair(),
                        question="What time does Ava's gym class meet "
                                 "these days?",
                        question_date="2025-06-16",
                        question_type="knowledge-update")
    assert "5pm" in hyp
    assert "6pm" not in hyp


def test_ku_point_in_time_answers_value_valid_at_asked_date():
    """E2E-9 (V3 mechanism = E5's chain-walk): a point-in-time question
    between the two sessions answers the value valid at the asked date
    (6pm); the current value (5pm) appears only as trailing context."""
    reader = LLMReader(_AggregationFaithfulModel(), model_id="a2-faithful")
    hyp = reader.answer(context_hits=_superseded_pair(),
                        question="What time was Ava's gym class at on "
                                 "2025-06-10?",
                        question_date="2025-06-15",
                        question_type="knowledge-update")
    assert "6pm" in hyp
    assert "5pm" in hyp            # present, but…
    assert hyp.index("6pm") < hyp.index("5pm")  # …only as trailing context


def test_ku_point_in_time_ambiguous_restore_abstains():
    """E2E-9 owned-negative: an asked date before the earliest session
    covers no version → the reader abstains (A1 marker), never guesses."""
    reader = LLMReader(_AggregationFaithfulModel(), model_id="a2-faithful")
    hyp = reader.answer(context_hits=_superseded_pair(),
                        question="What time was Ava's gym class at on "
                                 "2025-05-01?",
                        question_date="2025-06-15",
                        question_type="knowledge-update")
    assert "do not know" in hyp.lower()


def test_msr_same_fact_restated_is_one_event():
    """MSR discipline: the same fact restated in a later session is the SAME
    event — the answer counts it once (no double-count)."""
    hits = [
        {"id": "a", "content": "We decided to go to Tokyo.",
         "lme_session_index": 0, "session_date": "2025-06-01"},
        {"id": "b", "content": "We decided to go to Tokyo.",
         "lme_session_index": 1, "session_date": "2025-06-12"},
    ]
    reader = LLMReader(_AggregationFaithfulModel(), model_id="a2-faithful")
    hyp = reader.answer(context_hits=hits,
                        question="How many times did Ava decide on Tokyo?",
                        question_date="2025-06-15",
                        question_type="multi-session")
    assert "Tokyo" in hyp
    assert hyp.count("Tokyo") == 1  # restated fact counted exactly once


def test_msr_aggregates_distinct_decisions_across_sessions():
    """MSR discipline: complementary facts across sessions synthesize into
    the decision (Tokyo in November), not a dump of every entry."""
    hits = [
        {"id": "a", "content": "I really want to go to Tokyo.",
         "lme_session_index": 0, "session_date": "2025-06-01"},
        {"id": "b", "content": "November it is, we travel in November.",
         "lme_session_index": 1, "session_date": "2025-06-12"},
    ]
    reader = LLMReader(_AggregationFaithfulModel(), model_id="a2-faithful")
    hyp = reader.answer(context_hits=hits,
                        question="Where and when did Ava decide to travel?",
                        question_date="2025-06-15",
                        question_type="multi-session")
    assert "Tokyo" in hyp and "November" in hyp
    # a synthesis of the distinct decisions (each surfaced once), not a
    # verbatim re-dump of both session blocks verbatim
    assert hyp.count("Tokyo") == 1
    assert " ; " in hyp  # the faithful model joins distinct decisions


# ── 3. E2E-6 / E2E-9 chain end-to-end (real graph, embedded FalkorDBLite) ─

def _supersession_graph(tmp_path):
    """Write the two gym-schedule points + production supersede_point
    (CORRECTS edge) on a real embedded graph. Returns the open SDK."""
    sdk = _fresh_sdk(tmp_path)
    sdk.create_point(
        "statement", "gym class is at 6pm on weekdays.", id="gym_old",
        status="live", lme_question_id="a2_ku_e2e", lme_session_index=0,
        is_episodic=True)
    sdk.create_point(
        "statement", "gym class is now at 5pm on weekdays.", id="gym_new",
        status="live", lme_question_id="a2_ku_e2e", lme_session_index=1,
        is_episodic=True)
    sdk.supersede_point("gym_old", "gym_new")
    return sdk


def _ku_question(question: str, qid: str = "a2_ku_e2e") -> dict:
    return {
        "question_id": qid,
        "question_type": "knowledge-update",
        "question": question,
        "answer": "5pm",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["s0", "s1"],
        "haystack_dates": ["2025-06-02", "2025-06-16"],
        "answer_session_ids": ["s1"],
        "haystack_sessions": [[{"role": "user", "content": "gym at 6pm",
                                "has_answer": False}],
                              [{"role": "user", "content": "gym now 5pm",
                                "has_answer": True}]],
    }


def test_supersession_chain_renders_and_answers_newest_end_to_end(tmp_path):
    """E2E-6 aligned (offline): production supersede_point → CORRECTS edge;
    the superseded point co-retrieves with include_terminal=True (E5); the
    [SUPERSEDED BY: …] marker renders; the prompt-faithful reader answers
    the NEWEST value (5pm)."""
    sdk = _supersession_graph(tmp_path)
    try:
        # base retrieval (E5 wiring): the superseded point must survive
        raw = sdk.tortoise_fts_query(
            "gym class", entity_type="point", limit=20,
            include_terminal=True)
        ids = {h["id"] for h in raw}
        assert "gym_old" in ids and "gym_new" in ids
        old_raw = next(h for h in raw if h["id"] == "gym_old")
        assert old_raw.get("superseded_by", {}).get("id") == "gym_new"

        # retrieve_for_question-style annotation → the marker renders
        q = _ku_question("What time does Ava's gym class meet these days?")
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        text = render_context(ret["hits"], question_date=q["question_date"])
        assert "[SUPERSEDED BY: gym class is now at 5pm on weekdays.]" in text

        # the prompt-faithful reader answers the newest value
        reader = LLMReader(_AggregationFaithfulModel(), model_id="a2-faithful")
        hyp = reader.answer(context_hits=ret["hits"], question=q["question"],
                            question_date=q["question_date"],
                            question_type="knowledge-update")
        assert "5pm" in hyp
        assert "6pm" not in hyp
    finally:
        sdk.close()


def test_chain_walk_answers_historical_value_end_to_end(tmp_path):
    """E2E-9 aligned (offline): V3 restore = E5's chain-walk. A point-in-time
    question between the two sessions answers the value valid at the asked
    date (6pm) with the current value (5pm) only as context; a date before
    the chain abstains."""
    sdk = _supersession_graph(tmp_path)
    try:
        q = _ku_question("What time was Ava's gym class at on 2025-06-10?")
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        reader = LLMReader(_AggregationFaithfulModel(), model_id="a2-faithful")
        hyp = reader.answer(context_hits=ret["hits"], question=q["question"],
                            question_date=q["question_date"],
                            question_type="knowledge-update")
        assert "6pm" in hyp
        assert "5pm" in hyp
        assert hyp.index("6pm") < hyp.index("5pm")  # current value = context

        # ambiguous restore: asked date predates the chain → abstain
        q2 = _ku_question("What time was Ava's gym class at on 2025-05-01?")
        ret2 = retrieve_for_question(sdk, q2, ks=(5,), top_k=20)
        hyp2 = reader.answer(context_hits=ret2["hits"],
                             question=q2["question"],
                             question_date=q2["question_date"],
                             question_type="knowledge-update")
        assert "do not know" in hyp2.lower()
    finally:
        sdk.close()
