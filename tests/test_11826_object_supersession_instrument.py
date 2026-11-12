"""Issue #11826 — an Object supersession must not be INVISIBLE.

The defect this file is about (#11826, measured 2026-11-11): an Object
supersession moves **no edges**. ``_fold_object_superseded`` writes
``status``/``supersededBy``/``supersededAt`` and nothing else, so an operator
edge stays anchored to a node the supersession just made terminal for EP. EP
then strips that input (``1/2 inputs stripped — factor skipped (degenerate,
#780/#2422)``), the factor stops funding the successor, and NOTHING reports it:
``audit.audit_graph``'s supersession checks were ``MATCH (n:Point)`` on both
sides, so the shape had no instrument at all.

What is pinned here is the INSTRUMENT (and the two honesty fixes in the same
issue's "also measured" list), never the transfer:

  * ``superseded_object_active_edges`` (high) — a superseded :Object that still
    carries an active IMPL/NAND edge, i.e. the defunded factor's shape —
    including the states of its OWN anchor that the emitted strings depend on and
    that the anchor does not rule out: a successorless supersede (the generic
    ``update_entity`` door reaches the status with no ``supersededBy``), a
    set-but-unresolvable pointer (a renamed successor), a self-pointer, a
    successor that was already rewired while the stale edge survived (the factor
    then RUNS — the belief is not lost and the repair reduces to the delete), a
    DIRECTIONAL operator whose idx-0 source is the stripped input (rewiring does
    not restore it, and a delete alone renumbers and inverts the factor), a
    DIRECT operator-less edge (no factor is built, so no skipped-factor record
    exists), an ``op_type``-only operator (EP's predicate is the flag OR
    ``op_type``, so it takes the operator arms, not the direct-edge one), an
    AMBIGUOUS successor name (N≥2 carriers — #3573 — where the check must not
    describe "the successor" by picking one carrier's status), and a supersession
    CHAIN with one hop of lookahead (the repair targets the next link, or says the
    chain has no resolvable head — including a terminal successor that names none);
  * ``superseded_by_unresolvable`` (medium) — ``supersededBy`` is a NAME, so
    renaming the successor dangles the only link the predecessor has to it
    (#3590 made the name mutable and the id the identity);
  * ``get_historical_knowledge(...)["current"]`` now carries ``status`` and
    ``terminal``, so a superseded chain head is not served as the claim that
    stands today.

⛔ SCOPE: both checks are ``:Object``-anchored. A superseded ``:Subject`` that is
an operator input has the same shape (the Subject lane moves no edges either, and
EP's participation predicate is label-agnostic) and is reported by NEITHER — a
declared false-clean, not an oversight; see the check's own note in
``tortoise/audit.py``.

⛔ THE TRANSFER IS NOT IMPLEMENTED HERE, deliberately, and the reason is a
RECORDED DECISION rather than effort: ``docs/ONTOLOGY.md`` §3.1 requires
semantic edges to be disposed of PER EDGE at write time (carry / drop / **pend**,
rationale stored per edge, "never silently bulk-transferred"), and #11824's
decision comment (C2/C3) records that the Point lane's bulk transfer is the
NON-conforming side, so propagating it to :Object would spread the violation.
When that disposition lands, ``test_the_measured_defund_is_reported`` is the
tripwire that must INVERT: the finding disappears because the successor
inherited the edge, not because the check stopped looking.
"""
from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tortoise.audit import audit_graph
from tortoise.commit_ops import apply_supersessions
from tortoise.sdk import TortoiseSDK
from tests._embedded import skip_if_no_falkor


@pytest.fixture
def sdk(tmp_path):
    """Fresh embedded SDK per test (unique DB path)."""
    if skip_if_no_falkor():
        pytest.skip("embedded FalkorDBLite unavailable")
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _q(sdk, query: str, params: dict | None = None) -> list:
    r = sdk._get_proj().g.query(query, params=params or {})
    return r.result_set if r else []


def _call(fix: str) -> str:
    """The door CALL a ``fix`` hands the caller (the last one it spells out)."""
    return fix[fix.rindex("tortoise_update_entity("):]


def _issues(sdk, issue_type: str) -> list:
    return [i for i in audit_graph(sdk._get_proj()).issues
            if i.issue_type == issue_type]


def _obj(sdk, name: str, **props) -> dict:
    """Create an :Object and return its NODE record (``create_entity`` wraps it)."""
    return sdk.create_entity("object", name, objectKind=props.pop("objectKind", "other"),
                             **props)["node"]


def _supersede_object(sdk, old_name: str, new_name: str) -> None:
    """Drive the REAL entity supersession lane (the one capture/ingest use)."""
    apply_supersessions(
        sdk._get_proj(), sdk,
        [{"superseded": old_name, "supersedes_by": new_name,
          "evidence": "policy v2 supersedes v1"}],
        session_id="sess_11826_supersede")


# ── 1. the measured defect gets an instrument ─────────────────────


def test_the_measured_defund_is_reported(sdk):
    """The issue's own measurement, replayed end to end.

    ``P --IMPL--> PolicyV1``, supersede ``PolicyV1`` with ``PolicyV2``: the
    successor inherits NOTHING (asserted, so this test cannot pass by the fix
    having silently landed) and the audit must SAY so.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])

    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    # The measured state: the fold terminalized V1 and moved no edge.
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.status, o.supersededBy",
              {"i": v1["id"]}) == [["superseded", "PolicyV2"]]
    assert _q(sdk, "MATCH (o:Object {id:$i})-[]-(x) RETURN x.id",
              {"i": v2["id"]}) == [], \
        "fixture drifted: the successor now carries an edge — if the §3.1 " \
        "disposition landed, invert this test rather than deleting it"

    issues = _issues(sdk, "superseded_object_active_edges")
    assert issues, (
        "the defunded factor has no instrument again — a superseded Object "
        "with a live IMPL edge is exactly the shape #11826 measured")
    issue = next(i for i in issues if i.node_id == v1["id"])
    assert issue.severity == "high", (
        "on an :Object the successor inherits nothing by construction, so the "
        "finding is the defect rather than a stray survivor (check 4's "
        f"medium is the :Point reading): {issue.severity!r}")
    assert "IMPL" in issue.detail and "inherits no belief" in issue.detail
    # The consequence is stated at its MEASURED scope: only a TWO-input factor is
    # skipped as degenerate; a wider one runs without the stripped input (diluted,
    # not zeroed). An unconditional "the factor is skipped" would overstate it.
    assert "degenerate" in issue.detail, issue.detail
    assert "PolicyV1" in issue.detail, issue.detail
    # ⛔ The repair must not name a door that MINTS an operator: `create_operator`
    # takes its `source_id` from the caller, so passing the operator's own id
    # builds a meta-operator over it and transfers no belief — guidance that
    # executes and builds the wrong graph (#7819's rule). No SDK door rewires an
    # operator, so the fix states the explicit edge write and names the slot.
    assert "tortoise_create_operator" not in issue.fix, issue.fix
    assert "PRESERVES the edge idx" in issue.fix, issue.fix
    assert "successor" in issue.fix, issue.fix
    assert issue.fix.startswith(f"Re-point this IMPL input from '{v1['id']}'"), issue.fix
    # A sketch that binds the destination as a FREE variable is the trap this
    # string must not carry: `MERGE (op)-[:IMPL]->(successor)` re-matches the
    # existing edge into the superseded node and the following delete removes the
    # operator's only input (measured, #11826 round 6). No `MERGE` here at all.
    for cypher in ("MERGE", "CREATE", "MATCH", "DELETE", "SET "):
        assert cypher not in issue.fix, (cypher, issue.fix)


def test_an_object_whose_belief_was_moved_is_not_reported(sdk):
    """The negative half: a successor that DID inherit is silent.

    Without this, the check could pass by reporting every superseded Object.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    other = sdk.create_point("statement", "the policy is published")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"], other["id"]])
    live = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert len(live["confidences"]) >= 2 and not live.get("skipped_factors"), (
        "fixture drifted: a two-input operator over live inputs must run: "
        + repr(live))
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    # The repair the emitted `fix` prescribes, performed literally: move the
    # operator's EXISTING input onto the successor (both endpoints bound, so MERGE
    # matches only that edge), preserving the idx, then drop the stale edge.
    _q(sdk, "MATCH (op:Point {id:$op})-[r:IMPL]->(sup:Object {id:$old}), "
            "(succ:Object {id:$new}) "
            "MERGE (op)-[nr:IMPL]->(succ) SET nr.idx = r.idx DELETE r",
       {"op": op["id"], "old": v1["id"], "new": v2["id"]})

    assert _q(sdk, "MATCH (o:Object {id:$i})-[]-(x) RETURN x.id", {"i": v2["id"]})
    assert _issues(sdk, "superseded_object_active_edges") == [], \
        "an operator wired to the SUCCESSOR is the repaired shape, not a finding"
    assert v1["id"] != v2["id"]


def test_a_deprecated_object_is_not_this_check_s_business(sdk):
    """Scope boundary, pinned: no successor exists to inherit anything.

    A ``deprecated``/``archived``/``retracted`` Object is terminal without a
    supersession write, so "the successor inherits no belief" cannot be phrased
    against it — and reporting it would be a false positive generated by the
    widening itself (``audit._superseded_w``'s recorded reason).
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    old = _obj(sdk, "PolicyV0")
    sdk.create_operator("IMPL", p["id"], [old["id"]])
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'deprecated'",
       {"i": old["id"]})

    assert _issues(sdk, "superseded_object_active_edges") == []


def test_the_check_exists_even_when_it_finds_nothing(sdk):
    """A check a clean graph never mentions cannot be trusted to be running.

    ``to_dict()`` lists only checks that produced issues, so the existence half
    is asserted on the uncapped ``check_counts`` — where a 0 means "ran, found
    nothing", never "absent".
    """
    _obj(sdk, "NothingSuperseded")
    counts = audit_graph(sdk._get_proj()).check_counts
    assert counts["superseded_object_active_edges"] == 0
    assert counts["superseded_by_unresolvable"] == 0


def test_a_successorless_supersede_still_gets_a_performable_repair(sdk):
    """The anchor is ``status='superseded'``, and the GENERIC door reaches that
    status with no successor at all.

    ``#11824``'s AX table records that ``update_entity(object,
    status='superseded')`` is accepted ON PURPOSE (the mirror shape is out of the
    guard — refusing it would over-block), so this is a state the engine produces,
    not a hypothetical. The emitted repair must then name the step that can
    actually be taken: a successor "named by its supersededBy" does not exist in
    this state, so the plain transfer wording would be an unperformable repair
    (#7819's rule) — and ``archived`` must NOT be offered as the repair, because
    it clears the ANCHOR without clearing the belief (asserted below on the engine,
    not just in the string).
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]])

    # The door the code deliberately permits (measured on this head).
    sdk.update_entity(v1["id"], status="superseded")
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.status, o.supersededBy",
              {"i": v1["id"]}) == [["superseded", None]], \
        "fixture drifted: this door no longer produces a successorless supersede"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues, "the successorless shape is still lost belief — report it"
    issue = issues[0]
    assert "no OTHER Object answers its supersededBy (none)" in issue.detail, \
        issue.detail
    assert "if a Subject or Point carries that name" in issue.detail, issue.detail
    assert "nothing to re-point this input onto" not in issue.detail, \
        "a Subject or Point carrying the name IS something to re-point onto: " \
        + issue.detail
    # ⛔ The repair must not send the reader to a node that does not exist, and it
    # must name the step that DOES exist (name the successor).
    assert "successor named by its supersededBy" not in issue.fix, issue.fix
    assert "No :Object answers the successor name" in issue.fix, issue.fix
    assert "If a Subject or Point carries that name" in issue.fix, issue.fix
    assert "'supersededBy': '<the successor name>'" in issue.fix, issue.fix
    # The door's own signature form (props as ONE dict), pinned again here because
    # this arm is new: a keyword spelling raises TypeError at the caller.
    assert f"tortoise_update_entity('{v1['id']}', {{" in issue.fix, issue.fix

    # ⛔ AND ``archived`` is a TRAP the string must warn about, not a repair it may
    # recommend: archiving clears this check's anchor while EP strips the input
    # exactly as before, so a re-audit reads CLEAN over a still-degenerate factor.
    # Both halves are measured here so the warning cannot rot.
    # ⛔ Archiving costs nothing to the DEFICIT — and, measured below, it DOES clear
    # the FINDING (the archived node leaves the anchor while EP still strips the
    # input). The string must say exactly that, or a reader archives, sees a clean
    # re-audit and stops. ⛔ It must NOT say "does NOT clear this" (#12111 review):
    # the anchor is ``status='superseded'``, so archiving DOES clear the row.
    assert "SILENCES THIS CHECK WITHOUT CLEARING THE DEFICIT" in issue.fix, issue.fix
    assert "does NOT clear this" not in issue.fix, issue.fix
    assert "ONE left is skipped as degenerate" in issue.fix, (
        "this row is a two-input operator, so its archive clause must name the "
        "arm that actually fires: " + issue.fix)
    sdk.update_entity(v1["id"], status="archived")
    assert _issues(sdk, "superseded_object_active_edges") == [], \
        "archiving did not clear the finding — the string's warning is now wrong"
    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert [s["reason"] for s in run.get("skipped_factors") or []] == ["degenerate"], \
        ("EP no longer strips an archived input, so archiving WOULD be a repair "
         f"and the warning must be withdrawn: {run.get('skipped_factors')}")


def test_a_barely_resolvable_pointer_takes_the_successorless_arm(sdk):
    """``supersededBy`` set but resolving to nothing is the SAME repair problem.

    A renamed successor (``#3590`` made the name mutable) leaves the pointer a
    string that addresses no node, so "re-point onto the successor named by its
    supersededBy" is as unperformable here as with no pointer at all — and the arm
    is selected on RESOLUTION, not on the field being present.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.name = 'PolicyV2b'", {"i": v2["id"]})

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "No :Object answers the successor name" in issues[0].fix, issues[0].fix
    assert "successor named by its supersededBy" not in issues[0].fix, issues[0].fix
    # The pointer is still SET — so this is the 'set but unresolvable' arm, and the
    # detail must quote the dead name rather than claim no pointer exists.
    assert "'PolicyV2'" in issues[0].detail, issues[0].detail
    assert "none is set" not in issues[0].detail, issues[0].detail


def test_a_directional_operators_stripped_source_is_not_repaired_by_deleting(sdk):
    """The idx-0 SOURCE slot of a directional operator is its own repair.

    EP drops the whole factor while that slot is terminal (the ``source_stripped``
    branch, ahead of the degenerate test), so a successor wired in alongside does
    NOT restore participation — and DELETING the stale edge alone is worse than
    useless: the surviving input is renumbered into idx 0 and the factor's
    direction inverts (``ep.py``'s own note). The emitted ``fix`` must say "write
    the successor into the SOURCE slot", not "re-point, preserving idx".
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    op = sdk.create_operator("IMPL", v1["id"], [p["id"]], direction="->")
    assert _q(sdk, "MATCH (o:Point {id:$i})-[r:IMPL]->(c:Object) RETURN r.idx",
              {"i": op["id"]}) == [[0]], \
        "fixture drifted: the superseded Object is no longer the idx-0 source"
    # Step 1 of the repair, wired anyway: the successor becomes an input.
    _q(sdk, "MATCH (op:Point {id:$op}), (succ:Object {id:$new}) "
            "MERGE (op)-[nr:IMPL]->(succ) SET nr.idx = 2",
       {"op": op["id"], "new": v2["id"]})
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert [s["reason"] for s in run.get("skipped_factors") or []] == ["source_stripped"], \
        ("a rewired directional source is no longer skipped — the arm is now "
         f"over-cautious: {run.get('skipped_factors')}")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "source_stripped" in issues[0].detail, issues[0].detail
    assert "a slot EP reads" in issues[0].detail, issues[0].detail
    # ⛔ The delete-only wording is exactly what must NOT appear for this row.
    assert "only that delete" not in issues[0].fix, issues[0].fix
    assert "into the SOURCE slot" in issues[0].fix, issues[0].fix
    assert "direction inverts" in issues[0].fix, issues[0].fix


def test_a_direct_edge_gets_the_direct_edge_upkeep(sdk):
    """A DIRECT (operator-less) edge is a different mechanism, so a different string.

    ``create_direct_edge("IMPL", <point>, <object>)`` writes
    ``Point-[:IMPL]->Object`` — no operator node, no ``idx``, and EP builds no
    factor record for it: with a terminal endpoint the row is excluded before
    evaluation, so there is no skipped-factor record to find and "preserve the
    edge idx" would name a property the edge does not have (measured:
    ``keys(r) == ['direction']``).
    """
    o = _obj(sdk, "PolicyV1")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    sdk.create_direct_edge("IMPL", p["id"], o["id"])
    assert _q(sdk, "MATCH (a:Point {id:$p})-[r:IMPL]->(b:Object {id:$o}) "
                   "RETURN r.idx, keys(r)",
              {"o": o["id"], "p": p["id"]})[0][0] is None
    _obj(sdk, "PolicyV2")
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'PolicyV2'", {"i": o["id"]})

    # The engine fact the wording rests on: no factor is built, so there is no
    # skipped-factor record — the exclusion happens before evaluation.
    run = sdk.compute_confidence(anchors=[p["id"]], require_calibration=False)
    assert not run.get("skipped_factors"), run.get("skipped_factors")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == o["id"]]
    assert issues, "an operator-less edge into a terminal endpoint is still the shape"
    issue = issues[0]
    assert "EP builds NO factor for a direct edge" in issue.detail, issue.detail
    assert "skipped-factor record" in issue.detail, issue.detail
    assert "it is a DIRECT edge" in issue.fix, issue.fix
    assert "PRESERVES the edge idx" not in issue.fix, issue.fix
    # ⛔ A DIRECT edge carries real belief while its target is live (measured: two
    # confidences before the supersession, none after), so its §3.1 disposition is
    # the carry/drop triage — NOT the DROP-only text that belongs to a neither-arm
    # row (#12111 review).
    assert "may CARRY to the successor" in issue.fix, issue.fix
    assert "the disposition here is DROP" not in issue.fix, issue.fix


def test_a_successorless_direct_edge_names_the_wiring_it_silences(sdk):
    """The successorless arm's archive clause is SPLIT by mechanism, and both
    halves must be true of their own row.

    An OPERATOR input's factor is built and its terminal input stripped (the
    ``degenerate`` record); a DIRECT edge builds no factor at all, because the row
    is excluded before evaluation. Archiving silences this check in both cases —
    the anchor is ``status='superseded'``, so the archived node leaves it — but only
    the first loses a belief EP had actually assembled. One sentence covering both
    was FALSE on one of them, so the clause branches and this pins the second arm
    (the first is pinned by
    ``test_a_successorless_supersede_still_gets_a_performable_repair``, which
    measures ``skipped_factors == ['degenerate']``).
    """
    o = _obj(sdk, "PolicyV1")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    sdk.create_direct_edge("IMPL", p["id"], o["id"])
    # SUPERSEDED with a successor name no Object answers: the successorless arm.
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'PolicyGone'", {"i": o["id"]})

    # The mechanism, measured: a direct edge into a terminal endpoint assembles
    # NOTHING, so there is no skipped-factor record for the clause to appeal to.
    run = sdk.compute_confidence(anchors=[p["id"]], require_calibration=False)
    assert not run.get("skipped_factors"), run.get("skipped_factors")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == o["id"]]
    assert issues, "a successorless direct edge is still lost wiring"
    fix = issues[0].fix
    assert "No :Object answers the successor name" in fix, fix
    assert "SILENCES THIS CHECK WITHOUT CLEARING THE WIRING" in fix, fix
    # ⛔ The property the docstring is about: no deficit is claimed on this row.
    # An assertion on a phrase only ONE arm emits would stay green if the direct
    # row took the operator arm (#12111 review).
    assert "CLEARING THE DEFICIT" not in fix, (
        "a direct edge builds no factor, so no deficit is at stake: " + fix)
    assert "EP still strips" not in fix, fix
    # The §3.1 triage rides the emission site, so a successorless row carries it
    # too — this is the arm that would go silent if `+ _triage(s)` were moved into
    # the resolvable branch (#12111 review). A DIRECT row keeps the carry triage.
    assert "ONTOLOGY §3.1's per-edge triage" in fix, fix
    assert "may CARRY to the successor" in fix, fix
    # Archiving does silence the CHECK (the finding goes away) — the clause says
    # so, and this measures it rather than trusting the sentence.
    sdk.update_entity(o["id"], status="archived")
    assert _issues(sdk, "superseded_object_active_edges") == [], \
        "archiving did not clear the finding — the clause's premise is wrong"


def test_a_successorless_neutral_operator_loses_no_belief_by_archiving(sdk):
    """The archive clause's NEUTRAL arm: nothing was carried, so nothing is lost.

    The sibling neutral test has a RESOLVABLE successor and therefore pins the
    ``fix`` arm of the resolvable path. The successorless path is a different
    string, and it must not tell a reader that archiving costs a belief this
    operator never transferred (``direction='-'`` returns from
    ``_update_nary_factor`` before any pair math).
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]], semantics="related")
    assert _q(sdk, "MATCH (o {id:$i}) RETURN o.direction", {"i": op["id"]}) == \
        [["-"]], "fixture drifted: the operator is not neutral"
    # SUPERSEDED with a successor name no Object answers: the successorless arm.
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'PolicyGone'", {"i": v1["id"]})

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues, "a successorless neutral operator is still leftover wiring"
    fix = issues[0].fix
    assert "No :Object answers the successor name" in fix, fix
    assert "SILENCES THIS CHECK WITHOUT CLEARING THE WIRING" in fix, fix
    assert "transfers NO confidence by design" in fix, (
        "a neutral operator carries nothing, so archiving cannot cost a belief: "
        + fix)
    assert "degenerate" not in fix, (
        "neither leg flows, so no factor is stripped and nothing is degenerate: "
        + fix)
    # ⛔ Severity stays `high` on purpose: a per-row demotion was measured against
    # the module's aggregates (one severity per check id — `_sev_total` kept the
    # FIRST sample's), so a medium row first made `summary.high` read 0 over a real
    # high row, a false PASS. Recorded in the 4b `_record` comment; the over-warn is
    # the safe direction until the aggregation is redesigned.
    assert issues[0].severity == "high", issues[0].severity
    assert "ONTOLOGY §3.1's per-edge triage" in fix, fix
    # ...and the triage must NOT offer a carry this operator cannot perform.
    assert "may CARRY to the successor" not in fix, (
        "a neutral operator carries nothing, so §3.1's disposition here is DROP: "
        + fix)
    sdk.update_entity(v1["id"], status="archived")
    assert _issues(sdk, "superseded_object_active_edges") == [], \
        "archiving did not clear the finding — the clause's premise is wrong"


def test_a_one_input_operator_claims_no_degenerate_skip(sdk):
    """A one-input operator going terminal is never even collected — say nothing more.

    ``create_operator("IMPL", <obj>, [])`` is door-accepted, and once its only
    input is terminal the operator never enters EP's affected set — measured on this
    fixture: no factor, no ``skipped_factors``, no run at all. So the clause must
    not promise a ``degenerate`` skip a reader could look for; the honest statement
    is that no factor existed to lose. The fixture form is the one
    ``test_an_operators_arity_is_not_asserted`` already establishes.
    """
    v1 = _obj(sdk, "PolicyV1")
    sdk.create_operator("IMPL", v1["id"], [], direction="->")
    assert _q(sdk, "MATCH ()-[r:IMPL]->() RETURN count(r)", {}) == [[1]], \
        "fixture drifted: the operator has more than one input"
    # SUPERSEDED with a successor name no Object answers: the successorless arm.
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'PolicyGone'", {"i": v1["id"]})

    # The measured engine fact: nothing is collected, so nothing is skipped.
    run = sdk.compute_confidence(require_calibration=False)
    assert not run.get("skipped_factors"), run.get("skipped_factors")
    assert not run.get("confidences"), (
        "the one-input operator was collected after all — then the fixture no "
        f"longer isolates the zero-remaining case: {run.get('confidences')}")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues, "the one-input operator's stale edge is still wiring"
    fix = issues[0].fix
    # The clause names the whole ladder, zero-remaining included, so a reader on
    # this row is told there was no factor rather than sent after a skip record.
    assert "has no factor to begin with" in fix, fix
    assert "ONTOLOGY §3.1's per-edge triage" in fix, fix


def test_a_neutral_direct_edge_carries_nothing_and_says_so(sdk):
    """A DIRECT edge's direction lives on the RELATIONSHIP, and EP reads it there.

    ``create_direct_edge(..., direction='-')`` is a door, and `ep.py` Batch 3 reads
    ``coalesce(r.direction, 'bidirectional')`` — the source NODE's ``direction``
    means nothing for this family. Projecting only ``active.direction`` made such a
    row read as flowing: it announced "the claim silently loses that support" and
    offered a §3.1 carry, while nothing had ever been transferred through it
    (measured below). The mirror error matters too — a node-level ``'-'`` on an
    ordinary direct edge read as neutral while the belief was real — and it is why
    ``_4b_state`` takes the direction from the EDGE for this family.
    """
    o = _obj(sdk, "PolicyV1")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    sdk.create_direct_edge("IMPL", p["id"], o["id"], direction="-")
    assert _q(sdk, "MATCH (:Point {id:$p})-[r:IMPL]->(:Object {id:$o}) "
                   "RETURN r.direction", {"p": p["id"], "o": o["id"]}) == \
        [["-"]], "fixture drifted: the edge is not neutral"
    # The engine fact: a neutral direct edge transfers nothing, even while live.
    before = sdk.compute_confidence(anchors=[p["id"]], require_calibration=False)
    assert not before.get("confidences"), before.get("confidences")

    _obj(sdk, "PolicyV2")
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == o["id"]]
    assert issues, "a neutral direct edge still leaves a stale edge on the Object"
    issue = issues[0]
    assert "no confidence ever transferred through it" in issue.detail, issue.detail
    assert "silently loses that support" not in issue.detail, (
        "nothing was ever transferred through a neutral edge: " + issue.detail)
    assert "may CARRY to the successor" not in issue.fix, (
        "a neutral edge carries nothing, so §3.1's disposition is DROP: "
        + issue.fix)
    assert "the disposition here is DROP" in issue.fix, issue.fix
    # ...and the archive clause must be the neutral one, not the flowing one.
    assert "neutral" in issue.fix, issue.fix
    assert "CLEARING THE DEFICIT" not in issue.fix, issue.fix
    # ⛔ The INSTRUCTION must agree with the triage: a neutral edge carried nothing,
    # so "re-point it onto the successor" contradicts the DROP appended after it.
    assert "Delete this IMPL edge" in issue.fix, issue.fix
    assert "Re-point this IMPL edge off" not in issue.fix, (
        "a neutral edge has no belief to preserve by re-pointing: " + issue.fix)


def test_a_direct_edge_into_a_shaped_successor_keeps_its_belief_claim(sdk):
    """The successor's shape does not turn a belief-carrying edge into "no factor".

    `_dropped_input` used ``is_operator`` as the "was a belief at stake?" test, so a
    DIRECT row (where the belief is real while the target is live) was told "EP
    assembles no factor for this row in the first place … the re-point moves no
    belief either way" — in the same `fix` string whose §3.1 clause says the IMPL
    ``may CARRY`` to the successor. Measured: two confidences before the
    supersession, none after, and two again once the edge is re-pointed onto a live
    successor.
    """
    o = _obj(sdk, "PolicyV1")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    sdk.create_direct_edge("IMPL", p["id"], o["id"])
    before = sdk.compute_confidence(anchors=[p["id"]], require_calibration=False)
    assert before.get("confidences"), (
        "fixture drifted: a direct edge builds no belief at all: "
        + repr(before.get("confidences")))
    # An OPERATOR-SHAPED successor: EP refuses it as a factor input (#9269).
    _obj(sdk, "PolicyV2", is_operator=True)
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == o["id"]]
    assert issues
    fix = issues[0].fix
    assert "OPERATOR-SHAPED" in fix, (
        "the successor is operator-shaped, which is why a re-point alone will not "
        "fund the factor: " + fix)
    assert "the direct edge carried belief" in fix, (
        "a direct edge into a live target did carry belief, whatever the "
        "successor's shape: " + fix)
    assert "EP assembles no factor for this row in the first place" not in fix, (
        "that sentence belongs to a neither-arm row, not a direct edge: " + fix)
    # ⛔ ...and the sentence must answer the question THIS arm asks (the repair
    # leaves the belief where it was), not promise a move the arm measured as
    # absent: re-pointing onto the shaped successor leaves `confidences {}`.
    assert "that belief is NOT moved" in fix, (
        "the repair this arm names does not move the belief: " + fix)
    assert "so the re-point moves that belief onto the successor" not in fix, fix


def test_a_shaped_and_neutral_endpoint_gets_the_shape_clause_not_the_neutral_one(sdk):
    """A shaped endpoint is in NEITHER EP arm, and the DIRECTION is irrelevant there.

    ``_archive_clause`` used to test the neutral branch first, so this row — a shaped
    endpoint whose edge is ``'-'`` — was told the silence comes from the direction and
    lost the ":Point operator" guidance, while its own ``detail`` says EP never
    consults the direction for that endpoint. The clause order is the one
    ``_active_edge_detail`` and ``_triage`` use (SHAPE first), so the row cannot get
    an explanation from a mechanism EP does not reach.
    """
    actor = sdk.create_entity("subject", "AcmeCorp")["node"]
    v1 = _obj(sdk, "PolicyV1")
    sdk.create_direct_edge("IMPL", actor["id"], v1["id"], direction="-")
    sdk.update_entity(actor["id"], is_operator=True)
    # Shaped AND neutral, successorless → the archive clause is emitted.
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'PolicyGone'", {"i": v1["id"]})

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    detail, fix = issues[0].detail, issues[0].fix
    assert "in EITHER arm" in detail, detail
    assert "in EITHER arm" in fix, (
        "the shape clause is the one that explains this row: " + fix)
    assert "clearing the shape alone restores nothing" in fix, fix
    assert "EP never reaches the direction test for this endpoint" in fix, fix
    assert "no belief — and re-pointing or deleting it restores none" not in fix, (
        "the neutral clause attributes the silence to a direction EP never reads "
        "here: " + fix)


def test_an_op_type_only_successor_is_refused_by_the_direct_arm(sdk):
    """EP's DIRECT arm uses the stricter ``_non_op``: flag false/NULL AND no op_type.

    An ``op_type``-only Object IS admitted by the operator-input arm (``_non_operator``)
    and REFUSED by the direct-edge arm, so keying "operator-shaped" on the raw flag
    told a flowing DIRECT row to re-point onto such a successor. Measured: confidences
    went 2 → 0 and a re-audit read CLEAN — the instrument prescribing the lost-belief
    shape it exists to report.
    """
    o = _obj(sdk, "PolicyV1")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    sdk.create_direct_edge("IMPL", p["id"], o["id"])
    before = sdk.compute_confidence(anchors=[p["id"]], require_calibration=False)
    assert before.get("confidences"), (
        "fixture drifted: a flowing direct edge builds no belief: "
        + repr(before.get("confidences")))
    # Only ``op_type`` — the flag stays NULL, so the raw-flag test called it a claim.
    _obj(sdk, "PolicyV2", op_type="IMPL")
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == o["id"]]
    assert issues
    fix = issues[0].fix
    assert "OPERATOR-SHAPED" in fix, (
        "an op_type-only successor is operator-shaped for EP's direct arm: " + fix)
    assert "target a CLAIM successor" in fix, fix
    # The quoted test must be the SHAPE, not the stored value: this node's
    # ``is_operator`` is None/false and only ``op_type`` makes it shaped, so a
    # reader checking the flag would find the warning "wrong".
    assert "or an ``op_type`` is set" in fix, (
        "the warning must name the fact that makes this node shaped: " + fix)


    # ...and the engine agrees the prescribed re-point loses the belief.
    sdk.create_direct_edge("IMPL", p["id"], _q(
        sdk, "MATCH (o:Object {name:'PolicyV2'}) RETURN o.id", {})[0][0])
    _q(sdk, "MATCH (:Point {id:$p})-[r:IMPL]->(:Object {id:$o}) DELETE r",
       {"p": p["id"], "o": o["id"]})
    after = sdk.compute_confidence(anchors=[p["id"]], require_calibration=False)
    assert after.get("confidences") == {}, (
        "EP apparently does read this successor through the direct arm — then the "
        f"warning is over-cautious: {after.get('confidences')}")


def test_a_supersession_chain_points_the_repair_at_the_live_head(sdk):
    """Re-pointing onto an immediate successor that is ITSELF superseded clears the
    finding and leaves the input anchored to another EP-dead node.

    The Object lane writes no chain edge (the fold moves nothing), so this is
    reachable with two ordinary supersessions: V1→V2→V3.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    _obj(sdk, "PolicyV3")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _supersede_object(sdk, "PolicyV2", "PolicyV3")
    assert _q(sdk, "MATCH (o:Object) WHERE o.status = 'superseded' "
                   "RETURN o.name, o.supersededBy ORDER BY o.name",
              {}) == [["PolicyV1", "PolicyV2"], ["PolicyV2", "PolicyV3"]]

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "the successor it names is ITSELF 'superseded'" in issues[0].detail, \
        issues[0].detail
    # The fix must name the reachable live link, not just say "the head" — V3 is the
    # hop the row proves is live, so V2 is NOT the write target.
    assert "is ITSELF 'superseded'" in issues[0].fix, issues[0].fix
    assert "the next link ('PolicyV3'), which is LIVE — target THAT" in issues[0].fix, \
        issues[0].fix
    # ⛔ The DETAIL and the FIX describe the same row: a detail that says "target the
    # LIVE head" over a fix that says "there is no chain" is one FALSE sentence, and
    # the reader cannot tell which (measured — both were emitted for one finding).
    assert "target the LIVE head of that chain" not in issues[0].detail, \
        issues[0].detail
    assert "which is LIVE" in issues[0].detail, issues[0].detail


def test_a_terminal_successor_with_no_chain_is_not_told_to_follow_one(sdk):
    """A successor can be terminal WITHOUT a supersession — then there is no chain.

    `update_entity(object, status='archived')` is a legal Object write and leaves
    `supersededBy` absent, so "target the LIVE head of that chain" would name a node
    that does not exist (the #7819 rule this module refuses to emit).
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    arc = _obj(sdk, "ArcA2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    sdk.update_entity(v1["id"], status="superseded", **{"supersededBy": "ArcA2"})
    sdk.update_entity(arc["id"], status="archived")
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.status, o.supersededBy",
              {"i": arc["id"]}) == [["archived", None]]

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    fix = issues[0].fix
    assert "names NO successor, so there is no chain to follow" in fix, fix
    assert "target the LIVE head of that chain" not in fix, fix
    # ...and the DETAIL must not contradict it (it did: it announced a chain head
    # while the fix said there is no chain).
    assert "target the LIVE head of that chain" not in issues[0].detail, \
        issues[0].detail
    assert "names NO successor, so there is no chain to follow" in issues[0].detail, \
        issues[0].detail


def test_a_chains_dangling_next_hop_is_not_named_as_the_target(sdk):
    """The hop after the successor can dangle — then there is no resolvable head."""
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) SET o.status = 'superseded', "
            "o.supersededBy = 'Ghost'", {})
    assert _q(sdk, "MATCH (o:Object {name:'Ghost'}) RETURN o.id", {}) == []

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    fix = issues[0].fix
    assert "answers no OTHER " in fix, fix
    assert "no resolvable head" in fix, fix
    assert "target THAT" not in fix, fix


def test_a_rewired_successor_leaves_the_stale_edge_reported_honestly(sdk):
    """Step 1 of the emitted repair performed, step 2 not: the successor carries
    an equivalent input and the stale edge into the superseded Object survives.

    The finding is still real (a live edge into a terminal node must not stay),
    but the belief is NOT lost: the factor runs on the survivors. Measured on the
    engine below — so a detail claiming "the successor inherits no belief"
    unconditionally would be false in a state the repair itself can produce, and
    ``high`` would lose the rationale the check gives for choosing it.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]])
    # Step 1 only: wire the successor, leave the stale edge.
    _q(sdk, "MATCH (op:Point {id:$op}), (succ:Object {id:$new}) "
            "MERGE (op)-[nr:IMPL]->(succ) SET nr.idx = 2",
       {"op": op["id"], "new": v2["id"]})
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    assert _q(sdk, "MATCH (o:Object {id:$i})<-[r:IMPL]-(x) RETURN x.id",
              {"i": v2["id"]}), "fixture drifted: the successor is not wired"

    # The engine fact the wording rests on: three inputs, one stripped, the
    # factor STILL RUNS (2 participating), so the successor inherits the belief.
    # ⛔ ``skipped_factors`` alone cannot carry that claim — the key is absent both
    # when the run consumed the factor AND when it never evaluated it, so the
    # confidences are asserted beside it.
    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert not run.get("skipped_factors"), (
        "the factor was skipped anyway — the rewired shape would then be lost "
        f"belief after all: {run.get('skipped_factors')}")
    assert run.get("confidences"), (
        "the factor was never consumed, so 'it still runs' is unproven: "
        f"{run}")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues, "the stale edge must still be reported — it must not stay"
    issue = issues[0]
    assert "unless it was separately rewired into a slot EP reads" in issue.detail, \
        issue.detail
    assert "only that delete" in issue.fix, issue.fix


# ── 2. the name-keyed successor pointer ───────────────────────────


def test_a_renamed_successor_dangles_the_pointer_and_is_reported(sdk):
    """#11826's second measurement: ``supersededBy`` is a NAME, not a pointer."""
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    assert _issues(sdk, "superseded_by_unresolvable") == [], \
        "the pointer resolves before the rename — the control for this test"

    # #3590 made the Object name MUTABLE and the id the identity.
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.name = 'PolicyV2b'",
       {"i": v2["id"]})

    issues = _issues(sdk, "superseded_by_unresolvable")
    assert [i.node_id for i in issues] == [v1["id"]], (
        f"a dangling successor pointer must be reported for {v1['id']}: {issues}")
    assert "PolicyV2" in issues[0].detail
    assert "NO Object carries that name" in issues[0].detail
    # The repair must be CALLABLE: ``tortoise_update_entity`` takes its properties as
    # ONE dict (``mcp_server.py``), so a keyword spelling raises TypeError at the
    # caller — a repair that always raises is worse than no repair (#7819's rule).
    assert f"tortoise_update_entity('{v1['id']}', {{" in issues[0].fix, \
        issues[0].fix
    assert "''" not in issues[0].fix, "a doubled apostrophe is not a usable literal"
    # ⛔ A single stray quote is the same defect and a prefix match cannot see it:
    # parse the CALL the string hands the caller (see
    # ``test_the_unresolvable_repair_is_a_parsable_call``).
    ast.parse(_call(issues[0].fix))


def test_an_ambiguous_name_is_not_reported_as_a_dead_one(sdk):
    """N>=2 carriers is a DIFFERENT fact (#3573), not an unresolvable one.

    The two need different repairs (disambiguation vs. re-pointing), so folding
    them would misdirect the fix — the check stays silent whenever the name still
    addresses something.  ⛔ ``create_entity`` UPSERTS on the name, so calling it
    twice returns ONE node: the second carrier has to be minted raw, and the count
    is asserted first, or this test passes on a one-carrier graph and a rewrite to
    ``count(carrier) < 2`` would keep it green (#12111 review).
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    # A genuine SECOND node carrying the successor's name (name-upsert bypassed).
    _q(sdk, "CREATE (:Object {id:'twin-PolicyV2', name:'PolicyV2', "
            "objectKind:'other', status:'live'})", {})
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) RETURN count(o)", {}) == [[2]], \
        "fixture drifted: the twin was upserted into one node, so there is no ambiguity"
    assert v2["id"] != "twin-PolicyV2"

    assert _issues(sdk, "superseded_by_unresolvable") == [], (
        "an ambiguous pointer was reported as a dead one — same-name "
        "coexistence is #3573's fact, not #11826's")
    # And 4b must not describe "the successor" by picking one carrier's status.
    edge_issues = [i for i in _issues(sdk, "superseded_object_active_edges")
                   if i.node_id == v1["id"]]
    assert edge_issues
    assert "is AMBIGUOUS (2 Objects carry it, #3573)" in edge_issues[0].detail, \
        edge_issues[0].detail
    assert "FIRST name the successor meant" in edge_issues[0].fix, edge_issues[0].fix


def test_an_op_type_only_operator_is_not_mistaken_for_a_direct_edge(sdk):
    """EP's operator predicate is the flag OR ``op_type`` — not the flag alone.

    ``create_point(..., op_type='IMPL')`` stores ``is_operator=False,
    op_type='IMPL'`` (the hybrid/legacy shape both ``graph-scripts/
    backfill_is_operator.py`` and ``sdk.py`` name), and EP builds the factor from it
    all the same. Reading the bare flag sent that row down the DIRECT-edge arm —
    "EP builds NO factor … no skipped-factor record" (false: the record exists) and
    a repair with no ``idx`` for an edge that is an operator input.
    """
    op = sdk.create_point("statement", "the hybrid operator", op_type="IMPL")
    o1 = _obj(sdk, "PolicyV1")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    _obj(sdk, "PolicyV2")
    # idx 0 = the source slot, which the stale edge occupies; idx 1 = a live input.
    assert sdk.create_direct_edge("IMPL", op["id"], o1["id"])
    assert sdk.create_direct_edge("IMPL", op["id"], p["id"])
    for eid, i in ((o1["id"], 0), (p["id"], 1)):
        _q(sdk, "MATCH (a {id:$a})-[r:IMPL]->(b {id:$b}) SET r.idx = $i",
           {"a": op["id"], "b": eid, "i": i})
    _q(sdk, "MATCH (x:Point {id:$i}) SET x.status = 'live', x.direction = '->'",
       {"i": op["id"]})
    sdk.update_entity(o1["id"], status="superseded", **{"supersededBy": "PolicyV2"})
    assert _q(sdk, "MATCH (p:Point {id:$i}) RETURN p.is_operator, p.op_type",
              {"i": op["id"]}) == [[False, "IMPL"]], \
        "fixture drifted: this is no longer the op_type-only shape"

    # The engine fact: EP treats it as an operator and records the strip.
    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert [s["reason"] for s in run.get("skipped_factors") or []] == ["source_stripped"], \
        f"EP did not build a factor for the hybrid operator: {run.get('skipped_factors')}"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == o1["id"]]
    assert issues
    issue = issues[0]
    assert "EP builds NO factor for a direct edge" not in issue.detail, issue.detail
    assert "source_stripped" in issue.detail, issue.detail
    assert "it is a DIRECT edge" not in issue.fix, issue.fix
    assert "into the SOURCE slot" in issue.fix, issue.fix


def test_an_object_with_no_successor_name_is_not_reported(sdk):
    """The check is about a POINTER, so an absent one is out of its scope."""
    v1 = _obj(sdk, "Orphan")
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = ''", {"i": v1["id"]})
    assert _issues(sdk, "superseded_by_unresolvable") == []


def test_a_self_pointer_is_not_a_successor(sdk):
    """``supersededBy`` naming the Object ITSELF resolves to no OTHER node.

    A plain name match is satisfied by the node that carries the name by
    definition, so without ``carrier.id <> sup.id`` a self-pointer read as
    RESOLVED and nothing in the audit mentioned it (measured: ``update_entity(obj,
    supersededBy='<its own name>')`` is accepted). The repairable fact is the same
    one a dangling name is — name the real successor.
    """
    v1 = _obj(sdk, "PolicyV1")
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded'", {"i": v1["id"]})
    sdk.update_entity(v1["id"], **{"supersededBy": "PolicyV1"})
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.supersededBy",
              {"i": v1["id"]}) == [["PolicyV1"]], \
        "fixture drifted: the self-pointer was refused by the door"

    issues = _issues(sdk, "superseded_by_unresolvable")
    assert [i.node_id for i in issues] == [v1["id"]], (
        "a pointer that names only the node itself was read as resolved: "
        f"{[(i.node_id, i.detail) for i in issues]}")
    # ⛔ The sentence must not claim NO Object carries the name — this one does.
    assert "names ITSELF as its successor" in issues[0].detail, issues[0].detail
    assert "NO Object carries that name" not in issues[0].detail, issues[0].detail
    # 4b reads the same pointer, so its repair arm must say the same thing.
    p = sdk.create_point("statement", "the gateway enforces the policy")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    edge_issues = [i for i in _issues(sdk, "superseded_object_active_edges")
                   if i.node_id == v1["id"]]
    assert edge_issues
    assert "names the Object ITSELF" in edge_issues[0].detail, edge_issues[0].detail
    assert "No :Object answers the successor name" in edge_issues[0].fix, \
        edge_issues[0].fix

    # The counterpart the exclusion must NOT break: a successor that is a
    # DIFFERENT node sharing the name still resolves. Keep this node superseded and
    # self-pointed — with the status cleared there are no superseded Objects at all,
    # so the assertion could not fail (#12111 review).
    _obj(sdk, "PolicyV1")  # a second, distinct Object with the same name
    assert [i for i in _issues(sdk, "superseded_by_unresolvable")
            if i.node_id == v1["id"]] == [], \
        "a twin carrying the name resolves the pointer — the exclusion went too far"


def test_a_draft_successor_is_not_a_working_repair(sdk):
    """A DRAFT successor is not terminal, but EP strips it exactly as it strips one.

    ``is_terminal_status('draft')`` is False, so a draft successor takes the
    ordinary "re-point onto the successor" arm — and the reader who performs it
    gets a re-audit that reads CLEAN over a factor that is STILL degenerate
    (``include_draft=False``, #780). That is the same self-inflicted false
    NEGATIVE the successorless arm refuses for ``archived``, and it is reachable
    through the SANCTIONED fold lane: ``apply_supersessions`` folds a draft
    successor with no warning, and a draft is the natural state of a fresh node.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2", status="draft")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) RETURN o.status", {}) == \
        [["draft"]], "fixture drifted: the successor is not a draft"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "which EP strips like a terminal input" in issues[0].detail, \
        issues[0].detail
    assert "promote it to live as well" in issues[0].fix, issues[0].fix

    # And the clause is load-bearing: perform the repair the fix prescribes and
    # the factor is still degenerate, so "re-point and re-audit clean" is false.
    _q(sdk, "MATCH (o:Object {id:$i})-[r:IMPL]->() "
            "MATCH (s:Object {name:$s}) MERGE (o)-[nr:IMPL]->(s) "
            "SET nr.idx = 0 DELETE r",
       {"i": op["id"], "s": "PolicyV2"})
    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert [s["reason"] for s in run.get("skipped_factors") or []] == ["degenerate"], \
        ("the draft input is stripped, so the prescribed re-point alone does not "
         f"restore the belief: {run.get('skipped_factors')}")


def test_a_chain_whose_tail_is_terminal_names_no_live_head(sdk):
    """A terminal next link is not a head either — and the row proves no more.

    Measured shape: V1->V2->V3, V3 retracted with no successor, so no live Object
    exists anywhere. Both strings said "target the LIVE head of that chain" /
    "walk that chain to its LIVE head" — a node that is not in the graph (the
    #7819 rule). The DETAIL and the FIX for one row must also agree: they are the
    same fact, and one of them contradicting the other leaves the reader unable to
    tell which is false.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    v3 = _obj(sdk, "PolicyV3")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _supersede_object(sdk, "PolicyV2", "PolicyV3")
    sdk.update_entity(v3["id"], status="retracted")
    assert _q(sdk, "MATCH (o:Object) WHERE o.status = 'live' RETURN count(o)", {}) \
        == [[0]], "fixture drifted: a live Object exists to target"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    detail, fix = issues[0].detail, issues[0].fix
    for text in (detail, fix):
        assert "target the LIVE head" not in text and \
            "LIVE head of that chain" not in text, \
            "no live head exists, so naming one sends the write nowhere: " + text
        assert "is not a head either" in text, text
        assert "'retracted'" in text, text
    # ...and the escape is named, so the reader is not left without a repair.
    assert "re-point onto whatever now carries the claim or delete the edge" in fix, fix


def test_a_chains_ambiguous_next_hop_is_not_named_as_the_target(sdk):
    """The hop after the successor is subject to the SAME ambiguity rule.

    ``head(collect(nxt.status))`` picks an arbitrary carrier, so an ambiguous next
    name could be announced as "LIVE, so target THAT" — handing the reader a name
    this check refuses to resolve one hop earlier, and a re-point onto a terminal
    twin. The order the carriers were created in must not change the sentence.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _obj(sdk, "AH2")  # live carrier, created first
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'AH2'", {"i": v2["id"]})
    _q(sdk, "CREATE (:Object {id:'dead-ah', name:'AH2', status:'superseded'})", {})
    assert _q(sdk, "MATCH (o:Object {name:'AH2'}) RETURN count(o)", {}) == [[2]], \
        "fixture drifted: the next hop is not ambiguous"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    fix = issues[0].fix
    assert "names 2 Objects" in fix, fix
    assert "name the LIVE head by ID" in fix, fix
    assert "target THAT" not in fix, (
        "an ambiguous next hop must not be named as the target: " + fix)


def test_a_chains_self_loop_is_not_walked(sdk):
    """A successor whose own ``supersededBy`` is its own name has no live head.

    The second hop did not exclude the CARRIER, so it found ITSELF and the string
    told the reader to walk a chain whose only named node is the successor.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    sdk.update_entity(v2["id"], status="superseded", **{"supersededBy": "PolicyV2"})
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.supersededBy",
              {"i": v2["id"]}) == [["PolicyV2"]], "fixture drifted"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    fix = issues[0].fix
    assert "names ITSELF as its own successor" in fix, fix
    assert "no live head" in fix, fix
    assert "target THAT" not in fix, fix


def test_the_unresolvable_repair_is_a_parsable_call(sdk):
    """The emitted repair must be callable Python, not just the right prefix.

    ``tortoise_update_entity(<id>, {...})`` is the door's shape; a stray quote makes
    the whole call a SyntaxError, and a prefix assertion cannot see one. #7819's rule
    — a repair that always raises is worse than no repair — is about the CALL, so
    the test parses it rather than matching its start.
    """
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) SET o.name = 'PolicyV2b'", {})

    issues = [i for i in _issues(sdk, "superseded_by_unresolvable")
              if i.node_id == v1["id"]]
    assert issues
    call = _call(issues[0].fix)
    ast.parse(call)  # raises SyntaxError on a stray quote
    assert call.endswith("})"), call


def test_an_idless_object_is_not_handed_an_id_keyed_call(sdk):
    """``tortoise_update_entity`` keys on the ID, so a NAME there cannot work.

    Only a raw ``REMOVE o.id`` reaches this state (no SDK writer mints an id-less
    Object), but the string must not hand out a call that cannot work, and 4c
    already answers the same state in prose — the two checks must not disagree.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'Gone' REMOVE o.id", {"i": v1["id"]})
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV1'}) RETURN o.id", {}) == [[None]], \
        "fixture drifted: the id survived"

    issues = _issues(sdk, "superseded_object_active_edges")
    assert issues
    fix = issues[0].fix
    assert "tortoise_update_entity" in fix and "cannot address it" in fix, fix
    assert "tortoise_update_entity('PolicyV1'" not in fix, (
        "a name was embedded where the door expects an id: " + fix)


def test_a_neutral_operator_does_not_claim_lost_belief(sdk):
    """``direction='-'`` transfers NO confidence BY DESIGN — so nothing is lost.

    ``create_operator(..., semantics='related')`` is a documented path into the
    NEUTRAL direction (``pack_registry.SEMANTICS_DIRECTIONS``), and
    ``_update_nary_factor`` returns at ``if not (fwd or bwd)`` before any pair
    math. The belief-loss arms therefore stated a consequence the engine never
    produces: the detail claimed the successor could inherit the belief if
    "separately rewired" (it cannot — measured, posteriors identical), and the
    idx-0 arm told the reader that deleting the edge renumbers a surviving input
    and inverts the direction (nothing is renumbered).
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]], semantics="related")
    assert _q(sdk, "MATCH (o {id:$i}) RETURN o.direction", {"i": op["id"]}) == \
        [["-"]], "fixture drifted: the operator is not neutral"
    before = sdk.compute_confidence(factors=[op["id"]],
                                    require_calibration=False)["confidences"]

    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "transfers NO confidence by design" in issues[0].detail, issues[0].detail
    assert "The successor inherits no belief" not in issues[0].detail, (
        "a neutral operator transfers nothing, so the successor cannot inherit "
        "it however it is wired: " + issues[0].detail)
    assert "Nothing was LOST by leaving it" in issues[0].fix, issues[0].fix
    assert "direction inverts" not in issues[0].fix, (
        "nothing is renumbered when neither leg flows: " + issues[0].fix)

    # ...and the engine agrees: the superseded input is STRIPPED (its row leaves
    # the result) and every SURVIVING posterior is unchanged — no belief moved.
    after = sdk.compute_confidence(factors=[op["id"]],
                                   require_calibration=False)["confidences"]
    assert v1["id"] not in after, (
        f"the terminal input was not stripped at all: {after}")
    for node_id, value in after.items():
        assert value == before[node_id], (
            f"a neutral operator moved the posterior of {node_id}: "
            f"{before[node_id]} -> {value}")


def test_a_draft_next_hop_is_not_announced_as_the_live_head(sdk):
    """``draft`` is NOT terminal, so hop 2's live arm caught it and called it LIVE.

    Measured: V1->V2->V3 with V3 draft, target V3 as the fix says, and the factor
    produces NO belief (confidences empty) while a re-audit reads CLEAN. Reachable
    through the sanctioned lane — ``apply_supersessions`` admits a draft successor.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    _obj(sdk, "PolicyV3", status="draft")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _supersede_object(sdk, "PolicyV2", "PolicyV3")
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV3'}) RETURN o.status", {}) == \
        [["draft"]], "fixture drifted"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    for text in (issues[0].detail, issues[0].fix):
        assert "is LIVE" not in text, (
            "a draft input is stripped, so it is not a head: " + text)
        assert "EP strips a draft input like a terminal one" in text, text

    # The clause is load-bearing: target the draft as the LIVE arm said, and the
    # factor produces nothing while the audit reads CLEAN.
    _q(sdk, "MATCH (o {id:$i})-[r:IMPL]->() MATCH (s:Object {name:$s}) "
            "MERGE (o)-[nr:IMPL]->(s) SET nr.idx = r.idx DELETE r",
       {"i": op["id"], "s": "PolicyV3"})
    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert run["confidences"] == {}, (
        "targeting the draft tail left a belief, so this arm is over-strict: "
        f"{run['confidences']}")
    assert [i for i in _issues(sdk, "superseded_object_active_edges")] == [], (
        "the prescribed repair must leave the check reading CLEAN — that is why the "
        "string has to refuse it")


def test_an_operator_shaped_next_hop_is_not_announced_as_the_live_head(sdk):
    """Same one-hop-short ladder: an operator-shaped hop 2 is excluded, not LIVE."""
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    _obj(sdk, "PolicyV3", is_operator=True)
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _supersede_object(sdk, "PolicyV2", "PolicyV3")
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV3'}) RETURN o.is_operator", {}) == \
        [[True]], "fixture drifted"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    for text in (issues[0].detail, issues[0].fix):
        assert "is LIVE" not in text, (
            "an operator-shaped node is not a factor input: " + text)
        assert "OPERATOR-SHAPED" in text, text
        assert "target a CLAIM successor" in text, text


def test_an_ep_dead_source_does_not_claim_belief_was_active(sdk):
    """EP's source liveness is ``_live_only``, which also requires ``outdated`` false.

    ``invalidate_point`` is a door into that state, and the row described it as an
    "active" edge — a finding over a factor EP never builds.
    """
    src = sdk.create_point("statement", "the gateway is audited")
    v1 = _obj(sdk, "PolicyV1")
    sdk.create_direct_edge("IMPL", src["id"], v1["id"])
    corrected = sdk.create_point("statement", "the gateway was never audited")
    sdk.invalidate_point(src["id"], corrected["id"])
    assert _q(sdk, "MATCH (p {id:$i}) RETURN p.status, p.outdated",
              {"i": src["id"]}) == [["live", True]], \
        "fixture drifted: invalidate_point flags outdated and leaves status alone"
    from tortoise.live import is_terminal_status
    assert is_terminal_status("live", True), \
        "EP's source liveness is _live_only, which is terminal for a flagged node"
    sdk.update_entity(v1["id"], status="superseded", **{"supersededBy": "Gone"})

    assert [i for i in _issues(sdk, "superseded_object_active_edges")
            if i.node_id == v1["id"]] == [], (
        "the source is EP-dead, so no belief was lost from this edge")


def test_the_remaining_input_threshold_is_eps_one_not_two(sdk):
    """EP skips the factor when FEWER THAN TWO inputs REMAIN — i.e. one left.

    ``ep.py``: ``elif len(input_ids) < 2 <= len(full_inputs): degenerate``. The
    detail said "two left is skipped as degenerate, more runs without it", which
    reports the ordinary 3-input operator (1 stripped, 2 remaining) as skipped when
    EP re-scores it on the survivors, and contradicts the sentence before it ("the
    factor over its REMAINING inputs is then re-scored without it").
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    p2 = sdk.create_point("statement", "the gateway is audited")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"], p2["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert run.get("skipped_factors") in (None, []), (
        "fixture drifted: two inputs still participate, so the factor RUNS — that "
        f"is the fact the string must not contradict: {run.get('skipped_factors')}")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    detail = issues[0].detail
    assert "ONE left is skipped as degenerate" in detail, detail
    assert "two or more run without it" in detail, detail
    assert "two left is skipped" not in detail, (
        "two remaining inputs RUN — saying they are skipped sends the reader to a "
        "degenerate record that does not exist: " + detail)


def test_a_shaped_non_point_endpoint_is_in_neither_arm(sdk):
    """EP's two arms exclude it twice over, so the repair must not be an edge rewrite.

    The operator arm needs the ``:Point`` label; the direct-edge arm refuses an
    operator-shaped endpoint on either side. So EP builds NO factor — before the
    supersession as well — and "write the equivalent edge from the same source onto
    the successor" (the direct-edge sentence) changes nothing: measured, confidences
    stay empty and a re-audit reads CLEAN.
    """
    actor = sdk.create_entity("subject", "AcmeCorp")["node"]
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_direct_edge("IMPL", actor["id"], v1["id"])
    sdk.update_entity(actor["id"], is_operator=True)
    assert sdk.compute_confidence(factors=[v1["id"]],
                                 require_calibration=False)["confidences"] == {}
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    detail, fix = issues[0].detail, issues[0].fix
    assert "in EITHER arm" in detail, detail
    assert "EP builds NO factor" in fix, fix
    assert "write the equivalent edge" not in fix, (
        "an edge rewrite cannot create a factor EP refuses to build: " + fix)
    assert "tortoise_update_entity(" in fix and "'is_operator': False" in fix, fix


def test_a_shaped_endpoint_with_no_successor_gets_no_edge_rewrite(sdk):
    """The successorless arm picked the DIRECT sentence for a shaped endpoint too.

    That put two contradicting sentences in ONE finding: the detail said EP builds
    NO factor in either arm, the fix said "this is a DIRECT edge … write the
    equivalent edge onto that successor". The prescribed repair restores nothing and
    the re-audit reads CLEAN over a claim whose support was never there.
    """
    actor = sdk.create_entity("subject", "AcmeCorp")["node"]
    v1 = _obj(sdk, "PolicyV1")
    sdk.create_direct_edge("IMPL", actor["id"], v1["id"])
    sdk.update_entity(actor["id"], is_operator=True)
    sdk.update_entity(v1["id"], status="superseded")  # NO successor at all
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.supersededBy",
              {"i": v1["id"]}) == [[None]], "fixture drifted"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    detail, fix = issues[0].detail, issues[0].fix
    assert "in EITHER arm" in detail, detail
    assert "write the equivalent edge" not in fix, (
        "the detail says no factor exists in either arm, so an edge rewrite cannot "
        "help: " + fix)
    assert "no edge rewrite repairs this" in fix, fix
    # ⛔ The shape belongs to the ACTIVE endpoint, never to the superseded anchor: a
    # call on the anchor sets the flag on a node that never carried it, so APPLY the
    # emitted call and re-measure rather than string-matching it (#12111 review).
    assert (f"tortoise_update_entity('{actor['id']}', "
            "{'is_operator': False, 'op_type': None})") in fix, (
        "the repair must address the shaped endpoint: " + fix)
    assert f"tortoise_update_entity('{v1['id']}', {{'is_operator': False}})" not in fix
    # ...and the call must clear BOTH halves of the shape: the flag alone is a
    # no-op where ``op_type`` carries it (measured: the row came back verbatim).
    sdk.update_entity(actor["id"], is_operator=False, op_type=None)
    assert _q(sdk, "MATCH (a {id:$i}) RETURN a.is_operator, a.op_type",
              {"i": actor["id"]}) == [[False, None]], \
        "the emitted call did not clear BOTH halves of the shape"
    # Clearing the shape does not clear the FINDING: the edge still ends at the
    # superseded Object, so the row comes back — as a DIRECT row, not a neither-arm
    # one (that is the half of the repair the string says is missing).
    after = [i for i in _issues(sdk, "superseded_object_active_edges")
             if i.node_id == v1["id"]]
    assert after, "the stale edge is still there, so the finding must persist"
    assert "in EITHER arm" not in after[0].fix, (
        "the shape is cleared, so this row is a DIRECT row now: " + after[0].fix)
    # The archive sentence must not claim a strip that never happens either.
    assert "EP strips a terminal input either way" not in fix, fix
    # ⛔ §3.1's disposition on a neither-arm row is DROP: this fix says no edge
    # rewrite repairs it, so offering a CARRY in the appended triage would
    # contradict the repair it rides on (#12111 review).
    assert "may CARRY to the successor" not in fix, fix
    assert "the disposition here is DROP" in fix, fix


def test_a_flag_dead_successor_names_the_flag_not_none(sdk):
    """``outdated=true`` with no status is terminal and renders no status at all.

    ``_quoted(None)`` emitted the literal ``'None'`` — a status no node carries —
    and the one fact the repair must act on (the flag) was never stated.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) SET o.outdated = true", {})
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) RETURN o.status, o.outdated",
              {}) == [[None, True]], "fixture drifted"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    for text in (issues[0].detail, issues[0].fix):
        assert "'None'" not in text and "None" not in text, text
        assert "live(outdated)" in text, text


def test_a_non_point_operator_is_not_an_operator_input(sdk):
    """EP's operator arm requires the LABEL, so a flagged non-:Point builds nothing.

    ``ep.py`` selects inputs through ``(o:Point)-[:IMPL|NAND]->(c)``, and the
    direct-edge arm demands a non-operator on both endpoints — so a non-:Point node
    carrying ``is_operator=true`` is in NEITHER arm and no factor exists. Reading the
    flag alone took the operator arm, which told the reader to stamp an ``idx`` no
    factor would ever read. Door-reachable: ``create_direct_edge`` then
    ``update_entity(..., is_operator=True)``.
    """
    actor = sdk.create_entity("subject", "AcmeCorp")["node"]
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_direct_edge("IMPL", actor["id"], v1["id"])
    sdk.update_entity(actor["id"], is_operator=True)
    assert _q(sdk, "MATCH (a {id:$i}) RETURN labels(a), a.is_operator",
              {"i": actor["id"]}) == [[["Subject"], True]], \
        "fixture drifted: the subject is not operator-shaped"
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    assert sdk.compute_confidence(factors=[actor["id"]],
                                 require_calibration=False)["confidences"] == {}, \
        "fixture drifted: EP built a factor for a non-:Point operator"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "EP builds NO factor" in issues[0].detail, issues[0].detail
    assert "stamp one — idx 0 is the source slot" not in issues[0].fix, (
        "no factor will read an idx here, and there is none to stamp: "
        + issues[0].fix)


def test_a_legacy_outdated_successor_is_terminal_too(sdk):
    """EP's terminal test is the status vocab OR the legacy ``outdated`` flag.

    ``update_entity(outdated=...)`` is refused on an Object (server-managed), so
    only a raw/legacy write reaches the state — but ``is_terminal_status(status,
    outdated)`` is True for it, so the ordinary "re-point and it works" arm left the
    factor degenerate while a re-audit read CLEAN.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) SET o.outdated = true", {})
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) RETURN o.status, o.outdated",
              {}) == [[None, True]], "fixture drifted"
    from tortoise.live import is_terminal_status
    assert is_terminal_status(None, True), "the shared predicate disagrees"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "is ITSELF" in issues[0].detail, (
        "an EP-dead successor took the ordinary arm: " + issues[0].detail)
    assert "names NO successor, so there is no chain to follow" in issues[0].fix, \
        issues[0].fix


def test_an_idless_and_unnamed_object_says_unnamed(sdk):
    """The id-less arm is about a node missing its key — it must not invent a name."""
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'Gone' REMOVE o.id, o.name", {"i": v1["id"]})
    assert _q(sdk, "MATCH (o:Object) WHERE o.status = 'superseded' "
                   "RETURN o.id, o.name", {}) == [[None, None]], "fixture drifted"

    issues = _issues(sdk, "superseded_object_active_edges")
    assert issues
    assert "'None'" not in issues[0].fix, issues[0].fix
    assert "<unnamed>" in issues[0].fix, issues[0].fix


def test_a_subject_only_next_hop_is_still_a_head(sdk):
    """The next hop is probed as ``:Object`` only — a Subject there is a real node.

    V1->V2->(Subject PolicyV3): the dangling arm concluded "the chain has no
    resolvable head" and offered only "delete", for a state where a node EP accepts
    as an input does carry that name.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_entity("subject", "PolicyV3")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) SET o.status = 'superseded', "
            "o.supersededBy = 'PolicyV3'", {})
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV3'}) RETURN count(o)", {}) == [[0]]

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    fix = issues[0].fix
    assert "if a Subject or Point carries that name" in fix, fix
    assert "so the chain has no resolvable head" not in fix, (
        "a Subject carries the name, so a head may well be resolvable: " + fix)


def test_an_operator_shaped_successor_is_not_a_working_repair(sdk):
    """EP does not admit an ``is_operator=true`` node as a factor input (#9269).

    ``create_entity("object", n, is_operator=True)`` is door-permitted, and
    ``_non_operator`` strips such an input exactly as it strips a draft — so the
    ordinary re-point arm prescribed a repair that leaves the factor degenerate
    while a re-audit reads CLEAN. ⛔ The arm keys on the RAW FLAG: an
    ``op_type``-only Object IS admitted (measured, ``skipped: []``), so reusing
    ``_4b_state``'s flag-OR-``op_type`` predicate here would be wrong.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyA")
    _obj(sdk, "PolicyB", is_operator=True)
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyA", "PolicyB")
    assert _q(sdk, "MATCH (o:Object {name:'PolicyB'}) RETURN o.status, o.is_operator",
              {}) == [[None, True]], "fixture drifted: not operator-shaped"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "OPERATOR-SHAPED" in issues[0].detail, issues[0].detail
    assert "excludes from a factor's inputs" in issues[0].detail, issues[0].detail
    assert "OPERATOR-SHAPED" in issues[0].fix, issues[0].fix
    # ⛔ The QUOTED test follows the LANE, like the predicate that fires the arm: a
    # parenthetical naming ``op_type`` here would state a disqualifier that is not in
    # force on an operator row and steer the reader off a successor EP reads.
    assert "or an ``op_type`` is set" not in issues[0].detail, issues[0].detail
    assert "or an ``op_type`` is set" not in issues[0].fix, issues[0].fix

    # The clause is load-bearing, and the measured consequence is STRONGER than
    # "still degenerate": EP excludes the operator-shaped node from the input
    # selection, so the operator is left with no factor at all — no confidences, no
    # skipped-factor record — and 4b then reads CLEAN.
    _q(sdk, "MATCH (o {id:$i})-[r:IMPL]->() MATCH (s:Object {name:$s}) "
            "MERGE (o)-[nr:IMPL]->(s) SET nr.idx = 0 DELETE r",
       {"i": op["id"], "s": "PolicyB"})
    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert run["confidences"] == {}, (
        "the operator-shaped input was admitted after all, so this arm is wrong: "
        f"{run['confidences']}")
    assert "PolicyB" not in str(run.get("skipped_factors")), run.get("skipped_factors")
    assert [i for i in _issues(sdk, "superseded_object_active_edges")] == [], (
        "the prescribed repair must leave the check reading CLEAN — that is exactly "
        "why the string has to refuse it")


def test_an_op_type_only_successor_is_read_by_the_operator_arm(sdk):
    """The two EP successor predicates disagree, so the row's LANE picks the test.

    ``_non_operator`` (the OPERATOR-INPUT arm) refuses only the FLAG, so an
    ``op_type``-only successor IS read there — measured on a TWO-input operator:
    ``iterations`` 11 after the prescribed re-point, the successor in
    ``confidences``, ``skipped_factors: None``. Applying the direct arm's stricter
    ``_non_op`` to this lane announced "OPERATOR-SHAPED … target a CLAIM successor"
    about a successor EP reads perfectly, i.e. it told the reader to abandon a
    repair that works. ⛔ Without this test the per-lane split can be reverted
    (``_succ_refused``/``_next_refused`` back to the shape test) with the whole
    file still green — measured.

    ⛔ TWO inputs on purpose: an operator left with ONE is below EP's floor
    (``len(input_ids) < 2``) and returns a hydrated 0.5 STUB, so a one-input fixture
    passes a bare ``assert run["confidences"]`` whether or not the successor was
    read at all.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyA")
    other = _obj(sdk, "Unrelated")
    # Only ``op_type``: the flag stays NULL/false, so the FLAG test admits it.
    succ = _obj(sdk, "PolicyB", op_type="IMPL")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"], other["id"]])
    _supersede_object(sdk, "PolicyA", "PolicyB")
    assert _q(sdk, "MATCH (o:Object {name:'PolicyB'}) "
                   "RETURN o.status, o.is_operator, o.op_type", {}) == \
        [[None, None, "IMPL"]], "fixture drifted: not the op_type-only shape"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues, "fixture drifted: the stale edge must be reported"
    assert "OPERATOR-SHAPED" not in issues[0].detail, (
        "EP's operator arm reads an op_type-only successor, so this arm must not "
        "fire: " + issues[0].detail)
    assert issues[0].fix.endswith("target a CLAIM successor") is False, issues[0].fix

    # The consequence the arm would have claimed is measurably FALSE: the ordinary
    # re-point onto PolicyB leaves the factor running — over BOTH inputs, with the
    # successor among the posteriors (not the degenerate one-input stub).
    _q(sdk, "MATCH (o {id:$i})-[r:IMPL]->(x:Object {name:'PolicyA'}) "
            "MATCH (s:Object {name:'PolicyB'}) "
            "MERGE (o)-[nr:IMPL]->(s) SET nr.idx = r.idx DELETE r",
       {"i": op["id"]})
    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert (run.get("iterations") or 0) > 0, (
        "fixture drifted: the factor did not run, so this proves nothing: " + repr(run))
    assert succ["id"] in (run.get("confidences") or {}), (
        "EP refused the op_type-only successor, so the stricter test was right "
        "after all: " + repr(run.get("confidences")))
    assert not run.get("skipped_factors"), (
        "EP recorded a strip, so the successor was NOT read: "
        + repr(run.get("skipped_factors")))
    assert not [i for i in _issues(sdk, "superseded_object_active_edges")
                if i.node_id == v1["id"]], (
        "the prescribed repair must clear the finding")


def test_an_unnamed_object_is_not_reported_as_the_string_none(sdk):
    """``update_entity(obj, name=None)`` is accepted, so a name can be absent.

    An f-string then interpolated Python's ``None`` into the finding, naming a node
    that exists nowhere in the graph.
    """
    v1 = _obj(sdk, "PolicyA")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    sdk.update_entity(v1["id"], name=None, status="superseded",
                      **{"supersededBy": "Gone"})
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.name", {"i": v1["id"]}) == [[None]]

    edges = [i for i in _issues(sdk, "superseded_object_active_edges")
             if i.node_id == v1["id"]]
    assert edges
    assert "'None'" not in edges[0].detail and "None" not in edges[0].detail, \
        edges[0].detail
    assert "<unnamed>" in edges[0].detail, edges[0].detail
    dangling = [i for i in _issues(sdk, "superseded_by_unresolvable")
                if i.node_id == v1["id"]]
    assert dangling
    assert "None" not in dangling[0].detail, dangling[0].detail
    assert "<unnamed>" in dangling[0].detail, dangling[0].detail


def test_a_subject_carrier_makes_the_pointer_not_dangling(sdk):
    """A ``:Subject`` carrying the name is a node EP accepts as an input.

    Both checks describe the same row in one run, and 4b said "if a Subject or
    Point carries that name..." while 4c enumerated only rename/delete/never-created
    causes and prescribed re-writing the name it already holds — a repair that
    cannot clear its own finding.
    """
    v1 = _obj(sdk, "PolicyA")
    sdk.create_entity("subject", "AcmeCorp")
    sdk.update_entity(v1["id"], status="superseded", **{"supersededBy": "AcmeCorp"})

    dangling = [i for i in _issues(sdk, "superseded_by_unresolvable")
                if i.node_id == v1["id"]]
    assert dangling, "the :Object anchor still cannot resolve it — report it"
    assert "Subject/Point" in dangling[0].detail, dangling[0].detail
    assert "Subject or Point" in dangling[0].fix, dangling[0].fix
    # The call it hands the caller must still be callable.
    ast.parse(_call(dangling[0].fix))


def test_an_idless_subject_still_resolves_its_successor(sdk):
    """The self-exclusion must be null-safe, or a resolvable pointer reads dead.

    ``carrier.id <> sup.id`` is NULL-swallowing: an id-less Object on either side
    makes the comparison NULL and the OPTIONAL MATCH binds NOTHING, so both checks
    stated the OPPOSITE of the graph — 4c "NO Object carries that name" and 4b
    "there is nothing to re-point this input onto" — over a successor that is
    sitting right there. ``id(...)`` is internal identity and always present.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = 'PolicyV2' REMOVE o.id", {"i": v1["id"]})
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV1'}) RETURN o.id", {}) == [[None]]
    assert _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) RETURN count(o)", {}) == [[1]]

    assert [i for i in _issues(sdk, "superseded_by_unresolvable")] == [], (
        "the pointer RESOLVES — reporting it as dead states the opposite of the "
        "graph and prescribes a repair for a defect that is not there")
    edges = _issues(sdk, "superseded_object_active_edges")
    assert edges
    assert "no OTHER Object answers its supersededBy" not in edges[0].detail, \
        edges[0].detail
    assert "there is nothing to re-point this input onto" not in edges[0].fix, \
        edges[0].fix
    # The successor resolves; the item's own missing identity is the real limit.
    assert "No id-keyed repair is possible" in edges[0].fix, edges[0].fix


def test_an_operators_arity_is_not_asserted(sdk):
    """A one-input operator has NO factor at all — the ladder must say so.

    ``create_operator("IMPL", <obj>, [])`` is accepted, so an operator can hold a
    single input (measured: ``compute_confidence`` returns no factor and no
    ``skipped_factors`` record, before the supersession as well). The strings
    asserted "a two-input factor" and "the surviving input is renumbered",
    describing an object that does not exist — and the idx-0 source arm asserted the
    renumbering UNCONDITIONALLY, which is the same arity claim: measured on the
    canonical TWO-input directional operator, deleting the source edge leaving ONE
    input does not renumber anything, the factor stops running and NO skip record
    marks it (``skipped: None``, survivor at a bare prior).
    """
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_operator("IMPL", v1["id"], [], direction="->")
    assert _q(sdk, "MATCH ()-[r:IMPL]->() RETURN count(r)", {}) == [[1]], \
        "fixture drifted: the operator has more than one input"
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "has no factor to begin with" in issues[0].detail, issues[0].detail
    assert "a two-input factor is then skipped" not in issues[0].detail, (
        "the row cannot prove two inputs exist: " + issues[0].detail)
    fix = issues[0].fix
    # ⛔ The renumbering hazard is scoped to the rows that HAVE a survivor to
    # renumber, and the zero-survivor case is named rather than left to "no factor to
    # begin with" (#12111 review: measured on a two-input operator, one survivor after
    # the delete is NOT renumbered — the factor stops with an EMPTY skip record).
    assert "has no factor to begin with" in fix, fix
    assert "with TWO OR MORE inputs surviving" in fix, (
        "the renumbering is conditional on there being a survivor to renumber: "
        + fix)
    assert "with exactly ONE left the factor stops running altogether" in fix, fix
    assert "any SURVIVING input is renumbered" not in fix, (
        "that phrasing asserts the renumbering for every survivor count: " + fix)


# ── 3. the history surface stops hiding a terminal chain head ──────


def test_a_flag_dead_operator_is_not_reported(sdk):
    """EP's operator filter requires ``coalesce(outdated, false) = false``.

    A FLAG-dead operator never builds a factor, so the Object's supersession
    loses nothing through this edge and a HIGH finding would carry a causal claim
    that is false of the node in front of the reader — the ``invalidate_point``
    shape, which stamps only the legacy flag and leaves ``status`` alone.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    assert _issues(sdk, "superseded_object_active_edges"), \
        "fixture broken: the live-operator case must report"
    _q(sdk, "MATCH (o:Point {id:$i}) SET o.outdated = true", {"i": op["id"]})
    assert _issues(sdk, "superseded_object_active_edges") == [], (
        "a flag-dead operator was reported as an active factor — EP never builds "
        "it (#7075's predicate is coalesce(outdated,false) = false)")


def test_every_repair_names_the_section_3_1_triage(sdk):
    """ONTOLOGY §3.1: a per-edge DISPOSITION, never a blanket re-point.

    §3.1 requires carry / drop / **pend** per edge with the rationale recorded,
    and a NAND that MOTIVATED the correction must not re-attach — the successor
    would inherit the refutation its predecessor was retired in response to
    (Case 2). The check cannot tell that case apart, so EVERY arm must name both,
    say that `pend` has no door today, and warn that a raw write is not
    rebuild-durable (IMPL/NAND are absent from ``STRUCTURAL_REPOINT_RELS``).
    """
    p = sdk.create_point("statement", "the gateway refutes the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_operator("NAND", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    issues = _issues(sdk, "superseded_object_active_edges")
    assert issues, "fixture broken: the NAND defund was not reported"
    for issue in issues:
        assert "ONTOLOGY §3.1's per-edge triage" in issue.fix, issue.fix
        assert "may CARRY" in issue.fix and "must NOT re-attach" in issue.fix, \
            f"a repair prescribes attaching a NAND unconditionally: {issue.fix!r}"
        assert "no door implements `pend` today" in issue.fix, issue.fix
        assert "is not rebuild-durable" in issue.fix, issue.fix


def test_historical_knowledge_reports_the_current_points_status(sdk):
    """``current`` is the CHAIN HEAD, and for a superseded predecessor that IS
    the predecessor — serving it as ``{id, content}`` alone let a dead point
    read as the claim that stands today."""
    old = sdk.create_point("statement", "gym at 5pm", validFrom="2026-01-01")
    new = sdk.create_point("statement", "gym at 6pm", validFrom="2026-06-01")
    sdk.supersede_point(old["id"], new["id"])

    out = sdk.get_historical_knowledge(old["id"], "2026-03-01")
    assert out["found"] is True
    assert out["current"]["id"] == old["id"]
    assert out["current"]["status"] == "superseded"
    assert out["current"]["terminal"] is True, (
        "the shared lifecycle predicate is what EP branches on; a caller must "
        "not have to re-derive it from the status string")


def test_historical_knowledge_renders_an_absent_status_as_live(sdk):
    """The rendering rule (#9974 R1), pinned on this surface too: an ABSENT
    status READS ``live``, and it is not terminal."""
    live = sdk.create_point("statement", "gym at 7pm")
    out = sdk.get_historical_knowledge(live["id"], "2026-03-01")
    assert out["current"]["status"] == "live"
    assert out["current"]["terminal"] is False


# ── 4. the classification is IDENTITY, not truthiness (#12111 round 13) ──


def test_a_string_flag_on_a_point_is_not_an_operator_input(sdk):
    """``is_operator='false'`` is a DOOR, and ``bool()`` is not EP's comparison.

    ``update_entity(p, is_operator='false')`` is accepted and the property then holds
    the STRING ``'false'`` (measured, on a ``:Point`` and on a ``:Object``). The
    engine compares with STRICT type semantics — ``RETURN 'false' = true, 'false' =
    false, 0 = false, 1 = true`` → ``[False, False, False, False]`` — so EP's
    ``is_operator = true`` arm does NOT match it, while ``_non_operator`` (absence-or-
    the-boolean-``false``) does not admit it either: NEITHER arm. ``bool('false')``
    is True, so the check emitted the OPERATOR arm — "stamp one, idx 0 is the source
    slot" — and performing that repair left the new edge's ``msg_alpha``/``msg_beta``
    None with a re-audit reading CLEAN over a factor EP never built.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2")
    sdk.create_direct_edge("IMPL", p["id"], v1["id"])
    sdk.update_entity(p["id"], is_operator="false")
    assert _q(sdk, "MATCH (a:Point {id:$i}) RETURN a.is_operator", {"i": p["id"]}) \
        == [["false"]], "fixture drifted: the flag is not the string 'false'"
    assert _q(sdk, "RETURN 'false' = true, 'false' = false, 0 = false, 1 = true", {}) \
        == [[False, False, False, False]], "fixture drifted: the engine coerces"
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues, "fixture broken: the row must still be reported"
    detail, fix = issues[0].detail, issues[0].fix
    assert "in EITHER arm" in detail, detail
    assert "idx 0 is the source slot" not in fix, (
        "no factor will read an idx here — EP matches neither arm: " + fix)

    # The measurement the arm rests on: the repair an operator-arm reader would
    # perform restores nothing, and the finding goes quiet anyway.
    _q(sdk, "MATCH (a {id:$p}), (t {id:$t}) CREATE (a)-[:IMPL {idx: 0}]->(t)",
       {"p": p["id"], "t": v2["id"]})
    _q(sdk, "MATCH (a {id:$p})-[r:IMPL]->(o {id:$v}) DELETE r",
       {"p": p["id"], "v": v1["id"]})
    assert _q(sdk, "MATCH (a {id:$p})-[r:IMPL]->(t {id:$t}) "
                   "RETURN r.msg_alpha, r.msg_beta",
              {"p": p["id"], "t": v2["id"]}) == [[None, None]], \
        "fixture drifted: EP propagated through this edge after all"
    assert [i for i in _issues(sdk, "superseded_object_active_edges")] == [], (
        "fixture drifted: the finding must go quiet after the re-point — that is "
        "what makes the wrong arm dangerous")


def test_a_zero_flag_successor_is_operator_shaped(sdk):
    """``is_operator=0`` (an INT) is excluded by EP and was read as a plain claim.

    The door accepts it (measured: the property holds the integer ``0``), and the
    engine's ``0 = false`` is False — so ``_non_operator`` refuses the node and the
    re-point leaves the factor short, while ``bool(0)`` is False and put the row on
    the ordinary arm ("the successor inherits no belief unless it was separately
    rewired") instead of naming the shape.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyA")
    v2 = _obj(sdk, "PolicyB")
    sdk.create_operator("IMPL", p["id"], [v1["id"]])
    sdk.update_entity(v2["id"], is_operator=0)
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.is_operator", {"i": v2["id"]}) \
        == [[0]], "fixture drifted: the flag is not the integer 0"
    _supersede_object(sdk, "PolicyA", "PolicyB")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    assert "OPERATOR-SHAPED" in issues[0].detail, (
        "EP refuses this endpoint as a factor input (0 = false is False), so the "
        "ordinary arm's 'it inherits no belief unless rewired' understates it: "
        + issues[0].detail)
    # ⛔ The SHAPE is EP's PREDICATE, not the stored value: this node's flag reads
    # ``0``, so a finding that says ``(is_operator=true)`` names a value it does not
    # hold, and a reader who checks with ``= true`` finds nothing and cannot tell
    # why the audit flagged it.
    for text in (issues[0].detail, issues[0].fix):
        assert "(is_operator=true)" not in text, (
            "the node's stored flag is the integer 0, not true: " + text)
    # ⛔ The quoted test follows the ROW's lane: this row is an OPERATOR input, so
    # it is ``_non_operator`` — which is why a stored ``0`` makes the successor
    # operator-SHAPED (it is present and not the boolean ``false``).
    assert ("EP's operator-input test: ``is_operator`` is present and not "
            "``false``") in issues[0].detail, issues[0].detail


# ── 5. a consequence is the LADDER, never an arity (#12111 round 13) ──


def test_a_three_input_operator_loses_only_the_dropped_input(sdk):
    """EP skips a factor only when FEWER THAN TWO inputs remain (``ep.py``).

    The operator-shaped-successor arms asserted "the operator is left with NO factor
    (measured: confidences empty, successor absent)". Measured on a THREE-input
    operator: the shaped input is dropped, the factor runs over the two that remain,
    and ``compute_confidence`` returns **two** confidences with ``skipped: None`` —
    so the string claims a measurement of a two-input factor that this row does not
    have, and tells the reader to abandon a repair that would have worked.
    """
    a = sdk.create_point("statement", "alpha claim")
    b = sdk.create_point("statement", "beta claim")
    v1 = _obj(sdk, "PolicyA")
    _obj(sdk, "PolicyB", is_operator=True)
    op = sdk.create_operator("IMPL", a["id"], [b["id"], v1["id"]])
    _supersede_object(sdk, "PolicyA", "PolicyB")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    for text in (issues[0].detail, issues[0].fix):
        assert "OPERATOR-SHAPED" in text, text
        assert "NO factor" not in text and "confidences empty" not in text, (
            "this row has three inputs, and two survive the drop: " + text)
        assert "two or more run without it" in text or "re-scores" in text, text

    # The consequence the ladder names, measured by performing the re-point.
    _q(sdk, "MATCH (o:Point {id:$o})-[r:IMPL]->(t:Object {id:$v}) "
            "MATCH (s:Object {name:'PolicyB'}) MERGE (o)-[nr:IMPL]->(s) "
            "SET nr.idx = r.idx DELETE r", {"o": op["id"], "v": v1["id"]})
    run = sdk.compute_confidence(factors=[a["id"]], require_calibration=False)
    assert len(run["confidences"]) == 2, (
        "fixture drifted: the remaining two inputs must still run: "
        f"{run['confidences']}")
    assert run.get("skipped_factors") is None, run.get("skipped_factors")


def test_a_three_input_operator_with_a_draft_successor_is_not_degenerate(sdk):
    """The draft arm made the same arity claim as the operator-shaped one.

    "the re-point leaves the factor degenerate" holds at two inputs; with three, one
    dropped input leaves two and the factor runs (measured: two confidences,
    ``skipped: None``).
    """
    a = sdk.create_point("statement", "alpha claim")
    b = sdk.create_point("statement", "beta claim")
    v1 = _obj(sdk, "PolicyA")
    _obj(sdk, "PolicyB", status="draft")
    sdk.create_operator("IMPL", a["id"], [b["id"], v1["id"]])
    _supersede_object(sdk, "PolicyA", "PolicyB")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    for text in (issues[0].detail, issues[0].fix):
        assert "'draft'" in text, text
        assert "leaves the factor degenerate" not in text and \
            "the factor degenerate" not in text, (
            "this row has three inputs, so one drop cannot make it degenerate: "
            + text)


def test_clearing_the_shape_without_moving_the_edge_restores_nothing(sdk):
    """The shaped-endpoint repair dropped the re-point step.

    It ended at "clearing the shape … is what restores the support". Measured: the
    edge still ends at the superseded Object, so clearing the flag alone leaves
    ``confidences {}`` and the finding returns as a direct-edge row; clearing AND
    re-pointing onto the live successor builds the factor and the check goes CLEAN.
    """
    actor = sdk.create_entity("subject", "AcmeCorp")["node"]
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_direct_edge("IMPL", actor["id"], v1["id"])
    sdk.update_entity(actor["id"], is_operator=True)
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    fix = issues[0].fix
    assert "clearing the FLAG alone restores nothing" in fix, fix
    assert "onto the successor" in fix, (
        "the repair must still move the edge: " + fix)

    # Measured, in the order the fix now spells it: flag first, edge second.
    sdk.update_entity(actor["id"], is_operator=False)
    assert sdk.compute_confidence(factors=[actor["id"]],
                                 require_calibration=False)["confidences"] == {}, (
        "fixture drifted: clearing the flag restored the support by itself")
    assert [i for i in _issues(sdk, "superseded_object_active_edges")
            if i.node_id == v1["id"]], (
        "fixture drifted: the finding must survive the flag-clearing step")
    _q(sdk, "MATCH (a {id:$a}), (t {id:$t}) CREATE (a)-[:IMPL]->(t)",
       {"a": actor["id"], "t": _q(sdk, "MATCH (o:Object {name:'PolicyV2'}) "
                                      "RETURN o.id", {})[0][0]})
    _q(sdk, "MATCH (a {id:$a})-[r:IMPL]->(o {id:$v}) DELETE r",
       {"a": actor["id"], "v": v1["id"]})
    assert len(sdk.compute_confidence(factors=[actor["id"]],
                                      require_calibration=False)["confidences"]) == 2
    assert [i for i in _issues(sdk, "superseded_object_active_edges")] == []


def test_the_successorless_shaped_arm_does_not_promise_the_flag_restores(sdk):
    """With no successor the flag is not even half a repair: the edge must go.

    The successorless shaped arm ended at "clearing the shape … is what restores the
    support", which is unreachable here — there is no successor to move the edge to,
    so clearing the flag leaves ``msg_alpha`` None and the finding re-reports as a
    direct-edge row.
    """
    actor = sdk.create_entity("subject", "AcmeCorp")["node"]
    v1 = _obj(sdk, "PolicyV1")
    sdk.create_direct_edge("IMPL", actor["id"], v1["id"])
    sdk.update_entity(actor["id"], is_operator=True)
    sdk.update_entity(v1["id"], status="superseded")  # NO successor
    assert _q(sdk, "MATCH (o:Object {id:$i}) RETURN o.supersededBy",
              {"i": v1["id"]}) == [[None]], "fixture drifted"

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    fix = issues[0].fix
    assert "Clearing the shape is NOT sufficient" in fix, fix
    assert "delete this edge" in fix, (
        "with no successor the only complete repair is the delete: " + fix)
    sdk.update_entity(actor["id"], is_operator=False)
    assert sdk.compute_confidence(factors=[actor["id"]],
                                 require_calibration=False)["confidences"] == {}, (
        "fixture drifted: clearing the flag restored the support by itself")
    assert [i for i in _issues(sdk, "superseded_object_active_edges")
            if i.node_id == v1["id"]], (
        "fixture drifted: the flag-clearing step must not clear the finding")


def test_an_integer_one_flag_is_in_neither_arm(sdk):
    """``is_operator=1`` is a door, and this engine does NOT compare ``1 = true``.

    ``update_entity(p, is_operator=1)`` stores the INTEGER ``1`` (measured), and
    ``RETURN 1 = true, 1 = false, 0 = false`` → ``[False, False, False]`` — the same
    strictness that makes ``'false'`` neither arm. So EP's operator arm
    (``is_operator = true``) matches nothing, ``_non_op`` refuses it too (``1 =
    false`` is False), and NO factor is built: measured,
    ``compute_confidence(factors=[p])`` → ``iterations: 0``, a 0.5 stub on the
    Object, no ``skipped_factors``. Accepting the integer announced the OPERATOR
    lane — "idx 0 is the source slot", "stamp one" — over a node with no factor at
    all, and flipped the ``direction`` source to the node's for the same reason.
    """
    p = sdk.create_point("statement", "the gateway enforces the policy")
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    sdk.create_direct_edge("IMPL", p["id"], v1["id"])
    sdk.update_entity(p["id"], is_operator=1, direction="->")
    assert _q(sdk, "MATCH (a:Point {id:$i}) RETURN a.is_operator", {"i": p["id"]}) \
        == [[1]], "fixture drifted: the flag is not the integer 1"
    assert _q(sdk, "RETURN 1 = true, 1 = false, 0 = false", {}) == \
        [[False, False, False]], "fixture drifted: the engine coerces numeric flags"
    run = sdk.compute_confidence(factors=[p["id"]], require_calibration=False)
    assert not (run.get("iterations") or 0), (
        "EP built a factor for the integer-1 flag, so it IS an arm: " + repr(run))
    assert p["id"] not in (run.get("confidences") or {}), repr(
        run.get("confidences"))
    _supersede_object(sdk, "PolicyV1", "PolicyV2")

    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues, "fixture broken: the stale edge must be reported"
    assert "in EITHER arm" in issues[0].detail, (
        "EP matches neither arm for an integer-1 flag, so no factor exists to "
        "strip: " + issues[0].detail)
    assert "idx 0 is the source slot" not in issues[0].fix, (
        "no idx is ever read here — EP builds no factor at all: " + issues[0].fix)


def test_the_zero_live_input_rung_is_stated(sdk):
    """When every input is terminal EP assembles no factor and records NOTHING.

    Measured: a three-input operator whose every input carries
    ``status='superseded'`` → ``compute_confidence`` returns ``confidences {}`` and
    an EMPTY skip record, while three 4b rows are emitted — one per superseded
    anchor — each saying "two or more run without it". The ladder stopped at ONE and
    at two-or-more, so the row a reader is most likely to meet (an operator feeding
    several claims that were all retired) asserted a re-score that never happens.
    """
    p = sdk.create_point("statement", "the policy holds")
    objs = [_obj(sdk, n) for n in ("PolicyA", "PolicyB", "PolicyC")]
    for n in ("A", "B", "C"):
        _obj(sdk, f"{n}Prime")
    sdk.create_operator("IMPL", p["id"], [o["id"] for o in objs])
    for n in ("PolicyA", "PolicyB", "PolicyC"):
        _supersede_object(sdk, n, f"{n.replace('Policy', '')}Prime")

    run = sdk.compute_confidence(factors=[p["id"]], require_calibration=False)
    assert run["confidences"] == {} and not (run.get("skipped_factors") or []), (
        "fixture drifted: EP must build no factor and record no skip here: "
        f"{run.get('skipped_factors')}")

    rows = [i for i in _issues(sdk, "superseded_object_active_edges")
            if i.node_id in {o["id"] for o in objs}]
    assert len(rows) == 3, f"fixture drifted: {len(rows)} rows"
    for row in rows:
        assert "NO participating input left" in row.detail, row.detail
        assert "writes NO skipped-factor record" in row.detail, row.detail


def test_a_refused_carry_is_not_offered_on_a_direct_edge(sdk):
    """The §3.1 clause may not offer a CARRY through the endpoint it just refused.

    Measured: with a direct edge onto an OPERATOR-SHAPED successor, the fix said
    "an IMPL may CARRY to the successor" two clauses after "which EP excludes from a
    factor's inputs … the belief is NOT moved". One of the two is false, and §3.1's
    per-edge rule already answers it — a carry needs a successor EP reads, so this
    row's disposition is DROP (or `pend`).
    """
    o = _obj(sdk, "PolicyV1")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    sdk.create_direct_edge("IMPL", p["id"], o["id"])
    _obj(sdk, "PolicyV2", is_operator=True)
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    fix = next(i for i in _issues(sdk, "superseded_object_active_edges")
               if i.node_id == o["id"]).fix
    assert "OPERATOR-SHAPED" in fix, fix
    assert "may CARRY" not in fix, fix
    assert "the disposition for this edge is DROP" in fix, fix


def test_an_op_type_only_successor_is_READ_by_the_operator_input_arm(sdk):
    """The refusal test is per MECHANISM, and an operator input ADMITS this node.

    EP's DIRECT-edge arm demands ``op_type IS NULL`` on both endpoints, but its
    OPERATOR-INPUT arm admits ``create_entity(obj, op_type='IMPL')`` (measured:
    ``skipped: []`` and the factor runs). A shape test applied to both mechanisms
    therefore told an operator-input row that its successor "is excluded from a
    factor's inputs" — a false block: the prescribed re-point keeps the factor.
    """
    v1 = _obj(sdk, "PolicyV1")
    v2 = _obj(sdk, "PolicyV2", op_type="IMPL")
    p = sdk.create_point("statement", "the gateway enforces the policy")
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]])
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    text = issues[0].detail + issues[0].fix
    assert "OPERATOR-SHAPED" not in text, (
        "EP reads an op_type-only OPERATOR INPUT, so calling it operator-shaped "
        "refuses a repair that works: " + text)

    # ...and the engine agrees: the prescribed re-point keeps the factor.

    _q(sdk, "MATCH (op:Point {id:$op})-[r:IMPL]->(sup:Object {id:$old}), "
            "(succ:Object {id:$new}) "
            "MERGE (op)-[nr:IMPL]->(succ) SET nr.idx = r.idx DELETE r",
       {"op": op["id"], "old": v1["id"], "new": v2["id"]})
    after = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert len(after["confidences"]) >= 2 and not after.get("skipped_factors"), (
        "fixture drifted: EP must read an op_type-only OPERATOR INPUT — the factor "
        "has to keep running with no skip record: " + repr(after))
    assert [i for i in _issues(sdk, "superseded_object_active_edges")] == []


def test_the_source_stripped_claim_names_its_idx_precondition(sdk):
    """EP's ``source_stripped`` skip is gated on ``op_idx_known``.

    An operator with ONE legacy idx-less input edge stands that guard down and the
    factor RUNS with a survivor renumbered into the source slot (measured: three
    confidences, no skip record) — the opposite of "EP drops the whole factor", which
    the directional arms asserted unconditionally.
    """
    v1 = _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    p = sdk.create_point("statement", "alpha")
    t2 = sdk.create_point("statement", "beta")
    t3 = sdk.create_point("statement", "gamma")
    op = sdk.create_operator("IMPL", v1["id"], [p["id"], t2["id"]], direction="->")
    _q(sdk, "MATCH (o:Point {id:$o}), (t {id:$t}) CREATE (o)-[:IMPL]->(t)",
       {"o": op["id"], "t": t3["id"]})  # a legacy idx-less input edge
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    run = sdk.compute_confidence(factors=[op["id"]], require_calibration=False)
    assert len(run["confidences"]) == 3 and not (run.get("skipped_factors") or []), (
        "fixture drifted: the idx-less edge must stand EP's guard down and the "
        f"factor must RUN: {run.get('skipped_factors')}")
    issues = [i for i in _issues(sdk, "superseded_object_active_edges")
              if i.node_id == v1["id"]]
    assert issues
    for text in (issues[0].detail, issues[0].fix):
        assert "EVERY input edge carries an ``idx``" in text, (
            "the skip is conditional on EP's ``op_idx_known``: " + text)
