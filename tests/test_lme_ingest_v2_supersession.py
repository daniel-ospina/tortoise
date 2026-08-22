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
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.ids import content_hash  # noqa: E402


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
    """Simulate a real backend: S3 returns live statement points (terminal
    excluded — #1391 contract)."""
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
