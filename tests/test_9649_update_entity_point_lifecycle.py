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
import re

import pytest

from tortoise.sdk import _ALLOWED_TRANSITIONS, _TEMPORAL_RESTAMP_PROPS, TortoiseSDK

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


def test_deprecated_is_terminal_even_though_the_map_has_no_key(env):
    """Terminality is the SHARED predicate, not an entry in the transition map.

    ``deprecated`` is terminal in ``live.is_terminal_status`` and has NO key in
    ``_ALLOWED_TRANSITIONS``, so a guard that re-derived terminality from the map
    (or from an empty declared-target set) would let this write through. The
    shared instrument refuses it — which is what makes "is this claim dead?" and
    "which status may it become?" answer from ONE place rather than two.
    """
    sdk, _ = env
    pid = _live(sdk)
    _force_status(sdk, pid, "deprecated")
    before = _row(sdk, pid)
    assert "deprecated" not in _ALLOWED_TRANSITIONS, (
        "this arm is only non-vacuous while the map carries no `deprecated` key")

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(pid, validTo="2030-01-01T00:00:00Z")
    assert _row(sdk, pid) == before


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
    """that it refused · exactly what failed · exactly what to do.

    Review round 12c: part 3 must be **performable**, not merely named. This
    refusal fires only for a claim the guard already found TERMINAL, and
    ``supersede_point`` refuses a terminal SOURCE — so the old
    ``supersede_point(dead_id, new_id)`` sentence prescribed a call that always
    raises, and ``assert "supersede_point" in text`` (this test's previous form)
    was satisfied by exactly that unperformable sentence. It now asserts the
    route that exists (``create_point``) AND runs the negative control that
    makes the assertion non-vacuous.
    """
    sdk, _ = env
    old, new = _superseded(sdk)

    with pytest.raises(ValueError) as exc:
        sdk.update_entity(old, status="live")
    text = str(exc.value)

    # 1. that it refused
    assert "refus" in text.lower(), f"no refusal stated: {text!r}"
    # 2. exactly what failed — the dead state and the fields asked for
    assert "superseded" in text, f"the found state is missing: {text!r}"
    assert "status" in text, f"the refused field is missing: {text!r}"
    # 3. exactly what to do — a route that can actually be taken
    assert "create_point(" in text, (
        f"the repair must name the route that exists (mint the corrected "
        f"claim): {text!r}")
    assert "supersede this one with" not in text, (
        f"the refusal must not tell the caller to supersede a claim it has "
        f"just called terminal — `supersede_point` refuses a terminal SOURCE, "
        f"so that prescription cannot be run: {text!r}")
    # ⛔ Review round 12d/12e, and this is a pin, not a preference: the denial
    # must be scoped to a claim whose RECORDED STATUS is terminal — in EVERY
    # clause. The absolute form ("no SDK door re-livens a dead claim") is
    # measurably FALSE on the flag-dead shape this same arm fires for —
    # `update_point(<status NULL, outdated=true>, status='live')` stores
    # `status='live'`, the residual `update_point`'s own promote-arm comment
    # records — and a refusal whose subject is repair accuracy cannot itself
    # overstate what is possible.
    #
    # ⛔ The check reads the DENIAL, it does not match one phrasing: round 12d's
    # first form was `"re-livens a dead claim" not in text`, which a message
    # naming NO scope (`re-livens a claim, or …`) and a one-word paraphrase of
    # the same absolute (`re-livens any dead claim`) both satisfied — an
    # assertion claiming more than it enforced, the exact class this PR closes.
    # The shape asserted is the PROPERTY: the one-way sentence names a scope
    # keyed to the recorded status, and carries no unscoped dead-claim absolute.
    # ⛔ The sentence is selected by the DENIAL VERB, not by "one-way": the
    # guard's own interpolated message also says "one-way" (`… terminalization is
    # one-way`), and selecting on that pulled the outer prefix "on a dead claim:"
    # into the check (measured: the pin red on its own correct message).
    _denial = [s for s in re.split(r"(?<=[.;])\s+", text) if "re-livens" in s]
    assert _denial, f"the one-way denial is missing: {text!r}"
    _denial = " ".join(_denial)
    assert "dead claim" not in _denial, (
        f"the one-way denial must not state the absolute a measured door "
        f"contradicts (a flag-dead point takes `status='live'` through "
        f"`update_point`): {_denial!r}")
    assert re.search(r"recorded[^.;]{0,24}terminal", _denial), (
        f"the one-way denial must name its scope (a claim whose RECORDED status "
        f"is terminal), not merely assert one-wayness: {_denial!r}")

    # ...and the assertion above is not vacuous: superseding the dead id really
    # does raise, which is WHY the old prescription was wrong.
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede_point(old, new)


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


# ══════════════════════════════════════════════════════════════════
# The SHAPE the write binds, not just the key it was given (review F1/F2)
# ══════════════════════════════════════════════════════════════════

def test_a_point_addressed_by_its_event_id_is_guarded_too(env):
    """Review F1: this door binds a Point by `id`, `eventId` AND `url`.

    A `MATCH (n:Point {id:$id})` guard missed a `:Point:Event` reached through
    the Event arm by `eventId` — measured at the review head: `validTo`,
    `validFrom`, `when` and `status='live'` all landed on a SUPERSEDED claim,
    resurrecting it with a rewritten window. No production writer mints the
    hybrid label, but the guard's claim is "the Point the write touches".
    """
    sdk, _ = env
    old, _new = _superseded(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Event, n.eventId=$eid",
        params={"id": old, "eid": "evt-review-9649"})
    before = _row(sdk, old)

    for props in ({"validTo": "2031-01-01T00:00:00Z"},
                  {"validFrom": "2019-01-01T00:00:00Z"},
                  {"when": "2031-01-01T00:00:00Z"},
                  {"status": "live"}):
        with pytest.raises(ValueError, match="terminal"):
            sdk.update_entity("evt-review-9649", **props)
    assert _row(sdk, old) == before, (
        "the eventId-addressed write must leave the dead claim alone")


def test_a_point_addressed_by_its_url_is_guarded_too(env):
    """Review F1, the Source arm: `status='live'` on a dead `:Point:Source`."""
    sdk, _ = env
    old, _new = _superseded(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Source, n.url=$url",
        params={"id": old, "url": "src://review-9649"})
    before = _row(sdk, old)

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity("src://review-9649", status="live")
    assert _row(sdk, old) == before


def test_one_row_is_not_the_fan_out_for_the_transition_check(env):
    """Review F2: the write binds every node, so the check must ask every row.

    A duplicate-id fan-out is mintable through the public `create_point(id=…)`
    door (the shape `live.py` calls "tested, graph-reachable"). Reading ONE
    representative status let `{draft, live} -> draft` through — the
    representative is the sorted-first, i.e. the draft — which DEMOTED the live
    row: an undeclared `live -> draft`, the transition this check exists to
    refuse (a draft is inert for EP).
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "fan one", dedup=False,
                           status="draft")["id"]
    sdk.create_point("statement", "fan two", dedup=False,
                     status="live", id=pid)
    assert sorted(r[0] for r in _fan(sdk, pid)) == ["draft", "live"]

    with pytest.raises(ValueError) as exc:
        sdk.update_entity(pid, status="draft")
    text = str(exc.value)
    assert "transition" in text.lower() and "live" in text, (
        f"the refusal must name the row it would demote: {text!r}")
    assert sorted(r[0] for r in _fan(sdk, pid)) == ["draft", "live"], (
        "a refused transition must not demote the live row")


def _fan(sdk, pid: str) -> list[tuple]:
    """Every `(status, validTo)` row the fan-out holds under one id."""
    return sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, n.validTo",
        params={"id": pid}).result_set


def test_an_id_less_terminal_point_is_refused_not_skipped(env):
    """Review round 2, P1: the fail-closed arm could not fire.

    `collect(DISTINCT n.id)` DROPS nulls, so an id-less `:Point:Source` came back
    as `[]` — read as "no Point addressed" — and `status='live'` resurrected it
    (measured: a superseded row went live). `sum(CASE WHEN n.id IS NULL …)` is what
    detects the shape; this test reds if it is reverted to a length test.
    """
    sdk, _ = env
    sdk._get_proj().g.query(
        "CREATE (n:Point:Source {url:'src://idless-9649', status:'superseded', "
        "validTo:'2020-01-01T00:00:00Z'})")
    before = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.url=$u RETURN n.status, n.validTo",
        params={"u": "src://idless-9649"}).result_set

    with pytest.raises(ValueError, match="carries no `id`"):
        sdk.update_entity("src://idless-9649", status="live")
    after = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.url=$u RETURN n.status, n.validTo",
        params={"u": "src://idless-9649"}).result_set
    assert after == before == [["superseded", "2020-01-01T00:00:00Z"]], (
        f"the id-less dead claim must not move: {before} -> {after}")


def test_a_points_provenance_event_id_does_not_guard_an_event_write(env):
    """Review round 2, P2: the address resolution must not be WIDER than the write.

    `eventId` is a writable provenance property on a plain `:Point` (#1417), and
    this door reaches a Point by `eventId` only through the `:Event` arm. An
    unqualified `n.eventId = $id` therefore refused an ordinary `:Event` update
    because an unrelated Point carried that value — naming a Point the caller
    never addressed (AX rule 2) and blocking a healthy write.
    """
    sdk, _ = env
    sdk.create_point("statement", "carries provenance", dedup=False,
                     status="retracted", eventId="ev-9649-prov")["id"]
    sdk._get_proj().g.query(
        "CREATE (e:Event {eventId:'ev-9649-prov', name:'session'})")

    # ⛔ A NON-TERMINAL status is what makes this test still possible at all:
    # since #11825 a TERMINAL status on an `:Event` is refused by that guard
    # first (measured: `status='archived'` here raises "not writable on an
    # :Event" before the address resolution is consulted), so `archived` would
    # pin #11825's rule instead of this one. `live` rides the prop lane #2788
    # leaves open, and the address read still has to NOT find the retracted
    # provenance Point — which is the whole claim under test.
    sdk.update_entity("ev-9649-prov", status="live")
    assert sdk.get_entity("ev-9649-prov")["status"] == "live"


def test_a_hybrid_point_status_write_is_validated_and_journals_the_POINT(env):
    """Review round 3, P2: the status arm must key on the RESOLVED address.

    The guard learned to resolve `eventId`/`url`, but the vocabulary check, the
    declared transition and the `status`-drop from `props` were still gated on
    `MATCH (n:Point {id:$id})`. On a `:Point:Event` addressed by its `eventId`
    that read found no Point, so `status='archived'` and `status='garbage'` both
    LANDED and the key rode into the `:Event` arm, which journalled `restatus`
    against `label='Event'` — a record no fold can apply (a live write that makes
    `rebuild_all` refuse). Measured at `a35a3973b7`.

    ⛔ TWO NODES since #11825 (#9649 merge review): an `:Event` never carries a
    TERMINAL status (that guard refuses one first, with its own message), and
    every DECLARED transition out of a non-terminal Point status is terminal —
    so the transition refusal reachable on a hybrid is an UNDECLARED
    non-terminal one (`live -> draft`), while the journal half is the prop-lane
    promotion `draft -> live`. Splitting them keeps both halves of this test's
    claim pinned instead of pinning #11825's rule by accident.
    """
    sdk, events = env
    pid = _live(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Event, n.eventId=$eid",
        params={"id": pid, "eid": "evt-hybrid-9649"})

    with pytest.raises(ValueError, match="transition"):
        sdk.update_entity("evt-hybrid-9649", status="draft")
    with pytest.raises(ValueError, match="Invalid status"):
        sdk.update_entity("evt-hybrid-9649", status="garbage")
    assert _row(sdk, pid)["status"] == "live", (
        "a refused hybrid status write must not touch the row")

    draft_pid = sdk.create_point(
        "statement", "draft hybrid", dedup=False, status="draft")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Event, n.eventId=$eid",
        params={"id": draft_pid, "eid": "evt-hybrid-draft-9649"})
    sdk.update_entity("evt-hybrid-draft-9649", status="live")
    assert _row(sdk, draft_pid)["status"] == "live"
    recs = _mutations(events, draft_pid)
    assert recs, "the declared transition must be journaled against the POINT id"
    assert all(r.get("label") == "Point" for r in recs), (
        f"a restatus record must name the label the node replays as: {recs}")
    assert _mutations(events, "evt-hybrid-draft-9649") == [], (
        "nothing may be journalled against the ADDRESS the caller typed")
    # …and the record must actually FOLD: a wrong label is only visible on replay
    # (a live write that makes `rebuild_all` raise `NonFoldedEventsError`).
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _row(sdk, draft_pid)["status"] == "live", (
        "the journalled restatus must replay onto the Point")


def test_a_source_keyed_by_id_is_not_blocked_by_a_hybrid_point_url(env):
    """Review round 3, P2: primary first, secondary only on a miss.

    The `:Source` arm resolves `id` first and consults `url` only on a miss, so a
    `:Point:Source{url:<X>}` is untouched by `update_entity(<X>, …)` when a
    `:Source{id:<X>}` exists. The guard's unqualified url arm refused that healthy
    write and blamed a Point the caller never addressed (AX rule 2).
    """
    sdk, _ = env
    sdk._get_proj().g.query("MERGE (s:Source {id:'X-9649'}) SET s.url='healthy-src'")
    sdk._get_proj().g.query(
        "CREATE (n:Point:Source {url:'X-9649', status:'superseded', "
        "validTo:'2020-01-01T00:00:00Z'})")
    hybrid = sdk._get_proj().g.query(
        "MATCH (n:Point:Source) WHERE n.url=$u RETURN n.status",
        params={"u": "X-9649"}).result_set

    sdk.update_entity("X-9649", status="retired")
    assert sdk._get_proj().g.query(
        "MATCH (s:Source {id:'X-9649'}) RETURN s.status",
    ).result_set == [["retired"]]
    assert sdk._get_proj().g.query(
        "MATCH (n:Point:Source) WHERE n.url=$u RETURN n.status",
        params={"u": "X-9649"}).result_set == hybrid, (
        "the hybrid Point the write could not reach must be untouched")


def test_a_null_status_point_still_validates_and_journals_its_status(env):
    """Review round 4, P1: the status arm must gate on the ADDRESS, not on a
    collected status list.

    `collect(DISTINCT n.status)` drops NULLs, so a Point whose stored status is
    NULL (the legacy shape the backfill route exists for) yielded `()` — read as
    "no Point addressed". Measured with that gate: `status='garbage'` was STORED
    (the #3311 defect this door closes) and `status='live'` landed UNJOURNALLED,
    so `rebuild_all` reverted it. Both must behave as on a status-bearing point.
    """
    sdk, events = env
    pid = _live(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=NULL", params={"id": pid})

    with pytest.raises(ValueError, match="Invalid status"):
        sdk.update_entity(pid, status="garbage")
    assert _row(sdk, pid)["status"] is None, (
        "a refused vocabulary must not store anything")

    sdk.update_entity(pid, status="live")
    assert _row(sdk, pid)["status"] == "live"
    recs = _mutations(events, pid)
    assert [r.get("label") for r in recs] == ["Point"], (
        f"the backfill write must be journaled against Point: {recs}")


def test_a_read_modify_write_of_the_unchanged_status_still_lands(env):
    """Review round 4, P2: an ENTAILED status is not a lifecycle write.

    A generic tenant PUT reads the row and sends it back with the fields it
    changed. Refusing `update_entity(terminal, status=<stored>, content=…)`
    contradicted this refusal's own last sentence ("content and metadata
    revisions are still accepted on it") and made the declared-preserved #3305
    revision a hard error. A CHANGED status on the same claim is still refused.
    """
    sdk, _ = env
    pid = _live(sdk, "rmw")
    sdk.retract_point(pid)
    stored = sdk.get_point(pid)["status"]
    assert stored == "retracted"

    sdk.update_entity(pid, status=stored, content="read-modify-write revision")
    assert sdk.get_point(pid)["content"] == "read-modify-write revision"
    assert _row(sdk, pid)["status"] == "retracted", (
        "the revision must not disturb the terminal status")

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(pid, status="live", content="a resurrection attempt")


def test_the_entailed_status_exemption_is_narrow(env):
    """Review round 5, P2: the exemption is pinned at all three of its edges.

    A generic PUT may re-send the status a terminal claim already has — that is
    `test_a_read_modify_write_of_the_unchanged_status_still_lands`. Each of the
    three narrowings below, dropped, either re-opens probe 2 or resurrects a
    sibling, while the rest of the suite stays green.
    """
    sdk, _ = env

    # (a) an entailed status PLUS a temporal field is still a window write.
    t = _live(sdk, "entailed+temporal")
    sdk.retract_point(t)
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(t, status="retracted", validTo="2030-01-01T00:00:00Z")
    assert _row(sdk, t)["validTo"] != "2030-01-01T00:00:00Z", (
        "the temporal half must be refused even when the status is entailed")

    # (b) a NULL-status row is NOT "equal" — and it must not ride in on the
    #     exemption just because its SIBLINGS hold the status. (A lone
    #     NULL-status row is already refused by the `()` != (status,) clause, so
    #     only the fan-out shape exercises `not _bound_null_statuses`.)
    f2 = _live(sdk, "fan with legacy null")
    sdk.retract_point(f2)
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:$id, content:'legacy null row'})",
        params={"id": f2})
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(f2, status="retracted")

    # (c) EVERY bound row must hold it — "any" resurrects a terminal sibling.
    f = _live(sdk, "fan terminal")
    sdk.retract_point(f)
    sdk.create_point("statement", "fan live twin", dedup=False,
                     status="live", id=f)
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(f, status="retracted")
    assert sorted(r[0] for r in _fan(sdk, f)) == ["live", "retracted"], (
        "the refused call must leave both rows as they were")


def test_the_entailed_status_exemption_reads_the_write_row_set(env):
    """Review round 5, P1: the exemption must read the rows the WRITE binds.

    The secondary arms match NARROWER than the write (`:Event` by eventId,
    `:Source` by url) while the write binds every node carrying the resolved id.
    Measuring the exemption on the ADDRESSED rows let an addressed `{live}` beside
    an unaddressed terminal sibling satisfy "every row holds it", and the write
    then resurrected the sibling — measured on the `deprecated` twin of a
    `:Point:Event` addressed by its eventId.
    """
    sdk, _ = env
    pid = _live(sdk, "hybrid with twin")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Event, n.eventId=$eid",
        params={"id": pid, "eid": "evt-twin-9649"})
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:$id, content:'twin', status:'deprecated'})",
        params={"id": pid})
    before = sorted(r[0] for r in _fan(sdk, pid))
    assert before == ["deprecated", "live"], before

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity("evt-twin-9649", status="live")
    assert sorted(r[0] for r in _fan(sdk, pid)) == before, (
        "the unaddressed terminal twin must not be resurrected")


def test_a_falsy_non_null_id_is_still_guarded(env):
    """Review round 6, P2: `if i` filtered falsy NON-null ids, failing open.

    `_lifecycle_ids` is the single gate for the guard, the vocabulary check, the
    declared transition, the `status`-drop and the journal, so a dropped row
    skips all five. `collect` already drops NULLs, so `if i` could only ever
    filter a falsy id (`0`) — measured: a superseded `id: 0` Point was
    resurrected by `status='live'` and had its window rewritten, and a live one
    accepted `status='garbage'` unjournalled. Unreachable through
    `create_point`, but the door fails CLOSED on the id-less shape for the same
    reason, so it must here too.
    """
    sdk, _ = env
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:0, status:'superseded', outdated:true, "
        "validTo:'2020-01-01T00:00:00Z'})")

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(0, status="live")
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(0, validTo="2031-01-01T00:00:00Z")
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:0}) RETURN n.status, n.validTo").result_set == [
            ["superseded", "2020-01-01T00:00:00Z"]], (
        "the falsy-id row must be untouched")


def test_operator_points_keep_their_documented_lifecycle(env):
    """Review round 6, P2: `allow_operator=True` had no coverage.

    This door writes operator points too. A LIVE operator's declared transition
    lands (it must — `allow_operator` exists for it), while a TERMINAL operator
    refuses both `status` and the window (terminality is one-way on every
    Point, operator or not). Turning `allow_operator` off reds this.
    """
    sdk, _ = env
    a = _live(sdk, "op src")
    b = _live(sdk, "op tgt")
    live_op = sdk.create_operator("IMPL", a, [b])["id"]
    sdk.update_entity(live_op, status="retracted")
    assert _row(sdk, live_op)["status"] == "retracted"

    dead_op = sdk.create_operator("NAND", a, [b])["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='superseded'",
        params={"id": dead_op})
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(dead_op, status="live")
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_entity(dead_op, validTo="2031-01-01T00:00:00Z")
    assert _row(sdk, dead_op)["status"] == "superseded"


def test_the_guard_is_asked_with_the_doors_own_permissions(env, monkeypatch):
    """Review round 8, P2: `missing_ok`/`allow_operator` were un-pinned.

    The absent contract (#10119) is protected by the ADDRESS loop being empty, not
    by the parameter — so this pins both halves: a resolved Point reaches the ONE
    instrument exactly once with `missing_ok=True` and `allow_operator=True`, and
    an absent id never reaches it at all.
    """
    sdk, _ = env
    pid = _live(sdk)
    seen: list = []
    real = sdk._assert_lifecycle_guard

    def spy(point_id, **kw):
        seen.append((point_id, kw))
        return real(point_id, **kw)

    monkeypatch.setattr(sdk, "_assert_lifecycle_guard", spy)

    sdk.update_entity(pid, validTo="2030-01-01T00:00:00Z")
    assert [s[0] for s in seen] == [pid], seen
    assert seen[0][1].get("missing_ok") is True, seen[0][1]
    assert seen[0][1].get("allow_operator") is True, seen[0][1]

    seen.clear()
    assert sdk.update_entity("no-such-point-9649", status="live") == {}
    assert seen == [], (
        "an absent id must not reach the guard — the loop's emptiness is the "
        "absent contract, not the parameter")


def test_a_map_less_stored_status_still_defers(env):
    """Review round 8, P2: the DEFER half had no pin for a non-NULL legacy word.

    `_ALLOWED_TRANSITIONS` carries no entry for a legacy spelling, and the door
    must DEFER rather than guess — refusing there would strand every graph
    written with a word the table does not know. Every map-less status in the
    fixtures so far is also TERMINAL, so the guard refused it before the
    transition loop and the deferral was never exercised.
    """
    sdk, _ = env
    pid = _live(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='current'", params={"id": pid})
    assert "current" not in _ALLOWED_TRANSITIONS, (
        "this arm is only non-vacuous while the map has no 'current' key")

    sdk.update_entity(pid, status="live")
    assert _row(sdk, pid)["status"] == "live"
    sdk.update_entity(pid, status="retracted")
    assert _row(sdk, pid)["status"] == "retracted"


def test_two_address_arms_resolving_one_id_journal_one_record(env):
    """Review round 9, P2: the resolved-id list must be DE-DUPLICATED.

    A `:Point:Event` whose `id` and `eventId` are the same string is resolved by
    two arms, so an un-deduplicated list journalled the SAME `restatus` twice for
    one intent — the class `delete_point`'s own comment names ("a second
    destruction record for one intent"). Replay is idempotent, so only a count
    assertion catches it.
    """
    sdk, events = env
    pid = sdk.create_point(
        "statement", "one node, two arms", dedup=False, status="draft")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Event, n.eventId=$id",
        params={"id": pid})

    # ⛔ `draft -> live`, not `-> retracted`: since #11825 a TERMINAL status on
    # an `:Event` is refused before this arm (measured), and this test is about
    # the dedupe, not that rule. The promotion is a declared, non-terminal
    # transition that still journals exactly one `restatus`.
    sdk.update_entity(pid, status="live")
    recs = [r for r in _mutations(events, pid) if r.get("op") == "restatus"]
    assert len(recs) == 1, f"one intent must journal one record: {recs}"


def test_a_write_that_matches_no_row_journals_nothing(env, monkeypatch):
    """Review round 9, P2: the journal is gated on the write having MATCHED.

    ``_journal_entity_mutation`` runs only when ``SET n += $s`` reported a row, or
    a row deleted between the read and the write would leave a `restatus` record
    for a write that never happened (the class the loop's own comment names).
    The deletion is injected through the guard, which is the last step before the
    write, so the race is deterministic here.
    """
    sdk, events = env
    pid = _live(sdk, "vanishes before the write")
    real = sdk._assert_lifecycle_guard

    def spy(point_id, **kw):
        out = real(point_id, **kw)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": point_id})
        return out

    monkeypatch.setattr(sdk, "_assert_lifecycle_guard", spy)
    # ⚠️ a DECLARED transition, not an entailed one: an entailed status skips the
    # guard loop entirely (that is its purpose), so it would not run the spy.
    sdk.update_entity(pid, status="retracted")
    restatus = [r for r in _mutations(events, pid) if r.get("op") == "restatus"]
    assert restatus == [], (
        f"a write that matched no row must not journal a restatus: {restatus}")


# ══════════════════════════════════════════════════════════════════════
# The residual this change DECLARES (#12403) — pinned, not just prose
# ══════════════════════════════════════════════════════════════════════

def test_a_declared_terminalizing_transition_carries_none_of_the_verbs_effects(env):
    """#9649 review F1 / #12403: the door PERMITS a declared terminal target,
    and performs none of the effects the verb that owns it performs.

    ``_ALLOWED_TRANSITIONS['live']`` is ``{retracted, superseded}``, so the
    transition arm accepts both — but this arm writes ``status`` and journals
    ``EntityMutated/restatus`` and NOTHING else. Measured here, at this head:

    * no ``PointSuperseded`` / ``PointRetracted`` record — those are the
      ``:GraphEvent`` store's claim-transition set (``#432`` Task 3), while
      ``EntityMutated`` is JSONL-only (``#548``), so a change-feed subscriber
      never learns the claim died;
    * no ``CORRECTS`` edge for ``superseded`` — ONTOLOGY §5 defines that state
      AS *"structural replacement via CORRECTS edge"*, so the claim leaves
      belief (``_terminal_excluded``) and nothing replaces it;
    * no window close and no ``updatedAt`` stamp, unlike ``retract_point`` /
      ``supersede_point``.

    ⛔ THIS PINS A DISCLOSED RESIDUAL, NOT A CONTRACT WE WANT. If #12403 lands
    (refuse the terminalizing targets and name the verb, or emit the record),
    the assertions below INVERT — ``pytest.raises(ValueError)`` for the first
    shape, or a ``PointSuperseded`` record for the second. Update the pin with
    the fix; do not delete it, and do not read its presence as approval of the
    behaviour.
    """
    sdk, events = env
    pid = _live(sdk, "claim that will be retired through the generic door")
    before = _row(sdk, pid)
    assert before["validTo"] is None, before

    sdk.update_entity(pid, status="superseded")

    after = _row(sdk, pid)
    assert after["status"] == "superseded", after
    # 1. no claim-transition record of any kind.
    journal = [
        json.loads(line) for line in
        (events / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()]
    kinds = {r.get("type") for r in journal if r.get("id") == pid}
    assert "PointSuperseded" not in kinds, (
        f"if this record appears, the door now emits the claim transition and "
        f"this residual is CLOSED (#12403) — invert this pin: {sorted(kinds)}")
    assert "EntityMutated" in kinds, (
        f"the restatus journal is what the door does write: {sorted(kinds)}")
    # 2. no successor edge, and the claim really does leave belief.
    # ⛔ INCOMING, not outgoing (review round 12): the SDK spells the edge
    # `MERGE (a)-[:CORRECTS]->(b)` with `a` = the CORRECTING point, so a
    # superseded node has ZERO OUTGOING edges whether or not a successor
    # exists — an outgoing count is unfalsifiable in the direction that
    # matters, and a future fix minting `(new)-[:CORRECTS]->(old)` would
    # leave it green. ONTOLOGY §5 defines the state by the edge INTO it.
    assert sdk._get_proj().g.query(
        "MATCH (:Point {id:$id})<-[r:CORRECTS]-() RETURN count(r)",
        params={"id": pid}).result_set[0][0] == 0, (
        "#12403: a successor-less `superseded` is the shape ONTOLOGY §5 forbids")
    assert sdk.query("claim that will be retired") in ([], None), (
        "the dead claim must leave belief — that is WHY a missing successor "
        "matters (#2498)")
    # 3. the window the verbs close stays exactly as it was.
    assert after["validTo"] == before["validTo"], after

    # ...and the VERB, on the same shape, does all three (the contrast the
    # residual rests on — non-vacuity: this half must PASS). `supersede_point`,
    # not `retract_point`: an earlier version of this contrast used the retract
    # verb, which mints NO `CORRECTS` edge, so the residual's most consequential
    # half — the missing successor — had no verb-side demonstration at all.
    other = _live(sdk, "claim retired through the verb")
    succ = _live(sdk, "its successor")
    sdk.supersede_point(other, succ)
    assert _row(sdk, other)["status"] == "superseded"
    assert _row(sdk, other)["validTo"] is not None, (
        "the verb closes the superseded claim's window")
    kinds = {json.loads(line).get("type") for line in
             (events / "events.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip() and json.loads(line).get("id") == other}
    assert "PointSuperseded" in kinds, (
        f"the verb must journal the claim transition: {sorted(kinds)}")
    assert sdk._get_proj().g.query(
        "MATCH (:Point {id:$id})<-[r:CORRECTS]-() RETURN count(r)",
        params={"id": other}).result_set[0][0] == 1, (
        "the verb mints the successor edge the generic door omits — this is the "
        "half that makes the residual matter")


def test_the_refusal_also_names_the_route_for_a_read_modify_write(env):
    """Review round 11 (P2, AX rule 2): the repair must fit the caller's INTENT.

    A generic tenant PUT is a read-modify-write — it fetches the row and sends it
    back with the fields it changed (this change's own ``_status_entailed``
    comment names that shape) — so a caller revising the CONTENT of a dead claim
    echoes the stored window as well, and is refused for it. The rejection is
    right (#7819 refuses the same shape on ``update_point``); the PRESCRIPTION
    was not: "mint a new Point and supersede" is the wrong repair for a caller
    who only wanted a content revision, and the final sentence promised the
    capability without saying how to reach it.

    The route is therefore named in the message. ⛔ Do NOT "fix" the shape by
    comparing the echoed window against the stored one and exempting an unchanged
    value: ``update_point`` refuses it by design (#7819), so an exemption here
    would make the two doors disagree rather than patch this one.
    """
    sdk, _ = env
    old, _new = _superseded(sdk)
    stored = sdk.get_point(old)

    with pytest.raises(ValueError) as exc:
        sdk.update_entity(old, validTo=stored["validTo"],
                          content="a re-sent, corrected reading")
    text = str(exc.value)
    assert "WITHOUT the temporal fields" in text, (
        f"the repair must name the route a read-modify-write can take: {text!r}")
    assert "content" in text.lower(), (
        f"the message must say what IS still accepted: {text!r}")

    # ...and the route it names really works (non-vacuity: this half must pass).
    sdk.update_entity(old, content="a re-sent, corrected reading")
    assert sdk.get_point(old)["content"] == "a re-sent, corrected reading"
    assert _row(sdk, old)["validTo"] == stored["validTo"], (
        "the revision must not disturb the window the supersession closed")


def test_the_transition_refusal_names_the_RESOLVED_id_not_the_typed_address(env):
    """Review round 12 (P3, AX rule 2): the prescription must be PERFORMABLE.

    A `:Point:Source` addressed by its `url` reaches the transition arm with
    `id_val='<url>'` while the Point's own id is something else — and
    ``supersede_point`` resolves by ``id`` ONLY, so the message's
    ``supersede_point('<url>', …)`` was a call that raises ``No point``. The
    refusal now names the RESOLVED id (kept, with the typed address as a separate
    "addressed as" clause) and interpolates that same id into the prescription,
    matching what the dead-claim refusal already did.

    ⛔ The vehicle is `:Point:Source` because since #11825 the transition arm is
    UNREACHABLE on a `:Point:Event`: every declared transition out of a
    non-terminal Point status is terminal, and a terminal status on an `:Event`
    is refused by that guard first (measured at the merge head). The claim under
    test — the prescription names the RESOLVED id, not the typed address — is
    label-independent, and `url` is the other secondary arm that resolved one.
    """
    sdk, _ = env
    pid = _live(sdk, "a claim addressed by its url")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Source, n.url=$url, n.status='live'",
        params={"id": pid, "url": "src://round12-9649"})

    with pytest.raises(ValueError) as exc:
        sdk.update_entity("src://round12-9649", status="draft")
    text = str(exc.value)

    assert f"supersede_point({pid!r}" in text, (
        f"the prescription must name the RESOLVED id {pid!r}, or it cannot be "
        f"run: {text!r}")
    assert "supersede_point('src://round12-9649'" not in text, (
        f"supersede_point resolves by `id` only — this call would raise "
        f"`No point`: {text!r}")
    assert "addressed as 'src://round12-9649'" in text, (
        f"the typed address must still be shown, so the caller recognises its "
        f"own call: {text!r}")

    # ...and the prescribed call really runs from the RESOLVED id (non-vacuity:
    # an assertion that the message contains an id proves nothing about whether
    # that id can be handed to the verb — review round 13, P3).
    fresh = _live(sdk, "the successor the message tells me to mint")
    sdk.supersede_point(pid, fresh)
    assert _row(sdk, pid)["status"] == "superseded", (
        "the route the refusal names must be performable exactly as written, "
        "with the resolved id it interpolates")


# ══════════════════════════════════════════════════════════════════════
# Review round 13 — the guard's PRESENCE TEST is shape-independent
# ══════════════════════════════════════════════════════════════════════
#
# Measured at the review head, after the guard landed: a `str` SUBCLASS key that
# overrides `__hash__` is missed by `"status" in props` (and by `set(props)`),
# while the WRITE still files it under `status` — the live transport inlines
# `str(key)` and the journal writes the key's content. So the guard was skipped
# and the value it exists to refuse landed anyway:
#
#   update_entity(<superseded>, **{KHash("status"): "live"})  -> ACCEPTED
#     after: ['live', validTo=<the closed instant>, outdated=True], delta 0
#
# The sibling #11825 guard 70 lines above refuses the identical call on a
# `:Point:Event`, because it iterates the keys and asks the renderer union
# (`live.declares_event_state_key`). Same door, two presence tests, one hole —
# which is why the guard now reads its subject through
# `live.lifecycle_props_by_rendering` and drops through `live.keys_rendering_as`.
# These pins keep that from regressing on either axis (presence AND drop).

class _KHash(str):
    """A `status` key the literal `in` test cannot find (hash-only deviation)."""

    __hash__ = lambda s: 424242  # noqa: E731 — the whole point of the fixture


class _KStr(str):
    """A key whose CONTENT is not `status` but whose `__str__` renders as it.

    The live transport writes `str(key)`, so this key stores `status` — the
    harder half of the class, because a guard that only reads the content form
    sees an ordinary, harmless property name.
    """

    def __str__(self):
        return "status"


def test_a_hash_overriding_status_key_cannot_resurrect_a_dead_claim(env):
    """P1 (review round 13): the presence test, not just the refusal, is guarded."""
    sdk, _ = env
    old, _new = _superseded(sdk)
    before = _row(sdk, old)

    with pytest.raises(ValueError, match="superseded"):
        sdk.update_entity(old, **{_KHash("status"): "live"})

    after = _row(sdk, old)
    assert after == before, f"the refusal must leave the row untouched: {after}"
    assert _mutations(env[1], old) == [], (
        "a refused write must not be journaled — a journaled record replays the "
        "very state the guard refused")


def test_a_str_rendered_status_key_cannot_resurrect_a_dead_claim(env):
    """The other renderer: content `q`, `__str__` returns `status`."""
    sdk, _ = env
    old, _new = _superseded(sdk)
    before = _row(sdk, old)

    with pytest.raises(ValueError, match="superseded"):
        sdk.update_entity(old, **{_KStr("q"): "live"})

    assert _row(sdk, old) == before
    assert _mutations(env[1], old) == [], "nothing may be journaled for a refusal"


def test_a_hash_overriding_window_key_cannot_rewrite_a_closed_window(env):
    """Probe 2's axis, in the same key class: `validTo` must be guarded too."""
    sdk, _ = env
    old, _new = _superseded(sdk)
    before = _row(sdk, old)
    assert before["validTo"], "the fixture must be a CLOSED window"

    with pytest.raises(ValueError, match="validTo"):
        sdk.update_entity(old, **{_KHash("validTo"): "2030-01-01T00:00:00Z"})

    assert _row(sdk, old) == before, "the closed window must survive the refusal"


def test_a_hash_overriding_status_key_cannot_store_an_undeclared_word(env):
    """The #3311 vocabulary half: `status='garbage'` was STORED, not refused."""
    sdk, _ = env
    pid = _live(sdk, "a live claim")

    with pytest.raises(ValueError, match="Invalid status"):
        sdk.update_entity(pid, **{_KHash("status"): "garbage"})

    assert _row(sdk, pid)["status"] == "live", (
        "an undeclared status word must never reach the node, whichever name it "
        "was filed under")


def test_a_plain_status_key_still_gets_the_documented_answer(env):
    """Non-vacuity control: the ordinary spelling must behave, not be refused.

    Without this, every pin above would pass on a guard that refused every write.
    """
    sdk, _ = env
    pid = _live(sdk, "a live claim")

    sdk.update_entity(pid, status="superseded")
    assert _row(sdk, pid)["status"] == "superseded", (
        "a DECLARED transition must still land — the arm is not simply closed")

    # ...and the plain spelling is then refused on the SAME guarantee the exotic
    # keys were refused on, so the family is one rule and not two.
    with pytest.raises(ValueError, match="superseded"):
        sdk.update_entity(pid, status="live")


# ══════════════════════════════════════════════════════════════════════
def test_the_id_less_refusal_prescribes_no_unperformable_route(env):
    """Review round 12 (P3, AX rule 2): no repair the caller cannot execute.

    The fail-closed arm used to offer ``update_point(id, ...)`` — unperformable
    twice over: the node has no ``id`` to address it by, and this refusal may be
    about ``status``, which ``update_point`` cannot set at all (it promotes
    ``draft -> live`` only). It now says what is true: no SDK door can stamp an
    ``id`` (``_sanitize_props`` refuses it as a caller prop), so the routes are a
    direct stamp on the node or a re-create.
    """
    sdk, _ = env
    sdk._get_proj().g.query(
        "CREATE (n:Point:Source {url:'src://idless-9649-msg', "
        "status:'superseded'})")

    with pytest.raises(ValueError) as exc:
        sdk.update_entity("src://idless-9649-msg", status="retracted")
    text = str(exc.value)
    assert "update_point(" not in text, (
        f"the refusal must not prescribe an id-keyed call for a node with no "
        f"id (it resolves to nothing): {text!r}")
    assert "create_point(" in text, (
        f"the re-create route is the one that works and must be named: {text!r}")
    assert "_sanitize_props" in text, (
        f"the message must say WHY no SDK door can stamp the id: {text!r}")
