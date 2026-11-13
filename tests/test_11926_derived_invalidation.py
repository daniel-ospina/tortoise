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
import inspect
import re

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
    # ⛔ AND THE RECONCILE STILL RAN, sourced from the CLAIM (#12336 review
    # cycle 11): this `Invalidation` names no goal, so passing
    # `invalidation.goal_id` alone left `checked: False` over a claim that DOES
    # carry `goalId` — the dependent state unreconciled on the exact shape the
    # module block above says must be repaired.
    assert result["cache"]["checked"] is True, result["cache"]


def test_an_already_terminal_claim_reconciles_the_goal_the_CLAIM_names(sdk):
    """A caller that names no goal must still reconcile: the claim is the source.

    The shape is real and supported (the legacy-flag pin above builds it): an
    `Invalidation(judgement_id=…)` with no `goal_id`, over a claim retired out of
    band, so `verdicts`/`invalidate_stale` never sees THAT CLAIM again — and once
    it was the goal's last live claim, no automatic path repairs the goal's
    displayed word (#12336); while another live claim stands, that claim's own
    stale finding does. MEASURED by review: reading the
    id from `invalidation.goal_id` alone made this case report
    `cache.checked: False` while `goalState` still read `met` — and a skipped
    check moves neither `counts.failed` nor `counts.gone`, so the sweep read
    clean over a goal whose word names a claim that no longer counts.

    The mutation this excludes, stated per arm because the claim was previously
    broader than the evidence: reverting the ALREADY-TERMINAL arm to
    `invalidation.goal_id` alone fails this test and the legacy-flag pin above;
    reverting the POST-EXCEPTION arm fails
    `test_the_POST_EXCEPTION_terminal_arm_reconciles_too`
    (which now names no goal, on purpose); and reverting the SUCCESS arm fails
    `test_the_success_arm_reconciles_the_goal_the_CLAIM_names`. Every other test
    builds the `Invalidation` with the goal the claim carries, which is why each
    of the three was green before.
    """
    i, t, g = _world(sdk, tag="terminal-goal-from-claim")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    # out of band: no door call, so the word above is left naming this claim
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) SET p.outdated = 'true'",
        params={"id": made["point_id"]},
    )
    finding = Invalidation(judgement_id=made["point_id"], reason="target-revised",
                           detail="…")
    result = invalidate(sdk, finding)
    assert result["alreadyRetired"] is True, result
    assert result["found"] is True, result
    assert result["cache"]["checked"] is True, result["cache"]
    assert sdk.get_entity(g["id"]).get("goalState") is None


def test_the_success_arm_reconciles_the_goal_the_CLAIM_names(sdk):
    """The arm that ACTUALLY retires is the one where a skipped reconcile is fatal.

    #12315 review cycle 12. Every other success-path test goes through
    `invalidate_stale`, where `invalidation_for` copies `goalId` from the claim —
    so a DIRECT `invalidate()` call with a goal-less `Invalidation` was the one
    shape nobody built, and it passed `invalidation.goal_id` (None) to the
    reconcile. The claim is terminal after this call, so `invalidate_stale` never
    scans it again: the goal keeps the derived word FOREVER, and neither
    `counts.failed` nor `counts.gone` moves for the skip. The result must also
    report the resolved id, not the caller's `None`.
    """
    i, t, g = _world(sdk, tag="success-goal-from-claim")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    # the claim is LIVE and stale: the bar moved above the recorded reading
    sdk.update_entity(t["id"], threshold=8.0)
    finding = Invalidation(judgement_id=made["point_id"], reason="target-revised",
                           detail="threshold moved")
    result = invalidate(sdk, finding)
    assert result["invalidated"] is True, result
    assert not result.get("alreadyRetired"), result
    assert result["cache"]["checked"] is True, result["cache"]
    assert result["goalId"] == g["id"], result
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the goal still displays a word nothing backs: "
        f"{sdk.get_entity(g['id']).get('goalState')!r}")


def test_the_CLAIMS_goal_wins_when_the_caller_names_a_DIFFERENT_one(sdk):
    """Precedence, pinned where the two readings DIVERGE (#12315 review cycle 13).

    The rule is deliberate: the claim's own `goalId` says whose displayed word
    names this claim, so that is the state to repair; a caller's id that diverges
    names a goal that is not the one showing the stale word. Before this test the
    suite never built the divergent case, so inverting the precedence left every
    test green while three arms leaned on it — the `[pin-defensive-claim]` shape:
    the case where the readings differ is the case that has to be built.

    `reconcile_goal_state_cache` RE-DERIVES from the goal's live claims rather
    than clearing blindly, so the caller's goal here is not merely skipped, it is
    correctly left `met`: its own claim is live and still met.
    """
    i1, t1, g1 = _world(sdk, tag="precedence-claim")
    made = _judge(sdk, i1, t1, g1)
    i2, t2, g2 = _world(sdk, tag="precedence-caller")
    _judge(sdk, i2, t2, g2)
    assert sdk.get_entity(g1["id"])["goalState"] == "met"
    assert sdk.get_entity(g2["id"])["goalState"] == "met"
    sdk.update_entity(t1["id"], threshold=8.0)  # g1's claim goes stale, still live
    finding = Invalidation(judgement_id=made["point_id"], reason="target-revised",
                           detail="threshold moved", goal_id=g2["id"])
    result = invalidate(sdk, finding)
    assert result["invalidated"] is True, result
    # the CLAIM's goal, not the caller's — in the record and in the repair
    assert result["goalId"] == g1["id"], result
    assert sdk.get_entity(g1["id"]).get("goalState") is None, (
        "the claim's own goal still displays a word nothing backs: "
        f"{sdk.get_entity(g1['id']).get('goalState')!r}")
    assert sdk.get_entity(g2["id"])["goalState"] == "met", (
        "the caller's unrelated goal was reconciled: "
        f"{sdk.get_entity(g2['id']).get('goalState')!r}")


def _a_second_goal(sdk, tag):
    """Another goal whose word is DIRTY, so a reconcile onto it would show.

    ⛔ `met` ALONE WOULD NOT DISCRIMINATE (#12315 review cycle 15). A caller-first
    mutant reconciles the goal the CALLER named, and `reconcile_goal_state_cache`
    RE-DERIVES from that goal's live claims — so a second goal with a live, `met`
    claim would be written back as `met` and the "untouched" assertion would pass
    under the very mutation it claims to exclude. Its claim is therefore retired
    out of band while the word is left `met`: a reconcile onto it clears the word
    to `None`, which the callers assert against.
    """
    i, t, g = _world(sdk, tag=tag)
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) SET p.outdated = 'true'",
        params={"id": made["point_id"]},
    )
    assert sdk.get_entity(g["id"])["goalState"] == "met", (
        "the fixture's premise: a retired-out-of-band claim leaves the word "
        "naming it, which is exactly what a stray reconcile would clear")
    return g


def test_the_PRECEDENCE_holds_in_the_ALREADY_TERMINAL_arm(sdk):
    """Same rule, second arm (#12315 review cycle 14).

    The divergent case above reaches only the SUCCESS arm, so flipping the `or`
    in this arm (caller-first) left every test green: every other test either
    names no goal (both orders then resolve to the claim) or names the goal the
    claim already carries. The direction is the recorded decision, so each arm
    that implements it needs the case where the two readings differ.
    """
    i, t, g1 = _world(sdk, tag="prec-terminal")
    made = _judge(sdk, i, t, g1)
    g2 = _a_second_goal(sdk, "prec-terminal-other")
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) SET p.outdated = 'true'",
        params={"id": made["point_id"]},
    )
    result = invalidate(sdk, Invalidation(judgement_id=made["point_id"],
                                          reason="target-revised", detail="…",
                                          goal_id=g2["id"]))
    assert result["alreadyRetired"] is True, result
    # ⛔ THE RECORD NAMES THE GOAL IT RECONCILED (#12315 review cycle 16): the
    # documented repair route is "call `reconcile_goal_state_cache` again with the
    # goal's id", so a `cache.error` here must be walkable from the record — and
    # a direct `invalidate()` caller has no `report["stale"]` to join against.
    assert result["goalId"] == g1["id"], result
    assert sdk.get_entity(g1["id"]).get("goalState") is None, (
        "the claim's own goal still displays a word nothing backs: "
        f"{sdk.get_entity(g1['id']).get('goalState')!r}")
    assert sdk.get_entity(g2["id"])["goalState"] == "met", (
        "the caller's unrelated goal was reconciled: "
        f"{sdk.get_entity(g2['id']).get('goalState')!r}")


def test_the_PRECEDENCE_holds_in_the_POST_EXCEPTION_arm(sdk, monkeypatch):
    """Same rule, third arm — the re-read (`after`) is where the id comes from."""
    i, t, g1 = _world(sdk, tag="prec-post-exception")
    made = _judge(sdk, i, t, g1)
    g2 = _a_second_goal(sdk, "prec-post-exception-other")

    def retired_by_a_PEER(point_id, **_kwargs):
        sdk.update_entity(point_id, status="retracted")
        raise ValueError("already terminal")

    monkeypatch.setattr(sdk, "retract_point", retired_by_a_PEER)
    result = invalidate(sdk, Invalidation(judgement_id=made["point_id"],
                                          reason="target-revised", detail="…",
                                          goal_id=g2["id"]))
    assert result["alreadyRetired"] is True, result
    assert result["goalId"] == g1["id"], result
    assert sdk.get_entity(g1["id"]).get("goalState") is None, (
        "the claim's own goal still displays a word nothing backs: "
        f"{sdk.get_entity(g1['id']).get('goalState')!r}")
    assert sdk.get_entity(g2["id"])["goalState"] == "met", (
        "the caller's unrelated goal was reconciled: "
        f"{sdk.get_entity(g2['id']).get('goalState')!r}")


@pytest.mark.parametrize("shape", ["success", "terminal", "post-exception",
                                   "vanish"])
def test_a_claim_that_NAMES_NO_goal_falls_back_to_the_CALLERs(sdk, monkeypatch,
                                                              shape):
    """The `or invalidation.goal_id` half of every arm's precedence (#12315 c17).

    ⛔ THE FALLBACK IS A REACHABLE SHAPE, NOT DECORATION, and it was unpinned in
    all four arms: deleting `or invalidation.goal_id` from every local left the
    suite green, because every other test that reaches these locals reads a claim
    carrying a `goalId`. A claim can name none — `invalidation_for` documents
    "a corrupt or partial claim" as reachable, and `REMOVE p.goalId` produces it —
    and with the fallback gone that shape SKIPS the reconcile: `cache.checked` is
    `False` and neither `counts.failed` nor `counts.gone` moves, which is the
    silent class the
    helper exists to make visible (#12336).
    """
    i, t, g = _world(sdk, tag=f"fallback-{shape}")
    made = _judge(sdk, i, t, g)
    claim_id = made["point_id"]
    sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) REMOVE p.goalId", params={"id": claim_id},
    )
    assert sdk.get_entity(claim_id).get("goalId") is None, (
        "the fixture's premise: the claim names no goal at all")

    if shape == "success":
        sdk.update_entity(t["id"], threshold=8.0)  # live, and stale
    elif shape == "terminal":
        sdk._get_proj().g.query(
            "MATCH (p:Point {id:$id}) SET p.outdated = 'true'",
            params={"id": claim_id},
        )
    elif shape == "post-exception":
        def retired_by_a_PEER(point_id, **_kwargs):
            sdk.update_entity(point_id, status="retracted")
            raise ValueError("already terminal")
        monkeypatch.setattr(sdk, "retract_point", retired_by_a_PEER)
    else:
        sdk.update_entity(t["id"], threshold=8.0)  # live, and stale

        def vanish_during_retract(point_id, **_kwargs):
            sdk.delete_entity(point_id)
            raise ValueError("no such point")
        monkeypatch.setattr(sdk, "retract_point", vanish_during_retract)

    result = invalidate(sdk, Invalidation(judgement_id=claim_id,
                                          reason="target-revised", detail="…",
                                          goal_id=g["id"]))
    assert result["cache"]["checked"] is True, (
        "the reconcile was SKIPPED because the claim named no goal and the "
        f"caller's id was not used as the fallback: {result['cache']!r}")
    assert result["goalId"] == g["id"], result
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the caller's goal still displays a word nothing backs: "
        f"{sdk.get_entity(g['id']).get('goalState')!r}")


def test_the_PRECEDENCE_holds_when_the_claim_VANISHES(sdk, monkeypatch):
    """Same rule, fourth arm — the claim read at the top is the only source left."""
    i, t, g1 = _world(sdk, tag="prec-vanish")
    made = _judge(sdk, i, t, g1)
    g2 = _a_second_goal(sdk, "prec-vanish-other")
    sdk.update_entity(t["id"], threshold=8.0)  # live, and stale

    def vanish_during_retract(point_id, **_kwargs):
        sdk.delete_entity(point_id)
        raise ValueError("no such point")

    monkeypatch.setattr(sdk, "retract_point", vanish_during_retract)
    result = invalidate(sdk, Invalidation(judgement_id=made["point_id"],
                                          reason="target-revised",
                                          detail="threshold moved",
                                          goal_id=g2["id"]))
    assert result["alreadyRetired"] is True, result
    assert result["found"] is False, result
    # the id survives the claim: this is the arm where it cannot be re-read
    assert result["goalId"] == g1["id"], result
    assert sdk.get_entity(g1["id"]).get("goalState") is None, (
        "the goal of the claim that vanished still displays a word: "
        f"{sdk.get_entity(g1['id']).get('goalState')!r}")
    assert sdk.get_entity(g2["id"])["goalState"] == "met", (
        "the caller's unrelated goal was reconciled: "
        f"{sdk.get_entity(g2['id']).get('goalState')!r}")


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
    with no AUTOMATIC repair afterwards because no live judgement remains for a
    later sweep to reconcile (the manual, public, idempotent
    ``reconcile_goal_state_cache`` is the route, and this sweep's own step-4
    reconcile is what the assertions below measure)."""
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
    """Why these are the only shapes a MINTED dwell claim can reach the undecidable
    report through.

    (The undecidable arm has three routes in total; this paragraph covers only the
    one this test can build. `undecidable_scope` and the "cannot be ordered"
    boundary branch are separate routes, pinned elsewhere.)

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
    assert retired["found"] is True, retired   # the SUCCESS arm reports it too
    assert retired["validTo"] == "2026-11-12T12:00:00+00:00"
    assert retired["reason"] == "target-revised"
    assert retired["detail"] == first.detail
    # a second sweep finds NOTHING to do — the claim is no longer live, so it is
    # not even scanned; and a direct second retire says so instead of raising.
    assert invalidate_stale(sdk, goal_id=g["id"])["counts"]["scanned"] == 0
    again = invalidate(sdk, first)
    assert again["invalidated"] is False
    assert again["alreadyRetired"] is True
    assert again["found"] is True, again       # it WAS read, and was terminal
    assert sdk.get_entity(made["point_id"])["status"] == "retracted"


def test_retiring_a_missing_claim_reports_it_rather_than_raising(sdk):
    """An absent claim is the idempotent outcome, and it says WHICH one (#12315).

    ``alreadyRetired`` is the honest field for it — a claim that does not exist
    cannot be stale — and it is what keeps the finding inside a ``counts`` bucket;
    the previous ``False`` left it in none (see the mid-sweep test below).
    """
    report = invalidate(sdk, Invalidation(judgement_id="no-such-point",
                                          reason="target-revised", detail="…"))
    assert report["invalidated"] is False
    assert report["alreadyRetired"] is True
    assert report["status"] is None
    # ⛔ `found` is the MECHANICAL discriminator between the two facts
    # `alreadyRetired` now covers; `status` is not, because a claim whose
    # terminal predicate was the legacy `outdated` flag also reports None.
    assert report["found"] is False
    assert "no such judgement" in report["detail"]
    # no goal is named on this direct call, so there is nothing to reconcile —
    # REPORTED as such rather than assumed clean (#12336)
    assert report["cache"]["checked"] is False
    assert "goalId" in report["cache"]["reason"]
    # ⛔ AND THE RECORD STILL NAMES THE GOAL IT WOULD HAVE RECONCILED, so a
    # `cache.error` here is repairable from the record alone — the absent arm is
    # the one with no claim, so the caller's id is all there is (#12315 review
    # cycle 16). `None` when the caller named none, which is the honest value.
    assert "goalId" in report, report
    assert report["goalId"] is None, report


def test_the_VANISH_BETWEEN_READ_AND_RETIREMENT_reconciles_the_CLAIMS_goal(sdk, monkeypatch):
    """The goal id survives the claim: read BEFORE it vanished, used after.

    #12315 review cycle 13. The vanish-before-retirement arm re-reads the claim,
    finds it gone, and reports the idempotent outcome — but the dict read at the
    TOP of `invalidate` is still in scope, and its `goalId` is the one fact that
    says whose displayed word may still name the vanished claim (#12336). Passing
    only `invalidation.goal_id` made a caller that names no goal reconcile
    NOTHING here, while the counters read clean — and this is the arm where the
    loss is permanent: the claim no longer exists, so no later sweep can find it.
    """
    i, t, g = _world(sdk, tag="vanish-before-retirement")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"])["goalState"] == "met"
    sdk.update_entity(t["id"], threshold=8.0)  # live, and stale

    def vanish_during_retract(point_id, **_kwargs):
        sdk.delete_entity(point_id)  # gone between the read and the write
        raise ValueError("no such point")

    monkeypatch.setattr(sdk, "retract_point", vanish_during_retract)
    result = invalidate(sdk, Invalidation(judgement_id=made["point_id"],
                                          reason="target-revised",
                                          detail="threshold moved"))
    assert result["alreadyRetired"] is True, result
    assert result["found"] is False, result
    assert "vanished before the retirement" in result["detail"], result
    assert result["cache"]["checked"] is True, result["cache"]
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the goal still displays a word for a claim that vanished: "
        f"{sdk.get_entity(g['id']).get('goalState')!r}")


def test_a_claim_that_VANISHES_mid_sweep_lands_in_a_counts_bucket(sdk, monkeypatch):
    """⛔ #12315, reproduced. ``invalidate``'s MISSING arm returned
    ``alreadyRetired: False`` with no ``error`` and no ``cache`` — and
    ``invalidate_stale`` has no bucket for that shape. So a claim deleted between
    the scan and the retirement (a concurrent purge, a peer's cleanup) produced
    ``stale: 1`` with ``retired``/``alreadyRetired``/``failed``/``gone`` ALL zero:
    the per-finding record was honest and the SUMMARY could not be reconciled,
    which is the rule the ``failed`` and ``gone`` buckets were added to keep.

    The probe is the issue's own reproduction — delete the claim immediately
    before the retirement, so ``invalidate`` takes its MISSING arm.
    """
    i, t, g = _world(sdk, tag="vanish-mid-sweep")
    made = _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=12.0)  # a real stale finding

    from tortoise import derived_invalidation as di

    real_invalidate = di.invalidate

    def vanishing(sdk_, invalidation, **kwargs):
        sdk_.delete_entity(invalidation.judgement_id)
        return real_invalidate(sdk_, invalidation, **kwargs)

    monkeypatch.setattr(di, "invalidate", vanishing)
    report = invalidate_stale(sdk, goal_id=g["id"])
    counts = report["counts"]
    assert counts["stale"] == 1, counts
    assert counts["retired"] == 0, counts
    assert counts["alreadyRetired"] == 1, (
        "the vanished claim landed in NO bucket — a consumer totalling the "
        f"buckets cannot reconcile this summary: {counts}")
    assert counts["failed"] == 0 and counts["gone"] == 0, counts

    per_finding = report["invalidated"][0]
    assert per_finding["invalidated"] is False
    assert per_finding["alreadyRetired"] is True
    assert per_finding["found"] is False, per_finding
    assert "no such judgement" in per_finding["detail"]

    # ⛔ THE OVERLAP RULE — that `retired` and `failed` (or `gone`) can BOTH count
    # one finding, so the buckets are per-finding predicates and never a partition
    # — is deliberately NOT asserted here, and this is the measurement that decided
    # it: with a single finding whose `stale` and `alreadyRetired` are already
    # asserted above (`stale` 1, `retired` 0, `alreadyRetired` 1), any
    # `accounted >= stale` line is ARITHMETICALLY FORCED true, so no mutant can
    # red it — a dead assertion that would read as coverage. The rule is pinned
    # where it can actually fail: `test_a_failed_cache_write_counts_as_a_FAILED_sweep`
    # (one finding counted in `retired` AND `failed`) and
    # `test_a_GOAL_that_no_longer_EXISTS_is_reported_gone_not_cleared`
    # (`retired` 1 AND `gone` 1 — NOT the absent-claim goal-gone test below, whose
    # counts are `retired` 0 AND `gone` 1, i.e. the opposite pair); it is
    # documented on `invalidate_stale`.
    #
    # ⛔ AND THE GOAL'S CACHE IS RECONCILED, not just the bucket (#12336). The
    # cached word named the claim that vanished, and nothing after this sweep
    # would revisit the goal — `invalidate_stale` scans LIVE claims, so a goal
    # whose last claim is gone is never a finding again. Without the reconcile
    # the goal keeps displaying `met` with nothing behind it while the summary
    # above reads clean.
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the goal still displays a word nothing backs: "
        f"{sdk.get_entity(g['id']).get('goalState')!r}")
    assert per_finding["cache"]["checked"] is True, per_finding["cache"]
    # nothing was written for a claim that no longer exists (a missing point reads
    # back as a falsy dict, which is why `_read_claim` folds it to MISSING)
    assert not sdk.get_entity(made["point_id"])


def test_an_ALREADY_TERMINAL_claim_also_reconciles_the_goal_word(sdk):
    """#12336's second door-side half, on the arm that used to skip it entirely.

    A claim retired OUT OF BAND (a direct status write, a purge) leaves the goal's
    ``goalState`` word naming it. It IS cleared automatically once ANOTHER of that
    goal's claims goes stale — that finding's own step-4 reconcile re-derives the
    word from whatever still stands — but for a goal whose LAST claim is gone the
    repair route is the manual, public, idempotent
    ``reconcile_goal_state_cache``, because ``invalidate_stale`` scans LIVE claims
    and a gone claim never becomes a finding again.
    MEASURED before this round: ``goalState`` stayed ``met``, and the finding
    carried NO ``cache`` key at all — so `(r.get("cache") or {})` read clean and
    neither ``counts.failed`` nor ``counts.gone`` could see it.
    """
    i, t, g = _world(sdk, tag="terminal-reconciles")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"]).get("goalState") == "met"
    sdk.update_entity(made["point_id"], status="retracted")  # out of band

    report = invalidate(sdk, Invalidation(judgement_id=made["point_id"],
                                          goal_id=g["id"],
                                          reason="target-revised", detail="…"))
    assert report["alreadyRetired"] is True, report
    assert report["found"] is True, report
    assert report["cache"]["checked"] is True, report["cache"]
    assert report["cache"]["cleared"] is True, report["cache"]
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the goal still displays a word nothing backs: "
        f"{sdk.get_entity(g['id']).get('goalState')!r}")


def test_an_absent_claim_whose_GOAL_is_gone_lands_in_GONE_too(sdk, monkeypatch):
    """The clause #12315 forces onto ``counts.gone``'s docstring: a finding in the
    ABSENT arm can set ``gone`` with NO retirement having landed (the reconcile
    could not happen because there is no goal node), so ``gone`` there means
    "the cache could not be reconciled", never "a retirement landed".
    """
    i, t, g = _world(sdk, tag="vanish-goal-gone")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=12.0)  # a real stale finding

    from tortoise import derived_invalidation as di

    real_invalidate = di.invalidate

    def vanishing(sdk_, invalidation, **kwargs):
        sdk_.delete_entity(invalidation.judgement_id)
        sdk_.delete_entity(g["id"])
        return real_invalidate(sdk_, invalidation, **kwargs)

    monkeypatch.setattr(di, "invalidate", vanishing)
    counts = invalidate_stale(sdk, goal_id=g["id"])["counts"]
    assert counts["alreadyRetired"] == 1, counts
    assert counts["gone"] == 1, counts        # the cache could not be reconciled
    assert counts["retired"] == 0, counts     # nothing was retired here
    assert counts["failed"] == 0, counts


def test_the_POST_EXCEPTION_terminal_arm_reconciles_too(sdk, monkeypatch):
    """The third already-retired arm (#12336), which the claim-side predicate
    normally MASKs.

    ``invalidate`` re-reads the claim after ``retract_point`` raises, so this arm is
    only reachable when the claim becomes terminal BETWEEN those two reads — which
    is exactly the concurrent sweep it was written for. Reaching it needs the raise
    and the status flip together. ⚠️ Measured when this test was written: deleting
    the arm's ``cache`` key left the whole suite green — a dead arm that read as
    coverage. ``test_a_reconcile_that_RAISES_is_reported_not_raised`` now reaches
    that arm's cache report as well, so the arm is no longer singly-pinned.
    """
    i, t, g = _world(sdk, tag="post-exception")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"]).get("goalState") == "met"

    def retired_by_a_PEER(point_id, **_kwargs):
        sdk.update_entity(point_id, status="retracted")
        raise ValueError("already terminal")

    monkeypatch.setattr(sdk, "retract_point", retired_by_a_PEER)
    # ⛔ NO `goal_id` HERE, ON PURPOSE (#12315 review cycle 12): passing it makes
    # the claim-sourced read short-circuit, so the fallback this arm exists to
    # provide was evaluated by NO test — reverting it to `invalidation.goal_id`
    # alone left the suite green at the commit before this pin.
    report = invalidate(sdk, Invalidation(judgement_id=made["point_id"],
                                          reason="target-revised", detail="…"))
    assert report["alreadyRetired"] is True, report
    assert report["found"] is True, report
    assert "already terminal" in report["detail"], report
    assert report["cache"]["checked"] is True, report["cache"]
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the goal still displays a word nothing backs: "
        f"{sdk.get_entity(g['id']).get('goalState')!r}")


def test_a_claim_that_vanishes_BEFORE_the_retirement_lands_is_alreadyRetired(
        sdk, monkeypatch):
    """The SECOND vanish window (#12315 review, P1): the claim is deleted between
    the read at the top of ``invalidate`` and ``retract_point``, so the refusal
    comes back for a claim that is no longer there.

    ⛔ IT IS NOT A SECOND DEFECT CLASS. An absent claim cannot be stale — the rule
    the MISSING arm states — and this arm only reaches the same fact by a longer
    route: the claim WAS live when ``invalidate`` read it, and a peer's purge
    landed inside the call. Collapsing the post-raise re-read to ``None`` reported
    that benign no-op as ``failed`` AND skipped the goal's reconcile, which is the
    #12336 residual this module closes for every other already-retired arm:
    measured before this round, ``goalState`` stayed ``met`` naming a claim that no
    longer existed, and no later sweep could revisit THAT CLAIM — only another of
    the goal's claims going stale could reconcile the word, and that is luck, not
    a route this arm may rely on (a later ``judge_and_record`` that reaches a
    decided state also REWRITES the word, but it mints a new claim, so it is not
    a repair of this one).

    ⚠️ ``found`` here is ``False``, not ``None``, and that is the point: ``None``
    is reserved for a re-read that could not answer (``after is None``). "Gone" is
    an answer, and it is the one this race produces.
    """
    i, t, g = _world(sdk, tag="vanish-before-retract")
    made = _judge(sdk, i, t, g)
    assert sdk.get_entity(g["id"]).get("goalState") == "met"

    def purged_by_a_PEER(point_id, **_kwargs):
        sdk.delete_entity(point_id)          # the peer's cleanup, then its refusal
        raise ValueError(f"no such point {point_id}")

    monkeypatch.setattr(sdk, "retract_point", purged_by_a_PEER)
    report = invalidate(sdk, Invalidation(judgement_id=made["point_id"],
                                          goal_id=g["id"],
                                          reason="target-revised", detail="…"))
    assert report["invalidated"] is False, report
    assert report["alreadyRetired"] is True, report
    assert report["found"] is False, report
    # a cancel is never a failure: no `error` key, so `counts.failed` cannot see it
    assert "error" not in report, report
    assert report["cache"]["checked"] is True, report["cache"]
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the goal still displays a word nothing backs: "
        f"{sdk.get_entity(g['id']).get('goalState')!r}")

    # ...and the same race reached through a SWEEP lands in `alreadyRetired`, never
    # in `failed`. The sweep is where #12315's bucket-totality rule lives, and this
    # arm is only reachable with a claim that was STALE when the scan read it.
    i2, t2, g2 = _world(sdk, tag="vanish-before-retract-sweep")
    _judge(sdk, i2, t2, g2)
    sdk.update_entity(t2["id"], threshold=12.0)  # a real stale finding
    report2 = invalidate_stale(sdk, goal_id=g2["id"])
    counts = report2["counts"]
    assert counts["stale"] == 1, counts
    assert counts["alreadyRetired"] == 1, counts
    assert counts["retired"] == 0 and counts["failed"] == 0, counts
    assert report2["invalidated"][0]["found"] is False, report2["invalidated"][0]
    assert sdk.get_entity(g2["id"]).get("goalState") is None, (
        "the sweep's finding vanished and the goal's word was never reconciled: "
        f"{sdk.get_entity(g2['id']).get('goalState')!r}")


def test_a_reconcile_that_RAISES_is_reported_not_raised(sdk, monkeypatch):
    """``_reconcile_or_report``'s contract: the idempotent retirement outcome is
    never discarded because the cache follow-up broke.

    ``reconcile_goal_state_cache`` wraps only ``sdk.get_entity``; the
    ``latest_judgement`` read and the two ``update_entity`` writes after it are NOT
    wrapped, so a read failure there or a write failure (the shape
    ``test_a_failed_cache_write_counts_as_a_FAILED_sweep`` models) both make the
    reconcile raise. ``latest_judgement`` is used here because it is the one
    reachable without patching the SDK's write path.

    MEASURED without the guard: the exception leaves ``invalidate`` and is caught
    by ``invalidate_stale``'s per-finding handler, so the finding still lands in a
    bucket — but in ``failed`` rather than ``alreadyRetired`` (``counts``
    ``alreadyRetired`` 1 -> 0) and the record degrades to the generic refusal
    shape that carries ``error`` only, so the idempotent outcome AND
    ``cache.error`` are both lost.
    """
    i, t, g = _world(sdk, tag="reconcile-raises")
    _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=12.0)  # a real stale finding

    from tortoise import derived_invalidation as di

    def boom(*_args, **_kwargs):
        raise RuntimeError("graph read exploded")

    def retired_by_a_PEER(point_id, **_kwargs):
        sdk.update_entity(point_id, status="retracted")
        raise ValueError("already terminal")

    monkeypatch.setattr(di, "latest_judgement", boom)
    monkeypatch.setattr(sdk, "retract_point", retired_by_a_PEER)
    report = invalidate_stale(sdk, goal_id=g["id"])
    counts = report["counts"]
    assert counts["stale"] == 1, counts
    assert counts["alreadyRetired"] == 1, counts
    assert counts["failed"] == 1, counts      # reported, never raised
    # ⛔ AND `counts.undecidable` IS PINNED ON THIS SHAPE TOO (#12315 review).
    # A drift that folds either count into `undecidable`
    # (`len(undecidable) + len(stale)`, or `+ len(invalidated)`) reds this line —
    # the second one had NO pin anywhere while this line was absent, because the
    # dry-run site's `invalidated` is empty by construction and cannot see it.
    assert counts["undecidable"] == 0, counts
    # the finding keeps its idempotent outcome AND its cache report; the generic
    # refusal shape (the mutant's) has neither
    per_finding = report["invalidated"][0]
    assert per_finding["alreadyRetired"] is True, per_finding
    assert "graph read exploded" in per_finding["cache"]["error"], per_finding


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
    # ⛔ THE DOCUMENTED DISCRIMINATOR IS TOTAL (#12315). The docstring tells a
    # caller to separate the absent arm from the read arms on `found`, and this
    # is the one record a caller reads first when the sweep reported a failure:
    # while the key was omitted here, `record["found"]` was a KeyError and
    # `.get("found")` read `None` — indistinguishable from an unnamed bucket,
    # which is the #12315 defect class one level out. `None` is the conservative
    # answer: this synthetic raise happens before any read, and the trailing
    # `raise exc` in `invalidate` re-raises `retract_point`'s own exception, which
    # carries nothing about the read (the `claim is None` refusal does name itself
    # — "could not read judgement" — but the sweep does not parse prose).
    assert failed[0]["found"] is None, failed[0]
    # ⛔ AND `cache` IS A KEY A REFUSAL STILL OMITS, which is correct rather
    # than untidy: no reconcile ran, so there is no report to carry — but an
    # absent key must never be the whole answer, which is why `found` is present
    # above. Pinned here because nothing else fails if a plausible `cache` is
    # invented for a reconcile that never happened (the same class as the
    # `counts.gone` fold this module already fixed).
    assert "cache" not in failed[0], failed[0]
    # ⛔ AND IT OMITS `goalId` FOR THE SAME REASON, which the docstring now states
    # as a RULE (#12315 review cycle 17): `goalId` is present exactly where
    # `cache` is, because it names the goal a reconcile RAN AGAINST. `inv.goal_id`
    # is in scope here, so writing it in would report a reconcile that never
    # happened — the same class of untruth as inventing a `cache`.
    assert "goalId" not in failed[0], failed[0]
    # ⛔ AND A REFUSAL IS NOT AN ALREADY-RETIRED OUTCOME — the one word in this
    # synthetic record that could silently invert the summary (fresh-context
    # review, 2026-11-13). Flip `"alreadyRetired": False` to `True` where the
    # record is built and the suite stayed GREEN before these two lines: every
    # other `counts["alreadyRetired"]` assertion in this file is `== 1`, so
    # nothing pinned the zero side anywhere, and this is the only sweep that
    # contains a refusal record. The counter would then book a REFUSAL as
    # "already retired, nothing to do" — the #12315 defect class (a summary that
    # misreports a finding) one arm over, and the same shape as the `found` /
    # `cache` / `goalId` traps pinned just above.
    assert report["counts"]["alreadyRetired"] == 0, report["counts"]
    assert failed[0]["alreadyRetired"] is False, failed[0]


# ── 8. the sweep: scope, limit, dry run, and no re-derivation ────────────

def test_dry_run_writes_nothing_and_names_what_it_would_retire(sdk):
    i, t, g = _world(sdk, tag="dry-run")
    made = _judge(sdk, i, t, g)
    sdk.update_entity(t["id"], threshold=8.0)
    report = invalidate_stale(sdk, goal_id=g["id"], dry_run=True)
    assert report["dryRun"] is True
    assert report["counts"]["stale"] == 1
    # ⛔ `counts.undecidable` IS PINNED ON A SHAPE WHERE A DRIFT REDS IT (#12315
    # review). A sweep over one stale finding decides that finding, so nothing is
    # undecidable and the counter must read 0; `counts["stale"] == 1` is asserted
    # just above, so anything that folds `stale` into `undecidable` reds this
    # line. It also fails under the same mutant on the retirement-arm site, which
    # pins the `invalidated` half of the expression — so the line lives on BOTH
    # shapes: neither site alone sees every drift.
    assert report["counts"]["undecidable"] == 0, report["counts"]
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


def test_ANOTHER_claims_stale_finding_clears_the_word_an_out_of_band_retirement_left(sdk):
    """The SAME goal, repaired by the SWEEP, with no manual reconcile anywhere.

    This pins the qualifier the door-side docstrings state (``invalidate``'s
    ``alreadyRetired`` note and ``_reconcile_or_report``): a goal whose LAST claim
    is gone is never a finding again, so that shape needs the manual, public,
    idempotent ``reconcile_goal_state_cache`` — but while the goal still holds
    ANOTHER live claim, that claim's own stale finding repairs the word
    automatically, because its step-4 reconcile re-derives from whatever stands.
    Without this test the prose can regress to "no automatic path repairs it",
    which a single-claim world makes look true.
    """
    i, t, g = _world(sdk, tag="auto-repair")
    other = sdk.create_object("CI p95 <= 12m [auto-repair]", objectKind="target",
                              comparator="<=", threshold=12.0)
    first = _judge(sdk, i, t, g, value=11.0, at="2026-11-11T10:00:00Z")
    second = _judge(sdk, i, other, g, value=11.0, at="2026-11-11T11:00:00Z")
    assert sdk.get_entity(g["id"])["goalState"] == "met"

    # OUT OF BAND: a direct status write retires the NEWEST claim, so no door runs
    # and no reconcile happens — the word still names the claim that just left.
    sdk.update_entity(second["point_id"], status="retracted")
    assert sdk.get_entity(g["id"])["goalState"] == "met"

    # Now the FIRST claim's own justification goes (its target's bar moved). The
    # goal's LAST claim is therefore gone, and this finding's step-4 reconcile is
    # the automatic repair the docstring names — no manual call below.
    sdk.update_entity(t["id"], threshold=13.0)
    report = invalidate_stale(sdk, goal_id=g["id"])
    assert report["counts"]["retired"] == 1
    assert [f["judgementId"] for f in report["stale"]] == [first["point_id"]]
    assert report["invalidated"][0]["goalId"] == g["id"]
    assert report["invalidated"][0]["cache"]["cleared"] is True
    assert sdk.get_entity(g["id"]).get("goalState") is None, (
        "the sweep's own reconcile did not clear the word the out-of-band "
        "retirement left")


#: Up to three words of inserted text between two anchors of one denial.
#: A family stated as a LITERAL SUBSTRING is defeated by one word inserted INSIDE
#: the phrase (round 31: "nothing EVER repairs"); a FILLER allowlist is defeated by
#: any adverb off the list (round 32: "nothing STRICTLY repairs", "nothing QUIETLY
#: repairs"). So the gap is ingredient-agnostic: any word, at most three, and the
#: patterns run per SENTENCE so a match cannot cross a boundary.
_GAP = r"(?:\s+\w+){0,3}\s+"


def _family(*anchors: str) -> re.Pattern[str]:
    """Compile one repair-denial family as an anchor SEQUENCE with a bounded gap."""
    return re.compile(_GAP.join(anchors))


#: Every phrase family by which this PR's prose has denied a repair that exists.
_REPAIR_DENIALS = (
    _family(r"no", r"automatic", r"(?:path|repair|route|sweep|pass)"),
    _family(r"no", r"(?:later|subsequent|further)", r"(?:pass|sweep|scan|revisit\w*)"),
    _family(r"nothing", r"(?:else\s+)?(?:repairs?|fixes?|clears?|reconciles?|remediates?)"),
    _family(r"never", r"(?:a\s+)?finding", r"again"),
    re.compile(r"un[\s-]?repairable"),  # one token or two, hyphen or space
    _family(r"cannot", r"be", r"repaired"),
    _family(r"no", r"repair", r"(?:route|path|possible)"),
)

#: Sentences the net flags which state a TRUE fact, exempted by a stable marker AND
#: a reason. Exemptions are themselves asserted, so one cannot outlive its sentence.
_TRUE_NON_DENIALS = (
    # `nothing` is the cache's OBJECT ("holding nothing"), not an actor that repairs.
    "(or holding nothing) is reconciled",
)

#: The qualifiers that make a denial TRUE. The routing fact (#12315/#12336) is
#: that a goal holding ANY other live claim is reconciled by that claim's own
#: stale finding, so only the last-claim shape needs the MANUAL route.
_REPAIR_QUALIFIERS = (
    "once the goal holds no other live claim",
    "once its last live claim is gone",
    "whose last claim is gone",
    "while a live claim",
    "while another live claim",
    # Naming the goal sweep is the strongest admissible qualifier: a denial next to
    # `reconcile_goal_states` tells the reader where the automatic repair IS.
    "reconcile_goal_states",
)


def _assert_repair_denials_are_qualified(text: str, site: str) -> None:
    """Every repair denial in ``text`` must carry a qualifier in its OWN SENTENCE.

    A sentence, not a window: an inserted false clause in its own sentence must
    inherit nothing from a neighbour (round 30's window form let
    ``Nothing else repairs it afterwards.`` pass by sitting next to a qualified
    sentence). And anchors with a bounded gap, not substrings, so one inserted
    word cannot remove a denial from the net.
    """
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.lower().split())):
        # Match on a punctuation-FLATTENED copy: the gap is `\w`-only, so inserted
        # punctuation breaks an anchor without changing the claim — round 32 walked
        # through it with "never, EVER a finding again", "no automatic self-REPAIR
        # path" and "un-repairable", all green. The QUALIFIER test still reads the
        # raw sentence, so flattening cannot manufacture a qualifier.
        if any(marker in sentence for marker in _TRUE_NON_DENIALS):
            continue
        # A hyphen inside a word is a JOIN in one reading and a SEPARATOR in the
        # other, and both readings deny: "re-paired" is "repaired" (round 33) while
        # "self-repaired" is "self repaired" (round 32). So the sentence is matched
        # under BOTH -- joined and split -- and a hit in either one is a denial. The
        # QUALIFIER test still reads the raw sentence, so neither reading can
        # manufacture a qualifier.
        joined = re.sub(r"(?<=\w)[-'\u2019](?=\w)", "", sentence)
        split = re.sub(r"[^\w\s]", " ", sentence)
        for pattern in _REPAIR_DENIALS:
            m = pattern.search(joined) or pattern.search(split)
            if m is None:
                continue
            assert any(q in sentence for q in _REPAIR_QUALIFIERS), (
                f"{site}: {m.group(0)!r} is stated in a sentence with no qualifier "
                "— every repair denial must say WHICH shape needs the MANUAL "
                f"route, in its OWN sentence; found: {sentence[:170]!r}")


def test_the_module_docs_state_the_REPAIR_BOUNDARY_not_an_absolute():
    """The absolute regressed FIVE times, so pin the PROSE, not only the behaviour.

    `verdicts`' cap is pinned the same way ("the docstring is the only place a
    caller can learn …"), and here the docstrings are likewise the artifact a
    consumer reads: the sweep's `cache` dict is not something a caller can inspect
    for a missing repair. The behaviour is pinned by
    `test_ANOTHER_claims_stale_finding_clears_the_word_an_out_of_band_retirement_left`;
    this pins that the words still agree with it — as a (site x family) grid,
    because rounds 27-31 each escaped a narrower form of this assertion.

    ⛔ DECLARED RESIDUAL (round 30, unchanged by round 31): a false denial
    inserted INTO a sentence that already carries a qualifier is not caught here.
    That shape makes the sentence SELF-CONTRADICTORY ("no later pass revisits it
    once its last live claim is gone, and nothing ever repairs the word
    afterwards") and no text pin can separate it from legitimate prose without
    freezing every sentence verbatim — which would false-block benign rewording by
    the other writer on this branch. Stated rather than hidden.

    WHAT IS NOT RESIDUAL — material inserted INSIDE the family phrase, and what
    closes each of the three rounds that found it:

    * round 31, an inserted WORD ("nothing EVER repairs") — the families were
      literal substrings, so `_family` builds them as anchor SEQUENCES with a gap;
    * round 32, inserted PUNCTUATION ("never, EVER a finding again", "no automatic
      self-repair path") — punctuation is a separator, so the sentence is also
      matched on a split-normalised copy;
    * round 33, an arbitrary adverb off a filler allowlist ("nothing STRICTLY
      repairs") and a hyphen that JOINS a word ("cannot be re-paired") — the gap is
      now ingredient-agnostic (any word, at most three), and the sentence is matched
      under BOTH readings of an intra-word hyphen, joined ("re-paired" ->
      "repaired") and split ("self-repaired" -> "self repaired").

    Matching under both readings flags exactly one TRUE sentence, so that sentence is
    exempted by stable marker with its reason in `_TRUE_NON_DENIALS` — and the
    exemption is itself asserted, so it cannot outlive the prose it excuses.
    """
    import tortoise.derived_invalidation as di

    door = " ".join((di.invalidate.__doc__ or "").lower().split())
    helper = " ".join((di._reconcile_or_report.__doc__ or "").lower().split())
    cache_doc = " ".join((di.reconcile_goal_state_cache.__doc__ or "").lower().split())
    # Strip the leading comment markers so a WRAPPED comment reads as one sentence.
    norm = " ".join(
        re.sub(r"(?m)^\s*#", "", inspect.getsource(di.invalidate)).lower().split())

    # ---- POSITIVE anchors: zero occurrences must FAIL, never pass vacuously ----
    assert "claims going stale later does repair" in door, (
        "the `alreadyRetired` note must say WHICH shape repairs itself")
    assert "a goal whose last claim is gone is never a finding again" in door, (
        "and which shape cannot")
    assert "no claim-side pass can reach that shape" in door, (
        "and that no CLAIM-side pass reaches it — the claim that replaced the "
        "false 'no automatic path', which main's goal sweep (#12336) made wrong: "
        "the sweep IS the automatic path")
    assert "the repair starts from the goal" in door, (
        "the note must name WHERE the repair starts, not only what fails")
    assert "that reaches a decided state" in door, (
        "and the re-judge route must stay scoped to a DECIDED state: `unknown` "
        "writes nothing (`_write_goal_state_cache` skips a None)")
    for site, text in (("invalidate.__doc__", door),
                       ("_reconcile_or_report.__doc__", helper),
                       ("reconcile_goal_state_cache.__doc__", cache_doc),
                       ("invalidate (source + comments)", norm)):
        assert "no automatic path" not in text, (
            f"{site}: 'no automatic path' is FALSE on the merged tree — "
            ":func:`reconcile_goal_states` is exactly that path (#12336)")
    assert "no claim-side pass repairs it once the goal holds no other live claim" in helper
    assert ":func:`reconcile_goal_states`" in helper, (
        "the helper must NAME the goal sweep; a denial that names no repair route "
        "is precisely the false absolute")
    assert "no claim-side pass revisiting that goal once its last live claim is gone" in cache_doc, (
        "the archived-goal bullet must say WHY the last-claim shape cannot recur here")
    assert "archived sweep retires them together" in cache_doc
    assert ":func:`reconcile_goal_states`" in cache_doc
    assert ("no claim-side pass repairs it later once the goal holds no other live "
            "claim") in norm, (
        "the absent-arm COMMENT must keep the qualifier IN the sentence")
    assert "reconcile_goal_states" in norm, (
        "and must name the sweep that closes the shape")
    for marker in _TRUE_NON_DENIALS:
        assert any(marker in t for t in (door, helper, cache_doc, norm)), (
            f"stale exemption {marker!r} — it no longer appears at any site, so it "
            "excuses nothing; delete it rather than leaving a blind spot")
    assert norm.count("no claim-side pass") == 2, (
        "EXACTLY TWO 'no claim-side pass' statements may stand in `invalidate` — the "
        "docstring's ('can reach that shape') and the absent-arm comment's "
        "('repairs it later ONCE …'); the adjacency assertion above cannot see a "
        "third one planted in another comment, so the count is the guard")

    # ---- the grid: every family, at every site -------------------------------
    for site, text in (
        ("invalidate.__doc__", door),
        ("_reconcile_or_report.__doc__", helper),
        ("reconcile_goal_state_cache.__doc__", cache_doc),
        ("invalidate (source + comments)", norm),
    ):
        _assert_repair_denials_are_qualified(text, site)
