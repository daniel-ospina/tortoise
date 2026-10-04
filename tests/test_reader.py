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
    outputs → blank → abstained=True (the canonical NO_EVIDENCE_TEXT
    substitution happens at the ask-lane surface, not inside the reader).

Fully offline: stub models only, no API keys, no DB.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.reader import (  # noqa: E402, RUF100
    _ABSTAINED_PHRASES,
    _ABSTRACTION_FRAGMENT,
    _SYSTEM_PROMPT,
    _TYPE_FRAGMENTS,
    DEFAULT_READER_MAX_TOKENS,
    NO_EVIDENCE_TEXT,
    PROBE_SYSTEM,
    LLMReader,
    Reader,
    _looks_abstained,
    build_reader_user_message,
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
# #2027 (calibration fix): the golden hashes for the A1 clause (and the
# derived full-prompt hashes) were re-captured after the intentional
# generic-baseline Phase-1 presence-commit change — the snapshot pins
# against ACCIDENTAL drift, and an intentional, reviewed prompt change
# re-records it (see docs/runbook/1987-ask-abstention-check.md).
_GOLDEN = {
    "_SYSTEM_PROMPT": "0a4140a708890f69",
    "_ABSTRACTION_FRAGMENT": "092f425543110a80",
    "_TEMPORAL_FRAGMENT": "592395e8f607e749",
    "_PREFERENCE_FRAGMENT": "9a59cae923aedbd7",
    "_KNOWLEDGE_UPDATE_FRAGMENT": "71be4f3f3c08540a",
    "_MULTI_SESSION_FRAGMENT": "dab194c9c65fd916",
    "PROBE_SYSTEM": "4debae82c849c293",
    "generic+A1 (system_prompt_for(None))": "6cfd5d7635f90d1e",
    "temporal-reasoning full": "6b8b47ea2c357e2d",
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
    """Full-prompt assembly pin for EVERY fragment type — exact equality,
    so a dropped/reordered fragment fails loudly (the assembly ORDER is
    cross-pinned in test_longmem_reader_prompting.py
    test_type_fragments_append_after_universal_clause). Multi-session and
    the empty-string type ("" ≠ None — the ``_TYPE_FRAGMENTS.get(q, "")``
    path) were previously unpinned; both ship here."""
    for t in _TYPE_FRAGMENTS:
        assert system_prompt_for(t) == \
            _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT + _TYPE_FRAGMENTS[t], t
    assert system_prompt_for("knowledge-update") == \
        _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT + _TYPE_FRAGMENTS["knowledge-update"]
    # empty-string type falls to the generic baseline (same as None)
    assert system_prompt_for("") == _SYSTEM_PROMPT + _ABSTRACTION_FRAGMENT


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
    # NO_EVIDENCE_TEXT is deliberately NOT part of the byte-identity
    # snapshot (adding a snapshot entry for it would be a contract change,
    # not accidental drift) — policy documented here, not asserted


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


def test_llmreader_answer_guards_none_completion() -> None:
    """answer() must NOT crash when the model returns None (a provider
    response with empty content — refusal/empty generation observed live
    2026-09-02 on qwen via OpenRouter, question 830ce83f): the None-guard
    mirrors the ask lane's ``(raw or "").strip()`` so the eval's
    direct LLMReader path surfaces "" instead of AttributeError on
    None.strip()."""
    class _NoneModel:
        def __init__(self):
            self.calls = []

        def complete(self, *, system: str, user: str):
            self.calls.append((system, user))
            return None

    model = _NoneModel()
    reader = LLMReader(model, model_id="stub")
    out = reader.answer(context_hits=[{"content": "x", "has_answer": True}],
                        question="q")
    assert out == ""
    assert len(model.calls) == 1


def test_llmreader_ping_guards_none_completion() -> None:
    """ping() mirrors answer()'s None-guard (same crash class: the probe
    completion returning None must not AttributeError)."""
    class _NoneModel:
        def complete(self, *, system: str, user: str):
            return None

    reader = LLMReader(_NoneModel(), model_id="stub")
    assert reader.ping("probe") == ""


def test_llmreader_answer_strips_raw_completion() -> None:
    """answer() strips the raw completion (test-review re-review #2013): a
    padded non-blank reply through the product path — the strip regression
    class the ping test already covers."""
    model = _StubModel(reply="  42 days  \n")
    reader = LLMReader(model, model_id="stub")
    out = reader.answer(context_hits=[{"content": "x", "has_answer": True}],
                        question="q")
    assert out == "42 days"


def test_build_reader_user_message_direct() -> None:
    """Direct unit pin of the single-sourced user-message template — the
    eval-only ask lane (``ask_lane.run_ask_lane``) and ``LLMReader.answer``
    share ONE copy (no parallel
    template drift); the render path is cross-pinned above."""
    assert build_reader_user_message("[user] hi", "what changed?") == \
        "Memory context:\n[user] hi\n\nQuestion: what changed?\n\nAnswer:"


def test_llmreader_constructor_identity() -> None:
    """Carried identity contract: model_spec defaults to model_id; the
    provider/pinned passthrough round-trips (consumed by ask-lane metering
    and eval reporting — a dropped passthrough would silently de-identify
    runs)."""
    r = LLMReader(_StubModel(), model_id="bare")
    assert r.model_spec == "bare"
    assert r.provider is None
    assert r.pinned is None
    r2 = LLMReader(_StubModel(), model_id="bare", model_spec="deepseek:x",
                   provider="deepseek-direct", pinned=True)
    assert r2.model_spec == "deepseek:x"
    assert r2.provider == "deepseek-direct"
    assert r2.pinned is True
    # an explicit empty spec still falls back to model_id
    r3 = LLMReader(_StubModel(), model_id="bare", model_spec="")
    assert r3.model_spec == "bare"


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
        # test-review re-review #2013: the remaining KU alternation
        # branches — "at present" / "right now" (current-value markers)
        # and "during" (date-preposition) were unpinned
        "what are the gym hours at present?",
        "what is the schedule right now?",
        "what did we decide during 2024?",
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
        # dual-matching fixture: "how many days ago" (TR) + "before 2025"
        # (KU) BOTH match — TR must win (test-review re-review #2013: the
        # original fixture matched TR only, so a TR-after-KU reorder would
        # have passed silently).
        assert detect_question_type(
            "how many days ago was the schedule changed before 2025?") \
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
        # The KU rule requires a 4-digit year — a month name WITHOUT the
        # year does not match (deliberate month-only boundary).
        assert detect_question_type("what was the gym schedule before March?") is None

    # ── #2009: the case fold ───────────────────────────────────────────────

    @pytest.mark.parametrize("q,expected", [
        # Each fixture's fold-dependent cue is its FIRST word, so the
        # sentence-cased form exercises the fold rather than sidestepping it.
        # The SSP row's ONLY cue is its leading "Which option …": a bare
        # ``prefer`` later in the same sentence would still match after a
        # missing fold and the row would not test it.
        ("How many weeks ago did I meet my aunt?", "temporal-reasoning"),
        ("How many days did I spend camping?", "temporal-reasoning"),
        ("How often do I attend yoga?", "knowledge-update"),
        ("How many different doctors did I see?", "multi-session"),
        ("Currently, what is the schedule?", "knowledge-update"),
        ("Which option is better?", "single-session-preference"),
    ])
    def test_the_case_of_the_question_never_changes_the_type(
            self, q: str, expected: str) -> None:
        """#2009: detection is CASE-INSENSITIVE.

        Value that makes this test FAIL: removing the ``.lower()`` fold in
        ``detect_question_type``. The patterns are lowercase, so a rule
        anchored on a capitalised first word stops matching.

        Every fixture reaches that value: each one's cue is its FIRST word.
        Five rows then fail by returning ``None``; the ``spend`` row fails by
        returning ``multi-session`` instead (the lowercase ``spend`` later in
        the string still matches the MS cue), which is why each row asserts
        the exact expected type rather than merely non-None. The first assert
        is what catches a fold moved to a call site — it passes the
        sentence-cased input — while the lower/upper asserts additionally pin
        that the fold is unconditional on the input's case.
        """
        assert detect_question_type(q) == expected
        assert detect_question_type(q.lower()) == expected
        assert detect_question_type(q.upper()) == expected

    # ── #2009: cues added by the calibration, per class ────────────────────

    @pytest.mark.parametrize("q", [
        "I finished the book two weeks ago",
        "I attended a class a month ago",
        "How many days had passed between the two events?",
        "Which event happened first, the wedding or the party?",
        "What did I do last Saturday?",
        "Which mode of transport did I use most recently?",
    ])
    def test_temporal_cues_added_by_2009(self, q: str) -> None:
        """Cues #2009 added to TR.

        Value that makes it FAIL: each fixture's own cue, in the position it
        uses — a word number ("two") rather than a literal digit, an elapsed
        span between events, an ordering verb, or a relative day reference.
        Every fixture reaches it: each carries at least one such cue, and no
        fixture's cue is case-dependent. The first two rows rely SOLELY on
        the word-number rule, so removing it reds exactly those two. The
        remaining rows are not all multiply-covered: "How many days had
        passed between the two events?" is reached by three rules and
        "which event happened first, the wedding or the party?" by five, but
        "What did I do last Saturday?" and "most recently" rows are reached
        by one each — their rule is unpinned by a second, which is why they
        are asserted by type rather than left to deletion testing.
        """
        assert detect_question_type(q) == "temporal-reasoning"

    @pytest.mark.parametrize("q,expected", [
        ("How often do I attend yoga classes?", "knowledge-update"),
        ("What is my previous occupation?", "knowledge-update"),
        ("How many different doctors did I visit?", "multi-session"),
        ("What is the total number of items?", "multi-session"),
        ("How much did I spend in total?", "multi-session"),
    ])
    def test_cues_added_by_2009(self, q: str, expected: str) -> None:
        """Cues #2009 added to KU and MS.

        Value that makes it FAIL: the class the fixture's cue must reach.
        Three fixtures are MS and two KU, so a change that routed either
        group to the other class reds at least one row on the TYPE (not just
        on non-None). Every fixture reaches its cue: "how often", "my
        previous", then "how many", "total" and "how much". (The narrower
        `how many different` / `the total` / `in total` forms were dropped as
        syntactically subsumed by those broader cues — see `_MS_PATTERNS`.)
        """
        assert detect_question_type(q) == expected

    @pytest.mark.parametrize("q", [
        "I've been feeling nostalgic lately. Do you think it would be a good "
        "idea to attend my high school reunion?",
        "Can you suggest some accessories for my camera?",
        "Do you have any tips for keeping the kitchen clean?",
        "What should I serve for dinner this weekend?",
    ])
    def test_advice_shaped_questions_stay_generic(self, q: str) -> None:
        """Advice-shaped questions keep the generic baseline.

        Value that makes it FAIL: adding an advice-seeking cue to
        ``_SSP_PATTERNS``. The first three fixtures each carry one of the
        named cues ("do you think", "can you suggest", "any tips") and are
        reached by it — adding those three cues reds exactly those three.
        The fourth carries none of them: its cue is the ``what should I
        <verb>`` imperative form, refused for the same reason but expressed
        as a different pattern, so the three-cue mutation leaves it green and
        it is the type assertion (not the mutation) that pins it.

        The first fixture is the exact question ``d6233ab6`` whose
        characterization ``docs/runbook/4107-preference-abstention.md`` §3
        pins (``detect_question_type`` -> None, generic prompt emitted).
        """
        assert detect_question_type(q) is None

    @pytest.mark.parametrize("q", [
        # Two new baselines no cue added by #2009 may claim.
        "what is the office address?",
        "who leads the project?",
    ])
    def test_added_cues_leave_the_baseline_alone(self, q: str) -> None:
        """Generic questions stay generic under the #2009 cues.

        Value that makes it FAIL: any added cue broad enough to match these
        strings — a bare ``\bwho\b`` / ``\bwhat\b``, or a loose "the"/"is"
        form — which is the over-broad pattern this test exists to catch.
        Reachable: the value is exactly such a too-general cue matching the
        fixture; the two strings carry no cue word, so only an over-reach can
        claim them.
        """
        assert detect_question_type(q) is None

    def test_ordering_rules_need_first_not_before(self) -> None:
        """An ordinal is not a date: `before <year>` stays KU.

        Value that makes it FAIL: an unanchored ordinal-before-date branch
        (``\b(first|second|third)\b.{0,N}\bbefore\b``). Only the
        ``first version before 2025`` fixture reaches it — it is the one row
        carrying both an ordinal and ``before <year>``. The gym row carries
        no ordinal and pins instead that the KU year rule still claims a
        plain date question, with TR preceding KU.
        """
        assert detect_question_type(
            "what was the gym schedule before 2025?") == "knowledge-update"
        assert detect_question_type(
            "what was the first version before 2025?") == "knowledge-update"
        assert detect_question_type(
            "which event happened first?") == "temporal-reasoning"

    @pytest.mark.parametrize("q", [
        "what is my first name?",
        "who was my first manager?",
    ])
    def test_first_needs_an_ordering_verb(self, q: str) -> None:
        """An ATTRIBUTIVE "first" is a fact question, not an ordering one.

        Value that makes it FAIL: a bare wh-``first`` rule such as
        ``\\b(which|who|what)\\b.{0,50}\\bfirst\\b``. Both fixtures reach it —
        each contains a first-word wh-question with attributive "first".
        """
        assert detect_question_type(q) is None

    @pytest.mark.parametrize("q", [
        "which city did I visit first?",
        "which device did I purchase first?",
        "which book did I read first?",
        "who arrived first?",
    ])
    def test_ordering_first_without_an_alternative_is_still_ordering(
            self, q: str) -> None:
        """A sentence-final ordinal is ordering even with no verb listed.

        Value that makes it FAIL: a rule that requires an alternative
        (``or``) or a verb from a fixed list. Every fixture reaches it: each
        names no alternative and uses an unlisted verb, so only the
        sentence-final-ordinal form can claim them.
        """
        assert detect_question_type(q) == "temporal-reasoning"

    def test_ordinal_choice_is_first_only(self) -> None:
        """The `which … ordinal … or` rule is pinned to FIRST.

        Value that makes it FAIL: widening that rule to ``second|third``.
        The preference fixture is reachable — ``prefer`` is a LATER cue and
        TR wins the precedence race, so the widening flips it to
        temporal-reasoning.
        """
        assert detect_question_type(
            "which do you prefer, the second option or the third?") \
            == "single-session-preference"
        assert detect_question_type(
            "which book did I finish first, A or B?") == "temporal-reasoning"

    def test_participated_is_not_a_temporal_cue(self) -> None:
        """A bare verb must not outrank MS in the highest-precedence class.

        Value that makes it FAIL: a bare ``\\bparticipated?\\b`` in TR. The
        counting fixture is reachable — it contains that word, so TR claims
        it before MS sees the explicit ``how many times`` marker. The second
        assertion pins that the genuine ordering form is still covered (by
        the pre-existing ``which event`` rule, not by the bare verb).
        """
        assert detect_question_type(
            "how many times have I participated in the marathon?") \
            == "multi-session"
        assert detect_question_type("which event did I participate in?") \
            == "temporal-reasoning"

    def test_past_elapsed_state_does_not_steal_the_current_form(self) -> None:
        """"how long HAD I" (TR) and "how long HAVE I" (KU) are distinct.

        Value that makes it FAIL: a ``(had|have)`` alternation in the new TR
        elapsed-state rule. Reachable: the KU fixture contains ``have``, so
        TR claims it first and the row reds.
        """
        assert detect_question_type(
            "how long had I been a member when I attended the meetup?") \
            == "temporal-reasoning"
        assert detect_question_type(
            "how long have I been using my Fitbit?") == "knowledge-update"

    # #2027 test-review pins: regex branches previously unpinned — a
    # widening here silently changes which prompt ships on the product
    # path. EXACT per-branch types (test-review re-review #2013): a marker
    # leaking into the wrong category would ship the wrong reasoning
    # fragment — membership in a 3-type set cannot catch that.
    @pytest.mark.parametrize("q,expected", [
        ("how long months has it been since the gym changed?",
         "temporal-reasoning"),                 # TR "how long"+unit
        ("how long years ago did we start the office?",
         "temporal-reasoning"),
        ("throughout the year, how did the schedule change?",
         "multi-session"),                      # MS "throughout"
        ("which city do you like best?",
         "single-session-preference"),          # SSP "like best"
        ("which restaurant do you like most?",
         "single-session-preference"),          # SSP "like most"
    ])
    def test_untested_markers_positive(self, q: str, expected: str) -> None:
        assert detect_question_type(q) == expected, q

    def test_between_gap_over_60_chars_falls_through(self) -> None:
        # the TR between-range rule is bounded at 60 chars (.{1,60}) — a
        # wider gap must NOT match (accidental widening is the class to pin)
        long_gap = "between " + "words " * 20 + "and then what changed?"
        assert len(long_gap) > 60
        assert detect_question_type(long_gap) is None

    def test_how_long_without_unit_falls_through(self) -> None:
        # "how long" without a day/week/month/year unit is not temporal
        assert detect_question_type("how long was the meeting?") is None


# ══ Task 2: _looks_abstained ══════════════════════════════════════════════

class TestLooksAbstained:
    def test_abstained_phrases_each_positive(self) -> None:
        # every phrase in the list must label abstained
        for phrase in _ABSTAINED_PHRASES:
            assert _looks_abstained(f"I {phrase} about that.") is True, phrase

    def test_abstained_phrase_set_pinned(self) -> None:
        """The phrase list is the census authority (judge ⊆ product, plan
        P2-32) — pin the EXACT set so a deletion fails loudly (the loop
        above alone would silently shrink with the list)."""
        assert set(_ABSTAINED_PHRASES) == {
            "do not know", "don't know", "not know", "unanswerable",
            "incomplete", "cannot answer", "can't answer", "not enough",
            "does not contain", "doesn't contain", "not mention",
            "not mentioned", "no mention of", "asked information is absent",
            "information is absent", "no memory", "no information",
            "nothing related", "unable to answer", "not sure", "unsure",
            "don't have that information", "don't have information",
            "absent from the context",
        }

    def test_confident_answer_false(self) -> None:
        assert _looks_abstained("The gym schedule is Monday and Wednesday.") is False
        assert _looks_abstained("3 days ago.") is False

    def test_separator_only_output_not_abstained(self) -> None:
        """Separator-only output (".", "...", "!?", "—", ",") has no
        clause to match — NOT abstained (pre-cycle-2 behavior; the
        cycle-2 clause filter must not crash on an empty clause list —
        this label gates NO_EVIDENCE_TEXT substitution upstream, so an
        uncaught IndexError would escape ``ask_lane.run_ask_lane``'s
        documented Raises contract)."""
        assert _looks_abstained("...") is False
        assert _looks_abstained(".") is False
        assert _looks_abstained("!?") is False
        assert _looks_abstained("—") is False
        # "./" is NOT separator-only — it splits to a non-empty clause
        # list (["/"]), so it never exercises the empty-clause guard;
        # non-separator residue with no matching phrase, still False.
        assert _looks_abstained("./") is False
        assert _looks_abstained(",") is False

    def test_innocuous_phrase_usage_is_documented_limitation(self) -> None:
        """Best-effort limitation (pinned, not silent): a committed answer
        whose FIRST clause legitimately contains an abstention phrase as
        ordinary text ("not enough chairs", "does not contain the
        document") IS labeled abstained — the label is a clause-scoped
        substring heuristic, NEVER a gate (the two-phase prompt is
        authoritative; the ask lane substitutes NO_EVIDENCE_TEXT on this
        label). Since the clause-scoped
        tightening (P2), the FIRST-clause occurrence labels abstained; a
        TRAILING qualifier does not (pinned in
        ``test_trailing_qualifier_not_abstained`` below) — except a
        FINAL-clause flat refusal ("I don't know.") which labels
        abstained (cycle-2 pins in
        ``test_reader_abstention_calibration.py``). These pins make
        the accepted trade-off explicit."""
        assert _looks_abstained(
            "There were not enough chairs, so we moved the meeting.") is True
        assert _looks_abstained(
            "The drawer does not contain the document; it is in the safe.") is True

    def test_trailing_qualifier_not_abstained(self) -> None:
        """P2 clause-scoped tightening: a COMMITTED answer that carries a
        trailing confidence qualifier ("…though I do not know if it
        changed", "I'm not sure about the move date", "the context does
        not mention his age") is NOT labeled abstained — the abstention
        must be the answer's operative clause, not a trailing hedge (the
        #2027 hedge class)."""
        assert _looks_abstained(
            "The gym schedule is Monday, though I do not know if it changed.") is False
        assert _looks_abstained(
            "The move date is Monday, though I'm not sure about the move date.") is False
        assert _looks_abstained(
            "He was born in 1978, though the context does not mention his age.") is False
        assert _looks_abstained(
            "I bought the smoker 10 days ago, though I do not know the exact hour.") is False

    def test_case_insensitive_and_non_str(self) -> None:
        # the lowercasing contract (a refactor could drop it — this is the
        # function that gates NO_EVIDENCE_TEXT substitution upstream)
        assert _looks_abstained("I DO NOT KNOW the answer.") is True
        assert _looks_abstained("I Do Not Know the answer.") is True
        # non-str input goes through str(answer) — never crashes
        assert _looks_abstained(42) is False

    def test_judge_markers_are_subset(self) -> None:
        """The product phrase list is a STRICT SUPERSET of the judge's
        abstention markers — the judge never flags an abstention the product
        label misses."""
        import tools.longmem_eval.judge as judge_mod
        for marker in judge_mod.MockJudge._ABSTRACTION_MARKERS:
            assert marker in _ABSTAINED_PHRASES, marker

    def test_blank_and_whitespace_true(self) -> None:
        assert _looks_abstained("") is True
        assert _looks_abstained("   \n\t  ") is True
        assert _looks_abstained(None) is True

    def test_blank_output_labels_abstained(self) -> None:
        """Blank model output → the reader returns the raw blank and
        ``_looks_abstained`` labels it abstained (the deterministic case).
        The canonical ``NO_EVIDENCE_TEXT`` substitution happens at the
        ask-lane surface, not inside the reader —
        this test pins the reader-side label only."""
        model = _StubModel(reply="")
        reader = LLMReader(model, model_id="stub")
        out = reader.answer(context_hits=[], question="q")
        assert out == ""
        assert _looks_abstained(out) is True

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
