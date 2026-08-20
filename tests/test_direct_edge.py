"""Epic #902 §8 — operator-less direct-edge writer + supersede 2a extension.

Plan §5.3: bare-MERGE + SET (one edge per (src,tgt,type), last-writer-wins),
terminal-endpoint guard, typed-endpoint rejection, promotion-on-created-only,
CYCLE-25 NAND direction default, JSONL descriptor emission, supersede 2a
direct-edge repoint both directions with REPOINT descriptor (E2E-11.6).
"""
import os
import tempfile

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db = os.path.join(tempfile.mkdtemp(prefix="s8_"), "test.db")
    s = TortoiseSDK(db)
    yield s
    s.close()


def _two_points(sdk):
    pa = sdk.create_point("statement", "A implies B")["id"]
    pb = sdk.create_point("statement", "B")["id"]
    return pa, pb


class TestCreateDirectEdge:
    def test_creates_direct_edge_no_operator(self, sdk):
        pa, pb = _two_points(sdk)
        r = sdk.create_direct_edge("IMPL", pa, pb)
        assert r == {"direct_edge": "IMPL", "from": pa, "to": pb,
                     "created": True, "deduped": False}
        # exactly ONE edge, no operator node
        proj = sdk._get_proj()
        n = proj.g.query(
            "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) RETURN count(r)",
            params={"a": pa, "b": pb}).result_set[0][0]
        assert n == 1
        ops = proj.g.query(
            "MATCH (o:Point {is_operator:true}) RETURN count(o)").result_set[0][0]
        assert ops == 0

    def test_idempotent_reingest_single_edge(self, sdk):
        pa, pb = _two_points(sdk)
        sdk.create_direct_edge("IMPL", pa, pb)
        r2 = sdk.create_direct_edge("IMPL", pa, pb)
        assert r2["created"] is False and r2["deduped"] is True
        proj = sdk._get_proj()
        n = proj.g.query(
            "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) RETURN count(r)",
            params={"a": pa, "b": pb}).result_set[0][0]
        assert n == 1  # no parallel edge

    def test_last_writer_wins_attribute_change(self, sdk):
        pa, pb = _two_points(sdk)
        sdk.create_direct_edge("IMPL", pa, pb, label="first")
        sdk.create_direct_edge("IMPL", pa, pb, label="second")
        proj = sdk._get_proj()
        lab = proj.g.query(
            "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) RETURN r.label",
            params={"a": pa, "b": pb}).result_set[0][0]
        assert lab == "second"
        n = proj.g.query(
            "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) RETURN count(r)",
            params={"a": pa, "b": pb}).result_set[0][0]
        assert n == 1

    def test_direction_defaults_per_op_type(self, sdk):
        pa, pb = _two_points(sdk)
        sdk.create_direct_edge("IMPL", pa, pb)          # absent -> bidirectional
        pc, pd = _two_points(sdk)
        sdk.create_direct_edge("NAND", pc, pd)          # absent -> unidirectional (CYCLE-25)
        proj = sdk._get_proj()
        d1 = proj.g.query(
            "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) RETURN r.direction",
            params={"a": pa, "b": pb}).result_set[0][0]
        d2 = proj.g.query(
            "MATCH (a:Point {id:$a})-[r:NAND]->(b:Point {id:$b}) RETURN r.direction",
            params={"a": pc, "b": pd}).result_set[0][0]
        assert d1 == "bidirectional"
        assert d2 == "unidirectional"

    def test_explicit_direction_preserved(self, sdk):
        pa, pb = _two_points(sdk)
        sdk.create_direct_edge("IMPL", pa, pb, direction="unidirectional")
        proj = sdk._get_proj()
        d = proj.g.query(
            "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) RETURN r.direction",
            params={"a": pa, "b": pb}).result_set[0][0]
        assert d == "unidirectional"

    def test_terminal_endpoint_rejected(self, sdk):
        pa, pb = _two_points(sdk)
        sdk.supersede_point(pa, pb)  # pa now superseded
        with pytest.raises(ValueError, match="terminal"):
            sdk.create_direct_edge("IMPL", pa, pb)
        # and the other direction
        with pytest.raises(ValueError, match="terminal"):
            sdk.create_direct_edge("IMPL", pb, pa)

    def test_operator_endpoint_rejected(self, sdk):
        pa, pb = _two_points(sdk)
        op = sdk.create_operator("IMPL", pa, [pb])["id"]
        with pytest.raises(ValueError, match="operator"):
            sdk.create_direct_edge("IMPL", pa, op)

    def test_missing_endpoint_rejected(self, sdk):
        pa, _ = _two_points(sdk)
        with pytest.raises(ValueError, match="does not exist"):
            sdk.create_direct_edge("IMPL", pa, "ghost-123")

    def test_self_edge_rejected(self, sdk):
        pa, _ = _two_points(sdk)
        with pytest.raises(ValueError, match="must differ"):
            sdk.create_direct_edge("IMPL", pa, pa)

    def test_invalid_op_type_rejected(self, sdk):
        pa, pb = _two_points(sdk)
        with pytest.raises(ValueError, match="IMPL or NAND"):
            sdk.create_direct_edge("hasPart", pa, pb)

    def test_promotion_only_on_created(self, sdk):
        pa, pb = _two_points(sdk)
        # auto parity: created -> source live
        sdk.create_direct_edge("IMPL", pa, pb, promote_source=True)
        assert sdk.get_point(pa)["status"] == "live"
        # dedup hit: NO re-promotion (and no downgrade)
        sdk.create_direct_edge("IMPL", pa, pb, promote_source=True)
        assert sdk.get_point(pa)["status"] == "live"
        # promote_source=False: no status write
        pc, pd = _two_points(sdk)
        sdk.create_direct_edge("IMPL", pc, pd, promote_source=False)
        assert sdk.get_point(pc)["status"] == "draft"

    def test_descriptor_emitted_on_every_write(self, sdk, tmp_path):
        # REVIEW-FIX P1 (cycle-26): the descriptor is emitted on EVERY write
        # (MERGE-keyed replay is idempotent; a created-only gate loses the
        # edge from the rebuild log on crash-retry AND fails to replay the
        # last-writer-wins attrs post-rebuild).
        import json
        log_path = str(tmp_path / "events.jsonl")
        db = os.path.join(tempfile.mkdtemp(prefix="s8_"), "test.db")
        s2 = TortoiseSDK(db, event_log_path=log_path)
        pa, pb = _two_points(s2)
        s2.create_direct_edge("IMPL", pa, pb, batch_id="b1", label="v1")
        s2.create_direct_edge("IMPL", pa, pb, batch_id="b1", label="v2")  # dedup hit
        lines = [json.loads(l) for l in open(log_path)  # noqa: E741, SIM115
                 if "DirectEdgeCreated" in l]
        assert len(lines) == 2, "every write emits a descriptor"
        # the LAST descriptor carries the FINAL attrs (last-writer-wins replay)
        assert lines[-1]["src"] == pa and lines[-1]["tgt"] == pb
        assert lines[-1]["edge_type"] == "IMPL"
        assert lines[-1]["label"] == "v2"
        assert lines[-1]["batch_id"] == "b1"
        s2.close()


class TestSupersedeDirectEdges:
    def test_repoint_both_directions_preserves_attrs(self, sdk):
        pa, pb = _two_points(sdk)
        pc = sdk.create_point("statement", "C")["id"]
        # direct edges: pa->pb and pc->pa
        sdk.create_direct_edge("IMPL", pa, pb, label="L", confidence=0.8,
                               batch_id="b1")
        sdk.create_direct_edge("NAND", pc, pa, direction="unidirectional")
        sdk.supersede_point(pa, pc)  # repoint pa's edges to pc
        proj = sdk._get_proj()
        # zero direct edges incident to the superseded pa
        n = proj.g.query(
            "MATCH (a:Point {id:$id})-[r:IMPL|NAND]->() "
            "RETURN count(r)", params={"id": pa}).result_set[0][0]
        n2 = proj.g.query(
            "MATCH ()-[r:IMPL|NAND]->(a:Point {id:$id}) "
            "RETURN count(r)", params={"id": pa}).result_set[0][0]
        assert n == 0 and n2 == 0
        # REVIEW-FIX P1 (cycle-26): ZERO self-edges anywhere (the phantom
        # (new)->(new) leak is closed).
        self_edges = proj.g.query(
            "MATCH (a)-[r:IMPL|NAND]->(b) WHERE a.id = b.id RETURN count(r)"
        ).result_set[0][0]
        assert self_edges == 0, "no self-edges after supersede"
        n_pc_pb = proj.g.query(
            "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) "
            "RETURN r.label, r.confidence, r.batch_id",
            params={"a": pc, "b": pb}).result_set
        assert len(n_pc_pb) == 1
        lab, conf, bid = n_pc_pb[0]
        assert lab == "L" and conf == 0.8 and bid == "b1"

    def test_repoint_descriptor_emitted_before_transfer(self, sdk, tmp_path):
        import json
        log_path = str(tmp_path / "events.jsonl")
        db = os.path.join(tempfile.mkdtemp(prefix="s8_"), "test.db")
        s2 = TortoiseSDK(db, event_log_path=log_path)
        pa, pb = _two_points(s2)
        pc = s2.create_point("statement", "C")["id"]
        s2.create_direct_edge("IMPL", pa, pb, batch_id="b1")
        s2.supersede_point(pa, pc)
        lines = [json.loads(l) for l in open(log_path)  # noqa: E741, SIM115
                 if "DirectEdgeRepoint" in l]
        assert len(lines) == 1
        # Flat descriptor shape (matches DirectEdgeCreated).
        assert lines[0]["tgt"] == pb
        assert lines[0].get("batch_id") == "b1"
        s2.close()


class TestSupersedeOutPassTopology:
    def test_out_edge_to_successor_deleted_no_self_edge(self, sdk):
        # REVIEW-FIX P1 (cycle-26): (old)->(successor) out-edge must be
        # DELETED (repointing would create a self-edge), never repointed.
        pa, pb = _two_points(sdk)
        sdk.create_direct_edge("IMPL", pa, pb)  # pa->pb where pb is the successor
        sdk.supersede_point(pa, pb)
        proj = sdk._get_proj()
        self_edges = proj.g.query(
            "MATCH (a)-[r:IMPL|NAND]->(b) WHERE a.id = b.id RETURN count(r)"
        ).result_set[0][0]
        assert self_edges == 0
        # pa has zero incident direct edges (superseded)
        out = proj.g.query(
            "MATCH (a:Point {id:$id})-[r:IMPL|NAND]->() RETURN count(r)",
            params={"id": pa}).result_set[0][0]
        inn = proj.g.query(
            "MATCH ()-[r:IMPL|NAND]->(a:Point {id:$id}) RETURN count(r)",
            params={"id": pa}).result_set[0][0]
        assert out == 0 and inn == 0
