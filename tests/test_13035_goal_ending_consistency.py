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
    assert "status='deprecated'" in msg            # add the ending to `status`
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
    assert "status='deprecated'" in msg
    assert "goalState='abandoned'" in msg


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
        "the two surfaces disagree about this goal — the #13035 divergence")

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
