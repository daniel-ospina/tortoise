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
    op = sdk.create_operator("IMPL", p["id"], [v1["id"]])
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
    assert "nothing to re-point this input onto" in issue.detail, issue.detail
    # ⛔ The repair must not send the reader to a node that does not exist, and it
    # must name the step that DOES exist (name the successor).
    assert "successor named by its supersededBy" not in issue.fix, issue.fix
    assert "No resolvable successor is named" in issue.fix, issue.fix
    assert "'supersededBy': '<the successor name>'" in issue.fix, issue.fix
    # The door's own signature form (props as ONE dict), pinned again here because
    # this arm is new: a keyword spelling raises TypeError at the caller.
    assert f"tortoise_update_entity('{v1['id']}', {{" in issue.fix, issue.fix

    # ⛔ AND ``archived`` is a TRAP the string must warn about, not a repair it may
    # recommend: archiving clears this check's anchor while EP strips the input
    # exactly as before, so a re-audit reads CLEAN over a still-degenerate factor.
    # Both halves are measured here so the warning cannot rot.
    assert "does NOT clear this" in issue.fix, issue.fix
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
    assert "No resolvable successor is named" in issues[0].fix, issues[0].fix
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
    assert "the next link ('PolicyV3') is LIVE, so target THAT" in issues[0].fix, \
        issues[0].fix


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
    assert issues[0].fix.startswith(
        f"Re-point the successor link: tortoise_update_entity('{v1['id']}', {{"), \
        issues[0].fix
    assert "''" not in issues[0].fix, "a doubled apostrophe is not a usable literal"


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
    assert "No resolvable successor is named" in edge_issues[0].fix, edge_issues[0].fix

    # The counterpart the exclusion must NOT break: a successor that is a
    # DIFFERENT node sharing the name still resolves. Keep this node superseded and
    # self-pointed — with the status cleared there are no superseded Objects at all,
    # so the assertion could not fail (#12111 review).
    _obj(sdk, "PolicyV1")  # a second, distinct Object with the same name
    assert [i for i in _issues(sdk, "superseded_by_unresolvable")
            if i.node_id == v1["id"]] == [], \
        "a twin carrying the name resolves the pointer — the exclusion went too far"


# ── 3. the history surface stops hiding a terminal chain head ──────


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
