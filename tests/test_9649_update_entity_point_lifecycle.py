"""#9649 — ``update_entity``'s Point arms carry no lifecycle guard.

Measured at ``origin/main`` = ``f33f5e2c32`` (issue comment ``6839154493``).
Two probes, both through the documented tenant route
``tortoise_update_entity`` → ``TortoiseSDK.update_entity`` → ``_update_entity``:

    supersede_point(old, new)          -> status='superseded', validTo set, outdated=true
    update_entity(old, status='live')  -> ACCEPTED
       after: ['live', validTo unchanged, outdated=True]      # RESURRECTED, incoherent

    update_entity(terminal, validTo='2030-01-01T00:00:00Z') -> ACCEPTED
       after: validTo = '2030-01-01T00:00:00Z'                # window overwritten

``_assert_lifecycle_guard`` (#2498/#7819) is the shared terminality instrument;
every one of its call sites sat ~24 000 lines above these two doors, so neither
consulted it. The gap is a *known* one, not a decision: ``docs/ONTOLOGY.md`` §5
records it as *"tracked as #9649"* and the model it violates is §5's Point
Status Vocabulary table — ``retracted`` / ``superseded`` / ``outdated`` /
``archived`` each declare **no outgoing transition**.

What this suite pins:

* both probes are REFUSED, and the write leaves the row and the journal
  untouched (atomicity — the refusal must precede the status arm, which is this
  method's first write);
* the refusal carries AX rule 2's three parts (#10183): that it refused, which
  field, and the repair (mint a new Point and supersede — do not re-status a
  dead one);
* the refusal is SCOPED, not blanket: a content/metadata revision of a terminal
  claim stays legal, because a revision later than the terminalization is the
  newer same-id writer (#3305, the scope #7819 established for ``update_point``);
* a non-terminal Point keeps its declared transitions, and an undeclared one is
  refused by ``_ALLOWED_TRANSITIONS`` — the map ONTOLOGY §5 declares and
  ``tests/test_claim_lifecycle.py`` cross-checks, which had no production
  consumer before this change;
* the guard does not leak onto the other labels the generic door serves.

Runnable (docker lane):

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_t9649' \\
        uv run pytest tests/test_9649_update_entity_point_lifecycle.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from tortoise.sdk import (_ALLOWED_TRANSITIONS, _TEMPORAL_RESTAMP_PROPS,
                          TortoiseSDK)

TERMINAL = ["retracted", "superseded", "outdated", "archived"]


@pytest.fixture
def env(tmp_path):
    """``(sdk, events_dir)`` with the JSONL journal wired (the #3311 shape)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t9649.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _live(sdk, text="claim") -> str:
    return sdk.create_point("statement", text, dedup=False)["id"]


def _superseded(sdk, text="old claim") -> tuple[str, str]:
    old = _live(sdk, text)
    new = _live(sdk, f"successor of {text}")
    sdk.supersede_point(old, new)
    return old, new


def _force_status(sdk, pid: str, status, *, outdated: bool = False) -> None:
    """Persist a state ``create_point`` cannot mint (terminal / legacy-flag).

    The guard must handle any state the graph can HOLD, not only creatable
    ones — the same reasoning ``tests/test_lifecycle_guards.py::_point`` gives.
    """
    sets = ["n.status=$st"]
    if outdated:
        sets.append("n.outdated=true")
    sdk._get_proj().g.query(
        f"MATCH (n:Point {{id:$id}}) SET {', '.join(sets)}",
        params={"id": pid, "st": status})


def _row(sdk, pid: str) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, n.validTo, n.validFrom, "
        "n.outdated", params={"id": pid}).result_set
    status, valid_to, valid_from, outdated = rows[0]
    return {"status": status, "validTo": valid_to, "validFrom": valid_from,
            "outdated": bool(outdated)}


def _mutations(events, pid: str) -> list[dict]:
    """The ``EntityMutated`` records addressing ``pid``.

    ``id`` is TOP-LEVEL on the record (``_emit_event`` reserves it; only the
    state map is nested), which is why this reads the record and not a payload.
    """
    path = events / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("type") == "EntityMutated" and rec.get("id") == pid:
            out.append(rec)
    return out


# ══════════════════════════════════════════════════════════════════════
# Probe 1 — resurrection by status is refused
# ══════════════════════════════════════════════════════════════════════

def test_probe_1_a_superseded_point_cannot_be_resurrected(env):
    """The measured probe: ``supersede_point`` then ``status='live'``."""
    sdk, _ = env
    old, _new = _superseded(sdk)
    before = _row(sdk, old)
    assert before["status"] == "superseded", before

    with pytest.raises(ValueError) as exc:
        sdk.update_entity(old, status="live")

    text = str(exc.value)
    assert "terminal" in text and "superseded" in text, (
        f"the refusal must say WHAT failed — the dead status it found: {text!r}")
    assert _row(sdk, old) == before, (
        "a refused resurrection must not write the status (the status arm is "
        "this method's FIRST write, so the guard must run before it)")


@pytest.mark.parametrize("dead", TERMINAL)
def test_probe_1_every_terminal_status_resists_every_live_status(env, dead):
    """``retracted`` / ``superseded`` / ``outdated`` / ``archived`` are all
    one-way on this door (ONTOLOGY §5: each declares NO outgoing transition)."""
    sdk, _ = env
    pid = _live(sdk)
    _force_status(sdk, pid, dead)
    before = _row(sdk, pid)

    for target in ("live", "draft"):
        with pytest.raises(ValueError, match="terminal"):
            sdk.update_entity(pid, status=target)

    assert _row(sdk, pid) == before


def test_the_legacy_outdated_flag_alone_makes_the_point_dead(env):
    """``outdated=true`` with a NULL status is terminal on every read surface
    (#2498), so it must be terminal on this door too."""
    sdk, _ = env
    pid = _live(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=NULL, n.outdated=true",
        params={"id": pid})

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(pid, status="live")
    assert _row(sdk, pid)["status"] is None


def test_a_null_status_legacy_point_can_still_be_given_one(env):
    """A legacy point that never got a status is NOT terminal (no legacy flag).

    The declared-transition map has no ``None`` key, so it defers rather than
    guessing — this is the CLI backfill's own route to ``live``, and a guard that
    refused it would strand every pre-#1088 node.
    """
    sdk, _ = env
    pid = _live(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=NULL", params={"id": pid})

    sdk.update_entity(pid, status="live")
    assert _row(sdk, pid)["status"] == "live"


# ══════════════════════════════════════════════════════════════════════
# Probe 2 — the validity window of a terminal Point is refused
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("prop,value", [
    ("validTo", "2030-01-01T00:00:00Z"),
    ("validFrom", "2026-01-01T00:00:00Z"),
    ("validToPrecision", "day"),
    ("validFromPrecision", "day"),
    ("expiredAt", "2030-01-01T00:00:00Z"),
    ("when", "2030-01-01T00:00:00Z"),
])
def test_probe_2_a_terminal_points_temporal_record_is_refused(env, prop, value):
    """Every caller-temporal prop is refused on a dead claim — the
    ``_TEMPORAL_RESTAMP_PROPS`` scope #7819 established, on the second door."""
    assert prop in _TEMPORAL_RESTAMP_PROPS, (
        "this parametrization must cover the shared temporal set, not a copy")
    sdk, _ = env
    old, _new = _superseded(sdk)
    before = _row(sdk, old)

    with pytest.raises(ValueError) as exc:
        sdk.update_entity(old, **{prop: value})

    text = str(exc.value)
    assert prop in text, (
        f"the refusal must name the field it refused ({prop}): {text!r}")
    assert _row(sdk, old) == before, (
        f"a refused window write must not touch the record: {prop}")


def test_probe_2_a_retracted_point_refuses_the_window_too(env):
    sdk, _ = env
    pid = _live(sdk)
    sdk.retract_point(pid)
    before = _row(sdk, pid)

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(pid, validTo="2030-01-01T00:00:00Z")
    assert _row(sdk, pid) == before


# ══════════════════════════════════════════════════════════════════════
# AX rule 2 — the refusal carries all three parts (#10183)
# ══════════════════════════════════════════════════════════════════════

def test_the_refusal_carries_the_three_parts(env):
    """that it refused · exactly what failed · exactly what to do."""
    sdk, _ = env
    old, _new = _superseded(sdk)

    with pytest.raises(ValueError) as exc:
        sdk.update_entity(old, status="live")
    text = str(exc.value)

    # 1. that it refused
    assert "refus" in text.lower(), f"no refusal stated: {text!r}"
    # 2. exactly what failed — the dead state and the fields asked for
    assert "superseded" in text, f"the found state is missing: {text!r}"
    assert "status" in text, f"the refused field is missing: {text!r}"
    # 3. exactly what to do — the replacement route, named with its call
    assert "supersede_point" in text, (
        f"the repair must name supersede_point: {text!r}")
    assert "new Point" in text or "new point" in text, (
        f"the repair must say the claim is re-minted, not re-statused: {text!r}")


# ══════════════════════════════════════════════════════════════════════
# No over-correction — the scope #7819 established is preserved
# ══════════════════════════════════════════════════════════════════════

def test_a_content_revision_of_a_terminal_point_stays_legal(env):
    """#3305: a revision later than the terminalization is the newer same-id
    writer. #7819 kept that legal on ``update_point``; this door must not
    become a blanket terminal refusal either."""
    sdk, _ = env
    old, _new = _superseded(sdk)

    sdk.update_entity(old, content="a corrected reading of the dead claim")
    assert sdk.get_point(old)["content"] == \
        "a corrected reading of the dead claim"
    assert _row(sdk, old)["status"] == "superseded", (
        "the revision must not disturb the terminal status")


def test_a_live_point_keeps_its_window_and_its_nulls(env):
    """No over-refusal on the healthy population: a live claim's window is
    still writable, including a NULL end (the documented clear)."""
    sdk, _ = env
    pid = _live(sdk)
    sdk.update_entity(pid, validFrom="2026-01-01T00:00:00Z",
                      validFromPrecision="day")
    assert _row(sdk, pid)["validFrom"] == "2026-01-01T00:00:00Z"
    sdk.update_entity(pid, validTo=None)
    assert _row(sdk, pid)["validTo"] is None


def test_a_draft_point_still_promotes_through_this_door(env):
    """``draft → live`` is a DECLARED transition (ONTOLOGY §5) and the ruling
    that ``draft`` is an explicit, honoured mode depends on it working."""
    sdk, _ = env
    pid = sdk.create_point("statement", "d", dedup=False, status="draft")["id"]
    sdk.update_entity(pid, status="live")
    assert _row(sdk, pid)["status"] == "live"


# ══════════════════════════════════════════════════════════════════════
# The declared map is consulted for the non-terminal transition question
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("target", ["archived", "outdated", "retracted",
                                    "superseded"])
def test_an_undeclared_transition_from_live_is_refused(env, target):
    """``_ALLOWED_TRANSITIONS['live']`` declares exactly retracted/superseded.

    ``archived`` is *"reserved — no v1 write path"* and ``outdated`` is written
    only by ``invalidate_point`` / ``supersede_point``; neither is a declared
    transition from ``live``, so the generic door must not mint them.
    ``retracted`` / ``superseded`` are declared and stay legal.
    """
    sdk, _ = env
    pid = _live(sdk)
    declared = _ALLOWED_TRANSITIONS["live"]

    if target in declared:
        sdk.update_entity(pid, status=target)
        assert _row(sdk, pid)["status"] == target
        return

    with pytest.raises(ValueError) as exc:
        sdk.update_entity(pid, status=target)
    text = str(exc.value)
    assert target in text and "transition" in text.lower(), (
        f"the refusal must name the target status and the transition: {text!r}")
    assert _row(sdk, pid)["status"] == "live"


def test_restating_the_same_status_is_a_no_op_not_a_refusal(env):
    """Identical status is not a transition — idempotent re-writes stay legal."""
    sdk, _ = env
    pid = _live(sdk)
    sdk.update_entity(pid, status="live", content="same status, new content")
    assert _row(sdk, pid)["status"] == "live"
    assert sdk.get_point(pid)["content"] == "same status, new content"


# ══════════════════════════════════════════════════════════════════════
# Atomicity — a refusal precedes EVERY write, including the journal
# ══════════════════════════════════════════════════════════════════════

def test_a_refused_call_writes_nothing_and_journals_nothing(env):
    """The status arm journals ``restatus`` AFTER its write (#3311); a refusal
    must precede BOTH, or the caller is told it failed while the graph moved."""
    sdk, events = env
    old, _new = _superseded(sdk)
    # NON-VACUITY: this reader must be able to see a re-status record at all, or
    # "no record was added" is satisfied by a reader that always returns [].
    # A declared transition on a HEALTHY point is the control.
    _control = _live(sdk, "a healthy control claim")
    sdk.update_entity(_control, status="retracted")
    assert _mutations(events, _control), (
        "the journal reader saw no record for a re-status that MUST be "
        "journaled — the atomicity assertion below would be vacuous")
    before = _row(sdk, old)
    before_recs = _mutations(events, old)

    with pytest.raises(ValueError):
        sdk.update_entity(old, status="live")
    with pytest.raises(ValueError):
        sdk.update_entity(old, validTo="2030-01-01T00:00:00Z")

    assert _row(sdk, old) == before, "a refused call moved the graph"
    assert _mutations(events, old) == before_recs, (
        "a refused call appended an EntityMutated record — the journal would "
        "replay a write the caller was told did not happen")


# ══════════════════════════════════════════════════════════════════════
# The guard does not leak onto the other labels this door serves
# ══════════════════════════════════════════════════════════════════════

def test_the_generic_door_still_serves_the_other_labels(env):
    """``update_entity`` is the tenant route onto Object/Subject/Event/… —
    a Point guard that raises ``No point …`` for a non-Point id would break
    every one of them (``missing_ok`` exists for exactly this)."""
    sdk, _ = env
    obj = sdk.create_object("Widget", kind="core:entity")["id"]
    sdk.update_entity(obj, status="archived")
    assert sdk.get_entity(obj)["status"] == "archived"

    subj = sdk.create_subject("team-x")["id"]
    sdk.update_entity(subj, status="superseded")
    assert sdk.get_entity(subj)["status"] == "superseded"


def test_an_absent_id_is_still_the_documented_empty_reply(env):
    """A typo must not become a lifecycle refusal (the #10119 absent contract)."""
    sdk, _ = env
    assert sdk.update_entity("no-such-point-9649", status="live") == {}
