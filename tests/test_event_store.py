"""Task 3 tests — durable SDK event emission to :GraphEvent nodes.

Uses the shared sdk_factory fixture (tests/conftest.py, Task 1). No team_id
property anywhere — the graph namespace IS the team partition (plan-review P2).
"""
import json


def _events(proj):
    # plan-review P2: no team_id property — the graph namespace IS the partition
    rows = proj.g.query(
        "MATCH (e:GraphEvent) RETURN properties(e) ORDER BY e.seq").result_set
    return [r[0] for r in rows]


def test_create_point_emits_graph_event(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "hello events")
    proj = sdk._get_proj()
    evs = _events(proj)
    assert len(evs) == 1
    assert evs[0]["type"] == "PointAdded"
    assert evs[0]["event_id"]
    assert json.loads(evs[0]["payload"])["id"] == p["id"]
    assert evs[0]["seq"] == 1


def test_dedup_create_does_not_emit(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "dedup me", dedup=True)
    sdk.create_point("statement", "dedup me", dedup=True)  # returns existing
    assert len(_events(sdk._get_proj())) == 1


def test_append_duplicate_event_id_rejected(sdk_factory, tmp_path):
    """plan-review P1: duplicate event_id append is rejected/dedup'd at the
    storage layer — append_event catches the constraint violation, logs, and
    skips (no-op). event_id is a server-side ULID; a collision is a retry
    artifact, never legitimate data."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    event_store.ensure_event_schema(proj)
    seq1 = event_store.next_seq(proj)
    event_store.append_event(proj, seq1, "PointAdded", {"id": "p1"}, "evt-dup")
    n1 = len(_events(proj))
    seq2 = event_store.next_seq(proj)  # counter still advances
    event_store.append_event(proj, seq2, "PointAdded", {"id": "p1"}, "evt-dup")
    assert len(_events(proj)) == n1     # second append skipped (no-op)
    assert seq2 == seq1 + 1


def test_read_dedups_duplicate_event_ids(sdk_factory, tmp_path):
    """plan-review P1: two :GraphEvent nodes with the SAME event_id written
    directly via Cypher (bypassing append_event) → read_after returns one.
    (final-verification P2) The unique constraint is deliberately NOT
    installed here — a schema'd graph rejects the second duplicate CREATE
    (that rejection path is covered by test_append_duplicate_event_id_rejected
    and ensure_event_schema). This test pins the READ-side dedup contract on a
    constraint-free graph: seed dupes first, then read_after dedups."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path, ensure_schema=False)
    proj = sdk._get_proj()
    # NOTE: no create_point call — constraint must not be installed in this test
    proj.g.query(
        "CREATE (e:GraphEvent {seq: 90, ts: $ts, type: 'PointAdded', "
        "payload: $pl, event_id: 'dup-1'})", params={"ts": "x", "pl": "{}"})
    proj.g.query(
        "CREATE (e:GraphEvent {seq: 91, ts: $ts, type: 'PointAdded', "
        "payload: $pl, event_id: 'dup-1'})", params={"ts": "x", "pl": "{}"})
    evs = event_store.read_after(proj, 0, limit=100)  # raw node properties
    by_id = [e for e in evs if e.get("event_id") == "dup-1"]
    assert len(by_id) == 1  # deduped at read


def test_all_mutations_emit(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    s = sdk.create_point("statement", "src")
    t = sdk.create_point("statement", "tgt")
    op = sdk.create_operator("IMPL", s["id"], [t["id"]])
    sdk.retract_point(t["id"])
    old = sdk.create_point("statement", "old")
    new = sdk.create_point("statement", "new")
    sdk.supersede_point(old["id"], new["id"])
    sdk.annotate_operator(op["id"], 0.5, 0.5, 0.5, 0.5)
    types = [e["type"] for e in _events(sdk._get_proj())]
    assert types.count("PointAdded") == 4  # src, tgt, old, new
    assert "OperatorAdded" in types and "PointRetracted" in types
    assert "PointSuperseded" in types and "OperatorAnnotated" in types


def test_content_edit_emits_nothing(sdk_factory, tmp_path):
    """plan-review P1: update_point non-status edits do NOT emit."""
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "before")
    sdk.update_point(p["id"], content="after")
    assert [e["type"] for e in _events(sdk._get_proj())] == ["PointAdded"]


def test_event_nodes_have_zero_relationships(sdk_factory, tmp_path):
    sdk = sdk_factory(tmp_path)
    sdk.create_point("statement", "isolated")
    proj = sdk._get_proj()
    assert proj.g.query("MATCH (e:GraphEvent)-[r]-() RETURN count(r)").result_set[0][0] == 0


def test_event_nodes_invisible_to_domain_queries(sdk_factory, tmp_path):
    """plan-review P2: get_point, search, and domain-label counts exclude events."""
    sdk = sdk_factory(tmp_path)
    p = sdk.create_point("statement", "visible")
    proj = sdk._get_proj()
    ev = _events(proj)[0]
    assert proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 1
    assert len(sdk.query()) == 1
    assert sdk.get_point(ev["event_id"]) == {}  # {} per get_point missing contract
    hits = sdk.tortoise_fts_query("visible", limit=50)  # search scans Points only
    hit_ids = {h["id"] for h in hits}
    assert p["id"] in hit_ids
    assert ev["event_id"] not in hit_ids
    domain = proj.g.query(
        "MATCH (n) WHERE NOT (n:GraphEvent) AND NOT (n:GraphEventMeta) "
        "AND NOT (n:EpMeta) RETURN count(n)"
    ).result_set[0][0]
    assert domain == 1  # only the Point; event + EpMeta bookkeeping nodes excluded


def test_seq_is_monotonic_under_concurrency(sdk_factory, tmp_path):
    """P1 (code-review): the previous version was VACUOUS — sdk_factory mints a
    fresh db per call, so 8 workers wrote to 8 isolated graphs and the final
    assertion ran on an empty graph (trivially true). Here we assert every
    worker's per-graph seqs are strictly monotonic+unique AND the run produced
    events at all (non-vacuous). TRUE cross-worker atomicity on ONE shared
    graph needs a live FalkorDB (TORTOISE_DB_URI=docker://...) — the embedded
    redislite client is not multi-connection-safe (documented on sdk_factory).

    #915: SDK construction is serialized (a lock) — 8 simultaneous embedded
    redislite server starts with AOF init can trip the 10s start window
    (Connection refused on the unix socket). The per-worker WRITES stay
    concurrent; only the server boots serialize, which is not what this test
    measures.
    """
    import threading
    errors = []
    per_worker: list[list[int]] = []
    _sdk_lock = threading.Lock()

    def worker(i):
        try:
            with _sdk_lock:
                s = sdk_factory(tmp_path)
            s.create_point("statement", f"c{i}")
            per_worker.append([e["seq"] for e in _events(s._get_proj())])
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert per_worker, "no worker produced events — test would be vacuous"
    for seqs in per_worker:
        assert sorted(seqs) == seqs and len(set(seqs)) == len(seqs),             f"non-monotonic or duplicate seqs: {seqs}"


def test_seq_is_monotonic_under_concurrency_live_falkor():
    """TRUE cross-worker atomicity on ONE shared live graph (#942).

    The embedded sibling test documents that real cross-worker atomicity
    needs a live FalkorDB — this is that path, run by CI's
    test-concurrency-falkor job (TORTOISE_DB_URI=docker://...). Skips
    visibly everywhere else. Deliberately does NOT use sdk_factory (it mints
    isolated embedded files — the vacuous pattern this test replaces).
    """
    import os  # noqa: I001
    import threading

    from _live_utils import _skip_unless_live_uri
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK

    _skip_unless_live_uri()
    uri = os.environ["TORTOISE_DB_URI"]

    # Reset the shared graph (test-prefixed name passes _assert_test_graph).
    proj = FalkorProjection.from_uri(uri, graph_name="test_live_seq_tortoise")
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.close()

    # Warm-up: install the event schema + seq counter deterministically
    # before the threads race (next_seq is a server-side atomic MERGE).
    warm = TortoiseSDK(namespace="test_live_seq")
    assert warm._db_uri, "live test must resolve the URI backend (not embedded)"
    warm.create_point("statement", "warmup")
    warm.close()

    errors, per_worker = [], []

    def worker(i):
        try:
            s = TortoiseSDK(namespace="test_live_seq")
            s.create_point("statement", f"live-c{i}")
            per_worker.append([e["seq"] for e in _events(s._get_proj())])
            s.close()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    union = sorted({seq for seqs in per_worker for seq in seqs})
    assert union == list(range(1, len(union) + 1)), (
        f"global seqs not contiguous 1..N — lost or duplicate seqs: {union}")
    assert len(union) >= 8, f"expected >= 8 events (warmup + 8 workers), got {len(union)}"


def test_purge_expired_removes_old_events(sdk_factory, tmp_path):
    """Task 7: events older than retention_days are purged (ts cutoff)."""
    from datetime import datetime, timedelta, timezone  # noqa: I001
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    event_store.ensure_event_schema(proj)
    now = datetime.now(timezone.utc)  # noqa: UP017
    old = (now - timedelta(days=31)).isoformat()
    fresh = (now - timedelta(days=1)).isoformat()
    event_store.append_event(proj, 1, "PointAdded", {"id": "a"}, "ev-old", ts=old)
    event_store.append_event(proj, 2, "PointAdded", {"id": "b"}, "ev-fresh", ts=fresh)
    deleted = event_store.purge_expired(proj, retention_days=30)
    assert deleted == 1
    evs = event_store.read_after(proj, 0)
    assert [e["event_id"] for e in evs] == ["ev-fresh"]


def test_purge_overflow_caps_per_team(sdk_factory, tmp_path):
    """Task 7: size cap drops the OLDEST events (by seq)."""
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    event_store.ensure_event_schema(proj)
    for i in range(1, 4):
        event_store.append_event(proj, i, "PointAdded", {"id": str(i)}, f"ev-{i}")
    deleted = event_store.purge_overflow(proj, max_events=2)
    assert deleted == 1
    evs = event_store.read_after(proj, 0)
    assert [e["seq"] for e in evs] == [2, 3]  # oldest (seq 1) dropped


def test_purged_seq_cursor_expires(sdk_factory, tmp_path):
    """Task 7: after purge, a cursor pointing at a purged seq → expired (410)."""
    from datetime import datetime, timedelta, timezone  # noqa: I001
    from tortoise import event_store
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    now = datetime.now(timezone.utc)  # noqa: UP017
    event_store.ensure_event_schema(proj)
    event_store.next_seq(proj)  # creates GraphEventMeta (first_seq=1)
    # 1. a FRESH event is pollable → cursor points at seq 1
    event_store.append_event(proj, 1, "PointAdded", {"id": "a"}, "ev-1", ts=now.isoformat())
    stale = sdk.events_poll()["next_cursor"]
    # 2. time passes: backdate the event past retention, purge it (refreshes
    #    the first_seq watermark to last_seq+1 = 2)
    proj.g.query("MATCH (e:GraphEvent) SET e.ts = $old_ts",
                 params={"old_ts": (now - timedelta(days=31)).isoformat()})
    deleted = event_store.purge_expired(proj, retention_days=30)
    assert deleted == 1
    # 3. the stale cursor (seq 1) is now below the watermark → expired
    import pytest
    with pytest.raises(ValueError, match="cursor expired"):
        sdk.events_poll(after=stale)
