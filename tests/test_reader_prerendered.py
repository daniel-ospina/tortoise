"""Pre-rendered-evidence reader seam tests (#3011 Track D) — tortoise/reader.py.

The context-assembly experiment arms B/C produce ALREADY-RENDERED evidence
text (typed relation lines such as ``C1 IMPLIES C3`` / ``confidence: 0.82``)
rather than a list of hit dicts. Feeding that text through ``LLMReader.answer``
would route it through ``render_context`` → ``_render_block`` and decorate it
with ``[session ?]`` / ``[speaker]`` prefixes, mangling the frozen render
format. Track D adds a pre-rendered path that reuses the EXACT same system
prompt, user-message builder, model and call shape.

Contracts pinned here (all hermetic — no network, no DB, stub models only):

  1. Byte-identical equivalence — when
     ``render_context(hits, question_date=X) == evidence``, the pre-rendered
     path and the hit-list path send byte-identical (system, user) to the
     model and return byte-identical output.
  2. Identical system prompt between the two paths for the same
     ``question_type``.
  3. Empty evidence (arm D sends none) reproduces the existing empty-context
     behaviour exactly — including NOT substituting ``NO_EVIDENCE_TEXT``
     (that substitution belongs to the SDK/ask lane, not the reader).
  4. The pre-rendered path does NOT wrap text in ``[session ?]`` /
     ``[speaker]`` decoration (proved against the hit path, which does).
  5. The base ``Reader`` Protocol is unchanged — every existing structural
     implementation (the eval's ``MockReader`` included) still satisfies it;
     the pre-rendered method lives on the separate, optional
     ``EvidenceReader`` Protocol.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.reader import (
    NO_EVIDENCE_TEXT,
    EvidenceReader,
    LLMReader,
    Reader,
    build_reader_user_message,
    system_prompt_for,
)
from tortoise.retrieval import render_context


class _RecordingModel:
    """Fake ``complete(system, user)`` model that records every call.

    Returns a canned reply so the two paths' returned strings can also be
    compared. No network, no DB, no API key.
    """

    def __init__(self, reply: str = "  canned answer  ") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


# A hit list exercising every per-hit decoration ``_render_block`` adds:
# a session index + date, a speaker, and no-session unknown case. If these
# leak into the pre-rendered lane the equivalence tests fail.
_HITS = [
    {
        "content": "I moved to Lisbon in March 2025.",
        "session_date": "2025-03-04",
        "lme_session_index": 0,
        "speaker": "user",
    },
    {
        "content": "The user prefers window seats.",
        "session_date": "2025-04-01",
        "lme_session_index": 3,
        "speaker": "assistant",
    },
    {"content": "An undated, unsessioned fact."},
]

# Arm B/C-style pre-rendered text — note it is NOT a hit dict and carries no
# session/speaker metadata.
_ARM_BC_EVIDENCE = (
    "Current Date: 2026-07-10\n\n"
    "C1: Portugal residency started 2025-03.\n"
    "C1 IMPLIES C3\n"
    "confidence: 0.82\n"
    "C3: Tax residency filed.\n"
    "C2 CONTRADICTS C4\nC2 [SUPERSEDED BY C5]\n"
    "confidence: unmeasured"
)

_QUESTION_TYPES = (None, "temporal-reasoning", "single-session-preference",
                   "knowledge-update", "multi-session")


# ══ 1. Byte-identical equivalence ══════════════════════════════════════════

@pytest.mark.parametrize("question_type", _QUESTION_TYPES)
@pytest.mark.parametrize("question_date", [None, "2026-07-10"])
def test_answer_with_evidence_byte_identical_to_answer(question_type, question_date):
    """When ``render_context(hits, question_date=X) == evidence``, both paths
    send byte-identical (system, user) and return byte-identical output.

    The ``question_date`` pairing is chosen so the rendered context matches:
    with ``X=None`` the header is absent and the evidence is the bare block;
    with a date, the header is included. Either way ``evidence`` is exactly
    ``render_context(hits, question_date=X)``.
    """
    evidence = render_context(_HITS, question_date=question_date)
    model = _RecordingModel()
    reader = LLMReader(model, model_id="stub")

    out_answer = reader.answer(
        context_hits=_HITS, question="Where does the user live?",
        question_date=question_date, question_type=question_type)
    out_pre = reader.answer_with_evidence(
        evidence=evidence, question="Where does the user live?",
        question_date=question_date, question_type=question_type)

    # exactly two calls, one per path, and they are byte-identical.
    assert len(model.calls) == 2
    assert model.calls[0] == model.calls[1]
    assert out_answer == out_pre


def test_equivalence_is_sha256_exact():
    """The equivalence is byte-level, not merely semantic — sha256 of the
    rendered context must equal sha256 of the evidence string."""
    evidence = render_context(_HITS, question_date="2026-07-10")
    rendered = render_context(_HITS, question_date="2026-07-10")
    assert _sha(rendered) == _sha(evidence)

    model = _RecordingModel()
    reader = LLMReader(model, model_id="stub")
    reader.answer(context_hits=_HITS, question="q", question_date="2026-07-10")
    reader.answer_with_evidence(evidence=evidence, question="q",
                                question_date="2026-07-10")
    assert _sha(model.calls[0][1]) == _sha(model.calls[1][1])


def test_answer_with_evidence_ignores_question_date():
    """``question_date`` is accepted for signature symmetry and IGNORED —
    the evidence string is authoritative (re-adding a header would break
    byte-identity)."""
    model = _RecordingModel()
    reader = LLMReader(model, model_id="stub")
    reader.answer_with_evidence(evidence="E", question="q", question_date=None)
    reader.answer_with_evidence(evidence="E", question="q",
                                question_date="2099-01-01")
    assert model.calls[0] == model.calls[1]


# ══ 2. Identical system prompt ═════════════════════════════════════════════

@pytest.mark.parametrize("question_type", _QUESTION_TYPES)
def test_system_prompt_identical_between_paths(question_type):
    """Both paths send ``system_prompt_for(question_type)`` verbatim."""
    model = _RecordingModel()
    reader = LLMReader(model, model_id="stub")
    reader.answer(context_hits=_HITS, question="q", question_type=question_type)
    reader.answer_with_evidence(evidence=render_context(_HITS), question="q",
                                question_type=question_type)
    assert model.calls[0][0] == system_prompt_for(question_type)
    assert model.calls[1][0] == system_prompt_for(question_type)
    assert model.calls[0][0] == model.calls[1][0]


# ══ 3. Empty evidence == empty context ═════════════════════════════════════

def test_empty_evidence_matches_empty_context_exactly():
    """Arm D sends NO evidence. ``evidence=""`` must reproduce
    ``answer(context_hits=[])`` byte-for-byte — including the raw
    ``Memory context:\\n\\n\\nQuestion:`` shape and NO
    ``NO_EVIDENCE_TEXT`` substitution (that is the SDK lane's job).

    Note the actual empty-context render: with ``question_date=None``
    ``render_context([])`` is the empty string, so the pre-rendered call is
    the exact mirror. (With a date it renders a header-only string — see the
    companion test below.)
    """
    model = _RecordingModel()
    reader = LLMReader(model, model_id="stub")
    out_answer = reader.answer(context_hits=[], question="What is X?",
                               question_date=None,
                               question_type="knowledge-update")
    out_pre = reader.answer_with_evidence(evidence="", question="What is X?",
                                          question_date=None,
                                          question_type="knowledge-update")

    assert model.calls[0] == model.calls[1]
    assert model.calls[0][1] == build_reader_user_message("", "What is X?")
    assert out_answer == out_pre
    # The reader must NOT substitute the canonical abstention text.
    assert NO_EVIDENCE_TEXT not in model.calls[1][1]


def test_empty_context_header_only_render_is_also_reproducible():
    """The other empty-context render: a ``question_date`` on no hits yields
    a HEADER-ONLY string (not the empty string). The pre-rendered path
    mirrors ``answer(context_hits=[], question_date=D)`` exactly when handed
    that string — the byte-identity contract holds for both empty shapes."""
    header_only = render_context([], question_date="2026-07-10")
    assert header_only == "Current Date: 2026-07-10\n\n"

    model = _RecordingModel()
    reader = LLMReader(model, model_id="stub")
    reader.answer(context_hits=[], question="What is X?",
                  question_date="2026-07-10", question_type="multi-session")
    reader.answer_with_evidence(evidence=header_only, question="What is X?",
                                question_date="2026-07-10",
                                question_type="multi-session")
    assert model.calls[0] == model.calls[1]
    assert NO_EVIDENCE_TEXT not in model.calls[1][1]


# ══ 4. No ``[session ?]`` / ``[speaker]`` decoration ══════════════════════

def test_prerendered_text_is_not_decorated():
    """The pre-rendered lane passes the text through verbatim; the hit lane
    would decorate the same content. Proves the seam exists for a reason."""
    model = _RecordingModel()
    reader = LLMReader(model, model_id="stub")
    reader.answer_with_evidence(evidence=_ARM_BC_EVIDENCE, question="q",
                                question_type=None)
    _, user_pre = model.calls[0]
    assert _ARM_BC_EVIDENCE in user_pre
    assert "[session ?]" not in user_pre
    assert "[speaker]" not in user_pre
    # Exactly once, unwrapped: the message is the canonical template.
    assert user_pre == build_reader_user_message(_ARM_BC_EVIDENCE, "q")

    # Control: the SAME content as a hit dict IS decorated by the hit lane —
    # confirming the pre-rendered path is genuinely bypassing _render_block.
    control = _RecordingModel()
    LLMReader(control, model_id="stub").answer(
        context_hits=[{"content": _ARM_BC_EVIDENCE}], question="q")
    _, user_hit = control.calls[0]
    assert "[session ?]" in user_hit
    assert user_hit != user_pre


# ══ 5. Protocol compatibility ═════════════════════════════════════════════

def test_reader_protocol_is_unchanged():
    """The base ``Reader`` Protocol must NOT require the new method — that is
    what keeps existing implementations valid."""
    assert "answer_with_evidence" not in Reader.__protocol_attrs__
    assert "answer" in Reader.__protocol_attrs__
    assert "ping" in Reader.__protocol_attrs__


def test_evidencereader_extends_reader_with_the_new_method():
    """``EvidenceReader`` is the OPTIONAL superset: it adds exactly the
    pre-rendered method on top of the base surface."""
    assert EvidenceReader._is_protocol
    assert EvidenceReader.__protocol_attrs__ == (
        Reader.__protocol_attrs__ | {"answer_with_evidence"})
    assert hasattr(LLMReader, "answer_with_evidence")


def test_existing_mockreader_still_satisfies_reader():
    """The eval's ``MockReader`` (the existing structural implementation)
    satisfies the unchanged ``Reader`` and is NOT obliged to implement the
    pre-rendered method."""
    from tools.longmem_eval.reader import MockReader

    mock = MockReader()
    missing = {a for a in Reader.__protocol_attrs__ if not hasattr(mock, a)}
    assert missing == set()
    # And it genuinely lacks the optional method — which is fine, because
    # the base protocol no longer (never did) require it.
    assert not hasattr(mock, "answer_with_evidence")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
