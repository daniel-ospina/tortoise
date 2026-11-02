"""#10538 — the offline replay proxy must reproduce the SHIPPED temporal wiring.

``tools/longmem_eval/temporal_leg_replay.py`` is the reproducible offline proxy
the leg's measured budget table is quoted from
(``tortoise/temporal_leg.py``'s module docstring cites it as the source), and
its own comment claims it applies "the SHIPPED wiring (no re-implementation)".

Review cycle 5 (#10549) measured that it did NOT: it passed ``anchors=`` only,
so on the anchor-less ``interval``/``recency`` class this PR targets — where the
leg promotes on session clustering inside the question's resolved date range —
the proxy returned ``picks: []`` / ``moved: False`` while the shipped wiring
returned ``picks: ['b1']`` / ``moved: True``. An ON/OFF measurement taken
through an inert proxy attributes nothing to the new arm.

These tests pin the glue, not the mechanism: the proxy must hand the leg the
SAME bounds ``retrieve.py`` resolves (one arithmetic, one resolver), with the
question's own date as the recency clock — and the leg must then actually fire
on this question class.
"""

from __future__ import annotations

import pytest

from tools.longmem_eval import temporal_leg_replay as replay_mod
from tools.longmem_eval.retrieve import detect_time_constraint

#: The measured failure shape, in turns: ONE session owns the top of the
#: TF-IDF ranking far past the reader window (13 turns), a second session sits
#: just below it, and the GOLD turn belongs to a third session at the very
#: bottom — so the base ranking cannot reach it and only a spread can.
_INTERVAL_QUESTION = {
    "question": "What did I do between 2023-04-10 and 2023-04-20 for the puppy?",
    "question_date": "2023/04/21 (Fri) 10:00",
    "haystack_session_ids": ["sA", "sB", "sD"],
    "haystack_dates": ["2023-04-11", "2023-04-12", "2023-04-19"],
    "haystack_sessions": [
        [{"content": f"puppy training class session {i} leash recall practice",
          "has_answer": False} for i in range(13)],
        [{"content": "puppy training class kennel note", "has_answer": False}],
        [{"content": "the puppy graduated from obedience school",
          "has_answer": True},
         {"content": "puppy graduation ceremony photograph",
          "has_answer": False}],
    ],
}


def _question(question: str, question_date: str = "2023/04/21 (Fri) 10:00"):
    q = dict(_INTERVAL_QUESTION)
    q["question"] = question
    q["question_date"] = question_date
    return q


def test_the_proxy_fires_on_the_anchorless_interval_class():
    """The whole point of the arm: no comparison anchor, a date range instead.

    Pre-fix (no ``date_window`` threaded through) the proxy's leg was inert
    here — ``leg_fired == 0`` and the gold session was unreachable — while the
    shipped wiring promoted it. The recall/co-presence deltas below are the
    non-vacuity proof: they cannot move unless the proxy actually ran the
    windowed arm."""
    constraint = detect_time_constraint(_INTERVAL_QUESTION["question"])
    assert (constraint.kind, constraint.anchors) == ("interval", ()), (
        "premise: this question class has a date range and NO anchor")

    out = replay_mod.replay([_INTERVAL_QUESTION], window=12)
    assert out["questions"] == 1
    assert out["leg_fired"] == 1, (
        "the proxy ran an inert leg on the class it exists to measure")
    assert out["all_gold_sessions_base"] == 0
    assert out["all_gold_sessions_leg"] == 1, out
    assert out["evidence_turn_recall_leg"] > out["evidence_turn_recall_base"]


def test_the_proxy_threads_the_shipped_interval_bounds(monkeypatch):
    """Line-for-line with ``retrieve.py``: the bounds handed to the leg are the
    ones ``facet_window_bounds`` resolves from the question's own range."""
    seen: dict = {}
    real = replay_mod.temporal_leg_fusion_order

    def spy(candidates, **kwargs):
        seen.update(kwargs)
        return real(candidates, **kwargs)

    monkeypatch.setattr(replay_mod, "temporal_leg_fusion_order", spy)
    replay_mod.replay([_INTERVAL_QUESTION], window=12)
    assert seen.get("date_window") == ("2023-04-10", "2023-04-20"), seen


def test_the_proxy_uses_the_question_date_as_the_recency_clock(monkeypatch):
    """``retrieve.py`` passes ``anchor=question_date``: the question date IS
    the clock, so the census's "latest known session date" must not stand in
    for it (that would be a different question's clock). With the anchor, the
    window is ``[question_date − 7d, question_date]`` — pinned literally."""
    seen: dict = {}
    real = replay_mod.temporal_leg_fusion_order

    def spy(candidates, **kwargs):
        seen.update(kwargs)
        return real(candidates, **kwargs)

    monkeypatch.setattr(replay_mod, "temporal_leg_fusion_order", spy)
    q = _question("What did I do in the last 7 days for the puppy?")
    assert detect_time_constraint(q["question"]).kind == "recency"
    replay_mod.replay([q], window=12)
    assert seen.get("date_window") == ("2023-04-14", "2023-04-21"), seen


def test_the_proxy_resolves_the_month_name_year_from_the_question_date(monkeypatch):
    """Review cycle 11 (#10549): the mirror was only half taken.

    ``retrieve.py`` calls ``detect_time_constraint(question, default_year=year)``
    where ``year`` comes from ``question_date`` (``:1891-1896``), because
    ``detect_time_constraint``'s month-name interval branch resolves its year
    from ``default_year`` with a ``2026`` fallback. The proxy omitted the
    kwarg, so ``"between April 10 and April 20"`` on a 2023 question built
    ``interval(2026-04-10, 2026-04-20)`` here and ``interval(2023-04-10,
    2023-04-20)`` on the shipped path — the window arm was INERT on this class
    while the shipped arm promoted, so an ON/OFF reading through the proxy
    attributed nothing to it (the same defect cycle 5 fixed for the ISO
    class, one branch over).

    Both halves fail pre-fix: the spy sees ``2026-04-1x``, and the leg fires
    zero times even though a session owns the visible slots and the gold turn
    sits in-window — which is what makes this discriminate rather than
    re-assert the happy path."""
    seen: dict = {}
    real = replay_mod.temporal_leg_fusion_order

    def spy(candidates, **kwargs):
        seen.update(kwargs)
        return real(candidates, **kwargs)

    monkeypatch.setattr(replay_mod, "temporal_leg_fusion_order", spy)
    q = _question("What did I do between April 10 and April 20 for the puppy?")
    assert detect_time_constraint(q["question"]).kind == "interval", (
        "premise: the month-name interval class is what this pins")
    # The year is unresolvable WITHOUT the caller's ``default_year``: the
    # resolver's own fallback is 2026, a window that matches no 2023 session.
    out = replay_mod.replay([q], window=12)
    assert seen.get("date_window") == ("2023-04-10", "2023-04-20"), seen
    assert out["leg_fired"] == 1, out


@pytest.mark.parametrize("question", [
    "How many days ago did I buy a smoker?",
    "Which did I get first, the leash or the kennel?",
])
def test_the_proxy_leaves_anchorless_searches_without_a_range_unwindowed(
        question, monkeypatch):
    """The pre-#10538 contract survives: no anchor AND no resolvable range means
    NO window (``None``), so the leg stays inert rather than promoting
    arbitrary deep pool items — the measured −3.3 pt shape (#2976)."""
    seen: dict = {}
    real = replay_mod.temporal_leg_fusion_order

    def spy(candidates, **kwargs):
        seen.update(kwargs)
        return real(candidates, **kwargs)

    monkeypatch.setattr(replay_mod, "temporal_leg_fusion_order", spy)
    out = replay_mod.replay([_question(question)], window=12)
    assert seen.get("date_window") is None, seen
    assert out["leg_fired"] == 0, out


def test_the_fallback_pool_cannot_fire_the_window_arm():
    """Review cycle 6 (#10549): ``retrieve.py`` claimed the window "still
    matters for the ``tr_window_fallback`` pool". It cannot — the pool is
    unfiltered because the filter EMPTIED it, and both seams resolve their
    bounds from the same ``facet_window_bounds`` call with the same anchor, so
    no dated hit there is in-window and the leg's ``_in_date_window`` keeps
    nothing.

    Pinned as the composed claim, seam by seam: the filter returns ``[]`` on
    this pool (which is exactly what sets ``tr_window_fallback``), and the
    leg handed the SAME bounds then promotes nothing and reports
    ``no_window_candidates``. A change that gave the fallback pool the
    reachability the old comment claimed fails here."""
    from tools.longmem_eval.retrieve import _apply_time_window
    from tortoise.temporal_leg import (
        temporal_leg_empty_reason,
        temporal_leg_fusion_order,
    )

    question = "What did I do between 2023-04-10 and 2023-04-20 for the puppy?"
    constraint = detect_time_constraint(question)
    assert (constraint.kind, constraint.anchors) == ("interval", ())
    question_date = "2023/04/21 (Fri) 10:00"
    bounds = ("2023-04-10", "2023-04-20")
    # The FALLBACK shape: every dated hit sits outside the window, and one
    # session owns the visible slots (so a window that COULD select would fire
    # — clustering is not what declines here). The pool spans past the
    # promotion ceiling on purpose: at ``window=12, limit=2`` the slice is
    # ``candidates[10:]``, and a pool that ENDS at index 10 would leave it
    # empty and make this pin vacuous (a bounds-ignoring mutant passes).
    hits = [
        {"id": f"a{i}", "session_id": "sA",
         "session_date": f"2022-01-0{i % 9 + 1}",
         "content": "puppy training"}
        for i in range(14)
    ] + [
        {"id": "b1", "session_id": "sB", "session_date": "2022-02-01",
         "content": "puppy training"},
    ]
    assert _apply_time_window(hits, constraint,
                              question_date=question_date) == [], (
        "premise: the filter empties the dated pool, which is what sets "
        "tr_window_fallback")

    order, picks = temporal_leg_fusion_order(
        hits, anchors=constraint.anchors, window=12, limit=2,
        date_window=bounds)
    assert picks == [], (
        f"the window arm fired on the unfiltered fallback pool: {picks}")
    assert order == [h["id"] for h in hits]
    assert temporal_leg_empty_reason(
        dated=len(hits), anchors=0, weight=1.0, budget=2,
        date_window=True) == "no_window_candidates"
