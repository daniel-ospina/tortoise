"""#2294 — the ask lane's D8 degradation gate must see the LEGACY ``outdated``
(flag-only) terminal state.

WHY THIS EXISTS
---------------
The ask lane reads with ``include_terminal=True`` so the reader sees the
supersession state story, and relies on ONE safety net to keep that honest:
``ask_lane._ask_d8_decoration_unavailable`` flips ``retrieval_degraded=True``
when a terminal hit arrives WITHOUT any supersession/window marker — i.e. the
D8 decoration silently failed and the evidence would render dead content as
current.

The gate tested ``status in TERMINAL_EXCLUDED_STATUSES``. That is a
hand-written subset of the terminal vocabulary, and it omits the legacy
``outdated=true`` flag — the exact #2901 anti-pattern (``tortoise/live.py``:
"Do NOT re-declare a status set at a call site … a hand-written subset is how
``outdated`` was omitted from three reader filters and a superseded/outdated
claim was then served as the CURRENT statement").

``invalidate_point`` (``sdk.py``) terminalises a Point by writing
``outdated=true`` and leaving ``status`` UNTOUCHED (``supersede_point`` is the
one that stamps ``status='superseded'``). So on the invalidate path the gate
read ``status='live'``, returned ``False``, and an invalidated claim whose
decoration failed rendered as CURRENT with ``retrieval_degraded=False``.

The fix has two halves, each pinned here:
  * the hit now carries the flag (``SearchResult.outdated`` → additive
    ``to_dict`` key, populated from ``fetch_point_epistemic_state``), and
  * the gate decides terminality with the SHARED ``live.is_terminal_status``.

Registers under ``core`` (``tortoise/search_engine.py``) and ``sdk``
(``tortoise/ask_lane.py``) — an ask-lane-only PR must still run it (see
``config/ci-surfaces.yml``).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.ask_lane import _ask_d8_decoration_unavailable


def _hit(**kw) -> dict:
    """A minimal hit dict (the gate reads only these keys)."""
    return {"id": "pt_x", "content": "c", **kw}


# ── the regression: flag-only terminal, no markers ──────────────────────────

def test_invalidated_flag_only_hit_without_markers_is_degraded():
    """THE #2294 regression: ``invalidate_point`` writes ``outdated=true`` and
    leaves ``status='live'`` — a marker-less hit of that shape MUST degrade."""
    assert _ask_d8_decoration_unavailable(
        [_hit(status="live", outdated=True)]) is True


def test_invalidated_flag_only_hit_absent_status_is_degraded():
    """A decoration failure can also drop ``status`` — the flag still carries
    the terminality."""
    assert _ask_d8_decoration_unavailable(
        [_hit(outdated=True)]) is True


def test_flagged_hit_with_counter_evidence_marker_is_not_degraded():
    """The flag PLUS a marker is the healthy path — the reader is told."""
    assert _ask_d8_decoration_unavailable(
        [_hit(status="live", outdated=True,
              superseded_by={"id": "pt_new", "content_snippet": "new"})]) is False


def test_flagged_hit_with_window_marker_is_not_degraded():
    assert _ask_d8_decoration_unavailable(
        [_hit(status="live", outdated=True, valid_to="2026-09-01")]) is False


# ── the pre-existing status-terminal cases must stay pinned ─────────────────

def test_superseded_status_without_markers_is_degraded():
    assert _ask_d8_decoration_unavailable([_hit(status="superseded")]) is True


def test_superseded_status_with_markers_is_not_degraded():
    assert _ask_d8_decoration_unavailable(
        [_hit(status="superseded",
              superseded_by={"id": "pt_new", "content_snippet": "new"})]) is False


def test_retracted_and_archived_statuses_are_degraded():
    for status in ("retracted", "archived", "outdated"):
        assert _ask_d8_decoration_unavailable([_hit(status=status)]) is True, status


def test_legacy_non_current_status_is_degraded():
    """``deprecated`` is in the exclusion set but NOT the write vocabulary — it
    is present in legacy graphs and must never be served as current (#2901)."""
    assert _ask_d8_decoration_unavailable([_hit(status="deprecated")]) is True


# ── live / empty pools stay clean (no false degradation) ────────────────────

def test_live_hit_is_not_degraded():
    assert _ask_d8_decoration_unavailable([_hit(status="live")]) is False


def test_unstatused_hit_is_not_degraded():
    assert _ask_d8_decoration_unavailable([_hit()]) is False


def test_non_terminal_hit_carrying_other_state_is_not_degraded():
    assert _ask_d8_decoration_unavailable(
        [_hit(status="live", has_answer=True, valid_from="2026-08-01")]) is False


def test_empty_pool_is_not_degraded():
    assert _ask_d8_decoration_unavailable([]) is False


def test_one_flagged_hit_among_live_hits_is_degraded():
    assert _ask_d8_decoration_unavailable(
        [_hit(status="live"), _hit(status="live", outdated=True),
         _hit(status="live")]) is True


# ── the wire half: the flag must reach consumers at all ─────────────────────

def test_search_result_to_dict_emits_outdated_only_when_true():
    """Additive D8 rule: present only when set, so live hits stay
    byte-identical on the wire."""
    from tortoise.search_engine import SearchResult
    live = SearchResult(id="p1", content="c", point_kind="statement")
    assert "outdated" not in live.to_dict()
    dead = SearchResult(id="p1", content="c", point_kind="statement",
                        outdated=True)
    assert dead.to_dict()["outdated"] is True


# ── integration: the flag actually rides a real invalidated hit ─────────────

def test_invalidate_point_surfaces_flag_on_terminal_inclusive_hits(
        sdk_factory, force_sparse_tfidf):
    """End-to-end: after ``invalidate_point`` the point is terminal by the
    legacy flag ONLY (status untouched), the default read excludes it, and a
    terminal-inclusive read carries ``outdated=True`` so the gate can fire."""
    sdk = sdk_factory()
    try:
        old = sdk.create_point("statement", "The office is in Chicago.")
        new = sdk.create_point("statement", "The office moved to the suburbs.")
        sdk.invalidate_point(old["id"], new["id"])

        # The write is flag-only: status is NOT stamped on this path.
        row = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$i}) RETURN n.status, coalesce(n.outdated,false)",
            params={"i": old["id"]}).result_set[0]
        assert row[0] == "live" and bool(row[1]) is True

        # Default read: excluded (the exclusion already honoured the flag).
        default_hits = sdk.tortoise_fts_query("Chicago office", limit=20)
        assert all(h.get("id") != old["id"] for h in default_hits)

        # Terminal-inclusive read: present, and the flag rides the hit.
        hits = sdk.tortoise_fts_query("Chicago office", limit=20,
                                      include_terminal=True)
        hit = next((h for h in hits if h.get("id") == old["id"]), None)
        assert hit is not None, "invalidated point must be visible when opted in"
        assert hit.get("outdated") is True
        assert hit.get("status") == "live"

        # Decorated (healthy): markers present → not degraded.
        assert _ask_d8_decoration_unavailable([hit]) is False
        # Decoration FAILED (markers stripped): now degraded, not silent.
        stripped = {k: v for k, v in hit.items()
                    if k not in ("superseded_by", "supersedes", "valid_from",
                                 "valid_to", "expired_at")}
        assert _ask_d8_decoration_unavailable([stripped]) is True
    finally:
        sdk.close()


def test_supersede_point_surfaces_status_and_flag(sdk_factory, force_sparse_tfidf):
    """The supersede path stamps ``status='superseded'`` AND the flag — both
    halves reach the hit, so the gate fired here even before the fix."""
    sdk = sdk_factory()
    try:
        old = sdk.create_point("statement", "My gym was downtown.")
        new = sdk.create_point("statement", "My gym is now uptown.")
        sdk.supersede_point(old["id"], new["id"])
        hits = sdk.tortoise_fts_query("downtown gym", limit=20,
                                      include_terminal=True)
        hit = next((h for h in hits if h.get("id") == old["id"]), None)
        assert hit is not None
        assert hit.get("status") == "superseded"
        assert hit.get("outdated") is True
        stripped = {k: v for k, v in hit.items()
                    if k not in ("superseded_by", "supersedes", "valid_from",
                                 "valid_to", "expired_at")}
        assert _ask_d8_decoration_unavailable([stripped]) is True
    finally:
        sdk.close()
