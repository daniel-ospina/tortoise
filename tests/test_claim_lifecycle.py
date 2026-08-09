"""Claim lifecycle tests — #432 Task 1 (state vocabulary + transition guards).

State vocabulary: draft → live → retracted → superseded (plus outdated/archived
reserved). challenged is DERIVED (NAND-operator edge on a live point), NOT a
state. update_point only promotes draft→live; retract/supersede are the
lifecycle-transition paths and are terminal.
"""
from __future__ import annotations

import pytest

from tortoise.sdk import POINT_STATUS_VALUES


def test_status_vocabulary():
    assert POINT_STATUS_VALUES == frozenset(
        {"draft", "live", "retracted", "superseded", "outdated", "archived"}
    )
    assert "challenged" not in POINT_STATUS_VALUES


def test_transition_guards_live_to_draft(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "guarded")
    assert p["status"] == "draft"
    sdk.update_point(p["id"], status="live")  # draft→live promote still allowed
    with pytest.raises(ValueError, match="live"):
        sdk.update_point(p["id"], status="draft")  # any non-promote status rejected


def test_update_point_rejects_status_changes_except_promote(sdk_factory, tmp_path):
    # plan-review P1: update_point is non-status except draft→live promote
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "status-guard")
    for bad in ("retracted", "superseded", "outdated", "archived"):
        with pytest.raises(ValueError, match="retract_point|supersede_point"):
            sdk.update_point(p["id"], status=bad)


def test_retract_tombstone_status(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "tombstone-me")
    r = sdk.retract_point(p["id"])
    assert r["status"] == "retracted"


def test_retracted_is_terminal(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "terminal")
    sdk.retract_point(p["id"])
    with pytest.raises(ValueError):
        sdk.retract_point(p["id"])  # already retracted
    with pytest.raises(ValueError):
        sdk.update_point(p["id"], status="live")  # terminal → no promote


def test_retract_archived_is_terminal(sdk_factory, tmp_path):
    # plan-review P2 boundary: archived is terminal. v1 has no SDK path to
    # archived (reserved), so set it via the graph directly to exercise the guard.
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "archived")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='archived'", params={"id": p["id"]})
    with pytest.raises(ValueError, match="already"):
        sdk.retract_point(p["id"])


def test_retract_point_missing_and_operator_guards(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    with pytest.raises(ValueError, match="No point"):
        sdk.retract_point("does-not-exist")
    s = sdk.create_point("statement", "src")
    t = sdk.create_point("statement", "tgt")
    op = sdk.create_operator("IMPL", s["id"], [t["id"]])
    with pytest.raises(ValueError, match="operator"):
        sdk.retract_point(op["id"])  # operators are not retractable


def test_supersede_sets_status_and_keeps_flag(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    old = sdk.create_point("statement", "old")
    new = sdk.create_point("statement", "new")
    sdk.supersede_point(old["id"], new["id"])
    got = sdk.get_point(old["id"])
    assert got["status"] == "superseded"
    assert got.get("outdated") is True


# ── Task 2: retraction-as-absence consumer audit ────────────────────────

def test_query_excludes_retracted_by_default(sdk_factory, tmp_path):
    # #432 Task 2: query() omits retracted points by default; the
    # include_retracted flag surfaces them (tombstone contract).
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "gone")
    sdk.retract_point(p["id"])
    assert p["id"] not in [q["id"] for q in sdk.query()]
    assert p["id"] in [q["id"] for q in sdk.query(include_retracted=True)]


def test_paginated_query_excludes_retracted_by_default(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    live = sdk.create_point("statement", "stay")
    gone = sdk.create_point("statement", "gone")
    sdk.retract_point(gone["id"])
    page = sdk.paginated_query(kind="statement")
    assert [r["id"] for r in page["results"]] == [live["id"]]
    assert page["total"] == 1
    page_all = sdk.paginated_query(kind="statement", include_retracted=True)
    assert {r["id"] for r in page_all["results"]} == {live["id"], gone["id"]}
    assert page_all["total"] == 2


def test_query_explicit_retracted_status_filter(sdk_factory, tmp_path):
    # tombstone contract: an explicit status='retracted' filter is the
    # queryable-with-filter path (no include_retracted needed).
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "gone")
    sdk.retract_point(p["id"])
    hits = sdk.query(status="retracted")
    assert [q["id"] for q in hits] == [p["id"]]
    assert p["id"] not in [q["id"] for q in sdk.query(status="live")]


def test_retracted_point_retrievable_by_id(sdk_factory, tmp_path):
    # tombstone contract: get_point on a retracted point returns it with
    # status='retracted' (not {} / 404).
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "gone")
    sdk.retract_point(p["id"])
    got = sdk.get_point(p["id"])
    assert got["id"] == p["id"]
    assert got["status"] == "retracted"
