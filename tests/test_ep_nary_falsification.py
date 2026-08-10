"""EP validation gaps (#420): n-ary factor coverage + falsification tests.

Previously untested surface:
  1. _update_nary_factor — only reachable via _update_factor's >2-inputs
     branch; had ZERO test matches. Covers the pairwise decomposition
     semantics (NAND = all pairs, IMPL = source→targets only) and the
     #326 corruption failure mode: an n-ary factor's TOTAL pull scales
     with arity (C(4,2)=6 pairs × w) instead of staying at weight w, and
     per-claim messages depend on input order.
  2. Falsification / failure modes — run() must return gracefully on
     empty graphs (early return, no crash), and report non-convergence
     honestly ((max_iter, False)) instead of silently succeeding.
  3. The "FalkorDB error path": empty affected set / empty factor list
     must short-circuit before any iteration or graph write.

These tests are hermetic: they drive TortoiseEP through its in-memory
caches (_node_cache/_msg_cache) and stub the graph I/O boundary
(_affected_claims/_affected_factors/_flush_cache), so they run without a
live FalkorDB while still exercising the real factor-update arithmetic
and the real run() loop.

Measured behavior (w=3, neutral Beta(1,1) cavities, damping=1.0;
message norms are L1 = |η1|+|η2|) — AFTER the #326 fix:
  - isolated 2-input NAND:   per-claim msg |η| = 1.158, factor total 2.316
  - 4/6-input NAND:           factor total ratio vs binary = 1.000
                              (weight conserved across pairs, #326; the
                              test asserts within ±5% of the binary total)
  - per-claim after scaling:  binary per-claim × 2/n (0.579 for n=4), i.e.
                              the binary total shared across n claims
  - reversed input order → per-claim messages identical (diff ~1e-15 —
                              clean-state accumulation, #326)
"""
from __future__ import annotations

import types

import pytest

from tortoise.ep import TortoiseEP


# ── Hermetic EP harness ───────────────────────────────────────────

def _stub_proj():
    """Projection stub: TortoiseEP.__init__ only needs .g (never queried
    when caches are pre-populated and I/O boundary is stubbed)."""
    return types.SimpleNamespace(
        g=types.SimpleNamespace(query=lambda *a, **k: types.SimpleNamespace(result_set=[]))
    )


def _make_ep(nodes: list[str], *, damping: float = 1.0, max_iter: int = 50,
             tol: float = 1e-3) -> TortoiseEP:
    ep = TortoiseEP(_stub_proj(), damping=damping, n_quad=8,
                    max_iter=max_iter, tol=tol)
    # Pre-populate caches so _read_node/_read_message/_is_strong never
    # fall through to graph queries.
    ep._node_cache = {cid: (1.0, 1.0) for cid in nodes}
    ep._msg_cache = {}
    return ep


def _msg_norm(msg) -> float:
    """L1 norm of a message's natural params (η1, η2)."""
    return abs(msg[0]) + abs(msg[1])


def _total_pull(ep: TortoiseEP) -> float:
    """Sum of |η| over every message the factor wrote (factor-level pull)."""
    return sum(_msg_norm(v) for v in ep._msg_cache.values())


def _record_factor_calls(ep: TortoiseEP) -> list[tuple]:
    """Wrap _update_factor with a recorder; returns the call log."""
    calls: list[tuple] = []
    orig = ep._update_factor

    def wrapped(op_id, op_type, input_ids, weight=1.0, label=None, direction="bidirectional"):
        calls.append((op_id, op_type, list(input_ids), weight, label, direction))
        return orig(op_id, op_type, input_ids, weight, label, direction)

    ep._update_factor = wrapped  # type: ignore[method-assign]
    return calls


# ═══════════════════════════════════════════════════════════════════
# 1. n-ary factor coverage (#420 item 1)
# ═══════════════════════════════════════════════════════════════════

def test_nary_nand_decomposes_into_all_pairs():
    """4-input NAND → C(4,2)=6 pairwise factor applications, all at full weight.

    This is the branch of _update_factor (len(input_ids) > 2 → nary) that
    previously had zero test matches. Every pair must be processed, and
    every claim must receive a real downward NAND pull (η1 < 0).
    """
    ep = _make_ep(["a", "b", "c", "d"])
    calls = _record_factor_calls(ep)
    ep._update_factor("op", "NAND", ["a", "b", "c", "d"], weight=2.0)

    # 6 pairwise applications, each a binary NAND at the FULL factor weight
    # (the recorder also sees the outer 4-input dispatch — filter it out).
    pairs_calls = [c for c in calls if len(c[2]) == 2]
    assert len(pairs_calls) == 6, f"expected C(4,2)=6 pair applications, got {len(pairs_calls)}"
    assert all(ct == "NAND" for _, ct, *_ in pairs_calls)
    assert all(w == 2.0 for *_, w, _, _ in [c for c in pairs_calls])
    pairs = {frozenset(c[2]) for c in pairs_calls}
    assert len(pairs) == 6, f"expected 6 distinct pairs, got {pairs}"

    # Accumulate-then-scale (#326): each claim receives ONE scaled message
    # (the per-pair contributions summed, then scaled by 2/(n(n-1))), each a
    # downward NAND pull.
    assert set(k[1] for k in ep._msg_cache) == {"a", "b", "c", "d"}
    for key, (ma, mb) in ep._msg_cache.items():
        assert _msg_norm((ma, mb)) > 0.0, f"degenerate message for {key}"
        assert ma < 0.0, f"NAND msg {key} not a downward pull: {(ma, mb)}"


def test_nary_impl_only_source_to_targets():
    """4-input IMPL (source + 3 targets) → exactly pairs (source, target).

    The nary decomposition for IMPL must not create spurious
    target↔target mutual-influence edges between sibling targets.
    Bidirectional IMPL still writes the source back-message per pair.
    """
    ep = _make_ep(["s", "t1", "t2", "t3"])
    calls = _record_factor_calls(ep)
    ep._update_factor("op", "IMPL", ["s", "t1", "t2", "t3"], weight=2.0)

    # The recorder also sees the outer 4-input dispatch — keep binary pairs.
    pairs_calls = [c for c in calls if len(c[2]) == 2]
    assert len(pairs_calls) == 3, f"expected 3 source→target pairs, got {len(pairs_calls)}"
    for _, ct, ids, *_ in pairs_calls:
        assert ct == "IMPL"
        assert ids[0] == "s" and ids[1] in {"t1", "t2", "t3"}, f"spurious pair {ids}"

    # Every claim gets a message: targets pulled UP (η1 > 0), and the
    # source receives a bidirectional back-message too.
    assert set(k[1] for k in ep._msg_cache) == {"s", "t1", "t2", "t3"}
    for cid in ("t1", "t2", "t3"):
        ma, mb = ep._msg_cache[("op", cid, "IMPL")]
        assert ma > 0.0, f"IMPL target {cid} not pulled up: {(ma, mb)}"
    assert _msg_norm(ep._msg_cache[("op", "s", "IMPL")]) > 0.0


def test_nary_nand_per_claim_pull_matches_binary_pair():
    """Per-claim pull stays within a bounded band of the binary pair's.

    After #326's accumulate-then-scale conservation fix, each claim of an
    n-ary factor receives (2/n) × the binary per-claim pull (the binary
    TOTAL is shared across n claims). For n=4 that is exactly 0.5× the
    binary per-claim — the band [0.45, 0.6] leaves margin for quadrature
    drift while pinning the conservation semantics (and failing the
    original last-pair-wins over-pull).
    """
    w = 3.0
    ep_nary = _make_ep(["a", "b", "c", "d"])
    ep_nary._update_factor("op", "NAND", ["a", "b", "c", "d"], weight=w)

    ep_bin = _make_ep(["a", "b"])
    ep_bin._update_factor("op", "NAND", ["a", "b"], weight=w)

    msg_bin = _msg_norm(ep_bin._msg_cache[("op", "a", "NAND")])
    msg_nary = _msg_norm(ep_nary._msg_cache[("op", "a", "NAND")])
    # n=4: per-claim = 2/n × binary = 0.5× exactly. Band [0.45, 0.6] leaves
    # margin for quadrature drift while FAILING the original #326 behavior
    # (per-claim ≈0.88-0.94× binary, last-pair-wins at full weight — outside
    # the band).
    assert 0.45 * msg_bin <= msg_nary <= 0.6 * msg_bin, (
        f"per-claim nary pull {msg_nary:.4f} drifted from binary {msg_bin:.4f}"
    )


def test_nary_nand_weight_not_overcounted():
    """Regression test for #326: n-ary weight over-counting corruption.

    A 4-input NAND decomposes into C(4,2)=6 pairwise factors. The operator
    carries ONE weight w (#326 design decision, pinned by the #420/#536
    falsification suite): the factor's TOTAL pull must equal the isolated
    2-input factor's total pull at the same weight, NOT scale with arity.
    Additionally, a symmetric factor must be input-order-invariant:
    reversing the input order must not change per-claim messages.

    The accumulate-then-scale fix (#326) computes every pair from a clean
    cavity state (input-order-invariant), accumulates per claim, and
    scales by 2/(n(n-1)) so the total equals the binary factor's exactly.
    """
    w = 3.0
    ep_bin = _make_ep(["a", "b"])
    ep_bin._update_factor("op", "NAND", ["a", "b"], weight=w)

    # (a) total pull must be CONSERVED (ratio ~1.0) at every arity — the
    # operator carries ONE weight w, so n-ary and binary totals match.
    for n in (4, 6):
        ids = [chr(ord("a") + i) for i in range(n)]
        ep_nary = _make_ep(ids)
        ep_nary._update_factor("op", "NAND", ids, weight=w)
        total_ratio = _total_pull(ep_nary) / _total_pull(ep_bin)
        assert 0.95 <= total_ratio <= 1.05, (
            f"nary({n}) total pull ratio {total_ratio:.4f} vs binary "
            f"{_total_pull(ep_bin):.4f} — weight over-counting (#326)"
        )

    # (b) order-invariance: same 4-input factor, reversed input order
    ep_fwd = _make_ep(["a", "b", "c", "d"])
    ep_fwd._update_factor("op", "NAND", ["a", "b", "c", "d"], weight=w)
    ep_rev = _make_ep(["d", "c", "b", "a"])
    ep_rev._update_factor("op", "NAND", ["d", "c", "b", "a"], weight=w)
    fwd = {k[1]: v for k, v in ep_fwd._msg_cache.items()}
    rev = {k[1]: v for k, v in ep_rev._msg_cache.items()}
    for cid in fwd:
        assert abs(fwd[cid][0] - rev[cid][0]) < 1e-3, (
            f"order-dependent message for {cid}: fwd {fwd[cid]} vs rev {rev[cid]}"
        )


def test_nary_impl_star_conserves_binary_total():
    """Star topology (IMPL source→targets): total pull conserved vs binary.

    #326: a 4-input IMPL (source + 3 targets) decomposes into 3 pairs; the
    star scale 1/(n-1) keeps the TOTAL equal to the binary IMPL's total at
    the same weight. Targets are diluted 1/(n-1) (evidence dilution across
    siblings); the source's accumulated back-message scales back to the
    binary source message.
    """
    w = 3.0
    bin_ep = _make_ep(["s", "t"])
    bin_ep._update_factor("op", "IMPL", ["s", "t"], weight=w)
    star_ep = _make_ep(["s", "t1", "t2", "t3"])
    star_ep._update_factor("op", "IMPL", ["s", "t1", "t2", "t3"], weight=w)

    ratio = _total_pull(star_ep) / _total_pull(bin_ep)
    assert 0.95 <= ratio <= 1.05, f"IMPL star total ratio {ratio:.4f}"


def test_nary_directed_nand_star_conserves():
    """Directed NAND (source attacks each target): star conservation and NO
    source message (#795/#326 semantics)."""
    w = 3.0
    bin_ep = _make_ep(["s", "t"])
    bin_ep._update_factor("op", "NAND", ["s", "t"], weight=w,
                          direction="unidirectional")
    star_ep = _make_ep(["s", "t1", "t2", "t3"])
    star_ep._update_factor("op", "NAND", ["s", "t1", "t2", "t3"], weight=w,
                           direction="unidirectional")

    ratio = _total_pull(star_ep) / _total_pull(bin_ep)
    assert 0.95 <= ratio <= 1.05, f"directed NAND star total ratio {ratio:.4f}"
    # targets get downward pulls; the source gets NO message (unidirectional)
    assert ("op", "s", "NAND") not in star_ep._msg_cache
    for cid in ("t1", "t2", "t3"):
        ma, mb = star_ep._msg_cache[("op", cid, "NAND")]
        assert ma < 0.0, f"directed NAND target {cid} not attacked: {(ma, mb)}"


def test_nary_directed_star_clears_stale_source_message():
    """Directed NAND: a stale source message (from a pre-migration
    bidirectional run, persisted by _flush_cache) must be CLEARED, never
    re-accumulated as a fresh pair contribution (#326 directed-path
    corruption)."""
    w = 3.0
    ep = _make_ep(["s", "t1", "t2", "t3"])
    ep._msg_cache[("op", "s", "NAND")] = (0.5, 0.3)  # stale bidirectional-era
    ep._update_factor("op", "NAND", ["s", "t1", "t2", "t3"], weight=w,
                      direction="unidirectional")
    assert ep._msg_cache.get(("op", "s", "NAND")) == (0.0, 0.0), (
        "stale source message must be cleared for directed NAND"
    )


def test_nary_cacheless_direct_call_conserves():
    """Direct _update_factor call on a fresh instance (no _msg_cache — graph
    mode) must apply the SAME accumulate-then-scale semantics, never the old
    per-pair over-counting (#326 retained-bug fallback)."""
    w = 3.0
    written: list[tuple[str, str]] = []

    class _G:
        def query(self, cypher, params=None):
            if "SET r.msg_alpha" in cypher:
                written.append((params["oid"], params["cid"]))
            return types.SimpleNamespace(result_set=[])

    ep = TortoiseEP(types.SimpleNamespace(g=_G()), damping=1.0, n_quad=8)
    assert not hasattr(ep, "_msg_cache"), "fresh instance has no msg cache"
    ep._update_factor("op", "NAND", ["a", "b", "c", "d"], weight=w)
    # exactly ONE scaled message written per claim (graph mode) — the old
    # per-pair fallback wrote 12 (6 pairs × 2 claims)
    claims = {c for _, c in written}
    assert len(written) == 4, f"expected 4 writes, got {len(written)}"
    assert claims == {"a", "b", "c", "d"}, f"wrote {claims}"
    # the attribute must NOT be left behind in graph mode (hasattr selects
    # graph I/O in _update_claim_posterior)
    assert not hasattr(ep, "_msg_cache")
    # and the sibling API must not crash on the fresh instance
    ep._update_claim_posterior("a")


def test_nary_with_less_than_two_inputs_is_noop():
    """_update_factor guards: <2 inputs returns without writing anything."""
    ep = _make_ep(["a"])
    ep._update_factor("op", "NAND", ["a"], weight=1.0)
    assert ep._msg_cache == {}

    ep2 = _make_ep([])
    ep2._update_factor("op", "NAND", [], weight=1.0)
    assert ep2._msg_cache == {}


# ═══════════════════════════════════════════════════════════════════
# 2. Falsification / failure-mode tests (#420 item 2)
# ═══════════════════════════════════════════════════════════════════

class _RecordingEP(TortoiseEP):
    """TortoiseEP with the graph I/O boundary stubbed; records flushes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._node_cache = {}
        self._msg_cache = {}
        self._final_node_cache: dict = {}
        self._final_msg_cache: dict = {}
        self.flushed = 0
        self._affected = set()
        self._factors = []

    # ── Stubbed I/O boundary ──
    def _affected_claims(self, operator_ids, max_hops=2):
        return set(self._affected)

    def _affected_factors(self, affected_claims):
        return list(self._factors)

    def _load_cache(self, affected_claims):
        # Mirror the real contract (#6761): reload wipes both caches.
        self._node_cache = {}
        self._msg_cache = {}
        for cid in affected_claims:
            self._node_cache.setdefault(cid, (1.0, 1.0))

    def _flush_cache(self):
        self.flushed += 1

    def _clear_caches(self) -> None:
        """Snapshot final posterior state before run() clears caches (#330)."""
        self._final_node_cache = dict(getattr(self, "_node_cache", {}))
        self._final_msg_cache = dict(getattr(self, "_msg_cache", {}))
        super()._clear_caches()


def test_run_empty_affected_claims_early_returns():
    """FalkorDB error path: no operators / empty graph → (0, True), no flush.

    run() must short-circuit before any iteration or graph write when the
    affected-subgraph extraction comes back empty (empty graph, unknown
    operator IDs, or operator with no edges).
    """
    ep = _RecordingEP(_stub_proj())
    ep._affected = set()  # nothing reachable
    iters, converged = ep.run(["op_that_does_not_exist"])
    assert iters == 0
    assert converged is True
    assert ep.flushed == 0, "empty graph must not trigger any graph write"


def test_run_empty_factors_early_returns():
    """FalkorDB error path: affected claims but zero factors → (0, True).

    An operator whose targets exist but whose edges were removed (or a
    partially-written graph) yields affected claims but no factors; run()
    must still return gracefully without iterating.
    """
    ep = _RecordingEP(_stub_proj())
    ep._affected = {"a", "b"}
    ep._factors = []
    iters, converged = ep.run(["op"])
    assert iters == 0
    assert converged is True
    assert ep.flushed == 0


def test_run_reports_non_convergence_honestly():
    """Falsification: a contradictory graph returns (max_iter, False).

    A→B IMPL plus A→B NAND on the same pair (simultaneous support and
    contradiction) cannot reach a fixed point; run() must exhaust
    max_iter and report failure rather than silently succeeding.
    """
    ep = _RecordingEP(_stub_proj(), damping=0.5, max_iter=10, tol=1e-12)
    ep._affected = {"a", "b"}
    ep._factors = [
        ("op1", "IMPL", ["a", "b"], 5.0, None, "bidirectional"),
        ("op2", "NAND", ["a", "b"], 5.0, None, "bidirectional"),
    ]

    iters, converged = ep.run(["op1", "op2"], max_hops=0)
    assert iters == 10, f"expected max_iter=10 exhausted, got {iters}"
    assert converged is False, "contradictory graph must not report convergence"
    assert ep.flushed == 1, "final state must still be flushed after max_iter"


def test_run_converges_with_gentle_factor():
    """Positive control: a well-behaved graph DOES converge.

    Falsification tests only mean something if the harness can also pass:
    a single binary NAND at modest weight with a reachable tolerance must
    converge within max_iter and report (n, True).
    """
    ep = _RecordingEP(_stub_proj(), damping=0.5, max_iter=50, tol=1e-3)
    ep._affected = {"a", "b"}
    ep._factors = [("op", "NAND", ["a", "b"], 1.0, None, "bidirectional")]

    iters, converged = ep.run(["op"], max_hops=0)
    assert converged is True, f"gentle NAND should converge, got ({iters}, {converged})"
    assert 0 < iters < 50
    assert ep.flushed == 1
    # Posterior moved: message arrived and shifted the Beta posterior away
    # from the neutral (1,1) prior. Read the snapshot taken before run()'s
    # _clear_caches (#330) — _node_cache is deleted post-run.
    a, b = ep._final_node_cache["a"]
    assert a != 1.0 or b != 1.0, "posterior should be updated after convergence"
