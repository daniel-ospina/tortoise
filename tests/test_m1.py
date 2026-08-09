"""M1 tests — EventAPI, idempotency, and the projection backends.

Runnable without pytest:  .venv/bin/python tests/test_m1.py
(also works under pytest if installed).
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI, provenance          # noqa: E402
from tortoise.idempotency import document_key          # noqa: E402
from tortoise.log import EventLog                       # noqa: E402
from tortoise.projection import (                        # noqa: E402
    FalkorProjection, InMemoryProjection, fold, split,
)


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def _api(projection=None):
    log = EventLog(_tmp("events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test",
                    projection=projection), log


def _build(api, source="doc.txt"):
    """Two statements + one IMPL operator between them."""
    prov = provenance(source, [0, 10], "quote", extracted_by="test@0")
    a = api.add_point("we should raise B slowly", "ctx", prov)
    b = api.add_point("fast raises wreck early buyers", "ctx", prov)
    op = api.add_operator("IMPL", [b, a], "ctx", prov)
    return a, b, op


def test_idempotency_skip():
    api, log = _api()
    text = "hello world"
    r1 = api.begin_ingest("doc.txt", "mock@0", document_key(text))
    assert not r1.skip and r1.run_id
    r2 = api.begin_ingest("doc.txt", "mock@0", document_key(text))
    assert r2.skip and r2.run_id == r1.run_id
    starts = [e for e in log.read_all() if e["type"] == "IngestStarted"]
    assert len(starts) == 1, "duplicate ingest must not append a second run"
    print("PASS test_idempotency_skip")


def test_reprocess_new_version_supersedes():
    api, log = _api()
    text = "hello world"
    api.begin_ingest("doc.txt", "mock@0", document_key(text))
    old = api.add_point("old extraction", "ctx",
                        provenance("doc.txt", [0, 3], "hel"))
    # same content, NEW extractor version → supersede the old run
    r = api.begin_ingest("doc.txt", "mock@1", document_key(text))
    assert not r.skip
    new = api.add_point("new extraction", "ctx",
                        provenance("doc.txt", [0, 3], "hel"))
    points = fold(log.read_all())
    # #432 Task 2: superseded points are kept as retracted tombstones
    assert points[old]["status"] == "retracted", \
        "superseded run's points must be tombstone-retracted"
    assert new in points
    print("PASS test_reprocess_new_version_supersedes")


def test_incremental_matches_batch():
    proj = InMemoryProjection()
    api, log = _api(projection=proj)
    _build(api)
    # revise + retract exercise more event types
    a, b, op = _build(api, source="doc2.txt")
    api.revise_point(a, new_content="edited", corrects=op)
    api.retract_point(b, corrects=op)
    assert proj.points == fold(log.read_all()), "incremental fold must equal batch fold"
    print("PASS test_incremental_matches_batch")


def test_full_mutation_set():
    api, log = _api()
    a, b, op = _build(api)
    api.revise_point(a, new_content="edited", new_context=None, corrects=op)
    api.merge_points(keep_id=a, merge_ids=[b], corrects=None)
    points = fold(log.read_all())
    assert points[a]["content"] == "edited"
    assert b not in points, "merged-away point should be gone"
    assert op in points
    print("PASS test_full_mutation_set")


def test_falkor_roundtrip():
    api, log = _api()
    a, b, op = _build(api)
    proj = FalkorProjection(_tmp("g.db"), graph_name="t")
    try:
        proj.rebuild(log)
        n = proj.query("MATCH (p:Point) RETURN count(p) AS n").result_set[0][0]
        assert n == len(fold(log.read_all())), f"node count {n} != fold"
        edges = proj.query(
            "MATCH (o:Point {op_type:'IMPL'})-[:IMPL]->(s:Point) RETURN o.id, s.id"
        ).result_set
        assert len(edges) == 2, f"expected 2 IMPL edges, got {len(edges)}"
        assert all(e[0] == op for e in edges)
        print("PASS test_falkor_roundtrip")
    finally:
        proj.close()


def test_falkor_matches_inmemory():
    proj_mem = InMemoryProjection()
    api, log = _api(projection=proj_mem)
    _build(api)
    proj_f = FalkorProjection(_tmp("g2.db"), graph_name="t")
    try:
        proj_f.rebuild(log)
        n = proj_f.query("MATCH (p:Point) RETURN count(p) AS n").result_set[0][0]
        stmts, ops = split(proj_mem.points)
        assert n == len(stmts) + len(ops)
        print("PASS test_falkor_matches_inmemory")
    finally:
        proj_f.close()


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall M1 tests passed")


if __name__ == "__main__":
    _run_all()
