"""#2520 (C6) — time-aware query expansion, hermetic rules (no DB, no model).

Every test answers the two Class-B questions:

  (1) **What value makes this test fail?** — named in each docstring.
  (2) **Does the fixture contain a row where that value is reachable?** —
      the inputs below deliberately carry the failing value's opposite
      (e.g. a superseded entry ranks FIRST, so a no-op reorder fails).

Nothing here touches a database, an embedder, or the network: these run on
every lane, including the URI-less one.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tortoise.time_aware import (
    DATE_PINNED,
    DEFAULT_TIME_AWARE_RECENCY_WEIGHT,
    PREFER_LATEST,
    QUERY_DATE_SUFFIX,
    TemporalIntent,
    detect_temporal_intent,
    inject_query_date,
    is_stale_entry,
    prefer_latest_order,
)


# ══════════════════════════════════════════════════════════════════════════
# detect_temporal_intent
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("query", [
    "where do I live now?",
    "Where does the user live currently?",
    "What type of coffee does Ava currently prefer?",
    "Is Ava still a member of that gym?",
    "What is the user's latest job title?",
    "Has the user changed their mind about the trip?",
    "Did I switch my phone plan?",
    "which city did the user end up choosing?",
])
def test_prefer_latest_intent_phrasings(query):
    """FAIL VALUE: ``kind != PREFER_LATEST`` (a detector that misses any of
    these KU/MSR phrasings). All eight inputs are reachable — each carries
    the cue verbatim."""
    assert detect_temporal_intent(query).kind == PREFER_LATEST


@pytest.mark.parametrize("query", [
    "where did I live in 2024?",
    "What was my address back in March?",
    "What did I say I used to prefer?",
    "What is the address I had 2 months ago?",
    "What happened between June 1 and June 15?",
    "What did I do last week?",
    "Where did the user live previously?",
])
def test_date_pinned_intent_phrasings(query):
    """FAIL VALUE: ``kind != DATE_PINNED``. A query pinning a past window
    MUST NOT receive the fresh bias (the invert-recency failure mode)."""
    assert detect_temporal_intent(query).kind == DATE_PINNED


def test_date_pinned_wins_over_prefer_latest():
    """FAIL VALUE: ``kind == PREFER_LATEST`` for a query that carries BOTH a
    year and a 'still' cue. The precedence rule is what makes the guard
    hold on mixed phrasing; the fixture contains both cues, so the failing
    value is reachable."""
    intent = detect_temporal_intent(
        "In 2024 did I still live in Madrid, and where do I live now?")
    assert intent.kind == DATE_PINNED


def test_no_temporal_signal_is_none():
    """FAIL VALUE: any non-None kind on a question with no temporal cue —
    the off/byte-identical path depends on this being None."""
    assert detect_temporal_intent(
        "What is Ava's favorite board game?").kind is None


@pytest.mark.parametrize("value", [None, "", "   ", "\n\t"])
def test_none_safe(value):
    """FAIL VALUE: an exception (full-scan mode passes ``query=None``) or a
    non-None kind. Reachable: ``tortoise_fts_query`` allows ``query=None``."""
    assert detect_temporal_intent(value).kind is None


def test_kind_vocabulary_is_disjoint_from_the_window_detector():
    """FAIL VALUE: ``prefer-latest``/``date-pinned`` colliding with the R5
    window detector's ``{interval, recency, ordering}``. The two detectors
    answer different questions (window vs freshness); a collision would let
    a future edit merge them silently. Reachable: both modules import."""
    from tools.longmem_eval.retrieve import detect_time_constraint
    # the window detector's kinds, exercised through its own public entry
    window_kinds = {
        detect_time_constraint("between 2025-06-01 and 2025-06-15").kind,
        detect_time_constraint("She told me 5 days ago.").kind,
        detect_time_constraint("Which happened first?").kind,
    }
    assert window_kinds <= {"interval", "recency", "ordering", None}
    assert window_kinds.isdisjoint({PREFER_LATEST, DATE_PINNED})


# ══════════════════════════════════════════════════════════════════════════
# inject_query_date
# ══════════════════════════════════════════════════════════════════════════

def test_inject_anchors_the_query():
    """FAIL VALUE: a no-op injection (the anchored string equals the bare
    query). Reachable: a valid ISO question date is supplied."""
    out = inject_query_date("where do I live now?", "2026-09-25")
    assert out != "where do I live now?"
    assert out == "where do I live now?" + QUERY_DATE_SUFFIX.format(
        date="2026-09-25")


def test_inject_differs_only_by_the_date():
    """FAIL VALUE: any other change to the query text (tokenization, case)
    between two dates. Reachable: the same query with two dates."""
    a = inject_query_date("where do I live now?", "2026-09-25")
    b = inject_query_date("where do I live now?", "2025-01-01")
    assert a != b
    assert a.replace("2026-09-25", "X") == b.replace("2025-01-01", "X")


def test_inject_accepts_full_timestamp_dates():
    """FAIL VALUE: a timestamp question_date leaking whole into the anchor,
    or the injection refusing it. Reachable: ``question_date`` may carry a
    full ISO timestamp from a caller override."""
    out = inject_query_date("where do I live?",
                            "2026-09-25T12:34:56+00:00")
    assert out.endswith(QUERY_DATE_SUFFIX.format(date="2026-09-25"))


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "25/09/2026"])
def test_inject_leaves_the_query_alone_without_a_valid_date(bad):
    """FAIL VALUE: inventing an anchor when the date is missing/unparseable.
    Reachable: every listed value is passed through."""
    assert inject_query_date("where do I live?", bad) == "where do I live?"


def test_inject_is_idempotent():
    """FAIL VALUE: a doubled anchor on a second pass. Reachable: the same
    call is made twice."""
    once = inject_query_date("where do I live?", "2026-09-25")
    assert inject_query_date(once, "2026-09-25") == once


def test_inject_empty_query_is_unchanged():
    """FAIL VALUE: returning ``" (as of …)"`` for an empty query."""
    assert inject_query_date("", "2026-09-25") == ""
    assert inject_query_date(None, "2026-09-25") is None


# ══════════════════════════════════════════════════════════════════════════
# is_stale_entry
# ══════════════════════════════════════════════════════════════════════════

def test_stale_on_superseded_by():
    """FAIL VALUE: ``False`` for an entry carrying a CORRECTS successor."""
    assert is_stale_entry({"superseded_by": {"id": "x"}})


def test_stale_on_terminal_status_alone():
    """FAIL VALUE: ``False`` for a status-only stale row — the clause that
    catches ``retract_point`` (status='retracted', NO window, NO CORRECTS).
    Reachable: the fixture carries only ``status``."""
    assert is_stale_entry({"status": "retracted"})
    assert is_stale_entry({"status": "superseded"})
    assert not is_stale_entry({"status": "live"})


def test_legacy_outdated_flag_alone_is_not_stale():
    """FAIL VALUE: ``True`` for an ``outdated``-flag-only entry. The eval's
    annotated surface does not carry the legacy boolean, and the window
    clause already catches every flag-invalidated point — so the intended
    behaviour is NOT stale on the flag alone. Reachable: the fixture carries
    only the flag, with a non-terminal status. Pinned so a future reader
    cannot "restore" a clause the design deliberately left out."""
    assert not is_stale_entry({"outdated": True, "status": "live"})


def test_falsy_numeric_window_is_still_a_window():
    """FAIL VALUE: ``False`` for a falsy epoch (``0``) — a truthiness test
    (``if not raw``) reads the 1970-01-01 epoch as "no window". Reachable:
    ``_as_date(0)`` -> ``"1970-01-01"``, which closed long before any
    question date."""
    assert is_stale_entry({"valid_to": 0}, question_date="2026-09-25")
    assert is_stale_entry({"expired_at": 0.0}, question_date="2026-09-25")


def test_stale_on_closed_window():
    """FAIL VALUE: ``False`` for a window that closed before the question
    date."""
    assert is_stale_entry({"valid_to": "2026-01-01T00:00:00+00:00"},
                          question_date="2026-09-25")
    assert is_stale_entry({"expired_at": "2026-01-01T00:00:00+00:00"},
                          question_date="2026-09-25")


def test_window_closed_on_the_question_date_is_stale():
    """FAIL VALUE: ``False`` at the equality boundary. A naive lexicographic
    compare of ``"2026-09-25T12:00:00+00:00"`` vs ``"2026-09-25"`` yields
    NOT stale; the day-normalized rule yields stale. Reachable: midday
    timestamp on the question date."""
    assert is_stale_entry({"valid_to": "2026-09-25T12:00:00+00:00"},
                          question_date="2026-09-25")


def test_future_window_is_not_stale():
    """FAIL VALUE: ``True`` for a window still open after the question date
    (would demote a currently-valid claim)."""
    assert not is_stale_entry({"valid_to": "2026-12-31T00:00:00+00:00"},
                              question_date="2026-09-25")


def test_numeric_epoch_windows_normalize():
    """FAIL VALUE: a misread numeric-epoch window. ``str(1740787200.0)[:10]``
    = ``"1740787200"`` compares as a string and would be misclassified;
    the epoch branch converts it. Reachable: ``supersede_point`` can carry a
    stored numeric ``validFrom`` into ``validTo``.
    1740787200 = 2025-03-01; 1900000000 = 2030-03-17."""
    assert is_stale_entry({"valid_to": 1740787200.0},
                          question_date="2026-09-25")
    assert not is_stale_entry({"valid_to": 1900000000.0},
                              question_date="2026-09-25")


def test_unparseable_window_is_not_stale():
    """FAIL VALUE: demoting on a value we cannot read (fail-closed is the
    conservative direction here)."""
    assert not is_stale_entry({"valid_to": "not-a-date"},
                              question_date="2026-09-25")
    assert not is_stale_entry({"valid_to": "someday"})


# ══════════════════════════════════════════════════════════════════════════
# dense_query_for (the SDK's factored anchor decision — hermetic)
# ══════════════════════════════════════════════════════════════════════════

def test_dense_query_for_prefer_latest_is_anchored():
    """FAIL VALUE: the bare query for an armed PREFER-LATEST question."""
    from tortoise.time_aware import dense_query_for
    assert dense_query_for("where do i live now", time_aware=True,
                           query_date="2026-09-25") == (
        "where do i live now (as of 2026-09-25)")


def test_dense_query_for_date_pinned_is_not_anchored():
    """FAIL VALUE: an anchor on a DATE-PINNED query (the invert-recency
    guard is enforced in the factored decision, not only in the caller)."""
    from tortoise.time_aware import dense_query_for
    assert dense_query_for("where did i live in 2024", time_aware=True,
                           query_date="2026-09-25") == (
        "where did i live in 2024")


def test_dense_query_for_off_path_and_edge_inputs_are_noops():
    """FAIL VALUE: any anchor on the OFF path, or an invented date for a
    missing/None/invalid ``query_date``. Reachable: the defaults."""
    from tortoise.time_aware import dense_query_for
    assert dense_query_for("where do i live now", time_aware=False,
                           query_date="2026-09-25") == "where do i live now"
    assert dense_query_for("where do i live now", time_aware=True,
                           query_date=None) == "where do i live now"
    assert dense_query_for("where do i live now", time_aware=True,
                           query_date="not-a-date") == "where do i live now"
    assert dense_query_for(None, time_aware=True,
                           query_date="2026-09-25") is None
    assert dense_query_for("", time_aware=True,
                           query_date="2026-09-25") == ""


def test_window_presence_fallback_without_question_date():
    """FAIL VALUE: ignoring a closed window when no question date is known."""
    assert is_stale_entry({"valid_to": "2026-01-01T00:00:00+00:00"})
    assert not is_stale_entry({})


def test_live_entry_is_not_stale():
    """FAIL VALUE: ``True`` for a plain live entry (would make the reorder a
    no-op or reorder noise)."""
    assert not is_stale_entry({"status": "draft", "superseded_by": None})


# ══════════════════════════════════════════════════════════════════════════
# prefer_latest_order
# ══════════════════════════════════════════════════════════════════════════

_STALE = {"id": "old", "superseded_by": {"id": "new"}, "status": "superseded"}
_LIVE = {"id": "new", "status": "draft", "superseded_by": None}


def test_reorder_puts_live_before_stale():
    """FAIL VALUE: a no-op reorder. Reachable: the stale row is FIRST in the
    input, so the live-first order differs from the input order."""
    out, stats = prefer_latest_order(
        [_STALE, _LIVE], intent=TemporalIntent(PREFER_LATEST),
        question_date="2026-09-25")
    assert [e["id"] for e in out] == ["new", "old"]
    assert stats["applied"] is True and stats["stale"] == 1
    assert out != [_STALE, _LIVE]


def test_reorder_is_stable_within_groups():
    """FAIL VALUE: any reorder that permutes live entries among themselves.
    Reachable: two live rows in a known order."""
    live_a = {"id": "a", "status": "draft"}
    live_b = {"id": "b", "status": "draft"}
    out, _ = prefer_latest_order(
        [live_a, _STALE, live_b], intent=TemporalIntent(PREFER_LATEST))
    assert [e["id"] for e in out] == ["a", "b", "old"]


def test_reorder_membership_is_preserved():
    """FAIL VALUE: a filter disguised as a reorder (dropped stale rows).
    Never-starve is structural — the set must be identical."""
    before = [_STALE, _LIVE]
    out, _ = prefer_latest_order(before, intent=TemporalIntent(PREFER_LATEST))
    assert sorted(e["id"] for e in out) == sorted(e["id"] for e in before)
    assert len(out) == len(before)


def test_date_pinned_intent_does_not_reorder():
    """FAIL VALUE: a fresh-biased reorder under a date-pinned intent — the
    invert-recency guard. Reachable: the stale row is first, so an applied
    reorder would visibly change the order."""
    out, stats = prefer_latest_order(
        [_STALE, _LIVE], intent=TemporalIntent(DATE_PINNED),
        question_date="2026-09-25")
    assert out == [_STALE, _LIVE]
    assert stats["applied"] is False
    assert stats["reason"] == "no-prefer-latest-intent"


def test_no_intent_does_not_reorder():
    """FAIL VALUE: reordering without a detected intent (byte-identical off
    path). Reachable: ``None`` and ``kind=None`` both passed."""
    assert prefer_latest_order([_STALE, _LIVE], intent=None)[0] == [_STALE, _LIVE]
    out, stats = prefer_latest_order(
        [_STALE, _LIVE], intent=TemporalIntent(None))
    assert out == [_STALE, _LIVE] and stats["applied"] is False


def test_all_stale_never_starves():
    """FAIL VALUE: returning an order with no live head, or mutating order
    when there is nothing to promote. Reachable: both rows are stale."""
    stale_a = {"id": "a", "status": "superseded"}
    stale_b = {"id": "b", "superseded_by": {"id": "c"}}
    out, stats = prefer_latest_order(
        [stale_a, stale_b], intent=TemporalIntent(PREFER_LATEST))
    assert out == [stale_a, stale_b]
    assert stats["applied"] is False
    assert stats["reason"] == "all-stale-never-starve"


def test_no_stale_entries_is_a_noop():
    """FAIL VALUE: reordering (or claiming applied) when nothing is stale."""
    a = {"id": "a", "status": "draft"}
    b = {"id": "b", "status": "live"}
    out, stats = prefer_latest_order(
        [a, b], intent=TemporalIntent(PREFER_LATEST))
    assert out == [a, b]
    assert stats["applied"] is False
    assert stats["reason"] == "no-stale-entries"


def test_already_latest_first_reports_not_applied():
    """FAIL VALUE: reporting ``applied=True`` when the order already holds
    (would misattribute the A/B effect to the reorder)."""
    out, stats = prefer_latest_order(
        [_LIVE, _STALE], intent=TemporalIntent(PREFER_LATEST))
    assert out == [_LIVE, _STALE]
    assert stats["applied"] is False
    assert stats["reason"] == "already-latest-first"


def test_default_weight_is_the_tr_weight():
    """FAIL VALUE: the non-TR recency weight silently diverging from the TR
    weight (0.5) without a recorded decision."""
    assert DEFAULT_TIME_AWARE_RECENCY_WEIGHT == 0.5


# ══════════════════════════════════════════════════════════════════════════
# #8487 — the read-path wiring: valid time as a SELECTION signal
# ══════════════════════════════════════════════════════════════════════════
#
# The measured defect (re-measured on the shipped read path 2026-10-18): a
# three-claim knowledge-update graph — an ENDED claim (`validTo` past,
# `status='live'`), its CURRENT successor and a NOT-YET-EFFECTIVE claim —
# came back in pure relevance order, the ended claim served as current. The
# rules below are the id-level adapter the SDK now calls on its
# pre-truncation fused candidate set.

from tortoise.env_truthy import env_flag_from_value  # noqa: E402
from tortoise.time_aware import (  # noqa: E402
    VALIDITY_RERANK_ENV,
    is_not_yet_effective,
    prefer_latest_enabled,
    prefer_latest_ids,
    resolve_question_date,
)

#: The three states the read path sees, keyed the way
#: `search_engine.fetch_point_epistemic_state` keys them.
_STATES = {
    "ended": {"status": "live", "valid_from": "2021-01-01",
              "valid_to": "2023-06-01"},
    "current": {"status": "live", "valid_from": "2023-06-02",
                "valid_to": None},
    "future": {"status": "live", "valid_from": "2099-01-01",
               "valid_to": None},
    "undated": {"status": "live"},
}

_PRESENT = "what database do we use now?"
_PINNED = "what database did we use in 2022?"


def test_ended_claim_is_demoted_below_the_current_one():
    """FAIL VALUE: the pre-#8487 outcome — the ended claim's position is
    whatever relevance gave it (here: rank 1, ahead of nothing but the
    future claim). Reachable: the fixture's ended claim is FIRST, so a no-op
    adapter returns it first."""
    out, stats = prefer_latest_ids(
        ["ended", "current"], _STATES, query=_PRESENT,
        question_date="2026-10-18")
    assert out == ["current", "ended"]
    assert stats["applied"] is True
    assert stats["reason"] == "latest-first"
    assert stats["ended"] == 1
    assert stats["demoted"] == ["ended"]


def test_not_yet_effective_claim_is_demoted_too():
    """FAIL VALUE: a 2099-dated claim served as true today. Reachable: the
    future claim is first in the input."""
    out, stats = prefer_latest_ids(
        ["future", "current"], _STATES, query=_PRESENT,
        question_date="2026-10-18")
    assert out == ["current", "future"]
    assert stats["not_yet_effective"] == 1
    assert stats["ended"] == 0


def test_undated_is_never_demoted():
    """FAIL VALUE: demoting a legacy/undated claim (an honest absence of a
    window read as ended). Reachable: `undated` carries no window at all."""
    out, stats = prefer_latest_ids(
        ["undated", "ended"], _STATES, query=_PRESENT,
        question_date="2026-10-18")
    assert out == ["undated", "ended"]
    assert stats["live"] == 1 and stats["stale"] == 1


def test_two_demotion_classes_keep_their_relative_order():
    """FAIL VALUE: inventing a rank between two non-current claims (neither
    is "more true now"). Reachable: both are demoted, in a known input
    order."""
    out, stats = prefer_latest_ids(
        ["ended", "future", "current"], _STATES, query=_PRESENT,
        question_date="2026-10-18")
    assert out == ["current", "ended", "future"]
    assert stats["demoted"] == ["ended", "future"]


def test_membership_is_preserved():
    """FAIL VALUE: a filter disguised as a reorder — the #8487 rule is
    ORDER, never selection. Reachable: a stale claim is last, so a filter
    would drop it."""
    ids = ["current", "future", "ended"]
    out, _ = prefer_latest_ids(ids, _STATES, query=_PRESENT,
                               question_date="2026-10-18")
    assert sorted(out) == sorted(ids) and len(out) == len(ids)


def test_date_pinned_query_is_not_reordered():
    """FAIL VALUE: applying the fresh bias to a historical question — the
    invert-recency guard. Reachable: the ended claim is first, so an applied
    reorder would visibly move it."""
    ids = ["ended", "current"]
    out, stats = prefer_latest_ids(ids, _STATES, query=_PINNED,
                                   question_date="2026-10-18")
    assert out == ids
    assert stats["applied"] is False
    assert stats["reason"] == "no-prefer-latest-intent"


def test_unintended_plain_query_is_byte_identical():
    """FAIL VALUE: reordering a query with no temporal intent. Reachable:
    a plain topical query over the SAME three-claim pool."""
    ids = ["ended", "current", "future"]
    out, stats = prefer_latest_ids(
        ids, _STATES, query="production database billing ledger",
        question_date="2026-10-18")
    assert out == ids and stats["applied"] is False


def test_all_demoted_never_starves():
    """FAIL VALUE: an order with no current head. Reachable: every id is
    non-current."""
    states = {"a": {"status": "outdated"}, "b": {"status": "superseded"}}
    out, stats = prefer_latest_ids(["a", "b"], states, query=_PRESENT,
                                   question_date="2026-10-18")
    assert out == ["a", "b"]
    assert stats["reason"] == "all-stale-never-starve"


def test_missing_state_is_not_a_demotion():
    """FAIL VALUE: an id absent from the state map treated as stale (a
    failed decoration would then demote the whole page)."""
    out, stats = prefer_latest_ids(["ghost", "current"], _STATES,
                                   query=_PRESENT,
                                   question_date="2026-10-18")
    assert out == ["ghost", "current"]
    assert stats["applied"] is False


def test_disabled_kill_switch_is_byte_identical():
    """FAIL VALUE: the kill switch not honoured. Reachable: the same input
    that DOES reorder when enabled."""
    ids = ["ended", "current"]
    out, stats = prefer_latest_ids(ids, _STATES, query=_PRESENT,
                                   question_date="2026-10-18", enabled=False)
    assert out == ids
    assert stats["reason"] == "disabled"


def test_future_window_is_not_yet_effective_but_not_stale():
    """The TWO predicates answer different questions (#8487). `is_stale_entry`
    keeps its #2520 answer for a future window (NOT stale — the eval's stale
    census depends on it); `is_not_yet_effective` is the read path's question.
    FAIL VALUE: either predicate collapsing into the other."""
    entry = {"status": "live", "valid_from": "2099-01-01"}
    assert is_stale_entry(entry, question_date="2026-10-18") is False
    assert is_not_yet_effective(entry, question_date="2026-10-18") is True
    # An open start is not evidence of anything.
    assert is_not_yet_effective({"status": "live"}, question_date="2026-10-18") is False
    # No instant to compare to → disabled, never a guess.
    assert is_not_yet_effective(entry, question_date=None) is False
    # A window that has started is current.
    assert is_not_yet_effective({"valid_from": "2020-01-01"},
                                question_date="2026-10-18") is False


@pytest.mark.parametrize("raw,expected", [
    (None, True), ("", True), ("1", True), ("true", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
    ("OFF", False), ("  off  ", False), ("garbage", True),
])
def test_kill_switch_resolution(raw, expected, monkeypatch):
    """FAIL VALUE: a typo or an explicit OFF flipping the default. Follows
    the ONE declared env contract (#4097): garbage → default (ON), explicit
    falsy → OFF.

    ENVIRONMENT-INDEPENDENT (#8487 review P2): the process variable is pinned
    to the OPPOSITE of the expected value first, so this case set must pass on
    a shell that exported ``TORTOISE_VALID_TIME_RERANK=0`` to try the OFF arm —
    which is exactly what the knob ships for. ``raw=None`` is NOT passed to
    ``prefer_latest_enabled``: ``None`` there means "read ``os.environ``", so
    that resolution is pinned by ``test_kill_switch_reads_the_environment``
    (monkeypatch) instead of being asserted against the developer's shell.
    """
    monkeypatch.setenv(VALIDITY_RERANK_ENV, "0" if expected else "1")
    assert env_flag_from_value(raw, True) is expected
    if raw is not None:
        assert prefer_latest_enabled(raw) is expected


def test_kill_switch_reads_the_environment(monkeypatch):
    """FAIL VALUE: the knob not being wired to the environment at all."""
    monkeypatch.setenv(VALIDITY_RERANK_ENV, "0")
    assert prefer_latest_enabled() is False
    monkeypatch.setenv(VALIDITY_RERANK_ENV, "1")
    assert prefer_latest_enabled() is True
    monkeypatch.delenv(VALIDITY_RERANK_ENV)
    assert prefer_latest_enabled() is True


def test_the_value_level_resolver_has_one_home():
    """FAIL VALUE: the #4097 resolution order duplicated in this rules module.

    The pre-fix shape carried a second copy here; the two copies were only ever
    exercised on DISJOINT inputs (the environment path vs an injected value), so
    a future change to ``env_truthy.env_flag``'s vocabulary or whitespace
    handling would move one and not the other with no test comparing them —
    #8487 review round 8 (P3), and the sixth divergent spelling #4097 exists to
    prevent."""
    import tortoise.time_aware as rules
    from tortoise.env_truthy import env_flag_from_value as contract_resolver

    assert not hasattr(rules, "env_flag_from_value")
    assert "env_flag_from_value" not in rules.__all__
    # The public entry point still resolves an injected value exactly as the
    # contract's own resolver does.
    assert prefer_latest_enabled("off") is contract_resolver("off", True) is False
    assert prefer_latest_enabled("on") is contract_resolver("on", False) is True
    assert prefer_latest_enabled("garbage") is contract_resolver("garbage", True) is True


def test_selection_date_uses_the_named_date_else_today():
    """FAIL VALUE: a named question date ignored (the eval passes it), or a
    scheduled end demoted because no instant was named. Reachable: both
    branches."""
    assert resolve_question_date("2024-01-02") == "2024-01-02"
    assert resolve_question_date("2024-01-02T10:00:00Z") == "2024-01-02"
    assert resolve_question_date(None, today="2026-10-18") == "2026-10-18"
    assert resolve_question_date("not-a-date", today="2026-10-18") == "2026-10-18"
    # No injection → the real UTC date. This IS a clock read, and it is the
    # rules module's own (declared in ``time_aware``'s module docstring as one
    # of its two exceptions to the purity claim) — which is exactly why the
    # SDK resolves the date once per read and threads it, and why every caller
    # that needs a fixed instant injects ``today=``.
    assert len(resolve_question_date()) == 10


def test_future_valid_to_is_not_demoted_with_a_named_date():
    """FAIL VALUE: a scheduled END (`validTo` in the future) demoted — the
    reason the selection passes an instant instead of relying on the
    presence-of-window fallback. Reachable: `is_stale_entry` demotes on
    presence alone when `question_date` is missing."""
    states = {"scheduled_end": {"status": "live",
                                "valid_to": "2099-01-01"}}
    out, _ = prefer_latest_ids(["scheduled_end", "current"], states,
                               query=_PRESENT, question_date="2026-10-18")
    assert out == ["scheduled_end", "current"]
    # …and the presence fallback WOULD have demoted it, which is why the
    # read path always resolves a date.
    assert is_stale_entry(states["scheduled_end"], question_date=None) is True


def test_env_example_declares_the_valid_time_kill_switch():
    """FAIL VALUE: the operator kill switch is advertised (PR body/plan doc)
    but discoverable nowhere — ``.env.example`` IS this repo's env contract
    (``TORTOISE_POOL_FLOOR``, the comparable read-path depth knob, is declared
    there). Mirrors ``tests/test_embedded_durability_claim.py``'s #2879 test
    for the previous comparable flag: *the flag must be visible to
    self-hosters*. Reachable: the pre-fix ``.env.example`` has zero
    occurrences of the name, so the grep finds nothing to assert against."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    env = (root / ".env.example").read_text()
    assert VALIDITY_RERANK_ENV in env, (
        f"{VALIDITY_RERANK_ENV} (default ON, #8487) is the operator switch "
        "that restores pre-#8487 relevance order but is not declared in "
        ".env.example"
    )
    declared = [line for line in env.splitlines()
                if line.strip().startswith(f"# {VALIDITY_RERANK_ENV}=")
                or line.strip().startswith(f"{VALIDITY_RERANK_ENV}=")]
    assert declared, (
        f"{VALIDITY_RERANK_ENV} must be shown as a commented assignment "
        "(the example form an operator copies), not just named in prose")
