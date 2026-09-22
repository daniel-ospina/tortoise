"""E2E-9 (05-detailed-e2e.md) — point-in-time restore via the CORRECTS
chain-walk: the E6 (#1538) V3 gate.

Two sessions, gym 6pm (date D1) → gym 5pm (date D2), extracted + ingested
through the v2 pipeline (Tasks 0–2), then:
  (a) default KU retrieval answers/ranks the 5pm point (live preference,
      E2E-6 still passes — no ranking change, #1391 exclusion);
  (b) restore_point_at(live_id, at_date ∈ (D1, D2)) returns the 6pm point;
  (c) rendered context for the superseded hit carries
      `[valid D1 → D2; expired …]` (via include_terminal co-retrieval);
  (d) owned negatives: restore before D1 → honest found:false; ambiguous
      overlapping windows → explicit ambiguity signal.

Window-QUERY assertions (`WHERE validFrom <= $t AND (validTo IS NULL OR
validTo >= $t)` retrieval filter + restore-recall measurement) are
V4-CONDITIONAL (post-baseline follow-up run) — marked
``@pytest.mark.v4_conditional`` and skipped in V3 per the plan (⛔ G3).

Runnable with:
  uv run pytest tests/test_e2e9_point_in_time.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.ids import content_hash


def _pid(content: str) -> str:
    return f"pt_{content_hash(content)[:62]}"


def _embed_for(blob: str) -> dict:
    if "6pm" in blob and "5pm" not in blob:
        return {
            "entities": [{"name": "gym", "kind": "core:place",
                          "lifecycle": "created", "supersedes": None}],
            "events": [], "operators": [],
            "points": [
                {"content": "gym at 6pm", "pointKind": "statement",
                 "about_entities": ["gym"], "tier": "A", "when": "2026-06-10",
                 "quote": "I go to the gym at 6pm",
                 "search_keys": ["gym time", "workout"]},
            ],
        }
    if "moved" in blob:
        return {
            "entities": [{"name": "gym", "kind": "core:place",
                          "lifecycle": "created", "supersedes": None}],
            "events": [], "operators": [],
            "points": [
                {"content": "gym at 5pm", "pointKind": "statement",
                 "about_entities": ["gym"], "tier": "A", "when": "2026-06-14",
                 "quote": "I moved my gym session to 5pm",
                 "search_keys": ["gym time", "workout"]},
            ],
        }
    return {"entities": [], "events": [], "points": [], "operators": []}


def _story_for(transcript: str) -> str:
    if "6pm" in transcript and "5pm" not in transcript:
        return "User goes to the gym at 6pm."
    if "moved" in transcript:
        return "User moved the gym session to 5pm."
    return "User talks about the gym."


def _model():
    def respond(system: str, user: str) -> str:
        if "STORY SUMMARIZER" in system:
            return _story_for(user)
        if "GAP REVIEWER" in system:
            return json.dumps({"entities": [], "events": [], "points": [],
                               "operators": [], "retractions": [],
                               "chain_notes": [], "link_before_create": []})
        if "GRAPH MAPPER" in system:
            blob = user if "GRAPH MAPPER" in system else system
            return json.dumps(_embed_for(blob))
        raise AssertionError(f"unexpected system prompt: {system[:60]}")

    class _M:
        def complete(self, *, system: str, user: str) -> str:
            return respond(system, user)

    return _M()


def _fake_search(sdk, embed_list, story, **kw):
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point {pointKind:'statement'}) "
        "WHERE (p.status IS NULL OR NOT (p.status IN $terminal)) "
        "RETURN p.id, p.content LIMIT 25",
        params={"terminal": ["retracted", "superseded", "archived",
                             "outdated"]},
    ).result_set
    return {"mode": "real", "degraded": False, "reason": None,
            "entities": [], "events": [],
            "points": [{"id": r[0], "content": r[1], "kind": "statement"}
                       for r in rows],
            "queries_run": 1}


def _question() -> dict:
    return {
        "question_id": "q1",
        "haystack_session_ids": ["s0", "s1"],
        "haystack_dates": ["2026-06-10", "2026-06-14"],
        "haystack_sessions": [
            [{"role": "user", "content": "I go to the gym at 6pm",
              "has_answer": True},
             {"role": "assistant", "content": "ok", "has_answer": False}],
            [{"role": "user", "content": "I moved my gym session to 5pm",
              "has_answer": True}],
        ],
    }


@pytest.fixture
def graph(tmp_path, monkeypatch):
    """V2 pipeline graph: two-session gym fixture ingested end-to-end."""
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2
    from tortoise.sdk import TortoiseSDK

    monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
    sdk = TortoiseSDK(str(tmp_path / "e2e9.db"))
    try:
        stats = ingest_haystack_v2(sdk, _question(), model=_model())
        assert stats["supersessions_written"] == 1
        yield sdk
    finally:
        sdk.close()


def _scan(sdk, include_terminal=False):
    return sdk.tortoise_fts_query(query=None, kind="statement",
                                  entity_type="point", limit=20,
                                  include_terminal=include_terminal)


# ── (a) default retrieval prefers live (E2E-6 unchanged) ────────────

def test_default_retrieval_prefers_live(graph):
    """Default KU retrieval (no include_terminal) does NOT surface the
    superseded 6pm point; the 5pm point is the live answer."""
    sdk = graph
    results = _scan(sdk)
    ids = [r["id"] for r in results]
    assert _pid("gym at 6pm") not in ids, "superseded point leaked into default"
    assert _pid("gym at 5pm") in ids, "live point must be retrievable"
    live = next(r for r in results if r["id"] == _pid("gym at 5pm"))
    assert live["status"] in ("", "live", "draft")
    assert live["valid_from"] == "2026-06-14"


# ── (b) restore via chain walk (V3 mechanism) ───────────────────────

def test_restore_returns_superseded_prior(graph):
    """restore_point_at(live_id, at_date ∈ (D1, D2)) → the 6pm point."""
    sdk = graph
    out = sdk.restore_point_at(_pid("gym at 5pm"), "2026-06-12")
    assert out["found"] is True
    assert out["valid_point"]["id"] == _pid("gym at 6pm")
    assert out["valid_point"]["valid_from"] == "2026-06-10"
    assert out["valid_point"]["valid_to"] == "2026-06-14"
    assert out["current"]["id"] == _pid("gym at 5pm")
    assert [e["id"] for e in out["chain"]] == [
        _pid("gym at 5pm"), _pid("gym at 6pm")]


def test_restore_at_live_date_returns_live(graph):
    """at_date == the live window → the live point is the answer."""
    sdk = graph
    out = sdk.restore_point_at(_pid("gym at 5pm"), "2026-07-01")
    assert out["found"] is True
    assert out["valid_point"]["id"] == _pid("gym at 5pm")


# ── (c) rendered context carries the [valid …] marker ───────────────

def test_rendered_context_valid_marker(graph):
    """Superseded hit (via include_terminal) renders
    `[valid D1 → D2; expired …]` in the reader context (D7)."""
    from tools.longmem_eval.retrieve import _validity_marker

    sdk = graph
    results = _scan(sdk, include_terminal=True)
    old = next(r for r in results if r["id"] == _pid("gym at 6pm"))
    marker = _validity_marker(old)
    assert "[valid 2026-06-10 → 2026-06-14; expired " in marker
    assert marker.endswith("]")
    # live hit → [valid since …] (with its own SUPERSEDES marker)
    live = next(r for r in results if r["id"] == _pid("gym at 5pm"))
    assert "[valid since 2026-06-14]" in _validity_marker(live)


def test_undated_hit_renders_no_valid_marker():
    """Undated hits (no window props) render NO [valid …] marker — the
    byte-identical baseline (D7)."""
    from tools.longmem_eval.retrieve import _validity_marker
    assert _validity_marker({"id": "x", "content": "c"}) == ""
    assert _validity_marker({"id": "x", "valid_from": ""}) == ""


# ── (d) owned negatives ─────────────────────────────────────────────

def test_restore_before_earliest_window_honest_absence(graph):
    """Restore at a date before D1 → found:false + nearest window, never a
    fabricated answer."""
    sdk = graph
    out = sdk.restore_point_at(_pid("gym at 5pm"), "2020-01-01")
    assert out["found"] is False
    assert "valid_point" not in out
    assert out["nearest"]["id"] == _pid("gym at 6pm")


def test_restore_ambiguous_windows_explicit_signal(graph):
    """Overlapping/ambiguous windows → explicit ambiguity (never silent)."""
    sdk = graph
    old, new = _pid("gym at 6pm"), _pid("gym at 5pm")
    # hand-plant overlap: old's window also still covers a later date
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.validTo = '2026-06-20'",
        params={"id": old})
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.validFrom = '2026-06-01'",
        params={"id": new})
    out = sdk.restore_point_at(new, "2026-06-18")
    assert out.get("ambiguous") is True
    assert {c["id"] for c in out["candidates"]} == {old, new}


# ── V4-conditional: window-query retrieval (post-baseline follow-up) ─

@pytest.mark.v4_conditional
@pytest.mark.skipif(
    True,
    reason="V4-conditional (⛔ G3): window-indexed restore + recall "
           "measurement are asserted in the post-baseline follow-up run, "
           "not the V3 gate",
)
def test_v4_window_query_retrieval_filter(graph):
    """V4: Cypher interval filter (validFrom <= $t AND (validTo IS NULL OR
    validTo >= $t)) — the post-baseline window-indexed restore. Skipped in
    V3; the follow-up run measures restore recall vs the V3 baseline."""
    sdk = graph
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {pointKind:'statement'}) "
        "WHERE p.validFrom <= $t AND (p.validTo IS NULL OR p.validTo >= $t) "
        "RETURN p.id ORDER BY p.validFrom",
        params={"t": "2026-06-12"},
    ).result_set
    assert [r[0] for r in rows] == [_pid("gym at 6pm")]
