"""#13035 — a goal's two ending words are the lifecycle's endings, on BOTH fields.

THE DEFECT, measured on the pre-change tree (main at ``6c160da471``):
``sdk.py`` validated ``goalState`` against its vocabulary and **never against
``status``**, while ``derived_invalidation``'s retirement arm retires a goal's
derived caches on ``goalState in {superseded, abandoned}``. So a goal written
``goalState='abandoned'`` with ``status='live'`` was **retired to the
invalidation path and current to every reader** — a word dead to one surface and
alive to another, which is the one failure the two-axis model exists to remove
(design: ``docs/plans/2026-11-15-state-lifecycle-and-pipelines.md`` §5).

WHAT THIS FILE PINS (the behaviour, not a test count):

  * the pair cannot be written apart, in EITHER direction, and the refusal names
    both repairs the way ``docs/ax/ax-principles.md`` rule 2 requires;
  * ``goalState='superseded'`` additionally NAMES its successor — the
    ``supersededBy`` carrier on an Object goal, the ``CORRECTS`` edge on a Point
    goal;
  * a goal retired through the ordinary verb ends on both fields in ONE write,
    and the invalidation path and the reading rule then AGREE about it;
  * ``guideline`` is refused, and no code path still expects it;
  * the judgement cache keeps writing only ``met``/``unmet``.

⚠️ WHAT IS DELIBERATELY NOT HERE — the Point goal's *alignment* path. A Point's
``goalState`` write is not journalled (``derived_invalidation``'s
``_GOAL_STATE_CANDIDATES`` block and its ``notDurable`` counter record that —
#4094's class), so teaching ``supersede_point`` to stamp ``goalState`` would mint
a live-only word a ``rebuild_all`` silently drops. A Point goal therefore still
reaches ``status='superseded'`` through ``supersede_point`` with its achievement
word untouched (the BENIGN direction: both surfaces say the goal is over, because
every reader asks ``status``), and the paired write above it is only reachable
once the point prop write is journalled. That residual is stated on #4094, not
papered over here.
"""
from __future__ import annotations

import ast
import contextlib
from pathlib import Path

import pytest

from tortoise import live
from tortoise.derived_invalidation import (
    DERIVED_GOAL_STATES,
    RETIRED_GOAL_STATES,
    invalidate_stale,
    verdicts,
)
from tortoise.goal_judgement import GOAL_STATE_CACHE, Target, judge_and_record
from tortoise.ids import ulid
from tortoise.sdk import (
    GOAL_ENDING_STATES,
    GOAL_LIVE_STATES,
    GOAL_STATE_VALUES,
    _rebind_guarded_words,
)

REPO = Path(__file__).resolve().parent.parent

#: ⛔ The word the STORE keeps for an ended row, after #11824 S0.
#:
#: S0 canonicalises the two synonyms ON WRITE: a door accepts `archived` or
#: `retracted` (and `deprecated` itself), and the node is stored as `deprecated`
#: — its own refusal message states the mapping ("`archived` / `retracted`, which
#: are stored as `deprecated`", ONTOLOGY §3). Every row below that reads a stored
#: word back is asserting what the STORE KEEPS, so it asserts this; the synonym a
#: caller may TYPE is a property of the door and is asserted at the doors.
_CANONICAL_ENDED = "deprecated"


@pytest.fixture
def sdk(sdk_factory):
    s = sdk_factory()
    yield s
    s.close()


def _goal(sdk, *, tag="g", **props):
    props.setdefault("goalState", "active")
    return sdk.create_object(f"CI is fast [{tag}]", objectKind="goal", **props)


# ── 1. ended on one field, left running on the other: REFUSED ──────────────

def test_an_ending_on_goalState_alone_is_refused_and_names_both_repairs(sdk):
    """The measured defect, one write. The refusal carries all three parts the
    AX rule requires: that it refused, what exactly failed (naming the surface
    and the two readers that disagree), and both ways to fix it."""
    goal = _goal(sdk)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="abandoned")
    msg = str(exc.value)
    assert "REFUSED" in msg
    # ...what failed: the two surfaces that used to disagree about this goal
    assert "derived_invalidation" in msg and "CURRENT" in msg
    # ...and BOTH repairs, each with the value to pass
    # #13065 review finding 7: repair 1 names a word the POINT doors also accept
    # ('deprecated' is an OBJECT word, so naming it first made the first-named
    # repair unperformable on `create_point`/`update_point` — AX rule 2)
    assert "status='archived'" in msg              # add the ending to `status`
    assert "goalState=None" in msg                 # or drop it from `goalState`
    assert "'met'" in msg or "'active'" in msg     # ...naming the live words
    # nothing landed: the refusal is before the first write
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "active"
    assert not live.is_terminal_status(node.get("status"), bool(node.get("outdated")))


def test_superseded_requires_the_matching_status_and_a_named_successor(sdk):
    """`superseded` is the ONE ending that keeps its spelling on both axes, and
    it obliges a successor (`supersededBy` on an Object goal; the `CORRECTS` edge
    on a Point goal). Design §5, "Same successor obligation"."""
    goal = _goal(sdk)
    # (a) the word without the status: refused, naming the status it spells
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="superseded")
    assert "status='superseded'" in str(exc.value)
    # (b) status + goalState, but NO successor: refused for that reason alone
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="superseded",
                             status="superseded")
    msg = str(exc.value)
    assert "REFUSED" in msg and "successor" in msg
    assert "supersededBy" in msg and "CORRECTS" in msg   # both carriers named
    assert "goalState='abandoned'" in msg                # ...and the other repair
    assert sdk.get_entity(goal["id"])["goalState"] == "active"
    # (c) the coherent triple is accepted, in ONE write
    sdk.update_knowledge(goal["id"], goalState="superseded",
                         status="superseded", supersededBy="goal-v2")
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "superseded"
    assert node["status"] == "superseded"
    assert node["supersededBy"] == "goal-v2"


def test_a_STATUS_ending_over_a_live_goal_state_is_refused_too(sdk):
    """The reverse direction: a goal may not be ended on `status` and left
    running on the achievement axis either."""
    goal = _goal(sdk, goalState="met")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], status="deprecated")
    msg = str(exc.value)
    assert "REFUSED" in msg
    assert "goalState='abandoned'" in msg      # repair 1: add the ending
    assert "status='live'" in msg              # repair 2: drop the ending
    # the goal is untouched by the refusal
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "met"
    assert not live.is_terminal_status(node.get("status"), bool(node.get("outdated")))
    # ...and the paired write is what lands it
    sdk.update_knowledge(goal["id"], status="deprecated", goalState="abandoned")
    node = sdk.get_entity(goal["id"])
    assert node["status"] == "deprecated" and node["goalState"] == "abandoned"


def test_the_two_endings_are_not_interchangeable(sdk):
    """`abandoned`/`deprecated` and `superseded`/`superseded` are two different
    endings — a write that mixes them says the goal ended two ways, and the
    refusal says so instead of silently picking one."""
    goal = _goal(sdk)
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="abandoned",
                             status="superseded")
    msg = str(exc.value)
    assert "not interchangeable" in msg
    assert "status='archived'" in msg
    assert "goalState='abandoned'" in msg


def test_an_EMPTY_successor_name_does_not_discharge_the_obligation(sdk):
    """#13065 review P2 — the write side and the read side of ONE field must apply
    ONE predicate.

    ``_lifecycle_carrier_keys`` deliberately keeps ``\"\"`` as a carrier ("an
    empty string IS one — the record view reads it with ``IS NOT NULL``"), while
    ``_stored_goal_ending_view`` reads ``\"\"`` as NO successor
    (``carrier if carrier not in (None, \"\") else None``). ``_goal_successor_named``
    asked only whether the column was PRESENT, so ``supersededBy=\"\"`` satisfied
    "names no successor" while naming nobody — and a later
    ``goalState='superseded'`` on the same node WAS refused for missing the very
    name the earlier write claimed to provide.
    """
    for spelling in ("supersededBy", "superseded_by"):
        goal = _goal(sdk, tag=f"empty-successor-{spelling}")
        with pytest.raises(ValueError) as exc:
            sdk.update_knowledge(goal["id"], goalState="superseded",
                                 status="superseded", **{spelling: ""})
        msg = str(exc.value)
        assert "REFUSED" in msg and "successor" in msg, msg
        assert "supersededBy" in msg and "CORRECTS" in msg
        # nothing landed: the empty carrier did not discharge the obligation
        node = sdk.get_entity(goal["id"])
        assert node["goalState"] == "active"
        assert not live.is_terminal_status(node.get("status"))

    # ...and a NAME in the same position is accepted, so the fix is "name
    # somebody", never "refuse the carrier"
    goal = _goal(sdk, tag="empty-successor-named")
    sdk.update_knowledge(goal["id"], goalState="superseded",
                         status="superseded", supersededBy="goal-v2")
    assert sdk.get_entity(goal["id"])["supersededBy"] == "goal-v2"


def test_the_create_door_grades_an_EXPLICIT_id_as_the_update_it_can_be(sdk):
    """#13065 review P1 — ``create_point`` with an explicit ``id`` is an ON-MATCH
    create, so it must read the node's STORED half before it writes.

    The door's own statement is a plain ``CREATE``, but a colliding ``id`` mints a
    duplicate whose ``PointAdded`` replay folds back onto the ORIGINAL node
    (``_upsert_point_props`` MERGEs on ``id``), so
    ``create_point(..., id=<goal>, status="archived")`` used to leave
    ``status="archived"`` beside a stored ``goalState="met"``: the pair both
    update doors refuse, in the direction that silences ``derived_invalidation``
    while every other reader serves the goal as ended.
    """
    # a `:Point` goal — the label `_upsert_point_props` MERGEs on, i.e. the node
    # this door's own journal fold can reach
    goal = sdk.create_point("goal", "GOAL - CI is fast [create-door]",
                            goalState="met")
    assert goal["status"] == "live" and goal["goalState"] == "met"
    with pytest.raises(ValueError) as exc:
        sdk.create_point("goal", "GOAL - duplicate [create-door]",
                         id=goal["id"], status="archived")
    msg = str(exc.value)
    assert "REFUSED" in msg and "create_point" in msg, msg
    assert "goalState='abandoned'" in msg       # repair 1: add the ending
    assert "status='live'" in msg               # repair 2: drop the ending
    # nothing landed: the stored pair is untouched
    node = sdk.get_point(goal["id"])
    assert node["goalState"] == "met"
    assert not live.is_terminal_status(node.get("status"))

    # the stored half is what decided it — on a GENUINELY fresh explicit id the
    # node does not exist, the probe answers `{}`, and the same write lands
    fresh = sdk.create_point("goal", "GOAL - fresh [create-door]",
                             id=ulid(), status="archived")
    assert fresh["status"] == _CANONICAL_ENDED

    # ⛔ THE FALSE-REFUSAL ARM: this write LANDS ``status="live"`` (the create
    # default, which the caller never names), so the pair it leaves is
    # (live, active) — coherent, even though the node it addresses is ended on
    # ``goalState``. Grading the BAG alone would read the stored status and
    # refuse a write whose result is fine; the helper is handed BOTH endpoints
    # instead, so it never even pays for the read. Proven by handing the probe a
    # view that WOULD refuse (stored deprecated/abandoned): if the door graded
    # the bag, this call raises.
    # ⛔ REVIEW ROUND 3, FINDING 4: the explicit-id leg now reads the node's
    # STORED ending on EVERY create (see the arm's finality branch), so the pair
    # the caller names can no longer resurrect an ended goal.
    #
    # the FALSE-REFUSAL arm below: this write LANDS `status="live"` (the create
    # default, which the caller never names), so the pair it leaves is (live,
    # active) — coherent, and the node it addresses is NOT ended, so the stored
    # read must not refuse it. That is what pins the read as non-destructive;
    # the RESURRECT test below pins the other direction.
    import tortoise.sdk as sdk_mod
    calls = []
    real = sdk_mod._stored_goal_ending_view

    def recording_probe(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)          # the node's OWN values

    sdk_mod._stored_goal_ending_view = recording_probe
    try:
        sdk.create_point("goal", "GOAL - revived [create-door]",
                         id=goal["id"], goalState="active")
    finally:
        sdk_mod._stored_goal_ending_view = real
    assert calls, ("the explicit-id create leg must read the stored half — an "
                   "ending there is final, so the pair alone cannot decide")
    revived = sdk.get_point(goal["id"])
    # the write was NOT refused (this call is the false-refusal arm): the node is
    # still live on both surfaces. ⚠️ The create door does not overwrite a
    # ``goalState`` on a colliding explicit id (measured here: it stays 'met'),
    # which is a separate question from this guard and is why the assertion is
    # about the PAIR's polarity rather than a specific word.
    assert not live.is_terminal_status(revived.get("status"))
    assert revived["goalState"] in GOAL_STATE_VALUES
    assert revived["goalState"] not in RETIRED_GOAL_STATES


def test_the_create_door_grades_the_DEFAULT_status_of_an_EXPLICIT_id(sdk):
    """#13065 review P2 — the create door's probe must be asked for a
    NON-terminal status too, because a CREATE always WRITES one.

    ⛔ The suite was one shape short: the create-door assertion above varies the
    CALLER-NAMED terminal status, while the path a caller actually takes —
    ``create_point(..., id=<retired goal>)`` with no ``status`` at all — resolves
    to the ``"live"`` default. The guard only asked the store when the status it
    resolved was TERMINAL, so this write was ADMITTED and its fold landed
    ``status="live"`` beside a stored ``goalState="abandoned"`` — the pair
    ``test_no_WRITABLE_pair_leaves_the_two_surfaces_disagreeing`` asserts cannot
    exist, reached through the one door that test does not mint from.
    """
    # a `:Point` goal retired on BOTH fields — coherent, so its own create lands
    # (`archived` is the ending a Point's own vocabulary accepts; `deprecated` is
    # an Object word — see the residual recorded on the PR)
    goal = sdk.create_point("goal", "GOAL - CI is fast [dup]",
                            goalState="abandoned", status="archived")
    stored = sdk.get_point(goal["id"])
    assert stored["goalState"] == "abandoned"
    assert live.is_terminal_status(stored["status"])

    # the caller names NO status: the CREATE lands its own default
    with pytest.raises(ValueError) as exc:
        sdk.create_point("goal", "GOAL - duplicate [no status]", id=goal["id"])
    msg = str(exc.value)
    assert "REFUSED" in msg and "create_point" in msg, msg
    assert "status='live'" in msg                # the pair it refused, named
    assert "drop the ending from `goalState`" in msg   # repair 2: drop it

    # nothing landed — the retired pair is untouched, and the two surfaces still
    # read it the same way
    after = sdk.get_point(goal["id"])
    assert after["goalState"] == "abandoned"
    assert after["status"] == _CANONICAL_ENDED
    assert (after["goalState"] in RETIRED_GOAL_STATES) != (
        not live.is_terminal_status(after.get("status"),
                                    bool(after.get("outdated"))))

    # a genuinely fresh explicit id under the same default still lands
    fresh = sdk.create_point("goal", "GOAL - fresh [dup]", id=ulid())
    assert not live.is_terminal_status(fresh["status"])


def test_a_NON_WORD_status_never_reaches_a_frozenset():
    """#13065 review P1 — a non-WORD `status` must never raise `TypeError`.

    ``TERMINAL_EXCLUDED_STATUSES`` / the ``wanted`` sets / ``GOAL_ENDING_STATES``
    are frozensets, so ``status in …`` hashes the operand and raises
    ``TypeError: unhashable type`` — and this guard runs from
    ``_sanitize_props`` on EVERY write door, BEFORE the door's own
    ``isinstance(status, str)`` check can answer. Measured at the pre-fix head:
    12 red in ``tests/test_status_projection.py::TestLifecycleCarrierCoherence``,
    whose assertions expect the door's advertised ``ValueError``
    ("is TERMINAL for a :Object"). ⛔ ``isinstance`` alone would not be enough —
    a ``str`` SUBCLASS can null its own ``__hash__`` — so the helper that answers
    these membership questions normalises with ``str.__str__`` (the transition
    guard's settled idiom) rather than trusting the value's dunders.

    ⛔ WHAT IS PINNED IS THE ERROR CLASS, not an abstention: a non-word beside an
    ENDING ``goalState`` may legitimately be refused with ``ValueError`` (that is
    the class every door advertises); what may never escape is ``TypeError``.
    """
    import tortoise.sdk as sdk_mod

    class _UnhashableWord(str):
        __hash__ = None          # a `str` that cannot be a frozenset member

    for status in (["live"], {"a": 1}, _UnhashableWord("archived")):
        with contextlib.suppress(ValueError):
            sdk_mod._refuse_inconsistent_goal_ending(
                {"goalState": "abandoned", "status": status}, surface="test")
        # ⛔ suppressed ONLY the door's own refusal class: any OTHER exception
        # (TypeError included) propagates and fails this test

    # the abstention is NARROW: `None` is the documented CLEAR, and a goal ended
    # on `goalState` beside it still ends with no reader able to see it ended
    with pytest.raises(ValueError):
        sdk_mod._refuse_inconsistent_goal_ending(
            {"goalState": "abandoned", "status": None}, surface="test")
    # ...and a real WORD on the wrong axis is still refused
    with pytest.raises(ValueError):
        sdk_mod._refuse_inconsistent_goal_ending(
            {"goalState": "abandoned", "status": "live"}, surface="test")


# ── 2. the ordinary verb: one write, and the two surfaces AGREE ────────────

def _world(sdk, *, tag):
    """A measured thing, its target and a goal — the shape the invalidation
    path was built for (#11926)."""
    indicator = sdk.create_object(f"CI duration [{tag}]",
                                  objectKind="workflow")
    target = sdk.create_object(f"CI <= 10m [{tag}]", objectKind="target",
                               comparator="<=", threshold=10.0, dwell=1)
    goal = sdk.create_object(f"CI is fast [{tag}]", objectKind="goal",
                             goalState="active")
    return indicator, target, goal


def test_retiring_with_the_ordinary_verb_ends_both_fields_and_the_surfaces_agree(sdk):
    """The acceptance in one test: the goal is cached ``met`` by a real
    judgement, retired by ONE ``update_knowledge`` call, and then the two
    questions the defect used to answer differently — "does the invalidation
    path consider it retired?" and "does the reading rule serve it as current?" —
    agree, on the node the sweep reads."""
    indicator, target, goal = _world(sdk, tag="agree")
    sdk.create_event("CI run", "measurement", value=9.0, unit="minutes",
                     startedAt="2026-11-11T10:00:00Z",
                     aboutObject=indicator["id"])
    made = judge_and_record(sdk, goal_id=goal["id"],
                            indicator_id=indicator["id"],
                            target=Target.from_node(target))
    assert sdk.get_entity(goal["id"])["goalState"] == "met"   # the cache wrote it

    sdk.update_knowledge(goal["id"], status="deprecated", goalState="abandoned")

    node = sdk.get_entity(goal["id"])
    retired_by_the_invalidation_path = node["goalState"] in RETIRED_GOAL_STATES
    current_to_the_reading_rule = not live.is_terminal_status(
        node.get("status"), bool(node.get("outdated")))
    # ⛔ #13065 review round 13, finding 6 (rule-3 class): the pair is asserted
    # AS A PAIR, once. The three-assert form that stood here ended with
    # `retired != current`, which is ENTAILED by the two lines above
    # (`True != False`) — an assertion no mutation of the diff can redden, seated
    # under a message claiming to pin the divergence. The single tuple comparison
    # below carries the same message and CAN fail: a goal served as current by
    # the reading rule while the invalidation path considers it retired is
    # exactly `(False, True)`, and a goal neither surface ends is `(False, False)`.
    assert (retired_by_the_invalidation_path,
            current_to_the_reading_rule) == (True, False), (
        "the two surfaces disagree about this goal where they must AGREE — the "
        "invalidation path and the reading rule serve it the opposite way "
        "round (the #13035 divergence), or neither serves it"
    )

    found = verdicts(sdk, goal_id=goal["id"])
    assert len(found) == 1
    assert found[0].invalidation.reason == "goal-retired"
    report = invalidate_stale(sdk, goal_id=goal["id"])
    assert report["counts"]["retired"] == 1
    # ⚠️ THIS row is written by the INVALIDATION FOLD, not by the write door, so
    # S0's `retracted`→`deprecated` synonym does NOT apply to it — measured at this
    # head, the stored word is still `retracted`.
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


#: The `status` words a goal can be written with today — the live ones, the two
#: Object/Point terminal words, and the legacy spellings that are already stored
#: in real graphs (`deprecated`) or still writable (`outdated`).
_WRITABLE_STATUSES = (None, "draft", "live", "deprecated", "superseded",
                      "archived", "retracted", "outdated")


def test_no_WRITABLE_pair_leaves_the_two_surfaces_disagreeing(sdk):
    """⛔ THE TEST THAT WOULD HAVE CAUGHT THE DEFECT, over the whole product.

    For every ``goalState`` word against every writable ``status`` word, ONE
    create write, then BOTH questions asked of the node a reader would see. If
    the pair is refused, nothing can disagree; if it is accepted, the invalidation
    path's answer (``goalState in RETIRED_GOAL_STATES``) must EQUAL the reading
    rule's answer inverted (``live.is_terminal_status``). On the pre-change tree
    this fails on ``('abandoned', 'live')`` — the issue's own headline — and on
    every other acceptance of that shape.
    """
    failures = []
    accepted = 0
    landed: set = set()
    for goal_state in sorted(GOAL_STATE_VALUES):
        for status in _WRITABLE_STATUSES:
            props = {"goalState": goal_state}
            if status is not None:
                props["status"] = status
            if goal_state == "superseded":
                # the word's own obligation, satisfied so the pair is judged on
                # AGREEMENT rather than on the missing successor
                props["supersededBy"] = "a-successor"
            try:
                goal = sdk.create_object(
                    f"pair [{goal_state}/{status}]", objectKind="goal", **props)
            except ValueError:
                continue        # refused: the pair cannot exist to disagree
            accepted += 1
            landed.add((goal_state, status))
            node = sdk.get_entity(goal["id"])
            retired = node.get("goalState") in RETIRED_GOAL_STATES
            current = not live.is_terminal_status(
                node.get("status"), bool(node.get("outdated")))
            if retired == current:
                failures.append((goal_state, status, node.get("status"),
                                 node.get("goalState")))
    assert failures == [], (
        "a writable goal is retired to the invalidation path and current to the "
        f"reading rule (or the reverse): {failures}")

    # ⛔ #13065 review finding 8: a POSITIVE CONTROL. `failures == []` alone also
    # holds if EVERY create in the product were refused — the sweep would then
    # never have a pair to disagree about, and the test would pass vacuously
    # while the vocabulary it claims to sweep was entirely unusable.
    assert accepted >= 3, (
        f"the sweep accepted only {accepted} pair(s) of {len(GOAL_STATE_VALUES) * len(_WRITABLE_STATUSES)} — "
        "a total refusal would make the agreement assertions above vacuous")
    assert ("active", "live") in landed, (
        "the ordinary coherent pair ('active', 'live') must land, or this sweep "
        f"is measuring refusals rather than agreement: {sorted(landed)}")

    # and the defect's own pair is REFUSED outright, not merely absent
    with pytest.raises(ValueError):
        sdk.create_object("the defect pair", objectKind="goal",
                          goalState="abandoned", status="live")


def test_a_bare_CORRECTS_edge_satisfies_the_successor_obligation(sdk):
    """A Point goal names its successor with the ``CORRECTS`` edge, never with a
    property (`ONTOLOGY.md` §4.7). ``supersede_point`` writes that edge and the
    ``superseded`` status; the achievement word can then be aligned in a second
    write without being refused for "no successor" — the edge IS the name."""
    old = sdk.create_point("goal", "GOAL - CI is fast [old]", goalState="active")
    new = sdk.create_point("goal", "GOAL - CI is fast [new]")
    sdk.supersede_point(old["id"], new["id"])
    assert sdk.get_point(old["id"])["status"] == "superseded"
    sdk.update_knowledge(old["id"], goalState="superseded")
    assert sdk.get_point(old["id"])["goalState"] == "superseded"


def test_an_ending_is_FINAL_so_a_live_word_cannot_be_revived_over_it(sdk):
    """Design §5, "Same transitions, same latch": an ending is final, so a goal
    at `abandoned` cannot return to `active`. Without this the pair could be made
    incoherent in TWO writes — a paired retirement, then a live achievement word
    — which is why a `goalState` write reads the node's `status` at all."""
    goal = _goal(sdk)
    sdk.update_knowledge(goal["id"], status="deprecated", goalState="abandoned")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="active")
    msg = str(exc.value)
    assert "REFUSED" in msg and "goalState='abandoned'" in msg
    assert sdk.get_entity(goal["id"])["goalState"] == "abandoned"
    # the lifecycle's own door stays open in the same write, and it is the one
    # that clears the ending it lifts (`goalState=None` states nothing)
    sdk.update_knowledge(goal["id"], goalState=None)
    node = sdk.get_entity(goal["id"])
    assert node.get("goalState") is None and node["status"] == "deprecated"


# ── 3. `guideline` is gone, and nothing still expects it ──────────────────

def test_guideline_is_refused_on_every_door(sdk):
    goal = _goal(sdk)
    for call in (
        lambda: sdk.create_object("G", objectKind="goal", goalState="guideline"),
        lambda: sdk.create_point("goal", "GOAL - g", goalState="guideline"),
        lambda: sdk.update_knowledge(goal["id"], goalState="guideline"),
        lambda: sdk.update_point(
            sdk.create_point("goal", "GOAL - h")["id"], goalState="guideline"),
    ):
        with pytest.raises(ValueError, match="guideline"):
            call()
    # the refusal says what to do instead, not just "no"
    with pytest.raises(ValueError) as exc:
        sdk.create_object("G", objectKind="goal", goalState="guideline")
    assert "REMOVED" in str(exc.value) and "#13035" in str(exc.value)
    assert sdk.get_entity(goal["id"])["goalState"] == "active"


def test_guideline_is_refused_through_a_RENDERED_key_too(sdk):
    """#13065 review ROUND 8, finding 1 (P2) — the vocabulary gate itself was a
    ONE-RENDERER presence test, so the test above asserted a boundary that was
    false on three of its four doors.

    MEASURED before the fix: `K("harmless", "goalState"): "guideline"` was
    ADMITTED on `update_knowledge`, `create_object` AND `create_entity` and the
    node stored `goalState='guideline'` — the word the owner REMOVED (#13035,
    owner ruling 2026-11-16) — while the literal spelling in the same run was
    refused. The gate reads through `lifecycle_props_by_rendering` now, the same
    renderer union the two ending guards use.
    """
    class Rendered(str):
        def __str__(self):                      # files as `goalState`
            return "goalState"

        def __hash__(self):                     # ...and misses `in props`
            return hash("not-the-literal-name")

    goal = _goal(sdk, tag="rendered-guideline")
    point = sdk.create_point("goal", "GOAL - rendered guideline")
    for call in (
        lambda: sdk.create_object("G", objectKind="goal",
                                  **{Rendered("harmless"): "guideline"}),
        lambda: sdk.create_point("goal", "GOAL - rg",
                                 **{Rendered("harmless"): "guideline"}),
        lambda: sdk.update_knowledge(goal["id"],
                                     **{Rendered("harmless"): "guideline"}),
        lambda: sdk.update_point(point["id"],
                                 **{Rendered("harmless"): "guideline"}),
    ):
        with pytest.raises(ValueError, match="guideline"):
            call()
    # nothing landed, on either door
    assert sdk.get_entity(goal["id"])["goalState"] == "active"
    assert sdk.get_point(point["id"]).get("goalState") is None


def test_no_code_path_still_EXPECTS_guideline():
    """The grep half of the acceptance: `guideline` may survive only as the
    refusal's own explanation (`sdk.py`), never as a set member, a comparison
    target, a parametrisation or a branch. An AST scan of every string constant
    in the package is used rather than a text grep so prose ABOUT the removal
    (comments, docstrings) cannot satisfy it by accident and cannot fail it
    either."""
    hits = []
    for path in sorted((REPO / "tortoise").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "guideline":
                hits.append((path.relative_to(REPO).as_posix(), node.lineno))
    assert [h for h in hits if h[0] != "tortoise/sdk.py"] == [], (
        "a code path outside sdk.py still names `guideline` as a VALUE: "
        f"{hits}")
    assert len(hits) == 1, (
        "sdk.py must name `guideline` exactly ONCE — in the refusal's own hint "
        f"that explains the removal; found {hits}")

    # ...and the vocabularies themselves, which is what a reader branches on
    assert "guideline" not in GOAL_STATE_VALUES
    assert "guideline" not in GOAL_ENDING_STATES
    assert "guideline" not in GOAL_LIVE_STATES
    assert "guideline" not in RETIRED_GOAL_STATES
    assert "guideline" not in DERIVED_GOAL_STATES


# ── 4. the cache cannot write an ending ───────────────────────────────────

def test_the_judgement_cache_writes_only_met_and_unmet():
    """`blocked`/`active` are stated by a person and the two endings arrive with
    a lifecycle verb (design §5, "Same writers"). A cache that wrote an ending
    would either be refused at the boundary or end a goal nobody decided to
    end."""
    written = {word for word in GOAL_STATE_CACHE.values() if word is not None}
    assert written == {"met", "unmet"}
    assert not (written & GOAL_ENDING_STATES)
    assert {"met", "unmet"} == DERIVED_GOAL_STATES


# ── 5. the probe is a cost the rare write pays, not every write ────────────

def test_the_stored_half_is_read_ONLY_when_the_write_cannot_answer(sdk):
    """The read the door adds (``_stored_goal_ending_view``) must not run on the
    ordinary write: a bag naming BOTH fields with an ENDING ``goalState``, a bag
    naming NEITHER, and a non-terminal ``status`` write are all answered from the
    bag alone. What does cost one read is any ``goalState`` write (the node's
    ``status`` decides whether the achievement word is being revived over an
    ended goal), an ENDED ``status`` write (the node's ``goalState`` is the other
    half), and — since #13065 review round 11 finding 3 — a bag naming both
    fields with a LIVE ``goalState``, because a row can be ended by the legacy
    ``outdated`` flag while its ``status`` says nothing. Pinned by counting
    calls, because this sits on every SDK write.
    """
    import tortoise.sdk as sdk_mod

    goal = _goal(sdk, tag="cost-pair", goalState="met")
    # a goal holding NO achievement word: the store has no claim to
    # contradict, so ending it on `status` alone is legal
    plain = sdk.create_object("CI is fast [cost-plain]", objectKind="goal")
    retiree = _goal(sdk, tag="cost-retired")
    sdk.update_knowledge(retiree["id"], status="deprecated",
                         goalState="abandoned")
    calls = []
    real = sdk_mod._stored_goal_ending_view

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    sdk_mod._stored_goal_ending_view = counting
    try:
        sdk.update_knowledge(goal["id"], status="deprecated",
                             goalState="abandoned")      # both fields: no read
        assert calls == []
        sdk.update_knowledge(goal["id"], name="CI is fast [renamed]")  # neither
        assert calls == []
        sdk.update_knowledge(plain["id"], status="live")  # non-terminal: no read
        assert calls == []
        # an ENDED status with no goalState in the bag: one read, and the node's
        # own (absent) achievement word is what lets it through — a goal that
        # makes no claim cannot contradict an ending
        sdk.update_knowledge(plain["id"], status="deprecated")
        assert len(calls) == 1
        # ...and a live achievement word written over an ENDED goal is refused,
        # which is the whole reason a goalState write pays for the read: the
        # ending is final, not merely a default.
        with pytest.raises(ValueError, match="REFUSED"):
            sdk.update_knowledge(retiree["id"], goalState="active")
        assert len(calls) == 2
    finally:
        sdk_mod._stored_goal_ending_view = real


# ── 9. review round 3: findings 2-5, one test per bypass ──────────────────────


def test_a_superseded_write_that_CLEARS_the_successor_is_refused(sdk):
    """#13065 review finding 2 — the obligation is not discharged by the value
    the write DESTROYS.

    Measured before the fix: a superseded goal carrying ``supersededBy='succ-1'``
    admitted ``update_knowledge(g, goalState='superseded', supersededBy=None)``
    and left ``supersededBy=None`` — the stored carrier the guard read was the
    one this same write was clearing, so the write satisfied the rule by erasing
    the evidence for it. The guard grades the state the write LEAVES.
    """
    goal = sdk.create_object("GOAL - replaced [clear]", objectKind="goal",
                             goalState="active")
    sdk.update_knowledge(goal["id"], status="superseded",
                         goalState="superseded", supersededBy="succ-1")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="superseded",
                             supersededBy=None)
    msg = str(exc.value)
    assert "names no successor" in msg, msg
    # nothing landed: the named successor is still there
    assert sdk.get_entity(goal["id"])["supersededBy"] == "succ-1"


def test_a_RENDERED_goalState_key_cannot_walk_past_the_guard(sdk):
    """#13065 review finding 3 — the PRESENCE TEST must be as shape-independent
    as the refusal it guards.

    ``"goalState" in props`` hashes the literal name, while the live transport
    files a key by ``str(key)``. A ``str`` subclass whose ``__str__`` renders
    ``goalState`` therefore slipped a write past the guard entirely: the defect
    pair (``goalState='abandoned'`` beside a live ``status``) was accepted
    through the door that claims to refuse it.
    """
    class Rendered(str):
        def __str__(self):                      # files as `goalState`
            return "goalState"

        def __hash__(self):                     # ...and misses `in props`
            return hash("not-the-literal-name")

    goal = sdk.create_object("GOAL - rendered [key]", objectKind="goal",
                             goalState="active")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], **{Rendered("zzz"): "abandoned"})
    assert "REFUSED" in str(exc.value), str(exc.value)
    # the node is untouched
    assert sdk.get_entity(goal["id"])["goalState"] == "active"


def test_the_create_door_cannot_RESURRECT_an_ended_goal_by_naming_both(sdk):
    """#13065 review finding 4 — an ending is final on the create door too.

    The door injects its resolved default ``status``, so "one endpoint missing"
    was never true once the caller named ``goalState``: a retired Point goal
    admitted ``create_point(..., id=<that goal>, goalState='met')`` and the fold
    landed a coherent LIVE pair over the ending. The identical transition through
    ``update_knowledge`` was already refused.
    """
    goal = sdk.create_point("goal", "GOAL - retired [resurrect]",
                            goalState="abandoned", status="archived")
    with pytest.raises(ValueError) as exc:
        sdk.create_point("goal", "GOAL - resurrect [named]",
                         id=goal["id"], goalState="met", status="live")
    assert "ALREADY ENDED" in str(exc.value), str(exc.value)
    after = sdk.get_point(goal["id"])
    assert after["goalState"] == "abandoned"          # the ending survived
    assert live.is_terminal_status(after["status"])


def test_DUPLICATE_ids_differing_ONLY_on_the_legacy_flag_are_a_CONFLICT(sdk):
    """#13065 review ROUND 11, finding 1 (P2) — every field the ended-predicate
    reads must be in the disagreement key.

    Round 10 made `outdated` part of "did this goal end?", but the conflict set
    still compared only `status`/`goalState`. Two nodes sharing one id that agree
    on those and differ ONLY on the flag therefore set no `conflict`, and the
    verdict came from `rows[0]`'s flag: MEASURED — `endings`=1, `conflict`=False,
    `create_point(…, id=<that id>, status='live', goalState='met')` ADMITTED on
    the first attempt, then REFUSED on the re-run once the landed write shifted
    the row order. Plan-order dependent, which is exactly what this arm exists to
    prevent.
    """
    goal = sdk.create_point("goal", "GOAL - dup flag [a]",
                            goalState="active", status="live")
    sdk._get_proj().g.query(
        "CREATE (:Point {id:$id, name:'GOAL - dup flag [b]', "
        "status:'live', goalState:'active', outdated:true})",
        params={"id": goal["id"]},
    )
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="met")
    assert "more than one node" in str(exc.value), str(exc.value)


def test_DUPLICATE_ids_differing_ONLY_on_the_SUCCESSOR_are_a_CONFLICT(sdk):
    """#13065 review ROUND 12, finding 1 (P2) — the successor is part of the
    ending, so it belongs in the disagreement key.

    `goalState='superseded'` is admitted or refused on whether a successor is
    named, but the successor was still read from `rows[0]`: MEASURED — two rows
    agreeing on `status`/`goalState`/`outdated` and differing only on
    `supersededBy` set no `conflict`, and the same call
    (`update_knowledge(id, status='superseded', goalState='superseded')`) was
    ADMITTED on one row and REFUSED "names no successor" on two, with no
    `conflict` raised. The verdict was literally taken from an unstable row order.
    """
    goal = sdk.create_point("goal", "GOAL - dup successor [a]",
                            goalState="active", status="live")
    sdk._get_proj().g.query(
        "CREATE (:Point {id:$id, name:'GOAL - dup successor [b]', "
        "status:'live', goalState:'active', supersededBy:'succ-1'})",
        params={"id": goal["id"]},
    )
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], status="superseded",
                             goalState="superseded")
    assert "more than one node" in str(exc.value), str(exc.value)


def test_DUPLICATE_ids_that_disagree_refuse_rather_than_answer_from_one(sdk):
    """#13065 review finding 5 — ``rows[0]`` is not the node set the write binds.

    Two nodes carrying one id are mintable through ``create_point(..., id=…)``,
    and the write lands on BOTH. Answering from ``rows[0]`` made the verdict
    plan-order dependent (measured: two identical statements in one process
    returned the rows in different orders).
    """
    goal = sdk.create_point("goal", "GOAL - dup ids [a]",
                            goalState="met", status="live")
    sdk._get_proj().g.query(
        "CREATE (:Point {id:$id, name:'GOAL - dup ids [b]', "
        "status:'archived', goalState:'abandoned'})",
        params={"id": goal["id"]},
    )
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="abandoned")
    assert "more than one node" in str(exc.value), str(exc.value)


# ── 10. review round 5: findings 1-3, one test per bypass ────────────────────


def test_a_RE_MENTION_create_cannot_land_HALF_an_ending(sdk):
    """#13065 review round 5, finding 1 (P1) — the create funnel's ON-MATCH fold
    writes ``status`` at CREATION only, so a coherent ENDING pair lands half.

    MEASURED before the fix: ``create_object(name=<an existing live goal>,
    status='archived', goalState='abandoned')`` was ADMITTED (both endpoints
    agree in the bag, and a create ran no stored probe) and the fold left
    ``status=None`` (reads LIVE) beside ``goalState='abandoned'`` — retired to
    ``derived_invalidation`` and served as CURRENT by every ``status`` reader, the
    issue's own headline through a door the cross-product sweep cannot mint from
    (every create in that sweep starts from a fresh holder).
    """
    goal = _goal(sdk, tag="re-mention")          # goalState='active', status absent
    with pytest.raises(ValueError) as exc:
        sdk.create_object("CI is fast [re-mention]", objectKind="goal",
                          status="archived", goalState="abandoned")
    msg = str(exc.value)
    assert "REFUSED" in msg
    assert "RE-MENTION" in msg, msg
    assert "update_knowledge" in msg, msg        # repair 1 names the door that works
    # nothing landed: the refusal is before the first write
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "active"
    assert not live.is_terminal_status(node.get("status"),
                                       bool(node.get("outdated")))
    # ...and the named repair really does end it on both fields
    sdk.update_knowledge(goal["id"], status="archived", goalState="abandoned")
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "abandoned"
    assert live.is_terminal_status(node.get("status"),
                                  bool(node.get("outdated")))


def test_a_RE_MENTION_create_with_a_RENDERED_goalState_key_is_refused(sdk):
    """#13065 review ROUND 7, finding 1 (P2) — the re-mention arm's OWN presence
    test was left on ONE renderer.

    Round 5 finding 1 added the re-mention refusal; round 6 finding 3 taught the
    sibling successor scan to see BOTH renderers; this arm kept
    ``props.get("goalState")``, which hashes the literal name. MEASURED before
    the fix: an existing goal plus
    ``create_object(name, objectKind="goal", status="archived",
    **{Rendered("harmless"): "abandoned"})`` was ADMITTED and left
    ``status=None`` beside ``goalState='abandoned'`` — the same headline pair,
    through the same door, one spelling over.

    The plain-key control lives in
    :func:`test_a_RE_MENTION_create_cannot_land_HALF_an_ending`; this is the arm
    that test could not mint (it only spells the key the literal way).
    """
    class Rendered(str):
        def __str__(self):                      # files as `goalState`
            return "goalState"

        def __hash__(self):                     # ...and misses `in props`
            return hash("not-the-literal-name")

    goal = _goal(sdk, tag="re-mention-rendered")   # goalState='active', no status
    with pytest.raises(ValueError) as exc:
        sdk.create_object("CI is fast [re-mention-rendered]", objectKind="goal",
                          status="archived", **{Rendered("harmless"): "abandoned"})
    msg = str(exc.value)
    assert "REFUSED" in msg
    assert "RE-MENTION" in msg, msg
    # nothing landed: the node keeps the state it had, on BOTH fields
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "active"
    assert not live.is_terminal_status(node.get("status"),
                                       bool(node.get("outdated")))


def test_an_UNHASHABLE_stored_status_reaches_the_documented_ValueError(sdk):
    """#13065 review round 5, finding 3 (P2) — the STORED side, not just the bag.

    The write side was made type-safe (``_is_lifecycle_word``); the stored side
    still built a SET from the raw ``status``/``goalState``, so an unhashable
    stored value — storable through the public door — raised ``TypeError:
    unhashable type: 'list'`` from inside ``_sanitize_props`` on any write that
    needed the stored half, where the doors promise the documented ``ValueError``.

    ⛔ THE FIXTURE WRITES THE ROW WITH RAW CYPHER (#12869 merge reconciliation).
    The sentence above ("storable through the public door") stopped being true
    when `#12869` landed: every SDK door now REFUSES a non-string `status`
    before it can store one, so `update_knowledge(goal, status=["live"])` raises
    at the fixture line instead of building the row this test is about. What the
    test asserts is unchanged and still matters — such a row remains reachable
    from a raw Cypher producer, a JSONL replay or a pre-fix SDK, which is the
    route `#12869`'s own suite documents
    (`test_a_row_that_ALREADY_holds_a_non_scalar_is_REFUSED_not_crashed`).
    ⚠️ Non-vacuity is unaffected: the assertion still reds as
    `TypeError: unhashable type: 'list'` if the stored-side hardening is
    reverted, because the fixture no longer depends on the door it is bypassing.
    """
    goal = _goal(sdk, tag="unhashable")
    # ⛔ #11824 S0 CLOSED THE PUBLIC ROUTE for this shape (`a status must be a
    # plain string, and list is not one`), so the unhashable value can no longer
    # be written through a door — and the STORED-side hazard this test exists for
    # is therefore reachable only BELOW the doors, which is where a legacy row
    # sits. Injected raw, exactly as the legacy-row arms above do.
    sdk._get_proj().g.query(
        "MATCH (n {id:$id}) SET n.status = ['live']", params={"id": goal["id"]})
    with pytest.raises(ValueError) as exc:              # NOT TypeError
        sdk.update_knowledge(goal["id"], goalState="abandoned")
    assert isinstance(exc.value, ValueError)
    assert not isinstance(exc.value, TypeError)
    assert "leaves it running" in str(exc.value), str(exc.value)


def test_a_goal_retired_by_the_LEGACY_outdated_flag_refuses_a_LIVE_word(sdk):
    """#13065 review ROUND 10, finding 1 (P2) — the door and its own repairs must
    agree about which rows are ended.

    The two read predicates this PR adds are `ended_on_status or bool(outdated)`
    (`derived_invalidation._retired_by_status`, `goal_judgement._goal_retired_on
    _status`). The write guard read only `status`, so a goal retired by the LEGACY
    `outdated` flag got no stored-side check: MEASURED before the fix —
    `SET n.outdated = true` on a live goal, then `update_knowledge(goal,
    goalState='met')` LANDED, leaving `status=None, goalState='met',
    outdated=True`: retired on the axis `derived_invalidation` reads and live on
    the axis every other reader reads. That is the #13035 divergence, admitted
    through the chokepoint that claims to close it.
    """
    goal = _goal(sdk, tag="legacy-outdated")
    # the pre-#2901 retirement, set the way the legacy rows carry it
    sdk._get_proj().g.query("MATCH (n {id:$id}) SET n.outdated = true",
                            params={"id": goal["id"]})
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="met")
    msg = str(exc.value)
    assert "REFUSED" in msg
    # the refusal names the carrier it actually found, not a `status` that is absent
    assert "outdated" in msg, msg
    # nothing landed
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "active"
    # ...and the coherent ending still works on that row
    sdk.update_knowledge(goal["id"], status="archived", goalState="abandoned")
    assert sdk.get_entity(goal["id"])["goalState"] == "abandoned"


def test_the_SWEEP_clears_a_goal_retired_on_its_status(sdk):
    """#13065 review round 5, finding 2 (P2) — ``expected == word`` is not
    "the cache is right".

    MEASURED before the fix: a goal carrying ``status='archived'`` (ended) and
    ``goalState='met'`` (a word that still stands) answered the sweep with
    ``consistent: 1``, wrote nothing, and left the word standing — a false PASS
    over exactly the pair the retired-by-status arm exists to CLEAR, while the
    door called directly cleared it.
    """
    from tortoise.derived_invalidation import reconcile_goal_states

    goal = _goal(sdk, tag="sweep-retired")
    # ⛔ A LIVE judgement that maps to the cached word is what makes this test
    # able to fail: without one, `expected` is already None and the sweep would
    # reach the repair no matter what — the vacuous-arm class review finding 8
    # named (measured: the first version of this test PASSED with the
    # `expected == word` short-circuit restored, i.e. it proved nothing).
    indicator = sdk.create_object("CI pipeline duration [sweep-retired]",
                                 objectKind="workflow")
    target = sdk.create_object("CI <= 10m [sweep-retired]", objectKind="target",
                              comparator="<=", threshold=10.0, dwell=1)
    sdk.create_event("CI run 2026-11-11T10:00:00Z", "measurement", value=9.0,
                     unit="minutes", startedAt="2026-11-11T10:00:00Z",
                     aboutObject=indicator["id"])
    made = judge_and_record(sdk, goal_id=goal["id"],
                            indicator_id=indicator["id"],
                            target=Target.from_node(target))
    assert made["goalState"] == "met", made        # the cache word STANDS
    # ...and the goal is then ENDED on its status, which is the legacy row the
    # retired-by-status arm exists for (the door refuses this pair; a legacy row
    # predates the door, so it is written raw)
    sdk._get_proj().g.query(
        "MATCH (n {id:$id}) SET n.status='archived'", params={"id": goal["id"]}
    )
    out = reconcile_goal_states(sdk)
    assert out["counts"]["consistent"] == 0, out["counts"]
    assert out["counts"]["cleared"] >= 1, out["counts"]
    node = sdk.get_entity(goal["id"])
    assert node.get("goalState") in (None, ""), node.get("goalState")


# ── 11. review round 6: the shapes that walked past rounds 3-5 ───────────────


class _TwoRenderings(str):
    """A key whose JOURNAL content and LIVE rendering differ — the shape the
    transport's two-renderer contract makes reachable (`live.py`)."""

    def __new__(cls, content, rendered):
        obj = str.__new__(cls, content)
        obj.rendered = rendered
        return obj

    def __str__(self):
        return self.rendered


def test_ONE_key_rendering_as_BOTH_guarded_names_is_refused(sdk):
    """#13065 review round 6, finding 2.

    ``_event_state_key_names`` answers the UNION of both renderers, so ONE key can
    populate ``status`` AND ``goalState`` in the guard's view while the live write
    files it under ``str(key)`` only. ``doubled`` counts keys PER name, so it read
    1 and 1 and never fired — measured: ``('superseded','superseded')`` ADMITTED
    and the node left ``status=None`` beside ``goalState='superseded'``.
    """
    goal = _goal(sdk, tag="two-renderings")
    key = _TwoRenderings("status", "goalState")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], **{key: "superseded",
                                           "supersededBy": "succ-1"})
    assert "CONFLICTING status write" in str(exc.value), str(exc.value)
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "active"           # nothing landed


def test_a_successor_cleared_through_a_RENDERED_key_is_refused(sdk):
    """#13065 review round 6, finding 3.

    The carrier was recognised by ``snake(key)`` (the key's CONTENT) while the
    live write files the key by ``str(key)``. A key with content ``'harmless'``
    and ``__str__() == 'supersededBy'`` was invisible to the scan, so the guard
    fell back to the STORED column — the very one the write nulls — and admitted
    the clear.
    """
    goal = _goal(sdk, tag="rendered-carrier", goalState="superseded",
                 status="superseded", supersededBy="succ-2")
    key = _TwoRenderings("harmless", "supersededBy")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="superseded", **{key: None})
    assert "names no successor" in str(exc.value), str(exc.value)
    assert sdk.get_entity(goal["id"])["supersededBy"] == "succ-2"


def test_the_SWEEP_clears_a_goal_retired_by_the_LEGACY_outdated_flag(sdk):
    """#13065 review round 6, finding 4 — the OTHER half of the same predicate.

    ``_retired_by_status`` is ``ended_on_status or bool(goal.get("outdated"))``;
    round 5 fed the sweep only the first half, so a flag-retired goal with a
    standing cache word still short-circuited to ``consistent`` (measured), while
    the door called directly cleared it.
    """
    from tortoise.derived_invalidation import reconcile_goal_states

    goal = _goal(sdk, tag="sweep-outdated")
    indicator = sdk.create_object("CI pipeline duration [outdated]",
                                 objectKind="workflow")
    target = sdk.create_object("CI <= 10m [outdated]", objectKind="target",
                              comparator="<=", threshold=10.0, dwell=1)
    sdk.create_event("CI run 2026-11-11T10:00:00Z", "measurement", value=9.0,
                     unit="minutes", startedAt="2026-11-11T10:00:00Z",
                     aboutObject=indicator["id"])
    made = judge_and_record(sdk, goal_id=goal["id"],
                            indicator_id=indicator["id"],
                            target=Target.from_node(target))
    assert made["goalState"] == "met", made
    sdk._get_proj().g.query(
        "MATCH (n {id:$id}) SET n.outdated = true", params={"id": goal["id"]})
    out = reconcile_goal_states(sdk)
    assert out["counts"]["consistent"] == 0, out["counts"]
    assert out["counts"]["cleared"] >= 1, out["counts"]
    assert sdk.get_entity(goal["id"]).get("goalState") in (None, "")


# ── 13. review round 13 ─────────────────────────────────────────────────────

def test_a_READ_MODIFY_WRITE_echo_of_a_terminal_status_is_admitted(sdk):
    """F1 (P2) — the ``#3305``/F4-2 decision, one axis over.

    A generic tenant PUT is a read-modify-write: it fetches the row and sends it
    back with the fields it changed. Every goal Point ended by
    ``supersede_point`` / ``retract_point`` / ``invalidate_point`` sits at
    ``status=terminal`` + ``goalState=live`` — the shape this file's own body
    calls the BENIGN direction and ``derived_invalidation``'s sweep exists to keep
    serving. MEASURED before the exemption: ``update_knowledge(<that goal>,
    status=<the status it already has>, content=…)`` raised, though it changed
    nothing about the ending. The exemption that admits it pre-PR
    (``_status_entailed``, ``#3305``: *"An unchanged status cannot resurrect
    anything"*) is computed inside ``_update_entity``, far AFTER this guard runs
    from ``_sanitize_props``, so it could never disarm it.

    ⛔ And it stays NARROW: the second half of the test proves a DIFFERENT
    terminal word is still graded, so the exemption is a restatement rule and not
    an open door.
    """
    goal = sdk.create_point("goal", "GOAL - echo [a]", goalState="active")
    successor = sdk.create_point("goal", "GOAL - echo [b]", goalState="active")
    sdk.supersede_point(goal["id"], successor["id"])

    before = sdk.get_point(goal["id"])
    assert before["status"] == "superseded", before          # the ending
    assert before["goalState"] == "active", before           # the benign half

    # the tenant PUT: the status it already has, sent back with another field
    sdk.update_knowledge(goal["id"], status="superseded",
                         content="GOAL - echo [a] (edited)")
    after = sdk.get_point(goal["id"])
    assert after["status"] == "superseded", after
    assert after["goalState"] == "active", after
    assert "(edited)" in after["content"], after

    # narrow: a terminal word the row does NOT already hold is not a restatement,
    # so the pair is graded and refused as before
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], status="retracted")
    assert "still describes a live commitment" in str(exc.value), str(exc.value)
    assert sdk.get_point(goal["id"])["status"] == "superseded"   # nothing landed


def test_judging_a_RETIRED_goal_writes_no_cache_and_keeps_its_ending(sdk):
    """F2 (P2) — the guard that stops a PARTIAL write had no covering assertion.

    ``_write_goal_state_cache`` returns before stamping the cache when the goal is
    retired. Nothing in the tree went red when those lines were deleted: every
    caller judges a LIVE goal and retires it afterwards. Without the guard,
    ``judge_and_record`` on a retired goal stamps ``met`` over the ending, the
    write boundary refuses that pair, and the ``ValueError`` escapes AFTER the
    judgement Point and its ``IMPL`` arrow landed — a partial write.

    The ending is written as ONE paired write (``status`` + ``goalState``), which
    is the only shape the boundary admits; the legacy half-shape is minted by raw
    graph writes in ``test_11926_derived_invalidation.py``.
    """
    indicator, target, goal = _world(sdk, tag="judge-retired")
    sdk.create_event("CI run", "measurement", value=9.0, unit="minutes",
                     startedAt="2026-11-11T10:00:00Z",
                     aboutObject=indicator["id"])
    first = judge_and_record(sdk, goal_id=goal["id"],
                             indicator_id=indicator["id"],
                             target=Target.from_node(target))
    assert first["goalState"] == "met", first

    sdk.update_knowledge(goal["id"], status="archived", goalState="abandoned")
    assert sdk.get_entity(goal["id"])["goalState"] == "abandoned"

    # judge the SAME goal again, now that it is retired
    made = judge_and_record(sdk, goal_id=goal["id"],
                            indicator_id=indicator["id"],
                            target=Target.from_node(target))
    assert made["goalState"] is None, made          # the cache was NOT written
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "abandoned", node   # the human's ending survived
    assert node["status"] == _CANONICAL_ENDED, node


class _UnreadableSdk:
    """An sdk whose goal read fails — a transient graph fault, not a missing node."""

    def __init__(self):
        self.cache_writes = []

    def get_entity(self, _id):
        raise RuntimeError("transient graph read fault")

    def update_knowledge(self, goal_id, **props):
        self.cache_writes.append((goal_id, props))
        return {}


def test_a_goal_whose_status_CANNOT_BE_READ_is_treated_as_RETIRED():
    """F2 (P2), second half — the read failure is FAIL-CLOSED and visible.

    The helper's ``except`` used to answer ``False`` ("not retired") under a
    ``# pragma: no cover``, so a transient ``get_entity`` fault silently restored
    the partial-write-then-raise path the guard exists to prevent, and the pragma
    made the gap invisible to the coverage floor. "I could not read the status" is
    not evidence that the goal is live: answering "retired" suppresses at most one
    cache write, which ``reconcile_goal_state_cache`` owns repairing, while the
    other direction corrupts a retired goal's ending.
    """
    from tortoise.goal_judgement import _goal_retired_on_status, _write_goal_state_cache

    boom = _UnreadableSdk()
    assert _goal_retired_on_status(boom, "goal-unreadable") is True
    # ...and the cache write is therefore SKIPPED, not attempted blind
    assert _write_goal_state_cache(boom, "goal-unreadable", "met") is None
    assert boom.cache_writes == []


def test_DUPLICATE_ids_differing_ONLY_on_a_FALSY_flag_SPELLING_are_NOT_a_conflict(sdk):
    """F3 (P3, rule-1 class) — ``bool()`` in the conflict key had no pin.

    The key normalises ``outdated`` by TRUTHINESS (the predicate compares
    ``bool(...)``), so a row with the flag ABSENT and a row with it spelled
    ``false`` are the SAME ending and must not read as a disagreement. Under the
    pre-fix ``repr`` the keys were ``r:None`` / ``r:False``, the disagreement set
    held two entries, and a write that legitimately lands was refused
    "more than one node". The shape is minted raw, the same route the sibling
    duplicate-id tests use.
    """
    goal = sdk.create_point("goal", "GOAL - dup flag falsy [a]",
                            goalState="active", status="live")
    sdk._get_proj().g.query(
        "CREATE (:Point {id:$id, name:'GOAL - dup flag falsy [b]', "
        "status:'live', goalState:'active', outdated:false})",
        params={"id": goal["id"]},
    )
    # no ValueError: the two rows agree about "did this goal end?" (no)
    sdk.update_knowledge(goal["id"], goalState="met")
    assert sdk.get_point(goal["id"])["goalState"] == "met"


def test_the_SUCCESSOR_refusal_names_a_repair_the_POINT_doors_ACCEPT(sdk):
    """F4 (P3, AX rule 2) — the prescription must be PERFORMABLE.

    Repair 2 used to say ``goalState='abandoned' with status='deprecated'``, but
    ``deprecated`` is not in ``POINT_STATUS_VALUES`` (``live.py`` states it
    outright: no SDK/API write path emits it), so on a Point the prescribed call
    raised again — the caller was sent in a circle.

    ⛔ The review's own suggested replacement (``status='archived'`` "works on both
    layers") is FALSIFIED by the primary source and MEASURED here: a Point's
    declared transitions from a live state are exactly ``{retracted, superseded}``
    (``_ALLOWED_TRANSITIONS``), and ``update_knowledge(<live goal Point>,
    status='archived')`` raises ``update_entity refused the status transition
    'live' -> 'archived'`` — ``archived`` is RESERVED on a Point. So the repair is
    carrier-aware, and this test performs the Point half rather than grepping it.
    """
    goal = sdk.create_point("goal", "GOAL - successor repair", goalState="active")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="superseded",
                             status="superseded")
    msg = str(exc.value)
    assert "successor" in msg, msg
    assert "status='retracted'" in msg, msg              # the :Point route
    assert "status='deprecated'" not in msg, msg         # the unperformable one
    # the prescribed Point write is accepted on both fields at once
    sdk.update_knowledge(goal["id"], goalState="abandoned", status="retracted")
    node = sdk.get_point(goal["id"])
    assert node["goalState"] == "abandoned", node
    assert node["status"] == _CANONICAL_ENDED, node


def test_the_DRY_RUN_reports_None_for_the_goal_it_is_about_to_CLEAR(sdk):
    """F5 (P3) — the preview contradicted its own documented contract.

    ``to`` reports the OUTCOME, the word that will STAND. A row retired on its
    ``status`` while holding a standing ``met`` word is ``expected == word`` AND
    ``retired``, so the preview reached ``would-clear`` with ``to=expected`` — it
    named the very word it was about to lose, while the LIVE run for the same goal
    reported ``cleared`` / ``to=None``. Measured before the fix: ``would-clear``
    with ``to='met'`` beside a live ``cleared`` with ``to=None``.
    """
    from tortoise.derived_invalidation import reconcile_goal_states

    goal = _goal(sdk, tag="dry-run-to")
    indicator = sdk.create_object("CI pipeline duration [dry-run-to]",
                                 objectKind="workflow")
    target = sdk.create_object("CI <= 10m [dry-run-to]", objectKind="target",
                              comparator="<=", threshold=10.0, dwell=1)
    sdk.create_event("CI run 2026-11-11T10:00:00Z", "measurement", value=9.0,
                     unit="minutes", startedAt="2026-11-11T10:00:00Z",
                     aboutObject=indicator["id"])
    made = judge_and_record(sdk, goal_id=goal["id"],
                            indicator_id=indicator["id"],
                            target=Target.from_node(target))
    assert made["goalState"] == "met", made
    sdk._get_proj().g.query(
        "MATCH (n {id:$id}) SET n.status='archived'", params={"id": goal["id"]})

    preview = reconcile_goal_states(sdk, dry_run=True)
    row = next(g for g in preview["goals"] if g["goalId"] == goal["id"])
    assert row["action"] == "would-clear", row
    assert row["to"] is None, row

    live = reconcile_goal_states(sdk)
    live_row = next(g for g in live["goals"] if g["goalId"] == goal["id"])
    assert (live_row["action"], live_row["to"]) == ("cleared", None), live_row


def test_the_conflict_arm_is_scoped_to_the_GOAL_axis(sdk):
    """A merge-exposed scoping defect: rows with NO `goalState` are not goals.

    The `#12592` successor guard grades the successor question over EVERY bound
    row (`test_12592_supersede_door.py::test_the_verdict_is_over_every_bound_row`),
    and rows that carry no achievement word have no goal ending to disagree
    about. MEASURED on the merge of this branch onto `origin/main`: two
    `:Object`s sharing an id, both `status='live'`, no `goalState` anywhere, one
    carrying `supersededBy`, differing ONLY on that — `update_knowledge(<id>,
    status='superseded')` was refused "more than one node … disagree about the
    goal's ending" instead of the successor guard's own "no successor".

    ⛔ Both directions are asserted, so the gate cannot be widened into a
    weakening: when the rows DO carry a `goalState`, the disagreement (
    including one over the successor) is still the conflict arm's.
    """
    g = sdk._get_proj().g

    # (a) no goalState anywhere: the successor guard answers, over every row
    plain = "fanout-no-goal-13035"
    g.query("CREATE (:Object {id:$id, name:'plain-carried', status:'live', "
            "supersededBy:'v2'})", params={"id": plain})
    g.query("CREATE (:Object {id:$id, name:'plain-bare', status:'live'})",
            params={"id": plain})
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(plain, status="superseded")
    msg = str(exc.value)
    assert "no successor" in msg, msg
    assert "more than one node" not in msg, msg
    statuses = sorted(r[0] for r in g.query(
        "MATCH (n:Object {id:$id}) RETURN n.status",
        params={"id": plain}).result_set)
    assert statuses == ["live", "live"], statuses

    # (b) the goal axis IS in play: the same successor-only difference is a
    # conflict, so the verdict cannot be taken from `rows[0]`
    goal = "fanout-goal-13035"
    g.query("CREATE (:Object {id:$id, name:'goal-carried', status:'live', "
            "goalState:'active', supersededBy:'v2'})", params={"id": goal})
    g.query("CREATE (:Object {id:$id, name:'goal-bare', status:'live', "
            "goalState:'active'})", params={"id": goal})
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal, status="superseded", goalState="superseded")
    assert "more than one node" in str(exc.value), str(exc.value)

    # (c) the OTHER disjunct — the rows carry a `goalState` while the BAG names
    # none (`#13065 review round 16, finding 3`: this half had no discriminating
    # row, so deleting `or _stored.get("anyGoalState")` reddened nothing). The
    # write is `status`-only, so the bag cannot put the goal axis in play; only
    # the ROWS can. With the disjunct the conflict arm answers; without it the
    # verdict falls through to the stored `goalState` from `rows[0]` — the
    # plan-order dependence this arm exists to prevent.
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal, status="archived")
    assert "more than one node" in str(exc.value), str(exc.value)


# ── 14. review rounds 15/16 ─────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["   ", 0, False, [], ""])
def test_a_NON_NAME_does_not_discharge_the_successor_obligation_on_a_CREATE(sdk, value):
    """F1 (P2) of rounds 15/16 — ONE authority for "does this name a successor".

    `#12914` is merged and its `_successor_named_by` owns that question (exact
    `str`, non-blank, camel spelling). The achievement axis answered it a second
    time with `value not in (None, "")`, which admits every one of these — and
    `create_object`/`create_subject` have NO second check (the merged door's own
    SCOPE note calls the create door a disclosed residual), so a goal was born
    `superseded` naming nobody. The owner's #13035 brief is a recorded decision:
    *"Do not re-derive the successor requirement … reuse its helper … so the two
    axes cannot drift apart in what they demand."*
    """
    with pytest.raises(ValueError) as exc:
        sdk.create_object(f"GOAL - non-name successor {value!r}",
                          objectKind="goal", goalState="superseded",
                          status="superseded", supersededBy=value)
    msg = str(exc.value)
    assert "REFUSED" in msg and "successor" in msg, msg


def test_the_SNAKE_successor_spelling_does_not_name_anybody(sdk):
    """F1 (P2), the spelling half — `superseded_by` is a SHADOW property.

    The live write applies `SET n += $props` literally, so the snake key stores a
    property that every camel-keyed reader of the successor ignores (#12782); the
    merged `#12914` helper rejects it for that measured reason, and this axis must
    not accept it either.
    """
    with pytest.raises(ValueError) as exc:
        sdk.create_object("GOAL - snake successor", objectKind="goal",
                          goalState="superseded", status="superseded",
                          superseded_by="v2")
    assert "successor" in str(exc.value), str(exc.value)


def test_the_STORED_half_reads_a_blank_successor_as_NOBODY(sdk):
    """F1 (P2), the stored half — `_stored_goal_ending_view`'s `successor`.

    The view carried the same looser test (`carrier not in (None, "")`), so a
    stored `supersededBy="   "` named a successor there and nowhere else.

    ⛔ Asserted at the VIEW, not through a door, and the reason is measured: on
    an UPDATE door the merged `#12914` door masks a blank stored value (it refuses
    a `superseded` write that names nothing in the BAG), and on the create funnel
    the guard's stored probe is absent (round 16, finding 2) — so an end-to-end
    assertion here would pass on the pre-fix code and prove nothing. The view is
    the place the rule decides, and the value it returns is what the guard and the
    conflict key both read.
    """
    from tortoise.sdk import _stored_goal_ending_view

    # ⛔ ONE row on purpose: the view answers from `rows[0]` (the conflict key is
    # what makes a multi-row verdict order-independent), so a two-row fixture
    # would pass on the pre-fix code whenever the bare row sorted first — the
    # vacuous shape this suite has had to fix twice before.
    g = sdk._get_proj().g
    blank = "blank-successor-13035"
    g.query("CREATE (:Point {id:$id, name:'bs-blank', status:'live', "
            "goalState:'active', supersededBy:'   '})", params={"id": blank})
    view = _stored_goal_ending_view(sdk._get_proj(), blank)
    assert view["successor"] is None, view      # a blank names nobody
    # ⛔ The SNAKE spelling is NOT asserted here: on the STORED side both
    # renderings are ONE record column (`record_projection.snake`, #12592
    # cycle-4), which is why the view reads `coalesce(supersededBy,
    # superseded_by)`. Rejecting the snake spelling on the STORED side would
    # reverse that recorded decision; the write-side rejection above is the
    # #12914 rule, where the shadow property is what gets written.


def test_a_RE_MENTION_create_on_DUPLICATE_disagreeing_ids_refuses(sdk):
    """F2 (P2) of round 16 — the deferral named an arm that cannot run here.

    The re-mention arm returned on `view["conflict"]`, deferring to the guard's
    conflict arm. That arm reads the conflict from the caller's stored-side
    PROBE, and the entity create funnel calls `_sanitize_props` with no probe —
    so `_stored` stays `{}`, `.get("conflict")` is falsy, and the arm never fires
    on any create door. The stated reason for returning was false on the one door
    that calls this function, so the shape landed half the pair.
    """
    g = sdk._get_proj().g
    twin = "re-mention-conflict-13035"
    g.query("CREATE (:Object {id:$id, name:'rm-a', objectKind:'goal', "
            "status:'live', goalState:'active'})", params={"id": twin})
    g.query("CREATE (:Object {id:$id, name:'rm-b', objectKind:'goal', "
            "status:'archived', goalState:'abandoned'})", params={"id": twin})
    with pytest.raises(ValueError) as exc:
        # The arm is called directly: its only production caller is the entity
        # CREATE funnel, whose id is NAME-DERIVED, so the duplicate-id fixture it
        # must be given cannot be reached by naming the create. This is the shape
        # the reviewer measured through the arm itself.
        from tortoise.sdk import _refuse_ending_dropped_by_a_re_mention
        _refuse_ending_dropped_by_a_re_mention(
            sdk._get_proj(), twin,
            {"goalState": "abandoned"}, label="Object",
            surface="create_object")
    msg = str(exc.value)
    assert "more than one node" in msg, msg
    assert "resolve the duplicate ids first" in msg, msg


def test_an_UNHASHABLE_goalState_reaches_the_documented_ValueError(sdk):
    """F4 (P3) of round 16 — PRE-EXISTING, and cheap here.

    The vocabulary gate hashed the value (`not in GOAL_STATE_VALUES`), so an
    unhashable `str` SUBCLASS raised `TypeError` where the doors document
    `ValueError` — and that gate runs BEFORE the guard, so the type-first helper
    the module already has never got the chance. `_is_lifecycle_word` is the
    module's rule for this class.
    """
    class _UnhashableWord(str):
        __hash__ = None

    # An unhashable value whose CONTENT is not a legal word: the gate must refuse
    # it with the documented `ValueError`. Before the fix the raw membership test
    # hashed it and raised `TypeError: unhashable type`.
    with pytest.raises(ValueError) as exc:
        sdk.create_object("GOAL - unhashable goalState", objectKind="goal",
                          goalState=_UnhashableWord("nonsense"))
    assert "Invalid goalState" in str(exc.value), str(exc.value)

    # ...and one whose content IS a legal word is graded by that content (the
    # module's type-first rule), never by hashing the object: no `TypeError`
    # escapes and the write lands.
    goal = sdk.create_object("GOAL - unhashable legal goalState",
                             objectKind="goal",
                             goalState=_UnhashableWord("met"))
    assert sdk.get_entity(goal["id"])["goalState"] == "met"


# ── 15. review round 15: the rendering split, and the supersede verb ──────────


class _RenderingLie(str):
    """A `str` whose CONTENT is honest and whose RENDERING lies (#12906's shape).

    The guard grades ``str.__str__`` (base content, what the journal and the
    record hold), but the DRIVER renders what it was given —
    `falkordb.helpers.quote_string` calls ``len(v)`` and ``v.replace(...)`` on the
    CALLER's object. So a subclass could be graded as one word and stored as
    another. Retargeted at ``goalState`` by #13065 review round 15, finding 1.
    """

    _renders_as = "abandoned"

    def replace(self, *args, **kwargs):
        return self._renders_as


class _EqualityLie(str):
    """A `str` whose CONTENT is honest and whose `__eq__` lies.

    #13065 review ROUND 16, finding 1 (P2). Rebinding the stored value closed the
    RENDERING gap, but the guard's verdicts are read from the same map and two of
    them are **equality tests** — `_status_words_for_goal_ending`'s
    `ending == 'superseded'` and the successor arm's `goal_state == 'superseded'`.
    A `str` subclass may override `__eq__`, so a value whose content IS a spelling
    of `superseded` could answer "no" and be filed as `superseded` while naming
    nobody — the one pair the guard's second arm exists to refuse.
    """

    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    __hash__ = str.__hash__


class _AlwaysEqualLie(str):
    """A `str` that answers "equal" to everything — the OTHER lie direction.

    #13065 review ROUND 18. The `False` direction of this lie is mostly harmless
    for a value whose content already spells the same ending; the `True` direction
    is what lets a content-distinct value pass a comparison it does not satisfy.
    MEASURED on this head with the verdict-map normalisation removed (i.e. the
    pre-fix path): ``_refuse_inconsistent_goal_ending({"status": "superseded",
    "supersededBy": "succ-1", "goalState": <content 'abandoned', __eq__ -> True>})``
    **LANDED** while the plain-`str` control was REFUSED with the identical bag —
    so a goal ended on ``status`` and running on ``goalState`` got through. With
    the normalisation in place both are REFUSED.
    """

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = str.__hash__


def test_a_goalState_whose_RENDERING_lies_cannot_split_the_pair(sdk):
    """#13065 review ROUND 15, finding 1 (P1) — grade what the WRITE SENDS.

    MEASURED on the reviewed head: ``update_knowledge(goal,
    goalState=<content 'active', replace()->'abandoned'>)`` was graded as the live
    word ``active`` (correct) and then STORED as ``abandoned``, leaving
    ``status='live', goalState='abandoned'`` — the issue's own headline pair and a
    ``failures`` row under this suite's own acceptance predicate. The same through
    ``create_object``, in both directions.

    The fix rebinds the guarded values to the base content the guard graded, the
    way `_update_entity`'s no-promote arm already does for ``status`` and
    ``supersede_knowledge`` does for its operands (#12914 P1). So the pair stays
    coherent AND the stored word is the one that was graded.
    """
    goal = _goal(sdk, tag="rendering-lie", goalState="active")
    assert live.is_terminal_status(sdk.get_entity(goal["id"]).get("status")) is False

    # (1) the content is a LIVE word, the rendering claims an ENDING: the write is
    # a no-op on the ending, so it must not end the row
    sdk.update_knowledge(goal["id"], goalState=_RenderingLie("active"))
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "active", (
        "the RENDERING was stored instead of the graded content: the pair is now "
        f"split ({node.get('status')!r}/{node.get('goalState')!r})")
    # ...and the PR's own acceptance predicate holds on the result: both surfaces
    # read this goal the SAME way (it is live on both)
    _retired = node["goalState"] in RETIRED_GOAL_STATES
    _current = not live.is_terminal_status(node.get("status"),
                                           bool(node.get("outdated")))
    assert (_retired, _current) == (False, True), (
        "the two surfaces disagree about the goal: "
        f"{node.get('status')!r}/{node.get('goalState')!r}")

    # (2) the same lie through the CREATE door
    created = sdk.create_object("CI is fast [rendering-lie-create]",
                                objectKind="goal", status="live",
                                goalState=_RenderingLie("active"))
    assert sdk.get_entity(created["id"])["goalState"] == "active"

    # (3) the OTHER half of the same class of gap: the rebinding must rewrite the
    # guard's OWN verdict map too, not just the values it sends. Asserted directly
    # because on most doors `_update_entity`'s no-promote arm also rebinds
    # `status` (#12906), so a door-level assertion would pass for two reasons and
    # discriminate neither. #13065 review round 16, finding 2 (P3).
    props = {"status": _RenderingLie("live", ),
             "goalState": _RenderingLie("active")}
    rendered = {"status": props["status"], "goalState": props["goalState"]}
    _rebind_guarded_words(props, rendered)
    assert (props["status"], props["goalState"]) == ("live", "active"), props
    assert type(props["status"]) is str and type(props["goalState"]) is str, (
        "the stored value is still a subclass whose RENDERING can differ from what "
        f"was graded: {type(props['status'])!r}")
    assert type(rendered["status"]) is str and type(rendered["goalState"]) is str, (
        "the guard's verdict map still holds the caller's object, so its verdicts "
        f"can consult an override: {rendered!r}")


def test_an_EQUALITY_lie_cannot_forge_a_superseded_ending(sdk):
    """#13065 review ROUND 16, finding 1 (P2) — the verdicts are equality tests.

    ⚠️ CORRECTED in round 17, and corrected AGAIN in round 18 — the first version
    of this note said row 1 was "caught by the guard's FIRST arm whichever way
    `__eq__` answers … so it never reaches an equality test". Both halves are
    false: the row-1 lie's `__eq__` IS consulted (`sdk.py:6363` → `5995`, and
    `sdk.py:6384`), and a `__eq__`->True lie on the SAME bag **LANDED** pre-fix
    (MEASURED above). What is true of row 1 is narrower: the `__eq__`->False
    answer happens to coincide with what the content says, so that direction
    cannot flip the outcome. Row 1-as-written is therefore a control, the
    `__eq__`->True direction is a second hole (pinned below), and row 2 is the
    third.

    The row that IS the hole — re-measured here, subclass LANDED vs plain control
    REFUSED:

    ``update_knowledge(goal, status='archived', goalState=<content 'superseded',
    __eq__ -> False>)``
        control REFUSED ("goalState='superseded' ends the goal, but status=
        'archived' leaves it running"), subclass LANDED as
        ``goalState='superseded'`` with **``supersededBy=None``** — a stored
        ``superseded`` ending naming no successor, the exact pair the second arm
        refuses. Same through the create door.

    The fix normalises the verdict map, so every later read compares base content.
    """
    goal = _goal(sdk, tag="equality-lie", goalState="active")

    # (1) CONTROL — a `superseded` status against a goalState whose content is an
    # ABANDONED word, with the lie answering "not equal". Refused with and without
    # the fix, because that answer coincides with the content; it pins "this
    # direction cannot hide a disagreement", not the fix.
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], status="superseded",
                             supersededBy="succ-1",
                             goalState=_EqualityLie("abandoned"))
    assert "not interchangeable" in str(exc.value), str(exc.value)

    # (1b) the SAME bag with the lie in the OTHER direction DID land pre-fix
    # (MEASURED: LANDED vs the plain control's REFUSED), so this is a second hole
    # of the same family, not a restatement of row 2.
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], status="superseded",
                             supersededBy="succ-1",
                             goalState=_AlwaysEqualLie("abandoned"))
    assert "not interchangeable" in str(exc.value), str(exc.value)

    # ...the plain-str control, so the assertion above is not just "this door
    # refuses everything"
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], status="superseded",
                             supersededBy="succ-1", goalState="abandoned")
    assert "not interchangeable" in str(exc.value), str(exc.value)

    # (2) the sharper consequence: a `superseded` goalState against a NON-superseded
    # status names no successor, so it must be refused rather than stored bare
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], status="archived",
                             goalState=_EqualityLie("superseded"))
    assert "successor" in str(exc.value).lower(), str(exc.value)

    # ...and the create door
    with pytest.raises(ValueError) as exc:
        sdk.create_object("CI is fast [equality-lie-create]", objectKind="goal",
                          status="archived",
                          goalState=_EqualityLie("superseded"))
    assert "successor" in str(exc.value).lower(), str(exc.value)

    # the row is untouched by the four refusals
    node = sdk.get_entity(goal["id"])
    assert node["goalState"] == "active"
    assert node.get("supersededBy") is None, node


def test_the_family_Supersede_verb_aligns_a_GOALs_achievement_word(sdk):
    """#13065 review ROUND 15, finding 2 (P2) — the shipped verb could not retire
    a goal, and neither named repair was performable through it.

    MEASURED on the reviewed head:
    ``supersede_knowledge(<plan Object, goalState='active'>, <successor>)`` was
    REFUSED ("status='superseded' ends the goal, but goalState='active' still
    describes a live commitment"), because the verb writes
    ``status``/``supersededBy``/``supersededAt`` and left ``goalState`` alone —
    while ``supersede_knowledge`` and its MCP tool (the only public family
    supersede route) have no ``goalState`` parameter, so neither repair in the
    refusal could be taken. A row with NO achievement word superseded fine, which
    is what made this a shipped-verb hole rather than a guard bug.

    ``superseded`` is the one ending that keeps its spelling on both axes, so the
    verb now aligns the pair in the same journaled write.
    """
    old = sdk.create_object("GOAL - plan v1 [supersede-verb]", objectKind="plan",
                            status="live", goalState="active")
    new = sdk.create_object("GOAL - plan v2 [supersede-verb]", objectKind="plan",
                            status="live", goalState="active")

    out = sdk.supersede_knowledge(old["id"], new["id"])
    assert out["superseded"] is True, out
    node = sdk.get_entity(old["id"])
    assert node["status"] == "superseded", node
    assert node["goalState"] == "superseded", (
        "the verb ended the row on `status` and left the achievement word "
        f"running: {node['goalState']!r}")
    # the PR's own acceptance predicate, in the shape the acceptance test uses
    # (retired, current) == (True, False): both surfaces say the goal is OVER
    assert (node["goalState"] in RETIRED_GOAL_STATES,
            not live.is_terminal_status(node.get("status"),
                                        bool(node.get("outdated")))) == (True, False), node

    # ⛔ a row that makes NO achievement claim is left making none — the verb
    # aligns an existing word, it does not invent one
    bare_old = sdk.create_object("PLAN - no claim [supersede-verb]",
                                 objectKind="plan", status="live")
    bare_new = sdk.create_object("PLAN - no claim v2 [supersede-verb]",
                                 objectKind="plan", status="live")
    sdk.supersede_knowledge(bare_old["id"], bare_new["id"])
    bare = sdk.get_entity(bare_old["id"])
    assert bare["status"] == "superseded", bare
    assert bare.get("goalState") in (None, ""), (
        f"the verb stamped an achievement word nobody claimed: {bare.get('goalState')!r}")


# ── 22. the AX half: what the refusals PRESCRIBE ──────────────────────────────
# Round 22's two findings are both about the REPAIR TEXT rather than the rule it
# announces: the rule holds at this head (no door can leave the two axes
# disagreeing), but four messages prescribed a successor value no reader of that
# column can resolve, and the `abandoned` arm first-named a status the door a
# `:Point` goal is using then REFUSES. Both are AX rule 2's third part — the
# prescription must be PERFORMABLE and must name the value the field reads — so
# each test below performs the prescribed call rather than grepping for it, and
# each one fails on the pre-fix text (that non-vacuity is measured in the
# docstrings, against the exact strings the pre-fix code emitted).

def test_the_SUCCESSOR_repairs_prescribe_the_NAME_the_carrier_RESOLVES(sdk):
    """F1 (P2) — `supersededBy` is NAME-keyed; all four repairs said `<successor id>`.

    ``_successor_name_of`` answers *"is this a NAME?"* with presence, not
    resolution, so a caller following the old prescription got an ADMITTED write
    that satisfied the obligation in form only — ``assembly._probe_visible_successors``
    matches ``o.name IN $names``, so an id resolves nothing and the render
    degrades to the name-only annotation printing the raw id. That is the state
    ``_supersede_entity`` calls *"the same defect as storing none"*.

    ⛔ MEASURED before the fix: all four sites emitted
    ``... together with the successor's id, supersededBy=<successor id>`` /
    ``supersededBy=<successor id>`` verbatim, so each ``in`` assertion below was
    FALSE. No assertion anywhere constrained this value, which is why it shipped.
    """
    # (a) the pair arm — `goalState` ends it, `status` still running
    goal = _goal(sdk, tag="f1-pair")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="superseded")
    msg = str(exc.value)
    assert "supersededBy=<successor name>" in msg, msg
    assert "supersededBy=<successor id>" not in msg, msg

    # (b) the successor arm — both fields ended, no successor named
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="superseded",
                             status="superseded")
    msg = str(exc.value)
    assert "supersededBy=<successor name>" in msg, msg
    assert "supersededBy=<successor id>" not in msg, msg
    # ...and the id-taking verb is named, so a caller holding only an id has a
    # one-step route to the name (AX rule 2: say exactly what to do)
    assert "supersede_knowledge(" in msg, msg

    # (c) the reverse arm — `status` ends it, `goalState` still running
    goal2 = _goal(sdk, tag="f1-reverse", goalState="met")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal2["id"], status="superseded")
    msg = str(exc.value)
    assert "supersededBy=<successor name>" in msg, msg
    assert "supersededBy=<successor id>" not in msg, msg

    # (d) the finality arm — a coherent LIVE pair over an ALREADY ENDED node
    ended = sdk.create_point("goal", "GOAL - ended [f1-finality]",
                             goalState="abandoned", status="archived")
    with pytest.raises(ValueError) as exc:
        sdk.create_point("goal", "GOAL - revived [f1-finality]",
                         id=ended["id"], goalState="met", status="live")
    msg = str(exc.value)
    assert "ALREADY ENDED" in msg, msg
    assert "supersededBy=<successor name>" in msg, msg
    assert "supersededBy=<successor id>" not in msg, msg

    # (e) the prescription is PERFORMABLE and lands the value the reader keys on:
    # a NAME is stored verbatim and is what `_probe_visible_successors` resolves.
    target = sdk.create_object("GOAL - successor [f1-name]",
                               objectKind="goal", goalState="active")
    goal3 = _goal(sdk, tag="f1-name")
    sdk.update_knowledge(goal3["id"], goalState="superseded",
                         status="superseded", supersededBy=target["name"])
    node = sdk.get_entity(goal3["id"])
    assert node["supersededBy"] == target["name"], node
    assert node["supersededBy"] != target["id"], node


def test_the_ABANDONED_repair_is_PERFORMABLE_on_a_POINT_goal(sdk):
    """F2 (P3) — the `abandoned` arm first-named `status='archived'`, which a
    `:Point` goal's own door REFUSES, so the repair was a circle.

    ``_ALLOWED_TRANSITIONS`` is ``live -> {retracted, superseded}`` and annotates
    ``archived`` as *"terminal (reserved — no v1 write path)"* (ONTOLOGY §5);
    ``_update_entity``'s Point status arm enforces it. This PR already made the
    SIBLING `superseded` arm carrier-aware and pinned it
    (``test_the_SUCCESSOR_refusal_names_a_repair_the_POINT_doors_ACCEPT``) — the
    `abandoned` arm kept the carrier-blind wording, and every message assertion
    on it used an OBJECT fixture, so nothing measured the Point path.

    ⛔ MEASURED before the fix: the message contained no ``status='retracted'``
    at all (so the first ``in`` assertion was FALSE), and performing the word it
    did first-name on a Point raised
    ``update_entity refused the status transition 'live' -> 'archived'``.
    """
    goal = sdk.create_point("goal", "GOAL - abandoned repair [point]",
                            goalState="active")
    with pytest.raises(ValueError) as exc:
        sdk.update_knowledge(goal["id"], goalState="abandoned")
    msg = str(exc.value)
    assert "REFUSED" in msg, msg
    # the message names the :Point route, the way the sibling arm does
    assert "status='retracted'" in msg, msg
    # ...and it says why `archived` is the wrong half on this carrier
    assert "'archived' is REFUSED there as reserved" in msg, msg
    # ⛔ the pre-fix prescription, PERFORMED, is refused — this is the circle
    with pytest.raises(ValueError) as refused:
        sdk.update_knowledge(goal["id"], status="archived")
    assert "archived" in str(refused.value), refused.value
    # ⛔ and the prescription the message now names LANDS, in ONE write
    sdk.update_knowledge(goal["id"], goalState="abandoned", status="retracted")
    node = sdk.get_point(goal["id"])
    assert node["goalState"] == "abandoned", node
    assert node["status"] == _CANONICAL_ENDED, node
    # both surfaces agree the goal is over — the rule the message serves
    assert (node["goalState"] in RETIRED_GOAL_STATES,
            not live.is_terminal_status(node.get("status"),
                                        bool(node.get("outdated")))) == (True, False), node


def test_the_FINALITY_repair_is_PERFORMABLE_on_a_POINT_goal_too(sdk):
    """F2's second site — the family's last refusal named the same Object-only word.

    ``sdk.py``'s finality arm said *"keep the ending — pass status='archived' with
    goalState='abandoned'"*, which a `:Point` goal cannot perform. The assertion
    at ``test_the_create_door_cannot_RESURRECT_an_ended_goal_by_naming_both`` only
    checks ``ALREADY ENDED``, so this clause was unconstrained.

    ⛔ MEASURED before the fix: the Point route was absent from the message, so
    ``status='retracted'`` was FALSE.
    """
    ended = sdk.create_point("goal", "GOAL - ended [finality-point]",
                             goalState="abandoned", status="retracted")
    with pytest.raises(ValueError) as exc:
        sdk.create_point("goal", "GOAL - revived [finality-point]",
                         id=ended["id"], goalState="met", status="live")
    msg = str(exc.value)
    assert "ALREADY ENDED" in msg, msg
    assert "status='retracted'" in msg, msg
    assert "supersededBy=<successor name>" in msg, msg
    # the ending survived the refused write
    after = sdk.get_point(ended["id"])
    assert after["goalState"] == "abandoned", after
    assert after["status"] == _CANONICAL_ENDED, after
