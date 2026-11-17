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
consulted it. The gap was a *known* one, not a decision: it was tracked as #9649,
and the model it violates is §5's Point
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
        sdk.update_knowledge(old, status="live")

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
            sdk.update_knowledge(pid, status=target)

    assert _row(sdk, pid) == before


# ══════════════════════════════════════════════════════════════════════
# #12907 — the word the guard APPROVED is the word the driver STORED
# ══════════════════════════════════════════════════════════════════════

class _LyingLen(str):
    """A `str` status whose RENDERING disagrees with its CONTENT (#12907).

    A `str` subclass is a shape JSON/MCP cannot forge but a Python caller can.
    Before the normalisation these pins require, the guard's word and the
    driver's word were two different objects: measured on a live Point,
    ``status=_LyingLen('superseded')`` stored ``''`` (the driver read
    ``len()``/truthiness) while the journal recorded the approved
    ``'superseded'`` — so the Point stayed CURRENT and ``rebuild_all`` would
    have reverted the write.
    """

    def __len__(self) -> int:
        return 0


class _CollidingHash(str):
    """A `str` status whose `__hash__`/`__eq__` collide with a LEGAL word.

    ``'garbage'`` is not in §5's Point vocabulary, but under the collision
    ``'garbage' in POINT_STATUS_VALUES`` answered True, so the vocabulary test
    admitted an UNDECLARED word and stored it verbatim (measured).
    """

    def __hash__(self) -> int:
        return hash("live")

    def __eq__(self, other) -> bool:  # the lie IS the fixture
        return True


def test_the_written_word_is_the_word_the_guard_approved(env):
    """#12907: normalise to the exact `str` content BEFORE the vocabulary test.

    Mutant that reddens: drop the `str.__str__` normalisation in the Point arm
    and the stored status becomes `''` while the journal still says
    `superseded` — a graph/record divergence, not a refusal.

    ⛔ #12592 merge: the call NAMES A SUCCESSOR, and that is not decoration.
    This case's vehicle used to be a bare ``status='superseded'`` on a live
    Point, which ONTOLOGY §4.3 now refuses at this door — *"`superseded` means
    replaced: it names the successor"* — for every label, Points included
    (`_assert_supersession_names_a_successor`). The pin's SUBJECT is the
    normalisation, not the successorless shape, so it keeps its subject and
    supplies the half §4.3 requires: the assertion, the mutant it names
    (drop `str.__str__` -> the driver stores `''`) and the journal checks are
    unchanged. §5 gives a Point's successor as the `CORRECTS` edge, but the
    guard also reads a carried `supersededBy` property, which is the half a
    single `update_entity` call can supply.
    """
    sdk, events = env
    pid = _live(sdk)
    assert _row(sdk, pid)["status"] == "live"

    sdk.update_knowledge(pid, status=_LyingLen("superseded"),
                      supersededBy="successor-name")

    after = _row(sdk, pid)
    assert after["status"] == "superseded", (
        "the driver stored a word the guard never approved (#12907): "
        + repr(after))
    blob = json.dumps(_mutations(events, pid))
    assert "restatus" in blob, (
        f"the terminalize must journal a restatus: {_mutations(events, pid)!r}")
    assert '"superseded"' in blob, (
        "the journal must carry the word the graph holds: " + blob)


def test_an_undeclared_word_cannot_ride_a_colliding_hash(env):
    """The vocabulary test must answer from CONTENT, not the caller's `__eq__`."""
    sdk, _ = env
    pid = _live(sdk)
    before = _row(sdk, pid)

    # ⛔ #11824 S0 moved the refusal's TEXT: the Point arm no longer spells its
    # own `Invalid status …` — it forwards to the ONE lifecycle door, whose
    # three-part message names the fault and the four accepted words. The
    # PROPERTY is unchanged (an undeclared word never lands), and the content
    # read is what makes it true: `_CollidingHash` claims `'garbage'`, so a
    # hash/eq-answered vocabulary test would still admit it.
    with pytest.raises(ValueError, match=r"was refused"):
        sdk.update_knowledge(pid, status=_CollidingHash("garbage"))

    assert _row(sdk, pid) == before, (
        "a refused vocabulary write must leave the Point untouched")


def test_the_legacy_outdated_flag_alone_makes_the_point_dead(env):
    """``outdated=true`` with a NULL status is terminal on every read surface
    (#2498), so it must be terminal on this door too."""
    sdk, _ = env
    pid = _live(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=NULL, n.outdated=true",
        params={"id": pid})

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge(pid, status="live")
    assert _row(sdk, pid)["status"] is None


def test_deprecated_is_terminal_even_though_the_map_has_no_key(env):
    """Terminality is the SHARED predicate, not an entry in the transition map.

    ``deprecated`` is terminal in ``live.is_terminal_status``, and the map's
    entry for it declares NO outgoing transition — so a guard that re-derived
    terminality from "the declared target set is non-empty" would let this write
    through. The shared instrument refuses it — which is what makes "is this
    claim dead?" and "which status may it become?" answer from ONE place rather
    than two.

    ⚠️ #11824 S0 gave `deprecated` a MAP KEY (the word joined the vocabulary, so
    the table gained its row); before that the arm's non-vacuity came from the
    key being ABSENT. The property to pin is the EMPTY outgoing set, which is
    what the row below asserts — an absent key is now impossible for a real
    terminal word (every member of `live.TERMINAL_EXCLUDED_STATUSES` has one).
    """
    sdk, _ = env
    pid = _live(sdk)
    _force_status(sdk, pid, "deprecated")
    before = _row(sdk, pid)
    assert _ALLOWED_TRANSITIONS.get("deprecated") == frozenset(), (
        "this arm is only non-vacuous while `deprecated` declares no outgoing "
        f"transition: {_ALLOWED_TRANSITIONS.get('deprecated')!r}")

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge(pid, validTo="2030-01-01T00:00:00Z")
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

    sdk.update_knowledge(pid, status="live")
    assert _row(sdk, pid)["status"] == "live"


# ══════════════════════════════════════════════════════════════════════
# #12907 cycle 1 — the Point ARM's own two doors (exactness + rendering)
# ══════════════════════════════════════════════════════════════════════

class _Impostor:
    """Claims `__class__ = str` — `isinstance` believes it, `type()` does not.

    The normalisation the Point arm applies is gated on
    `issubclass(type(x), str)`, so it correctly SKIPS this object (a real
    `str.__str__` on it raises). That left `isinstance(_status, str)` as the
    only type test, and the impostor passes it while its `__hash__`/`__eq__`
    collide with the LEGAL word `'live'` — so the vocabulary test admitted an
    object whose `replace()` the driver then rendered as `'archived'`: a §5
    TERMINAL word written onto a live Point, with no journal record at all.
    """

    __class__ = str

    def __eq__(self, other) -> bool:  # the lie IS the fixture
        return True

    def __hash__(self) -> int:
        return hash("live")

    def __contains__(self, item) -> bool:
        return False

    def __iter__(self):
        return iter(())

    def encode(self, *args):
        return b"archived"

    def __len__(self) -> int:
        return 8

    def replace(self, *args):
        return "archived"

    def __str__(self) -> str:
        return "archived"


class _HostileRepr:
    """A caller operand whose `__repr__` raises (#12907 cycle 1, P3)."""

    def __repr__(self) -> str:
        raise RuntimeError("repr-boom")


def test_an_impostor_cannot_write_a_terminal_word_on_a_live_point(env):
    """#12907: the vocabulary test must be EXACT-type, not `isinstance`.

    Mutant that reddens: restore `not isinstance(_status, str)`. Measured, the
    pin REDS on the refusal it actually receives: the impostor reaches the
    DECLARED-TRANSITION refusal (`'live' -> <_Impostor …>`), because the loop's
    content comparison still holds. `ACCEPTED` + a stored `'archived'` is what
    the PARENT does with BOTH pre-fix clauses restored — the graph/record
    divergence (`journal delta 0`) `rebuild_all` reverts.
    """
    sdk, events = env
    pid = _live(sdk)
    before = _row(sdk, pid)
    assert before["status"] == "live"

    with pytest.raises(ValueError, match="Invalid status"):
        sdk.update_knowledge(pid, status=_Impostor())

    assert _row(sdk, pid) == before, (
        "an impostor must not move a live Point, let alone to a terminal word: "
        + repr(_row(sdk, pid)))
    assert "restatus" not in json.dumps(_mutations(events, pid)), (
        "a refused write must journal nothing: " + repr(_mutations(events, pid)))


def test_a_hostile_repr_cannot_replace_a_point_refusal(env):
    """#12907: the refusal's contract is the CLASS — `ValueError`, always.

    Mutant that reddens: render the operand with the raw `!r` and this raises
    `RuntimeError: repr-boom` instead (row unchanged either way, so this is a
    contract break rather than a fail-open).
    """
    sdk, _ = env
    pid = _live(sdk)
    before = _row(sdk, pid)

    with pytest.raises(ValueError, match="Invalid status"):
        sdk.update_knowledge(pid, status=_HostileRepr())

    assert _row(sdk, pid) == before


class _StoredSub(str):
    """A `str` SUBCLASS as the STORED row status — the read seam (#12907 c2).

    No engine path returns one (measured: the driver normalises the parameter to
    an exact `str`, so ``_force_status`` cannot mint it), which is why the seam
    is injected at the row-set read. It exists because the cycle-1 repair
    ``type(_stored) is not str: continue`` SKIPPED the declared-transition check
    for every stored subclass where the parent checked them — a guard-STRENGTH
    regression, and a weaker guard is not a fix even at nil reachability.
    """


def test_a_stored_subclass_still_gets_the_transition_check(env, monkeypatch):
    """#12907 cycle 2, P3: normalising the STORED side must not skip it.

    Mutant that reddens: restore ``type(_stored) is not str: continue`` and this
    call is ACCEPTED, moving a `live` Point to the UNDECLARED `draft` — the
    check the loop exists to perform, skipped on the strength of the value's
    type alone.
    """
    sdk, events = env
    pid = _live(sdk, "a claim whose stored word arrives as a subclass")
    before = _row(sdk, pid)

    graph = type(sdk._get_proj().g)
    real = graph.query.__get__(sdk._get_proj().g, graph)

    class _RowSet:
        result_set = (((_StoredSub("live"),), 0),)

    def query(_graph, cypher, *args, **kwargs):
        if "collect(DISTINCT n.status)" in cypher:
            return _RowSet()
        return real(cypher, *args, **kwargs)

    monkeypatch.setattr(graph, "query", query)

    with pytest.raises(ValueError, match="refused the status transition"):
        sdk.update_knowledge(pid, status="draft")

    assert _row(sdk, pid) == before, (
        "a stored subclass must still be CHECKED, not skipped: "
        + repr(_row(sdk, pid)))
    assert "restatus" not in json.dumps(_mutations(events, pid)), (
        "the refused transition must journal nothing: "
        + repr(_mutations(events, pid)))


class _EqBoom(str):
    """A `str` SUBCLASS address whose `__eq__` raises (#12907 cycle 2, P3)."""

    __hash__ = str.__hash__

    def __eq__(self, other):  # the raise IS the fixture
        raise RuntimeError("eq-boom")


class _LieEq(str):
    """A `str` subclass that answers `True` to every content question."""

    __hash__ = str.__hash__

    def __eq__(self, other):  # the lie IS the fixture
        return True


def test_a_lying_eq_cannot_disarm_the_entailed_exemption(env, monkeypatch):
    """#12907 cycle 3, P2: the ENTAILED test must compare CONTENT, not objects.

    `_status_entailed` is what DISARMS the terminal guard, and that guard is the
    only instrument for a stored word `_ALLOWED_TRANSITIONS` has no key for —
    the declared-transition loop DEFERS on such a word, so a wrongly-entailed
    call reaches the write with nothing left to refuse it. A `str` subclass
    answers `__eq__` for whichever side of the tuple element it occupies, so
    BOTH directions are measured here.

    `deprecated` (not `retracted`) is the vehicle: it is terminal to the guard
    and absent from the map, which is exactly the pair that leaves the guard as
    the only refuser (that absence is asserted by
    `test_deprecated_is_terminal_even_though_the_map_has_no_key` above, and
    `test_a_map_less_stored_status_still_defers` pins the loop's deferral).

    Mutant that reddens: (M2) compare the stored tuple / the rendered status
    object directly (`_pt_statuses == (_lifecycle_rendered["status"],)`) and
    BOTH phases (a) and (b) are ACCEPTED, storing `live` on a dead row (a
    resurrection) with the guard never asked. (M3) destroy the exemption
    outright (`_status_entailed = False`) and phase (c) is REFUSED — that is
    what makes phase (c) discriminating, and it is only true because phase (c)
    sits on a TERMINAL row (#12907 cycle 4, P3: on a live row the same phase
    stayed GREEN under M3, because a live row's guard passes whether it was
    asked or skipped).
    """
    sdk, events = env

    # ── (a) the CALLER's object — reachable through `props`, no engine seam ──
    pid = _live(sdk, "a claim held dead by a map-less word")
    _force_status(sdk, pid, "deprecated")
    before = _row(sdk, pid)
    assert before["status"] == "deprecated"

    with pytest.raises(ValueError, match="on a dead claim"):
        sdk.update_knowledge(pid, status=_LieEq("live"))
    assert _row(sdk, pid) == before, (
        "a lying `__eq__` in the caller's own value must not disarm the "
        "terminal guard: " + repr(_row(sdk, pid)))

    # ── (b) the STORED object — the read seam, same guard ──
    pid2 = _live(sdk, "a claim whose stored word arrives as a lying subclass")
    _force_status(sdk, pid2, "deprecated")
    before2 = _row(sdk, pid2)

    graph = type(sdk._get_proj().g)
    real = graph.query.__get__(sdk._get_proj().g, graph)

    class _RowSet:
        result_set = (((_LieEq("deprecated"),), 0),)

    def query(_graph, cypher, *args, **kwargs):
        if "collect(DISTINCT n.status)" in cypher:
            return _RowSet()
        return real(cypher, *args, **kwargs)

    monkeypatch.setattr(graph, "query", query)
    with pytest.raises(ValueError, match="on a dead claim"):
        sdk.update_knowledge(pid2, status="live")
    assert _row(sdk, pid2) == before2, (
        "a stored lying `__eq__` must not disarm the terminal guard: "
        + repr(_row(sdk, pid2)))

    # ── (c) the LICENSED shape the exemption exists for, still entailed ──
    # A read-modify-write echo is what the exemption was introduced for (#3305):
    # a subclass ECHO of the row's own word reduces to that content, so it stays
    # entailed and still lands. Without this the fix could "pass" by refusing
    # everything — fail-closed is cheaper than fail-open, but it is not free.
    # ⛔ The row MUST be TERMINAL. On a LIVE row this phase passes under an
    # over-fix that destroys the exemption outright (`_status_entailed = False`),
    # because a live row's guard passes whether it was asked or skipped — so a
    # live-row phase asserted nothing (#12907 cycle 4, P3, measured).
    monkeypatch.undo()
    pid3 = _live(sdk, "a claim that echoes its own word through a subclass")
    sdk.retract_point(pid3)
    sdk.update_knowledge(pid3, status=_LieEq("retracted"), content="echoed, rewritten")
    assert _row(sdk, pid3)["status"] == "retracted", (
        "the entailed echo must land and must not disturb the terminal word: "
        + repr(_row(sdk, pid3)))
    assert sdk.get_point(pid3)["content"] == "echoed, rewritten", (
        "the entailed path must still write the content it carried")

    for _pid in (pid, pid2):
        assert "restatus" not in json.dumps(_mutations(events, _pid)), (
            "a refused write must journal nothing: "
            + repr(_mutations(events, _pid)))


def test_a_hostile_eq_address_cannot_replace_a_transition_refusal(env):
    """#12907 cycle 2, P3: the address compare must not reach the caller.

    Python tries the RIGHT operand's `__eq__` first when it is a proper
    subclass of the left's type, so the pre-fix `_named_id == id_val` let a
    caller's raising `__eq__` replace this refusal with `RuntimeError:
    eq-boom` (row unchanged either way). The comparison is now content on the
    LEFT behind an `issubclass` gate, so the caller's `__eq__` is unreachable.

    Mutant that reddens: restore `_named_id == id_val` and this raises
    `RuntimeError` instead of the promised `ValueError`.
    """
    sdk, events = env
    pid = _live(sdk, "a claim addressed through a hostile __eq__")
    before = _row(sdk, pid)

    with pytest.raises(ValueError, match="refused the status transition"):
        sdk.update_knowledge(_EqBoom(pid), status="draft")

    assert _row(sdk, pid) == before, repr(_row(sdk, pid))
    assert "restatus" not in json.dumps(_mutations(events, pid)), (
        "a refused write must journal nothing: "
        + repr(_mutations(events, pid)))


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
        sdk.update_knowledge(old, **{prop: value})

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
        sdk.update_knowledge(pid, validTo="2030-01-01T00:00:00Z")
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
        sdk.update_knowledge(old, status="live")
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

    sdk.update_knowledge(old, content="a corrected reading of the dead claim")
    assert sdk.get_point(old)["content"] == \
        "a corrected reading of the dead claim"
    assert _row(sdk, old)["status"] == "superseded", (
        "the revision must not disturb the terminal status")


def test_a_live_point_keeps_its_window_and_its_nulls(env):
    """No over-refusal on the healthy population: a live claim's window is
    still writable, including a NULL end (the documented clear)."""
    sdk, _ = env
    pid = _live(sdk)
    sdk.update_knowledge(pid, validFrom="2026-01-01T00:00:00Z",
                      validFromPrecision="day")
    assert _row(sdk, pid)["validFrom"] == "2026-01-01T00:00:00Z"
    sdk.update_knowledge(pid, validTo=None)
    assert _row(sdk, pid)["validTo"] is None


def test_a_draft_point_still_promotes_through_this_door(env):
    """``draft → live`` is a DECLARED transition (ONTOLOGY §5) and the ruling
    that ``draft`` is an explicit, honoured mode depends on it working."""
    sdk, _ = env
    pid = sdk.create_point("statement", "d", dedup=False, status="draft")["id"]
    sdk.update_knowledge(pid, status="live")
    assert _row(sdk, pid)["status"] == "live"


# ══════════════════════════════════════════════════════════════════════
# The declared map is consulted for the non-terminal transition question
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("target", ["draft"])
def test_an_undeclared_transition_from_live_is_refused(env, target):
    """``_ALLOWED_TRANSITIONS['live']`` declares exactly deprecated/superseded.

    ⛔ #11824 S0 CHANGED WHICH WORDS REACH THIS DOOR, so the pin moved with it
    rather than being retired. Three params used to sit here and each is now a
    DIFFERENT refusal, spelled out below so the delta is on the record:

    * ``retracted`` / ``archived`` are declared SYNONYMS of ``deprecated``, so
      they are normalised by ``lifecycle_vocabulary`` BEFORE this loop runs and
      they are now LEGAL transitions out of ``live`` (stored as ``deprecated``);
    * ``outdated`` is refused by the VOCABULARY door, which runs first — its
      message names the choice between two words, not a transition, and it is
      pinned by ``test_11926_derived_invalidation.py`` and the `outdated` rows in
      ``test_11825_event_terminal_status_refused.py``;
    * ``draft`` is the remaining word that is a real, undeclared BACKWARD
      transition from ``live``, so it is what exercises this arm now.

    ``superseded`` is declared and stays legal — ⛔ and since #12592 a declared
    ``superseded`` must ALSO name its successor, because ONTOLOGY §4.3 defines
    the word as *"replaced: it names the successor"*. The two halves are pinned
    here: the bare word is refused, the pair is accepted.
    """
    sdk, _ = env
    pid = _live(sdk)
    declared = _ALLOWED_TRANSITIONS["live"]

    if target in declared:
        if target == "superseded":
            # #12592: declared is not sufficient — the word needs its partner.
            with pytest.raises(ValueError, match="no successor"):
                sdk.update_knowledge(pid, status=target)
            assert _row(sdk, pid)["status"] == "live"
            sdk.update_knowledge(pid, status=target, supersededBy="the successor")
            assert _row(sdk, pid)["status"] == target
            return
        sdk.update_knowledge(pid, status=target)
        assert _row(sdk, pid)["status"] == target
        return

    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(pid, status=target)
    text = str(exc.value)
    assert target in text and "transition" in text.lower(), (
        f"the refusal must name the target status and the transition: {text!r}")
    assert _row(sdk, pid)["status"] == "live"

    # …the two halves the params above retired, asserted as the model now
    # states them: a synonym LANDS (as the canonical word)…
    for _synonym, _canonical in (("retracted", "deprecated"),
                                 ("archived", "deprecated")):
        _pid = _live(sdk, f"live -> {_synonym}")
        sdk.update_knowledge(_pid, status=_synonym)
        assert _row(sdk, _pid)["status"] == _canonical, (
            f"{_synonym!r} is a declared synonym of {_canonical!r} since #11824 "
            f"S0, so a live Point must accept it: {_row(sdk, _pid)!r}")
    # …and `outdated` is refused EARLIER, by the vocabulary door, because it
    # names a choice between two lifecycle words and never picks one.
    _pid = _live(sdk, "live -> outdated")
    with pytest.raises(ValueError, match=r"was refused"):
        sdk.update_knowledge(_pid, status="outdated")
    assert _row(sdk, _pid)["status"] == "live"


def test_restating_the_same_status_is_a_no_op_not_a_refusal(env):
    """Identical status is not a transition — idempotent re-writes stay legal."""
    sdk, _ = env
    pid = _live(sdk)
    sdk.update_knowledge(pid, status="live", content="same status, new content")
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
    sdk.update_knowledge(_control, status="retracted")
    assert _mutations(events, _control), (
        "the journal reader saw no record for a re-status that MUST be "
        "journaled — the atomicity assertion below would be vacuous")
    before = _row(sdk, old)
    before_recs = _mutations(events, old)

    with pytest.raises(ValueError):
        sdk.update_knowledge(old, status="live")
    with pytest.raises(ValueError):
        sdk.update_knowledge(old, validTo="2030-01-01T00:00:00Z")

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
    # ⛔ #11824 S0: `archived` is a declared SYNONYM of `deprecated`, so the
    # write lands and the store holds the canonical word — the whole point of
    # moving the pack words onto the position axis is that ONE four-word
    # vocabulary is minted for every knowledge kind.
    sdk.update_knowledge(obj, status="archived")
    assert sdk.get_entity(obj)["status"] == "deprecated"

    subj = sdk.create_subject("team-x")["id"]
    # ⛔ #12592: the bare word is refused on every label now (§4.3), so the
    # non-Point control names its successor. The point of THIS test is unchanged:
    # the door serves the other labels rather than raising `No point`.
    sdk.update_knowledge(subj, status="superseded", supersededBy="team-y")
    assert sdk.get_entity(subj)["status"] == "superseded"


def test_an_absent_id_is_still_the_documented_empty_reply(env):
    """A typo must not become a lifecycle refusal (the #10119 absent contract)."""
    sdk, _ = env
    assert sdk.update_knowledge("no-such-point-9649", status="live") == {}


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
                  {"when": "2031-01-01T00:00:00Z"}):
        with pytest.raises(ValueError, match="terminal"):
            sdk.update_knowledge("evt-review-9649", **props)
    # ⛔ The `status` half of this probe is answered EARLIER now, and that is a
    # deliberate narrowing rather than a lost pin: since #13100 an `:Event`
    # carries no lifecycle at all, so the door refuses ANY `status` on this
    # hybrid at its Event arm, before the address resolution runs. Both refusals
    # leave the dead claim alone — which is what this test exists for — and the
    # resolution half is still pinned by the three temporal props above (plus
    # the `url` arm's own test below). The status-arm claims that used to ride
    # this hybrid live on the `:Source` arm, the door's other secondary key.
    with pytest.raises(ValueError, match=":Event"):
        sdk.update_knowledge("evt-review-9649", status="live")
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
        sdk.update_knowledge("src://review-9649", status="live")
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
        sdk.update_knowledge(pid, status="draft")
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
        sdk.update_knowledge("src://idless-9649", status="live")
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
    provenance = sdk.create_point(
        "statement", "carries provenance", dedup=False,
        status="retracted", eventId="ev-9649-prov")["id"]
    sdk._get_proj().g.query(
        "CREATE (e:Event {eventId:'ev-9649-prov', name:'session'})")

    # ⛔ The probe is a TEMPORAL field, never `status`, since #13100: a `status`
    # write on an `:Event` is refused by that rule before this arm is consulted
    # (the earlier cut of this test said as much for `archived`, which #11825's
    # guard caught first, and #13100 now catches every word). `validTo` is a
    # `_TEMPORAL_RESTAMP_PROPS` member, so it still reaches the address
    # resolution — which is the whole claim under test.
    #
    # The two reads below are what keep the assertion non-vacuous: the PRE-FIX
    # arm (`n:Point {eventId:$id}`, unqualified) DOES match the provenance Point,
    # and the qualified arm does not match anything — so an unqualified
    # resolution would have handed the guard a dead claim the caller never
    # addressed. The refusal of that claim is measured right beside it.
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {eventId:$id}) RETURN count(n)",
        params={"id": "ev-9649-prov"}).result_set[0][0] == 1
    assert sdk._get_proj().g.query(
        "MATCH (n:Event {eventId:$id}) WHERE n:Point RETURN count(n)",
        params={"id": "ev-9649-prov"}).result_set[0][0] == 0
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge(provenance, validTo="2031-01-01T00:00:00Z")

    sdk.update_knowledge("ev-9649-prov", validTo="2031-01-01T00:00:00Z")
    assert sdk._get_proj().g.query(
        "MATCH (e:Event {eventId:'ev-9649-prov'}) RETURN e.validTo",
    ).result_set == [["2031-01-01T00:00:00Z"]], (
        "the Event the caller addressed must take the write")
    # ⛔ `retracted` is STORED as its canonical synonym `deprecated` since
    # #11824 S0 (the write door normalises), so this assertion names the stored
    # spelling; the claim is unchanged — the addressed Event took the write and
    # the provenance Point did not.
    assert sdk.get_entity(provenance)["status"] == "deprecated", (
        "the provenance Point must be untouched")


def test_a_hybrid_point_status_write_is_validated_and_journals_the_POINT(env):
    """Review round 3, P2: the status arm must key on the RESOLVED address.

    The guard learned to resolve `eventId`/`url`, but the vocabulary check, the
    declared transition and the `status`-drop from `props` were still gated on
    `MATCH (n:Point {id:$id})`. On a `:Point:Event` addressed by its `eventId`
    that read found no Point, so `status='archived'` and `status='garbage'` both
    LANDED and the key rode into the `:Event` arm, which journalled `restatus`
    against `label='Event'` — a record no fold can apply (a live write that makes
    `rebuild_all` refuse). Measured at `a35a3973b7`.

    ⛔ The fixture is a `:Point:Source` addressed by its `url`, not a
    `:Point:Event` addressed by its `eventId`: since #13100 an `:Event` carries no
    lifecycle at all, so the door refuses ANY `status` on that hybrid before this
    arm is reached (`test_a_point_addressed_by_its_event_id_is_guarded_too` pins
    that refusals). `url` is the door's OTHER secondary key, resolved by the same
    `_lifecycle_ids` read, so every claim here is unchanged: the status arm keys
    on the RESOLVED address, validates the word, asks the DECLARED transition,
    drops the key from `props`, and journals against the POINT.

    ⛔ TWO NODES since #11825 (#9649 merge review): an `:Event` never carries a
    TERMINAL status (that guard refuses one first, with its own message), and
    every DECLARED transition out of a non-terminal Point status is terminal —
    so the transition refusal reachable on a hybrid is an UNDECLARED
    non-terminal one (`live -> draft`), while the journal half is the prop-lane
    promotion `draft -> live`.
    """
    sdk, events = env
    pid = _live(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Source, n.url=$u",
        params={"id": pid, "u": "src://hybrid-9649"})

    with pytest.raises(ValueError, match="transition"):
        sdk.update_knowledge("src://hybrid-9649", status="draft")
    with pytest.raises(ValueError, match=r"was refused"):
        sdk.update_knowledge("src://hybrid-9649", status="garbage")
    assert _row(sdk, pid)["status"] == "live", (
        "a refused hybrid status write must not touch the row")

    draft_pid = sdk.create_point(
        "statement", "draft hybrid", dedup=False, status="draft")["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Source, n.url=$u",
        params={"id": draft_pid, "u": "src://hybrid-draft-9649"})
    sdk.update_knowledge("src://hybrid-draft-9649", status="live")
    assert _row(sdk, draft_pid)["status"] == "live"
    recs = _mutations(events, draft_pid)
    assert recs, "the declared transition must be journaled against the POINT id"
    assert all(r.get("label") == "Point" for r in recs), (
        f"a restatus record must name the label the node replays as: {recs}")
    assert _mutations(events, "src://hybrid-draft-9649") == [], (
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

    sdk.update_knowledge("X-9649", status="retired")
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

    with pytest.raises(ValueError, match=r"was refused"):
        sdk.update_knowledge(pid, status="garbage")
    assert _row(sdk, pid)["status"] is None, (
        "a refused vocabulary must not store anything")

    sdk.update_knowledge(pid, status="live")
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

    sdk.update_knowledge(pid, status=stored, content="read-modify-write revision")
    assert sdk.get_point(pid)["content"] == "read-modify-write revision"
    assert _row(sdk, pid)["status"] == "retracted", (
        "the revision must not disturb the terminal status")

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge(pid, status="live", content="a resurrection attempt")


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
        sdk.update_knowledge(t, status="retracted", validTo="2030-01-01T00:00:00Z")
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
        sdk.update_knowledge(f2, status="retracted")

    # (c) EVERY bound row must hold it — "any" resurrects a terminal sibling.
    f = _live(sdk, "fan terminal")
    sdk.retract_point(f)
    sdk.create_point("statement", "fan live twin", dedup=False,
                     status="live", id=f)
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge(f, status="retracted")
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

    ⛔ The fixture is a `:Point:Source` addressed by its `url` since #13100: an
    `:Event` hybrid refuses EVERY `status`, so this arm (which is a status-only
    claim) would be unreachable through it. The `url` arm matches just as
    narrowly as the `eventId` arm while the write binds the whole id row set,
    which is the shape this test needs — the arm's NAME is incidental, the
    narrower-than-the-write relation is not.

    ⛔ The TWIN is what the assertion is about, so the contrast is pinned beside
    it: a hybrid with no unaddressed sibling echoes its own status through the
    exemption (the #3305 read-modify-write capability) — the refusal above is
    therefore caused by the twin, not by the `status` key merely being present.
    A narrowed read would see `{live}` alone, call it entailed, skip the guard
    and resurrect the twin.
    """
    sdk, _ = env
    pid = _live(sdk, "hybrid with twin")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Source, n.url=$u",
        params={"id": pid, "u": "src://twin-9649"})
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:$id, content:'twin', status:'deprecated'})",
        params={"id": pid})
    before = sorted(r[0] for r in _fan(sdk, pid))
    assert before == ["deprecated", "live"], before

    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge("src://twin-9649", status="live")
    assert sorted(r[0] for r in _fan(sdk, pid)) == before, (
        "the unaddressed terminal twin must not be resurrected")

    # The contrast: same hybrid, NO twin → every bound row already holds `live`,
    # which is the entailed echo the exemption exists for.
    solo = _live(sdk, "hybrid without twin")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Source, n.url=$u",
        params={"id": solo, "u": "src://solo-9649"})
    sdk.update_knowledge("src://solo-9649", status="live")
    assert _row(sdk, solo)["status"] == "live"


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
        sdk.update_knowledge(0, status="live")
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge(0, validTo="2031-01-01T00:00:00Z")
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
    # ⛔ `retracted` is a declared SYNONYM of `deprecated` since #11824 S0, so
    # the write LANDS and the store holds the canonical word (a one-way read
    # delta: nothing may be minted as `retracted`).
    sdk.update_knowledge(live_op, status="retracted")
    assert _row(sdk, live_op)["status"] == "deprecated"

    dead_op = sdk.create_operator("NAND", a, [b])["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status='superseded'",
        params={"id": dead_op})
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge(dead_op, status="live")
    with pytest.raises(ValueError, match="terminal"):
        sdk.update_knowledge(dead_op, validTo="2031-01-01T00:00:00Z")
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

    sdk.update_knowledge(pid, validTo="2030-01-01T00:00:00Z")
    assert [s[0] for s in seen] == [pid], seen
    assert seen[0][1].get("missing_ok") is True, seen[0][1]
    assert seen[0][1].get("allow_operator") is True, seen[0][1]

    seen.clear()
    assert sdk.update_knowledge("no-such-point-9649", status="live") == {}
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

    sdk.update_knowledge(pid, status="live")
    assert _row(sdk, pid)["status"] == "live"
    # (`retracted` is a synonym of `deprecated` since #11824 S0 — it lands, and
    # the store keeps the canonical word.)
    sdk.update_knowledge(pid, status="retracted")
    assert _row(sdk, pid)["status"] == "deprecated"


def test_two_address_arms_resolving_one_id_ask_the_guard_once(env, monkeypatch):
    """Review round 9, P2: the resolved-id list must be DE-DUPLICATED.

    A `:Point:Event` whose `id` and `eventId` are the same string is resolved by
    two arms, so an un-deduplicated list asked the lifecycle guard TWICE for one
    intent and journalled the SAME `restatus` twice — the class `delete_point`'s
    own comment names ("a second destruction record for one intent"). Replay is
    idempotent, so only a count assertion catches it.

    ⛔ The count is taken on the GUARD LOOP since #13100, not on the journal:
    this hybrid can no longer take ANY `status` (that rule refuses it before the
    arm that journals — the `draft -> live` promotion this test used was legal
    only under #11825's interim boundary). A TEMPORAL write still reaches both
    the resolution and the loop, and `_lifecycle_ids` — the very list the dedupe
    produces — is what the loop iterates. Drop the `dict.fromkeys` and the guard
    runs twice.
    """
    sdk, _ = env
    pid = sdk.create_point("statement", "one node, two arms", dedup=False)["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Event, n.eventId=$id",
        params={"id": pid})

    seen: list = []
    real = sdk._assert_lifecycle_guard

    def spy(point_id, **kw):
        seen.append(point_id)
        return real(point_id, **kw)

    monkeypatch.setattr(sdk, "_assert_lifecycle_guard", spy)
    sdk.update_knowledge(pid, validTo="2031-01-01T00:00:00Z")
    assert seen == [pid], (
        f"two address arms resolving one id must ask the guard once: {seen}")
    assert _row(sdk, pid)["validTo"] == "2031-01-01T00:00:00Z"


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
    sdk.update_knowledge(pid, status="retracted")
    restatus = [r for r in _mutations(events, pid) if r.get("op") == "restatus"]
    assert restatus == [], (
        f"a write that matched no row must not journal a restatus: {restatus}")


# ══════════════════════════════════════════════════════════════════════
# The residual this change DECLARES (#12403) — pinned, not just prose
# ══════════════════════════════════════════════════════════════════════

def test_a_declared_terminalizing_transition_carries_none_of_the_verbs_effects(env):
    """#9649 review F1 / #12403: the door PERMITS a declared terminal target, and
    performs none of the effects the verb that owns it performs.

    ⛔ ``#12592`` CLOSED THE FIRST HALF OF THIS RESIDUAL FOR ``superseded`` and
    this pin INVERTS with it, exactly as its own docstring instructed: the bare
    ``status='superseded'`` is now REFUSED, because ONTOLOGY §4.3 defines the word
    as *"replaced: it names the successor"*, and the refusal names
    ``supersede_point`` — the verb that carries the effects. What REMAINS of
    #12403 is the second half, pinned below on the shape that is still accepted:
    a ``superseded`` write that DOES name a successor through this door still
    writes no ``CORRECTS`` edge, no ``PointSuperseded`` record, and closes no
    window. ``retracted`` (the other declared target) is untouched by #12592 and
    remains a full #12403 residual — it keeps the old assertions at the end.
    """
    sdk, events = env
    pid = _live(sdk, "claim that will be retired through the generic door")
    before = _row(sdk, pid)
    assert before["validTo"] is None, before

    # 1. the bare word is REFUSED now, and names the verb that does the work.
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(pid, status="superseded")
    # The named verb is the one that EXISTS at this head: `supersede` was
    # absorbed by `supersede_knowledge` (#4282 row 23), so a message that still
    # said `supersede(` would prescribe an unperformable repair (AX rule 2).
    assert "supersede_knowledge(" in str(exc.value), str(exc.value)
    assert _row(sdk, pid) == before, "the refused write moved the row"

    # 2. the residual that REMAINS: a successor-naming write is accepted and
    #    still mints no edge and no claim-transition record.
    sdk.update_knowledge(pid, status="superseded", supersededBy="the named successor")
    after = _row(sdk, pid)
    assert after["status"] == "superseded", after
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
    assert sdk._get_proj().g.query(
        "MATCH (:Point {id:$id})<-[r:CORRECTS]-() RETURN count(r)",
        params={"id": pid}).result_set[0][0] == 0, (
        "#12403: a successor-named `superseded` through this door mints no "
        "CORRECTS edge — the verb does, and that is the residual")
    assert sdk.query("claim that will be retired") in ([], None), (
        "the dead claim must leave belief — that is WHY a missing successor "
        "matters (#2498)")
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
        sdk.update_knowledge(old, validTo=stored["validTo"],
                          content="a re-sent, corrected reading")
    text = str(exc.value)
    assert "WITHOUT the temporal fields" in text, (
        f"the repair must name the route a read-modify-write can take: {text!r}")
    assert "content" in text.lower(), (
        f"the message must say what IS still accepted: {text!r}")

    # ...and the route it names really works (non-vacuity: this half must pass).
    sdk.update_knowledge(old, content="a re-sent, corrected reading")
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
        sdk.update_knowledge("src://round12-9649", status="draft")
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
        sdk.update_knowledge(old, **{_KHash("status"): "live"})

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
        sdk.update_knowledge(old, **{_KStr("q"): "live"})

    assert _row(sdk, old) == before
    assert _mutations(env[1], old) == [], "nothing may be journaled for a refusal"


def test_a_hash_overriding_window_key_cannot_rewrite_a_closed_window(env):
    """Probe 2's axis, in the same key class: `validTo` must be guarded too."""
    sdk, _ = env
    old, _new = _superseded(sdk)
    before = _row(sdk, old)
    assert before["validTo"], "the fixture must be a CLOSED window"

    with pytest.raises(ValueError, match="validTo"):
        sdk.update_knowledge(old, **{_KHash("validTo"): "2030-01-01T00:00:00Z"})

    assert _row(sdk, old) == before, "the closed window must survive the refusal"


def test_a_hash_overriding_status_key_cannot_store_an_undeclared_word(env):
    """The #3311 vocabulary half: `status='garbage'` was STORED, not refused."""
    sdk, _ = env
    pid = _live(sdk, "a live claim")

    with pytest.raises(ValueError, match=r"was refused"):
        sdk.update_knowledge(pid, **{_KHash("status"): "garbage"})

    assert _row(sdk, pid)["status"] == "live", (
        "an undeclared status word must never reach the node, whichever name it "
        "was filed under")


def test_a_plain_status_key_still_gets_the_documented_answer(env):
    """Non-vacuity control: the ordinary spelling must behave, not be refused.

    Without this, every pin above would pass on a guard that refused every write.
    ⛔ The control is ``retracted`` — a DECLARED transition from ``live`` that
    needs no partner. ``superseded`` stopped being usable as this control in
    #12592: ONTOLOGY §4.3 makes the bare word a refusal, and the declared-but-
    partnerless shape is pinned in
    ``test_an_undeclared_transition_from_live_is_refused``. Since #11824 S0 the
    control also asserts the SYNONYM half: ``retracted`` is a spelling of
    ``deprecated``, so the row lands as ``deprecated``.
    """
    sdk, _ = env
    pid = _live(sdk, "a live claim")

    sdk.update_knowledge(pid, status="retracted")
    assert _row(sdk, pid)["status"] == "deprecated", (
        "a DECLARED transition must still land — the arm is not simply closed "
        "— and a declared synonym is stored as the word it means")

    # ...and the plain spelling is then refused on the SAME guarantee the exotic
    # keys were refused on, so the family is one rule and not two.
    # Two instruments can answer this one and BOTH are correct: the dead-claim
    # guard (the row is `deprecated`, which is terminal) and, if that guard were
    # disarmed, the declared-transition loop. The point of the control is that
    # the write is REFUSED, so the assertion names both texts.
    with pytest.raises(ValueError,
                       match=r"dead claim|refused the status transition"):
        sdk.update_knowledge(pid, status="live")


def test_a_rendered_status_key_is_dropped_before_the_sibling_arm(env):
    """Review round 13b (P2): the DROP is load-bearing, not just the refusal.

    `keys_rendering_as` removes the key whose RENDERER names `status`, so the
    label loop below cannot re-file it under its own label. Measured with the
    drop reverted: a `:Point:Object` written through the content-rendering key
    journals TWO records — the Point's `restatus` and then a
    `revise`-under-`Object` for a property the caller never named — a record no
    fold can apply, which is the #3311/#9649 class this whole guard exists to
    close. Nothing pinned it before: reverting the hunk left 390 tests green.
    """
    sdk, events = env
    pid = _live(sdk, "a hybrid written through a rendering key")
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})

    # ⛔ `retracted`, not `superseded` (#12592): the word now needs its partner,
    # and adding one would put a SECOND field in this call — the very thing this
    # pin's "one intent journals ONE record" assertion measures. The DROP is what
    # is under test, and any status exercises it.
    sdk.update_knowledge(pid, **{_KStr("q"): "retracted"})

    recs = _mutations(events, pid)
    assert len(recs) == 1, (
        f"one intent must journal ONE record, and the key must not ride into "
        f"the sibling arm: {recs}")
    assert recs[0].get("label") == "Point", (
        f"the record must name the label the node replays as: {recs}")
    assert recs[0].get("op") == "restatus", f"unexpected record: {recs}"


def test_a_rendered_status_key_is_validated_against_the_SUBJECT_vocabulary(env):
    """Review round 13b (P2): the Subject arm had the same presence hole.

    The commit under review fixed this too but said so nowhere and pinned
    nothing: with the fix reverted, `update_entity(<:Subject>,
    **{KHash("status"): "dead"})` is ACCEPTED and stores a word that is not in
    `SUBJECT_STATUS_VALUES` — the #1423 shape, reached through the key class this
    round closed for the Point arm. Measured: head raises `Invalid status 'dead'
    for a :Subject`, revert stores it.
    """
    sdk, _ = env
    sid = sdk.create_entity("subject", "Rendered Key Co")["node"]["id"]

    with pytest.raises(ValueError, match=r"was refused"):
        sdk.update_knowledge(sid, **{_KHash("status"): "dead"})

    row = sdk._get_proj().g.query(
        "MATCH (n:Subject {id:$id}) RETURN n.status",
        params={"id": sid}).result_set[0][0]
    assert row != "dead", f"an undeclared Subject status must never land: {row!r}"


def test_a_rendered_ECHO_of_the_stored_status_is_still_entailed(env):
    """Review round 13b (P2): the exemption must see the rendering key too.

    `_status_entailed` decides from the same rendering-aware mapping, so a
    read-modify-write that echoed the terminal word back through the exotic key
    is still recognised as an echo and ACCEPTED, rather than refused as a
    transition. Measured with the exemption reverted onto `props`: the identical
    call is refused (fail-closed, so this is the mildest of the three — but it
    turns a declared-preserved capability, #3305, into a hard error).
    """
    sdk, events = env
    old, _new = _superseded(sdk)
    before = _row(sdk, old)
    records_before = len(_mutations(events, old))

    sdk.update_knowledge(old, **{_KHash("status"): "superseded"})

    assert _row(sdk, old) == before, (
        "an entailed echo must leave the row exactly as it was")
    assert len(_mutations(events, old)) >= records_before, (
        "the echo is a no-op on the row, not a refusal")


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
        sdk.update_knowledge("src://idless-9649-msg", status="retracted")
    text = str(exc.value)
    assert "update_point(" not in text, (
        f"the refusal must not prescribe an id-keyed call for a node with no "
        f"id (it resolves to nothing): {text!r}")
    assert "create_point(" in text, (
        f"the re-create route is the one that works and must be named: {text!r}")
    assert "_sanitize_props" in text, (
        f"the message must say WHY no SDK door can stamp the id: {text!r}")


# ══════════════════════════════════════════════════════════════════════
# #11824 S0 review (P2) — an echo of a LEGACY terminal word is entailed
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("retire", ["retracted", "archived"])
def test_an_echo_of_a_legacy_terminal_word_is_an_entailed_write(env, retire):
    """The read-modify-write shape a generic tenant PUT sends over a dead row.

    #11824 S0 put the lifecycle door on this arm, and the door NORMALISES a
    declared synonym (`retracted` / `archived` -> `deprecated`) while the row
    still HOLDS the legacy word — `retract_point`'s CAS writes
    `status='retracted'` (a stated residual of this slice), and `archived` is a
    pre-#11824 stored word (no backfill, #11824 §1).

    So a caller that fetches the point and PUTs it back with its own status
    arrived at the declared-transition loop as `retracted` -> `deprecated`: the
    loop called that a TRANSITION, `_ALLOWED_TRANSITIONS['retracted']` is empty
    (terminal), and the door refused a write that changes nothing. The
    entailment test ABOVE the loop had already (correctly) said "not a
    transition", so the SAME call was judged both ways — and the terminal guard
    had been disarmed on the strength of that first judgement.

    Mutant that reddens: compare spellings again (`str.__eq__(_stored, _status)`
    and `_ALLOWED_TRANSITIONS.get(_stored)`) instead of reading both sides
    through `_lifecycle_meaning`; both params then raise
    `update_entity refused the status transition … is terminal`.

    ⚠️ The echo rewrites NOTHING: the row keeps the legacy spelling it holds
    (`retracted` / `archived`), because an entailed call mints no word — the
    door's canonical spelling governs what may be MINTED, not what an echo of an
    existing row may rewrite. The row must stay DEAD either way, which this
    asserts alongside the acceptance.
    """
    sdk, _ = env
    if retire == "retracted":
        pid = _live(sdk, "a claim retired by the shipped verb")
        sdk.retract_point(pid)              # the CAS writes 'retracted'
    else:
        pid = _live(sdk, "a claim a pre-fix writer archived")
        _force_status(sdk, pid, "archived")
    assert _row(sdk, pid)["status"] == retire

    sdk.update_knowledge(pid, status=retire, content="echoed, rewritten")

    after = _row(sdk, pid)
    assert after["status"] == retire, (
        "an entailed echo must not rewrite the stored word: " + repr(after))
    assert sdk.get_point(pid)["content"] == "echoed, rewritten", (
        "the entailed path must still write the content it carried")

    # …and the point is still DEAD: the echo is not a resurrection.
    with pytest.raises(ValueError, match=r"terminal|dead"):
        sdk.update_knowledge(pid, status="live")
    assert _row(sdk, pid)["status"] == retire
