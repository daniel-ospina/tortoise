"""Product reader tests (#1987 Tasks 1-2) — tortoise/reader.py.

Task 1 — the product owns ALL reader prompt text and the reader class:
  * golden-hash snapshot of every ported constant (byte-identity guard —
    the hashes were captured from the PRE-move eval constants in
    tools/longmem_eval/reader.py / preflight.py; any byte drift fails),
  * re-export identity: the eval reader's constants/classes ARE the
    product's (is-identity — drift impossible by construction),
  * LLMReader.ping works against a stub model (no eval-relative import),
  * NO_EVIDENCE_TEXT pinned to the A1 abstention phrasing (NEW product
    code — deliberately NOT part of the byte-identity snapshot).
Task 2 — deterministic type detection + the best-effort abstained label:
  * detect_question_type ordered precedence TR→KU→MS→SSP→None,
  * _looks_abstained phrase list + blank/whitespace → True,
  * LLMReader.answer + _looks_abstained over empty/whitespace/refusal
    outputs → blank → abstained=True with NO_EVIDENCE_TEXT substitution.

Fully offline: stub models only, no API keys, no DB.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.reader import (  # noqa: E402, RUF100
    DEFAULT_READER_MAX_TOKENS,
    LLMReader,
    NO_EVIDENCE_TEXT,
    PROBE_SYSTEM,
    Reader,
    _ABSTRACTION_FRAGMENT,
    _ABSTAINED_PHRASES,
    _KNOWLEDGE_UPDATE_FRAGMENT,
    _MULTI_SESSION_FRAGMENT,
    _SYSTEM_PROMPT,
    _TYPE_FRAGMENTS,
    _looks_abstained,
    detect_question_type,
    reader_prompt_constants,
    system_prompt_for,
)


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ══ Task 1: golden-hash snapshot (byte-identity guard) ═════════════════════

# Golden hashes captured from the PRE-move eval constants
# (tools/longmem_eval/reader.py + tools/longmem_eval/preflight.py, commit
# aeb163ab-era) — a byte change in ANY ported constant fails the snapshot.
_GOLDEN = {
    "_SYSTEM_PROMPT": "0a4140a708890f69",
    "_ABSTRACTION_FRAGMENT": "715039d20a5ec054",
    "_TEMPORAL_FRAGMENT": "592395e8f607e749",
    "_PREFERENCE_FRAGMENT": "9a59cae923aedbd7",
    "_KNOWLEDGE_UPDATE_FRAGMENT": "71be4f3f3c08540a",
    "_MULTI_SESSION_FRAGMENT": "dab194c9c65fd916",
    "PROBE_SYSTEM": "4debae82c849c293",
    "generic+A1 (system_prompt_for(None))": "0df6c4515627f219",
    "temporal-reasoning full": "50b515d857ce1788",
}


def test_golden_constant_hashes() -> None:
    """Byte-identity snapshot of every ported constant. The snapshot covers
    only constants that EXIST in the eval reader today (NO_EVIDENCE_TEXT is
    new product code — not part of the snapshot)."""
    fragments = {k: _sha16(v) for k, v in _TYPE_FRAGMENTS.items()}
    assert fragments["temporal-reasoning"] == _GOLDEN["_TEMPORAL_FRAGMENT"]
    assert fragments["single-session-preference"] == _GOLDEN["_PREFERENCE_FRAGMENT"]
    assert fragments["knowledge-update"] == _GOLDEN["_KNOWLEDGE_UPDATE_FRAGMENT"]
    assert fragments["multi-session"] == _GOLDEN["_MULTI_SESSION_FRAGMENT"]
    assert _sha16(_SYSTEM_PROMPT) == _GOLDEN["_SYSTEM_PROMPT"]
    assert _sha16(_ABSTRACTION_FRAGMENT) == _GOLDEN["_ABSTRACTION_FRAGMENT"]
    assert _sha16(PROBE_SYSTEM) == _GOLDEN["PROBE_SYSTEM"]
    assert _sha16(system_prompt_for(None)) == _GOLDEN["generic+A1 (system_prompt_for(None))"]
    assert _sha16(system_prompt_for("temporal-reasoning")) == \
        _GOLDEN["temporal-reasoning full"]


def test_generic_prompt_is_generic_plus_a1_only() -> None:
    """system_prompt_for(None) = generic + A1 — no type fragment."""
    assert system_prompt_for(None) == _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT
    assert system_prompt_for("unknown-type") == _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT


def test_type_prompt_appends_fragment() -> None:
    assert system_prompt_for("temporal-reasoning") == \
        _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT + _TYPE_FRAGMENTS["temporal-reasoning"]
    assert system_prompt_for("knowledge-update") == \
        _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT + _TYPE_FRAGMENTS["knowledge-update"]


def test_reader_prompt_constants_covers_a1() -> None:
    """The recorded dict carries the A1 clause keyed 'abstention'."""
    generic, fragments = reader_prompt_constants()
    assert generic == _SYSTEM_PROMPT
    assert fragments["abstention"] == _ABSTRACTION_FRAGMENT
    assert set(fragments) == {"abstention", *(_TYPE_FRAGMENTS.keys())}


def test_no_evidence_text_pinned() -> None:
    """NO_EVIDENCE_TEXT is NEW product code, pinned to the A1 abstention
    phrasing — the exact canonical no-evidence answer text."""
    assert NO_EVIDENCE_TEXT == (
        "The memory context does not contain the information needed to "
        "answer this question."
    )
    # it is the product's own constant — not part of the eval snapshot
    assert _sha16(NO_EVIDENCE_TEXT) != _GOLDEN["generic+A1 (system_prompt_for(None))"]


# ══ Task 1: re-export identity (the eval reader IS the product reader) ════

def test_eval_reexport_identity() -> None:
    """The eval re-export must re-export the PRODUCT objects (is-identity) —
    a parallel copy would fail this test."""
    import tools.longmem_eval.reader as eval_reader
    assert eval_reader.system_prompt_for is system_prompt_for
    assert eval_reader.LLMReader is LLMReader
    assert eval_reader.Reader is Reader
    assert eval_reader._SYSTEM_PROMPT is _SYSTEM_PROMPT
    assert eval_reader._ABSTRACTION_FRAGMENT is _ABSTRACTION_FRAGMENT
    assert eval_reader._TYPE_FRAGMENTS is _TYPE_FRAGMENTS
    assert eval_reader.DEFAULT_READER_MAX_TOKENS == DEFAULT_READER_MAX_TOKENS == 500
    assert eval_reader.PROBE_SYSTEM is PROBE_SYSTEM


def test_eval_preflight_uses_product_probe() -> None:
    import tools.longmem_eval.preflight as preflight
    assert preflight.PROBE_SYSTEM is PROBE_SYSTEM


class _StubModel:
    """Minimal complete(system, user) stub — records the call, returns canned."""

    def __init__(self, reply: str = "stub reply"):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


def test_llmreader_ping_with_stub() -> None:
    """LLMReader.ping works against a stub model with the module-level
    constant — no eval-relative import (the ping must not break after the
    move)."""
    model = _StubModel(reply="  ping ok  ")
    reader = LLMReader(model, model_id="stub")
    assert reader.ping("probe") == "ping ok"
    assert len(model.calls) == 1
    system, user = model.calls[0]
    assert system == PROBE_SYSTEM
    assert user == "probe"


def test_llmreader_answer_renders_product_context() -> None:
    """LLMReader.answer renders the context via tortoise.retrieval.render_context
    (Current Date header + session-date annotations) and makes ONE call."""
    from tortoise.retrieval import render_context
    model = _StubModel(reply="42 days")
    reader = LLMReader(model, model_id="stub")
    hits = [{"content": "we met on Monday", "session_date": "2026-07-01",
             "lme_session_index": 0}]
    out = reader.answer(context_hits=hits, question="how many days ago did we meet?",
                        question_date="2026-07-10", question_type="temporal-reasoning")
    assert out == "42 days"
    assert len(model.calls) == 1
    system, user = model.calls[0]
    assert system == system_prompt_for("temporal-reasoning")
    expected_ctx = render_context(hits, question_date="2026-07-10")
    assert f"Memory context:\n{expected_ctx}\n\nQuestion: how many days ago did we meet?\n\nAnswer:" == user


# ══ Task 2: detect_question_type ══════════════════════════════════════════

class TestDetectQuestionType:
    @pytest.mark.parametrize("q", [
        "how many days ago did we discuss the budget?",
        "3 weeks ago we decided on the office",
        "how many months ago was the gym schedule changed?",
        "between March and May, what changed?",
    ])
    def test_temporal_reasoning(self, q: str) -> None:
        assert detect_question_type(q) == "temporal-reasoning"

    @pytest.mark.parametrize("q", [
        "what is currently the gym schedule?",
        "what are the office hours these days?",
        "what was the gym schedule before March 2025?",
        "what was the gym schedule before 2025?",
        "what did we decide at the meeting on 2025-03-14?",
        "what was the policy back in 2024?",
    ])
    def test_knowledge_update(self, q: str) -> None:
        assert detect_question_type(q) == "knowledge-update"

    @pytest.mark.parametrize("q", [
        "how many times did we meet about pricing?",
        "which option do you prefer across sessions?",
        "did you ever discuss the rebranding?",
        "have you ever mentioned the new office?",
        "what changed over time in the schedule?",
    ])
    def test_multi_session(self, q: str) -> None:
        assert detect_question_type(q) == "multi-session"

    @pytest.mark.parametrize("q", [
        "which option do you prefer?",
        "what is your favorite color?",
        "do you prefer the red one or the blue one?",
        "which one would you choose?",
        "would you rather go to the park?",
        "which do you like better, tea or coffee?",
    ])
    def test_single_session_preference(self, q: str) -> None:
        assert detect_question_type(q) == "single-session-preference"

    @pytest.mark.parametrize("q", [
        "what did we decide about the API?",
        "where is the meeting?",
        "who is the new hire?",
        "",
        "   ",
        None,
    ])
    def test_generic_or_empty(self, q: str | None) -> None:
        assert detect_question_type(q) is None

    def test_precedence_tr_over_ku(self) -> None:
        # "how many days ago" is high-precision temporal — even though it
        # mentions a past tense + date-ish shape, TR wins.
        assert detect_question_type("how many days ago was the schedule changed?") \
            == "temporal-reasoning"

    def test_precedence_ku_over_ms(self) -> None:
        # "currently" is a KU current-value marker; the cross-session phrase
        # loses the precedence race.
        assert detect_question_type(
            "what is currently the schedule across sessions?") == "knowledge-update"

    def test_precedence_ms_over_ssp(self) -> None:
        assert detect_question_type(
            "how many times did you prefer option A?") == "multi-session"

    def test_month_only_date_falls_through(self) -> None:
        # The KU rule requires a 4-digit year — an optional month word
        # WITHOUT the year does not match (deliberate month-only boundary).
        assert detect_question_type("what was the gym schedule before March?") is None


# ══ Task 2: _looks_abstained ══════════════════════════════════════════════

class TestLooksAbstained:
    def test_abstained_phrases_each_positive(self) -> None:
        # every phrase in the list must label abstained
        for phrase in _ABSTAINED_PHRASES:
            assert _looks_abstained(f"I {phrase} about that.") is True, phrase

    def test_judge_markers_are_subset(self) -> None:
        """The product phrase list is a STRICT SUPERSET of the judge's
        abstention markers — the judge never flags an abstention the product
        label misses."""
        import tools.longmem_eval.judge as judge_mod
        for marker in judge_mod.MockJudge._ABSTRACTION_MARKERS:
            assert marker in _ABSTAINED_PHRASES, marker

    def test_confident_answer_false(self) -> None:
        assert _looks_abstained("The gym schedule is Monday and Wednesday.") is False
        assert _looks_abstained("3 days ago.") is False

    def test_blank_and_whitespace_true(self) -> None:
        assert _looks_abstained("") is True
        assert _looks_abstained("   \n\t  ") is True
        assert _looks_abstained(None) is True

    def test_empty_output_substitutes_no_evidence(self) -> None:
        """Blank output → abstained=True with the canonical no-evidence text
        (the deterministic substitution the SDK/hosted surfaces use)."""
        model = _StubModel(reply="")
        reader = LLMReader(model, model_id="stub")
        out = reader.answer(context_hits=[], question="q")
        assert out == ""
        assert _looks_abstained(out) is True
        # the canonical substitution is the upstream contract
        assert NO_EVIDENCE_TEXT

    def test_whitespace_output_abstained(self) -> None:
        model = _StubModel(reply="   \n  ")
        reader = LLMReader(model, model_id="stub")
        out = reader.answer(context_hits=[], question="q")
        assert _looks_abstained(out) is True

    def test_refusal_output_abstained(self) -> None:
        model = _StubModel(reply="I do not know the answer.")
        reader = LLMReader(model, model_id="stub")
        out = reader.answer(context_hits=[{"content": "noise"}], question="q")
        assert _looks_abstained(out) is True
