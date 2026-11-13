"""#11926 — a derived state is retired when the thing it was derived from changes.

Three inputs can move under a judgement (#11822 writes it, this suite retires it):
the TARGET (its bar), the DEFINITION (what the indicator's number means), and the
GOAL (the commitment the state is about). Before #11926 nothing retried any of
them, so a goal could sit at ``met`` after the threshold changed, two readings
under two definitions could read as a trend, and a retired goal's states kept
counting.

Every fact asserted here was MEASURED on this head, embedded OR docker lane (the
shared ``sdk_factory`` is URI-aware); ``TORTOISE_TEST_CARVE_OUT=1`` selects the
embedded carve-out. The suite is hermetic: no network, no model, and every instant
an assertion ORDER as evidence is supplied by the test. ⚠️ Two facts read from the
WRITER's clock are not test-supplied, and are named here rather than claimed away
by the sentence above (review P3): ``evaluatedAt``/``createdAt`` are
``datetime.now(UTC)`` minted inside ``record_judgement`` (which exposes no seam to
pass one in), so the back-dated test asserts that the mint is *later than* the
boundary it wrote — a comparison no host clock older than ``2026-11-11`` could
satisfy. The seeded names use a digest, never ``hash()``, whose ``str`` salt is
per process (``PYTHONHASHSEED``).
"""
from __future__ import annotations

import hashlib

import pytest

from tortoise.derived_invalidation import (
    RETIRED_GOAL_STATES,
    STALE_REASONS,
    Invalidation,
    invalidate,
    invalidate_stale,
    mark_definition_change,
    reconcile_goal_state_cache,
    reconcile_goal_states,
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


# ── 4a. the LEGACY retirement words retire an input too ───────────────────

@pytest.mark.parametrize("retire,reason", [
    ("target", "target-retired"),
    ("indicator", "indicator-retired"),
    ("goal", "goal-retired"),
])
def test_the_LEGACY_outdated_WORD_retires_an_input_like_the_recall_lane(sdk, retire, reason):
    """`outdated` is the legacy supersede/invalidate WORD, and the RECALL lane
    already treats it as stale: `assembly._RECALL_OBJECT_EXCLUDED_STATUSES` is
    `commit_ops.OBJECT_TERMINAL_STATUSES | {"outdated"}`, frozen by
    `tests/test_assembly_pure.py`, and this module's own ``invalidate()`` uses
    `live.TERMINAL_EXCLUDED_STATUSES` on the claim for the same reason.

    ⛔ Measured RED before this case existed: `_retirement` tested the narrower
    `commit_ops.OBJECT_TERMINAL_STATUSES`, which OMITS `outdated`, so an input
    retired with the legacy word returned ``None`` — HEALTHY — and the derived
    state kept counting. `sdk.update_entity(<object>, status='outdated')` is an
    accepted public write, and #11825 measured that the SDK accepts terminal
    statuses on Objects — so this is the issue's own headline ("a conclusion
    whose justification is gone"), reachable through a public door, not a
    theoretical spelling.

    ⚠️ THE INVARIANT IS STALENESS, NOT READER UNANIMITY, and this docstring said the
    opposite until a review falsified it against the source: the OBJECT family is
    SPLIT about this word — the four retrieval legs and `sdk.recall_state` still
    SERVE an `outdated` Object (`search_engine._status_vocab_for` passes
    `OBJECT_TERMINAL_STATUSES` with `include_outdated_flag=False`; `commit_ops.py`
    says it outright: *"'outdated' IS visible — it is not in the object vocabulary,
    only in the POINT-terminal set"*). Retiring anyway is correct because a state
    derived from an input carrying the legacy invalidate word is exactly the stale
    conclusion #11926 exists to retire, and re-derivation mints a fresh claim —
    nothing is lost.
    """
    i, t, g = _world(sdk, tag=f"legacy-outdated-{retire}")
    made = _judge(sdk, i, t, g)
    node = {"target": t, "indicator": i, "goal": g}[retire]
    sdk.update_entity(node["id"], status="outdated")
    # the write landed; this assertion is what the sweep will read back
    assert sdk.get_entity(node["id"])["status"] == "outdated"
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == reason, v.invalidation
    assert invalidate_stale(sdk, goal_id=g["id"])["counts"]["retired"] == 1
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


def test_a_truthy_NON_BOOLEAN_outdated_flag_is_terminal_like_is_terminal_status(sdk):
    """The flag is read by ``bool()``, and the legacy population is why.

    ``live.is_terminal_status``'s flag half is ``bool(outdated)``, and the Cypher
    predicate (``coalesce(n.outdated, false) = false``) treats any non-``false``,
    non-NULL value as terminal — so a legacy node holding ``outdated = 'true'``
    (a STRING, which no in-repo writer produces but old graphs can carry) is
    terminal to every flag-aware read surface. An identity test (``is True``)
    called it LIVE: the same disagreement this arm exists to end, surviving on a
    narrower population. The one CLASS where ``bool()`` and the Cypher predicate
    still differ — falsy-but-not-``false`` values (``0``/``0.0``, ``""``): live
    here, terminal in Cypher — is the residual ``is_terminal_status`` documents
    and #7075 rules unreachable in-repo. (Both spellings point the same way there:
    Python says live, the graph says terminal — the direction the fix under test
    closed, not its opposite.)
    """
    i, t, g = _world(sdk, tag="legacy-outdated-truthy")
    made = _judge(sdk, i, t, g)
    sdk._get_proj().g.query(
        "MATCH (n:Object) WHERE n.id = $id SET n.outdated = 'true'",
        params={"id": t["id"]},
    )
    assert sdk.get_entity(t["id"])["outdated"] == "true"  # truthy, not boolean
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "target-retired", v.invalidation
    assert invalidate_stale(sdk, goal_id=g["id"])["counts"]["retired"] == 1
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


def test_the_LEGACY_outdated_FLAG_retires_an_input(sdk):
    """The OTHER legacy spelling: `outdated=true` with a LIVE status.

    ⚠️ THE FLAG IS NOT WRITABLE THROUGH `update_entity` — it refuses with "`outdated`
    is a server-managed lifecycle flag" (measured while writing this case). That
    refusal is the POINT: no Object writer sets the flag
    (``live._terminal_excluded`` passes ``include_outdated_flag=False`` for Objects
    for the same reason), so this shape only appears in LEGACY data — which is
    exactly the population a retirement sweep must not serve as live. The raw
    graph write below is how the repo simulates legacy data elsewhere
    (`tests/test_lifecycle_guards.py` writes a legacy ``deprecated`` status the
    same way).

    The claim's own lane already honours the flag (`derived_invalidation.invalidate`
    reads ``claim.get("outdated")`` by ``bool()``, the same spelling as this arm);
    the two lanes must not disagree about what "dead" means.
    """
    i, t, g = _world(sdk, tag="legacy-outdated-flag")
    made = _judge(sdk, i, t, g)
    sdk._get_proj().g.query(
        "MATCH (n:Object) WHERE n.id = $id SET n.outdated = true",
        params={"id": t["id"]},
    )
    props = sdk.get_entity(t["id"])
    assert props["outdated"] is True
    assert props.get("status") != "outdated"  # the flag, NOT the word
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation.reason == "target-retired", v.invalidation
    assert invalidate_stale(sdk, goal_id=g["id"])["counts"]["retired"] == 1
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"



def test_a_definite_bar_revision_OUTRANKS_an_undecidable_scope(sdk):
    """⛔ REVIEW P2, reproduced. An absent dimension is an ABSENCE of evidence; a
    bar that has visibly moved is evidence.

    The three scope guards used to RETURN ``undecidable`` before the bar check ran,
    so an unnamed `indicatorId` — the pre-#11965 shape this module's own comment
    says an unscoped repair sweep meets — SHIELDED a claim whose bar had moved:
    measured, `judgementState=met`, indicator removed, `threshold 10.0 -> 8.0` (so
    `9.0 <= 8.0` is false) → ``undecidable (names no indicator)``, `invalidation`
    ``None``, claim live, goal still reading ``met``. #11926's headline, on the one
    population the guard's own comment names.

    Ordering is the whole fix: only a DEFINITE finding may return early. This test
    asserts the definite one WINS, and (the other half) that a claim with nothing
    definite still reports undecidable rather than being retired on a technicality.

    ⛔ ALL THREE DIMENSIONS, because the fix is three guards and one pin does not
    cover them. MEASURED by review (cycle 9) at `352bf4782d`, which collected 72
    cases: with only the indicator arm pinned, reverting the GOAL guard — or the
    TARGET guard — left all 72 of them green, so two of the three arms could
    silently regress to shielding a definite finding. The target arm is pinned
    through the BOUNDARY trigger rather than `target-revised`, because a claim that
    names no target skips the bar check by design.
    """
    i, t, g = _world(sdk, tag="scope-outranked")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    # the pre-#11965 shape: a claim carrying a goal and a bar, but no indicator id
    sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.id = $id REMOVE p.indicatorId",
        params={"id": made["point_id"]},
    )
    assert "indicatorId" not in sdk.get_entity(made["point_id"])
    # nothing definite yet: the missing scope is undecidable, NOT a retirement
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is None
    assert "names no indicator" in v.undecidable
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 0
    assert sdk.get_entity(made["point_id"])["status"] == "live"

    # now the BAR moves: a definite finding, and it must not be shielded
    sdk.update_entity(t["id"], threshold=8.0)
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is not None, (
        "an unplaceable scope shielded a claim whose bar visibly moved: "
        f"{v.undecidable}")
    assert v.invalidation.reason == "target-revised"
    assert invalidate_stale(sdk, goal_id=g["id"])["counts"]["retired"] == 1
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


@pytest.mark.parametrize("missing", ["goalId", "targetId"])
def test_the_OTHER_scope_guards_also_defer_to_a_definite_finding(sdk, missing):
    """The two arms the single-dimension test above did not cover (review cycle 9).

    ⛔ `goalId` is pinned through `target-revised` and `targetId` through
    `definition-changed`: a claim that names NO target skips the bar check BY DESIGN
    — the target guard **defers** (it sets `target = None` and does NOT return, the
    shape `8182943a68` established), so the bar check's `isinstance(target, dict)`
    gate means `_drift` is never called and `target-revised` is UNREACHABLE in that
    world. The boundary is therefore the definite finding this test pins. (It is not
    the only definite finding behind that guard — `indicator-retired` also sits
    there, and superseding the indicator would red this test for that reason
    instead.) Pinning both through the bar would have been a test that cannot red for
    the right reason.
    """
    i, t, g = _world(sdk, tag=f"scope-outranked-{missing}")
    made = _judge(sdk, i, t, g, at="2026-11-11T10:00:00Z")
    sdk._get_proj().g.query(
        f"MATCH (p:Point) WHERE p.id = $id REMOVE p.{missing}",
        params={"id": made["point_id"]},
    )
    assert missing not in sdk.get_entity(made["point_id"])

    # nothing definite yet: undecidable, and NOT retired
    v = _only([x for x in verdicts(sdk) if x.judgement_id == made["point_id"]])
    assert v.invalidation is None, v.invalidation
    assert v.undecidable is not None

    if missing == "goalId":
        sdk.update_entity(t["id"], threshold=8.0)
        expected = "target-revised"
    else:
        mark_definition_change(sdk, i["id"], at="2026-11-12T00:00:00+00:00")
        expected = "definition-changed"

    v = _only([x for x in verdicts(sdk) if x.judgement_id == made["point_id"]])
    assert v.invalidation is not None, (
        f"the {missing} guard returned before a DEFINITE finding and shielded it: "
        f"{v.undecidable}")
    assert v.invalidation.reason == expected
    assert invalidate(sdk, v.invalidation)["invalidated"] is True
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


def test_a_claim_carrying_the_LEGACY_outdated_flag_is_already_retired(sdk):
    """The claim-side legacy-flag gate, pinned (review cycle 9).

    `invalidate` decides "already retired" with the SHARED vocabulary plus the
    legacy `outdated` flag, read with `bool()` so a truthy string — the spelling a
    legacy graph holds — is honoured. MEASURED: reverting both reads to `is True`
    left the whole suite green, and a claim carrying `outdated='true'` then made
    `invalidate()` RAISE from `retract_point` instead of reporting
    `alreadyRetired: True`. Asserting the RESULT (not one read) is what makes this
    pin meaningful: the first gate is masked by the caught retract refusal.
    """
    i, t, g = _world(sdk, tag="legacy-claim-flag")
    made = _judge(sdk, i, t, g)
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) SET p.outdated = 'true'",
        params={"id": made["point_id"]},
    )
    finding = Invalidation(judgement_id=made["point_id"], reason="target-revised",
                          detail="…")
    result = invalidate(sdk, finding)
    assert result["invalidated"] is False
    assert result["alreadyRetired"] is True, result
    assert sdk.get_entity(made["point_id"])["status"] != "retracted"


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


def test_a_failed_boundary_read_does_NOT_derive_or_write(sdk):
    """⛔ REVIEW P2, reproduced. The derivation half inherited the very fold the
    invalidation half refuses: ``definition_boundary()`` returned ``None`` both for
    "no boundary declared" and for "the indicator read raised", so ``judge``
    served a PRE-boundary reading, called it ``met``, and ``judge_and_record``
    minted that state and wrote it into the goal's ``goalState`` — a durable wrong
    word from a transient read failure, and exactly the "wrong number rather than a
    stale one" hazard capability 9 exists to remove.

    MEASURED before the fix, on a world with one reading at 10:00 and the boundary
    at 12:00 (only the indicator read failing)::

        healthy read -> state='unknown' reading=None             (nothing written)
        failed read  -> state='met'     reading='<pre-boundary>' (minted + cached)

    The honest answer costs nothing: ``GOAL_STATE_CACHE['unknown'] is None``, so
    ``unknown`` writes no cache word and flips nothing (#11822's rule for a missing
    reading).
    """
    i, t, g = _world(sdk, tag="boundary-read-fail-derive")
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")
    mark_definition_change(sdk, i["id"], at="2026-11-11T12:00:00+00:00")
    target = Target.from_node(t)
    # control: a healthy read of the SAME world answers unknown — the boundary
    # excludes the only reading, so there is nothing to judge.
    assert judge(sdk, indicator_id=i["id"], target=target, goal_id=g["id"]).state == "unknown"

    real_get = sdk.get_entity

    def failing(node_id, *a, **kw):
        if node_id == i["id"]:
            raise RuntimeError("transient read failure")
        return real_get(node_id, *a, **kw)

    sdk.get_entity = failing
    try:
        judged = judge(sdk, indicator_id=i["id"], target=target, goal_id=g["id"])
        assert judged.state == "unknown", (
            "a failed read of the definition boundary was treated as 'no boundary "
            f"declared', so a pre-boundary reading was judged: {judged.state!r}")
        assert judged.reading is None
        assert judged.reason == "boundary-unreadable"
        made = judge_and_record(sdk, goal_id=g["id"], indicator_id=i["id"], target=target)
        assert made["written"] is False, made
    finally:
        sdk.get_entity = real_get
    # ⛔ nothing was written — the goal keeps the word it was created with
    assert sdk.get_entity(g["id"])["goalState"] == "active"


def test_a_GOAL_that_no_longer_EXISTS_is_reported_gone_not_cleared(sdk):
    """⛔ REVIEW P3, reproduced. ``reconcile_goal_state_cache`` folded a MISSING goal
    into "the goal holds no cached word" (``get_entity(...) or {}``), and
    ``update_entity`` on a missing id is a silent no-op — so a sweep over a deleted
    goal reported ``cleared: True`` for a write that landed nowhere, and the
    documented repair route kept reporting it forever.

    The retirement DID land; only the cache follow-up could not happen. That is its
    own bucket (``counts.gone``), never a failure of the retirement and never a
    success of the reconcile.
    """
    i, t, g = _world(sdk, tag="goal-gone-cache")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    sdk.delete_entity(g["id"])
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    assert report["counts"]["failed"] == 0
    assert report["counts"]["gone"] == 1, report["counts"]
    cache = report["invalidated"][0]["cache"]
    assert cache["checked"] is False
    assert cache["gone"] is True
    assert "no longer exists" in cache["reason"]
    # the retirement itself landed — this is a reconcile gap, not a retire gap
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"
    # and the repair route says the same thing instead of claiming a clear
    again = reconcile_goal_state_cache(sdk, g["id"])
    assert again["checked"] is False and again["gone"] is True


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


def test_a_claim_that_records_NO_reading_instant_is_UNDECIDABLE_not_healthy(sdk):
    """⛔ REVIEW cycle 6, P2, reproduced. Claims written before `readingAsOf` and
    `readingWindowFrom` existed carry neither, so the boundary check fell back to
    the MINT clock — and a back-dated boundary between the reading and the mint
    then read as "the claim is newer, it stands". The retirement half said HEALTHY
    while the derivation half refused to satisfy the window: the two halves of this
    same change disagreeing on the same graph.

    The mint clock is not evidence of when a number was taken, so it is no longer a
    fallback: when a boundary is declared, the instant must come from a READING, and
    a claim that cannot supply one is reported as `undecidable` — loudly, and
    without retiring it."""
    i, t, g = _world(sdk, tag="legacy-instant", dwell=2)
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")
    _reading(sdk, i, 9.1, at="2026-11-11T11:00:00Z")
    made = judge_and_record(sdk, goal_id=g["id"], indicator_id=i["id"],
                            target=Target.from_node(t))
    # the pre-#11926 claim shape: no reading instants at all
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$i}) REMOVE p.readingAsOf, p.readingWindowFrom",
        params={"i": made["point_id"]})
    mark_definition_change(sdk, i["id"], at="2026-11-11T10:30:00+00:00")
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is None, v.invalidation
    assert "cannot be compared" in v.undecidable
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 0
    assert report["counts"]["undecidable"] == 1
    assert sdk.get_entity(made["point_id"])["status"] == "live"
    # ⛔ and the OTHER retirement reasons still work on that same legacy claim — an
    # undecidable DEFINITION check must not shield a revised bar.
    sdk.update_entity(t["id"], threshold=8.0)
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is not None, (
        "an unplaceable definition boundary suppressed the bar check — a stale "
        "claim would survive a target revision")
    assert v.invalidation.reason == "target-revised"


def test_a_window_with_an_UNDATED_reading_is_UNDECIDABLE_not_narrower(sdk):
    """The other half of the same finding: an undated reading inside the window is
    still `available`, so `_dwell_satisfied` counts it as agreement — but its
    instant is unknown. Reporting the oldest DATED reading as the window start
    under-reports how far back the decision reached, so a boundary inside the real
    window stays invisible. The window is therefore reported as UNPLACEABLE."""
    i, t, g = _world(sdk, tag="undated-window", dwell=2)
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")
    # an undated reading, still a real measurement
    sdk.create_event("CI run undated", "measurement", value=9.1, unit="minutes",
                     aboutObject=i["id"])
    made = judge_and_record(sdk, goal_id=g["id"], indicator_id=i["id"],
                            target=Target.from_node(t))
    claim = sdk.get_entity(made["point_id"])
    assert claim["dwell"] == 2
    assert claim.get("readingWindowFrom") is None, (
        "the window was narrowed to the oldest DATED reading, so its real start is "
        "hidden")
    mark_definition_change(sdk, i["id"], at="2026-11-11T10:30:00+00:00")
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is None, v.invalidation
    assert "dwell window cannot be placed" in v.undecidable
    assert sdk.get_entity(made["point_id"])["status"] == "live"


def test_every_minted_dwell_claim_places_its_window(sdk):
    """Why the two rows above are the ONLY shapes that need the undecidable arm.

    An UNCHANGED state mints nothing (#11822's unchanged arm re-asserts the live
    claim), and a `dwell-holding` pass reports the PREVIOUS state — which equals the
    live claim's state, so it mints nothing either. Every claim this writer mints is
    therefore a FLIP, so a `dwell > 1` claim that has a placeable window ALWAYS
    carries `readingWindowFrom`; a `met`/`unmet` dwell claim without one is exactly
    the shape whose window cannot be placed, which is why reporting it is not noise.
    """
    i, t, g = _world(sdk, tag="minted-dwell", dwell=2)
    _reading(sdk, i, 9.0, at="2026-11-11T10:00:00Z")
    _reading(sdk, i, 9.1, at="2026-11-11T11:00:00Z")
    target = Target.from_node(t)
    first = judge_and_record(sdk, goal_id=g["id"], indicator_id=i["id"], target=target)
    assert first["written"] is True
    claim = sdk.get_entity(first["point_id"])
    assert claim["judgementReason"] == "state-flipped"
    assert claim["readingWindowFrom"] == "2026-11-11T10:00:00Z"
    # a later pass whose state is UNCHANGED mints nothing, so no claim of that
    # shape can exist to be mis-reported
    _reading(sdk, i, 9.2, at="2026-11-11T12:00:00Z")
    second = judge_and_record(sdk, goal_id=g["id"], indicator_id=i["id"], target=target)
    assert second["written"] is False
    assert second["point_id"] == first["point_id"]
    # ... and that claim is placed by its WINDOW, not by the newest reading
    mark_definition_change(sdk, i["id"], at="2026-11-11T11:30:00+00:00")
    v = _only(verdicts(sdk, goal_id=g["id"]))
    assert v.invalidation is not None and v.invalidation.reason == "definition-changed"


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
    i, _t, _g = _world(sdk, tag="bad-stamp-" + hashlib.sha1(stamp.encode()).hexdigest()[:8])
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


def test_the_AUDIT_path_discloses_its_bound_instead_of_claiming_completeness(sdk):
    """#11823's class, on the AUDIT half: a caller that reads a capped list as
    exhaustive hears "clean" from a sweep that never looked.

    ``verdicts()`` returns a bare ``list``, so it has no payload in which a
    truncation flag could travel — the disclosure is therefore the docstring, and
    that is what this pins: the bound is REAL (``limit`` bites), and the stated
    contract names it instead of claiming every judgement in scope. The
    mechanically readable surface stays :func:`invalidate_stale`, whose
    ``truncated`` key is pinned by
    ``test_an_unscoped_sweep_covers_every_goal_and_reports_truncation``.
    """
    worlds = [_world(sdk, tag=f"audit-{n}") for n in range(3)]
    for i, t, g in worlds:
        _judge(sdk, i, t, g)
    assert len(verdicts(sdk)) == 3               # the whole scope, no cap hit
    assert len(verdicts(sdk, limit=2)) == 2      # the cap is REAL, not decorative
    doc = (verdicts.__doc__ or "").lower()
    assert "limit" in doc and "truncat" in doc, (
        "verdicts() must name its bound AND its truncation: it returns a bare "
        "list, so the docstring is the only place a caller can learn that a "
        "full-looking answer may have been cut off (#11823)")
    assert "every live judgement" not in doc, (
        "verdicts() must not claim completeness it cannot know: the scan is "
        "capped at `limit` (default 10,000) and the function has no way to "
        "report whether it hit the cap")
    lj = (live_judgements.__doc__ or "").lower()
    assert ("limit" in lj or "10,000" in lj or "10000" in lj), (
        "live_judgements() makes the same capped read, so its docstring must "
        "state the bound too")
    assert "every live judgement" not in lj, (
        "live_judgements() must not claim EVERY judgement either: it returns "
        "at most `limit` rows")


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


# ── 9. a claim that leaves by a route the door never sees (#12336) ────────
#
# `invalidate_stale` enumerates LIVE claims, so a goal whose LAST claim is gone
# is never a finding again: the door is never called, the cached word stands, and
# nothing repairs it. The two routes that reach that state without the door are
# a hard delete (`sdk.delete_entity`) and a retirement written out of band (a
# direct status/lifecycle write). Both are measured below, and the repair sweep
# is the mechanism that closes them — a sweep over GOALS, not over claims.

def _derived_word_standing(sdk, goal, *, word="met"):
    assert sdk.get_entity(goal["id"])["goalState"] == word, (
        f"precondition: the goal must hold the derived word {word!r} — a test "
        "that starts from a cleared cache cannot observe the defect")


def _lagging_word(sdk, indicator, target, goal):
    """Leave the cache on the PREVIOUS word under a live claim that says otherwise.

    The shape is documented rather than invented: a flip whose ``goalState`` write
    is refused leaves the old word in place while the claim and its IMPL arrow
    both land (`goal_judgement._cache_then_arrow` names exactly that arm, and its
    self-healing pass only runs when something re-derives the goal).
    """
    _judge(sdk, indicator, target, goal, value=9.0)
    assert sdk.get_entity(goal["id"])["goalState"] == "met"
    real_update = sdk.update_entity

    def failing(node_id, **props):
        if node_id == goal["id"] and "goalState" in props:
            raise RuntimeError("cache write refused on the flip")
        return real_update(node_id, **props)

    sdk.update_entity = failing
    try:
        _reading(sdk, indicator, 15.0, at="2026-11-11T11:00:00Z")
        with pytest.raises(RuntimeError):
            judge_and_record(sdk, goal_id=goal["id"],
                             indicator_id=indicator["id"],
                             target=Target.from_node(target))
    finally:
        sdk.update_entity = real_update
    assert sdk.get_entity(goal["id"])["goalState"] == "met", (
        "precondition: the refused flip leaves the PREVIOUS word in the cache")
    assert latest_judgement(sdk, goal_id=goal["id"])["judgementState"] == "unmet"


def test_ROUTE_A_a_claim_DELETED_outright_is_invisible_to_the_sweep(sdk):
    """THE DEFECT, measured with only the APIs that exist today (#12336).

    This is not the fix's acceptance test — it is the proof that the gap is real
    and that the whole graph is in the state #12336 describes: the claim is
    hard-deleted (route A: purge / concurrent cleanup), `invalidate_stale` reads
    ``scanned: 0`` with every counter zero, and the goal keeps displaying ``met``
    with nothing live behind it. Any repair must therefore be keyed on GOALS; no
    amount of claim-side work reaches this shape, which is why closing the
    `alreadyRetired` bucket (#12315) does not close it.
    """
    i, t, g = _world(sdk, tag="route-a")
    made = _judge(sdk, i, t, g)
    _derived_word_standing(sdk, g)
    sdk.delete_entity(made["point_id"])
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["scanned"] == 0, (
        "the sweep enumerates live claims; a deleted claim is not one")
    assert report["counts"] == {"scanned": 0, "stale": 0, "retired": 0,
                                "alreadyRetired": 0, "gone": 0, "failed": 0,
                                "undecidable": 0}, report["counts"]
    # ⛔ THE HEADLINE SURVIVES: `met` with no live judgement anywhere.
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    assert latest_judgement(sdk, goal_id=g["id"]) is None


def test_ROUTE_B_a_claim_retired_OUT_OF_BAND_is_invisible_to_the_sweep(sdk):
    """Route B: the claim is retracted by a direct write, not by the door."""
    i, t, g = _world(sdk, tag="route-b")
    made = _judge(sdk, i, t, g)
    _derived_word_standing(sdk, g)
    sdk.update_entity(made["point_id"], status="retracted")
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["scanned"] == 0, report
    assert latest_judgement(sdk, goal_id=g["id"]) is None
    assert sdk.get_entity(g["id"])["goalState"] == "met", (
        "route B leaves the word standing exactly as route A does")


def test_the_repair_sweep_clears_a_word_whose_claim_was_DELETED(sdk):
    """ROUTE A, repaired: the sweep finds the goal by its WORD, not by a claim."""
    i, t, g = _world(sdk, tag="sweep-route-a")
    made = _judge(sdk, i, t, g)
    _derived_word_standing(sdk, g)
    sdk.delete_entity(made["point_id"])
    report = reconcile_goal_states(sdk)
    assert report["truncated"] is False, report
    assert report["counts"]["repaired"] == 1, report
    assert report["counts"]["cleared"] == 1, report
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the goal still displays a word nothing backs — the #11926 headline "
        "surviving a repair sweep that reported success")
    record = _only([r for r in report["goals"] if r["goalId"] == g["id"]])
    assert record["action"] == "cleared", record
    assert record["from"] == "met", record


def test_the_repair_sweep_clears_a_word_whose_claim_was_RETIRED_OUT_OF_BAND(sdk):
    """ROUTE B, repaired: nobody called the door, so nothing else follows it."""
    i, t, g = _world(sdk, tag="sweep-route-b")
    made = _judge(sdk, i, t, g)
    _derived_word_standing(sdk, g)
    sdk.update_entity(made["point_id"], status="retracted")
    report = reconcile_goal_states(sdk)
    assert report["counts"]["repaired"] == 1, report
    assert sdk.get_entity(g["id"]).get("goalState") is None


def test_the_repair_sweep_MOVES_a_LAGGING_word_to_the_newest_LIVE_claim(sdk):
    """The second shape the sweep exists for: the word is not unsupported, it is
    OLD. A flip whose cache write was refused leaves the PREVIOUS word under a
    live claim (`goal_judgement._cache_then_arrow` documents exactly this), and
    no later pass revisits the goal — so the sweep must move the word forward to
    the newest live judgement, not merely clear unsupported ones.

    ⚠️ This test's `rewritten` count is NOT its discriminating power: the door
    re-derives the same word, so it reaches `rewritten` even from a wrong
    `expected` (measured against the `expected = None` mutation by a reviewer). The
    MAPPING leg is the dry-run twin, which reports `would-clear` instead of
    `would-rewrite` when the sweep stops computing it. What this test pins is the
    pair the sweep REPORTS — `from`/`to` are its own computation, so a sweep that
    could not say what it moved away from would fail here.
    """
    i, t, g = _world(sdk, tag="sweep-lagging")
    _lagging_word(sdk, i, t, g)
    report = reconcile_goal_states(sdk)
    assert report["counts"]["rewritten"] == 1, report
    assert report["counts"]["cleared"] == 0, report
    assert sdk.get_entity(g["id"])["goalState"] == "unmet", (
        "the cache still names the superseded claim")
    record = _only([r for r in report["goals"] if r["goalId"] == g["id"]])
    assert (record["from"], record["to"], record["action"]) == (
        "met", "unmet", "rewritten"), record


def test_the_repair_sweep_leaves_a_CONSISTENT_goal_alone_and_writes_NOTHING(sdk):
    """Idempotence, and the difference between "repaired" and "was already
    right": a sweep that re-writes every goal it sees journals a revision per
    goal per run and reports work it did not do."""
    i, t, g = _world(sdk, tag="sweep-consistent")
    _judge(sdk, i, t, g)
    _derived_word_standing(sdk, g)
    writes: list[tuple[str, dict]] = []
    real_update = sdk.update_entity

    def counting(node_id, **props):
        writes.append((node_id, dict(props)))
        return real_update(node_id, **props)

    sdk.update_entity = counting
    try:
        report = reconcile_goal_states(sdk)
    finally:
        sdk.update_entity = real_update
    assert report["counts"]["repaired"] == 0, report
    assert report["counts"]["consistent"] == 1, report
    assert [w for w in writes if w[0] == g["id"]] == [], (
        f"the sweep wrote to an already-correct goal: {writes}")
    assert sdk.get_entity(g["id"])["goalState"] == "met"


@pytest.mark.parametrize("word", ["active", "blocked", "guideline",
                                  "superseded", "abandoned"])
def test_a_goal_whose_word_THIS_ENGINE_never_wrote_is_not_even_a_CANDIDATE(
        sdk, word):
    """The reconcile's own rule, applied at the SWEEP's door: only a word
    `record_judgement` can write is a cache of a judgement. A human-set word is
    not a candidate at all, so a stale sweep cannot erase a decision nobody
    measured — and `superseded`/`abandoned` (the RETIRED_GOAL_STATES) are
    lifecycle decisions, not derived cache values."""
    _i, _t, g = _world(sdk, tag=f"sweep-human-{word}")
    sdk.update_entity(g["id"], goalState=word)
    report = reconcile_goal_states(sdk)
    assert report["counts"]["scanned"] == 0, report
    assert report["counts"]["repaired"] == 0, report
    assert sdk.get_entity(g["id"])["goalState"] == word


def test_a_LEGACY_goal_POINT_is_NOT_swept_COUNTED_and_the_reason_is_pinned(sdk):
    """THE BOUNDARY THAT MAKES THE REPAIR DURABLE, not a missing feature.

    `goalState` IS carried by legacy `:Point` goals (`projection/entities.py`
    declares the prop for the point write path, #7856), so this shape looks like
    the obvious second half of the sweep — and writing it is WRONG. The generic
    POINT branch of `sdk.update_entity` journals only the annotator dims and
    `mentions` (its own SCOPE note), so a `goalState` clear there is live-only.
    MEASURED on this lane with the durability harness (`check_consistency` +
    `rebuild_all`), one goal of each label: the OBJECT repair left
    `ok=True/divergence=None` and stayed cleared after a rebuild, while the POINT
    repair left `ok=False`, `divergence='unrecorded-mutation'`, and the rebuild
    restored `met` — a sweep that reports `cleared` every pass of a
    repair-then-rebuild loop, and that reds the journal/projection parity blaming
    a rogue writer it is itself.

    ⛔ SO IT IS DECLINED **AND COUNTED**: `counts.notDurable` is what keeps a sweep
    over unrepairable words distinguishable from a sweep over an empty graph — the
    same rule `counts.failed` keeps for a reconcile that did not run. THIS PIN
    FAILS WHENEVER `:Point` is re-added to the scan, with or without a durable
    point write: it distinguishes the label, not the durability. When the point
    `goalState` write becomes journalled (#4094's class), replace this pin with the
    Point arm of the durability test — that is the property the sweep actually
    needs, and it is asserted there for the labels that have it.
    """
    point = sdk.create_point("goal", "legacy goal [sweep-point]",
                             goalState="met")
    assert sdk.get_entity(point["id"])["goalState"] == "met", (
        "precondition: a legacy goal Point can hold the word")
    report = reconcile_goal_states(sdk)
    assert report["counts"]["scanned"] == 0, report
    assert report["counts"]["repaired"] == 0, report
    assert report["counts"]["notDurable"] == 1, (
        "the declined population must be COUNTED, or `scanned: 0` reads as a "
        f"clean graph while the word stands: {report}")
    assert report["notDurableReason"], (
        "a counter with no stated meaning is a counter a consumer ignores")
    assert sdk.get_entity(point["id"])["goalState"] == "met", (
        "the sweep wrote an UNJOURNALLED prop on a Point: the next rebuild "
        "reverts it and check_consistency blames a rogue writer — see the "
        "constant's block in tortoise/derived_invalidation.py")


def test_the_OBJECT_repair_is_JOURNALLED_so_a_rebuild_cannot_revert_it(tmp_path):
    """`derived = replay(journal)` must hold AFTER a repair — the same durability
    gate the retirement path carries, applied to the cache write (#12336).

    This is the test that WOULD have caught the legacy-Point mistake: it asserts
    the repair is journal-clean and SURVIVES a rebuild, which the Point arm cannot
    do. Without it the sweep's success is only ever a live-graph illusion on any
    label whose prop write is unwrapped by a replay fold.
    """
    import os

    from tortoise.config import is_db_uri
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    log = str(events / "events.jsonl")
    namespace = (f"test_suite_{os.urandom(4).hex()}"
                 if is_db_uri(os.environ.get("TORTOISE_DB_URI")) else None)
    durable = TortoiseSDK(str(tmp_path / "durable.db"), namespace=namespace,
                          event_log_path=log)
    try:
        i, t, g = _world(durable, tag="sweep-durable")
        made = _judge(durable, i, t, g)
        durable.delete_entity(made["point_id"])
        assert durable.get_entity(g["id"])["goalState"] == "met"
        before = _records(events)
        report = reconcile_goal_states(durable)
        assert report["counts"]["cleared"] == 1, report
        # ⛔ THE JOURNAL RECORD IS THE EVIDENCE, not `check_consistency`: measured
        # on this lane, an UNJOURNALLED clear of this exact prop still reads
        # `ok=True / divergence=None` (the first call re-baselines the mutated
        # graph, and `goalState` is not in the baseline digest's entity field set),
        # so a `check_consistency` leg here can never fail and would be a
        # decoration. The record is what survives a rebuild, and the rebuild below
        # is what proves it.
        new = _records(events)[len(before):]
        mutated = [r for r in new
                   if r.get("type") == "EntityMutated"
                   and (r.get("state") or {}).get("goalState", "absent") is None]
        assert mutated, (
            "the cache repair left NO replayable record carrying goalState — a "
            f"rebuild would resurrect the word. new records: {new}")
        assert mutated[-1].get("id") == g["id"], mutated[-1]
        durable._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert durable.get_entity(g["id"]).get("goalState") is None, (
            "a rebuild resurrected the word the sweep cleared")
    finally:
        durable.close()


def test_the_NON_OBJECT_repair_is_JOURNALLED_too(tmp_path):
    """The durability property is asserted for the WHOLE swept population, not
    only the label the defect was found on (#12412 review, cycle 3).

    The sweep's inclusion rule is "every label except `:Point`", justified by the
    claim that those labels' `goalState` writes are journalled — and the suite
    pinned that for `:Object` alone. A reviewer MEASURED the gap: making the
    non-Point journal arm drop `goalState` for `label != 'Object'` left the whole
    section-9 suite GREEN while every non-Object repair became live-only — the
    exact repair-then-rebuild loop the `:Point` exclusion exists to prevent,
    unpinned one label over. `:Subject` is the reachable non-Object case (the
    entity door routes a `subject` kind there by itself), so it is the one this
    asserts.
    """
    import os

    from tortoise.config import is_db_uri
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    log = str(events / "events.jsonl")
    namespace = (f"test_suite_{os.urandom(4).hex()}"
                 if is_db_uri(os.environ.get("TORTOISE_DB_URI")) else None)
    durable = TortoiseSDK(str(tmp_path / "durable-subject.db"),
                          namespace=namespace, event_log_path=log)
    try:
        subject = durable.create_subject("team probe [sweep-durable-subject]")
        durable.update_entity(subject["id"], goalState="met")
        before = _records(events)
        report = reconcile_goal_states(durable)
        assert report["counts"]["cleared"] == 1, report
        mutated = [r for r in _records(events)[len(before):]
                   if r.get("type") == "EntityMutated"
                   and r.get("id") == subject["id"]
                   and (r.get("state") or {}).get("goalState", "absent") is None]
        assert mutated, (
            "the repair of a non-Object goal left no replayable record carrying "
            f"goalState, so a rebuild reverts it. new records: "
            f"{_records(events)[len(before):]}")
        durable._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert durable.get_entity(subject["id"]).get("goalState") is None, (
            "a rebuild resurrected the word the sweep cleared on a :Subject")
    finally:
        durable.close()


def test_a_second_sweep_is_a_NO_OP(sdk):
    """Idempotence at the SWEEP level, not only at the door's: once repaired the
    goal holds no derived word, so it is not even a candidate — the next pass
    reports nothing to do instead of re-clearing or re-journalling it."""
    i, t, g = _world(sdk, tag="sweep-idempotent")
    made = _judge(sdk, i, t, g)
    sdk.delete_entity(made["point_id"])
    assert reconcile_goal_states(sdk)["counts"]["cleared"] == 1
    second = reconcile_goal_states(sdk)
    assert second["counts"]["scanned"] == 0, second
    assert second["counts"]["repaired"] == 0, second
    assert sdk.get_entity(g["id"]).get("goalState") is None


def _records(events):
    """Every journalled record in order — the durability surface a rebuild replays."""
    import json
    return [json.loads(line) for line in
            (events / "events.jsonl").read_text().splitlines() if line.strip()]


def test_a_goal_on_a_NON_OBJECT_label_is_swept_too(sdk):
    """The exclusion is ONE label, not a whitelist of labels (#12336 review
    cycle 2, P1).

    A `:Subject` goal takes an engine-written `goalState` through the JOURNALLED
    entity surface, and MEASURED it survives a rebuild — so it is a repairable
    candidate like any other, and an `:Object`-only scan reported `scanned: 0`
    over it: a clean summary over a stale word, the exact false PASS this module
    refuses. The entity door routes a `subject` kind to `:Subject` by itself, so
    this shape is reachable without anyone typing a label.
    """
    subject = sdk.create_subject("team probe [sweep-subject]")
    sdk.update_entity(subject["id"], goalState="met")
    assert sdk.get_entity(subject["id"])["goalState"] == "met"
    report = reconcile_goal_states(sdk)
    assert report["counts"]["scanned"] == 1, (
        "the sweep reported a clean scan over a non-:Object goal holding a "
        f"derived word: {report}")
    assert report["counts"]["cleared"] == 1, report
    assert sdk.get_entity(subject["id"]).get("goalState") is None
    assert report["counts"]["notDurable"] == 0, report
    assert report["notDurableReason"] is None, (
        "the reason is emitted only when the counter is non-zero — a payload "
        "that carries a reason on a clean sweep trains a consumer to ignore it")
    assert report["counts"]["unaddressable"] == 0, report


def test_a_goal_with_no_ID_cannot_be_repaired_and_is_COUNTED_not_dropped(sdk):
    """The second declined population, and the reason it is a COUNTER (#12412
    review, cycle 3): the scan matches on the word, but the entity surface can
    only address a node by `id`.

    Reachability is narrow — the `:Source` write surface refuses `goalState`, so
    only a raw or legacy write produces the shape — which is exactly why a silent
    `if row[0]` filter would never be noticed: the payload would be byte-identical
    to an empty graph's while the word stood.
    """
    sdk._get_proj().g.query(
        "CREATE (n:Object {goalState:'met', url:'https://example.test/no-id'})")
    report = reconcile_goal_states(sdk)
    assert report["counts"]["scanned"] == 0, report
    assert report["counts"]["unaddressable"] == 1, (
        f"the sweep matched a candidate it silently dropped: {report}")
    standing = sdk._get_proj().g.query(
        "MATCH (n:Object {url:'https://example.test/no-id'}) RETURN n.goalState"
    ).result_set
    assert standing and standing[0][0] == "met", standing


def test_the_repair_sweep_dry_run_writes_nothing_and_names_what_it_would_clear(sdk):
    i, t, g = _world(sdk, tag="sweep-dry-run")
    made = _judge(sdk, i, t, g)
    sdk.delete_entity(made["point_id"])
    report = reconcile_goal_states(sdk, dry_run=True)
    assert report["dryRun"] is True, report
    assert report["counts"]["repaired"] == 0, report
    assert report["counts"]["wouldRepair"] == 1, report
    assert sdk.get_entity(g["id"])["goalState"] == "met", (
        "a dry run wrote to the cache")
    record = _only([r for r in report["goals"] if r["goalId"] == g["id"]])
    assert record["action"] == "would-clear", record


def test_the_repair_sweep_discloses_its_own_BOUND(sdk):
    """A capped scan that reads complete is the #11823 defect class this module
    exists to close, so the sweep reports `truncated` rather than a total that
    looks exhaustive."""
    for n in range(2):
        i, t, g = _world(sdk, tag=f"sweep-bound-{n}")
        made = _judge(sdk, i, t, g)
        sdk.delete_entity(made["point_id"])
    report = reconcile_goal_states(sdk, limit=1)
    assert report["counts"]["scanned"] == 1, report
    assert report["truncated"] is True, (
        "the scan hit its cap and did not say so")


def test_a_failed_reconcile_is_REPORTED_and_the_rest_of_the_sweep_survives(sdk):
    """A sweep is a sequence of independent writes: one goal's refused write is
    recorded on THAT goal, and the other goals still get repaired. And — the rule
    the module already applies to `counts.failed` — a sweep may not read clean
    while a goal still shows a derived word."""
    worlds = []
    for n in range(2):
        i, t, g = _world(sdk, tag=f"sweep-fail-{n}")
        made = _judge(sdk, i, t, g)
        sdk.delete_entity(made["point_id"])
        worlds.append(g)
    doomed = worlds[0]["id"]
    real_update = sdk.update_entity

    def failing(node_id, **props):
        if node_id == doomed:
            raise RuntimeError("transient write failure")
        return real_update(node_id, **props)

    sdk.update_entity = failing
    try:
        report = reconcile_goal_states(sdk)
    finally:
        sdk.update_entity = real_update
    assert report["counts"]["failed"] == 1, report
    assert report["counts"]["repaired"] == 1, report
    failed = _only([r for r in report["goals"] if r.get("error")])
    assert failed["goalId"] == doomed, failed
    assert "transient write failure" in failed["error"], failed
    assert sdk.get_entity(worlds[1]["id"]).get("goalState") is None, (
        "one refused write discarded another goal's repair")
    assert sdk.get_entity(doomed).get("goalState") == "met", (
        "the module's own rule is that a sweep may not read clean while a goal "
        "still displays a word it could not repair — so the failed goal's word "
        "must still be standing here, and the caller has to see it")


def test_a_word_a_HUMAN_sets_mid_sweep_is_PRESERVED_and_reported(sdk):
    """The scan and the reconcile are two reads, so a human write can land
    between them. The candidate set was chosen by the WORD that stood at scan
    time; by the time the cache is repaired the word may be a decision nobody
    measured, and clearing it would erase that decision. It is reported rather
    than silently skipped — a sweep may not read clean over a goal it declined to
    touch."""
    i, t, g = _world(sdk, tag="sweep-human-race")
    made = _judge(sdk, i, t, g)
    sdk.delete_entity(made["point_id"])
    real_get = sdk.get_entity

    def racing(node_id):
        if node_id == g["id"]:
            return {"id": node_id, "objectKind": "goal", "goalState": "blocked"}
        return real_get(node_id)

    writes: list[tuple[str, dict]] = []
    real_update = sdk.update_entity

    def counting(node_id, **props):
        writes.append((node_id, dict(props)))
        return real_update(node_id, **props)

    sdk.get_entity = racing
    sdk.update_entity = counting
    try:
        report = reconcile_goal_states(sdk)
    finally:
        sdk.get_entity = real_get
        sdk.update_entity = real_update
    assert report["counts"]["preserved"] == 1, report
    assert report["counts"]["repaired"] == 0, report
    assert report["counts"]["cleared"] == 0, report
    record = _only([r for r in report["goals"] if r["goalId"] == g["id"]])
    assert record["action"] == "preserved", record
    assert [w for w in writes if w[0] == g["id"]] == [], (
        f"the sweep wrote over the word it reported as preserved: {writes}")


def test_a_goal_DELETED_mid_sweep_is_reported_GONE_not_repaired(sdk):
    """The same two-read window, the other outcome: there is no node to point at
    anything, so the sweep must report `gone` rather than a `cleared` that landed
    nowhere (a write to a missing id is a silent no-op)."""
    i, t, g = _world(sdk, tag="sweep-gone-race")
    made = _judge(sdk, i, t, g)
    sdk.delete_entity(made["point_id"])
    real_get = sdk.get_entity

    def vanishing(node_id):
        if node_id == g["id"]:
            return {}
        return real_get(node_id)

    sdk.get_entity = vanishing
    try:
        report = reconcile_goal_states(sdk)
    finally:
        sdk.get_entity = real_get
    assert report["counts"]["gone"] == 1, report
    assert report["counts"]["repaired"] == 0, report
    record = _only([r for r in report["goals"] if r["goalId"] == g["id"]])
    assert record["action"] == "gone", record


def test_a_dry_run_over_a_LAGGING_word_reports_would_rewrite_and_writes_nothing(sdk):
    """`would-rewrite` is a documented outcome of the preview and it had no test:
    a dry run over the CLEAR shape proves only half the preview. The two shapes
    differ in what the caller is told it will do, and a consumer that branches on
    `action` would read a lagging goal as 'nothing to do' shape if the clear arm
    were the only one exercised."""
    i, t, g = _world(sdk, tag="sweep-dry-run-rewrite")
    _lagging_word(sdk, i, t, g)
    writes: list[tuple[str, dict]] = []
    real_update = sdk.update_entity

    def counting(node_id, **props):
        writes.append((node_id, dict(props)))
        return real_update(node_id, **props)

    sdk.update_entity = counting
    try:
        report = reconcile_goal_states(sdk, dry_run=True)
    finally:
        sdk.update_entity = real_update
    assert report["counts"]["wouldRepair"] == 1, report
    assert report["counts"]["repaired"] == 0, report
    record = _only([r for r in report["goals"] if r["goalId"] == g["id"]])
    assert record["action"] == "would-rewrite", record
    assert (record["from"], record["to"]) == ("met", "unmet"), record
    assert [w for w in writes if w[0] == g["id"]] == [], (
        f"a dry run wrote to the cache: {writes}")
    assert sdk.get_entity(g["id"])["goalState"] == "met"


def test_a_DOOR_reported_reconcile_error_is_a_FAILED_goal_not_a_clean_one(sdk):
    """The door REPORTS a reconcile it could not run (`checked: False` + `error`,
    because its own `get_entity` raised) instead of raising. The sweep must fold
    that into `counts.failed` rather than counting the goal as repaired: the word
    is still standing, so a sweep that called it clean is the false PASS this whole
    module is about.

    ⚠️ SCOPE, so this test is not read as more than it is: a door that RAISED here
    would land the goal in the same `failed` bucket through the sweep's own catch-
    all, so this test does NOT discriminate report-vs-raise (verified: making the
    door re-raise leaves it green). What it pins is that the door's reported
    failure is not swallowed into `repaired`/`cleared`, and that the goal is
    repairable once the read works again.
    """
    i, t, g = _world(sdk, tag="sweep-door-error")
    made = _judge(sdk, i, t, g)
    sdk.delete_entity(made["point_id"])
    real_get = sdk.get_entity

    def failing(node_id):
        if node_id == g["id"]:
            raise RuntimeError("transient read failure")
        return real_get(node_id)

    sdk.get_entity = failing
    try:
        report = reconcile_goal_states(sdk)
    finally:
        sdk.get_entity = real_get
    assert report["counts"]["failed"] == 1, report
    assert report["counts"]["repaired"] == 0, report
    assert report["counts"]["cleared"] == 0, report
    record = _only([r for r in report["goals"] if r["goalId"] == g["id"]])
    assert record["action"] == "failed", record
    assert "transient read failure" in record["error"], record
    # the repair route still works once the read does
    reconcile_goal_state_cache(sdk, g["id"])
    assert sdk.get_entity(g["id"]).get("goalState") is None


def test_a_FAILED_standing_claim_read_is_a_FAILED_goal_and_the_sweep_carries_on(sdk):
    """The FIRST error arm, distinct from the door's: if the read that decides
    what the word SHOULD be cannot run, the sweep must not guess (a guess would
    either clear a supported word or rewrite a correct one) and must not abandon
    the remaining goals."""
    import tortoise.derived_invalidation as _mod

    worlds = []
    for n in range(2):
        i, t, g = _world(sdk, tag=f"sweep-read-fail-{n}")
        made = _judge(sdk, i, t, g)
        sdk.delete_entity(made["point_id"])
        worlds.append(g)
    doomed = worlds[0]["id"]
    original = _mod.latest_judgement

    def failing(sdk_, *, goal_id=None, indicator_id=None, target_id=None):
        if goal_id == doomed:
            raise RuntimeError("standing-claim read failed")
        return original(sdk_, goal_id=goal_id, indicator_id=indicator_id,
                        target_id=target_id)

    _mod.latest_judgement = failing
    try:
        report = reconcile_goal_states(sdk)
    finally:
        _mod.latest_judgement = original
    assert report["counts"]["failed"] == 1, report
    assert report["counts"]["repaired"] == 1, report
    failed = _only([r for r in report["goals"] if r.get("error")])
    assert failed["goalId"] == doomed, failed
    assert "standing-claim read failed" in failed["error"], failed
    assert sdk.get_entity(doomed)["goalState"] == "met", (
        "the sweep GUESSED at a word whose deciding read failed")
    assert sdk.get_entity(worlds[1]["id"]).get("goalState") is None, (
        "one failed read discarded another goal's repair")


def test_a_reconcile_that_NEVER_RAN_is_a_FAILED_goal_never_a_repair(sdk):
    """The door answers `checked: False` (with a `reason`, no `error`) when it was
    handed no goal id. Unreachable from the scan here — the candidate read keeps
    only rows carrying an id — so this pins the RULE rather than a live path: a
    reconcile that did not run must never be counted as one that did, because the
    word is still standing and a sweep that books it as `rewritten` is the false
    PASS this whole module is about."""
    import tortoise.derived_invalidation as _mod

    i, t, g = _world(sdk, tag="sweep-never-ran")
    made = _judge(sdk, i, t, g)
    sdk.delete_entity(made["point_id"])
    original = _mod.reconcile_goal_state_cache

    def not_run(sdk_, goal_id):
        return {"checked": False,
                "reason": "no goalId was supplied to the reconcile"}

    _mod.reconcile_goal_state_cache = not_run
    try:
        report = reconcile_goal_states(sdk)
    finally:
        _mod.reconcile_goal_state_cache = original
    assert report["counts"]["failed"] == 1, report
    assert report["counts"]["repaired"] == 0, report
    assert report["counts"]["rewritten"] == 0, report
    record = _only([r for r in report["goals"] if r["goalId"] == g["id"]])
    assert record["action"] == "failed", record
    assert "no goalId was supplied" in record["error"], record
    assert sdk.get_entity(g["id"])["goalState"] == "met"
