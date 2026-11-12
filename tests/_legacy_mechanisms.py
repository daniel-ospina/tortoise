"""Raw writes for MECHANISM SHAPES the closed set no longer produces (#12067).

⛔ Why a raw write, and why a module. #12067 closes the mechanism set at three and
refuses a second mechanism on one pair through BOTH SDK doors (``create_operator``
and ``create_direct_edge``), and ``ingest`` reports the same refusal as a Phase-1
violation. The shapes those doors now refuse still EXIST in every graph written
before the closure — and the detectors that find them (``review_connections``'s
``contradictory`` class, the served-skills contradiction walkthrough) do no
backfill, by ruling. A fixture that produced such a pair THROUGH THE SDK would be
asserting the detector can never fire.

ONE home for the write, because the same shape is needed by more than one suite: a
second copy is how one fixture drifts from the topology ``create_operator``
actually writes and stops reproducing the live shape.
"""


def legacy_nand_operator(sdk, src_id: str, tgt_id: str, *, label: str) -> str:
    """Write a NAND operator over a pair that ALREADY carries IMPL, the way the
    graph held it before #12067.

    The Cypher is exactly what ``create_operator`` writes: the operator Point,
    the typed edge per target with its ``idx``, and the reverse ``INPUT`` mirror.
    BOTH slots are written — ``idx:0`` is the SOURCE slot ``ep._update_factor``
    reads the forward message from, ``idx:1`` the target; a single-edge write is
    the LEGACY shape (one entry per unordered pair) and would not reproduce the
    live topology.
    """
    pid = f"legacy-nand-{label}"
    g = sdk._get_proj().g
    g.query(
        "CREATE (o:Point {id:$id, is_operator:true, op_type:'NAND', "
        "status:'live', label:$label, direction:'unidirectional'})",
        params={"id": pid, "label": label})
    for idx, node in ((0, src_id), (1, tgt_id)):
        g.query(
            "MATCH (o:Point {id:$oid}), (t:Point) WHERE t.id = $node "
            "MERGE (o)-[r:NAND]->(t) SET r.idx = $idx "
            "MERGE (t)-[:INPUT {idx:$idx}]->(o)",
            params={"oid": pid, "node": node, "idx": idx})
    return pid
