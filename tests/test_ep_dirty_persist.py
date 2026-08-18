"""#1163 — graph-persisted EP dirty state + ep_version epoch.

Multi-process EP (hosted HTTP local tier, #395 deferral): EP dirty state
must survive process/request boundaries and concurrent runs must not
interleave ep_alpha/msg_alpha writes mid-run (#6761 batch-I/O load→flush
race). The graph (`n.ep_dirty` + `:EpMeta.ep_version`) becomes the
cross-process source of truth; the in-process `_dirty_roots` set stays the
hot-path mirror.

The hosted topology (fresh request-scoped SDK per request, mcp_auth.py:69;
fresh SDK per dream drain, hosted_api.py `_dream_worker`) is simulated
without threads: separate TortoiseSDK instances over ONE embedded DB path =
separate processes over one graph. Sequential operations (the embedded
server serializes writes anyway, #6761); the stale-run guard is exercised
deterministically via the load → concurrent-write → flush ordering.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

# ── Fixtures ─────────────────────────────────────────────────────


@contextmanager
def _db(tmp_prefix: str = "tt_epdirty_"):
    td = tempfile.mkdtemp(prefix=tmp_prefix)
    yield os.path.join(td, "t.db")


def _open(db_path: str) -> TortoiseSDK:
    return TortoiseSDK(db_path)


def _chain(sdk: TortoiseSDK, n: int, prefix: str = "c") -> list[str]:
    """Calibrated chain of n claims linked by n-1 operators (SDK-level
    writes → _mark_dirty runs and the ep_dirty flags + epoch persist)."""
    ids = []
    for i in range(n):
        pid = sdk.create_point("statement", f"{prefix}{i}",
                               dedup=False, status="live")["id"]
        sdk.set_point_baseline(pid, 1, 1)
        ids.append(pid)
    for i in range(n - 1):
        sdk.create_operator("IMPL", ids[i], [ids[i + 1]],
                            direction="bidirectional")
    return ids


def _ep_dirty_ids(sdk: TortoiseSDK) -> set[str]:
    return {r[0] for r in sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.ep_dirty = true RETURN n.id").result_set}


def _ep_dirty_at(sdk: TortoiseSDK, cid: str):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.ep_dirty_at",
        params={"id": cid}).result_set
    return rows[0][0] if rows else None


def _build_deterministic(proj, points: dict[str, tuple[float, float]],
                         ops: list[tuple[str, str, str, str]]) -> None:
    """Fixed-id chain via raw Cypher (NO auto _mark_dirty — the test drives
    the epoch/flag lifecycle explicitly)."""
    g = proj.g
    rows = [{"id": pid, "a": a, "b": b} for pid, (a, b) in points.items()]
    g.query(
        "UNWIND $rows AS r "
        "CREATE (n:Point {id: r.id, is_operator: false, status: 'live', "
        "                 ep_alpha: r.a, ep_beta: r.b})",
        params={"rows": rows},
    )
    op_rows = [{"id": oid, "op": ot} for oid, ot, _s, _t in ops]
    g.query(
        "UNWIND $rows AS r "
        "CREATE (o:Point {id: r.id, op_type: r.op, is_operator: true, "
        "                 direction: 'bidirectional', status: 'live'})",
        params={"rows": op_rows},
    )
    edge_rows = []
    for oid, _ot, src, tgt in ops:
        edge_rows.append({"op": oid, "s": src, "t": tgt, "i": 0, "j": 1})
    g.query(
        "UNWIND $rows AS r "
        "MATCH (o:Point {id: r.op}), (s:Point {id: r.s}), (t:Point {id: r.t}) "
        "CREATE (o)-[:IMPL {idx: r.i}]->(s), (o)-[:IMPL {idx: r.j}]->(t)",
        params={"rows": edge_rows},
    )


# ── Persistence of dirty state ───────────────────────────────────


def test_mark_dirty_persists_flags_and_epoch():
    """A write path marks the graph dirty: ep_dirty flags + ep_dirty_at
    stamped on the mutated claims AND the reverse-BFS affected claims."""
    with _db() as db:
        sdk = _open(db)
        try:
            ids = _chain(sdk, 2)
            assert sdk._dirty_roots, "in-memory mirror must be populated"
            epoch = sdk._ep_epoch()
            assert epoch >= 1, "ep_version epoch advances on writes"
            flagged = _ep_dirty_ids(sdk)
            assert {ids[0], ids[1]} <= flagged, \
                f"both chain claims flagged, got {flagged}"
            assert _ep_dirty_at(sdk, ids[0]) == epoch
        finally:
            sdk.close()


def test_ep_version_epoch_increments_per_write():
    """Every write that dirties EP advances the ep_version epoch."""
    with _db() as db:
        sdk = _open(db)
        try:
            assert sdk._ep_epoch() == 0
            sdk.create_point("statement", "a1", dedup=False)
            e1 = sdk._ep_epoch()
            assert e1 >= 1
            sdk.create_point("statement", "a2", dedup=False)
            e2 = sdk._ep_epoch()
            assert e2 > e1, "epoch must be monotonic across writes"
        finally:
            sdk.close()


def test_fresh_sdk_sees_persisted_dirty_state():
    """Request boundary (SDK A closed) → fresh SDK B hydrates the persisted
    dirty state and the no-arg local-EP path works (not no_dirty_roots)."""
    with _db() as db:
        a = _open(db)
        try:
            ids = _chain(a, 3)
            assert a._dirty_roots
        finally:
            a.close()  # = end of request A
        b = _open(db)
        try:
            assert not b._dirty_roots, "fresh SDK starts with no in-memory state"
            hydrated = b._hydrate_dirty_roots()
            assert hydrated, "persisted dirty roots must hydrate into a fresh SDK"
            assert ids[0] in hydrated and ids[1] in hydrated
            # no-arg local EP over the persisted dirty subgraph (#395 AC8)
            result = b.compute_confidence()
            assert result.get("diagnostic") != "no_dirty_roots", result
            assert result.get("confidences"), \
                "local EP must produce confidences over the persisted zone"
        finally:
            b.close()


def test_converged_dream_sweeps_graph_flags():
    """A converged local dream sweeps BOTH the in-memory roots and the
    graph flags (the cross-process source of truth stays clean)."""
    with _db() as db:
        sdk = _open(db)
        try:
            _chain(sdk, 3)
            assert _ep_dirty_ids(sdk)
            result = sdk.dream(mode="local")
            assert result["converged"]
            assert not _ep_dirty_ids(sdk), \
                "converged pass must sweep graph ep_dirty flags"
            assert not sdk._dirty_roots
        finally:
            sdk.close()


# ── ep_version guard: stale run cannot clobber newer writes ──────


def test_stale_run_flush_guard_rejects_interleaved_writes():
    """Batch-I/O load→flush race (#6761): process B loads the run state at
    epoch e1; process A writes NEW values + marks dirty (epoch e2); B's
    stale flush must be rejected so A's writes survive."""
    from tortoise.ep import TortoiseEP
    with _db() as db:
        a = _open(db)
        b = _open(db)
        try:
            _build_deterministic(a._get_proj(),
                                 {"c0": (2.0, 1.0), "c1": (1.0, 1.0)},
                                 [("op01", "IMPL", "c0", "c1")])
            a._mark_dirty(["c1"])  # epoch → e1
            e1 = a._ep_epoch()
            # B loads the run state at e1 (the "load" half of batch I/O).
            ep_b = TortoiseEP(b._get_proj())
            ep_b._load_cache({"c0", "c1"})
            assert ep_b._run_ep_version == e1
            # A (another process) writes NEW values + marks dirty → e2.
            a._get_proj().g.query(
                "MATCH (n:Point {id:'c0'}) "
                "SET n.ep_alpha = 9.0, n.ep_beta = 1.0")
            a._mark_dirty(["c0"])
            e2 = a._ep_epoch()
            assert e2 > e1
            # B's stale flush is rejected (guard fired).
            flushed = ep_b._flush_cache()
            assert flushed is False
            assert ep_b._flush_skipped is True
            rows = a._get_proj().g.query(
                "MATCH (n:Point {id:'c0'}) RETURN n.ep_alpha").result_set
            assert float(rows[0][0]) == 9.0, \
                "stale flush must not clobber the newer process's write"
            # A fresh load at the current epoch flushes normally.
            ep_b2 = TortoiseEP(b._get_proj())
            ep_b2._load_cache({"c0", "c1"})
            assert ep_b2._run_ep_version == e2
            assert ep_b2._flush_cache() is True
            assert ep_b2._flush_skipped is False
        finally:
            a.close()
            b.close()


def test_sweep_preserves_newer_dirty_marking():
    """The dreamer's flag sweep is epoch-guarded: a run that saw an older
    epoch never clears a marking stamped by a newer write."""
    with _db() as db:
        sdk = _open(db)
        try:
            ids = _chain(sdk, 2)
            sdk.dream(mode="local")  # converged → flags swept
            assert not _ep_dirty_ids(sdk)
            sdk._mark_dirty([ids[1]])  # newer marking at the current epoch
            e = sdk._ep_epoch()
            sdk._sweep_dirty_roots({ids[1]}, run_ep=e - 1)
            assert _ep_dirty_at(sdk, ids[1]) == e, \
                "a stale sweep must not clear a newer marking"
            sdk._sweep_dirty_roots({ids[1]}, run_ep=e)
            assert _ep_dirty_at(sdk, ids[1]) is None
        finally:
            sdk.close()


# ── HTTP no-arg gate (mcp_server) ────────────────────────────────


def test_http_noarg_gate_allows_persisted_dirty_state(monkeypatch):
    """Over HTTP the no-arg compute_confidence gate only returns
    no_dirty_state_http when the graph has NO persisted dirty roots — with
    persisted dirty state it falls through to local EP (#1163 acceptance 1).
    """
    from tortoise import mcp_server
    from tortoise.mcp_auth import _transport_mode
    with _db() as db:
        a = _open(db)
        try:
            _chain(a, 2)
        finally:
            a.close()
        sdk = _open(db)
        try:
            assert sdk._hydrate_dirty_roots()
            token = _transport_mode.set("http")
            monkeypatch.setattr(mcp_server, "_get_team_sdk", lambda: sdk)
            try:
                result = mcp_server.tortoise_compute_confidence()
            finally:
                _transport_mode.reset(token)
            assert result.get("diagnostic") != "no_dirty_state_http", result
            assert result.get("confidences"), \
                "HTTP no-arg must run local EP over persisted dirty state"
        finally:
            sdk.close()
