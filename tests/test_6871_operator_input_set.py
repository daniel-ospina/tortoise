"""#6871 — the operator's INPUT set, and the self-support read that is NOT.

``create_operator`` builds ``inputs = [source_id] + list(target_ids)`` and
emits one typed edge per input, so an operator's ``(o)-[:IMPL]->(x)`` set is its
**input set** (``idx=0`` = the source, ``idx>=1`` = the targets) — not a set of
claims. Read as a claim set, an evidence query
``MATCH (e)-[:INPUT]->(o)-[:IMPL]->(h)`` matches ``e = h``, and #6871 reads that
as "a hypothesis is its own supporting evidence".

Re-measured on this tree, the read SHAPE exists (asserted below, so the fixture
provably reaches the failing state) but the consequence does not:

* ``compute_confidence`` does not double-count it. EP derives its positional
  input list from the operator's typed out-edges (``ep.py`` Batch 2 orders them
  by ``idx``) and builds ``pairs = [(input_ids[0], input_ids[j]) for j >= 1]`` —
  source→targets only, so the normal ``[source, target]`` operator never forms a
  self-pair. That derivation is what test 1 pins: it is why the issue body's
  second "suggested direction" (drop the source's ``IMPL`` edge) is not a fix —
  it would promote a TARGET into the source slot and invert directional IMPL.
* No product read surfaces the self-path. ``why._SUPPORT_OP_CYPHER`` guards both
  the role (``r.idx = 0``) and the identity (``sup.id <> n.id``); test 2 pins
  that through the canonical assembler (``why.assemble_why_blocks``) rather than
  through the query string.
* The replay reproduces the same edge (``projection/edges.py::_create_edges``
  MERGEs ``(o)-[:{rel} {idx}]->(s)`` and ``(s)-[:INPUT {idx}]->(o)`` over the
  same ``inputs`` list), so a rebuild neither introduces nor repairs it — there
  is nothing to repair.

Both pins are BEHAVIOURAL on purpose: a deliberate design change may move them
deliberately, but it cannot move them silently.

Embedded-only (the fixture is ``TortoiseSDK(<tmpfile>)``), so this file is
registered in ``carve_out`` AND in the ``sdk`` surface — the surface is where a
diff selects it, the carve-out is what lets it run without a URI.

Run:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_6871_operator_input_set.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.why import assemble_why_blocks


@pytest.fixture()
def sdk(tmp_path, force_sparse_tfidf):
    """SDK on a fresh embedded graph, one per test.

    ``force_sparse_tfidf`` pins the embedder OFF (conftest's named seam):
    neither pin below reads an embedding, and WITHOUT it every ``create_point``
    blocks on a cold HF model load that the hermetic egress guard refuses and
    huggingface_hub then retries for ~124 s before the suite degrades to
    sparse (measured: 127 s for this file). Determinism, not only speed — the
    pins must not move with the ambient model cache.
    """
    s = TortoiseSDK(str(tmp_path / "6871.db"))
    s._get_proj().g.query("MATCH (n) DETACH DELETE n")
    yield s


def _g(sdk: TortoiseSDK):
    return sdk._get_proj().g


def _mean(sdk: TortoiseSDK, pid: str) -> float:
    a, b = _g(sdk).query(
        "MATCH (n:Point {id:$id}) RETURN coalesce(n.posterior_alpha, 1.0), "
        "coalesce(n.posterior_beta, 1.0)", params={"id": pid},
    ).result_set[0]
    return float(a) / (float(a) + float(b))


def test_the_sources_idx0_edge_is_the_factors_source_slot(sdk):
    """A ``unidirectional`` IMPL flows source -> target BECAUSE the operator
    carries the source's typed edge at ``idx=0``.

    Falsifier: the TARGET must move off its weak prior and the SOURCE must not
    move off its strong one. Drop the source's ``IMPL`` edge — the read-side
    "fix" #6871 calls its second suggested direction — and EP's ``input_ids[0]``
    becomes the target: the factor then flows INTO the source and leaves the
    target at its prior. The assertion catches that inversion.
    """
    src = sdk.create_point("evidence", "S: strong source", status="live")["id"]
    tgt = sdk.create_point("hypothesis", "T: weak target", status="live")["id"]
    op = sdk.create_operator("IMPL", src, [tgt], direction="unidirectional")

    # The fixture reaches the state the assertion is about: one typed edge per
    # input, idx 0 = the source, idx 1 = the target.
    rows = _g(sdk).query(
        "MATCH (o:Point {id:$o})-[r:IMPL]->(c) RETURN c.id, r.idx ORDER BY r.idx",
        params={"o": op["id"]},
    ).result_set
    assert [r[1] for r in rows] == [0, 1]
    assert rows[0][0] == src, (
        "the operator's idx=0 IMPL endpoint must be its SOURCE — EP orders "
        "input_ids by idx and treats input_ids[0] as the factor's source"
    )

    sdk.compute_confidence(evidence={src: (9.0, 1.0), tgt: (1.0, 4.0)},
                           factors=[op["id"]], require_calibration=False)

    assert _mean(sdk, src) == pytest.approx(0.9, abs=1e-6), (
        "a unidirectional IMPL must not back-propagate the target's weakness "
        "into the source"
    )
    assert _mean(sdk, tgt) > 0.2 + 1e-3, (
        f"the target must receive the source's support (prior 0.2, got "
        f"{_mean(sdk, tgt):.6f}) — if the source's idx=0 edge is missing the "
        "target stays at its prior and the source moves instead"
    )


def test_canonical_evidence_read_never_reports_a_point_as_its_own_supporter(sdk):
    """The raw self-path EXISTS; the canonical product read never surfaces it.

    The unfiltered query is the shape #6871 reports (and the shape behind the
    H05 census anomaly). Asserting it matches ``e = h`` proves the fixture
    reaches the falsifying state, so the exclusion asserted afterwards is a real
    exclusion and not an empty-set artifact.
    """
    src = sdk.create_point("evidence", "E1: supports the claim",
                           status="live")["id"]
    sib = sdk.create_point("hypothesis", "X: a sibling", status="live")["id"]
    claim = sdk.create_point("hypothesis", "H: the claim", status="live")["id"]
    # Fan-out: the multi-input shape that made the census see several "inputs"
    # on one hypothesis.
    op = sdk.create_operator("IMPL", src, [claim, sib])

    # (a) the fatal state, read as a claim set: H is among H's own inputs.
    raw = _g(sdk).query(
        "MATCH (e:Point)-[ri:INPUT]->(o:Point {id:$o})-[:IMPL]->(n:Point {id:$n}) "
        "RETURN e.id, ri.idx", params={"o": op["id"], "n": claim},
    ).result_set
    assert claim in [r[0] for r in raw], (
        "fixture does not reach the failing state: the RAW evidence-set read "
        "must match the claim itself (that is the #6871 self-support shape)"
    )

    # (b) the product read: the operator's idx=0 source is the only supporter,
    # and no point is ever its own supporter.
    blocks = assemble_why_blocks(sdk._get_proj(), [src, claim, sib])
    for pid, label in ((claim, "the target"), (sib, "the sibling target"),
                       (src, "the source")):
        chain = [c["point_id"] for c in
                 (blocks.get(pid) or {}).get("support_chain") or []]
        assert pid not in chain, (
            f"{label} is reported as its own supporting evidence: {chain}"
        )
    assert [c["point_id"] for c in
            (blocks.get(claim) or {}).get("support_chain") or []] == [src], (
        "the claim's only supporter is the operator's idx=0 source"
    )


def test_analyze_support_reads_are_idx_role_guarded(sdk):
    """#6871 claim (2) on a PRODUCT surface.

    The operator's typed edge set is its INPUT set (idx 0 = source, idx >= 1 =
    targets), so a support read must treat idx=0 as the SUPPORTING side and
    idx>=1 as the supported one. Pre-fix these two `tortoise_analyze` templates
    guarded identity only (`target.id <> supporter.id`), which made the relation
    symmetric — asking for the SOURCE's support returned its own target.

    The queries below are the exact text `TEMPLATES[name]["cypher"]` hands the
    projector, with the same params `classify()` supplies. Falsifier: drop the
    `coalesce(r_out.idx, 0) >= 1` arm and the second assertion in each loop reds.
    """
    from tortoise.analyze import TEMPLATES

    src = sdk.create_point("evidence", "SOURCEMARKER", status="live")["id"]
    hyp = sdk.create_point("hypothesis", "HYPOMARKER", status="live")["id"]
    sdk.create_operator("IMPL", src, [hyp])
    proj = sdk._get_proj()

    def _run(name: str, entity: str) -> list[str]:
        return [r[0] for r in proj.g.query(
            TEMPLATES[name]["cypher"],
            params={"entity": entity, "limit": 20}).result_set]

    for name in ("strongest_for", "evidence_chain"):
        assert _run(name, "HYPOMARKER") == [src], (
            f"{name} must serve the operator's idx=0 SOURCE as the support "
            f"of its idx>=1 target"
        )
        assert _run(name, "SOURCEMARKER") == [], (
            f"{name} served the SOURCE's own target as ITS support — the "
            "#6871 idx-role inversion (the read is symmetric without the guard)"
        )


def test_annotate_ep_batch_does_not_count_the_sources_own_edge(sdk):
    """#6871 on the wire: `impl_count` is an INCOMING-evidence count.

    The source's idx=0 edge points AT the source, so unguarded it counted as
    evidence supporting the source — inflating `impl_count` and diluting the
    `contention` ratio (nand/(impl+nand)) that `SearchResult.to_dict()` puts on
    the wire.
    """
    from tortoise.search_engine import annotate_ep_batch

    src = sdk.create_point("evidence", "S: the source", status="live")["id"]
    hyp = sdk.create_point("hypothesis", "H: the target", status="live")["id"]
    sdk.create_operator("IMPL", src, [hyp])

    out = annotate_ep_batch(sdk._get_proj().g, [src, hyp])
    assert out[src].evidence.impl_count == 0, (
        "the source counted its own idx=0 edge as incoming evidence"
    )
    assert out[hyp].evidence.impl_count == 1, (
        "the target must still count the operator's idx>=1 edge as support"
    )


def test_annotate_ep_batch_still_counts_operator_less_direct_edges(sdk):
    """The #6871 role guard must not swallow `create_direct_edge` support.

    A direct edge is written with NO `idx` (there is no operator to index it),
    so an idx-ONLY guard (`coalesce(r.idx, 0) >= 1`) silently zeroed
    `impl_count` / `nand_count` / `contention` for every directly-supported
    claim. The guard therefore mirrors `why._CONFLICTS_CYPHER`: an
    operator-mediated edge obeys the role index, an operator-less direct edge
    carries no role and stays evidence by definition.
    """
    from tortoise.search_engine import annotate_ep_batch

    sup = sdk.create_point("evidence", "S: direct support", status="live")["id"]
    atk = sdk.create_point("hypothesis", "A: direct attack", status="live")["id"]
    hyp = sdk.create_point("hypothesis", "H: supported directly",
                           status="live")["id"]
    sdk.create_direct_edge("IMPL", sup, hyp)
    sdk.create_direct_edge("NAND", atk, hyp)

    out = annotate_ep_batch(sdk._get_proj().g, [hyp])
    assert out[hyp].evidence.impl_count == 1, (
        "a direct IMPL edge is support and must still be counted"
    )
    assert out[hyp].evidence.nand_count == 1, (
        "a direct NAND edge is counter-evidence and must still be counted"
    )
    assert out[hyp].contention == pytest.approx(0.5)


def test_counter_arguments_is_nand_direction_aware(sdk):
    """#6871 on `tortoise_analyze`, direction-aware NAND.

    DIRECTED (`unidirectional`): the operator attacks source -> targets, so the
    attacked claim's counter is the attacker and the attacker has none —
    identity-only matching returned the victim as the attacker's own counter.
    MUTUAL (the `bidirectional` default): all members conflict, so naming either
    as the other's counter is correct.
    """
    from tortoise.analyze import TEMPLATES

    def _run(entity: str) -> list[str]:
        return [r[0] for r in sdk._get_proj().g.query(
            TEMPLATES["counter_arguments"]["cypher"],
            params={"entity": entity, "limit": 20}).result_set]

    datk = sdk.create_point("hypothesis", "DIR-ATKMARK", status="live")["id"]
    dvic = sdk.create_point("hypothesis", "DIR-VICMARK", status="live")["id"]
    sdk.create_operator("NAND", datk, [dvic], direction="unidirectional")
    assert _run("DIR-VICMARK") == [datk], "the attacked claim's counter is the attacker"
    assert _run("DIR-ATKMARK") == [], (
        "the attacker returned the claim it attacks as its own counter-argument"
    )

    matk = sdk.create_point("hypothesis", "MUT-ATKMARK", status="live")["id"]
    mvic = sdk.create_point("hypothesis", "MUT-VICMARK", status="live")["id"]
    sdk.create_operator("NAND", matk, [mvic])  # default: mutual
    assert _run("MUT-VICMARK") == [matk]
    assert _run("MUT-ATKMARK") == [mvic], (
        "a MUTUAL NAND makes the two endpoints mutually exclusive, so each is "
        "a legitimate counter of the other"
    )


def test_state_counter_evidence_surfaces_the_directed_attacker_only(sdk):
    """#6871 on the state surface, DIRECTED NAND.

    The operator-mediated half obeys the role index, so the operator's idx=0
    endpoint — the attacker — is not contested by the operator it wrote. Under
    the MUTUAL default both endpoints genuinely conflict, and this must not
    regress.
    """
    datk = sdk.create_point("hypothesis", "DIR-ATK", status="live")["id"]
    dvic = sdk.create_point("hypothesis", "DIR-VIC", status="live")["id"]
    sdk.create_operator("NAND", datk, [dvic], direction="unidirectional")

    ce = sdk._state_counter_evidence([datk, dvic])
    assert ce.get(datk, []) == [], (
        "the directed attacker was surfaced as contested by the NAND it wrote"
    )
    assert len(ce.get(dvic, [])) == 1, "the attacked claim must stay contested"

    matk = sdk.create_point("hypothesis", "MUT-ATK", status="live")["id"]
    mvic = sdk.create_point("hypothesis", "MUT-VIC", status="live")["id"]
    sdk.create_operator("NAND", matk, [mvic])  # default: mutual
    mce = sdk._state_counter_evidence([matk, mvic])
    assert len(mce.get(matk, [])) == 1 and len(mce.get(mvic, [])) == 1, (
        "a MUTUAL NAND makes both endpoints contested"
    )


def test_belief_timeline_does_not_link_directed_co_targets(sdk):
    """#6871: a DIRECTED NAND's co-targets are not temporally NAND-linked.

    `belief_timeline`'s contract is "the temporal edge to the NEXT point in the
    chain". Two co-targets of a directed operator have no edge between them, so
    linking them fabricated a chain step; under the MUTUAL default they really
    are mutually exclusive and the link is correct.
    """
    def _nand_between(a_id: str, b_id: str) -> int:
        rows = sdk._get_proj().g.query(
            "MATCH (op:Point {is_operator:true})-[r1:NAND]->(:Point {id:$a}), "
            "(op)-[r2:NAND]->(:Point {id:$b}) "
            "WHERE coalesce(op.direction, 'bidirectional') IN ['<->', 'bidirectional'] "
            "   OR (r1.idx = 0 AND r2.idx >= 1) "
            "   OR (r1.idx >= 1 AND r2.idx = 0) "
            "RETURN count(op)",
            params={"a": a_id, "b": b_id},
        ).result_set
        return rows[0][0]

    atk = sdk.create_point("hypothesis", "DIR-ATK", status="live")["id"]
    v1 = sdk.create_point("hypothesis", "DIR-V1", status="live")["id"]
    v2 = sdk.create_point("hypothesis", "DIR-V2", status="live")["id"]
    sdk.create_operator("NAND", atk, [v1, v2], direction="unidirectional")
    assert _nand_between(atk, v1) == 1, "the directed attack link must remain"
    assert _nand_between(v1, v2) == 0, (
        "two co-targets of a DIRECTED operator were reported as NAND-linked"
    )

    matk = sdk.create_point("hypothesis", "MUT-ATK", status="live")["id"]
    m1 = sdk.create_point("hypothesis", "MUT-V1", status="live")["id"]
    m2 = sdk.create_point("hypothesis", "MUT-V2", status="live")["id"]
    sdk.create_operator("NAND", matk, [m1, m2])  # default: mutual
    assert _nand_between(m1, m2) == 1, (
        "a MUTUAL NAND makes every member conflict with every other"
    )


def test_disagreement_directed_nand_hides_co_targets(sdk):
    """#6871 on `tortoise_analyze`, DIRECTED NAND.

    `ep.py::_update_nary_factor` splits NAND two ways: `NAND and not mutual`
    pairs the source with each target, the `else` arm pairs every combination.
    For a DIRECTED (`unidirectional`) operator the co-targets are siblings under
    the operator and must not be reported as conflicting; identity-only matching
    bound them as `a`/`b`. Each pair is emitted ONCE — the formatter prints `a`
    then `b` in a fixed order and does not dedupe.
    """
    from tortoise.analyze import TEMPLATES

    atk = sdk.create_point("hypothesis", "DIR-ATKMARK", status="live")["id"]
    v1 = sdk.create_point("hypothesis", "DIR-V1MARK", status="live")["id"]
    v2 = sdk.create_point("hypothesis", "DIR-V2MARK", status="live")["id"]
    sdk.create_operator("NAND", atk, [v1, v2], direction="unidirectional")
    rows = sdk._get_proj().g.query(TEMPLATES["disagreement"]["cypher"],
                                   params={"limit": 20}).result_set
    pairs = {frozenset((r[0], r[3])) for r in rows}
    assert frozenset((atk, v1)) in pairs, f"the real conflict is missing: {pairs}"
    assert frozenset((atk, v2)) in pairs, f"the real conflict is missing: {pairs}"
    assert frozenset((v1, v2)) not in pairs, (
        "a DIRECTED NAND's co-targets are siblings, not a conflict"
    )
    assert len(rows) == len(pairs), (
        f"each conflict must be emitted once, not per direction: {rows}"
    )


def test_disagreement_mutual_nand_keeps_co_targets(sdk):
    """#6871: a MUTUAL NAND is all-pairs — and `bidirectional` is the DEFAULT.

    `ep.py`'s `else` arm pairs every combination for a mutual NAND ("at-most-one
    -true over all members"), so the co-targets of a default `create_operator`
    fan-out genuinely are mutually exclusive and MUST stay in the conflict list.
    This is why an identity-only read was right for the default and wrong only
    for a directed write.
    """
    from tortoise.analyze import TEMPLATES

    atk = sdk.create_point("hypothesis", "MUT-ATKMARK", status="live")["id"]
    v1 = sdk.create_point("hypothesis", "MUT-V1MARK", status="live")["id"]
    v2 = sdk.create_point("hypothesis", "MUT-V2MARK", status="live")["id"]
    sdk.create_operator("NAND", atk, [v1, v2])  # default: bidirectional/mutual
    rows = sdk._get_proj().g.query(TEMPLATES["disagreement"]["cypher"],
                                   params={"limit": 20}).result_set
    pairs = {frozenset((r[0], r[3])) for r in rows}
    assert frozenset((v1, v2)) in pairs, (
        "a MUTUAL NAND makes every member conflict with every other: the "
        f"co-target pair is required, got {pairs}"
    )
    assert len(rows) == len(pairs) == 3, f"all three pairs exactly once, got {rows}"


def test_state_counter_evidence_still_sees_a_direct_nand(sdk):
    """The P1-shape protection on the state surface.

    `_state_counter_evidence`'s clause scopes the idx requirement to
    OPERATOR-mediated edges, so an operator-less direct NAND — which carries no
    `idx` — must still surface its attacker.
    """
    datk = sdk.create_point("hypothesis", "DIRECT-ATK", status="live")["id"]
    dvic = sdk.create_point("hypothesis", "DIRECT-VIC", status="live")["id"]
    sdk.create_direct_edge("NAND", datk, dvic)

    ce = sdk._state_counter_evidence([datk, dvic])
    assert [e["id"] for e in ce.get(dvic, [])] == [datk], (
        "a direct NAND edge is counter-evidence and must still surface"
    )
    assert ce.get(datk, []) == [], (
        "the direct attacker is not itself contested by the edge it wrote"
    )
