"""#3990 — the why block must name the OPERATOR that mediates a support.

Objective 4's done-state is *"the claim, its provenance, **and the operator
that mediates it, plus any mitigation on that operator**"*. Until this file,
``support_chain`` named only the supporting POINT (``point_id`` /
``content_snippet`` / ``edge`` / ``weight``) and dropped the operator that
carries the implication: ``_SUPPORT_OP_CYPHER`` matched
``(sup)-[:INPUT]->(op {is_operator:true})-[:IMPL]->(n)`` and returned ``sup.*``
only. The operator id was not merely omitted from the payload — it was never
read. ``tradeoffs`` read ``op.id`` and threw it away, keeping only the
mitigation text. So the why layer's operator leg was unobservable on every
surface that emits ``why``, and any instrument written against it would have
measured an empty operator set (#3990's own "structural null" warning, the
same failure mode the C2 entity arm hit with Object = 0).

Contract shape pinned here (§3.1.4, additive-only per #1353 D8):
  ``support_chain[i]`` gains FOUR keys, always present on every entry:
    - ``operator_id``    — the mediating operator Point id, or null when the
                           support rides a bare statement→statement IMPL edge;
    - ``operator_type``  — the operator's ``op_type`` (``IMPL``/``NAND``), or
                           null on a direct edge;
    - ``operator_label`` — the Layer-3 predicate label (ONTOLOGY §8), or null
                           when the operator carries none / on a direct edge;
    - ``mitigation``     — the mitigation text of THAT operator, or null.
  ``tradeoffs[i]`` gains ``operator_id`` (the connecting operator), beside the
  ``mitigation`` key it already carried.

WHY PRESENT-AND-NULL, NOT ABSENT: the dimension is always COMPUTED now, so
``null`` is the honest "computed, unset scalar" (§3.1.3) and an instrument can
tell "no operator named" from "the instrument did not read the operator leg".
An absent key would reproduce exactly the ambiguity this change exists to end.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK
from tortoise.why import assemble_why_blocks

OPERATOR_KEYS = ("operator_id", "operator_type", "operator_label", "mitigation")


@pytest.fixture(autouse=True)
def _w4_on(monkeypatch):
    """The why block only assembles with the W4 flag on."""
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    yield


def _fresh_sdk() -> TortoiseSDK:
    """A private embedded graph — no shared state between tests."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_whyop_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:  # noqa: SIM105
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def _block(sdk: TortoiseSDK, pid: str) -> dict:
    return assemble_why_blocks(sdk._get_proj(), [pid])[pid]


def test_support_chain_names_the_mediating_operator():
    """A support that rides an operator must name it — id, type and label.

    The label is the Layer-3 predicate (``create_operator(label=...)``), which
    is what a reader needs to say *why* the claim is believed; ``op_type`` is
    the epistemic mechanism (IMPL/NAND). Both are read off the operator node.
    """
    sdk = _fresh_sdk()
    evidence = sdk.create_point("evidence", "billing record shows the renewal")
    claim = sdk.create_point("statement", "the contract renewed in March")
    op = sdk.create_operator("IMPL", evidence["id"], [claim["id"]],
                             label="bearsOn")

    chain = _block(sdk, claim["id"])["support_chain"]
    assert len(chain) == 1, chain
    entry = chain[0]
    assert entry["point_id"] == evidence["id"]
    assert entry["operator_id"] == op["id"], (
        f"the support entry must name the operator that mediates it "
        f"(#3990) — got {entry!r}")
    assert entry["operator_type"] == "IMPL"
    assert entry["operator_label"] == "bearsOn"


def test_support_chain_carries_the_mitigation_on_that_operator():
    """Objective 4's other half: "any mitigation on that operator".

    A mitigation is a SEPARATE Point attacking the operator bridge (#4937), so
    it is only reachable through the operator — which is why the operator id
    had to be exposed first.
    """
    sdk = _fresh_sdk()
    evidence = sdk.create_point("evidence", "staging burned half the window")
    claim = sdk.create_point("statement", "the release slipped a week")
    op = sdk.create_operator("IMPL", evidence["id"], [claim["id"]])
    sdk.mitigate_operator(op["id"], "QA gate + staged rollout")

    entry = _block(sdk, claim["id"])["support_chain"][0]
    assert entry["operator_id"] == op["id"]
    assert entry["mitigation"] == "QA gate + staged rollout"


def test_unmitigated_operator_reports_mitigation_null_not_absent():
    """No mitigation ⇒ ``mitigation: None``. Fabricating one, or dropping the
    key, are both wrong: an instrument's W3 leg must be able to RED on a
    fabricated mitigation AND on a missing one."""
    sdk = _fresh_sdk()
    evidence = sdk.create_point("evidence", "two reviewers signed off")
    claim = sdk.create_point("statement", "the design is accepted")
    sdk.create_operator("IMPL", evidence["id"], [claim["id"]])

    entry = _block(sdk, claim["id"])["support_chain"][0]
    assert "mitigation" in entry
    assert entry["mitigation"] is None


def test_direct_edge_support_keeps_the_same_key_shape():
    """A bare statement→statement IMPL edge has NO operator: the four keys are
    still present and null — uniform shape, so "no operator named" is a
    MEASURED null rather than an unread dimension."""
    sdk = _fresh_sdk()
    premise = sdk.create_point("statement", "the vendor raised prices 20%")
    claim = sdk.create_point("statement", "we should renegotiate")
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (a:Point {id:$a}), (b:Point {id:$b}) CREATE (a)-[:IMPL]->(b)",
        params={"a": premise["id"], "b": claim["id"]},
    )

    chain = _block(sdk, claim["id"])["support_chain"]
    assert [e["point_id"] for e in chain] == [premise["id"]]
    entry = chain[0]
    for key in OPERATOR_KEYS:
        assert key in entry, f"missing {key} on a direct-edge support: {entry}"
    assert entry["operator_id"] is None
    assert entry["operator_type"] is None
    assert entry["operator_label"] is None
    assert entry["mitigation"] is None


def test_tradeoffs_name_the_connecting_operator():
    """``tradeoffs`` already read ``op.id`` (it joined through the operator to
    reach the mitigation) and DISCARDED it. Expose it: an alternative without
    its connecting operator cannot be explained either."""
    sdk = _fresh_sdk()
    decision = sdk.create_point("decision", "which rollout strategy")
    opt_a = sdk.create_point("option", "staged rollout")
    opt_b = sdk.create_point("option", "big bang")
    op_a = sdk.create_operator("IMPL", decision["id"], [opt_a["id"]])
    op_b = sdk.create_operator("IMPL", decision["id"], [opt_b["id"]])
    sdk.mitigate_operator(op_a["id"], "communicate the delay")

    tradeoffs = _block(sdk, decision["id"])["tradeoffs"]
    by_point = {t["point_id"]: t for t in tradeoffs}
    # IDENTITY, not merely "some operator": `is not None and != op_a` would be
    # satisfied by any third id, so the test could not fail on the property it
    # claims (the support leg pins identity the same way).
    assert by_point[opt_a["id"]]["operator_id"] == op_a["id"]
    assert by_point[opt_a["id"]]["mitigation"] == "communicate the delay"
    assert by_point[opt_b["id"]]["operator_id"] == op_b["id"]
    assert by_point[opt_b["id"]]["mitigation"] is None


def test_one_supporter_with_two_operators_names_a_DETERMINISTIC_one():
    """A supporter can be mediated by more than one operator, and the contract
    has ONE entry per supporter — so the choice must not depend on which edge
    the storage layer returns first.

    Measured before this rule: the surfaced operator (and with it the surfaced
    mitigation) flipped with insertion order, so the same graph reported a
    different reason — and could report ``mitigation: null`` for a claim that
    IS mitigated through the other operator.
    """
    ids = []
    for order in (("first", "second"), ("second", "first")):
        sdk = _fresh_sdk()
        evidence = sdk.create_point("evidence", "the audit found 3 leaks")
        claim = sdk.create_point("statement", "the seal is defective")
        for tag in order:
            op = sdk.create_operator("IMPL", evidence["id"], [claim["id"]],
                                     label=tag)
            ids.append(op["id"])
        entry = _block(sdk, claim["id"])["support_chain"][0]
        assert entry["operator_id"] == min(ids[-2:]), (
            "both insertion orders must name the same (lowest-id) operator")
        sdk.close()

    sdk_a = _fresh_sdk()
    ev_a = sdk_a.create_point("evidence", "the audit found 3 leaks")
    cl_a = sdk_a.create_point("statement", "the seal is defective")
    op_a = sdk_a.create_operator("IMPL", ev_a["id"], [cl_a["id"]], label="a")
    sdk_a.create_operator("IMPL", ev_a["id"], [cl_a["id"]], label="b")
    assert _block(sdk_a, cl_a["id"])["support_chain"][0]["operator_id"] \
        == op_a["id"]


def test_one_operator_with_two_mitigations_picks_deterministically():
    """A graph where ONE operator carries TWO ``mitigated_by`` edges must report
    the same mitigation regardless of which edge the storage layer returns
    first.

    The public ``mitigate_operator`` is idempotent (a second call UPDATES the
    existing mitigation Point), so this state cannot be produced through it —
    it is written directly here, which is exactly how a legacy or hand-repaired
    graph carries it.

    SCOPE OF WHAT THIS PINS, stated plainly: it pins the RULE (the lowest
    mitigation id wins) and its stability across repeated reads. It is NOT a
    discrimination against a "first row wins" fold on this engine — measured:
    FalkorDB returns these rows in id order, so the two rules agree here, and
    the edge is written in reverse creation order deliberately to give them a
    chance to disagree. The rule is pinned anyway because row order is not a
    documented guarantee: a fold that followed it would silently change which
    caveat the block reports if the plan changed.
    """
    for order in (("alpha reason", "beta reason"), ("beta reason", "alpha reason")):
        sdk = _fresh_sdk()
        evidence = sdk.create_point("evidence", "the audit found 3 leaks")
        claim = sdk.create_point("statement", "the seal is defective")
        op = sdk.create_operator("IMPL", evidence["id"], [claim["id"]])
        proj = sdk._get_proj()
        # The LOWEST-id mitigation point is created first and its edge written
        # LAST, so a "first row wins" fold and the id rule have a chance to
        # disagree (measured: they do not on FalkorDB — see the docstring).
        points = [sdk.create_point("statement", f"[MITIGATION] {text}")
                  for text in order]
        texts = {p["id"]: text for p, text in zip(points, order, strict=True)}
        for mit in reversed(points):
            proj.g.query(
                "MATCH (op:Point {id:$o}), (m:Point {id:$m}) "
                "CREATE (op)-[:mitigated_by]->(m)",
                params={"o": op["id"], "m": mit["id"]},
            )
        # TWO properties, both about ONE graph (ids differ across graphs, so
        # comparing the TEXT across two graphs would compare two different
        # inputs):
        #   (a) the LOWEST mitigation id wins — the tie-break rule itself;
        #   (b) repeated reads are STABLE — the pre-fix defect was that the
        #       surfaced text followed row order, not a stated rule.
        reads = [_block(sdk, claim["id"])["support_chain"][0]["mitigation"]
                 for _ in range(5)]
        assert set(reads) == {texts[min(texts)]}, (
            f"expected only the lowest-id mitigation {texts[min(texts)]!r}, "
            f"got {reads!r} (all mitigations: {texts!r})")
        sdk.close()


def test_the_operator_fields_are_a_READ_of_the_live_operator_node():
    """Mutation control: the named operator is READ from the graph on every
    assembly, not cached or derived from the edge.

    Deleting the edge cannot test this (the support entry disappears either
    way), so the operator NODE is mutated instead — ``op_type`` and ``label``
    change while ``is_operator`` and the IMPL bridge stay intact. An
    implementation that returned a cached/annotated value would not follow.
    """
    sdk = _fresh_sdk()
    evidence = sdk.create_point("evidence", "the audit found 3 leaks")
    claim = sdk.create_point("statement", "the seal is defective")
    op = sdk.create_operator("IMPL", evidence["id"], [claim["id"]],
                             label="supports")

    before = _block(sdk, claim["id"])["support_chain"][0]
    assert before["operator_label"] == "supports"
    assert before["operator_type"] == "IMPL"

    sdk._get_proj().g.query(
        "MATCH (op:Point {id:$o}) SET op.label = 'hasPart', op.op_type = 'NAND'",
        params={"o": op["id"]},
    )
    after = _block(sdk, claim["id"])["support_chain"][0]
    assert after["operator_id"] == op["id"]
    assert after["operator_label"] == "hasPart", (
        "the label must follow the live operator node, not a cached copy")
    assert after["operator_type"] == "NAND", (
        "the type must follow the live operator node")


def test_mixed_edge_supporter_keeps_its_operator_entry():
    """A supporter reachable through BOTH legs (operator-mediated AND a direct
    IMPL edge) must keep the OPERATOR entry.

    The direct leg runs second with ``merge=True`` and unions by point id; the
    union used ``setdefault``, so the operator entry wins. Nothing pinned that:
    swapping it for an assignment re-introduces the pre-#3990 clobber (the
    operator keys become null) while every point-id assertion still passes.
    """
    sdk = _fresh_sdk()
    evidence = sdk.create_point("evidence", "the audit found 3 leaks")
    claim = sdk.create_point("statement", "the seal is defective")
    op = sdk.create_operator("IMPL", evidence["id"], [claim["id"]],
                             label="supports")
    sdk.mitigate_operator(op["id"], "only if measured")
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (a:Point {id:$a}), (b:Point {id:$b}) CREATE (a)-[:IMPL]->(b)",
        params={"a": evidence["id"], "b": claim["id"]},
    )

    chain = _block(sdk, claim["id"])["support_chain"]
    entries = [e for e in chain if e["point_id"] == evidence["id"]]
    assert len(entries) == 1, (
        f"a supporter reachable through both legs must appear ONCE: {chain}")
    assert entries[0]["operator_id"] == op["id"], (
        f"the operator entry must win the merge, got {entries[0]}")
    assert entries[0]["mitigation"] == "only if measured"
