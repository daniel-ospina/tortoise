"""#4107 characterization pin — the advice-shaped preference abstention.

This module does **not** assert that the reader should answer; it pins the
*characterisation* recorded in ``docs/runbook/4107-preference-abstention.md``
so it cannot go stale silently. On the measured tree (~2026-09-23) the D3
instrument scores ``d6233ab6`` L2/L3/``ctx_recall`` GREEN and **L1 RED**: the
answer-bearing turn ``answer_b0fac439_t2`` is at fused rank 4 and rendered in
the reader's context (16 contiguous verbatim words of the gold answer), yet
the pinned reader abstains. The finding is that the universal
``_ABSTRACTION_FRAGMENT`` carries two rules in tension — a **synthesis
license** and an **asked-subject scoping guard** — and the model resolves the
tension against the license.

These pins exist because #4107's plan rests on three facts that are cheap to
break by refactor and expensive to notice:

  * the fixture witness (gold session + has_answer turn + shared span) still
    exists — a structure-preserving edit to the frozen fixture is what this
    pin catches (it asserts the witness's SHAPE; the SHA is asserted by
    ``tools.ask_shape_rate.FIXTURE_SHA256`` on the instrument path);
  * ``detect_question_type`` returns ``None`` here, so the emitted prompt is
    the generic one and ``_PREFERENCE_FRAGMENT`` never reaches the reader
    (the issue's "covered twice" premise was wrong on the measured path);
  * both halves of the tension are still present in the universal clause.

PRIOR ART — this is NOT the #2027 cause. ``tortoise/reader.py`` documents the
``#1366 → #1546 → #1762 → #1775 → #2027`` oscillation, and #2027 already added
a regression fixture for this exact ``d6233ab6`` shape
(``test_reader_abstention_calibration.py::test_preference_synthesis_commits_on_generic_baseline``,
fixture 2). That test pins the WIRING with a compliant-model fake — the fake
mechanically obeys the clause, so it cannot detect that the real pinned model
does not. #2027's cause was that, with no type fragment engaged, "the reader
treated 'no category matched' as 'abstain'" (``tortoise/reader.py:196``); this
one is distinct — the asked *event* is absent, so the clause's own
asked-subject scoping guard licenses the abstention. A candidate sentence
(filed with the battery under #4837) would amend the #1775/#2027 ordered
clause.

If a future change flips any of these, this test fails and the reader of it
is pointed at the runbook to re-characterise — it is a drift alarm, not a
product-behaviour assertion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.reader import (
    _ABSTRACTION_FRAGMENT,
    detect_question_type,
    system_prompt_for,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ask_spotcheck_composition.json"
QID = "d6233ab6"
GOLD_SESSION = "answer_b0fac439"
GOLD_TURN_INDEX = 2
GOLD_SPAN = (
    "high school experiences such as being part of the debate team "
    "and taking advanced placement courses"
)


def _question() -> dict:
    data = json.loads(FIXTURE.read_text())
    questions = data["questions"] if isinstance(data, dict) else data
    return next(q for q in questions if q["question_id"] == QID)


def test_fixture_witness_is_intact():
    """The gold session's answer-bearing turn exists and bears the span the
    characterization rests on. A structure-preserving edit to the witness is
    what this catches; the fixture SHA itself is asserted by the instrument
    path (``ask_shape_rate.FIXTURE_SHA256``)."""
    q = _question()
    ids = q["haystack_session_ids"]
    sessions = q["haystack_sessions"]
    assert GOLD_SESSION in ids, "gold session missing from the fixture"
    assert q["answer_session_ids"] == [GOLD_SESSION]
    gi = ids.index(GOLD_SESSION)
    gold_turn = sessions[gi][GOLD_TURN_INDEX]
    assert gold_turn["has_answer"] is True, (
        f"{GOLD_SESSION}_t{GOLD_TURN_INDEX} is no longer the has_answer turn"
    )
    content = " ".join((gold_turn.get("content") or "").split())
    assert GOLD_SPAN in content, "the gold turn's content drifted from the recorded shared span"
    assert GOLD_SPAN in " ".join(q["answer"].split()), (
        "the fixture gold answer no longer contains the recorded span"
    )


def test_detector_emits_the_generic_prompt_not_the_preference_fragment():
    """#4107 correction: the issue said the prompt covers this class twice,
    citing ``_PREFERENCE_FRAGMENT``. On the measured path the detector
    returns ``None``, so the universal clause is all that reaches the reader
    and the preference fragment is never emitted. Pinned so the runbook's
    claim is checkable rather than asserted."""
    q = _question()
    # the fixture's own label — which the instrument does NOT pass through
    assert q["question_type"] == "single-session-preference"
    assert detect_question_type(q["question"]) is None
    emitted = system_prompt_for(detect_question_type(q["question"]))
    # The premise of the §4 tension: the universal clause — synthesis licence
    # included — is what actually reaches the reader, not a category-specific
    # prompt. (An equality against ``system_prompt_for(None)`` would be
    # vacuous: the line above already pins the detector's ``None``.)
    assert "asks what the user prefers" in emitted
    assert "PREFERENCE INSTRUCTIONS" not in emitted


def test_the_universal_clause_still_carries_both_sides_of_the_tension():
    """The characterisation's root cause: one clause, two rules that collide
    on an advice-shaped question whose asked *event* is absent but whose
    relevant *experiences* are present. Both halves must be present for the
    finding to hold — drop either and the runbook must be re-derived."""
    clause = _ABSTRACTION_FRAGMENT
    low = clause.lower()
    # the synthesis license (the rule the model did NOT follow here)
    assert "asks what the user prefers" in low
    assert "do not abstain because the answer must be synthesized" in low
    # the asked-subject scoping guard (the rule the model DID follow)
    assert "commit only when the events or facts the question asks about" in low
    # Phase 2's literal condition, which the guard points at — and which the
    # reader's abstention text ("does not mention … a reunion") mirrors.
    assert "abstain only when no turn in the context mentions" in low
