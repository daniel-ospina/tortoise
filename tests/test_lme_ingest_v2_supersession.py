"""E6 (#1538) Task 0 probe + Task 2 — eval ingest supersession + validFrom.

T0: the v2 ingest applies supersession records via the canonical
    `supersede()` (E5 #1537) — a two-session fixture ("gym at 6pm" then
    "gym at 5pm") yields (new)-[:CORRECTS]->(old) with old.status='superseded'
    + old.outdated=true, both points CONTAINS-linked to their sessions.
    (If E5's machinery were absent this test would be the failing spec;
    per the plan, E6 Task 0 absorbs the gap — but E5 landed it, so this
    is the probe that documents the no-op.)
T2: points created via the v2 pipeline carry validFrom == haystack_dates[si].

Runnable with:
  uv run pytest tests/test_lme_ingest_v2_supersession.py -v
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
    # #2164: entity-level supersession scenario — session 2's embed emits
    # gym-plan-B with a ``supersedes`` ref to gym-plan-A (the extractor
    # resolves the ref against the S3 search results and records an
    # ENTITY supersession; the ingest write path must fold it into
    # Object.status). Check gym-plan-B FIRST: the session-2 blob carries
    # both plan tokens.
    if "gym-plan-B" in blob:
        return {
            "entities": [{"name": "gym-plan-B", "kind": "core:place",
                          "lifecycle": "created",
                          "supersedes": "gym-plan-A"}],
            "events": [], "operators": [], "points": [],
        }
    if "gym-plan-A" in blob:
        return {
            "entities": [{"name": "gym-plan-A", "kind": "core:place",
                          "lifecycle": "created", "supersedes": None}],
            "events": [], "operators": [], "points": [],
        }
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
    if "gym-plan-B" in transcript:
        return "User switched from gym-plan-A to gym-plan-B."
    if "gym-plan-A" in transcript:
        return "User follows gym-plan-A for strength training."
    if "6pm" in transcript and "5pm" not in transcript:
        return "User goes to the gym at 6pm."
    if "moved" in transcript:
        return "User moved the gym session to 5pm."
    return "User talks about the gym."


def _model():
    def respond(system: str, user: str) -> str:
        if "STORY SUMMARIZER" in system:
            return _story_for(user)
        if "ENTITY RESOLUTION" in system:
            # D3 phase-2 fallback: never guess an alias — the plan names are
            # distinct entities (gym-plan-A superseded, gym-plan-B new).
            return json.dumps({"resolutions": []})
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
    """Simulate a real backend: S3 returns live statement points + live
    Objects (terminal excluded — #1391 contract; eval Object rows ride the
    ``entities`` key as {id, name, kind} — the search_graph row shape the
    extractor's entity resolution + supersede-ref resolver consume)."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (p:Point {pointKind:'statement'}) "
        "WHERE (p.status IS NULL OR NOT (p.status IN $terminal)) "
        "RETURN p.id, p.content LIMIT 25",
        params={"terminal": ["retracted", "superseded", "archived",
                             "outdated"]},
    ).result_set
    orows = proj.g.query(
        "MATCH (o:Object) "
        "WHERE (o.status IS NULL OR NOT (o.status IN $terminal)) "
        "RETURN o.id, o.name, o.objectKind LIMIT 25",
        params={"terminal": ["retracted", "superseded", "archived"]},
    ).result_set
    return {"mode": "real", "degraded": False, "reason": None,
            "entities": [{"id": r[0], "name": r[1], "kind": r[2]}
                          for r in orows],
            "events": [],
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


def _question_entity_supersede() -> dict:
    """#2164: two-session fixture where session 0 creates Object gym-plan-A
    and session 1 supersedes it with gym-plan-B (an ENTITY-level
    supersession — the eval mirror of the pt_ chain fixture above). No
    points ride either session so no pt_ record can form: the ONLY
    supersession record is the entity one the extractor derives from the
    session-1 embed's ``supersedes`` ref + the S3-resolved gym-plan-A."""
    return {
        "question_id": "q2",
        "haystack_session_ids": ["s0", "s1"],
        "haystack_dates": ["2026-06-18", "2026-06-20"],
        "haystack_sessions": [
            [{"role": "user",
              "content": "I follow gym-plan-A for strength training",
              "has_answer": True},
             {"role": "assistant", "content": "ok", "has_answer": False}],
            [{"role": "user",
              "content": "I replaced gym-plan-A with gym-plan-B",
              "has_answer": True}],
        ],
    }


@pytest.fixture
def sdk_factory(tmp_path):
    from tortoise.sdk import TortoiseSDK

    made = []

    def _make():
        sdk = TortoiseSDK(str(tmp_path / f"sdk{len(made)}.db"))
        made.append(sdk)
        return sdk

    yield _make
    for sdk in made:
        sdk.close()


def test_ingest_applies_supersession_chain(sdk_factory, monkeypatch):
    """T0 probe: two-session ingest → CORRECTS edge + status flip + CONTAINS
    links (the E2E-9 chain substrate)."""
    monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
    sdk = sdk_factory()
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    stats = ingest_haystack_v2(sdk, _question(), model=_model())
    assert stats["supersessions"] == 1
    assert stats["supersessions_written"] == 1

    proj = sdk._get_proj()
    old = _pid("gym at 6pm")
    new = _pid("gym at 5pm")

    # CORRECTS edge + status flip
    rows = proj.g.query(
        "MATCH (n:Point {id:$new})-[:CORRECTS]->(o:Point {id:$old}) "
        "RETURN o.status, o.outdated",
        params={"new": new, "old": old},
    ).result_set
    assert rows, "CORRECTS edge missing"
    status, outdated = rows[0]
    assert status == "superseded"
    assert outdated is True

    # both points CONTAINS-linked to their sessions
    for pid, sid in ((old, "lme:q1:s0"), (new, "lme:q1:s1")):
        n = proj.g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point {id:$pid}) "
            "RETURN count(p)",
            params={"sid": sid, "pid": pid}).result_set[0][0]
        assert n == 1, f"{pid} must be CONTAINS-linked to {sid}"


def test_ingest_valid_from_from_haystack_dates(sdk_factory, monkeypatch):
    """T2: created points carry validFrom == haystack_dates[si]; undated
    sessions write no validFrom (open window)."""
    monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
    sdk = sdk_factory()
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    ingest_haystack_v2(sdk, _question(), model=_model())
    proj = sdk._get_proj()

    new = _pid("gym at 5pm")  # session 1, date 2026-06-14
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.validFrom, n.when",
        params={"id": new}).result_set
    assert rows
    vf, wh = rows[0]
    assert vf == "2026-06-14"
    assert wh == "2026-06-14"


def test_ingest_applies_entity_supersession(sdk_factory, monkeypatch):
    """#2164: eval ingest must fold ENTITY-level supersession records too.
    Pre-fix the inline supersession loop was pt_-only — an entity record
    (`superseded` = an Object id, no pt_ prefix) fell through the
    both-pt_ gate and was SILENTLY continue-dropped: gym-plan-A stayed
    live and no ObjectSuperseded fold happened. Now the ingest mirrors
    the shared apply_supersessions helper (capture parity): gym-plan-A
    must land status='superseded' supersededBy='gym-plan-B', gym-plan-B
    stays live, and the fold counts under the new additive
    ``objects_superseded`` stats key (never the pt_ record counter)."""
    monkeypatch.setattr("tortoise.extractor_v2.search_graph", _fake_search)
    sdk = sdk_factory()
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    stats = ingest_haystack_v2(sdk, _question_entity_supersede(),
                               model=_model())
    # the extractor derived ONE entity supersession record (session 1's
    # gym-plan-B supersedes gym-plan-A); no pt_ record exists here
    assert stats["supersessions"] == 1
    assert stats["supersessions_written"] == 0
    assert stats["objects_superseded"] == 1

    proj = sdk._get_proj()
    a = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy",
        params={"n": "gym-plan-A"}).result_set
    assert a, "gym-plan-A Object missing from the graph"
    status, sb = a[0]
    assert status == "superseded", f"gym-plan-A never folded: {a!r}"
    assert sb == "gym-plan-B", f"wrong successor: {a!r}"

    b = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN coalesce(o.status, '')",
        params={"n": "gym-plan-B"}).result_set
    assert b and b[0][0] == "live", \
        f"successor gym-plan-B must stay live: {b!r}"
