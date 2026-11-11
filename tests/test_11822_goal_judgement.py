"""#11822 — the comparator, the judgement claim, and the wiring that makes EP move.

The three proofs the issue asks for, each pinned here:

1. the comparator's decision on a KNOWN input — threshold crossed / not crossed,
   and dwell respected / not;
2. a FLIP produces a SUPERSEDED judgement, never an edited one;
3. the belief NUMBERS on both nodes before and after, with the prior set by
   ``set_point_baseline`` — plus the negative control that makes proof 3 mean
   something: the same shape wired with ``confidence=`` alone leaves BOTH nodes
   at 0.500 and reports ``converged: True``.

The negative control is the load-bearing test in this file. #11822 exists because
a graph can look wired, converge, and compute nothing; a test suite that only
asserts "the goal moved" would not notice a later "simplification" that swapped
the baseline for a ``confidence=`` kwarg on the happy path only.

Runnable embedded:
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
        tests/test_11822_goal_judgement.py -q -p no:cacheprovider
Runs in the docker lane too: the shared ``sdk_factory`` fixture is URI-aware.
"""
from __future__ import annotations

import itertools

import pytest

from tortoise.goal_judgement import (
    COMPARATORS,
    DECISION_TABLE,
    GOAL_STATE_CACHE,
    JUDGEMENT_STATES,
    Reading,
    Target,
    compare,
    compare_values,
    judge,
    judge_and_record,
    latest_judgement,
    latest_readings,
    prior_for,
)

# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sdk(sdk_factory):
    s = sdk_factory()
    yield s
    s.close()


def _world(sdk, *, comparator="<=", threshold=10.0, dwell=1, tag=None):
    """A measured thing, a target, and a goal — all core kinds that exist today.

    ``indicator`` is NOT a core Object kind yet (#7860 D1 promotes it; the
    promotion is out of this issue's scope), so the comparator takes an opaque
    node id and the fixture uses a ``workflow`` Object as the measured thing. The
    comparator is deliberately kind-agnostic: it reads readings about an id, so
    it works the day the ``indicator`` kind lands.

    ``tag`` keeps two worlds APART: an Object's identity is MERGE-by-name
    (``projection/entities.py``), so two un-tagged worlds would collapse onto the
    same nodes and a reading about one would be served for the other.
    """
    suffix = f" [{tag}]" if tag else ""
    indicator = sdk.create_object(f"CI pipeline duration{suffix}",
                                  objectKind="workflow")
    target_node = sdk.create_object(
        f"CI <= 10m{suffix}", objectKind="target",
        comparator=comparator, threshold=threshold, dwell=dwell,
    )
    goal = sdk.create_object(f"CI is fast{suffix}", objectKind="goal",
                             goalState="active")
    return indicator, target_node, goal


def _reading(sdk, indicator, value, *, at, unit="minutes", kind="measurement"):
    return sdk.create_event(
        f"CI run at {at}", kind, value=value, unit=unit, startedAt=at,
        aboutObject=indicator["id"],
    )


def _r(value, at=None):
    return Reading(value=value, raw_value=value, reading_id=f"r-{at or value}",
                   started_at=at)


# ── 1. the decision table is declared, complete, and disjoint ───────────────

def test_decision_table_is_total_over_its_declared_input_space():
    """Exactly ONE row matches every cell of (comparable × candidate × previous
    × dwell). This is the completeness property §5 chose a table for: a missing
    case in a hand-written ``if`` chain is invisible, and here it is a red test."""
    cells = 0
    for comparable, candidate, previous, dwell in itertools.product(
        (True, False), (None, "met", "unmet"), JUDGEMENT_STATES, (True, False)
    ):
        inputs = _inputs(comparable, candidate, previous, dwell)
        matches = [row for row in DECISION_TABLE if row.applies(inputs)]
        assert len(matches) == 1, (
            f"{len(matches)} rows matched {inputs!r} — the table is not total "
            "or not disjoint"
        )
        state, flipped = matches[0].outcome(inputs)
        assert state in JUDGEMENT_STATES
        assert isinstance(flipped, bool)
        cells += 1
    # The FULL product — 2 × 3 × 3 × 2 — including the malformed cells (a reading
    # with a candidate it cannot have). No subset, no "declared space" caveat.
    assert cells == 2 * 3 * 3 * 2


def _inputs(comparable, candidate, previous, dwell):
    from tortoise.goal_judgement import _Inputs

    return _Inputs(comparable=comparable, candidate=candidate,
                   previous=previous, dwell_satisfied=dwell)


def test_every_declared_comparator_has_exactly_one_meaning():
    for spelling in ("<=", "≤", ">=", "≥", "<", ">", "=", "==", "!=", "≠"):
        assert spelling in COMPARATORS
    assert COMPARATORS["≤"] == COMPARATORS["<="]
    assert COMPARATORS["="] == COMPARATORS["=="]
    assert COMPARATORS["≠"] == COMPARATORS["!="]


@pytest.mark.parametrize("comparator,value,threshold,expected", [
    ("<=", 10.0, 10.0, True),
    ("<=", 10.1, 10.0, False),
    ("<", 10.0, 10.0, False),
    (">=", 10.0, 10.0, True),
    (">=", 9.9, 10.0, False),
    (">", 10.0, 10.0, False),
    ("=", 10.0, 10.0, True),
    ("!=", 10.0, 10.0, False),
    ("≤", 9.0, 10.0, True),
    ("≥", 11.0, 10.0, True),
])
def test_compare_values_on_known_inputs(comparator, value, threshold, expected):
    assert compare_values(value, comparator, threshold) is expected


def test_unknown_comparator_is_refused_not_defaulted():
    with pytest.raises(ValueError) as exc:
        Target(comparator="approximately", threshold=10.0)
    assert "Unknown comparator" in str(exc.value)


def test_target_requires_a_comparison_and_a_number():
    with pytest.raises(ValueError):
        Target.from_node({"threshold": 10.0})
    with pytest.raises(ValueError):
        Target.from_node({"comparator": "<="})
    with pytest.raises(ValueError):
        Target(comparator="<=", threshold="ten")
    with pytest.raises(ValueError):
        Target(comparator="<=", threshold=10.0, dwell=0)


def test_target_round_trips_from_a_target_object(sdk):
    _, target_node, _ = _world(sdk, comparator="<=", threshold=10.0, dwell=2)
    target = Target.from_node(target_node)
    assert (target.comparator, target.threshold, target.dwell) == ("<=", 10.0, 2)
    assert target.target_id == target_node["id"]


# ── 1b. threshold crossed / not crossed, dwell respected / not ─────────────

def test_threshold_crossed_is_met_and_not_crossed_is_unmet():
    target = Target(comparator="<=", threshold=10.0)
    met = compare([_r(9.5)], target)
    assert (met.state, met.comparison, met.flipped) == ("met", True, True)
    unmet = compare([_r(12.5)], target)
    assert (unmet.state, unmet.comparison, unmet.flipped) == ("unmet", False, True)


def test_no_reading_is_unknown_and_never_a_silent_unmet():
    target = Target(comparator="<=", threshold=10.0)
    j = compare([], target)
    assert (j.state, j.reason) == ("unknown", "reading-unavailable")


def test_unreadable_value_is_unknown():
    target = Target(comparator="<=", threshold=10.0)
    j = compare([Reading(value=None, raw_value="n/a")], target)
    assert (j.state, j.reason) == ("unknown", "reading-unavailable")
    # a bool is not a number for a numeric comparator
    assert compare([Reading(value=None, raw_value=True)], target).state == "unknown"


def test_dwell_not_respected_holds_the_previous_state_then_flips():
    """dwell=2: the FIRST disagreeing reading holds; the SECOND flips."""
    target = Target(comparator="<=", threshold=10.0, dwell=2)
    first = compare([_r(12.0)], target, previous_state="met")
    assert (first.state, first.reason, first.flipped) == ("met", "dwell-holding", False)
    second = compare([_r(11.0), _r(12.0)], target, previous_state="met")
    assert (second.state, second.reason, second.flipped, second.dwell_satisfied) == (
        "unmet", "state-flipped", True, True,
    )


def test_dwell_needs_the_full_run_of_readings():
    target = Target(comparator="<=", threshold=10.0, dwell=2)
    # only one disagreeing reading exists, so the dwell bar cannot be cleared
    j = compare([_r(11.0)], target, previous_state="met")
    assert j.dwell_satisfied is False
    assert j.state == "met"


def test_dwell_is_irrelevant_when_the_state_does_not_change():
    target = Target(comparator="<=", threshold=10.0, dwell=3)
    j = compare([_r(9.0)], target, previous_state="met")
    assert (j.state, j.reason, j.flipped) == ("met", "state-unchanged", False)


def test_dwell_ignores_an_unreadable_reading_in_the_run():
    target = Target(comparator="<=", threshold=10.0, dwell=2)
    j = compare([_r(11.0), Reading(value=None, raw_value="n/a")], target,
                previous_state="met")
    assert j.dwell_satisfied is False
    assert j.state == "met"


# ── 2. the flip SUPERSEDES; it never edits ─────────────────────────────────

def test_a_flip_supersedes_the_judgement_and_never_edits_it(sdk):
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)
    assert met["judged"]["state"] == "met"
    met_id = met["point_id"]
    met_content = met["content"]
    assert sdk.get_entity(met_id)["claimClass"] == "derived"

    # the reading that flips it
    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    unmet = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert unmet["judged"]["state"] == "unmet"
    assert unmet["written"] is True
    assert unmet["superseded_id"] == met_id
    assert unmet["point_id"] != met_id

    old = sdk.get_entity(met_id)
    assert old["status"] == "superseded"
    # the old claim is UNTOUCHED — same prose, same state — it merely stopped
    # counting. An edit would have rewritten the assertion the graph once held.
    assert old["content"] == met_content
    assert old["judgementState"] == "met"
    assert old["validTo"]  # window closed
    # the new claim is live and says something different
    new = sdk.get_entity(unmet["point_id"])
    assert new["status"] == "live"
    assert new["judgementState"] == "unmet"
    assert new["content"] != met_content
    # and the goal's cache followed the claim
    assert sdk.get_entity(goal["id"])["goalState"] == "unmet"


def test_supersession_window_starts_at_the_reading_instant(sdk):
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)
    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    unmet = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert unmet["windowFrom"] == "reading"
    # §4.7's window intent: the old fact stops being true when the new one
    # became true — the reading's own as-of instant, not the wall clock.
    old = sdk.get_entity(met["point_id"])
    assert str(old["validTo"]).startswith("2026-11-11T11:00:00")


def test_an_unchanged_state_writes_nothing(sdk):
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    first = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert first["written"] is True
    _reading(sdk, indicator, 9.5, at="2026-11-11T11:00:00Z")  # still met
    second = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                             target=target)
    assert second["written"] is False
    assert second["point_id"] == first["point_id"]
    assert second["superseded_id"] is None
    # exactly ONE judgement point exists for this goal
    assert len(_judgement_points(sdk, goal["id"])) == 1


def _judgement_points(sdk, goal_id):
    return sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.judgementState IS NOT NULL AND p.goalId = $g "
        "RETURN p.id, coalesce(p.status,'live')",
        params={"g": goal_id}).result_set


# ── 3. the belief numbers — with the baseline, and the trap without it ────

def test_THE_TRAP_confidence_alone_leaves_both_nodes_at_0500(sdk):
    """The negative control. `create_point(confidence=…)` writes NO prior, so a
    judgement wired that way reports `converged: True` and moves NOTHING.

    MEASURED at this head (embedded): both nodes 0.500, iters 1. This is the
    exact defect #11822 exists to remove, pinned so a later refactor cannot
    restore it while every other test in this file stays green.
    """
    goal = sdk.create_object("CI is fast [trap]", objectKind="goal",
                             goalState="active")
    point = sdk.create_point("statement", "judgement: CI met (confidence=)",
                             claim_class="derived", confidence=0.95)
    sdk.create_operator("IMPL", point["id"], [goal["id"]], direction="->")

    result = sdk.compute_confidence(require_calibration=False)
    assert result["confidences"][point["id"]]["mean"] == pytest.approx(0.5)
    assert result["confidences"][goal["id"]]["mean"] == pytest.approx(0.5)
    assert result["converged"] is True  # and it SAYS so — that is the trap


def test_with_a_baseline_the_belief_moves_and_the_flip_moves_it_back(sdk):
    """The positive control: baseline written → both numbers move, and a flip
    moves the GOAL back down through the supersession."""
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)

    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)
    assert met["written"] is True
    assert met["prior"] == {"alpha": 9.0, "beta": 1.0, "support": 0.9,
                            "source": "system-default"}

    after_met = sdk.compute_confidence()
    assert after_met["converged"] is True
    j_met = after_met["confidences"][met["point_id"]]["mean"]
    g_met = after_met["confidences"][goal["id"]]["mean"]
    # ⛔ the trap this test exists to catch: BOTH at 0.5 is a failed wiring,
    # not a passing test.
    assert j_met > 0.5, f"the judgement's prior did not take effect ({j_met})"
    assert g_met > 0.5, f"the judgement did not reach the goal ({g_met})"
    assert (j_met, round(g_met, 3)) == (pytest.approx(0.9), pytest.approx(0.65, abs=0.02))

    # the flip
    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    unmet = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert unmet["judged"]["state"] == "unmet"
    # the prior is the SUPPORT this judgement gives the goal, so an `unmet`
    # judgement carries 0.1 — not 0.9 "confidence in the word"
    assert unmet["prior"]["support"] == pytest.approx(0.1)
    after_unmet = sdk.compute_confidence()
    j_unmet = after_unmet["confidences"][unmet["point_id"]]["mean"]
    g_unmet = after_unmet["confidences"][goal["id"]]["mean"]
    assert j_unmet < 0.2, f"the new judgement carries no negative belief ({j_unmet})"
    assert g_unmet < g_met, (
        f"the goal did not fall after the flip: {g_met} -> {g_unmet} "
        "(a symmetric prior would RAISE it — measured 0.650 -> 0.692)"
    )
    assert round(g_unmet, 3) == pytest.approx(0.308, abs=0.02)


def test_a_symmetric_prior_is_the_wrong_sign_and_is_pinned(sdk):
    """The negative control for the SECOND trap.

    A judgement whose prior means "confidence in the state word" — 0.9 for both
    `met` and `unmet` — is a loop that argues the opposite of its own
    measurement: measured 0.650 -> 0.692 on a flip to `unmet`. `prior_for` is the
    only place that mapping lives, so this test pins it directly rather than
    rebuilding the wrong wiring (which would leave a second, wrong way to write a
    judgement in the suite).
    """
    assert goal_support("met") > 0.5 > goal_support("unmet")
    assert goal_support("unknown") == pytest.approx(0.5)
    assert goal_support("met") == pytest.approx(1.0 - goal_support("unmet"))
    # a symmetric mapping is exactly what this forbids:
    assert prior_for("met") != prior_for("unmet")
    for bad in (0.0, 0.3, 0.5, 1.0, 1.5, -0.2):
        with pytest.raises(ValueError):
            prior_for("met", bad)


def goal_support(state, strength=None):
    alpha, beta = prior_for(state) if strength is None else prior_for(state, strength)
    return alpha / (alpha + beta)


# ── 4. goalState is a CACHE, never an input to belief ─────────────────────

def test_goal_state_alone_never_moves_a_judgement(sdk):
    """The judgement is a function of (readings, target, previous judgement) and
    NOTHING else. Writing `goalState` by hand changes neither the decision nor
    the belief signal — it is a display cache, which is the inversion #11822
    removes."""
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 12.0, at="2026-11-11T10:00:00Z")

    # a human (or a script) claims the goal is met; the reading says otherwise
    sdk.update_entity(goal["id"], goalState="met")
    judged = judge(sdk, indicator_id=indicator["id"], target=target)
    assert judged.state == "unmet"
    assert judged.previous_state == "unknown"  # goalState was NOT consulted


def test_unknown_writes_no_cache_over_a_humans_word(sdk):
    """A judgement that cannot be reached must not overwrite a word nobody
    measured: `unknown` writes no `goalState` at all."""
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    sdk.update_entity(goal["id"], goalState="blocked")  # a human's word

    result = judge_and_record(sdk, goal_id=goal["id"],
                              indicator_id=indicator["id"], target=target)
    assert result["judged"]["state"] == "unknown"
    assert result["goalState"] is None
    assert GOAL_STATE_CACHE["unknown"] is None
    assert sdk.get_entity(goal["id"])["goalState"] == "blocked"


def test_the_module_never_reads_goal_state():
    """A STRUCTURAL pin: no expression in the module may READ ``goalState``.

    The cache WRITE (``update_entity(..., goalState=…)``), the cache table and
    the dict keys this module RETURNS are all fine — they are outputs. What is
    forbidden is a read: ``node["goalState"]``, ``node.get("goalState")``,
    ``node.goalState``. If one ever appears, the cached word has become an input
    to the belief path — the inversion #11822 removes — so this walk of the AST
    is the pin, not a grep over prose (which cannot tell a docstring from a read).
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "tortoise" / "goal_judgement.py"
    tree = ast.parse(src.read_text())

    def is_goal_state(node) -> bool:
        return isinstance(node, ast.Constant) and node.value == "goalState"

    reads = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and is_goal_state(node.slice):
            reads.append(("subscript", node.lineno))
        elif isinstance(node, ast.Attribute) and node.attr == "goalState":
            reads.append(("attribute", node.lineno))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.args and is_goal_state(node.args[0])):
            reads.append((f"{node.func.attr}()", node.lineno))
    assert reads == [], f"goalState is READ in the belief path: {reads}"


# ── 5. the graph-level read path ──────────────────────────────────────────

def test_latest_readings_orders_newest_first_and_ignores_other_indicators(sdk):
    indicator, _, _ = _world(sdk, tag="a")
    other, _, _ = _world(sdk, tag="b")
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    _reading(sdk, indicator, 12.0, at="2026-11-11T12:00:00Z")
    _reading(sdk, indicator, 11.0, at="2026-11-11T11:00:00Z")
    _reading(sdk, other, 99.0, at="2026-11-11T13:00:00Z")
    got = latest_readings(sdk, indicator["id"])
    assert [r.value for r in got] == [12.0, 11.0, 9.0]
    assert all(r.unit == "minutes" for r in got)


def test_latest_readings_can_be_scoped_by_event_kind(sdk):
    indicator, _, _ = _world(sdk)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    _reading(sdk, indicator, 1.0, at="2026-11-11T11:00:00Z", kind="otherKind")
    assert [r.value for r in latest_readings(sdk, indicator["id"])] == [9.0]
    assert len(latest_readings(sdk, indicator["id"], event_kind=None)) == 2


def test_latest_judgement_reads_the_claim_not_the_cache(sdk):
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    assert latest_judgement(sdk, goal_id=goal["id"]) is None
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    first = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    found = latest_judgement(sdk, goal_id=goal["id"])
    assert found["id"] == first["point_id"]
    assert found["judgementState"] == "met"
    # a superseded judgement is not "latest"
    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    second = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                             target=target)
    assert latest_judgement(sdk, goal_id=goal["id"])["id"] == second["point_id"]


def test_two_goals_sharing_one_indicator_do_not_share_a_dwell_baseline(sdk):
    """D4 makes an indicator's targets 0..n; two goals may share one indicator.

    The previous-judgement lookup is scoped by `goalId` for exactly this: keyed
    on the indicator alone, goal A's last state would become goal B's dwell
    baseline and B would sit in `dwell-holding` over a state it never had.
    """
    indicator, target_node, goal_a = _world(sdk, tag="a")
    _, _, goal_b = _world(sdk, tag="b")
    goal_b["id"] = sdk.create_object("CI is fast [b2]", objectKind="goal",
                                     goalState="active")["id"]
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    judge_and_record(sdk, goal_id=goal_a["id"], indicator_id=indicator["id"],
                     target=target)

    # goal B has never been judged, so its own previous state is `unknown` —
    # NOT goal A's `met`.
    judged_b = judge(sdk, goal_id=goal_b["id"], indicator_id=indicator["id"],
                     target=target)
    assert judged_b.previous_state == "unknown"
    judged_a = judge(sdk, goal_id=goal_a["id"], indicator_id=indicator["id"],
                     target=target)
    assert judged_a.previous_state == "met"
