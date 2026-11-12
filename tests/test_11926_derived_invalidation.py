"""#11926 — a derived state is retired when the thing it was derived from changes.

Three inputs can move under a judgement (#11822 writes it, this suite retires it):
the TARGET (its bar), the DEFINITION (what the indicator's number means), and the
GOAL (the commitment the state is about). Before #11926 nothing retried any of
them, so a goal could sit at ``met`` after the threshold changed, two readings
under two definitions could read as a trend, and a retired goal's states kept
counting.

Every fact asserted here was MEASURED on this head, embedded OR docker lane (the
shared ``sdk_factory`` is URI-aware); ``TORTOISE_TEST_CARVE_OUT=1`` selects the
embedded carve-out. The suite is hermetic: no network, no model, no clock
dependence — every instant it compares is supplied by the test.
"""
from __future__ import annotations

import pytest

from tortoise.derived_invalidation import (
    RETIRED_GOAL_STATES,
    STALE_REASONS,
    Invalidation,
    invalidate,
    invalidate_stale,
    mark_definition_change,
    reconcile_goal_state_cache,
    verdicts,
)
from tortoise.goal_judgement import (
    DEFINITION_BOUNDARY_PROP,
    Target,
    judge,
    judge_and_record,
    latest_judgement,
    latest_readings,
    live_judgements,
)

# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sdk(sdk_factory):
    s = sdk_factory()
    yield s
    s.close()


def _world(sdk, *, tag, comparator="<=", threshold=10.0, dwell=1):
    """A measured thing, a target and a goal. ``tag`` keeps two worlds apart."""
    indicator = sdk.create_object(f"CI pipeline duration [{tag}]",
                                 objectKind="workflow")
    target = sdk.create_object(
        f"CI <= {threshold:g}m [{tag}]", objectKind="target",
        comparator=comparator, threshold=threshold, dwell=dwell,
    )
    goal = sdk.create_object(f"CI is fast [{tag}]", objectKind="goal",
                             goalState="active")
    return indicator, target, goal


def _reading(sdk, indicator, value, *, at, unit="minutes"):
    return sdk.create_event(
        f"CI run {at}", "measurement", value=value, unit=unit, startedAt=at,
        aboutObject=indicator["id"],
    )


def _judge(sdk, indicator, target, goal, *, value=9.0, at="2026-11-11T10:00:00Z",
           **kwargs):
    _reading(sdk, indicator, value, at=at)
    return judge_and_record(sdk, goal_id=goal["id"], indicator_id=indicator["id"],
                            target=Target.from_node(target), **kwargs)


def _only(verdict_list):
    assert len(verdict_list) == 1, verdict_list
    return verdict_list[0]


# ── 1. the reason set is CLOSED, and every member is reachable ─────────────

def test_the_reason_set_is_closed_and_every_reason_is_reachable(sdk):
    """§5's posture applied to the invalidation half: a declared set, proven
    reachable — a hardcoded ``if`` chain's missing case is invisible, this one's
    is either a red test or a reason nobody can build a graph for."""
    reachable: dict[str, str] = {}

    # goal-retired
    i, t, g = _world(sdk, tag="reason-goal")
    _judge(sdk, i, t, g)
    sdk.update_entity(g["id"], goalState="abandoned")
    reachable["goal-retired"] = _only(verdicts(sdk, goal_id=g["id"])).invalidation.reason

    # target-retired
    i, t, g = _world(sdk, tag="reason-target")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], status="superseded")
    reachable["target-retired"] = _only(verdicts(sdk, goal_id=g["id"])).invalidation.reason

    # indicator-retired
    i, t, g = _world(sdk, tag="reason-indicator")
    _judge(sdk, i, t, g)
    sdk.update_entity(i["id"], status="superseded")
    reachable["indicator-retired"] = _only(verdicts(sdk, goal_id=g["id"])).invalidation.reason

    # definition-changed
    i, t, g = _world(sdk, tag="reason-definition")
    _judge(sdk, i, t, g)
    mark_definition_change(sdk, i["id"], at="2026-11-12T00:00:00+00:00")
    reachable["definition-changed"] = _only(verdicts(sdk, goal_id=g["id"])).invalidation.reason

    # target-revised
    i, t, g = _world(sdk, tag="reason-revised")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=12.0)
    reachable["target-revised"] = _only(verdicts(sdk, goal_id=g["id"])).invalidation.reason

    assert set(reachable) == set(STALE_REASONS), (
        "a declared reason has no graph that produces it, or a reason was produced "
        f"that the set does not name: {sorted(reachable)} vs {sorted(STALE_REASONS)}"
    )


def test_an_undeclared_reason_cannot_be_constructed():
    """The closed set is enforced at construction, not by convention."""
    with pytest.raises(AssertionError, match="closed"):
        Invalidation(judgement_id="p", reason="because-i-said-so", detail="…")


def test_the_retired_goal_states_are_a_subset_of_the_goal_state_vocabulary():
    """`RETIRED_GOAL_STATES` is a SUBSET of the closed #7856 vocabulary, not a
    second vocabulary: a word nobody can write is not a retirement signal, and a
    retirement signal the vocabulary does not carry is a local dialect."""
    from tortoise.sdk import GOAL_STATE_VALUES
    assert RETIRED_GOAL_STATES < GOAL_STATE_VALUES
    # the words this module must NEVER read as a retirement, named so a later
    # "simplification" to "any terminal-looking word" reds here.
    assert not (RETIRED_GOAL_STATES & {"met", "unmet", "blocked", "active", "guideline"})


# ── 2. no false positives: a healthy judgement is left alone ───────────────

def test_a_healthy_judgement_is_not_stale_and_retires_nothing(sdk):
    i, t, g = _world(sdk, tag="healthy")
    made = _judge(sdk, i, t, g)
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.judgement_id == made["point_id"]
    # NEITHER field: healthy is not "undecidable", and it is not stale either
    assert v.invalidation is None
    assert v.undecidable is None
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["stale"] == []
    assert report["undecidable"] == []
    assert sdk.get_entity(made["point_id"])["status"] == "live"


# ── 3. the TARGET's bar is revised (capability 8) ─────────────────────────

@pytest.mark.parametrize(
    "change, expected",
    [
        ({"threshold": 12.0}, "threshold 10 -> 12"),
        ({"comparator": ">="}, "comparator '<=' -> '>='"),
        ({"dwell": 3}, "dwell 1 -> 3"),
        # the comparator spelled with the unicode operator is the SAME bar: a
        # spelling change is not a revision (the writer canonicalises both).
        ({"comparator": "≤"}, None),
    ],
)
def test_revising_any_field_of_the_bar_retires_the_state(sdk, change, expected):
    i, t, g = _world(sdk, tag=f"bar-{'-'.join(change)}")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], **change)
    v = _only(verdicts(sdk, goal_id=g["id"]))
    if expected is None:
        assert v.invalidation is None, v.invalidation
        return
    assert v.invalidation.reason == "target-revised"
    assert expected in v.invalidation.detail, v.invalidation.detail


def test_the_headline_case_a_goal_cannot_sit_at_met_after_the_threshold_moves(sdk):
    """The issue's own table row, end to end: the goal reads ``met``, the bar
    moves, and after the sweep the goal no longer claims to be met — because the
    state that said so stopped counting and the CACHE followed it."""
    i, t, g = _world(sdk, tag="headline")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    sdk.update_entity(t["id"], threshold=8.0)  # the very same 9-minute reading now fails
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    claim = sdk.get_entity(made["point_id"])
    # the claim stopped counting, with a recorded window end
    assert claim["status"] == "retracted"
    assert claim["validTo"]
    # the REASON is in the answer, not on the claim — see the module block above
    # STALE_REASONS for the measurement that decided that (a live-only prop reds
    # `check-consistency` on a faithful graph, permanently).
    assert report["stale"][0]["reason"] == "target-revised"
    assert "threshold 10 -> 8" in report["stale"][0]["detail"]
    # ⛔ AND THE CLAIM IS NOT REWRITTEN: a retirement records that it stopped
    # counting, never that it said something else.
    assert claim["judgementState"] == "met"
    assert claim["threshold"] == 10.0
    assert claim["content"] == made["content"]
    # the read paths agree it is gone, and the cache no longer claims `met`
    assert latest_judgement(sdk, goal_id=g["id"]) is None
    assert sdk.get_entity(g["id"]).get("goalState") is None


def test_a_removed_threshold_is_a_revision_not_a_crash(sdk):
    """`Target.from_node` refuses a target with no threshold. This reader must
    NOT: a bar that was deleted is exactly the change to report."""
    i, t, g = _world(sdk, tag="removed-bar")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=None)
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "target-revised"
    assert "unreadable" in v.invalidation.detail


# ── 4. the TARGET / INDICATOR are retired ─────────────────────────────────

def test_a_superseded_target_retires_the_state_it_judged(sdk):
    i, t, g = _world(sdk, tag="target-superseded")
    made = _judge(sdk, i, t, g)
    successor = sdk.create_object("CI <= 8m [target-superseded]",
                                  objectKind="target", comparator="<=",
                                  threshold=8.0)
    sdk.update_entity(t["id"], status="superseded", supersededBy=successor["id"])
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "target-retired"
    assert "superseded" in v.invalidation.detail
    assert invalidate_stale(sdk, goal_id=g["id"])["counts"]["retired"] == 1
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


def test_a_target_that_no_longer_exists_is_retired_state(sdk):
    i, t, g = _world(sdk, tag="target-gone")
    made = _judge(sdk, i, t, g)
    sdk.delete_entity(t["id"])
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "target-retired"
    assert "no longer exists" in v.invalidation.detail
    invalidate_stale(sdk, goal_id=g["id"])
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


def test_a_retired_indicator_retires_the_state_derived_from_its_readings(sdk):
    i, t, g = _world(sdk, tag="indicator-retired")
    made = _judge(sdk, i, t, g)
    sdk.update_entity(i["id"], status="superseded")
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "indicator-retired"
    invalidate_stale(sdk, goal_id=g["id"])
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


# ── 4b. an UNNAMED scope is undecidable, never a retirement ──────────────

@pytest.mark.parametrize("missing, word", [
    ("goalId", "names no goal"),
    ("indicatorId", "names no indicator"),
])
def test_a_claim_that_names_no_goal_or_indicator_is_UNDECIDABLE(sdk, missing, word):
    """⛔ REVIEW P1, reproduced. `_Nodes.get` maps a falsy id to MISSING, which
    `_retirement` reads as "it no longer exists" — so an unnamed dimension was
    TERMINALLY retired with a claim about a node that was never named. The target
    branch already guarded this; the goal and indicator branches did not.

    The shape is reachable from real data for the indicator: claims written by the
    pre-#11965 writer carry `judgementState` + `goalId` and NO `indicatorId`, and
    an unscoped `invalidate_stale()` is precisely the call that meets them.
    """
    i, t, g = _world(sdk, tag=f"unnamed-{missing}")
    made = _judge(sdk, i, t, g)
    # remove the dimension entirely (a raw write: neither door will mint one)
    sdk._get_proj().g.query(
        f"MATCH (p:Point {{id:$i}}) REMOVE p.{missing}", params={"i": made["point_id"]})
    # ⛔ UNSCOPED on purpose: a scoped read filters ON the dimension that was
    # removed, so the claim would simply not be found — which is why this shape
    # reaches an unscoped repair sweep and nothing else.
    scoped = [v for v in verdicts(sdk) if v.judgement_id == made["point_id"]]
    v = _only(scoped)
    assert v.invalidation is None, v.invalidation
    assert word in v.undecidable
    report = invalidate_stale(sdk)
    assert report["stale"] == []
    assert report["undecidable"][0]["judgementId"] == made["point_id"]
    assert sdk.get_entity(made["point_id"])["status"] == "live"


def test_a_failed_read_of_the_DEFINITION_BOUNDARY_is_undecidable_not_healthy(sdk):
    """⛔ REVIEW P1, reproduced. `definition_boundary()` folds "the read failed"
    into the same `None` as "no boundary was declared" — fine for the derivation,
    fatal for an invalidation: a stale claim would read HEALTHY on a transient
    read failure. `invalidation_for` therefore reads the property from the
    indicator node it already fetched, and this test fails the INDICATOR read to
    prove the difference is still visible (it is `undecidable`, never `None`)."""
    i, t, g = _world(sdk, tag="boundary-read-fail")
    made = _judge(sdk, i, t, g)
    mark_definition_change(sdk, i["id"], at="2026-11-12T00:00:00+00:00")
    # control: with healthy reads the claim IS stale
    assert _only(verdicts(sdk, goal_id=g["id"])).invalidation.reason == "definition-changed"

    real_get = sdk.get_entity

    def failing(node_id, *a, **kw):
        if node_id == i["id"]:
            raise RuntimeError("transient read failure")
        return real_get(node_id, *a, **kw)

    sdk.get_entity = failing
    try:
        v = _only(verdicts(sdk, goal_id=g["id"]))
        assert v.invalidation is None
        assert "could not be read" in v.undecidable
        report = invalidate_stale(sdk, goal_id=g["id"])
        assert report["counts"]["retired"] == 0
        assert report["counts"]["undecidable"] == 1
    finally:
        sdk.get_entity = real_get
    assert sdk.get_entity(made["point_id"])["status"] == "live"


def test_the_boundary_is_read_from_the_NODE_already_fetched(sdk):
    """⛔ REVIEW cycles 2–3, P2. TWO earlier versions of this pin were wrong, and
    both wrongnesses are worth recording because the fix is a BEHAVIOURAL
    invariant rather than a spy on a name:

    * cycle 1's test failed the whole `get_entity` read for the indicator, which
      also fails the `indicator-retired` arm — whose message is verbatim "the
      indicator … could not be read" — so its assertion held with no boundary
      declared at all (reproduced by review);
    * cycle 2's test patched the `goal_judgement.definition_boundary` MODULE
      ATTRIBUTE. That only bites a refactor that calls the helper by attribute
      lookup; the spelling a maintainer actually writes
      (`from .goal_judgement import definition_boundary`, which is how this file's
      own import block reads) binds at import, and the whole suite stayed GREEN
      under that mutation (measured by review: 59 passed).

    So the invariant pinned here is the FACT both refactors would break: the
    boundary comes from the node this function has ALREADY fetched, i.e. the
    indicator is read ONCE per verdict. A second read through the helper is the
    silent-healthy hole of cycle 1 — the helper folds "the read failed" into "no
    boundary declared" — and it is counted here regardless of how it is spelled.
    """
    i, t, g = _world(sdk, tag="boundary-source")
    _judge(sdk, i, t, g, at="2026-11-11T10:00:00Z")
    mark_definition_change(sdk, i["id"], at="2026-11-12T00:00:00+00:00")

    reads: list[str] = []
    real_get = sdk.get_entity

    def counting(node_id, *a, **kw):
        reads.append(node_id)
        return real_get(node_id, *a, **kw)

    sdk.get_entity = counting
    try:
        v = _only(verdicts(sdk, goal_id=g["id"]))
    finally:
        sdk.get_entity = real_get
    assert v.invalidation is not None, v.undecidable
    assert v.invalidation.reason == "definition-changed"
    assert reads.count(i["id"]) == 1, (
        f"the indicator was read {reads.count(i['id'])} times for one verdict — "
        "the boundary must come from the node the retirement check already "
        "fetched: a second read through `definition_boundary()` folds a read "
        "failure into 'no boundary declared' and makes a stale claim read healthy")


# ── 5. the GOAL is retired (capability 12's derived half) ─────────────────

@pytest.mark.parametrize("retirement", [
    {"goalState": "abandoned"},
    {"goalState": "superseded"},
    {"status": "archived"},
])
def test_retiring_the_goal_retires_the_states_derived_from_it(sdk, retirement):
    i, t, g = _world(sdk, tag=f"goal-{'-'.join(retirement)}")
    made = _judge(sdk, i, t, g)
    sdk.update_entity(g["id"], **retirement)
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "goal-retired"
    invalidate_stale(sdk, goal_id=g["id"])
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


@pytest.mark.parametrize("state", ["met", "unmet", "blocked", "active", "guideline"])
def test_a_live_goal_state_is_never_read_as_a_retirement(sdk, state):
    """The negative control for the row above: four of these five are ordinary
    states of a PURSUED commitment, and ``guideline`` is a demoted target (#6792).
    Reading any of them as retirement would retire every blocked goal's evidence."""
    i, t, g = _world(sdk, tag=f"live-{state}")
    _judge(sdk, i, t, g)
    sdk.update_entity(g["id"], goalState=state)
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is None, v.invalidation


def test_a_goal_retired_by_its_STATUS_does_not_keep_a_derived_word(sdk):
    """⛔ REVIEW P1, reproduced. The cache gate must be the WORD, not the reason:
    retiring a goal by its ``status`` used to skip the reconcile entirely (the
    reason was ``goal-retired``), so a goal carrying a derived ``met`` kept it
    forever — the issue's own headline surviving one of its three named inputs,
    and unrepairable afterwards because no live judgement remains for a later
    sweep to reconcile."""
    i, t, g = _world(sdk, tag="goal-status-word")
    _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    sdk.update_entity(g["id"], status="archived")
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    assert report["invalidated"][0]["cache"]["cleared"] is True
    assert sdk.get_entity(g["id"]).get("goalState") is None


@pytest.mark.parametrize("word", ["blocked", "guideline", "active"])
def test_a_word_this_engine_never_wrote_is_PRESERVED_by_a_retirement(sdk, word):
    """⛔ REVIEW P1, the other direction. A ``blocked`` goal used to lose its word
    to a ``target-revised`` retirement that cleared the cache under it — erasing a
    decision nobody measured, which is the inversion this module's own docstring
    says it prevents."""
    i, t, g = _world(sdk, tag=f"preserve-{word}")
    _judge(sdk, i, t, g)
    sdk.update_entity(g["id"], goalState=word)
    sdk.update_entity(t["id"], threshold=8.0)
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    assert report["invalidated"][0]["cache"] == {
        "checked": True, "goalState": word, "preserved": True,
        "from": None, "cleared": False}
    assert sdk.get_entity(g["id"])["goalState"] == word


def test_a_retired_goal_keeps_its_own_word_and_the_cache_never_overwrites_it(sdk):
    """⛔ THE ORDERING TRAP. The cache is reconciled for every OTHER reason; doing
    it here would replace a human's ``abandoned`` with a derived word — the
    inversion #11822's `GOAL_STATE_CACHE` rule exists to prevent."""
    i, t, g = _world(sdk, tag="goal-word")
    _judge(sdk, i, t, g)
    sdk.update_entity(g["id"], goalState="abandoned")
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    assert report["invalidated"][0]["cache"]["preserved"] is True
    assert sdk.get_entity(g["id"])["goalState"] == "abandoned"


# ── 6. the DEFINITION changes (capability 9) ─────────────────────────────

def test_a_definition_change_retires_the_state_derived_under_the_old_one(sdk):
    i, t, g = _world(sdk, tag="definition")
    made = _judge(sdk, i, t, g, at="2026-11-11T10:00:00Z")
    marked = mark_definition_change(sdk, i["id"], at="2026-11-12T00:00:00+00:00",
                                    definition="p95 latency, not the mean")
    assert marked[DEFINITION_BOUNDARY_PROP] == "2026-11-12T00:00:00+00:00"
    assert marked["previous"] is None
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "definition-changed"
    # the boundary IS the instant the justification went away, so it is the
    # window end — not the wall clock at sweep time.
    assert v.invalidation.effective_at == "2026-11-12T00:00:00+00:00"
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["invalidated"][0]["validTo"] == "2026-11-12T00:00:00+00:00"
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


def test_a_backdated_definition_change_still_retires_a_later_judgement(sdk):
    """"The definition changed on Monday, noticed on Tuesday" is a real
    sequence: the claim is MINTED after the boundary but its READING was taken
    before it. Judging that claim on its mint clock alone would call it current,
    so the test pins both facts — and then pins that the finding still fires."""
    i, t, g = _world(sdk, tag="backdated")
    made = _judge(sdk, i, t, g, at="2026-11-11T10:00:00Z")
    claimed = sdk.get_entity(made["point_id"])
    # the boundary sits BETWEEN the reading and the mint: the case where the
    # mint clock would have been wrong.
    mark_definition_change(sdk, i["id"], at="2026-11-11T12:00:00+00:00")
    assert claimed["readingAsOf"] == "2026-11-11T10:00:00Z"
    assert claimed["evaluatedAt"] > "2026-11-11T12:00:00+00:00"
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "definition-changed"


def test_a_judgement_derived_EXACTLY_AT_the_boundary_STANDS(sdk):
    """⛔ REVIEW cycle 2, P1, reproduced. `instant_at_or_after` is `>=`, so at
    EQUALITY both directions are true and the boundary looked "later" — retiring a
    claim the derivation half of this same change ADMITS (the reading filter is
    `>= boundary`), and starting a retire/re-derive loop: the documented
    `judge_and_record` follow-up re-mints the claim and the next sweep retires it
    again, growing a Point and an operator per cycle.

    The equality cell is the one neither of the strictly-before / strictly-after
    tests covers, and it is the cell that decides which side of the module is
    right."""
    i, t, g = _world(sdk, tag="definition-equal")
    instant = "2026-11-11T10:00:00Z"
    made = _judge(sdk, i, t, g, at=instant)
    mark_definition_change(sdk, i["id"], at=instant)
    assert sdk.get_entity(made["point_id"])["readingAsOf"] == instant
    # the derivation half admits a reading taken exactly AT the boundary ...
    assert len(latest_readings(sdk, i["id"], since=instant)) == 1
    # ... so the retirement half must not retire the claim built from it
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is None, v.invalidation
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 0
    assert sdk.get_entity(made["point_id"])["status"] == "live"


def test_a_judgement_derived_after_the_boundary_stands(sdk):
    """The negative control for the row above: a reading taken after the
    definition changed is comparable, so its judgement is NOT stale."""
    i, t, g = _world(sdk, tag="definition-after")
    mark_definition_change(sdk, i["id"], at="2026-11-11T09:00:00+00:00")
    made = _judge(sdk, i, t, g, at="2026-11-11T10:00:00Z")
    assert made["written"] is True
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is None, v.invalidation


def test_readings_from_before_the_definition_boundary_are_not_served(sdk):
    """The OTHER half of capability 9: *"two readings under different
    definitions read as a trend"*. The derivation must not average across the
    boundary, so the pre-boundary reading is not even offered to it."""
    i, t, g = _world(sdk, tag="boundary-readings")
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")
    _reading(sdk, i, 9.2, at="2026-11-11T11:00:00Z")
    assert len(latest_readings(sdk, i["id"])) == 2
    mark_definition_change(sdk, i["id"], at="2026-11-11T10:30:00+00:00")
    served = latest_readings(sdk, i["id"], since="2026-11-11T10:30:00+00:00")
    assert [r.value for r in served] == [9.2]
    # and the DERIVATION applies the same rule without the caller repeating it
    judged = judge(sdk, indicator_id=i["id"], target=Target.from_node(t),
                   goal_id=g["id"])
    assert judged.reading.value == 9.2


def test_a_dwell_window_cannot_span_the_definition_boundary(sdk):
    """``dwell`` counts consecutive agreeing readings. Counting two readings
    taken under two definitions as agreement is the defect, so the pre-boundary
    reading does not count toward the window."""
    i, t, g = _world(sdk, tag="boundary-dwell", dwell=2)
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")   # old definition
    _reading(sdk, i, 9.1, at="2026-11-11T11:00:00Z")   # new definition
    mark_definition_change(sdk, i["id"], at="2026-11-11T10:30:00+00:00")
    judged = judge(sdk, indicator_id=i["id"], target=Target.from_node(t),
                   goal_id=g["id"])
    assert judged.dwell_satisfied is False, (
        "the dwell window counted a reading taken under the previous definition"
    )


def test_a_dwell_window_STRADDLING_the_boundary_retires_the_claim(sdk):
    """⛔ REVIEW cycle 5, P2, reproduced. A `dwell=2` flip rests on BOTH readings it
    inspected, but the claim carried only the NEWEST one's instant — so a boundary
    landing INSIDE the window was invisible to the sweep: the derivation half then
    refused to satisfy dwell post-boundary while the goal kept a `met` whose
    justification was agreement drawn across two definitions. That is
    design-of-record §3 row 9 — *"two readings under different definitions look like
    a trend"* — surviving the very module that exists to detect it.

    The claim now carries the OLDEST consumed instant (`readingWindowFrom`), and
    this is the retirement-side pin the derivation-side dwell test never had."""
    i, t, g = _world(sdk, tag="dwell-boundary", dwell=2)
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")   # OLD definition
    _reading(sdk, i, 9.1, at="2026-11-11T11:00:00Z")   # NEW definition
    # NOT `_judge` here: that helper mints its own reading, which would put a
    # second instant at the head of the window and hide the one this test is about.
    made = judge_and_record(sdk, goal_id=g["id"], indicator_id=i["id"],
                            target=Target.from_node(t))
    claim = sdk.get_entity(made["point_id"])
    assert claim["dwell"] == 2
    assert claim["readingAsOf"] == "2026-11-11T11:00:00Z"
    assert claim["readingWindowFrom"] == "2026-11-11T10:00:00Z", (
        "the claim does not say how far back its dwell window reached, so a "
        "boundary inside the window cannot be seen")
    mark_definition_change(sdk, i["id"], at="2026-11-11T10:30:00+00:00")
    # the derivation half agrees the window no longer holds
    assert judge(sdk, indicator_id=i["id"], target=Target.from_node(t),
                 goal_id=g["id"]).dwell_satisfied is False
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "definition-changed"
    assert v.invalidation.effective_at == "2026-11-11T10:30:00+00:00"
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    assert sdk.get_entity(g["id"]).get("goalState") is None


def test_a_dwell_window_ENTIRELY_after_the_boundary_stands(sdk):
    """The negative control for the row above: every reading the window consumed
    was taken under the current definition, so the claim must survive — otherwise
    the fix would retire any dwell claim that outlives a definition change."""
    i, t, g = _world(sdk, tag="dwell-after", dwell=2)
    mark_definition_change(sdk, i["id"], at="2026-11-11T09:00:00+00:00")
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")
    _reading(sdk, i, 9.1, at="2026-11-11T11:00:00Z")
    made = judge_and_record(sdk, goal_id=g["id"], indicator_id=i["id"],
                            target=Target.from_node(t))
    assert made["written"] is True
    assert sdk.get_entity(made["point_id"])["readingWindowFrom"] == "2026-11-11T10:00:00Z"
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is None, v.invalidation
    assert invalidate_stale(sdk, goal_id=g["id"])["counts"]["retired"] == 0


def test_a_reading_that_names_no_instant_cannot_prove_it_is_comparable(sdk):
    """Fail-closed on the reading side: an undated reading cannot be shown to be
    after the boundary, so it is dropped rather than trended."""
    i, _t, _g = _world(sdk, tag="undated-reading")
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")
    mark_definition_change(sdk, i["id"], at="2026-11-11T11:00:00+00:00")
    assert latest_readings(sdk, i["id"], since="2026-11-11T11:00:00+00:00") == []
    # ⛔ and an UNORDERABLE BOUNDARY excludes EVERYTHING — the deliberate loud
    # direction (see `latest_readings`): nothing can be shown to be after a moment
    # nobody can place, and treating the stamp as absent would restart the trend.
    assert latest_readings(sdk, i["id"], since="not-an-instant") == []


def test_an_unreadable_boundary_is_UNDECIDABLE_never_silently_clean(sdk):
    """"We could not tell" must not be reported as "nothing to do" — and it must
    not be reported as "stale" either: a retirement is terminal, so a comparison
    that cannot be made must not fire one."""
    i, t, g = _world(sdk, tag="bad-boundary")
    made = _judge(sdk, i, t, g)
    sdk.update_entity(i["id"], **{DEFINITION_BOUNDARY_PROP: "TBD"})
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["stale"] == []
    assert len(report["undecidable"]) == 1
    assert report["undecidable"][0]["judgementId"] == made["point_id"]
    assert "cannot be ordered" in report["undecidable"][0]["why"]
    assert sdk.get_entity(made["point_id"])["status"] == "live"


@pytest.mark.parametrize("stamp", [
    "2026-11-11T12:00:00+00:00",
    "2026-11-11T12:00:00Z",
])
def test_the_boundary_is_MONOTONE_FORWARD(sdk, stamp):
    """A boundary may only move later. Moving it earlier would make readings
    comparable that were not, and could never un-retire what the earlier one
    already justified retiring."""
    i, _t, _g = _world(sdk, tag=f"monotone-{stamp[-6:]}")
    mark_definition_change(sdk, i["id"], at=stamp)
    with pytest.raises(ValueError, match="MONOTONE"):
        mark_definition_change(sdk, i["id"], at="2026-11-11T11:00:00+00:00")
    # forward is accepted, and the previous value is reported
    again = mark_definition_change(sdk, i["id"], at="2026-11-11T13:00:00+00:00")
    assert again["previous"] == stamp


@pytest.mark.parametrize("stamp", ["TBD", "", "soon", "2026-13-45T99:00:00Z"])
def test_a_boundary_that_names_no_instant_is_REFUSED_at_the_door(sdk, stamp):
    """⛔ REVIEW P1, reproduced. `mark_definition_change` used to store whatever
    it was handed: `at='TBD'` became the boundary, which then excluded EVERY
    reading (nothing can be shown to be after a moment nobody can place), so the
    indicator silently stopped producing judgements — and every later repair was
    refused, because the monotone check could not order the stored value either."""
    i, _t, _g = _world(sdk, tag=f"bad-stamp-{abs(hash(stamp))}")
    with pytest.raises(ValueError, match="must name an instant"):
        mark_definition_change(sdk, i["id"], at=stamp)
    assert sdk.get_entity(i["id"]).get(DEFINITION_BOUNDARY_PROP) is None


def test_an_UNORDERABLE_stored_boundary_is_REPAIRED_forward_not_stuck(sdk):
    """The other half of the same finding: a corrupt/legacy stamp must not become
    a monotone floor that refuses every repair. A well-formed stamp replaces it,
    and the repair is REPORTED rather than silently passing a check that never
    happened."""
    i, _t, _g = _world(sdk, tag="repair-stamp")
    # an outside write is the only way to reach this state now (see the door test)
    sdk.update_entity(i["id"], **{DEFINITION_BOUNDARY_PROP: "TBD"})
    repaired = mark_definition_change(sdk, i["id"], at="2026-11-12T00:00:00+00:00")
    assert repaired["repaired"] is True
    assert repaired["previous"] == "TBD"
    assert sdk.get_entity(i["id"])[DEFINITION_BOUNDARY_PROP] == "2026-11-12T00:00:00+00:00"
    # a WELL-FORMED previous is still monotone: no repair flag, no backwards move
    again = mark_definition_change(sdk, i["id"], at="2026-11-13T00:00:00+00:00")
    assert again["repaired"] is False
    assert again["previous"] == "2026-11-12T00:00:00+00:00"


def test_marking_a_boundary_on_a_missing_indicator_is_refused(sdk):
    with pytest.raises(ValueError, match="does not exist"):
        mark_definition_change(sdk, "no-such-indicator", at="2026-11-11T12:00:00Z")
    with pytest.raises(ValueError, match="indicator"):
        mark_definition_change(sdk, "", at="2026-11-11T12:00:00Z")


# ── 7. the retire door: one mechanism, journal-first, idempotent ──────────

def test_the_retirement_is_reported_in_full_and_is_IDEMPOTENT(sdk):
    i, t, g = _world(sdk, tag="idempotent")
    made = _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=8.0)
    first = _only([v.invalidation for v in verdicts(sdk, goal_id=g["id"])
                   if v.invalidation])
    retired = invalidate(sdk, first, at="2026-11-12T12:00:00+00:00")
    assert retired["invalidated"] is True
    assert retired["validTo"] == "2026-11-12T12:00:00+00:00"
    assert retired["reason"] == "target-revised"
    assert retired["detail"] == first.detail
    # a second sweep finds NOTHING to do — the claim is no longer live, so it is
    # not even scanned; and a direct second retire says so instead of raising.
    assert invalidate_stale(sdk, goal_id=g["id"])["counts"]["scanned"] == 0
    again = invalidate(sdk, first)
    assert again["invalidated"] is False
    assert again["alreadyRetired"] is True
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


def test_retiring_a_missing_claim_reports_it_rather_than_raising(sdk):
    report = invalidate(sdk, Invalidation(judgement_id="no-such-point",
                                          reason="target-revised", detail="…"))
    assert report["invalidated"] is False
    assert report["alreadyRetired"] is False
    assert "no such judgement" in report["detail"]


def test_the_derived_state_stops_feeding_the_goal(sdk):
    """The whole point, measured on belief rather than on status: once the claim
    is retired, EP stops propagating it into the goal.

    ⚠️ RECORDED RESIDUAL (not repaired here, and not a passing property): the
    goal's stored ``confidence``/``posterior_*`` — EP's own output — keep their
    last computed values until a pass with a factor recomputes them. Clearing
    EP-owned state from a retirement door would be a second writer for a quantity
    EP owns; it is filed against the `replace`/EP lane (#11824).
    """
    i, t, g = _world(sdk, tag="belief")
    made = _judge(sdk, i, t, g)
    before = sdk.compute_confidence()
    assert before["confidences"][g["id"]]["mean"] > 0.5
    assert before["confidences"][made["point_id"]]["mean"] > 0.5
    sdk.update_entity(t["id"], threshold=8.0)
    invalidate_stale(sdk, goal_id=g["id"])
    after = sdk.compute_confidence()
    assert g["id"] not in after["confidences"], (
        "the goal is still receiving belief from a retired derived state: "
        f"{after['confidences'].get(g['id'])}"
    )


def test_the_retirement_leaves_the_DURABILITY_gate_clean(tmp_path):
    """`derived = replay(journal)` must hold AFTER a retirement — the invariant
    that makes a retirement survive a rebuild instead of resurrecting a claim the
    live graph retired.

    ⛔ This is also the test that WOULD have caught the design mistake this
    module's own history records: stamping the reason onto the retracted claim
    with `update_point` writes a `PointRevised` prop neither replay fold carries,
    and `check-consistency` then reports `divergence="content"` on a FAITHFUL
    graph — with advice ("replay the journal") that reproduces exactly that graph
    and can therefore never clear it. Measured red before the props were dropped
    (`fields: ['invalidatedAt','invalidatedBy','invalidationReason']`), clean
    after.
    """
    import os

    from tortoise.config import is_db_uri
    from tortoise.consistency import check_consistency
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    log = str(events / "events.jsonl")
    namespace = (f"test_suite_{os.urandom(4).hex()}"
                 if is_db_uri(os.environ.get("TORTOISE_DB_URI")) else None)
    sdk = TortoiseSDK(str(tmp_path / "durable.db"), namespace=namespace,
                      event_log_path=log)
    try:
        indicator, target, goal = _world(sdk, tag="durable")
        made = _judge(sdk, indicator, target, goal)
        sdk.update_entity(target["id"], threshold=8.0)
        assert invalidate_stale(sdk, goal_id=goal["id"])["counts"]["retired"] == 1
        proj = sdk._get_proj()
        first = check_consistency(log, proj)   # adopts the baseline on a healthy run
        assert first["ok"], first
        result = check_consistency(log, proj)
        named = {d["id"]: d["fields"] for d in result["divergent_points"]}
        assert result["divergence"] is None, (
            "a faithful retirement was reported as a divergence — the journal and "
            f"the live graph disagree. divergence={result['divergence']!r} "
            f"named={named!r}")
        # non-vacuity: the claim really is retired in the live graph this ran against
        assert sdk.get_entity(made["point_id"])["status"] == "retracted"
    finally:
        sdk.close()


def test_the_reading_as_of_prop_is_DECLARED_for_replay():
    """``readingAsOf``/``readingWindowFrom`` ride the `PointAdded` snapshot, so they
    must be declared on
    the Point prop contract — undeclared, every rebuild of every judged point logs
    a FALSE ``"Point prop %r is not declared"`` drift warning, and that noise is
    what hides a genuine one. The same pin the #6110 (`key`) and docs-miner
    (`externalId`) rows carry.

    ⛔ The RETIREMENT's own props are deliberately NOT on that contract, and this
    test does not look for them: they would be live-only (neither replay fold
    carries an arbitrary `PointRevised` prop), so declaring them would suppress a
    warning while leaving a real live/replay divergence behind.
    """
    from tortoise.projection.entities import _EntityHandlers
    for prop in ("readingAsOf", "readingWindowFrom"):
        assert prop in _EntityHandlers._POINT_DECLARED_PROPS


def test_the_retirement_is_journalled_so_a_rebuild_cannot_resurrect_it(sdk):
    """`retract_point` is the journal-first door: the retirement is a
    ``PointRetracted`` record, not a bare status stamp — otherwise
    ``derived = replay(journal)`` would bring the claim back."""
    from tortoise.live import TERMINAL_EXCLUDED_STATUSES
    i, t, g = _world(sdk, tag="journal")
    made = _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=8.0)
    invalidate_stale(sdk, goal_id=g["id"])
    claim = sdk.get_entity(made["point_id"])
    assert claim["status"] in TERMINAL_EXCLUDED_STATUSES
    assert claim["validTo"]
    # the SAME predicate every read surface uses now excludes it
    assert live_judgements(sdk, goal_id=g["id"]) == []


# ── 7b. failures are REPORTED, never silently taken or thrown away ───────

def test_a_failed_cache_reconcile_is_reported_and_the_retirement_still_lands(sdk):
    """⛔ REVIEW P2, reproduced. The retirement is terminal and durable; the cache
    follow-up is not. An unguarded reconcile used to abort the call AFTER the
    retirement landed, so a caller learned nothing had happened when the important
    half had — and `reconcile_goal_state_cache` is public and idempotent, so the
    repair route is a second call."""
    i, t, g = _world(sdk, tag="cache-fail")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=8.0)
    finding = _only([v.invalidation for v in verdicts(sdk, goal_id=g["id"])
                     if v.invalidation])

    real_update = sdk.update_entity

    def failing(node_id, **props):
        if node_id == g["id"]:
            raise RuntimeError("transient write failure")
        return real_update(node_id, **props)

    sdk.update_entity = failing
    try:
        retired = invalidate(sdk, finding)
    finally:
        sdk.update_entity = real_update
    assert retired["invalidated"] is True
    assert retired["cache"]["checked"] is False
    assert "transient write failure" in retired["cache"]["error"]
    # the repair route works, and is a no-op when there is nothing to do
    repaired = reconcile_goal_state_cache(sdk, g["id"])
    assert repaired["cleared"] is True
    assert sdk.get_entity(g["id"]).get("goalState") is None


def test_a_failed_cache_write_counts_as_a_FAILED_sweep(sdk):
    """⛔ REVIEW cycle 2, P2, reproduced. A cache reconcile failure was reported
    per finding but left OUT of `counts.failed`, so a sweep whose cache write
    failed read clean while the goal still showed `met` — #11926's own headline
    surviving a sweep that claimed nothing went wrong. The summary must not
    contradict the nested key."""
    i, t, g = _world(sdk, tag="cache-fail-count")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=8.0)
    real_update = sdk.update_entity

    def failing(node_id, **props):
        if node_id == g["id"]:
            raise RuntimeError("transient write failure")
        return real_update(node_id, **props)

    sdk.update_entity = failing
    try:
        report = invalidate_stale(sdk, goal_id=g["id"])
    finally:
        sdk.update_entity = real_update
    assert report["counts"]["retired"] == 1
    assert report["invalidated"][0]["cache"]["checked"] is False
    assert report["counts"]["failed"] == 1, (
        "the sweep reported no failure while the goal still shows a derived word")
    # and the repair route clears it
    reconcile_goal_state_cache(sdk, g["id"])
    assert sdk.get_entity(g["id"]).get("goalState") is None


def test_one_refused_retirement_does_not_discard_the_rest_of_the_sweep(sdk):
    """A sweep is a sequence of independent writes: a refusal on ONE finding is
    recorded on that finding, so a caller can still tell which of the others
    landed. Throwing the exception out would have lost that."""
    worlds = [_world(sdk, tag=f"sweep-partial-{n}") for n in range(2)]
    findings = []
    for i, t, g in worlds:
        _judge(sdk, i, t, g)
        sdk.update_entity(t["id"], threshold=8.0)
        findings.append((i, t, g))
    import tortoise.derived_invalidation as _mod

    doomed = findings[0][2]["id"]
    original = _mod.invalidate

    def refusing(sdk_, inv, **kw):
        if inv.goal_id == doomed:
            raise RuntimeError("refused on purpose")
        return original(sdk_, inv, **kw)

    _mod.invalidate = refusing
    try:
        report = invalidate_stale(sdk)
    finally:
        _mod.invalidate = original
    assert report["counts"]["retired"] == 1
    assert report["counts"]["failed"] == 1
    failed = [r for r in report["invalidated"] if r.get("error")]
    assert len(failed) == 1 and "refused on purpose" in failed[0]["error"]
    assert len(report["invalidated"]) == 2


# ── 8. the sweep: scope, limit, dry run, and no re-derivation ────────────

def test_dry_run_writes_nothing_and_names_what_it_would_retire(sdk):
    i, t, g = _world(sdk, tag="dry-run")
    made = _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=8.0)
    report = invalidate_stale(sdk, goal_id=g["id"], dry_run=True)
    assert report["dryRun"] is True
    assert report["counts"]["stale"] == 1
    assert report["invalidated"] == []
    assert sdk.get_entity(made["point_id"])["status"] == "live"
    assert sdk.get_entity(g["id"])["goalState"] == "met"


def test_the_sweep_does_NOT_re_derive(sdk):
    """Re-deriving here would decide #7860's D13 ("what triggers the loop?") by
    shipping. The retirement is the effect; the new state is the caller's next
    ``judge_and_record``."""
    i, t, g = _world(sdk, tag="no-rederive")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=8.0)
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    assert live_judgements(sdk, goal_id=g["id"]) == []


def test_an_unscoped_sweep_covers_every_goal_and_reports_truncation(sdk):
    worlds = [_world(sdk, tag=f"unscoped-{n}") for n in range(3)]
    for i, t, g in worlds:
        _judge(sdk, i, t, g)
        sdk.update_entity(t["id"], threshold=8.0)
    full = invalidate_stale(sdk, dry_run=True)
    assert full["counts"]["scanned"] == 3
    assert full["counts"]["stale"] == 3
    assert full["truncated"] is False
    # a scan that hits its bound SAYS SO — a silent partial sweep is the
    # "coverage=1.0 is its own denominator" defect class (#11823)
    capped = invalidate_stale(sdk, dry_run=True, limit=2)
    assert capped["truncated"] is True


def test_the_claim_survives_with_a_SECOND_live_judgement_taking_over_the_cache(sdk):
    """Two targets on one goal (D4's 0..n) both produce a judgement. Retiring one
    must leave the goal reading the SURVIVING state, not a cleared cache."""
    i, t, g = _world(sdk, tag="two-targets")
    other = sdk.create_object("CI p95 <= 12m [two-targets]", objectKind="target",
                              comparator="<=", threshold=12.0)
    first = _judge(sdk, i, t, g, value=11.0, at="2026-11-11T10:00:00Z")
    second = _judge(sdk, i, other, g, value=11.0, at="2026-11-11T11:00:00Z")
    assert second["judged"]["state"] == "met"
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    # revise the FIRST target's bar: its judgement goes, the other target's stands
    sdk.update_entity(t["id"], threshold=13.0)
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    assert latest_judgement(sdk, goal_id=g["id"])["id"] == second["point_id"]
    assert sdk.get_entity(first["point_id"])["status"] == "retracted"
