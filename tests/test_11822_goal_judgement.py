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
    Judgement,
    Reading,
    Target,
    _ensure_judgement_edge,
    _extra_operators,
    _point_status,
    _supersession_evidence,
    compare,
    compare_values,
    judge,
    judge_and_record,
    latest_judgement,
    latest_readings,
    prior_for,
    record_judgement,
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


def test_the_claim_prose_quotes_the_bar_not_the_result(sdk):
    """`12 minutes <= 10` beside `unmet` reads as an assertion that 12 <= 10.

    The claim has to state the STATE and the reading, and quote the target's
    comparator as the bar — a human reading a superseded judgement to answer "why
    did this flip?" must not have to disambiguate the wording.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 12.0, at="2026-11-11T10:00:00Z")
    res = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target, subject="CI is fast")
    text = res["content"]
    assert text.startswith("CI is fast: unmet")
    assert "against <= 10" in text, text
    assert "12" in text and "minutes" in text
    # the reading must NOT be glued to the operator with nothing between them
    assert "minutes <=" not in text, "the comparator is quoted as the result"


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
        "(a symmetric prior transmits NOTHING — measured 0.650 -> 0.650)"
    )
    assert round(g_unmet, 3) == pytest.approx(0.350, abs=0.02)


def test_a_symmetric_prior_is_the_wrong_sign_and_is_pinned(sdk):
    """The negative control for the SECOND trap.

    A judgement whose prior means "confidence in the state word" — 0.9 for both
    `met` and `unmet` — is a loop that DISCARDS the direction of its own
    measurement: measured, a flip to `unmet` left the goal at 0.650 -> 0.650, i.e.
    the reading that broke the goal reached it as exactly as much support as the
    reading that satisfied it. `prior_for` is the only place that mapping lives,
    so this test pins it directly rather than rebuilding the wrong wiring (which
    would leave a second, wrong way to write a judgement in the suite).
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
    NOTHING else. Writing `goalState` by hand changes neither the decision nor the
    previous state — it is a display cache, which is the inversion #11822 removes.

    ⚠️ This pin must be able to FAIL. An earlier version called `judge()` WITHOUT
    `goal_id`, so the goal's cache was unreachable by construction and the
    assertion held for any implementation — including one that reads it. Here the
    goal_id IS passed, the claim says `met`, a human overrides the cache to
    `unmet`, and the next judgement must still see `met` (the claim), not `unmet`
    (the cache).
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    first = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert first["judged"]["state"] == "met"
    assert sdk.get_entity(goal["id"])["goalState"] == "met"

    # a human (or a script) overrides the CACHE
    sdk.update_entity(goal["id"], goalState="unmet")

    judged = judge(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                   target=target)
    assert judged.previous_state == "met", (
        "the cache was read as the previous state — goalState became a belief input"
    )
    assert judged.state == "met"  # the reading still says met


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


# ── 6. review round 1 — the findings, each pinned ─────────────────────────

def _operators_of(sdk, point_id):
    """The operator Points whose INPUT source is `point_id` (source→op, per
    `create_operator`: `(source)-[:INPUT]->(op)`)."""
    return sdk._get_proj().g.query(
        "MATCH (p:Point {id:$i})-[:INPUT]->(op) RETURN op.id",
        params={"i": point_id}).result_set


def test_a_flip_does_not_accumulate_operators(sdk):
    """A flip must not mint a SECOND IMPL arrow for the same evidence.

    `supersede_point` transfers the predecessor's operator onto the successor
    (#6180), so creating one on the successor as well gives the live judgement two
    operators over one piece of evidence — and the transfer MERGEs edges, not
    operators, so nothing collapses them. MEASURED on this exact five-pass
    sequence (met, unmet, met, unmet, met; identical evidence bar the reading),
    goal belief per pass PRE-FIX: 0.6502 → 0.3080 → 0.7197 → 0.2600 → 0.7556 — a
    flapping metric HARDENED the goal instead of tracking it (two operators after
    the first flip, three after the second, …). The same sequence POST-FIX reads
    0.6502 → 0.3498 → 0.6502 → 0.3498 → 0.6502: the belief returns to exactly its
    first value every time.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    beliefs = []
    for i, value in enumerate([9.0, 12.0, 9.0, 12.0, 9.0]):
        _reading(sdk, indicator, value, at=f"2026-11-11T1{i}:00:00Z")
        res = judge_and_record(sdk, goal_id=goal["id"],
                              indicator_id=indicator["id"], target=target)
        ops = _operators_of(sdk, res["point_id"])
        assert len(ops) == 1, (
            f"the live judgement carries {len(ops)} operators after flip {i} — "
            "one piece of evidence, counted once"
        )
        beliefs.append(round(
            sdk.compute_confidence()["confidences"][goal["id"]]["mean"], 4))
    assert beliefs[0] == beliefs[2] == beliefs[4], (
        f"same evidence, different belief by flip count: {beliefs}"
    )
    assert beliefs[1] == beliefs[3]
    assert beliefs[4] > beliefs[3]  # met > unmet


def test_a_refused_strength_writes_nothing_and_the_next_call_works(sdk):
    """A public parameter that can be refused must not leave a half-written
    judgement behind.

    MEASURED before the fix: `strength=0.5` raised AFTER `create_point`, leaving a
    live `claimClass: 'derived'` Point with `judgementState`/`goalId` and no prior.
    Three consequences, all reproduced: `latest_judgement` returned the orphan as
    the current state, the unchanged arm answered `written: False` forever, and
    `compute_confidence()` raised `CalibrationError` for the WHOLE graph.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")

    with pytest.raises(ValueError):
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=target, strength=0.5)

    assert _judgement_points(sdk, goal["id"]) == [], (
        "a refused strength left a judgement point behind"
    )
    retry = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert retry["written"] is True
    assert sdk.compute_confidence()["converged"] is True  # no uncalibrated orphan


def test_a_target_without_an_id_is_refused_by_the_writer(sdk):
    """Two targets on one goal+indicator must not supersede each other.

    Without `targetId` the supersession lookup is scoped by goal+indicator only,
    so each target's pass retires the other's live claim and the goal can hold
    exactly one judgement no matter how many targets D4 allows (measured before
    the fix: A → written, B → superseded A, A again → superseded B …). The pure
    `compare()` still takes a bare Target — it writes nothing; only the WRITER
    demands the name.
    """
    indicator, target_node, goal = _world(sdk)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    bare = Target(comparator="<=", threshold=10.0)
    assert bare.target_id is None
    with pytest.raises(ValueError) as exc:
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=bare)
    assert "target_id" in str(exc.value)

    # two NAMED targets on the same goal+indicator keep one live claim each
    a = Target.from_node(target_node)
    b_node = sdk.create_object("CI >= 30m", objectKind="target",
                               comparator=">=", threshold=30.0, dwell=1)
    b = Target.from_node(b_node)
    ra = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=a)
    rb = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=b)
    assert rb["superseded_id"] is None, "target B superseded target A's judgement"
    live_a = latest_judgement(sdk, goal_id=goal["id"], target_id=a.target_id)
    live_b = latest_judgement(sdk, goal_id=goal["id"], target_id=b.target_id)
    assert live_a["id"] == ra["point_id"] and live_a["status"] == "live"
    assert live_b["id"] == rb["point_id"]
    assert live_a["judgementState"] == "met" and live_b["judgementState"] == "unmet"


def test_from_node_honours_a_declared_zero_dwell_by_refusing_it(sdk):
    """`dwell: 0` must raise, not silently become 1.

    The earlier `int(node.get("dwell") or 1)` mapped a declared `0` (and `""` and
    `False`) to 1 — the target's author set a bar and got "flip on the first
    disagreeing reading", the flapping the field exists to suppress, with no error.
    """
    _, target_node, _ = _world(sdk, dwell=1)
    target_node = dict(target_node, dwell=0)
    with pytest.raises(ValueError):
        Target.from_node(target_node)
    assert Target.from_node({"comparator": "<=", "threshold": 10.0}).dwell == 1


def test_a_dwell_above_the_default_read_page_can_still_be_satisfied(sdk):
    """`dwell` is a count of READINGS, so the read window has to scale with it.

    With the page pinned at 100, a `dwell` of 101 could NEVER be satisfied:
    `_dwell_satisfied` would return False on every pass, every state change would
    score `dwell-holding`, and the goal would never flip — silently.
    """
    indicator, target_node, goal = _world(sdk, dwell=101)
    target = Target.from_node(target_node)
    for i in range(101):
        _reading(sdk, indicator, 12.0,
                 at=f"2026-11-11T{10 + i // 60:02d}:{i % 60:02d}:00Z")
    judged = judge(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                   target=target)
    assert judged.dwell_satisfied is True, "the read window never reached the dwell"
    assert judged.state == "unmet"


def test_the_unchanged_arm_still_reowns_the_cache(sdk):
    """An unchanged state mints nothing — but the cache is still the judgement's.

    Without this, a hand-edited `goalState` contradicts the live claim FOREVER: the
    state never changes again, so no later pass would ever fix it (measured:
    claim `unmet`, human sets `blocked`, every subsequent pass reported
    `written: False, goalState: None` while the graph said `blocked`).
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 12.0, at="2026-11-11T10:00:00Z")
    first = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert first["goalState"] == "unmet"

    sdk.update_entity(goal["id"], goalState="blocked")  # a human's word
    again = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert again["written"] is False            # no new claim
    assert again["goalState"] == "unmet"        # but the cache is re-owned
    assert sdk.get_entity(goal["id"])["goalState"] == "unmet"


def test_a_point_that_lost_its_arrow_is_rewired_on_the_next_pass(sdk):
    """The self-healing arm: a write that failed after its Point landed leaves a
    live judgement with no IMPL arrow, and the unchanged arm must repair it —
    otherwise the goal is never wired again."""
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    res = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=target)
    # simulate the failure: remove the arrow
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$i})-[:INPUT]->(op) DETACH DELETE op",
        params={"i": res["point_id"]})
    assert _operators_of(sdk, res["point_id"]) == []

    again = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert again["written"] is False
    assert again["operator_id"], "the unchanged arm did not repair the arrow"
    assert len(_operators_of(sdk, again["point_id"])) == 1


# ── 7. review round 2 — the shared terminal vocabulary, refusal safety,
#      and the pins the round named as missing ─────────────────────────

def test_a_retracted_judgement_is_not_the_current_state(sdk):
    """The "live" filter must be the SHARED terminal vocabulary, not a local
    `<> 'superseded'`.

    A hand-rolled subset is the #2422/#3301 class. Measured before the fix: a
    `retracted` `met` judgement was returned by `latest_judgement`, the unchanged
    arm re-wrote `goalState='met'` FROM the retracted claim, and the next
    differing pass raised out of `supersede_point`'s lifecycle guard — after its
    point and baseline had landed — instead of judging.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=target)
    sdk.retract_point(met["point_id"])

    assert latest_judgement(sdk, goal_id=goal["id"]) is None, (
        "a retracted judgement is still served as the current state"
    )
    # the next pass must JUDGE rather than raise, and must not resurrect the cache
    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    unmet = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert unmet["written"] is True
    assert unmet["superseded_id"] is None  # nothing to supersede: it is terminal
    assert sdk.get_entity(goal["id"])["goalState"] == "unmet"


def test_a_refused_supersede_leaves_no_second_live_claim(sdk, monkeypatch):
    """A refused `supersede_point` must not leave TWO live judgements on one goal.

    The lifecycle / claim-class guards (#2498 / #2351) raise before any mutation,
    so the point this call just created is removed. Measured before the fix: the
    pass raised, the next pass took the unchanged arm on the NEWEST live claim
    (the orphan) and wired it, so one goal ended with two live judgements and two
    IMPL operators — the double-count this module exists to remove.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                     target=target)

    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    real = sdk.supersede_point

    def refuse(*a, **kw):
        raise ValueError("refused by the lifecycle guard")

    monkeypatch.setattr(sdk, "supersede_point", refuse)
    with pytest.raises(ValueError):
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=target)
    monkeypatch.setattr(sdk, "supersede_point", real)

    live = [r for r in _judgement_points(sdk, goal["id"])
            if r[1] not in {"superseded", "retracted"}]
    assert len(live) == 1, f"a refused supersede left {len(live)} live judgements"


def test_a_refused_baseline_leaves_no_judgement_point(sdk, monkeypatch):
    """The `delete_point` compensation: if the prior cannot be written, the point
    this call created is removed rather than left prior-less.

    A prior-less `derived` Point carrying `judgementState`/`goalId` is served as
    the current state, makes the unchanged arm answer `written: False` forever,
    and raises `CalibrationError` for the WHOLE graph at the next
    `compute_confidence()`.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")

    sdk.set_point_baseline = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("baseline refused"))
    with pytest.raises(RuntimeError):
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=target)
    assert _judgement_points(sdk, goal["id"]) == []


def test_a_supersede_that_fails_after_mutating_keeps_the_migrated_mirror(
        sdk, monkeypatch):
    """⛔ #11953 P2-1: the compensation is only safe BEFORE any mutation lands.

    `supersede_point` emits `PointSuperseded` FIRST and then runs ~15 mutating
    statements, so a driver/infra failure inside that window arrives at the
    success arm's `except Exception` looking exactly like a guard refusal —
    while demanding the OPPOSITE response. Measured before this pin: the handler
    deleted the successor, which by then already owned the migrated `INPUT`
    mirror (the 2a-INPUT leg MERGEs `(new)-[:INPUT]->(op)` and DELETEs
    `(old)-[:INPUT]->(op)`), leaving the operator holding `-[:IMPL]->(goal)` and
    NO input — an operator nothing prunes, because the next pass finds no
    `(p)-[:INPUT]->(op)-[:IMPL]->(g)` match and mints a SECOND one (the
    double-count this module exists to remove).

    So the survivor is asserted, not the exception: the successor must still be
    the live claim AND must still reach the goal through an INPUT→operator.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                     target=target)

    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    real = sdk.supersede_point

    def dies_after_the_mutation(old_id, new_id, **kw):
        real(old_id, new_id, **kw)  # the supersession LANDED (mirror migrated)
        raise RuntimeError("driver died after the journal append")

    monkeypatch.setattr(sdk, "supersede_point", dies_after_the_mutation)
    with pytest.raises(RuntimeError) as excinfo:
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=target)
    monkeypatch.setattr(sdk, "supersede_point", real)

    live = [r for r in _judgement_points(sdk, goal["id"])
            if r[1] not in {"superseded", "retracted"}]
    assert len(live) == 1, (
        f"a post-mutation supersede failure left {len(live)} live judgements"
    )
    wired = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$i})-[:INPUT]->(op:Point)-[:IMPL]->(g) "
        "WHERE g.id = $goal RETURN op.id",
        params={"i": live[0][0], "goal": goal["id"]}).result_set
    assert wired, (
        "the survivor lost the INPUT mirror the supersession had migrated onto "
        "it — its operator now holds an IMPL arrow with no source"
    )
    assert any("#11953" in note for note in getattr(excinfo.value, "__notes__", [])), (
        "a survivor left standing must SAY so on the raised exception"
    )


def test_a_supersede_that_dies_mid_transfer_keeps_the_successor(sdk, monkeypatch):
    """⛔ #11953, the window the STATUS witness misses.

    `supersede_point` re-points the operator's `IMPL` in the 2a leg (~`sdk.py`
    22136), migrates the `INPUT` mirror in 2a-INPUT (~22218), and writes the
    predecessor's `status` LAST (~22712). So a failure between 2a and 2a-INPUT
    leaves the predecessor reading `live` while the successor already carries
    the operator — the case a status-only test deletes, orphaning the operator.

    The witness is therefore the successor's own edges, and this case pins it:
    the predecessor is asserted to STILL BE LIVE, which is exactly why the
    delete must not happen.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)

    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    real = sdk.supersede_point

    def dies_mid_transfer(old_id, new_id, **kw):
        # 2a landed: the operator's IMPL now points at the successor …
        sdk._get_proj().g.query(
            "MATCH (op:Point)-[:IMPL]->(old:Point {id:$old}), "
            "(new:Point {id:$new}) MERGE (op)-[:IMPL]->(new)",
            params={"old": old_id, "new": new_id})
        # … and 2a-INPUT + the status write never happen.
        raise RuntimeError("driver died between the 2a and 2a-INPUT legs")

    monkeypatch.setattr(sdk, "supersede_point", dies_mid_transfer)
    with pytest.raises(RuntimeError):
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=target)
    monkeypatch.setattr(sdk, "supersede_point", real)

    assert sdk.get_entity(met["point_id"])["status"] == "live", (
        "this pin is about the window where the predecessor is STILL live"
    )
    live = [r for r in _judgement_points(sdk, goal["id"])
            if r[1] not in {"superseded", "retracted"}]
    assert len(live) == 2, (
        "the successor took part in the transfer, so it must not be deleted "
        f"(live judgements: {[r[0] for r in live]})"
    )


def test_a_refused_supersede_over_a_non_live_predecessor_still_removes_the_successor(
        sdk):
    """⛔ #11953 — a predecessor that was ALREADY non-live is not evidence of a landing.

    `draft` is a CURRENT status (``live.CURRENT_POINT_STATUS_VALUES``), so
    ``latest_judgement`` selects it — reachable through the public writer, no
    seam needed. The #2351 claim-class guard then refuses a DERIVED successor to
    an ASSERTED claim BEFORE any mutation. Witnessing "the predecessor is not
    `live` now" as "the supersession landed" skipped the compensation and left
    the goal with TWO current claims; because ``latest_judgement`` takes the
    newest, the derived ``unmet`` out-ranked the authored ``met`` and the raised
    judgement became the recorded state — the exact outcome #2351 exists to
    prevent.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    authored = sdk.create_point(
        "statement", "CI is fast — an authored judgement",
        claim_class="asserted", status="draft", judgementState="met",
        goalId=goal["id"], indicatorId=indicator["id"],
        targetId=target.target_id, evaluatedAt="2026-11-11T10:00:00Z",
    )

    # unmatched by the reading, so the pass does not take the unchanged arm and
    # actually reaches `supersede_point` — where #2351 refuses it.
    with pytest.raises(ValueError):
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=target)

    current = [r[0] for r in _judgement_points(sdk, goal["id"])
               if r[1] not in {"superseded", "retracted"}]
    assert current == [authored["id"]], (
        "a refused supersession over a non-live predecessor left a second "
        f"current claim: {current}"
    )


def test_a_status_only_supersession_landing_keeps_the_successor(sdk, monkeypatch):
    """⛔ #11953 — witness 2's ONLY job: a landing that leaves the successor edge-less.

    The predecessor's status is written LAST (~`sdk.py`:22712), and a predecessor
    with nothing to transfer leaves the successor with NO incident edge, so
    witness 1 is silent and the status change is the only evidence. Without
    witness 2 this shape deletes a successor the supersession had already
    accepted, leaving ZERO live judgements.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)

    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    real = sdk.supersede_point

    def stamps_then_dies(old_id, new_id, **kw):
        # the status write landed; no edge leg ran for this shape
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'superseded'",
            params={"id": old_id})
        raise RuntimeError("driver died after the status write")

    monkeypatch.setattr(sdk, "supersede_point", stamps_then_dies)
    with pytest.raises(RuntimeError):
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=target)
    monkeypatch.setattr(sdk, "supersede_point", real)

    assert sdk.get_entity(met["point_id"])["status"] == "superseded"
    live = [r[0] for r in _judgement_points(sdk, goal["id"])
            if r[1] not in {"superseded", "retracted"}]
    assert len(live) == 1, (
        "the status write had landed, so the successor is the claim now — "
        f"deleting it leaves {len(live)} live judgements"
    )
    assert live[0] != met["point_id"]


def test_the_witness_treats_an_unreadable_read_as_landed(sdk, monkeypatch):
    """⛔ #11953 — both read-failure arms: when the witness cannot see, keep.

    The two outcomes are not symmetric: deleting is irreversible, while a
    successor left standing over an un-retired predecessor is recoverable and
    visible (`extraOperators`). An unreadable probe must therefore never authorise
    the delete.
    """
    monkeypatch.setattr(sdk, "_get_proj",
                        lambda: (_ for _ in ()).throw(RuntimeError("proj gone")))
    reason = _supersession_evidence(sdk, "prev", "succ", "live", guard_class=False)
    assert reason is not None
    # ⛔ the note is the only operator-facing report of this arm, so it must name
    # the READ failure rather than assert a mutation that was never observed.
    assert "could not be read" in reason, reason
    assert "moved" not in reason, reason

    monkeypatch.undo()
    monkeypatch.setattr(sdk, "get_entity",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            RuntimeError("entity read gone")))
    reason = _supersession_evidence(sdk, "prev", "succ", "live", guard_class=False)
    assert reason is not None
    assert "could not be read" in reason, reason


def test_the_witness_keeps_the_successor_when_the_baseline_read_failed(
        sdk, monkeypatch):
    """⛔ #11953 — a baseline that could not be READ is not a baseline of "absent".

    "Absent before the call" means the call cannot have retired anything (delete is
    safe); "unreadable" means we know nothing (delete is not safe). Only this pin
    separates the two, so a refactor that collapses them back together flips the
    one branch that chooses between keeping and irreversibly deleting.
    """
    monkeypatch.setattr(sdk, "get_entity",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            RuntimeError("entity read gone")))
    baseline = _point_status(sdk, "a-predecessor")
    monkeypatch.undo()

    reason = _supersession_evidence(sdk, "a-predecessor", "a-successor", baseline,
                                  guard_class=False)
    assert reason is not None, "an unreadable baseline must keep the successor"
    assert "before the call" in reason, reason


def test_a_guard_refusal_over_an_unreadable_graph_still_removes_the_successor(
        sdk, monkeypatch):
    """⛔ #11953 review P1 — the inconclusive arm must not keep a CURRENT claim.

    A lifecycle / claim-class guard raises BEFORE any mutation, so an unreadable
    graph on top of a `ValueError` still means nothing landed. Keeping the
    successor there would leave a CURRENT `derived` claim, and the module's own
    next pass takes the unchanged arm on the newest current claim — so a
    transition #2351 refused becomes the goal's recorded state, durably, and the
    guard never fires again. Pre-fix the guard kept firing; that must stay true.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    authored = sdk.create_point(
        "statement", "CI is fast — an authored judgement",
        claim_class="asserted", status="draft", judgementState="met",
        goalId=goal["id"], indicatorId=indicator["id"],
        targetId=target.target_id, evaluatedAt="2026-11-11T10:00:00Z",
    )

    # the projection is available everywhere EXCEPT the witness's edge probe, so
    # the graph read in `_supersession_evidence` fails while the pass still runs.
    real_proj = sdk._get_proj

    class _Graph:
        def __init__(self, g):
            self._g = g

        def __getattr__(self, name):  # everything else is the real graph
            return getattr(self._g, name)

        def query(self, q, params=None, **kw):
            if "RETURN type(r) LIMIT 1" in q:
                raise RuntimeError("projection unavailable")
            return self._g.query(q, params=params or {}, **kw)

    class _Proj:
        def __init__(self, p):
            self._p = p
            self.g = _Graph(p.g)

        def __getattr__(self, name):  # everything else is the real projection
            return getattr(self._p, name)

    monkeypatch.setattr(sdk, "_get_proj", lambda: _Proj(real_proj()))

    judge_kwargs = dict(goal_id=goal["id"], indicator_id=indicator["id"],
                        target=target)
    with pytest.raises(ValueError):
        judge_and_record(sdk, **judge_kwargs)
    current = [r[0] for r in _judgement_points(sdk, goal["id"])
               if r[1] not in {"superseded", "retracted"}]
    assert current == [authored["id"]], (
        "the successor survived a pre-mutation refusal, so the refused state is "
        f"now the goal's newest current claim: {current}"
    )

    # and the guard still holds on the next pass (it is not silently adopted)
    with pytest.raises(ValueError):
        judge_and_record(sdk, **judge_kwargs)


def test_a_missing_predecessor_is_not_an_unreadable_one(sdk):
    """⛔ #11953 review P1: ABSENT is not UNREADABLE.

    A caller-supplied `previous_point_id` (the public seam kwarg) may name a point
    that does not exist. `supersede_point` then refuses in its lifecycle guard
    (`No point …`), which raises BEFORE any mutation — so nothing was retired and
    the successor must be deleted, exactly as before this change. Treating the
    missing point as an unreadable read keeps the successor, and because the
    goal's OLDER claim is still live and still wired, the next normal pass takes
    the unchanged arm on the leftover: the over-count becomes durable (the defect
    this module exists to remove) rather than one write's worth of noise.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                     target=target)

    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    # the lifecycle guard refuses a missing point with ValueError (pre-mutation)
    with pytest.raises(ValueError):
        record_judgement(
            sdk, goal_id=goal["id"], indicator_id=indicator["id"],
            target=target, judgement=Judgement(state="unmet", reason="flipped"),
            previous_point_id="a-point-that-does-not-exist",
        )

    live = [r[0] for r in _judgement_points(sdk, goal["id"])
            if r[1] not in {"superseded", "retracted"}]
    assert len(live) == 1, (
        "a missing predecessor is a pre-mutation refusal, so the successor must "
        f"be removed; live judgements: {live}"
    )


def test_a_failed_compensation_does_not_mask_the_supersede_refusal(
        sdk, monkeypatch):
    """⛔ #11953 P2-2: `delete_point` can itself fail, and must not replace the why.

    `delete_point` declares `WriteNotLandedError` / `destroy_verified=True`, so
    the cleanup can raise on its own. The arm is `raise exc from cleanup_exc` so
    BOTH are visible; without this pin a later refactor back to `raise
    cleanup_exc` would silently report the cleanup failure as the reason the
    judgement was refused.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                     target=target)

    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    refusal = ValueError("refused by the lifecycle guard")

    def refuse(*a, **kw):
        raise refusal

    def cleanup_cannot_confirm(*a, **kw):
        raise RuntimeError("journal append for the delete did not confirm")

    monkeypatch.setattr(sdk, "supersede_point", refuse)
    monkeypatch.setattr(sdk, "delete_point", cleanup_cannot_confirm)
    with pytest.raises(ValueError) as excinfo:
        judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                         target=target)

    assert excinfo.value is refusal, (
        "the cleanup failure replaced the reason the supersession was refused"
    )
    assert isinstance(excinfo.value.__cause__, RuntimeError), (
        "the cleanup failure must be CHAINED (both visible), not dropped"
    )


def test_strength_actually_reaches_the_prior(sdk):
    """`strength` is a documented knob — a caller "that wants a weaker voice
    passes e.g. 0.6" — so the write path must use it.

    Pinned because replacing `s = float(strength)` with the default left the whole
    suite green: the parameter was only ever exercised by its REFUSAL arm.
    """
    assert prior_for("met", 0.6) == (6.0, 4.0)
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    res = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=target, strength=0.6)
    assert res["prior"] == {"alpha": 6.0, "beta": 4.0,
                            "source": "system-default", "support": 0.6}


def test_a_missing_reading_does_not_retire_a_live_judgement(sdk):
    """A telemetry gap is not a state change.

    Measured before the fix: `met` at goal 0.6502 -> one unreadable reading ->
    `written: True, state: 'unknown'`, the `met` claim SUPERSEDED, and the goal
    driven to 0.5000 — exactly the belief of a graph nobody ever judged, reached
    by transmitting Beta(1,1) as 0.5 SUPPORT through IMPL. The cache path already
    refuses to degrade on `unknown`; the claim path now does too.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=target)
    sdk.compute_confidence()
    before = sdk.get_confidence(goal["id"], require_calibration=False)["mean"]

    # an unreadable reading arrives
    sdk.create_event("CI run (bad)", "measurement", value="n/a", unit="minutes",
                     startedAt="2026-11-11T11:00:00Z", aboutObject=indicator["id"])
    res = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=target)
    assert res["written"] is False
    assert res["judged"]["state"] == "unknown"
    assert res["point_id"] == met["point_id"]
    sdk.compute_confidence()
    after = sdk.get_confidence(goal["id"], require_calibration=False)["mean"]
    assert after == pytest.approx(before), (
        f"a missing reading moved the goal's belief {before} -> {after}"
    )
    assert sdk.get_entity(met["point_id"])["status"] == "live"


def test_an_out_of_order_reading_drops_the_window_instant_not_the_supersession(sdk):
    """A reading whose as-of instant predates the predecessor's window cannot open
    the successor's window (§4.7: an inverted window is satisfiable by no query
    instant), so the instant is dropped and the supersession still lands.

    Unpinned before: replacing the fallback with `raise` left the suite green.

    The predecessor needs a STORED ``validFrom`` for the check to fire, and this
    writer does not put one on the claims it mints (§4.7's closed-vs-half-open
    question is open at #7818) — but a graph built by another writer, a repair, or
    an older path carries one, so the fallback is defensive code for a real shape
    rather than for an unreachable one. Constructed here explicitly.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)
    # the predecessor's window is recorded by another writer
    sdk.update_entity(met["point_id"], validFrom="2026-11-11T10:00:00Z")

    late_judgement = Judgement(
        state="unmet", reason="state-flipped", target=target,
        reading=Reading(value=12.0, raw_value=12.0, reading_id="r-late",
                        started_at="2026-11-11T09:30:00Z"),
        comparison=False, dwell_satisfied=True, flipped=True,
        previous_state="met",
    )
    late = record_judgement(sdk, goal_id=goal["id"], judgement=late_judgement,
                            target=target, indicator_id=indicator["id"],
                            previous_point_id=met["point_id"])
    assert late["windowFrom"] == "successor", (
        "an inverted window instant was NOT dropped — the supersession should "
        "still land, on the successor's own start"
    )
    assert late["superseded_id"] == met["point_id"]
    assert sdk.get_entity(met["point_id"])["status"] == "superseded"
    assert sdk.get_entity(late["point_id"])["status"] == "live"


def test_the_edge_is_scoped_to_the_goal_it_was_recorded_for(sdk):
    """"Has an operator" is not the question — "has one INTO THIS GOAL" is.

    Measured on the goal-blind version: a point already wired into goal A,
    recorded for goal B, reported `written: False` with goal A's `operator_id`
    while `MATCH (op)-[:IMPL]->(B)` was empty — B's belief never moved and the
    result said it was wired.
    """
    indicator, target_node, goal_a = _world(sdk, tag="a")
    _, _, goal_b = _world(sdk, tag="b")
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    a = judge_and_record(sdk, goal_id=goal_a["id"], indicator_id=indicator["id"],
                        target=target)
    assert _impl_into(sdk, a["point_id"], goal_b["id"]) == 0

    # the module's own edge helper must wire goal B as well, not reuse A's arrow
    op_b = _ensure_judgement_edge(sdk, a["point_id"], goal_b["id"])
    assert op_b, "no arrow was created for goal B"
    assert _impl_into(sdk, a["point_id"], goal_b["id"]) == 1
    assert _impl_into(sdk, a["point_id"], goal_a["id"]) == 1
    # and it is idempotent: a second call reuses, never doubles
    assert _ensure_judgement_edge(sdk, a["point_id"], goal_b["id"]) == op_b
    assert _impl_into(sdk, a["point_id"], goal_b["id"]) == 1


def test_a_cross_goal_predecessor_is_refused(sdk):
    """`previous_point_id` decides WHAT gets retired, so its scope is asserted.

    A supersession TRANSFERS the predecessor's operator edges (#6180): retiring
    goal A's claim while re-pointing its arrow onto goal B's chain leaves goal A
    with NO live judgement and a claim scoped to B driving A's belief — measured
    through this exact seam before the assertion existed.
    """
    indicator, target_node, goal_a = _world(sdk, tag="a")
    _, _, goal_b = _world(sdk, tag="b")
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    a = judge_and_record(sdk, goal_id=goal_a["id"], indicator_id=indicator["id"],
                        target=target)
    same = judge(sdk, goal_id=goal_b["id"], indicator_id=indicator["id"],
                 target=target)
    with pytest.raises(ValueError) as exc:
        record_judgement(sdk, goal_id=goal_b["id"], judgement=same, target=target,
                         indicator_id=indicator["id"],
                         previous_point_id=a["point_id"])
    assert "refusing to supersede" in str(exc.value)
    # goal A's claim is untouched and still live; goal B has no judgement
    assert sdk.get_entity(a["point_id"])["status"] == "live"
    assert latest_judgement(sdk, goal_id=goal_b["id"]) is None


def _impl_into(sdk, point_id, goal_id):
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$i})-[:INPUT]->(op:Point)-[:IMPL]->(g) "
        "WHERE g.id = $g RETURN count(op)",
        params={"i": point_id, "g": goal_id}).result_set
    return rows[0][0] if rows else 0


def test_the_unchanged_arm_does_not_claim_a_prior_it_never_wrote(sdk):
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    first = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target, strength=0.6)
    assert first["prior"]["alpha"] == 6.0
    again = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=target)
    assert again["written"] is False
    assert again["prior"] is None, (
        "the unchanged arm reported a prior it did not write"
    )


# ── 8. round 3: the pins round 3 measured as MISSING (five surviving
#      mutations + the AST pin's own blind spot) ─────────────────────────

def test_dwell_reads_the_NEWEST_window_not_the_oldest(sdk):
    """`_dwell_satisfied` must look at the newest `dwell` readings.

    Every earlier dwell test used a list no longer than `dwell`, so newest-vs-oldest
    was indistinguishable and flipping the slice to `readings[-dwell:]` survived the
    whole suite. Here the page is LONGER than the dwell: an old consistent run and a
    recent run that disagrees, which only the newest-first reading finds.
    """
    target = Target(comparator="<=", threshold=10.0, dwell=2)
    # newest first: 12, 11 (disagree) then an older 9, 9 run (agree)
    readings = [_r(12.0), _r(11.0), _r(9.0), _r(9.0)]
    j = compare(readings, target, previous_state="met")
    assert j.dwell_satisfied is True   # the two NEWEST agree on "unmet"
    assert j.state == "unmet"
    # the mirror: recent agreement, older disagreement
    readings2 = [_r(9.0), _r(9.5), _r(12.0), _r(12.0)]
    j2 = compare(readings2, target, previous_state="unmet")
    assert j2.dwell_satisfied is True
    assert j2.state == "met"


def test_judge_scopes_the_previous_state_by_target(sdk):
    """One target's state must not become another target's dwell baseline.

    Mutation that survived before: dropping `target_id` from `judge`'s
    `latest_judgement` call. Only the goal scoping was pinned.
    """
    indicator, target_node, goal = _world(sdk)
    a = Target.from_node(target_node)
    b_node = sdk.create_object("CI >= 30m", objectKind="target",
                               comparator=">=", threshold=30.0, dwell=1)
    b = Target.from_node(b_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    ra = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=a)
    assert ra["judged"]["state"] == "met"
    # target B has never been judged; A's `met` must NOT be its previous state
    judged_b = judge(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                     target=b)
    assert judged_b.previous_state == "unknown"
    judged_a = judge(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                     target=a)
    assert judged_a.previous_state == "met"


def test_an_invalidated_judgement_is_not_the_current_state(sdk):
    """`invalidate_point` sets `outdated=true` and does NOT touch `status`.

    So the vocabularies alone are not enough — the legacy flag is the second dead
    marker (`live._terminal_excluded` ANDs it for exactly this reason). Mutation
    that survived before: dropping the `coalesce(p.outdated, false) = false`
    conjunct. No test ever set `outdated`.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)
    correction = sdk.create_point("statement", "correction", claim_class="derived")
    sdk.invalidate_point(met["point_id"], corrected_by_id=correction["id"])
    node = sdk.get_entity(met["point_id"])
    assert node.get("outdated") is True and node.get("status") == "live", (
        "this pin depends on invalidate_point leaving `status` alone"
    )
    assert latest_judgement(sdk, goal_id=goal["id"]) is None


def test_the_minted_claim_records_the_bar_and_the_reading_it_was_judged_on(sdk):
    """The claim has to say WHAT it was judged against and ON WHAT.

    A superseded judgement is read by a human asking "why did the goal flip?";
    dropping `readingId`/`readingValue` or `comparator`/`threshold`/`dwell` from
    the minted point survived the whole suite before this pin.
    """
    indicator, target_node, goal = _world(sdk, comparator="<=", threshold=10.0,
                                          dwell=2)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    reading = _reading(sdk, indicator, 9.5, at="2026-11-11T11:00:00Z")
    res = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target, subject="CI is fast")
    assert res["written"] is True and res["judged"]["state"] == "met"
    node = sdk.get_entity(res["point_id"])
    assert node["comparator"] == "<="
    assert node["threshold"] == 10.0
    assert node["dwell"] == 2
    assert node["readingId"] == reading["id"]
    assert node["readingValue"] == 9.5
    assert node["goalId"] == goal["id"]
    assert node["indicatorId"] == indicator["id"]
    assert node["targetId"] == target_node["id"]
    assert node["judgementState"] == "met"
    assert node["evaluatedAt"]


def test_the_minted_claim_honours_an_explicit_evaluation_instant(sdk):
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    res = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)
    explicit = record_judgement(
        sdk, goal_id=goal["id"],
        judgement=Judgement(state="unmet", reason="state-flipped", target=target,
                            reading=Reading(12.0, 12.0, "r-explicit",
                                            started_at="2026-11-11T11:00:00Z"),
                            comparison=False, dwell_satisfied=True, flipped=True,
                            previous_state="met"),
        target=target, indicator_id=indicator["id"],
        previous_point_id=res["point_id"],
        evaluated_at="2030-01-01T00:00:00+00:00",
    )
    assert sdk.get_entity(explicit["point_id"])["evaluatedAt"] == \
        "2030-01-01T00:00:00+00:00"


def test_a_non_finite_value_is_unknown_and_never_bricks_the_writer(sdk):
    """`nan`/`inf` are not readings.

    MEASURED before the fix: `nan <= 10` is False, so a telemetry gap was judged
    `unmet` at Beta(1,9) — the gap PUSHED BELIEF DOWN, contradicting this module's
    own "could not tell is not a failure" rule — and `record_judgement` then wrote
    `readingValue=nan`, which the store's numeric-domain guard refuses, so EVERY
    later pass for that indicator raised and no judgement could ever be minted
    again.
    """
    target = Target(comparator="<=", threshold=10.0)
    for bad in ("nan", float("nan"), "inf", float("-inf"), "Infinity"):
        assert compare([Reading(None, bad)][:1], target).state == "unknown"

    indicator, target_node, goal = _world(sdk)
    real = Target.from_node(target_node)
    sdk.create_event("CI run (nan)", "measurement", value="nan", unit="minutes",
                     startedAt="2026-11-11T10:00:00Z", aboutObject=indicator["id"])
    res = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=real)
    assert res["written"] is False
    assert res["judged"]["state"] == "unknown"
    # and a real reading afterwards still works (the writer is not bricked)
    _reading(sdk, indicator, 9.0, at="2026-11-11T11:00:00Z")
    ok = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=real)
    assert ok["written"] is True and ok["judged"]["state"] == "met"


def test_from_node_refuses_a_fractional_dwell_instead_of_truncating_it(sdk):
    """`int(1.9)` is 1 — a declared 1.9-reading window judged against a smaller bar.

    The same "declared value silently coalesced" failure round 1 fixed for `0`,
    arriving through the truncating branch. A string spelling still round-trips.
    """
    base = {"comparator": "<=", "threshold": 10.0}
    with pytest.raises(ValueError):
        Target.from_node(dict(base, dwell=1.9))
    assert Target.from_node(dict(base, dwell="2")).dwell == 2
    assert Target.from_node(dict(base, dwell=2.0)).dwell == 2


def test_a_missing_goal_is_refused_before_anything_is_written(sdk):
    """A stale/typo'd `goal_id` must not leave a live derived claim behind.

    MEASURED before the fix: the only step that refused it was `create_operator`,
    which runs after the point and its baseline — leaving a live
    `claimClass: 'derived'` Point whose `goalId` names nothing, with no arrow.
    """
    indicator, target_node, _goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    with pytest.raises(ValueError) as exc:
        judge_and_record(sdk, goal_id="goal-does-not-exist",
                         indicator_id=indicator["id"], target=target)
    assert "does not exist" in str(exc.value)
    assert _judgement_points(sdk, "goal-does-not-exist") == []


def test_the_unchanged_arm_believes_the_stored_claim_not_the_caller(sdk):
    """The stored claim is the authority on what the current state is.

    MEASURED before the fix: a live claim saying `unmet` + a Judgement whose
    `previous_state` said `met` returned `written: False` and overwrote the cache
    to `met` — a state change that was never recorded, and the live claim kept
    asserting the opposite.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 12.0, at="2026-11-11T10:00:00Z")
    unmet = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                             target=target)
    assert unmet["judged"]["state"] == "unmet"
    lying = Judgement(state="met", reason="state-flipped", target=target,
                      reading=Reading(6.0, 6.0, "r-lie",
                                      started_at="2026-11-11T11:00:00Z"),
                      comparison=True, dwell_satisfied=True, flipped=False,
                      previous_state="met")   # the caller's word is wrong
    res = record_judgement(sdk, goal_id=goal["id"], judgement=lying, target=target,
                           indicator_id=indicator["id"],
                           previous_point_id=unmet["point_id"])
    assert res["written"] is True, (
        "an unchanged arm was taken on the caller's word while the stored claim "
        "asserted the opposite"
    )
    assert sdk.get_entity(unmet["point_id"])["status"] == "superseded"
    assert latest_judgement(sdk, goal_id=goal["id"])["judgementState"] == "met"


def test_the_ast_pin_also_sees_a_constant_indirection():
    """The `goalState` read pin must not be defeated by a module constant.

    `_CACHE_KEY = "goalState"; node.get(_CACHE_KEY)` was invisible to a
    literal-only walk. This asserts the pin's own coverage: no module-level string
    constant may equal "goalState", and the module's Cypher must not mention it.
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "tortoise"
           / "goal_judgement.py")
    tree = ast.parse(src.read_text())
    constants = set()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            constants.add(node.value.value)
        if (isinstance(node, ast.AnnAssign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            constants.add(node.value.value)
    assert "goalState" not in constants, (
        "a module-level constant aliases goalState — the AST read pin can be "
        "defeated through it"
    )
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and "goalState" in node.value and "MATCH" in node.value):
            raise AssertionError("a Cypher statement mentions goalState")


def test_the_unknown_arm_never_echoes_a_superseded_predecessor(sdk):
    """`written: False` must not hand back a DEAD claim's id as "the judgement".

    A caller-supplied `previous_point_id` is unvalidated input; on the `unknown`
    arm it must not be echoed, or a caller reading `point_id` (the documented
    "the judgement's point") gets a superseded reference that looks current.
    Mutation this pin exists for: restoring `previous_point_id or (...)` at the
    `unknown` return left all 62 tests green.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    met = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                          target=target)
    _reading(sdk, indicator, 12.0, at="2026-11-11T11:00:00Z")
    unmet = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                             target=target)
    assert unmet["superseded_id"] == met["point_id"]
    assert sdk.get_entity(met["point_id"])["status"] == "superseded"

    unknown = Judgement(state="unknown", reason="reading-unavailable",
                        target=target, previous_state="unmet")
    res = record_judgement(sdk, goal_id=goal["id"], judgement=unknown,
                           target=target, indicator_id=indicator["id"],
                           previous_point_id=met["point_id"])   # a DEAD claim
    assert res["written"] is False
    assert res["point_id"] != met["point_id"], (
        "the unknown arm echoed a superseded predecessor as the current point"
    )
    assert res["point_id"] == unmet["point_id"]


def test_a_double_counted_arrow_is_reported_not_hidden(sdk):
    """`extraOperators` in the write result must reflect reality.

    `_ensure_judgement_edge` can only ensure "at least one" arrow (there is no
    operator-delete API), so a second IMPL into the same goal — from another
    writer, a restored graph, or a transferred arrow — has to be VISIBLE rather
    than collapsed to a healthy-looking empty list. Mutation this pin exists for:
    `"extraOperators": []` left all 62 tests green, and `extraOperators` was
    asserted nowhere.
    """
    indicator, target_node, goal = _world(sdk)
    target = Target.from_node(target_node)
    _reading(sdk, indicator, 9.0, at="2026-11-11T10:00:00Z")
    res = judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                           target=target)
    assert res["extraOperators"] == []

    # another writer adds a second arrow into the same goal
    dupe = sdk.create_operator("IMPL", res["point_id"], [goal["id"]],
                               direction="->")
    assert _extra_operators(sdk, res["point_id"], goal["id"],
                            res["operator_id"]) == [dupe["id"]]
    # and the helper still reuses rather than doubling again
    assert _ensure_judgement_edge(sdk, res["point_id"], goal["id"]) == \
        res["operator_id"]
    assert _impl_into(sdk, res["point_id"], goal["id"]) == 2

    # the UNCHANGED arm is the one that meets such a graph, so IT must report the
    # double-count too (mutation: `"extraOperators": []` survived while only the
    # written arm was pinned)
    again = judge_and_record(sdk, goal_id=goal["id"],
                             indicator_id=indicator["id"], target=target)
    assert again["written"] is False
    assert set([again["operator_id"], *again["extraOperators"]]) == {
        res["operator_id"], dupe["id"]}
    assert again["extraOperators"], "a double-count was collapsed to []"
