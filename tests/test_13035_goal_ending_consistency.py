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
)

REPO = Path(__file__).resolve().parent.parent


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
    assert fresh["status"] == "archived"

    # ⛔ THE FALSE-REFUSAL ARM: this write LANDS ``status="live"`` (the create
    # default, which the caller never names), so the pair it leaves is
    # (live, active) — coherent, even though the node it addresses is ended on
    # ``goalState``. Grading the BAG alone would read the stored status and
    # refuse a write whose result is fine; the helper is handed BOTH endpoints
    # instead, so it never even pays for the read. Proven by handing the probe a
    # view that WOULD refuse (stored deprecated/abandoned): if the door graded
    # the bag, this call raises.
    # ⛔ REVIEW ROUND 3, FINDING 4 CHANGED THIS ARM'S PREMISE. It used to assert
    # that the door NEVER reads the stored half when the caller names both
    # endpoints. That premise is what let `create_point(id=<retired goal>,
    # goalState="met")` write a coherent LIVE pair over an ending: the CREATE
    # always lands a status (the door's default `"live"` when the caller names
    # none), so "both endpoints named" does not mean "the stored ending is
    # irrelevant". The door now reads the stored half on this leg WHATEVER the
    # caller names, and a stored ENDING refuses the resurrection (the test below
    # pins that). What survives, and is what this arm still proves: when the
    # stored node is NOT ended the write LANDS — the read cannot manufacture a
    # false refusal.
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
    assert after["status"] == "archived"
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
    assert retired_by_the_invalidation_path is True
    assert current_to_the_reading_rule is False
    assert retired_by_the_invalidation_path != current_to_the_reading_rule, (
        "the two surfaces AGREE about this goal where they must DIFFER — one "
        "reads it the opposite way from the other (the #13035 divergence, or a "
        "goal neither surface serves)")

    found = verdicts(sdk, goal_id=goal["id"])
    assert len(found) == 1
    assert found[0].invalidation.reason == "goal-retired"
    report = invalidate_stale(sdk, goal_id=goal["id"])
    assert report["counts"]["retired"] == 1
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
    ordinary write: a bag naming BOTH fields, a bag naming NEITHER, and a
    non-terminal ``status`` write are all answered from the bag alone. What does
    cost one read is any ``goalState`` write (the node's ``status`` decides
    whether the achievement word is being revived over an ended goal) and an
    ENDED ``status`` write (the node's ``goalState`` is the other half). Pinned by
    counting calls, because this sits on every SDK write.
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
