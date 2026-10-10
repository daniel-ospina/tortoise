"""#7856 — the objective map cannot express goal state, and its TOP node is inert.

Two defects, both measured on the live objective map (tortoise#7856):

1. A goal's ACHIEVEMENT state had no vocabulary and no validator — it was a
   free string (`unmet`/`active`/`met`/`guideline`) written by a throwaway
   script, so "what is active?" could not be answered from the graph and an
   unknown word persisted verbatim.

2. The TOP objective (REVENUE) was a `goal` POINT created without an explicit
   status, so it defaulted to `draft`. A draft point that is only ever a TARGET
   is EP-inert (`create_operator` promotes only the SOURCE, #131), so the top of
   the cascade could never influence a verdict — yet `converged=True` was still
   reported. A goal that can never influence a verdict must not be creatable by
   default.

Runnable with:
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
        tests/test_7856_goal_state.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from tortoise.sdk import GOAL_STATE_VALUES, TortoiseSDK


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_7856_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


# ── Defect 1 — goal state is a closed, validated vocabulary ──────────────

def test_goal_state_vocabulary_is_the_intended_closed_set():
    """Pin the vocabulary so a drive-by edit cannot silently widen it. It is
    the #7871 owner-proposed set + `guideline` (the value the objective map
    already uses for the #6792-demoted CI 'target')."""
    assert {
        "met", "active", "blocked", "unmet", "superseded", "abandoned",
        "guideline",
    } == GOAL_STATE_VALUES


@pytest.mark.parametrize("state", sorted(GOAL_STATE_VALUES))
def test_goal_state_valid_value_accepted_and_persisted(sdk, state):
    goal = sdk.create_object("REVENUE", objectKind="goal", goalState=state)
    assert goal["goalState"] == state
    # Persisted (not just echoed on the create return).
    assert sdk.get_entity(goal["id"])["goalState"] == state


def test_goal_state_invalid_value_refused_on_create_object(sdk):
    with pytest.raises(ValueError, match="goalState"):
        sdk.create_object("REVENUE", objectKind="goal", goalState="green")


def test_goal_state_invalid_value_refused_on_update_entity(sdk):
    goal = sdk.create_object("REVENUE", objectKind="goal", goalState="active")
    with pytest.raises(ValueError, match="goalState"):
        sdk.update_entity(goal["id"], goalState="in-flight")
    # The refused write did not land.
    assert sdk.get_entity(goal["id"])["goalState"] == "active"


def test_goal_state_invalid_value_refused_on_create_point(sdk):
    """The legacy Point leg (where the objective map's goals actually live)
    validates through the same boundary."""
    with pytest.raises(ValueError, match="goalState"):
        sdk.create_point("goal", "GOAL - REVENUE", goalState="nope")


def test_goal_state_invalid_value_refused_on_update_point(sdk):
    p = sdk.create_point("goal", "GOAL - REVENUE")
    with pytest.raises(ValueError, match="goalState"):
        sdk.update_point(p["id"], goalState="halfway")
    assert sdk.get_point(p["id"]).get("goalState") is None


def test_goal_state_none_clears_without_error(sdk):
    goal = sdk.create_object("REVENUE", objectKind="goal", goalState="met")
    sdk.update_entity(goal["id"], goalState=None)
    assert sdk.get_entity(goal["id"]).get("goalState") is None


def test_goal_state_does_not_leak_into_status(sdk):
    """The whole point of the fix is the two-axis split: `goalState` is
    achievement, `status` is the draft/live lifetime. Setting one must not
    move the other."""
    goal = sdk.create_object("REVENUE", objectKind="goal", goalState="met")
    assert goal["status"] == "live"
    assert goal["status"] != "met"
    p = sdk.create_point("goal", "GOAL - REVENUE", status="draft",
                         goalState="unmet")
    assert p["status"] == "draft"
    assert p["goalState"] == "unmet"


# ── Defect 2 — the TOP node must not be born inert ───────────────────────

def test_goal_point_is_born_live_not_draft(sdk):
    """A goal created through the legacy Point leg defaulted to `draft` — and
    a draft that is only ever a TARGET never promotes. It must be born live."""
    p = sdk.create_point("goal", "GOAL - REVENUE")
    assert p["status"] == "live", (
        "a goal-kind Point must not be born draft — a target-only draft goal "
        "is EP-inert and silently reported as converged (#7856)"
    )


def test_goal_point_explicit_draft_is_still_honoured(sdk):
    """The extraction path posts `status='draft'` explicitly; an explicit
    status always wins, so extraction semantics are unchanged."""
    p = sdk.create_point("goal", "extracted goal", status="draft")
    assert p["status"] == "draft"


def test_objective_map_top_node_survives_being_only_a_target(sdk):
    """The measured failure: REVENUE sits at the TOP of the cascade and is
    only ever a TARGET. Build that shape and assert the top node is reachable
    (live), not silently inert."""
    top = sdk.create_point("goal", "GOAL - REVENUE")
    mid = sdk.create_point("goal", "GOAL - PILOTS")
    leaf = sdk.create_point("goal", "GOAL - PRIVATE-ALPHA")
    sdk.create_operator("IMPL", mid["id"], [top["id"]])    # top is TARGET
    sdk.create_operator("IMPL", leaf["id"], [mid["id"]])

    assert sdk.get_point(top["id"])["status"] == "live"
    assert sdk.get_point(mid["id"])["status"] == "live"
    assert sdk.get_point(leaf["id"])["status"] == "live"


def test_non_goal_points_still_born_draft(sdk):
    """The born-live default is scoped to `goal` — ordinary claims keep the
    #131 draft→live lifecycle."""
    p = sdk.create_point("statement", "an ordinary claim")
    assert p["status"] == "draft"


# ── Durability — the map is rebuilt from the journal ─────────────────────

def test_goal_state_survives_rebuild(tmp_path):
    """A graph-backed goal map is only useful if it survives `rebuild_all`.
    The Point leg needs `goalState` declared in `_POINT_DECLARED_PROPS` or the
    replay open-set passthrough drops it; the Object leg rides the journal."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        goal = sdk.create_object("REVENUE", objectKind="goal", goalState="met")
        pt = sdk.create_point("goal", "GOAL - REVENUE", goalState="active")
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert sdk.get_entity(goal["id"])["goalState"] == "met"
        assert sdk.get_point(pt["id"])["goalState"] == "active"
        assert sdk.get_point(pt["id"])["status"] == "live"
    finally:
        sdk.close()
