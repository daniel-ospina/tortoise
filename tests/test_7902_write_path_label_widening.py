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

End-to-end posterior propagation for the newly-writable labels is #7853's
half (its general epistemic reads are still :Point-only on origin/main); the
#7902 tests assert the write and factor-extraction surfaces this issue owns.

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
