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

#6211 (mode 2) — THE TARGETED PASS CLEARS ITS OWN ROOTS. The clearing code
(`_sweep_dirty_roots`) was reachable only from the three dreaming adapters, so
the call the graph instruction points a writer at — `compute_confidence(
factors=…)` / `compute_confidence(anchors=…)` — wrote posteriors and left the
writer's own `ep_dirty` flags set forever. The last section below pins that
behaviour and every gate that guards it (converged / not truncated / ran no
factor / flush not rejected / epoch snapshot present / epoch guard / unreached
roots retained), plus the request-scoped shape, the best-effort failure path and
the rule that the no-arg branch does NOT use this clear.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: F401

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


def test_mitigation_write_leaves_no_permanent_dirty_root():
    """#7505: a mitigation write must not leave an UNSWEEPABLE dirty root.

    `mitigate_operator` marks the mitigation point (and, before the fix, the
    operator it damps). `_sweep_dirty_roots` has ONE clearing rule — subtract
    the run's `affected` set — and #6599 correctly keeps a mitigation OUT of
    that set: its only edge is `(m)-[:IMPL]->(op)`, a reverse-only IMPL that
    forms no factor (#5566 neutral-link rule). So neither id was ever
    cleared: `_dirty_roots` grew one permanent entry per mitigation,
    `dream_health_check()`'s `stale_backlog` never drained, every
    mitigation pinned `_auto_dream_mode()` to 'local', and the mitigation's
    persisted `ep_dirty` survived every pass.

    Red on origin/main and green with the fix; the confidence delta is
    asserted too, so the fix cannot "clear the flag" by dropping the
    propagation the write exists for.
    """
    with _db() as db:
        sdk = _open(db)
        try:
            src = sdk.create_point("statement", "src claim", dedup=False,
                                   status="live")["id"]
            clm = sdk.create_point("statement", "clm claim", dedup=False,
                                   status="live")["id"]
            sdk.set_point_baseline(src, 3.0, 1.0)
            sdk.set_point_baseline(clm, 1.0, 1.0)
            op = sdk.create_operator("IMPL", src, [clm])["id"]
            assert sdk.compute_confidence()["converged"]
            conf_before = sdk.get_confidence(clm)["mean"]
            assert not _ep_dirty_ids(sdk), "baseline converged state is clean"

            mid = sdk.mitigate_operator(
                op, "weaker than it looks", strength=0.5)["id"]
            # The mitigation is a factor participant's NEIGHBOUR, so the write
            # legitimately dirties the operator's inputs — that is the
            # propagation. What must NOT survive is a flag nobody can clear.
            assert mid in _ep_dirty_ids(sdk), "the write marks the mitigation"

            assert sdk.compute_confidence()["converged"]

            # No belief corruption: the strength still reaches EP's factor.
            assert sdk.get_confidence(clm)["mean"] < conf_before, (
                "the mitigation must still dampen the operator's factor")

            assert mid not in _ep_dirty_ids(sdk), (
                "a mitigation must not keep a persisted ep_dirty flag after a "
                "converged pass — no pass can ever clear it (#7505)")
            assert mid not in sdk._dirty_roots
            assert op not in sdk._dirty_roots, (
                "the mitigated operator is not a factor participant: as a "
                "dirty root it can never be swept (#7505)")
            assert sdk.dream_health_check()["stale_backlog"] == 0, (
                "the stale backlog must drain — a permanently non-empty one "
                "is what pins _auto_dream_mode() to 'local'")
            # The observable that was unreachable at head: with the backlog
            # drained, the next no-arg pass has nothing left to do.
            assert sdk.compute_confidence().get(
                "diagnostic") == "no_dirty_roots"
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
            monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: sdk)
            try:
                result = mcp_server.tortoise_compute_confidence()
            finally:
                _transport_mode.reset(token)
            assert result.get("diagnostic") != "no_dirty_state_http", result
            assert result.get("confidences"), \
                "HTTP no-arg must run local EP over persisted dirty state"
        finally:
            sdk.close()


# ── #6211 (mode 2): the TARGETED pass clears its own roots ───────


def _hub(sdk: TortoiseSDK, n_ops: int) -> tuple[list[str], list[str]]:
    """Two live claims wired through ``n_ops`` binary operators (raw Cypher).

    Every operator's inputs are the SAME two claims, so one anchor selects
    every operator in ONE hop — the cheapest shape whose operator selection can
    exceed the shipped 200 cap (which is what the truncation gate needs).
    Returns ``(claim_ids, operator_ids)``.
    """
    proj = sdk._get_proj()
    # Scoped to the ids this helper creates — NOT ``MATCH (n) DETACH DELETE n``,
    # which would also delete the ``:EpMeta`` node and reset the ``ep_version``
    # epoch this file's assertions run against (the wipe was invisible only
    # while every call site ran it on a fresh graph).
    proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids DETACH DELETE n",
        params={"ids": ["c0", "c1"] + [f"op{i}" for i in range(n_ops)]},
    )
    proj.g.query(
        "UNWIND $ids AS id CREATE (n:Point) "
        "SET n.id = id, n.is_operator = false, n.status = 'live', "
        "    n.confidence = 0.5",
        params={"ids": ["c0", "c1"]},
    )
    proj.g.query(
        "UNWIND $ids AS id CREATE (n:Point) "
        "SET n.id = id, n.is_operator = true, n.op_type = 'IMPL', "
        "    n.status = 'live', n.direction = 'bidirectional', "
        "    n.confidence = 0.5",
        params={"ids": [f"op{i}" for i in range(n_ops)]},
    )
    # Scoped to the operators THIS call created — the unscoped
    # `WHERE o.is_operator = true` silently rewired every other operator in the
    # graph to c0/c1, so a second `_hub` on a shared graph changed the operator
    # count a later test keys its truncation precondition on.
    proj.g.query(
        "MATCH (o:Point) WHERE o.id IN $ops "
        "MATCH (a:Point {id:'c0'}), (b:Point {id:'c1'}) "
        "CREATE (o)-[:IMPL {direction: 'bidirectional'}]->(a), "
        "       (o)-[:IMPL {direction: 'bidirectional'}]->(b)",
        params={"ops": [f"op{i}" for i in range(n_ops)]},
    )
    return ["c0", "c1"], [f"op{i}" for i in range(n_ops)]


def test_targeted_pass_clears_the_roots_it_recomputed():
    """#6211 mode 2 — the documented "compute confidence for my claims" call
    clears the writer's own `ep_dirty` flags.

    Red on origin/main before the fix (measured: converged, untruncated, all 4
    claims written back, all 4 still `ep_dirty`; the no-arg pass on the same
    graph cleared them). The flag is the graph's cross-process staleness source
    of truth, so a writer that could only ever SET it left every reader of that
    state — `dream_health_check()`'s `stale_backlog` and `_auto_dream_mode()` —
    reporting a belief it had already recomputed. (`calibration_passed()` is a
    DIFFERENT signal: the Gate B milestone on `:Meta`, not a reader of
    `ep_dirty`.)
    """
    with _db() as db:
        sdk = _open(db)
        try:
            ids = _chain(sdk, 4)
            assert {ids[0], ids[1], ids[2], ids[3]} <= _ep_dirty_ids(sdk), \
                "precondition: the writes marked every claim dirty"
            result = sdk.compute_confidence(anchors=[ids[0]], max_hops=3,
                                            require_calibration=False)
            assert result["converged"] is True, result
            assert result["truncated"] is False, \
                f"precondition: the pass must be untruncated; got {result}"
            assert len(result["confidences"]) == 4, \
                "precondition: the pass recomputed all four claims"
            assert not _ep_dirty_ids(sdk), (
                "a converged, untruncated TARGETED pass must clear the flags it "
                f"recomputed — still dirty: {sorted(_ep_dirty_ids(sdk))}"
            )
            assert not sdk._dirty_roots, \
                "the in-memory mirror must agree with the graph flags"
            assert _ep_dirty_at(sdk, ids[0]) is None, \
                "the ordering stamp must be cleared with the flag"
        finally:
            sdk.close()


def test_targeted_pass_clears_the_factors_path_too():
    """Same rule on the OTHER targeted entry point (`factors=`).

    A caller-supplied factor set runs no BFS selection, so it can never be
    capped — the truncation gate cannot mask the clear there, and both entry
    points of the defect must be closed (they are the two halves of "a writer
    can mark claims dirty and has no API that clears them").
    """
    with _db() as db:
        sdk = _open(db)
        try:
            claims, ops = _hub(sdk, 2)
            sdk._mark_dirty(claims)
            assert set(claims) <= _ep_dirty_ids(sdk)
            result = sdk.compute_confidence(factors=ops[:2],
                                            require_calibration=False)
            assert result["converged"] is True, result
            assert result["truncated"] is False
            assert set(claims) <= set(result["confidences"]), result["confidences"]
            assert not _ep_dirty_ids(sdk), (
                "the explicit-factors path must clear the flags it recomputed — "
                f"still dirty: {sorted(_ep_dirty_ids(sdk))}"
            )
        finally:
            sdk.close()


def test_targeted_pass_clears_only_the_roots_it_reached():
    """W4 retention (#1243) still holds: a root the pass did NOT recompute
    stays dirty, so the retry backlog is not silently dropped.

    Two disjoint claims wired into two disjoint operators. The pass is scoped
    to one of them; the other's belief was never touched, so clearing it would
    claim a recompute that did not happen.
    """
    with _db() as db:
        sdk = _open(db)
        try:
            reached = _chain(sdk, 2, prefix="r")
            untouched = _chain(sdk, 2, prefix="u")
            assert set(reached) | set(untouched) <= _ep_dirty_ids(sdk)
            result = sdk.compute_confidence(anchors=[reached[0]], max_hops=1,
                                            require_calibration=False)
            assert result["converged"] is True, result
            assert result["truncated"] is False
            after = _ep_dirty_ids(sdk)
            assert not (set(reached) & after), (
                "the reached chain was recomputed and must be clean — still "
                f"dirty: {sorted(set(reached) & after)}"
            )
            assert set(untouched) <= after, (
                "a root the pass never reached must stay dirty (W4 retention) — "
                f"cleared anyway: {sorted(set(untouched) - after)}"
            )
        finally:
            sdk.close()


def test_a_truncated_targeted_pass_clears_nothing():
    """The #6211 report IS the clearing gate: a truncated pass covers a
    REDUCED topology, so its `affected` set is incomplete by construction.

    This is why the clearing path was blocked on the reporting half (#9007):
    before `truncated` was in the result, a capped pass and a full one were
    indistinguishable and there was nothing sound to gate on. Driven through
    the shipped 200-operator cap, not by raising it.
    """
    with _db() as db:
        sdk = _open(db)
        try:
            claims, _ops = _hub(sdk, 260)
            sdk._mark_dirty(claims)
            assert set(claims) <= _ep_dirty_ids(sdk)
            result = sdk.compute_confidence(anchors=[claims[0]], max_hops=1,
                                            require_calibration=False)
            assert result["truncated"] is True, (
                "precondition: 260 reachable operators against the shipped "
                f"200 cap must report truncation; got {sorted(result)}"
            )
            assert _ep_dirty_ids(sdk) == set(claims), (
                "a truncated pass must clear NOTHING — its affected set is "
                f"incomplete; cleared: "
                f"{sorted(set(claims) - _ep_dirty_ids(sdk))}"
            )
        finally:
            sdk.close()


def test_a_concurrent_marking_mid_pass_survives_the_targeted_pass(monkeypatch):
    """#1163's failure mode, end to end: a marking made AFTER the run took its
    epoch snapshot must survive the run.

    The ordering is forced deterministically — load → concurrent write → flush
    — which is the same shape `test_stale_run_flush_guard_rejects_interleaved_writes`
    uses. The stale-run guard rejects the flush, so nothing was recomputed and
    the sweep must stand down entirely.
    """
    from tortoise.ep import TortoiseEP
    with _db() as db:
        sdk = _open(db)
        try:
            ids = _chain(sdk, 3)
            assert set(ids) <= _ep_dirty_ids(sdk)
            original = TortoiseEP._load_cache

            def _load_then_concurrent_write(self, affected_claims):
                original(self, affected_claims)
                # Another process writes and marks dirty after this run's
                # snapshot: the epoch advances past `_run_ep_version`.
                sdk._mark_dirty([ids[2]])

            monkeypatch.setattr(TortoiseEP, "_load_cache",
                                _load_then_concurrent_write)
            result = sdk.compute_confidence(anchors=[ids[0]], max_hops=3,
                                            require_calibration=False)
            assert result["converged"] is True, result
            assert set(ids) <= _ep_dirty_ids(sdk), (
                "a run whose posteriors were dropped by the stale-run guard "
                "recomputed nothing and must clear nothing — cleared: "
                f"{sorted(set(ids) - _ep_dirty_ids(sdk))}"
            )
        finally:
            sdk.close()


def test_the_clear_gates_are_fail_closed(monkeypatch):
    """Each gate on its own refuses the clear — pinned as a matrix so a later
    edit cannot quietly drop one (a dropped gate is a false PASS: the flag goes
    clean while the belief it protects was never recomputed).

    Driven on `_clear_targeted_pass_dirty` directly, because the failure modes
    it guards (a non-converged pass, a missing epoch snapshot) cannot be
    produced through the public call without either a synthetic graph EP cannot
    converge on or a fabricated epoch — and a fabricated epoch is exactly what
    the guard exists to refuse.
    """
    from types import SimpleNamespace
    with _db() as db:
        sdk = _open(db)
        try:
            ids = _chain(sdk, 2)

            # A RECORDER in front of the sweep: a refusing gate must not even
            # CALL it. The graph flag alone cannot tell "the gate refused" from
            # "the sweep ran and the graph guard matched nothing" — and the
            # mirror assertion below is blind to a gate whose removal only lets
            # an empty `affected` reach the sweep (nothing is subtracted then).
            calls: list = []
            real_sweep = TortoiseSDK._sweep_dirty_roots

            def _recording_sweep(self, affected, run_ep=None):
                calls.append((sorted(affected), run_ep))
                return real_sweep(self, affected, run_ep=run_ep)

            monkeypatch.setattr(TortoiseSDK, "_sweep_dirty_roots",
                                _recording_sweep)

            def _attempt(label: str, result: dict, *, reaches_sweep: bool = False,
                         **attrs) -> None:
                assert ids[0] in _ep_dirty_ids(sdk), \
                    f"precondition for {label}: the graph root is dirty"
                assert ids[0] in sdk._dirty_roots, \
                    f"precondition for {label}: the mirror root is dirty"
                ep = SimpleNamespace(
                    _last_affected=attrs.pop("affected", {ids[0]}),
                    _run_ep_version=attrs.pop("run_ep", sdk._ep_epoch()),
                )
                # The attribute is OMITTED (not defaulted) when a row asks for
                # it — `getattr(ep, "_flush_skipped", True)` must refuse on a
                # missing datum, and a row that always supplies it cannot see
                # a one-token `..., False` edit open the gate.
                if not attrs.pop("omit_flush_attr", False):
                    ep._flush_skipped = attrs.pop("flush_skipped", False)
                before = len(calls)
                sdk._clear_targeted_pass_dirty(ep, result)
                expected = before + (1 if reaches_sweep else 0)
                assert len(calls) == expected, (
                    f"gate {label!r} must refuse BEFORE the sweep — it was "
                    f"called {len(calls) - before} time(s); calls so far: {calls}"
                )
                assert ids[0] in _ep_dirty_ids(sdk), (
                    f"gate {label!r} must refuse the clear — the graph flag was "
                    "cleared anyway"
                )
                # Every gate but the epoch guard refuses BEFORE the sweep, so
                # the in-memory mirror must still hold the root — an assertion
                # on the graph flag alone cannot tell "the gate refused" from
                # "the sweep ran and the graph guard happened to match nothing"
                # (which is exactly why the `run_ep=-1` row was non-
                # discriminating before this assertion existed).
                if not reaches_sweep:
                    assert ids[0] in sdk._dirty_roots, (
                        f"gate {label!r} must refuse the clear BEFORE the "
                        "sweep — the in-memory root was dropped anyway"
                    )

            # Each refusing row isolates ONE gate: `iterations` is present and
            # truthy everywhere below, so no row can pass because a DIFFERENT
            # gate happened to refuse for it.
            _attempt("non-converged",
                     {"converged": False, "truncated": False, "iterations": 1})
            # Same fail-closed rule on the sibling gate: an ABSENT `converged`
            # must refuse, not read as "not False".
            _attempt("missing-converged-key",
                     {"truncated": False, "iterations": 1})
            _attempt("truncated",
                     {"converged": True, "truncated": True, "iterations": 1})
            _attempt("ran-no-factor",
                     {"converged": True, "truncated": False, "iterations": 0})
            # Fail-closed on the missing datum too, like its siblings above.
            _attempt("missing-iterations-key",
                     {"converged": True, "truncated": False})
            # …and on a missing `_flush_skipped` ATTRIBUTE (never defaulted by
            # any production path, but the default is what makes the arm
            # fail-closed).
            _attempt("flush-attr-absent",
                     {"converged": True, "truncated": False, "iterations": 1},
                     omit_flush_attr=True)
            # An ABSENT `truncated` must read as truncated (fail-closed), not as
            # "no truncation": that is the direction that retires a flag on a
            # reduced topology. The sole production call site always supplies
            # the key (via `_report`), so this arm is what keeps a one-token
            # `get("truncated", False)` edit from silently opening the gate.
            _attempt("missing-truncated-key",
                     {"converged": True, "iterations": 1})
            # The second conjunct of the "ran no factor" gate. Belt-and-braces
            # today (a zero-iteration run is already refused above), but it is
            # NOT inert: with it gone an empty `affected` would reach
            # `_sweep_dirty_roots(set(), run_ep)`, whose
            # `_unsweepable_dirty_roots` candidates are `mirror - set()` — it
            # could retire neutral-link roots nothing recomputed.
            _attempt("empty-affected",
                     {"converged": True, "truncated": False, "iterations": 1},
                     affected=set())
            _attempt("flush-skipped",
                     {"converged": True, "truncated": False, "iterations": 1},
                     flush_skipped=True)
            _attempt("no-epoch-snapshot",
                     {"converged": True, "truncated": False, "iterations": 1},
                     run_ep=-1)
            # The `isinstance` half: a non-int snapshot is not an epoch either,
            # and must refuse rather than reach `ep_dirty_at <= None`.
            _attempt("non-int-epoch",
                     {"converged": True, "truncated": False, "iterations": 1},
                     run_ep=None)
            # The epoch guard is the ONE row that reaches the sweep: it is
            # per-node (`ep_dirty_at <= run_ep`), and `_sweep_dirty_roots`
            # subtracts the whole run set from the in-flight mirror BEFORE the
            # graph clear. So the graph flag survives (asserted above) while the
            # mirror entry is dropped — pre-existing behaviour of the shared
            # sweep, inherited here, and recorded as its own finding rather than
            # silently asserted away.
            _attempt("older-than-the-marking",
                     {"converged": True, "truncated": False, "iterations": 1},
                     reaches_sweep=True, run_ep=sdk._ep_epoch() - 1)

            # …and the same call with every gate satisfied DOES clear, so the
            # matrix above cannot pass by the method being inert. Driven on the
            # SECOND root: the `older-than-the-marking` row above reaches the
            # sweep, which drops the whole run set from the mirror before its
            # per-node graph guard refuses — so `ids[0]`'s mirror entry is
            # already gone and asserting on it here would pass on residue.
            sdk._mark_dirty([ids[1]])
            assert ids[1] in _ep_dirty_ids(sdk)
            assert ids[1] in sdk._dirty_roots
            ep = SimpleNamespace(_last_affected={ids[1]},
                                 _run_ep_version=sdk._ep_epoch(),
                                 _flush_skipped=False)
            sdk._clear_targeted_pass_dirty(
                ep, {"converged": True, "truncated": False, "iterations": 1})
            assert ids[1] not in _ep_dirty_ids(sdk), \
                "with every gate satisfied the root must be cleared"
            assert ids[1] not in sdk._dirty_roots, \
                "with every gate satisfied the mirror must be cleared too"
            assert len(calls) == 2, (
                "two rows reach the sweep (the epoch-guard row and the "
                f"satisfying call); recorder saw {calls}"
            )
        finally:
            sdk.close()


def test_a_converged_pass_that_ran_no_factor_clears_nothing():
    """The #3139 zero-work gate, driven through the REAL call path.

    A targeted pass whose every factor is dropped as degenerate (here: the only
    operator's second input is a `draft` point, #780/#2422) returns
    `{converged: True, iterations: 0, diagnostic: factors_skipped}` with a
    NON-empty `_last_affected` — but `TortoiseEP.run` returns BEFORE
    `_load_cache`, so it recomputed nothing.

    This is the defect a fresh-context review of the first revision found: the
    run-entry reset block cleared `_last_affected` / `_last_truncated` /
    `_last_skipped_factors` / `_flush_skipped` but NOT `_run_ep_version`, so this
    pass inherited the PREVIOUS pass's epoch snapshot and every gate in
    `_clear_targeted_pass_dirty` passed. Measured then: a healthy pass took
    snapshot 8, the degenerate pass then cleared the untouched claim's
    `ep_dirty` at epoch 8 — the "#3139 no-op that sweeps its own backlog" false
    PASS, and unrecoverable, because nothing retries a claim that is not marked
    stale.

    What this test discriminates: the run-entry reset. VERIFIED by reverting
    `self._run_ep_version = -1` alone — the `assert ep._run_ep_version == -1`
    assertion below reds. It
    does NOT discriminate the `iterations` arm: with the reset in place that arm
    is unreachable on this path, so it is pinned by the `ran-no-factor` row of
    `test_the_clear_gates_are_fail_closed` instead. The observable flag is
    protected by either arm — measured on the pre-review revision (both absent)
    the flag WAS cleared, which is the regression this test exists to catch.
    """
    with _db() as db:
        sdk = _open(db)
        try:
            healthy = _chain(sdk, 2, prefix="r")
            untouched = sdk.create_point("statement", "u0", dedup=False,
                                          status="live")["id"]
            draft = sdk.create_point("statement", "d0", dedup=False,
                                     status="draft")["id"]
            degenerate = sdk.create_operator("IMPL", untouched, [draft],
                                             direction="bidirectional")["id"]
            # A healthy targeted pass FIRST — it is what leaves a stale epoch
            # snapshot on the cached EP instance (`_get_ep()` is per-SDK).
            warm = sdk.compute_confidence(anchors=[healthy[0]], max_hops=1,
                                          require_calibration=False)
            assert warm["converged"] is True, warm
            ep = sdk._get_ep()
            snapshot = ep._run_ep_version
            assert snapshot >= 0, \
                "precondition: the healthy pass took an epoch snapshot"
            assert untouched in _ep_dirty_ids(sdk), \
                "precondition: the draft-gated claim is still stale"

            result = sdk.compute_confidence(factors=[degenerate],
                                            require_calibration=False)
            assert result["converged"] is True, result
            assert result["iterations"] == 0, (
                "precondition: every factor was dropped, so nothing ran; got "
                f"{result['iterations']} iteration(s)"
            )
            assert result["diagnostic"] == "factors_skipped", result
            assert ep._run_ep_version == -1, (
                "a run that returned before `_load_cache` took no epoch "
                f"snapshot, so `_run_ep_version` must be -1; it is {ep._run_ep_version} "
                f"(the previous pass saw {snapshot})"
            )
            assert untouched in _ep_dirty_ids(sdk), (
                "a converged pass that ran NO factor recomputed nothing and "
                "must clear nothing — the untouched claim's flag was cleared"
            )
        finally:
            sdk.close()


def test_a_fresh_sdk_targeted_pass_clears_persisted_flags():
    """The clear must not depend on the WRITING process's dirty mirror.

    The hosted shape is a fresh request-scoped SDK per request (`mcp_auth.py`),
    whose `_dirty_roots` starts EMPTY while the graph carries the flags — the
    shape the issue is actually about. The targeted clear is driven by the
    pass's own `affected` set, so it works there; this pins it, because a
    refactor that keyed the clear on the mirror (an `if not self._dirty_roots:
    return` guard, or `affected &= self._dirty_roots`) would silently break
    every hosted caller while every other test in this file — all of which write
    and clear in ONE long-lived SDK — still passed.
    """
    with _db() as db:
        writer = _open(db)
        try:
            ids = _chain(writer, 3)
            assert set(ids) <= _ep_dirty_ids(writer)
        finally:
            writer.close()  # = end of the writing request
        sdk = _open(db)
        try:
            assert not sdk._dirty_roots, \
                "precondition: a fresh SDK starts with an EMPTY mirror"
            result = sdk.compute_confidence(anchors=[ids[0]], max_hops=3,
                                            require_calibration=False)
            assert result["converged"] is True, result
            assert result["truncated"] is False
            assert set(ids) <= set(result["confidences"]), result["confidences"]
            assert not _ep_dirty_ids(sdk), (
                "a request-scoped SDK must clear the flags its pass recomputed — "
                f"still dirty: {sorted(_ep_dirty_ids(sdk))}"
            )
        finally:
            sdk.close()


def test_a_failed_sweep_does_not_fail_the_pass(monkeypatch):
    """The clear is BEST-EFFORT: by the time it runs, the posteriors, the
    `n.confidence` write-back and the `ConfidenceChanged` journal entries have
    already landed, so a graph hiccup on the sweep must not turn committed work
    into a raised pass.

    A failed clear must also leave the flags SET (the safe, W4 direction) AND
    restore the in-memory mirror EXACTLY. `_sweep_dirty_roots` subtracts
    `affected | _unsweepable_dirty_roots(affected)` from the mirror BEFORE it
    issues the graph write, and `_hydrate_dirty_roots` returns early while the
    mirror is non-empty — so anything not restored is unretryable for the life
    of this SDK. The failure is injected AFTER that subtraction (by a shim that
    performs it and then raises), because stubbing the whole method would raise
    before the subtraction and leave every mirror assertion trivially true. The
    fixture includes a NEUTRAL-LINK root (#7505: the mitigation `mid`, whose
    only edge is `(mid)-[:IMPL]->(op)`), which is part of `swept` but NOT of
    `affected` — so a restore keyed on `affected` alone is red here.
    """
    with _db() as db:
        sdk = _open(db)
        try:
            claims, ops = _hub(sdk, 1)
            mid = sdk.mitigate_operator(
                ops[0], "weaker than it looks", strength=0.5)["id"]
            assert set(claims) <= _ep_dirty_ids(sdk), \
                "precondition: the mitigation write dirties the operator inputs"
            assert mid in _ep_dirty_ids(sdk), \
                "precondition: the mitigation is a persisted dirty root (#7505)"
            assert mid in sdk._dirty_roots, \
                "precondition: and it is in the in-memory mirror"

            def _subtract_then_boom(self, affected, run_ep=None):
                # The real ordering: the mirror is mutated, THEN the graph write
                # is attempted. The simulated abort happens where a real engine
                # abort would (on the `SET n.ep_dirty = null`).
                self._dirty_roots -= set(affected) | \
                    self._unsweepable_dirty_roots(affected)
                raise RuntimeError("simulated engine abort on the sweep write")

            monkeypatch.setattr(TortoiseSDK, "_sweep_dirty_roots",
                                _subtract_then_boom)
            result = sdk.compute_confidence(anchors=[claims[0]], max_hops=2,
                                            require_calibration=False)
            assert result["converged"] is True, result
            assert result["confidences"], (
                "the pass itself succeeded — a failed clear must not discard it"
            )
            assert set(claims) <= _ep_dirty_ids(sdk), (
                "a failed clear must leave the flags SET (retained for the next "
                f"pass) — cleared: {sorted(set(claims) - _ep_dirty_ids(sdk))}"
            )
            assert set(claims) <= sdk._dirty_roots, (
                "a failed clear must restore the recomputed roots in the "
                f"in-memory mirror — missing: {sorted(set(claims) - sdk._dirty_roots)}"
            )
            assert mid in sdk._dirty_roots, (
                "the NEUTRAL-LINK root is part of what the sweep subtracts "
                "(`affected | _unsweepable_dirty_roots`) — a restore keyed on "
                "`affected` alone loses it, and nothing in this process can "
                "ever clear it again (#7505)"
            )
        finally:
            sdk.close()


def test_the_noarg_path_does_not_use_the_targeted_clear(monkeypatch):
    """The no-arg branch's clearing is the Dreamer's, NOT this helper's.

    It matters: routing the no-arg path through `_clear_targeted_pass_dirty`
    would stack the targeted gates (truncated / zero-work / epoch snapshot) on
    top of the Dreamer's own, changing behaviour `compute_confidence` documents
    as inherited-and-unchanged. Nothing else in this file would notice.
    """
    with _db() as db:
        sdk = _open(db)
        try:
            calls: list = []
            original = TortoiseSDK._clear_targeted_pass_dirty

            def _spy(self, ep, result):
                calls.append(sorted(result))
                return original(self, ep, result)

            monkeypatch.setattr(TortoiseSDK, "_clear_targeted_pass_dirty", _spy)
            _chain(sdk, 2)
            result = sdk.compute_confidence(require_calibration=False)
            assert result["converged"] is True, result
            assert calls == [], (
                "the no-arg branch must not run the targeted clear — it was "
                f"called with result keys {calls}"
            )
            # Positive control: the same spy DOES fire on a targeted pass, so
            # the assertion above cannot pass by the spy being inert. The ids
            # are the claims `_chain` GENERATED — marking literal "c0"/"c1"
            # here would persist nothing and inject two phantom mirror roots
            # that no `:Point` row can ever resolve.
            ids = [r[0] for r in sdk._get_proj().g.query(
                "MATCH (n:Point) WHERE n.is_operator = false RETURN n.id"
            ).result_set]
            sdk._mark_dirty(ids)
            assert set(ids) <= sdk._dirty_roots, \
                "precondition: the claims are dirty before the targeted pass"
            sdk.compute_confidence(anchors=[ids[0]], max_hops=2,
                                   require_calibration=False)
            assert len(calls) == 1, (
                "positive control: a targeted pass must route through the "
                f"targeted clear exactly once; spy calls={calls}"
            )
        finally:
            sdk.close()
