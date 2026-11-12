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
    carries an active IMPL/NAND edge, i.e. the defunded factor's shape;
  * ``superseded_by_unresolvable`` (medium) — ``supersededBy`` is a NAME, so
    renaming the successor dangles the only link the predecessor has to it
    (#3590 made the name mutable and the id the identity);
  * ``get_historical_knowledge(...)["current"]`` now carries ``status`` and
    ``terminal``, so a superseded chain head is not served as the claim that
    stands today.

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
    them would misdirect the fix — the check stays silent whenever the name
    still addresses something.
    """
    _obj(sdk, "PolicyV1")
    _obj(sdk, "PolicyV2")
    _supersede_object(sdk, "PolicyV1", "PolicyV2")
    # A second, unrelated Object carrying the successor's name.
    _obj(sdk, "PolicyV2")

    assert _issues(sdk, "superseded_by_unresolvable") == [], (
        "an ambiguous pointer was reported as a dead one — same-name "
        "coexistence is #3573's fact, not #11826's")


def test_an_object_with_no_successor_name_is_not_reported(sdk):
    """The check is about a POINTER, so an absent one is out of its scope."""
    v1 = _obj(sdk, "Orphan")
    _q(sdk, "MATCH (o:Object {id:$i}) SET o.status = 'superseded', "
            "o.supersededBy = ''", {"i": v1["id"]})
    assert _issues(sdk, "superseded_by_unresolvable") == []


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
