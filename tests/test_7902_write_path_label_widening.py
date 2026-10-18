"""#7902 — the operator-endpoint WRITE path inherits the #7853 label widening.

Owner ruling #7813: confidence (EP) propagates across :Point / :Subject /
:Object / :Event. #7853 widened the READ half (ep.py + the sdk read
surfaces). The WRITE half still admitted only Point/Event, so a
:Subject/:Object endpoint was unreachable end to end:

    create_operator("IMPL", p, [subject]) ->
        ValueError: Cannot create operator: Points [...] do not exist
    create_direct_edge("IMPL", subject, object) ->
        ValueError: ... endpoint '...' does not exist or is not a Point

This file pins the WRITE half (#7902):
  1. ``create_operator`` admits all four epistemic endpoint labels.
  2. ``create_direct_edge`` admits all four (the direct-edge policy
     re-decision under the #7813 ruling).
  3. ``_affected_factors`` Batch 3 (operator-less direct edges) returns a
     factor for a direct edge between non-Point endpoints.
  4. ``_load_cache``'s direct-edge back-message read admits a non-Point
     SOURCE endpoint (the half #7853 left Point-only, deferred to #7902).

End-to-end posterior propagation for the newly-writable labels was the
#7853 read half. A fresh-context review (P1) measured that landing the WRITE
half alone made a widened write a SILENT NO-OP: the operator/direct edge wrote
but the EP engine could not see it (`_affected_factors`/`_affected_claims`
were Point-only), so `ep.run` did zero work. #7902 therefore now widens the
reads the write path depends on too — the EP engine (`ep.py`), the projection
fold (`projection/edges.py`), and the operator-dedup id sweep
(`sdk._find_operator`) — so a newly-writable endpoint is EP-live on write AND
on replay. (#7853's branch converges on the same shared declaration.)

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
      tests/test_7902_write_path_label_widening.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from tortoise.ep import TortoiseEP
from tortoise.live import (
    EPISTEMIC_LABELS,
    epistemic_disjunction,
    epistemic_label_queries,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db = os.path.join(tempfile.mkdtemp(prefix="s7902_"), "test.db")
    s = TortoiseSDK(db)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db), ignore_errors=True)


def _point(sdk: TortoiseSDK, content: str) -> str:
    return sdk.create_point("statement", content, status="live")["id"]


def _node(sdk: TortoiseSDK, label: str, node_id: str) -> str:
    """Create a bare epistemic node of ``label`` (Subject/Object/Event).

    The SDK mints :Subject/:Object/:Event nodes through create_entity and the
    pack/entity paths; this helper writes them directly so the test pins the
    LABEL handling and not the (separately covered) entity-write surface.
    """
    if label == "Event":
        sdk._get_proj().g.query(
            "CREATE (e:Event {id:$id, eventId:$id, eventKind:'test', "
            "startedAt:'2026-01-01T00:00:00Z', name:$n})",
            params={"id": node_id, "n": node_id},
        )
    else:
        sdk._get_proj().g.query(
            f"CREATE (n:{label} {{id:$id, name:$n}})",
            params={"id": node_id, "n": node_id},
        )
    return node_id


def _edge_count(sdk: TortoiseSDK, src: str, rel: str, tgt: str) -> int:
    rows = sdk._get_proj().g.query(
        f"MATCH (a {{id:$a}})-[r:{rel}]->(b {{id:$b}}) RETURN count(r)",
        params={"a": src, "b": tgt},
    ).result_set
    return int(rows[0][0]) if rows else 0


def _labels(sdk: TortoiseSDK, node_id: str) -> list[str]:
    rows = sdk._get_proj().g.query(
        "MATCH (n {id:$id}) RETURN labels(n)", params={"id": node_id},
    ).result_set
    return list(rows[0][0]) if rows else []


# ═══════════════════════════════════════════════════════════════════════
# Helpers — the shared abstraction (#7902 reuses #7853's declaration)
# ═══════════════════════════════════════════════════════════════════════

def test_epistemic_labels_are_the_four_ruling_labels():
    assert EPISTEMIC_LABELS == ("Point", "Subject", "Object", "Event")


def test_epistemic_disjunction_is_a_where_fragment():
    clause = epistemic_disjunction("s")
    assert clause.startswith("(") and clause.endswith(")")
    for label in EPISTEMIC_LABELS:
        assert f"s:{label}" in clause


def test_epistemic_label_queries_expands_once_per_label():
    queries = epistemic_label_queries("MATCH (n:{label} {id:$id}) RETURN n.id")
    assert len(queries) == len(EPISTEMIC_LABELS) == 4
    for label, q in zip(EPISTEMIC_LABELS, queries):  # noqa: B905
        assert f"(n:{label} " in q
        assert "{id:$id}" in q  # property maps untouched (str.replace)


# ═══════════════════════════════════════════════════════════════════════
# 1. create_operator — all four epistemic endpoint labels are writable
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("label", ["Subject", "Object", "Event"])
def test_create_operator_admits_every_epistemic_target(sdk, label):
    src = _point(sdk, "strong source")
    tgt = _node(sdk, label, f"{label.lower()}-7902-tgt")
    op = sdk.create_operator("IMPL", src, [tgt], direction="bidirectional")
    assert op["id"]
    assert _edge_count(sdk, tgt, "INPUT", op["id"]) == 1
    # the typed edge is the operator -> endpoint arm
    rows = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$o})-[r:IMPL]->(t) RETURN labels(t), t.id",
        params={"o": op["id"]},
    ).result_set
    assert tgt in {r[1] for r in rows}
    tgt_labels = [row[0] for row in rows if row[1] == tgt]
    assert tgt_labels and label in tgt_labels[0]


def test_create_operator_admits_non_point_source(sdk):
    """The SOURCE slot (idx=0) accepts a non-Point label too."""
    obj = _node(sdk, "Object", "obj-7902-src")
    tgt = _point(sdk, "plain target")
    op = sdk.create_operator("IMPL", obj, [tgt])
    assert op["id"]
    assert _edge_count(sdk, obj, "INPUT", op["id"]) == 1


def test_create_operator_part_whole_accepts_non_point(sdk):
    """Part/whole operators (hasPart edge type) widen with the same set."""
    sub = _node(sdk, "Subject", "sub-7902-part")
    obj = _node(sdk, "Object", "obj-7902-part")
    op = sdk.create_operator("contains", sub, [obj])
    assert _edge_count(sdk, op["id"], "hasPart", obj) == 1


def test_create_operator_point_path_unchanged(sdk):
    a = _point(sdk, "A")
    b = _point(sdk, "B")
    op = sdk.create_operator("IMPL", a, [b])
    assert _edge_count(sdk, op["id"], "IMPL", b) == 1
    assert _edge_count(sdk, b, "INPUT", op["id"]) == 1


def test_create_operator_still_rejects_missing_endpoint(sdk):
    a = _point(sdk, "A")
    with pytest.raises(ValueError, match="do not exist"):
        sdk.create_operator("IMPL", a, ["ghost-7902"])


def test_create_operator_still_rejects_source_endpoint(sdk):
    """A :Source is NOT an epistemic node — the widening is not a blanket."""
    a = _point(sdk, "A")
    src = sdk.create_source("https://pre.example/7902", "report")
    src_id = src.get("id") or src.get("url")
    with pytest.raises(ValueError, match="do not exist"):
        sdk.create_operator("IMPL", a, [src_id])


# ═══════════════════════════════════════════════════════════════════════
# 2. create_direct_edge — direct-edge endpoint policy re-decided
# ═══════════════════════════════════════════════════════════════════════

def test_create_direct_edge_subject_to_object(sdk):
    sub = _node(sdk, "Subject", "sub-7902-de")
    obj = _node(sdk, "Object", "obj-7902-de")
    r = sdk.create_direct_edge("IMPL", sub, obj)
    assert r == {"direct_edge": "IMPL", "from": sub, "to": obj,
                 "created": True, "deduped": False}
    assert _edge_count(sdk, sub, "IMPL", obj) == 1


@pytest.mark.parametrize("src_label,tgt_label", [
    ("Subject", "Object"),
    ("Object", "Subject"),
    ("Event", "Point"),
    ("Subject", "Event"),
])
def test_create_direct_edge_admits_epistemic_label_pairs(sdk, src_label,
                                                         tgt_label):
    src = (_point(sdk, "P") if src_label == "Point"
           else _node(sdk, src_label, f"{src_label.lower()}-7902-s"))
    tgt = (_point(sdk, "Q") if tgt_label == "Point"
           else _node(sdk, tgt_label, f"{tgt_label.lower()}-7902-t"))
    r = sdk.create_direct_edge("IMPL", src, tgt)
    assert r["created"] is True
    # exactly one edge; the resolved label anchored the MERGE (no dup nodes)
    assert _edge_count(sdk, src, "IMPL", tgt) == 1
    assert _labels(sdk, src) == [src_label]
    assert _labels(sdk, tgt) == [tgt_label]


def test_create_direct_edge_non_point_idempotent(sdk):
    """The bare-MERGE dedup contract holds for the widened labels too."""
    sub = _node(sdk, "Subject", "sub-7902-idem")
    obj = _node(sdk, "Object", "obj-7902-idem")
    assert sdk.create_direct_edge("IMPL", sub, obj)["created"] is True
    r2 = sdk.create_direct_edge("IMPL", sub, obj)
    assert r2["created"] is False and r2["deduped"] is True
    assert _edge_count(sdk, sub, "IMPL", obj) == 1


def test_create_direct_edge_point_path_unchanged(sdk):
    a = _point(sdk, "A")
    b = _point(sdk, "B")
    assert sdk.create_direct_edge("IMPL", a, b)["created"] is True


def test_create_direct_edge_still_rejects_source_endpoint(sdk):
    """A :Source endpoint stays rejected — the policy widening is epistemic-only."""
    a = _point(sdk, "A")
    src = sdk.create_source("https://pre.example/7902b", "report")
    src_id = src.get("id") or src.get("url")
    with pytest.raises(ValueError, match="does not exist or is not an epistemic"):
        sdk.create_direct_edge("IMPL", a, src_id)


def test_create_direct_edge_still_rejects_operator_endpoint(sdk):
    a = _point(sdk, "A")
    b = _point(sdk, "B")
    op = sdk.create_operator("IMPL", a, [b])["id"]
    with pytest.raises(ValueError, match="is an operator"):
        sdk.create_direct_edge("IMPL", a, op)


def test_create_direct_edge_still_rejects_terminal_endpoint(sdk):
    a = _point(sdk, "A")
    b = _point(sdk, "B")
    sdk.supersede_point(a, b)
    with pytest.raises(ValueError, match="terminal"):
        sdk.create_direct_edge("IMPL", a, b)


# ═══════════════════════════════════════════════════════════════════════
# 3/4. Direct-edge READS widened to match the write path
# ═══════════════════════════════════════════════════════════════════════

def test_batch3_direct_edge_between_non_point_labels_is_a_factor(sdk):
    """Batch 3 must surface a factor for a non-Point direct edge (#7902)."""
    sub = _node(sdk, "Subject", "sub-7902-b3")
    obj = _node(sdk, "Object", "obj-7902-b3")
    sdk.create_direct_edge("IMPL", sub, obj, direction="bidirectional")
    factors = sdk._get_ep()._affected_factors({sub})
    hits = [f for f in factors if f[0] == sub and f[2] == [sub, obj]]
    assert hits, f"Batch 3 dropped the non-Point direct edge: {factors!r}"
    assert hits[0][1] == "IMPL"


def test_batch3_point_only_control_unchanged(sdk):
    """Control: the Point direct edge still yields exactly one factor."""
    a = _point(sdk, "A")
    b = _point(sdk, "B")
    sdk.create_direct_edge("IMPL", a, b)
    factors = sdk._get_ep()._affected_factors({a})
    assert [f for f in factors if f[0] == a and f[2] == [a, b]]


def test_load_cache_back_message_read_admits_non_point_source(sdk):
    """#7902: the back-message read's SOURCE side is widened (the #7853 gap)."""
    sub = _node(sdk, "Subject", "sub-7902-bm")
    obj = _node(sdk, "Object", "obj-7902-bm")
    sdk.create_direct_edge("IMPL", sub, obj, direction="bidirectional")
    ep = sdk._get_ep()
    ep._load_cache({sub, obj})
    assert (sub, obj, "IMPL") in ep._back_cache, (
        "direct-edge back-message slot not loaded for a non-Point source")


def test_ep_engine_construction_smoke(sdk):
    """Sanity: the widened module still imports and builds an engine."""
    assert isinstance(sdk._get_ep(), TortoiseEP)


# ═══════════════════════════════════════════════════════════════════════
# 5. The widened WRITE path is EP-LIVE — not a silent no-op (reviewer P1)
# ═══════════════════════════════════════════════════════════════════════

def _subject_row(sdk: TortoiseSDK, node_id: str):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Subject {id:$id}) "
        "RETURN n.posterior_alpha, n.posterior_beta, n.confidence",
        params={"id": node_id},
    ).result_set
    return rows[0] if rows else None


def test_operator_non_point_input_is_ep_live(sdk):
    """A :Subject operator input must be discoverable AND run by the engine.

    Pre-fix (write path widened, EP reads still Point-only): the operator
    wrote, but `_affected_factors` never returned it and `ep.run` did zero
    work — a loud `ValueError` replaced by a silent no-op.
    """
    src = _point(sdk, "source")
    sub = _node(sdk, "Subject", "sub-7902-oplive")
    op = sdk.create_operator(
        "IMPL", src, [sub], direction="bidirectional")["id"]

    ep = sdk._get_ep()
    affected = ep._affected_claims([src])
    assert sub in affected, f"operator's Subject input not reached: {affected!r}"
    assert op in {f[0] for f in ep._affected_factors(affected)}, (
        "the engine cannot see the operator the write path just created")

    n, _conv = ep.run([src], max_hops=2)
    assert n > 0, "ep.run did zero work for a non-Point operator endpoint"
    post = _subject_row(sdk, sub)
    assert post is not None and post[0] is not None, (
        "the operator endpoint's posterior was never written")


def test_direct_edge_non_point_target_is_ep_live(sdk):
    """A Point→:Subject direct edge must run, not silently converge on 0 work."""
    p1 = _point(sdk, "P1")
    sub = _node(sdk, "Subject", "sub-7902-delive")
    sdk.create_direct_edge("IMPL", p1, sub, direction="bidirectional")

    ep = sdk._get_ep()
    assert sub in ep._affected_claims([p1]), (
        "the direct edge's Subject endpoint is invisible to the affected set")
    n, _conv = ep.run([p1], max_hops=2)
    assert n > 0, "ep.run silently did nothing for a non-Point direct edge"
    post = _subject_row(sdk, sub)
    assert post is not None and post[0] is not None, (
        "the direct-edge endpoint's posterior was never written")


def test_read_write_node_admits_non_point(sdk):
    """`_read_node`/`_write_node` must round-trip a non-Point node."""
    sub = _node(sdk, "Subject", "sub-7902-rw")
    ep = sdk._get_ep()
    ep._clear_caches()
    ep._write_node(sub, 3.0, 1.0)
    ep._clear_caches()
    assert ep._read_node(sub) == (3.0, 1.0), (
        "non-Point posterior did not round-trip through _write_node/_read_node")
    row = sdk._get_proj().g.query(
        "MATCH (n:Subject {id:$id}) RETURN n.posterior_alpha",
        params={"id": sub},
    ).result_set
    assert row and float(row[0][0]) == 3.0, "posterior never persisted"


def test_non_point_back_message_round_trips_and_invalidates(sdk):
    """Belief/message write-back must persist for a non-Point SOURCE endpoint."""
    p1 = _point(sdk, "P1")
    sub = _node(sdk, "Subject", "sub-7902-wb")
    sdk.create_direct_edge("IMPL", p1, sub, direction="bidirectional")

    ep = sdk._get_ep()
    ep.run([p1], max_hops=2)

    def _back(a: str, b: str):
        rows = sdk._get_proj().g.query(
            "MATCH (x)-[r:IMPL]->(y) WHERE x.id=$a AND y.id=$b "
            "RETURN r.back_msg_alpha, r.back_msg_beta",
            params={"a": a, "b": b},
        ).result_set
        return rows[0] if rows else None

    seeded = _back(p1, sub)
    assert seeded is not None and seeded[0] is not None, (
        "the non-Point direct-edge back-message was never flushed")
    ep.invalidate_messages([sub])
    assert _back(p1, sub)[0] is None, (
        "invalidate_messages could not drop a non-Point endpoint's seed")


# ═══════════════════════════════════════════════════════════════════════
# 5. Replay/fold parity — the shared live+replay writer (reviewer P1)
# ═══════════════════════════════════════════════════════════════════════

def _run_fold(sdk: TortoiseSDK, op_id: str, op_type: str, inputs: list[str]):
    """Drive the shared live+replay fold (`_create_edges`) for one operator."""
    sdk._get_proj().g.query(
        "CREATE (o:Point {id:$id, is_operator:true, op_type:$t, "
        "direction:'bidirectional'})",
        params={"id": op_id, "t": op_type},
    )
    sdk._get_proj()._create_edges({
        "id": op_id,
        "is_operator": True,
        "operator": {"op_type": op_type, "inputs": inputs},
    })


_LONG_EPISTEMIC_ID = "01J7902FOLDSUBJECT00000001"


@pytest.mark.parametrize("node_id", ["sub-7902-fold", _LONG_EPISTEMIC_ID])
def test_fold_widens_non_point_operator_input(sdk, node_id):
    """The replay fold must rebuild the typed + INPUT edges for a non-Point input.

    Pre-fix: a long id was skipped (existence probe Point/Event-only) and a
    short id minted a phantom ``:Point`` stub with the SAME id — so a rebuilt
    graph diverged from the live one.
    """
    sub = _node(sdk, "Subject", node_id)
    op_id = "op-7902-fold"
    _run_fold(sdk, op_id, "IMPL", [sub])

    g = sdk._get_proj().g
    typed = g.query(
        "MATCH (o:Point {id:$o})-[r:IMPL]->(s) WHERE s.id=$s RETURN count(r)",
        params={"o": op_id, "s": sub},
    ).result_set
    assert typed[0][0] == 1, "the replay fold skipped the non-Point input"
    inp = g.query(
        "MATCH (s)-[:INPUT]->(o:Point {id:$o}) WHERE s.id=$s RETURN count(*)",
        params={"o": op_id, "s": sub},
    ).result_set
    assert inp[0][0] == 1, "the reverse INPUT edge was not rebuilt"
    labels = [r[0] for r in g.query(
        "MATCH (n {id:$id}) RETURN labels(n)", params={"id": sub},
    ).result_set]
    assert labels == [["Subject"]], (
        f"the fold minted a phantom node for {node_id!r}: {labels!r}")


def test_fold_widens_part_whole_non_point_input(sdk):
    """Part-whole operators (hasPart) widen with the same label set."""
    sub = _node(sdk, "Subject", "sub-7902-fold-part")
    op_id = "op-7902-fold-part"
    _run_fold(sdk, op_id, "contains", [sub])
    g = sdk._get_proj().g
    typed = g.query(
        "MATCH (o:Point {id:$o})-[:hasPart]->(s) WHERE s.id=$s RETURN count(*)",
        params={"o": op_id, "s": sub},
    ).result_set
    assert typed[0][0] == 1
    labels = [r[0] for r in g.query(
        "MATCH (n {id:$id}) RETURN labels(n)", params={"id": sub},
    ).result_set]
    assert labels == [["Subject"]]


# ═══════════════════════════════════════════════════════════════════════
# 5. Operator dedup / idempotency (reviewer P1)
# ═══════════════════════════════════════════════════════════════════════

def test_find_operator_exact_hit_admits_non_point_input(sdk):
    """An exact re-submission with a non-Point input must dedup, not partial-absorb.

    Pre-fix: the exact-hit ``targets`` collect dropped the non-Point input, so
    size(targets) < size(inputs) and the call rerouted into partial-absorb.
    """
    src = _point(sdk, "src")
    sub = _node(sdk, "Subject", "sub-7902-find")
    op = sdk.create_operator(
        "IMPL", src, [sub], direction="bidirectional")["id"]

    found = sdk._find_operator("IMPL", [src, sub], direction="bidirectional")
    assert found is not None, "an exact re-submission must be found"
    assert found["kind"] == "exact", (
        f"a non-Point input rerouted dedup into {found['kind']!r}")
    assert found["id"] == op


# ═══════════════════════════════════════════════════════════════════════
# 6. The missed mirrors of the same widening (reviewer P2 ×2)
# ═══════════════════════════════════════════════════════════════════════

def _graph_dirty(sdk: TortoiseSDK) -> list[str]:
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n.ep_dirty = true RETURN n.id ORDER BY n.id",
    ).result_set
    return [r[0] for r in rows]


def _minimal_payload(session_id: str):
    """A CommitPayload with no points/operators — enough for the graph-state
    read (`_load_commit_graph_state` derives `operators` from the GRAPH)."""
    from tortoise.commit_schema import (
        CommitPayload,
        ExtractorInfo,
        Telemetry,
        TelemetryExtractor,
        TelemetryModel,
    )

    return CommitPayload(
        session_id=session_id,
        client_commit_id=f"ccid-{session_id}",
        captured_at="2026-01-01T00:00:00Z",
        extractor=ExtractorInfo(
            version="value@1.0.0", mode="byok", calibration_version="v3"),
        telemetry=Telemetry(
            extractor=TelemetryExtractor(version="value@1.0.0", mode="byok"),
            model=TelemetryModel(provider="anthropic", id="claude-3-7"),
        ),
    )


def test_reconcile_sees_an_operator_whose_inputs_are_non_point(sdk):
    """The hosted commit-reconcile must use the same four-label disjunction as
    `sdk._find_operator` and the `commit_ops` MITIGATES dedup. Pre-fix it kept a
    hand-spelled ``(t:Point OR t:Event)``: an operator whose input is a
    :Subject/:Object was invisible, so `reconcile_payload` re-admitted it as
    'new' and burned the new-operator budget on every re-send."""
    from tortoise.hosted_api import _load_commit_graph_state

    sub = _node(sdk, "Subject", "sub-7902-reconcile")
    obj = _node(sdk, "Object", "obj-7902-reconcile")
    op = sdk.create_operator(
        "IMPL", sub, [obj], direction="bidirectional")["id"]
    assert op, "the operator node must exist (non-vacuity)"

    state = _load_commit_graph_state(sdk, _minimal_payload("s-7902-reconcile"))
    assert state.operators, (
        "a non-Point input operator is invisible to reconciliation: "
        f"{state.operators!r}")
    assert (sub, obj, "IMPL") in state.operators, (
        f"the widened input labels did not reconcile: {state.operators!r}")


def test_list_batch_detail_agrees_with_list_batches_for_a_non_point_edge(sdk):
    """`list_batches` counts direct edges label-agnostically
    (``MATCH ()-[r:IMPL|NAND]->()``), so a non-Point direct edge is discoverable
    there; `list_batch`'s audit detail must see the SAME edge. Pre-fix the
    detail kept ``(a:Point)-[…]->(b:Point)`` and reported 0 for a batch the
    summary counted — two surfaces disagreeing about one batch."""
    sub = _node(sdk, "Subject", "sub-7902-batch")
    obj = _node(sdk, "Object", "obj-7902-batch")
    assert sdk.create_direct_edge(
        "IMPL", sub, obj, batch_id="batch-7902-so")["created"] is True

    detail = sdk.list_batch("batch-7902-so")
    assert detail["counts"]["direct_edges"] == 1, (
        "list_batch's audit detail dropped the non-Point direct edge")
    assert [(e["from"], e["to"]) for e in detail["direct_edges"]] == [(sub, obj)]

    summary = {b["batch_id"]: b for b in sdk.list_batches()}
    assert summary["batch-7902-so"]["direct_edges"] == 1
    assert (detail["counts"]["direct_edges"]
            == summary["batch-7902-so"]["direct_edges"])


# ═══════════════════════════════════════════════════════════════════════
# 7. PINNED GAP — the residual split to #7927 (not a contract)
# ═══════════════════════════════════════════════════════════════════════

def test_pinned_gap__non_point_write_is_not_graph_persisted():
    """PINNED, not silenced — a measured boundary, not desired behaviour.

    The EP DIRTY MARKER is graph-persisted for ``:Point`` writers only:
    ``_mark_dirty``'s persist + zombie-classification queries,
    ``_hydrate_dirty_roots``, ``_sweep_dirty_roots`` and
    ``_prune_nonexistent_dirty_roots`` all anchor ``MATCH (n:Point)``. So a
    write whose endpoints are ALL non-``:Point`` is EP-live IN the writing
    process (the widened EP reads see it) but EP-inert across the
    request/process boundary.

    #7927 landed the READ half (``analyze._bfs_select_operators`` now selects
    non-``:Point`` roots and direct-edge factors), which fixed the IN-PROCESS
    half of this gap: the write-triggered local dream now sees the written
    factor instead of returning an empty affected set. What remains — and what
    this test still pins — is the CROSS-REQUEST half: the dirty marker never
    reaches the graph, so a fresh SDK runs zero iterations and the endpoint's
    convergence is never recomputed. Widening this lifecycle is NOT a label
    predicate edit — it must move in lockstep with the selector (already
    widened) or a persisted non-Point root is never swept, the #2422 / #1243
    stale-region class.

    Do NOT delete this test: #7927 did NOT close the cross-request gap, and
    deleting the pin would let a later PR claim a guarantee it does not have.
    Delete it only when the lifecycle persistence itself is widened.
    """
    dbdir = tempfile.mkdtemp(prefix="s7902_pin_")
    db = os.path.join(dbdir, "test.db")
    try:
        s1 = TortoiseSDK(db)
        sub = _node(s1, "Subject", "sub-7902-pin")
        obj = _node(s1, "Object", "obj-7902-pin")
        assert s1.create_direct_edge("IMPL", sub, obj)["created"] is True

        assert _graph_dirty(s1) == [], (
            "the dirty marker reached the graph for a non-Point write — the "
            "pinned gap is CLOSED; delete this test (#7927)")
        assert sorted(s1._dirty_roots) == [obj, sub], (
            "in-process mirror only")

        # #7927: the IN-PROCESS half is now FIXED — the widened selector sees
        # the (Subject)-[:IMPL]->(Object) direct-edge factor, so the local dream
        # runs it instead of returning an empty affected set.
        d = s1.dream(mode="local")
        assert sorted(d["affected_claims"]) == sorted([obj, sub]), (
            "the write-triggered local dream no longer sees the non-Point "
            "direct-edge factor — the #7927 selector widening regressed")
        assert d["iterations"] >= 1

        # a FRESH scope (the shape #1163's hydration exists for) sees nothing
        s1.close()
        s2 = TortoiseSDK(db)
        assert s2._hydrate_dirty_roots() == set()
        assert s2.compute_confidence()["iterations"] == 0

        # CONTROL: a :Point endpoint takes the pre-existing persisted path.
        pt = _point(s2, "ctrl-7902-pin")
        obj2 = _node(s2, "Object", "obj-7902-pin")
        assert s2.create_direct_edge("IMPL", pt, obj2)["created"] is True
        assert _graph_dirty(s2) == [pt], "the :Point half must persist"
        s2.close()
    finally:
        shutil.rmtree(dbdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Coverage invariant under the widened labels (#6597 × #7902)
# ═══════════════════════════════════════════════════════════════════════

def test_mixed_label_window_closure_covers_affected(sdk):
    """#6597's `affected ⊆ closure` must survive the #7902 label widening.

    A :Point root with a non-:Point direct neighbour is reachable by
    `_affected_claims` (widened by #7902) but was NOT in `_window_closure`
    (still :Point-only), so `dream()["coverage"]` exceeded 1.0 — measured
    1.5 on this exact shape. The existing #6597 harness uses only Points and
    therefore cannot see the regression.
    """
    p1 = _point(sdk, "p1-7902-mixed")
    p2 = _point(sdk, "p2-7902-mixed")
    sdk.create_direct_edge("IMPL", p1, p2)
    sub = _node(sdk, "Subject", "sub-7902-mixed")
    sdk.create_direct_edge("IMPL", p1, sub)

    # Pin the Point roots as the window (mirrors the #6597 harness): the
    # `create_direct_edge` writes marked the endpoints dirty, and `dream()`
    # would otherwise hydrate a wider window that hides the bug.
    sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.ep_dirty = true "
        "SET n.ep_dirty = null, n.ep_dirty_at = null")
    sdk._dirty_roots.clear()
    sdk._dirty_roots.update({p1, p2})

    affected = sdk._get_ep()._affected_claims([p1, p2], 2)
    closure = sdk._window_closure([p1, p2], 2)
    assert sub in affected, "the widened pass must reach the :Subject"
    assert affected <= closure, (
        f"affected ∖ closure = {sorted(affected - closure)!r} — the widened "
        "affected set trails the coverage denominator")

    result = sdk.dream(mode="local", require_calibration=False, max_hops=2)
    assert 0.0 <= result["coverage"] <= 1.0, (
        f"coverage={result['coverage']} — affected ⊆ closure is broken")


def test_affected_claims_tolerates_unresolved_and_duplicate_seeds(sdk):
    """The batched seed resolution (#7902) must not raise or double-count.

    Covers the two shapes the per-seed loop handled implicitly and the
    batched rewrite must preserve: a seed that resolves to NO epistemic
    label contributes nothing, and a repeated seed is expanded once.
    """
    p1 = _point(sdk, "p1-7902-seeds")
    p2 = _point(sdk, "p2-7902-seeds")
    sdk.create_direct_edge("IMPL", p1, p2)

    ep = sdk._get_ep()
    baseline = ep._affected_claims([p1], 2)
    assert ep._affected_claims([p1, p1], 2) == baseline
    assert ep._affected_claims([p1, "no-such-7902-seed"], 2) == baseline


def test_full_coverage_excludes_unreachable_non_point_component(sdk):
    """Full-mode coverage denominator is the Point-anchored closure, not
    every edged claim: a non-Point component the full pass cannot reach must
    not deflate coverage below 1.0 (the #1244 docstring forbids a fully
    converged pass reporting coverage < 1.0 forever).
    """
    p1 = _point(sdk, "p1-7902-fullA")
    p2 = _point(sdk, "p2-7902-fullA")
    sdk.create_direct_edge("IMPL", p1, p2)
    sub = _node(sdk, "Subject", "sub-7902-fullA")
    obj = _node(sdk, "Object", "obj-7902-fullA")
    sdk.create_direct_edge("IMPL", sub, obj)

    result = sdk.dream(mode="full", require_calibration=False)
    assert result["converged_all"] is True
    assert result["total_affected"] == 2
    assert result["coverage"] == 1.0, (
        f"coverage={result['coverage']} — an unreachable non-Point component "
        "inflated the full-mode denominator")


def test_full_coverage_includes_point_reachable_non_point_neighbour(sdk):
    """The dual of the above: a non-Point neighbour the full pass DOES reach
    must be in the denominator, or `total_affected / reachable` exceeds 1.0.
    """
    p1 = _point(sdk, "p1-7902-fullB")
    p2 = _point(sdk, "p2-7902-fullB")
    sdk.create_direct_edge("IMPL", p1, p2)
    sub = _node(sdk, "Subject", "sub-7902-fullB")
    sdk.create_direct_edge("IMPL", p1, sub)

    result = sdk.dream(mode="full", require_calibration=False)
    assert result["converged_all"] is True
    assert 0.0 <= result["coverage"] <= 1.0, (
        f"coverage={result['coverage']} — the full-mode denominator trails "
        "the widened affected set")


# ═══════════════════════════════════════════════════════════════════════
# The dirty-MARK half widens in lockstep (#7505 review P1)
# ═══════════════════════════════════════════════════════════════════════

def _belief(sdk: TortoiseSDK, node_id: str) -> tuple:
    rows = sdk._get_proj().g.query(
        "MATCH (n {id:$id}) RETURN n.ep_alpha, n.ep_beta, "
        "n.posterior_alpha, n.posterior_beta",
        params={"id": node_id}).result_set
    return tuple(rows[0]) if rows else ()


def test_mitigation_reaches_an_operator_whose_inputs_are_not_points(sdk):
    """#7505 review (P1): a mitigation must move belief on an operator whose
    inputs are ALL non-`:Point` labels.

    #7902/#7853 widened the READ half — `_affected_claims`' operator-seed query
    and `_affected_factors` Batch 2 both admit `:Subject`/`:Object`/`:Event`
    inputs — so such an operator is EP-live and its factors write posteriors.
    The dirty-MARK half has to widen in lockstep, because a mitigation write
    reaches that factor ONLY through `_reverse_bfs_neighbors`' claim query:
    `(m)-[:IMPL]->(op)` is a neutral link, so `mid` on its own admits nothing
    (#5566's rule). Left `:Point`-only, `claim_ids` is EMPTY for this operator,
    `mid` is the only dirty root, the next pass is a clean no-op
    (`diagnostic == 'no_factors'`) and the mitigated strength silently stops
    moving the Object's posterior — the exact half-widening hazard #7902
    documents, on a write path instead of a read path.

    Measured (Object posterior alpha): 1.262738 → 1.140587 with the widened
    claim query, 1.262738 → 1.262738 without it. It also pins that fixing the
    propagation keeps the #7505 property: no permanent dirty root survives the
    converged pass that consumed the mitigation.
    """
    sub = _node(sdk, "Subject", "sub-7505-mit")
    obj = _node(sdk, "Object", "obj-7505-mit")
    op = sdk.create_operator("IMPL", sub, [obj])
    # Converged baseline first: the operator write's own marking must not be
    # what the mitigated pass consumes.
    assert sdk.compute_confidence(require_calibration=False)
    before = _belief(sdk, obj)
    assert before and before != (None, None, None, None), (
        "the non-Point factor must have written a posterior first")

    mid = sdk.mitigate_operator(op["id"], "weaker than it looks",
                                strength=0.5)["id"]
    sdk.compute_confidence(require_calibration=False)
    after = _belief(sdk, obj)
    assert after != before, (
        "a mitigation on an operator with non-:Point inputs must still reach "
        f"its factor — Object belief did not move: {before} -> {after}")

    # #7505: propagation must not come back as a permanent dirty root.
    assert mid not in sdk._dirty_roots
    assert sdk.dream_health_check()["stale_backlog"] == 0
