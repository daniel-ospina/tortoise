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
    assert POINT_STATUS_VALUES == frozenset(  # noqa: SIM300
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
        with pytest.raises(ValueError, match="retract_point|supersede_point"):  # noqa: RUF043
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


# ── #690 parity tests: vocabulary ↔ code consistency ──────────────────

def test_code_writes_only_valid_statuses(sdk_factory, tmp_path):
    """Every status the code writes is in POINT_STATUS_VALUES."""
    from tortoise.sdk import POINT_STATUS_VALUES
    sdk = sdk_factory(tmp_path)

    # create_point: default draft
    p = sdk.create_point("statement", "parity-1")
    assert p["status"] in POINT_STATUS_VALUES

    # create_operator: promotes source to live
    p2 = sdk.create_point("statement", "parity-2")
    op = sdk.create_operator("IMPL", p2["id"], [p["id"]])  # noqa: F841
    assert sdk.get_point(p2["id"])["status"] == "live"
    assert "live" in POINT_STATUS_VALUES

    # retract_point: sets retracted
    r = sdk.retract_point(p["id"])
    assert r["status"] == "retracted"
    assert "retracted" in POINT_STATUS_VALUES

    # supersede_point: sets superseded
    old = sdk.create_point("statement", "old-parity")
    new = sdk.create_point("statement", "new-parity")
    sdk.supersede_point(old["id"], new["id"])
    assert sdk.get_point(old["id"])["status"] == "superseded"
    assert "superseded" in POINT_STATUS_VALUES

    # update_point: can promote draft→live (status='live' set)
    p3 = sdk.create_point("statement", "parity-3")
    sdk.update_point(p3["id"], status="live")
    assert sdk.get_point(p3["id"])["status"] == "live"


def test_all_documented_transitions_allowed(sdk_factory, tmp_path):
    """Every transition documented in ONTOLOGY §5 is allowed by _ALLOWED_TRANSITIONS."""
    from tortoise.sdk import _ALLOWED_TRANSITIONS

    # Canonical transitions from ONTOLOGY §5 table (#432 code-review decision:
    # a draft point can go terminal before ever going live):
    # draft → live, retracted, superseded
    # live → retracted, superseded
    # outdated → retracted
    # retracted → (none)
    # superseded → (none)
    # archived → (none)

    assert "live" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → live must be allowed"
    assert "retracted" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → retracted must be allowed (#432: draft can go terminal)"
    assert "superseded" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → superseded must be allowed (#432: draft can go terminal)"
    assert "retracted" in _ALLOWED_TRANSITIONS["live"], \
        "live → retracted must be allowed"
    assert "superseded" in _ALLOWED_TRANSITIONS["live"], \
        "live → superseded must be allowed"
    assert "retracted" in _ALLOWED_TRANSITIONS["outdated"], \
        "outdated → retracted must be allowed"
    assert _ALLOWED_TRANSITIONS["retracted"] == frozenset(), \
        "retracted is terminal — no outgoing transitions"
    assert _ALLOWED_TRANSITIONS["superseded"] == frozenset(), \
        "superseded is terminal — no outgoing transitions"
    assert _ALLOWED_TRANSITIONS["archived"] == frozenset(), \
        "archived is terminal — no outgoing transitions"


def test_every_status_in_values_has_transition_entry(sdk_factory, tmp_path):
    """Every status in POINT_STATUS_VALUES has an entry in _ALLOWED_TRANSITIONS."""
    from tortoise.sdk import POINT_STATUS_VALUES, _ALLOWED_TRANSITIONS  # noqa: I001

    for status in POINT_STATUS_VALUES:
        assert status in _ALLOWED_TRANSITIONS, \
            f"{status!r} in POINT_STATUS_VALUES but missing from _ALLOWED_TRANSITIONS"

    for status in _ALLOWED_TRANSITIONS:
        assert status in POINT_STATUS_VALUES, \
            f"{status!r} in _ALLOWED_TRANSITIONS but not in POINT_STATUS_VALUES"
