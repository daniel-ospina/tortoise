"""Claim lifecycle tests — #432 Task 1 (state vocabulary + transition guards).

State vocabulary: draft → live → retracted → superseded (plus outdated/archived
reserved). challenged is DERIVED (NAND-operator edge on a live point), NOT a
state. update_point only promotes draft→live; retract/supersede are the
lifecycle-transition paths and are terminal.
"""
from __future__ import annotations

import pytest

from tortoise.live import is_terminal_status
from tortoise.sdk import POINT_STATUS_VALUES


def test_status_vocabulary():
    # ⚠️ #11824 S0 NARROWED this deliberately, and the narrowing is the slice's
    # deliverable: every knowledge kind's `status` is the same closed FOUR words,
    # because the rule that separates the two endings is one rule — `superseded`
    # NAMES ITS SUCCESSOR, and if nothing replaced it the word is `deprecated`.
    # The previous six (`retracted`, `outdated`, `archived` extra) were the #432
    # set plus two words that were never lifecycle states: `outdated` (meaning
    # already carried by the pair, and ambiguous between them) and `archived` (a
    # collision of "this ended" with "do not show me this").
    #
    # ⛔ The READ vocabulary did NOT narrow (`live.TERMINAL_EXCLUDED_STATUSES`
    # still names every legacy word) — stored rows keep what they hold.
    assert POINT_STATUS_VALUES == frozenset(  # noqa: SIM300
        {"draft", "live", "deprecated", "superseded"}
    )
    assert "challenged" not in POINT_STATUS_VALUES
    # the retired words are refused at the door, not merely absent from the set
    for retired in ("outdated", "archived", "retracted"):
        assert retired not in POINT_STATUS_VALUES


def test_transition_guards_live_to_draft(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    # Draft is explicit since #1088 (a no-status create is LIVE) — the guard
    # under test is the draft→live promote, so start from a real draft.
    p = sdk.create_point("statement", "guarded", status="draft")
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

    # create_point: no-status is LIVE since #1088; an explicit draft is
    # honoured and never auto-promoted (see the p3 update below)
    p = sdk.create_point("statement", "parity-1")
    assert p["status"] in POINT_STATUS_VALUES

    # create_operator: promotes a NEVER-SET source to live. Since #1088
    # create_point always sets a status, so this pins the resulting value —
    # the never-set promotion path is only reachable from legacy graphs.
    p2 = sdk.create_point("statement", "parity-2")
    op = sdk.create_operator("IMPL", p2["id"], [p["id"]])  # noqa: F841
    assert sdk.get_point(p2["id"])["status"] == "live"
    assert "live" in POINT_STATUS_VALUES

    # retract_point: sets the LEGACY word `retracted`
    r = sdk.retract_point(p["id"])
    # ⚠️ #11824 S0 states a RESIDUAL here rather than work around it: the verb
    # still writes `retracted` through its own CAS, while the write DOOR's
    # vocabulary is the four words. Migrating the verb to `deprecated` is plan
    # slice S2 (`retract_point` targets `deprecated`), and it is not done in S0 —
    # so the assertion is inverted to pin the truth: the value the ENGINE produces
    # is a READ-side legacy word, not a word a caller may mint.
    assert r["status"] == "retracted"
    assert "retracted" not in POINT_STATUS_VALUES
    # …and it is terminal to every reader, which is why the residual is safe.
    assert is_terminal_status("retracted")

    # supersede_point: sets superseded
    old = sdk.create_point("statement", "old-parity")
    new = sdk.create_point("statement", "new-parity")
    sdk.supersede_point(old["id"], new["id"])
    assert sdk.get_point(old["id"])["status"] == "superseded"
    assert "superseded" in POINT_STATUS_VALUES

    # update_point: can promote draft→live (status='live' set)
    p3 = sdk.create_point("statement", "parity-3", status="draft")
    sdk.update_point(p3["id"], status="live")
    assert sdk.get_point(p3["id"])["status"] == "live"


def test_all_documented_transitions_allowed(sdk_factory, tmp_path):
    """Every transition the MODEL declares is allowed by _ALLOWED_TRANSITIONS."""
    from tortoise.sdk import _ALLOWED_TRANSITIONS

    # #11824 S0: the LIVE rows now offer `deprecated` — it is a declared word of
    # the four-word vocabulary, and before S0 it was absent from
    # POINT_STATUS_VALUES, so the vocabulary gate raised before this table was
    # ever read.
    #
    # draft → live, deprecated, superseded      (#432: a draft can end)
    # live  → deprecated, superseded
    #
    # ⛔ The legacy spellings keep TERMINAL rows rather than being deleted, and
    # that is a READ delta, not a write path: `retracted` is still written by
    # `retract_point`'s own CAS (S2 migrates it), `outdated`/`archived` are words
    # stored rows hold. A stored word with NO row would DEFER — i.e. silently
    # become transitionable — which is the opposite of terminal.
    #
    # ⚠️ ONTOLOGY §5 still documents `retracted`, because removing it from that
    # document needs the owner's exact-text approval and is NOT this slice's.

    assert "live" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → live must be allowed"
    assert "deprecated" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → deprecated must be allowed (#11824 S0)"
    assert "superseded" in _ALLOWED_TRANSITIONS["draft"], \
        "draft → superseded must be allowed (#432: draft can go terminal)"
    assert "deprecated" in _ALLOWED_TRANSITIONS["live"], \
        "live → deprecated must be allowed (#11824 S0)"
    assert "superseded" in _ALLOWED_TRANSITIONS["live"], \
        "live → superseded must be allowed"
    # …and NO live row still offers a retired spelling (the door refuses it, so
    # a transition entry would name a target no write can reach).
    for retired in ("retracted", "outdated", "archived"):
        assert retired not in _ALLOWED_TRANSITIONS["draft"], retired
        assert retired not in _ALLOWED_TRANSITIONS["live"], retired
    assert _ALLOWED_TRANSITIONS["outdated"] == frozenset(), \
        "outdated is terminal — no outgoing transitions (#2498)"
    assert _ALLOWED_TRANSITIONS["retracted"] == frozenset(), \
        "retracted is terminal — no outgoing transitions"
    assert _ALLOWED_TRANSITIONS["superseded"] == frozenset(), \
        "superseded is terminal — no outgoing transitions"
    assert _ALLOWED_TRANSITIONS["deprecated"] == frozenset(), \
        "deprecated is terminal — no outgoing transitions (#11824 S0)"
    assert _ALLOWED_TRANSITIONS["archived"] == frozenset(), \
        "archived is terminal — no outgoing transitions"


def test_every_status_in_values_has_transition_entry(sdk_factory, tmp_path):
    """Every status in POINT_STATUS_VALUES has an entry in _ALLOWED_TRANSITIONS."""
    from tortoise.sdk import POINT_STATUS_VALUES, _ALLOWED_TRANSITIONS  # noqa: I001

    for status in POINT_STATUS_VALUES:
        assert status in _ALLOWED_TRANSITIONS, \
            f"{status!r} in POINT_STATUS_VALUES but missing from _ALLOWED_TRANSITIONS"

    # ⚠️ The reverse direction is deliberately NOT an equality any more (#11824
    # S0): the table also carries the RETIRED spellings as terminal rows, because
    # a stored row may still hold one and a word with no row would DEFER (become
    # transitionable) instead of being terminal. So the pin is: every key is
    # either a writable word or a declared legacy word, and every legacy key is
    # terminal.
    legacy = {"retracted", "outdated", "archived"}
    for status in _ALLOWED_TRANSITIONS:
        assert status in POINT_STATUS_VALUES or status in legacy, \
            f"{status!r} in _ALLOWED_TRANSITIONS but is neither writable nor legacy"
        if status in legacy:
            assert _ALLOWED_TRANSITIONS[status] == frozenset(), \
                f"the legacy word {status!r} must have no outgoing transition"
