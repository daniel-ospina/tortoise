"""EP (Expectation Propagation) belief propagation for Tortoise.

Uses Beta messages in natural parameter space. Moment projection
via Gauss-Jacobi quadrature on [0,1]². Batch I/O eliminates
FalkorDBLite SQLite concurrent-write crashes (#6761).

See: tortoise/research/reflection-agentic-systems/ep-implementation-plan.md
"""
from __future__ import annotations  # noqa: I001

import logging
import math
import random
import re
import time

from .quadrature import tilted_moments, moments_to_beta, phi_nand, phi_impl
from .live import (
    EPISTEMIC_LABELS,
    _live_only,
    _terminal_excluded,
    direction_flows,
    epistemic_disjunction,
    epistemic_identity,
    epistemic_label_queries,
    TERMINAL_EXCLUDED_STATUSES,
)

logger = logging.getLogger(__name__)

# ── Degeneration-guard thresholds (#395, Phase-3 profiling pinned) ──
# A max_hops=None closure must not expand unboundedly on the interactive
# path. Two bounds, aligned with the 200-operator precedent (analyze.py):
#   A. affected ≈ full graph: collected >= fraction of the graph's CLAIMS
#      (claims-only count — `affected` holds claim ids; comparing against
#      all Points would fire on operator-sparse graphs while the component
#      is still far from fully collected, PR #1273)
#      -> the "local" run is degenerate (whole-graph EP); stop expanding.
#   B. per-hop frontier growth: one hop producing > cap new claims while
#      the graph is already large -> explosive-blowup risk; stop expanding.
# Guard B is gated on graph size (combined bound, P3 note): a legitimately
# dense 10-50 claim zone through a hub operator in a small graph must stay
# EXACT (a real closure is never truncated).
_EP_GUARD_FULL_GRAPH_FRACTION = 0.5
_EP_GUARD_FRONTIER_CAP = 200
_EP_GUARD_MIN_GRAPH_CLAIMS = 500


try:  # redis-py is a hard dependency of the graph lane; guard anyway (#6211)
    from redis.exceptions import RedisError as _RedisError
    from redis.exceptions import ResponseError as _ResponseError
except Exception:  # pragma: no cover - import-environment guard
    class _ResponseError(Exception):  # type: ignore[no-redef]
        """Stand-in when redis-py is unavailable (nothing to catch)."""

    class _RedisError(Exception):  # type: ignore[no-redef]
        """Stand-in when redis-py is unavailable (nothing to catch)."""


#: A hop whose query the graph engine ABORTED on its own timeout. Matching on
#: the message is the only signal the engine gives: FalkorDB reports a timeout
#: as a generic ``ResponseError: Query timed out`` (measured, TIMEOUT=1000 on
#: falkordb/falkordb:latest), indistinguishable from a syntax/permission error
#: except by text. Wrapped narrowly on purpose — see
#: :meth:`TortoiseEP._hop_query` for why a non-timeout ``ResponseError`` is
#: re-raised untouched.
_QUERY_TIMEOUT_RE = re.compile(r"query\s+timed\s+out|query\s+timeout",
                               re.IGNORECASE)


class EPSubgraphHopError(_ResponseError):
    """A hop of the affected-claim BFS could not complete (#6211).

    WHY THIS EXISTS. At the graph's own scale the hop query is the part of an
    EP pass that dies. Measured on a 3,353-claim / 160,000-edge graph: the
    two-hop bridge ``MATCH (n)<-[r]-(op:Point)-[r2]->(m) WHERE n.id IN $ids``
    takes 2.7 s on a second-hop frontier, so on an engine with the 1,000 ms
    default query timeout the pass aborts after 1.1 s with
    ``redis.exceptions.ResponseError: Query timed out`` — a bare, hop-less
    error that escapes ``compute_confidence`` and tells the caller nothing
    about WHERE the pass stopped. A caller could not even distinguish "this
    graph is too big for one hop" from "this query was malformed".

    This class carries the hop identity so the failure is *addressable*: a
    caller can bound the hop (smaller seeds / fewer hops), report which hop
    was unaffordable, or retry that hop alone. The attributes are delivered on
    the exception an SDK caller catches; the MCP/HTTP wire path flattens
    exceptions to a prose error payload (``mcp_server._safe``), so a hosted
    caller still reads the hop out of the message until that channel gets a
    structured arm (tracked on #6211), and the ``recovery`` sentence in the
    message is written to be actionable on its own.

    COMPATIBILITY. It subclasses ``redis.exceptions.ResponseError`` (the
    exception the engine actually raised), so every existing
    ``except ResponseError:`` clause — including the retry classifier in
    ``tortoise/retry.py`` — keeps catching it exactly as before. This is an
    additive typing, not a changed contract.

    Attributes:
        hop:           BFS hop index within this ``_affected_claims`` call:
                       ``0`` is the SEED phase (the same two-hop pattern and
                       the seed-set sweeps, run BEFORE the loop — so they are
                       usually the first queries that can die), and
                       ``1..max_hops`` are the loop's hops. ``None`` for a
                       PASS-LEVEL query: one that walks the finished affected
                       set rather than a hop frontier (factor extraction, the
                       epoch read, the degeneration guard's claim count), so
                       no hop index exists to name.
        phase:         which query failed — ``"seed_lookup"`` /
                       ``"seed_operators"`` / ``"seed_direct_edge"`` /
                       ``"seed_neighbourhood"`` (hop 0: the seed phase);
                       ``"operator_bridge"`` (the operator-mediated two-hop
                       pattern) or ``"direct_edge"`` (the operator-less
                       IMPL|NAND hop) for the BFS loop's hops; and
                       ``"factor_extraction"`` / ``"draft_filter"`` /
                       ``"claim_count"`` / ``"ep_epoch"`` / ``"cache_load"`` /
                       ``"cache_flush"`` / ``"evidence_prewrite"`` for
                       pass-level queries.

                       A pass reads and writes the WHOLE affected set, not just
                       its frontier: `_load_cache` sweeps 16 statements over it,
                       `_flush_cache` writes it back, and `run` pre-writes the
                       evidence priors over it. Every one of those is typed
                       too — they are the largest queries of a pass, so an
                       untyped one would keep the issue's symptom (a bare,
                       hop-less ``Query timed out``) reachable on the biggest
                       statement the pass makes.
        frontier_size: how many claim ids the failed query was scoped to.
                       For a hop this is the frontier being expanded FROM —
                       the quantity that drives the hop's cost, so the number
                       an operator needs to bound the pass. ``None`` for a
                       query with no id set (the epoch read).
        elapsed_s:     wall time the failed query consumed before the engine
                       aborted it (``None`` when unknown).
    """

    def __init__(self, *, hop: int | None, phase: str,
                 frontier_size: int | None,
                 elapsed_s: float | None = None, detail: str = "") -> None:
        self.hop = hop
        self.phase = phase
        self.frontier_size = frontier_size
        self.elapsed_s = elapsed_s
        self.detail = detail
        took = "" if elapsed_s is None else f" after {elapsed_s:.2f}s"
        if hop is None:
            if frontier_size is None:
                subject = f"EP pass-level query ({phase}) could not complete{took}"
            else:
                subject = (
                    f"EP pass-level query ({phase}) could not complete{took} "
                    f"over the {frontier_size}-claim affected set"
                )
        else:
            subject = (
                f"EP affected-subgraph BFS: hop {hop} ({phase}) could not "
                f"complete{took} over a {frontier_size}-claim frontier"
            )
        message = (
            f"{subject} — the graph engine aborted the query (query timed "
            f"out). {_recovery_sentence(phase)}"
        )
        if detail:
            message = f"{message} [engine: {detail}]"
        super().__init__(message)


def _recovery_sentence(phase: str) -> str:
    """The remedy for a timed-out pass query, told per phase (#6211).

    A remedy that cannot affect the failing query is worse than none: on the
    epoch read and the claim count, narrowing the pass' seeds or ``max_hops``
    does NOT make the query cheaper, so pointing an operator at those knobs
    sends them to tune something unrelated. The cache phases are separated for
    the same reason — a load failure means the pass never started, a flush
    failure means the posteriors were dropped.
    """
    if phase in ("claim_count", "ep_epoch"):
        return (
            "This query is not hop-scoped — narrowing the pass (fewer seeds or "
            "fewer max_hops) cannot make it cheaper. Raise the engine's query "
            "TIMEOUT (the graph is large enough that a whole-graph read no "
            "longer fits it); on the flush path the epoch re-read rejects the "
            "write-back, so THIS RUN'S POSTERIORS WERE NOT PERSISTED."
        )
    if phase == "cache_load":
        return (
            "The pass could not load the affected set it had selected, so it "
            "never ran: narrow the pass (fewer seeds or fewer max_hops) or "
            "raise the engine's query TIMEOUT."
        )
    if phase == "cache_flush":
        return (
            "THIS RUN'S POSTERIORS WERE NOT PERSISTED: raise the engine's "
            "query TIMEOUT, or narrow the pass so the write-back spans fewer "
            "rows."
        )
    return (
        "The affected-claim expansion is incomplete: narrow the pass (fewer "
        "seeds or fewer max_hops) or raise the engine's query TIMEOUT."
    )


def _is_query_timeout(exc: BaseException) -> bool:
    """True when *exc* is the engine's own query-timeout abort."""
    return isinstance(exc, _RedisError) and bool(
        _QUERY_TIMEOUT_RE.search(str(exc)))


class TortoiseEP:
    """EP belief propagation on Tortoise factor graphs.

    Operates on the FalkorProjection's graph. Uses batch I/O:
    loads all affected data into Python dicts at the start of each
    iteration, computes factor updates in Python, flushes writes at end.

    Parameters:
        projection: FalkorProjection instance (provides .g and ._neighbors)
        damping: message damping factor, 0 < λ ≤ 1
        n_quad: Gauss-Jacobi points per dimension (8 = ≤0.8% error at
            w=100, <0.03% at w=50; n_quad=16 recovers to machine-like
            precision across all w ≤ 100)
        max_iter: hard cap on EP outer iterations
        tol: convergence threshold (max relative change in α,β)
        emit: OPTIONAL journal emitter (#2884 D3) — called as
            ``emit("ConfidenceChanged", id=<id>, **props)`` once per belief
            value actually committed to the graph (never per iteration).
            ``None`` (the default) means the graph-only lane: no journaling,
            byte-identical to the pre-#2884 behaviour. The projection is
            never mutated by this parameter.
    """

    def __init__(self, projection, *, damping=0.5, n_quad=8,
                 max_iter=50, tol=1e-4, evidence=None, emit=None):
        self.proj = projection
        self.g = projection.g
        # #2884 D3: the durability-journal seam. A plain callable (the SDK
        # passes ``_emit_event``) so this module never imports sdk.py; the
        # `or None` coerces a falsy emitter to the no-journal lane.
        self._emit = emit or None
        self.damping = damping
        self.n_quad = n_quad
        self.max_iter = max_iter
        self.tol = tol
        # Fixed evidence priors: {claim_id: (alpha, beta)}
        self._evidence = dict(evidence) if evidence else {}
        # #395 (delta C): run-set diagnostics, stashed by run() for write-back
        # consistency. _last_affected = the affected-claim set of the most
        # recent run (== the set _load_cache/_flush_cache covered); reset at
        # run entry, assigned BEFORE the early returns so a vacuous run never
        # leaves stale state. _last_truncated = the degeneration guard fired
        # (per-hop cap or affected≈full) during the most recent run.
        self._last_affected: set[str] = set()
        self._last_truncated: bool = False
        # #6105: run-set diagnostics for factors DROPPED before EP could use
        # them — one dict per skipped factor (operator id, reason, and every
        # input's status). Stashed by run() (reset at entry, appended by
        # `_affected_factors`) so a caller can SEE that a wired operator
        # contributed nothing: a degenerate skip is otherwise a log line
        # while the run still reports `converged=True`, and a whole arm of
        # the graph (a causality map's goal, wired only as a TARGET) is
        # discarded silently.
        self._last_skipped_factors: list[dict] = []
        # #1163 (multi-process EP): the graph-wide EP epoch snapshot for THIS
        # run — captured at load (``_load_cache``), re-read by the flush guard
        # so a stale run (a concurrent ``_mark_dirty`` advanced the epoch
        # mid-run) cannot clobber the newer run's ep_alpha/msg_alpha writes
        # (the #6761 batch-I/O load→flush race). ``_flush_skipped`` records
        # that the guard fired. -1 = no snapshot taken (a DIRECT flush with
        # manually-set caches is unconditional — only load→flush runs are
        # guarded).
        self._run_ep_version: int = -1
        self._flush_skipped: bool = False

    def _hop_query(self, cypher: str, params: dict, *, hop: int | None,
                   phase: str, frontier_size: int | None) -> list:
        """Run one EP pass query, typing an engine timeout (#6211).

        A hop that the engine ABORTS on its own timeout used to escape as a
        bare ``redis.exceptions.ResponseError: Query timed out`` — no hop, no
        frontier size, indistinguishable from a malformed query. Here it becomes
        :class:`EPSubgraphHopError`, which says WHICH hop failed and over how
        large a frontier, while remaining a ``ResponseError`` subclass so every
        existing ``except`` clause (and ``tortoise/retry.py``'s classifier) is
        unaffected.

        Used for every id-set query of a pass, not only the two-hop expansions:
        the seed phase (hop 0), the loop's hops, and the PASS-LEVEL queries that
        walk the finished affected set — factor extraction over thousands of ids
        is the largest query of the whole pass, so leaving it bare would keep
        the issue's exact symptom on one of the paths it measured (#6211 review
        round 2). Those pass-level callers pass ``hop=None``.

        ONLY timeouts are wrapped, deliberately. A ``ResponseError`` that is not
        a timeout (WRONGTYPE, a malformed recipe, a permission refusal) is a
        DIFFERENT failure — it is not "this hop was too big", the remedy is not
        "narrow the pass", and ``retry.py`` classifies some of them (MISCONF,
        write-lock, graph-abort) into retry arms that a rewriting wrapper would
        hide behind a new message. Those re-raise untouched.
        """
        started = time.perf_counter()
        try:
            return self.g.query(cypher, params=params).result_set
        except _ResponseError as exc:
            if not _is_query_timeout(exc):
                raise
            raise EPSubgraphHopError(
                hop=hop, phase=phase, frontier_size=frontier_size,
                elapsed_s=time.perf_counter() - started, detail=str(exc),
            ) from exc

    def _graph_ep_version(self) -> int:
        """Current graph-wide EP epoch (the :EpMeta node's ep_version).

        0 on a graph that has never dirtied EP (no EpMeta node yet).
        """
        rows = self._hop_query(
            "MATCH (m:EpMeta) RETURN m.ep_version", {},
            hop=None, phase="ep_epoch", frontier_size=None,
        )
        if not rows:
            return 0
        try:
            return int(rows[0][0])
        except (TypeError, ValueError):
            return 0

    # ── Batch I/O cache (#6761) ──────────────────────────────────

    def _load_cache(self, affected_claims: set[str]):
        """Load all node params and edge messages into Python dicts."""
        # #1163: the flush guard's load-side snapshot — the epoch as this
        # run SAW the graph. A concurrent _mark_dirty after this point
        # advances the graph epoch; the flush then self-rejects.
        self._run_ep_version = self._graph_ep_version()
        self._flush_skipped = False
        self._node_cache: dict[str, tuple[float, float]] = {}
        self._msg_cache: dict[tuple[str, str, str], tuple[float, float]] = {}
        # Operator-less direct-edge back-messages (#888 W5): the back-message
        # of a direct edge lives ON the edge (r.back_msg_alpha/beta) so each
        # edge has its own slot (fan-out sources don't collide).
        self._back_cache: dict[tuple[str, str, str], tuple[float, float]] = {}

        self._immutable_priors: set[str] = set()
        if affected_claims:
            rows: list = []
            # #7853: id-anchored read — keep the label and sweep the four
            # epistemic labels so the per-(label, property) id index anchors
            # each probe (a bare `MATCH (n) WHERE n.id IN $ids` full-scans).
            for _q in epistemic_label_queries(
                "MATCH (n:{label}) WHERE n.id IN $ids "
                "RETURN n.id, coalesce(n.ep_alpha,1.0), coalesce(n.ep_beta,1.0), "
                "       coalesce(n.baseline_set, false)"
            ):
                rows.extend(self._hop_query(
                    _q, {"ids": list(affected_claims)}, hop=None,
                    phase="cache_load", frontier_size=len(affected_claims),
                ))
            for cid, a, b, is_baseline in rows:
                self._node_cache[cid] = (float(a), float(b))
                # #844: explicit baselines (baseline_set=true) are IMMUTABLE
                # evidence priors per sdk.py 'NEVER recomputed' contract — EP
                # must not persist posteriors over them or re-runs drift
                # (each run re-hydrates the previous posterior as the new
                # prior → confidence erodes monotonically).
                if is_baseline:
                    self._immutable_priors.add(cid)

        for rel in ("IMPL", "NAND"):
            rows = []
            # #7853: c is the id-anchored side (c.id IN $ids) — sweep labels.
            for _q in epistemic_label_queries(
                f"MATCH (o:Point)-[r:{rel}]->(c:{{label}}) "
                "WHERE c.id IN $ids "
                "RETURN o.id, c.id, coalesce(r.msg_alpha,0.0), coalesce(r.msg_beta,0.0)"
            ):
                rows.extend(self._hop_query(
                    _q, {"ids": list(affected_claims)}, hop=None,
                    phase="cache_load", frontier_size=len(affected_claims),
                ))
            for oid, cid, ma, mb in rows:
                self._msg_cache[(oid, cid, rel)] = (float(ma), float(mb))

        if affected_claims:
            # #7853/#7902: back-messages live on DIRECT edges
            # (a)-[:IMPL|NAND]->(b). Both endpoints are id-anchored by the
            # write path (#7902 widened create_direct_edge to the four
            # epistemic labels), so sweep the TARGET label for the index and
            # widen the SOURCE traversal position with the disjunction
            # (live.epistemic_disjunction) — a :Subject/:Object/:Event source
            # direct edge must load its back-message slot.
            back_rows: list = []
            for _q in epistemic_label_queries(
                "MATCH (a)-[r:IMPL|NAND]->(b:{label}) "
                f"WHERE {epistemic_disjunction('a')} "
                "AND coalesce(r.direction, 'bidirectional') IN "
                "['bidirectional', '<->', '<-'] "
                "AND (a.id IN $ids OR b.id IN $ids) "
                "RETURN a.id, b.id, type(r), "
                "       coalesce(r.back_msg_alpha,0.0), coalesce(r.back_msg_beta,0.0)"
            ):
                back_rows.extend(self._hop_query(
                    _q, {"ids": list(affected_claims)}, hop=None,
                    phase="cache_load", frontier_size=len(affected_claims),
                ))
            for src, tgt, rel, ma, mb in back_rows:
                self._back_cache[(src, tgt, rel)] = (float(ma), float(mb))

    def _flush_cache(self) -> bool:
        """Write all cached data back to FalkorDB in batch.

        Attribute-safe (#330): caches may be absent (never loaded, or removed
        by the per-run lifecycle) — flush whatever is present.

        #1163 (multi-process EP): stale-run guard — when the graph's
        ep_version advanced past the version this run loaded (``_run_ep_version``
        — a concurrent process marked something dirty mid-run, so this run's
        cached state is stale), the ENTIRE flush is rejected: no ep_alpha/
        msg_alpha writes interleave with the newer run's. ``self._flush_skipped``
        records the rejection for the dreamer (confidence write-back + dirty-
        flag sweep must also stand down). Returns True when the flush ran (or
        had nothing to write), False when the stale-run guard rejected it.
        """
        if (getattr(self, "_node_cache", None)
                or getattr(self, "_msg_cache", None)
                or getattr(self, "_back_cache", None)):
            current = self._graph_ep_version()
            if self._run_ep_version >= 0 and current > self._run_ep_version:
                # A concurrent write marked claims dirty mid-run: reject this
                # stale flush rather than clobber the newer run's writes.
                # Only load→flush runs are guarded (a snapshot exists); a
                # direct flush with manually-set caches is unconditional.
                self._flush_skipped = True
                return False
        self._flush_skipped = False
        if getattr(self, "_node_cache", None):
            immutable = getattr(self, "_immutable_priors", set())
            params_list = [
                {"id": cid, "a": a, "b": b,
                 "c": round(a/(a+b), 4) if (a + b) > 0 else 0.5,
                 "keep_prior": cid in immutable}
                for cid, (a, b) in self._node_cache.items()
            ]
            result = None
            written: set = set()
            # #7853: id-anchored batch write — sweep the epistemic labels so
            # each node is written through its own label's id index (the
            # Point arm stays the hot, indexed first probe).
            for _q in epistemic_label_queries(
                "UNWIND $params AS p "
                "MATCH (n:{label} {id: p.id}) "
                # n.posterior_alpha/beta = the true EP posterior (preferred by
                # _read_node/compute_confidence — resolves observability for
                # baseline'd claims, #852 review P1). n.confidence = posterior
                # mean. n.ep_alpha/beta stay as IMMUTABLE priors for baseline'd
                # claims (re-run stability), posteriors for others (back-compat).
                "SET n.confidence = p.c, "
                "    n.posterior_alpha = p.a, n.posterior_beta = p.b, "
                "    n.ep_alpha = CASE WHEN p.keep_prior THEN n.ep_alpha ELSE p.a END, "
                "    n.ep_beta  = CASE WHEN p.keep_prior THEN n.ep_beta  ELSE p.b END "
                "RETURN n.id"
            ):
                result = self._hop_query(
                    _q, {"params": params_list}, hop=None,
                    phase="cache_flush", frontier_size=len(params_list),
                )
                written.update(row[0] for row in result)
            # #2884 D3: journal EXACTLY what this statement committed — one
            # ConfidenceChanged per MATCHed node, once per flush (the cache
            # is the batched accumulator; emitting per EP iteration would
            # blow up the journal). ``RETURN n.id`` makes the journal set the
            # committed set: a cache entry whose Point vanished mid-run is
            # NOT journaled as a write. The stale-run guard above already
            # returned before any write, so a rejected flush journals nothing.
            if self._emit is not None:
                for p in params_list:
                    if p["id"] in written:
                        self._emit(
                            "ConfidenceChanged", id=p["id"],
                            confidence=p["c"], posterior_alpha=p["a"],
                            posterior_beta=p["b"],
                        )

        if getattr(self, "_msg_cache", None):
            for rel in ("IMPL", "NAND"):
                params_list = [
                    {"oid": oid, "cid": cid, "a": ma, "b": mb}
                    for (oid, cid, r), (ma, mb) in self._msg_cache.items()
                    if r == rel
                ]
                if params_list:
                    # #7853: o is id-anchored (the operator); drop the child's
                    # label and filter the traversal with the disjunction —
                    # no index is lost (the edge is reached FROM o).
                    result = self._hop_query(
                        f"UNWIND $params AS p "
                        f"MATCH (o:Point {{id: p.oid}})-[r:{rel}]->(c) "
                        f"WHERE {epistemic_disjunction('c')} "
                        "AND c.id = p.cid "
                        "SET r.msg_alpha = p.a, r.msg_beta = p.b "
                        "RETURN p.oid, p.cid, p.a, p.b",
                        {"params": params_list}, hop=None,
                        phase="cache_flush", frontier_size=len(params_list),
                    )
                    # #5380: journal EXACTLY the edges this statement
                    # committed. ``RETURN`` makes the journal set the committed
                    # set — a parameter whose edge vanished mid-run is NOT
                    # journaled as a write (the node arm's `RETURN n.id`
                    # discipline, above).
                    for _oid, _cid, _a, _b in result:
                        self._emit_edge_message(
                            _oid, _cid, rel, self.EDGE_MESSAGE_FWD_SLOTS,
                            (_a, _b))

        if getattr(self, "_back_cache", None):
            for rel in ("IMPL", "NAND"):
                params_list = [
                    {"src": src, "tgt": tgt, "a": ma, "b": mb}
                    for (src, tgt, r), (ma, mb) in self._back_cache.items()
                    if r == rel
                ]
                if params_list:
                    # #7902: both endpoints of a direct edge may carry any
                    # epistemic label. Sweep the SOURCE label (id index kept)
                    # and widen the TARGET traversal position — a non-Point
                    # direct-edge back-message must persist.
                    for _q in epistemic_label_queries(
                        f"UNWIND $params AS p "
                        f"MATCH (o:{{label}} {{id:p.src}})-[r:{rel}]->(c) "
                        f"WHERE {epistemic_disjunction('c')} "
                        "AND c.id = p.tgt "
                        "SET r.back_msg_alpha = p.a, r.back_msg_beta = p.b "
                        "RETURN p.src, p.tgt, p.a, p.b"
                    ):
                        _res = self._hop_query(
                            _q, {"params": params_list}, hop=None,
                            phase="cache_flush", frontier_size=len(params_list),
                        )
                        # #5380: one record per committed edge, per label.
                        for _s, _t, _a, _b in _res:
                            self._emit_edge_message(
                                _s, _t, rel,
                                self.EDGE_MESSAGE_BACK_SLOTS, (_a, _b))
        return True

    def _clear_caches(self) -> None:
        """Remove the batch caches so post-run reads hit the graph (#330)."""
        for _attr in ("_node_cache", "_msg_cache", "_back_cache"):
            if hasattr(self, _attr):
                delattr(self, _attr)

    # ── #5380: edge-message durability journaling ────────────────
    # The four edge-resident EP message slots, paired by direction. The emit
    # sites below pass the two PAIR constants — never a literal — and
    # `tests/test_4503_edge_relationship_accounting.py` pins the flat
    # `EDGE_MESSAGE_SLOTS` equal to the projection fold's
    # `projection.entities.EDGE_MESSAGE_PROPS` and to
    # `tools.edge_census.EP_EDGE_SLOTS`, so a fifth slot added to one copy of
    # the vocabulary reds that pin instead of drifting silently.
    EDGE_MESSAGE_FWD_SLOTS: tuple = ("msg_alpha", "msg_beta")
    EDGE_MESSAGE_BACK_SLOTS: tuple = ("back_msg_alpha", "back_msg_beta")
    EDGE_MESSAGE_SLOTS: tuple = (EDGE_MESSAGE_FWD_SLOTS
                                 + EDGE_MESSAGE_BACK_SLOTS)

    def _emit_edge_message(self, src: str, tgt: str, rel: str,
                           keys: tuple, values: tuple) -> None:
        """Journal ONE committed edge-message write (#5380).

        ``_emit`` is ``None`` on a journal-less lane, so the graph-only path
        stays byte-identical to the pre-#5380 behaviour (no emission, no
        extra read). The record carries the edge IDENTITY
        (``src``/``tgt``/``rel`` — the edge has no node id of its own, the
        same flat composite ``DirectEdgeCreated`` uses) plus the slot values
        actually committed, mirroring the node arm's ``RETURN n.id``
        discipline above: the journal set IS the committed set.

        Called ONCE per committed edge, never per EP iteration — the caches
        are the batched accumulator (#6761's load→flush boundary).
        """
        if self._emit is None:
            return
        self._emit("EdgeMessageChanged", op="set",
                   id=f"{src}->{tgt}:{rel}", src=src, tgt=tgt, rel=rel,
                   **dict(zip(keys, values, strict=True)))

    def _emit_edge_message_clear(self, scope: str,
                                 point_ids: list | None = None) -> None:
        """Journal ONE committed edge-message INVALIDATION (#5380).

        A BULK record, deliberately not one-per-edge: the live clear is a
        single Cypher ``REMOVE`` over a whole scope, and enumerating its edges
        to journal per-edge records would turn a documented-cheap full drop
        into an O(#edges) result-set materialization on a hot write path
        (``create_point`` with a baseline reaches the full-graph drop).
        ``scope`` names the live query the replay fold re-runs; a record is
        emitted whenever the live statement MATCHED at least one edge. Its
        ``RETURN count`` is the matched-edge count, NOT the number of edges
        that actually carried message state — a topology change that merely
        touches a bare edge still emits a record.

        Without this leg the fold would RESURRECT seeds the live graph had
        dropped — the #330 live/replay parity break, arriving the moment the
        write leg above starts journaling: pre-#5380 a rebuild produced bare
        edges, which happened to agree with a post-clear live graph.
        """
        if self._emit is None:
            return
        payload: dict = {"op": "clear", "scope": scope}
        if point_ids is not None:
            payload["point_ids"] = [p for p in point_ids if isinstance(p, str)]
        self._emit("EdgeMessageChanged", id="edge-messages-clear", **payload)

    # ── Cached read/write ────────────────────────────────────────

    def _read_node(self, node_id: str) -> tuple[float, float]:
        # #330: capture once — a concurrent run() may delattr the cache between
        # the hasattr check and the read (TOCTOU); a local None falls through
        # to the graph instead of raising AttributeError.
        _cache = getattr(self, '_node_cache', None)
        if _cache is not None and node_id in _cache:
            return _cache[node_id]
        # #7853: id-anchored — sweep the epistemic labels, Point first
        # (indexed hot path).
        for _q in epistemic_label_queries(
            "MATCH (n:{label} {id:$id}) "
            "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
            "       coalesce(n.posterior_beta, n.ep_beta, 1.0)"
        ):
            rows = self.g.query(_q, params={"id": node_id}).result_set
            if rows:
                return (float(rows[0][0]), float(rows[0][1]))
        return (1.0, 1.0)

    def _write_node(self, node_id: str, alpha: float, beta: float) -> None:
        _cache = getattr(self, '_node_cache', None)
        if _cache is not None:
            _cache[node_id] = (alpha, beta)
            return
        # #330: guard degenerate (0,0) params — uniform fallback instead of ZDE
        mean = round(alpha / (alpha + beta), 4) if (alpha + beta) > 0 else 0.5
        # #852 round-6: mirror _flush_cache — baseline'd claims keep their
        # immutable prior; posterior written separately for observability.
        # #7853: id-anchored — sweep the epistemic labels (Point first).
        matched = False
        for _q in epistemic_label_queries(
            "MATCH (n:{label} {id:$id}) "
            "SET n.confidence=$c, n.posterior_alpha=$a, n.posterior_beta=$b, "
            "    n.ep_alpha=CASE WHEN coalesce(n.baseline_set,false) "
            "                    THEN n.ep_alpha ELSE $a END, "
            "    n.ep_beta =CASE WHEN coalesce(n.baseline_set,false) "
            "                    THEN n.ep_beta  ELSE $b END "
            "RETURN n.id"
        ):
            result = self.g.query(_q, params={
                "id": node_id, "a": alpha, "b": beta, "c": mean})
            if result.result_set:
                matched = True
        # #2884 D3: the DIRECT (no-cache) path is a committed write — journal
        # it here; the cached path defers to _flush_cache's batched emit.
        # Guard on the MATCH: a missing Point committed nothing.
        if self._emit is not None and matched:
            self._emit("ConfidenceChanged", id=node_id, confidence=mean,
                       posterior_alpha=alpha, posterior_beta=beta)

    def _read_message(self, op_id: str, claim_id: str,
                      rel_type: str = "IMPL") -> tuple[float, float]:
        key = (op_id, claim_id, rel_type)
        _cache = getattr(self, '_msg_cache', None)
        if _cache is not None and key in _cache:
            return _cache[key]
        rows = self.g.query(
            f"MATCH (o:Point {{id:$oid}})-[r:{rel_type}]->(c) "
            f"WHERE {epistemic_disjunction('c')} AND c.id = $cid "
            "RETURN coalesce(r.msg_alpha, 0.0), coalesce(r.msg_beta, 0.0)",
            params={"oid": op_id, "cid": claim_id},
        ).result_set
        return (float(rows[0][0]), float(rows[0][1])) if rows else (0.0, 0.0)

    def _write_message(self, op_id: str, claim_id: str,
                       msg_alpha: float, msg_beta: float,
                       rel_type: str = "IMPL") -> None:
        key = (op_id, claim_id, rel_type)
        _cache = getattr(self, '_msg_cache', None)
        if _cache is not None:
            _cache[key] = (msg_alpha, msg_beta)
            return
        result = self.g.query(
            f"MATCH (o:Point {{id:$oid}})-[r:{rel_type}]->(c) "
            f"WHERE {epistemic_disjunction('c')} AND c.id = $cid "
            "SET r.msg_alpha=$a, r.msg_beta=$b RETURN c.id",
            params={"oid": op_id, "cid": claim_id, "a": msg_alpha, "b": msg_beta},
        )
        # #5380: the DIRECT (no-cache) path is a committed write — journal it
        # here; the cached path defers to ``_flush_cache``'s batched emit.
        # Guard on the MATCH, exactly as ``_write_node`` does: a missing edge
        # committed nothing.
        if result.result_set:
            self._emit_edge_message(op_id, claim_id, rel_type,
                                    self.EDGE_MESSAGE_FWD_SLOTS,
                                    (msg_alpha, msg_beta))

    # ── Operator-less direct-edge back-messages (#888 W5) ──────────
    # The back-message of a direct edge (src)-[r]->(tgt) lives ON the edge
    # as r.back_msg_alpha/r.back_msg_beta (distinct from the forward
    # message r.msg_alpha/r.msg_beta), so every direct edge has its own
    # back-message slot and fan-out sources do not collide.

    def _read_back_message(self, src_id: str, tgt_id: str,
                           rel_type: str = "IMPL") -> tuple[float, float]:
        key = (src_id, tgt_id, rel_type)
        _cache = getattr(self, '_back_cache', None)
        if _cache is not None and key in _cache:
            return _cache[key]
        rows = []
        # #7902: the SOURCE endpoint of a direct edge may be any epistemic
        # label — sweep it (id index) and widen the target by traversal.
        for _q in epistemic_label_queries(
            f"MATCH (o:{{label}} {{id:$oid}})-[r:{rel_type}]->(c) "
            f"WHERE {epistemic_disjunction('c')} AND c.id = $cid "
            "RETURN coalesce(r.back_msg_alpha, 0.0), coalesce(r.back_msg_beta, 0.0)"
        ):
            rows = self.g.query(
                _q, params={"oid": src_id, "cid": tgt_id}).result_set
            if rows:
                break
        return (float(rows[0][0]), float(rows[0][1])) if rows else (0.0, 0.0)

    def _write_back_message(self, src_id: str, tgt_id: str,
                            msg_alpha: float, msg_beta: float,
                            rel_type: str = "IMPL") -> None:
        key = (src_id, tgt_id, rel_type)
        _cache = getattr(self, '_back_cache', None)
        if _cache is not None:
            _cache[key] = (msg_alpha, msg_beta)
            return
        # #7902: sweep the SOURCE label (id index); widen the target traversal.
        for _q in epistemic_label_queries(
            f"MATCH (o:{{label}} {{id:$oid}})-[r:{rel_type}]->(c) "
            f"WHERE {epistemic_disjunction('c')} AND c.id = $cid "
            "SET r.back_msg_alpha=$a, r.back_msg_beta=$b RETURN c.id"
        ):
            _res = self.g.query(_q, params={
                "oid": src_id, "cid": tgt_id, "a": msg_alpha, "b": msg_beta})
            # #5380: journal the committed write (direct, no-cache path).
            if _res.result_set:
                self._emit_edge_message(
                    src_id, tgt_id, rel_type,
                    self.EDGE_MESSAGE_BACK_SLOTS,
                    (msg_alpha, msg_beta))

    # ── Natural parameter helpers ─────────────────────────────────

    @staticmethod
    def _natural_from_beta(alpha: float, beta: float) -> tuple[float, float]:
        return (alpha - 1, beta - 1)

    @staticmethod
    def _beta_from_natural(eta1: float, eta2: float) -> tuple[float, float]:
        return (max(eta1 + 1, 0.01), max(eta2 + 1, 0.01))

    def _posterior_natural(self, claim_id: str) -> tuple[float, float]:
        alpha, beta = self._read_node(claim_id)
        return self._natural_from_beta(alpha, beta)


    def _cavity_natural(self, claim_id: str, op_id: str,
                        rel_type: str) -> tuple[float, float]:
        post_eta1, post_eta2 = self._posterior_natural(claim_id)
        msg_eta1, msg_eta2 = self._read_message(op_id, claim_id, rel_type)
        return (post_eta1 - msg_eta1, post_eta2 - msg_eta2)

    # ── Single-factor EP update ───────────────────────────────────

    def _is_strong(self, claim_id: str, threshold: float = 0.85) -> bool:
        """True if the claim's current belief is at/above the given threshold.

        Utility predicate (reads posterior-first, consistent with
        _read_node). The #86 bidirectional-IMPL back-message hack that was
        its production caller was removed in #855 (difference coupling
        handles upward damage naturally) — retained for defensive utility
        and the zero-division guard test.
        """
        _cache = getattr(self, "_node_cache", None)
        if _cache is not None and claim_id in _cache:
            a, b = _cache[claim_id]
            mean = a / (a + b) if (a + b) > 0 else 0.5
            return mean >= threshold
        rows = []
        # #7853: id-anchored — sweep the epistemic labels (Point first).
        for _q in epistemic_label_queries(
            "MATCH (n:{label} {id:$id}) "
            "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
            "       coalesce(n.posterior_beta, n.ep_beta, 1.0)"
        ):
            rows = self.g.query(_q, params={"id": claim_id}).result_set
            if rows:
                break
        if not rows or rows[0][0] is None:
            return False
        a, b = float(rows[0][0]), float(rows[0][1])
        # #330: guard degenerate (0,0) params (cache path already guards)
        return a / (a + b) >= threshold if (a + b) > 0 else False

    def _update_factor(self, op_id: str, op_type: str,
                       input_ids: list[str], weight: float = 1.0,
                       label: str | None = None,
                       direction: str = "bidirectional") -> None:
        if len(input_ids) < 2:
            return
        if len(input_ids) > 2:
            return self._update_nary_factor(op_id, op_type, input_ids, weight, label, direction)

        id_a, id_b = input_ids

        # Operator-less direct edge (#888 W5): the factor's "op" endpoint is
        # the source point itself (op_id == id_a). The forward message stays
        # on the edge (r.msg_alpha/r.msg_beta, key (src, tgt, rel)); the
        # back-message gets its OWN per-edge slot (r.back_msg_*), so fan-out
        # sources with several bidirectional direct edges do not collide.
        direct = (op_id == id_a)

        if direct:
            post_eta_a = self._posterior_natural(id_a)
            back_a = self._read_back_message(id_a, id_b, op_type)
            cav_eta_a = (post_eta_a[0] - back_a[0], post_eta_a[1] - back_a[1])
        else:
            cav_eta_a = self._cavity_natural(id_a, op_id, op_type)
        cav_eta_b = self._cavity_natural(id_b, op_id, op_type)
        alpha_a, beta_a = self._beta_from_natural(*cav_eta_a)
        alpha_b, beta_b = self._beta_from_natural(*cav_eta_b)

        # NAND uses the standard symmetric contradiction potential for the
        # target message. DIRECTION is enforced structurally by the
        # back-message guard below: for a forward-only direction (`->` /
        # `unidirectional`) the source/attacker receives NO factor message (the
        # Dung-style directed attack an agent opts into), and for `-` neither
        # endpoint does. Default is bidirectional
        # (mutual contradiction) per product owner (#753).
        phi = phi_nand if op_type == "NAND" else phi_impl
        mom_a, mom_b = tilted_moments(
            alpha_a, beta_a, alpha_b, beta_b, weight, phi, self.n_quad
        )

        new_alpha_a, new_beta_a = moments_to_beta(*mom_a)
        new_alpha_b, new_beta_b = moments_to_beta(*mom_b)
        new_eta_a = self._natural_from_beta(new_alpha_a, new_beta_a)
        new_eta_b = self._natural_from_beta(new_alpha_b, new_beta_b)

        raw_eta_a = (new_eta_a[0] - cav_eta_a[0], new_eta_a[1] - cav_eta_a[1])
        raw_eta_b = (new_eta_b[0] - cav_eta_b[0], new_eta_b[1] - cav_eta_b[1])

        # Determine how this operator flows (#7852). DIRECTION is the Layer-1
        # four-value vocabulary with the two legacy aliases: `->` /
        # `unidirectional` (source→target), `<-` (target→source), `<->` /
        # `bidirectional` (both), `-` (neither — the connection exists and NO
        # confidence transfers). `direction_flows` is the ONE normalizer, so
        # the four model values cannot be misread as their opposite here.
        fwd, bwd = direction_flows(direction)

        # Proportional boost: breaks EP fixed-point symmetry that forces
        # messages to near-zero for unevidenced targets. Fades as evidence
        # accumulates: Beta(1,1)=3×, Beta(4,4)≈1.29×, Beta(10,10)≈1.1× (1+2/max(α+β−1,1)).
        # Applied to IMPL agreement messages ONLY: boosting a NAND
        # (contradiction) message to a weakly-evidenced claim would crush it
        # to ~0 (T4 claim hit by a T0 NAND at w=8 collapses 0.52→0.15 raw,
        # →0.001 boosted), cratering the whole downstream chain (#855).
        alpha_a, beta_a = self._beta_from_natural(*cav_eta_a)
        alpha_b, beta_b = self._beta_from_natural(*cav_eta_b)
        boost_a = 1.0 + 2.0 / max(alpha_a + beta_a - 1.0, 1.0)
        boost_b = 1.0 + 2.0 / max(alpha_b + beta_b - 1.0, 1.0)
        if op_type == "NAND":
            boost_a, boost_b = 1.0, 1.0
        raw_eta_a = (raw_eta_a[0] * boost_a, raw_eta_a[1] * boost_a)
        raw_eta_b = (raw_eta_b[0] * boost_b, raw_eta_b[1] * boost_b)

        d = self.damping
        if direct:
            old_eta_a = self._read_back_message(id_a, id_b, op_type)
        else:
            old_eta_a = self._read_message(op_id, id_a, op_type)
        old_eta_b = self._read_message(op_id, id_b, op_type)

        damped_a = (d * raw_eta_a[0] + (1 - d) * old_eta_a[0],
                    d * raw_eta_a[1] + (1 - d) * old_eta_a[1])
        damped_b = (d * raw_eta_b[0] + (1 - d) * old_eta_b[0],
                    d * raw_eta_b[1] + (1 - d) * old_eta_b[1])

        damped_a = (max(min(damped_a[0], 1000), -1000),
                    max(min(damped_a[1], 1000), -1000))
        damped_b = (max(min(damped_b[0], 1000), -1000),
                    max(min(damped_b[1], 1000), -1000))

        # Persist the message(s) the direction actually flows. The factor math
        # above is direction-agnostic (the potential is symmetric); direction
        # selects WHICH endpoint receives. `->` writes the target message;
        # `<-` writes the SOURCE message (the reverse of the legacy default);
        # `<->` both; `-` neither.
        if fwd:
            self._write_message(op_id, id_b, *damped_b, op_type)
        if bwd:
            if direct:
                self._write_back_message(id_a, id_b, *damped_a, op_type)
            else:
                self._write_message(op_id, id_a, *damped_a, op_type)

    def _update_nary_factor(self, op_id: str, op_type: str,
                            input_ids: list[str], weight: float = 1.0,
                            label: str | None = None,
                            direction: str = "bidirectional") -> None:
        # input_ids are sorted by idx (source=0, targets=1..N).
        # IMPL: source→target pairs only (skip target↔target).
        # NAND mutual (default): all pairwise combinations (mutual
        # contradiction — at-most-one-true over all members).
        # NAND one-way (agent-chosen directed attack): source→each-target
        # attacks ONLY — target↔target pairwise edges would otherwise create
        # arbitrary directed attacks between targets (review P2, #795).
        #
        # #7852 — direction is the Layer-1 four-value vocabulary (with the two
        # legacy aliases): `->`/`unidirectional` flows source→target, `<-`
        # target→source, `<->`/`bidirectional` both, `-` NEITHER. `direction_flows`
        # is the ONE normalizer; a `-` factor contributes nothing (never a
        # silent mutual default).
        n = len(input_ids)
        if n < 2:
            return
        fwd, bwd = direction_flows(direction)
        mutual = fwd and bwd
        if not (fwd or bwd):
            # `-`: the connection exists and NO confidence transfers.
            return
        if op_type == "IMPL":
            pairs = [(input_ids[0], input_ids[j]) for j in range(1, n)]
        elif op_type == "NAND" and not mutual:
            # Directed NAND: the source (and, for `<-`, the receiving end)
            # pairs with each target only; targets do not attack each other.
            pairs = [(input_ids[0], input_ids[j]) for j in range(1, n)]
        else:
            pairs = [(input_ids[i], input_ids[j])
                     for i in range(n) for j in range(i + 1, n)]
        if not pairs:
            return

        # Accumulate-then-scale (#326). The operator carries ONE weight w:
        # the factor's TOTAL pull must equal the isolated 2-input factor's
        # pull at the same weight, and the decomposition must be
        # input-order-invariant (design decision recorded in #326; the
        # #420/#536 falsification suite pins it).
        #
        # Every pairwise application is computed from a CLEAN cavity state
        # (the pre-factor messages for THIS operator are restored between
        # pairs, and damping is temporarily disabled so raw messages are
        # captured), so each pair contributes identically regardless of
        # input order. Per-claim contributions are summed (accumulate) and
        # the factor is scaled once (scale) to conserve total pull.
        cache = getattr(self, "_msg_cache", None)
        saved = dict(cache) if cache is not None else {}
        # Overlay cache: only THIS operator's (op, claim, rel) keys matter
        # to the pairwise computations, so copying just those keys between
        # pairs is O(arity) instead of O(|cache|).
        overlay_base = {k: v for k, v in saved.items()
                        if k[0] == op_id and k[2] == op_type}
        orig_damping = self.damping
        acc: dict[str, tuple[float, float]] = {c: (0.0, 0.0) for c in input_ids}
        touched: set[str] = set()
        try:
            self.damping = 1.0  # capture raw (undamped) pair messages
            for a, b in pairs:
                self._msg_cache = dict(overlay_base)  # clean per-pair state
                self._update_factor(op_id, op_type, [a, b], weight, label, direction)
                # _update_factor persists the messages this direction flows:
                # `a` (the pair's source slot) for backward flow, `b` (the
                # target slot) for forward flow, both for mutual, neither for
                # `-`. Accumulate ONLY freshly written claims — never the
                # pre-factor state (a stale message must not be re-accumulated
                # as if fresh, #326 directed-path corruption).
                for c in (a, b):
                    if c == a and not bwd:
                        continue
                    if c == b and not fwd:
                        continue
                    msg = self._msg_cache.get((op_id, c, op_type))
                    if msg is not None:
                        ea, eb = acc[c]
                        acc[c] = (ea + msg[0], eb + msg[1])
                        touched.add(c)
        finally:
            if cache is None:
                # Cache-less entry: the attribute did not pre-exist — remove
                # it again so graph-mode callers (which use hasattr(_msg_cache)
                # to select graph I/O) keep working.
                if hasattr(self, "_msg_cache"):
                    delattr(self, "_msg_cache")
            else:
                self._msg_cache = cache
            self.damping = orig_damping

        # Conservation scale: the FULL pairwise (clique) topology — NAND
        # bidirectional — has C(n,2) pair applications with each claim touched
        # by (n-1) of them -> scale 2/(n(n-1)) makes the total
        # n*(n-1)*pair*scale = 2*pair = the binary factor's total. The STAR
        # topology (IMPL source→targets, and directed NAND attacks) has (n-1)
        # pairs -> scale 1/(n-1) makes the total pair_tgt (directed: targets
        # only) or pair_src + pair_tgt (bidirectional IMPL: source back-message
        # + each target), matching the binary factor's total at the same
        # weight. Star targets are diluted 1/(n-1) (evidence dilution across
        # siblings).
        is_star = (op_type != "NAND") or not mutual
        scale = (1.0 / (n - 1)) if is_star else (2.0 / (n * (n - 1)))
        d = self.damping
        for c in input_ids:
            if c not in touched:
                # An endpoint this direction does NOT reach gets no message by
                # design — clear any stale persisted message so the direction
                # holds. For a star, the non-receiving end is the source for
                # `->` and every target for `<-`; for `-` nothing receives.
                # Cache mode: only when a stale entry exists (don't create a
                # zero entry for a never-messaged endpoint). Graph mode: the
                # MATCH…SET is a no-op when no edge exists, so write always.
                receives = bwd if c == input_ids[0] else fwd
                if not receives:  # noqa: SIM102
                    if cache is None or saved.get((op_id, c, op_type)) is not None:
                        self._write_message(op_id, c, 0.0, 0.0, op_type)
                continue
            ea, eb = acc[c]
            new_eta = (ea * scale, eb * scale)
            new_eta = (max(min(new_eta[0], 1000), -1000),
                       max(min(new_eta[1], 1000), -1000))
            if cache is not None:
                old_eta = saved.get((op_id, c, op_type), (0.0, 0.0))
                damped = (d * new_eta[0] + (1 - d) * old_eta[0],
                          d * new_eta[1] + (1 - d) * old_eta[1])
            else:
                damped = new_eta  # graph mode: no damping base available
            self._write_message(op_id, c, *damped, op_type)

    def _update_claim_posterior(self, claim_id: str,
                                 run_evidence: dict | None = None) -> None:
        # Start from evidence prior in natural parameter space.
        # #330: consume the run-scoped evidence (constructor + run-level),
        # never the instance dict directly.
        ev = (run_evidence or self._evidence).get(claim_id)
        if ev:
            total_eta1, total_eta2 = ev[0] - 1.0, ev[1] - 1.0
        else:
            total_eta1, total_eta2 = 0.0, 0.0
        if hasattr(self, '_msg_cache'):
            for (oid, cid, rel), (ma, mb) in self._msg_cache.items():  # noqa: B007
                if cid == claim_id:
                    total_eta1 += ma
                    total_eta2 += mb
            # Operator-less direct edges (#888 W5): the back-message of the
            # claim's OWN outgoing direct edges also bears on its posterior
            # (it is the factor's message to the source endpoint).
            if hasattr(self, '_back_cache'):
                for (src, tgt, rel), (ma, mb) in self._back_cache.items():  # noqa: B007
                    if src == claim_id:
                        total_eta1 += ma
                        total_eta2 += mb
        else:
            for rel in ("IMPL", "NAND"):
                rows = self.g.query(
                    f"MATCH (o:Point)-[r:{rel}]->(c) "
                    f"WHERE {epistemic_disjunction('c')} AND c.id = $cid "
                    "RETURN coalesce(r.msg_alpha,0.0), coalesce(r.msg_beta,0.0)",
                    params={"cid": claim_id},
                ).result_set
                for ma, mb in rows:
                    total_eta1 += float(ma)
                    total_eta2 += float(mb)
                rows = self.g.query(
                    f"MATCH (a:Point)-[r:{rel}]->(b) "
                    "WHERE a.id = $cid "
                    f"AND {epistemic_disjunction('b')} "
                    "AND coalesce(r.direction, 'bidirectional') IN "
                    "['bidirectional', '<->', '<-'] "
                    "RETURN coalesce(r.back_msg_alpha,0.0), coalesce(r.back_msg_beta,0.0)",
                    params={"cid": claim_id},
                ).result_set
                for ma, mb in rows:
                    total_eta1 += float(ma)
                    total_eta2 += float(mb)
        alpha, beta = self._beta_from_natural(total_eta1, total_eta2)
        self._write_node(claim_id, alpha, beta)

    # ── Affected subgraph extraction ──────────────────────────────

    def _affected_claims(self, operator_ids: list[str],
                         max_hops: int | None = 2,
                         include_draft: bool = False) -> set[str]:
        """Affected claims for a run, seeded from operator and/or point ids.

        Seeds may be operator ids (legacy) or plain point ids (#888 W5): a
        plain-point seed discovers operator-less direct edges in both
        directions (an operator-less edge is a factor shared by BOTH of its
        endpoints) AND its operator-mediated neighborhood, so seeding a claim
        runs every factor it participates in.

        max_hops: expansion depth. int k = k BFS hops (current semantics);
        None = unbounded — expand until the frontier is empty (the full
        connected subgraph from the seeds, #395 delta A). The unbounded
        regime carries a degeneration guard (per-hop frontier cap + affected
        ≈ full-graph detection) so the interactive path never expands
        unboundedly; when the guard fires, `self._last_truncated` is set and
        the collected (truncated) set is returned.

        With include_draft=False (default, #780): draft target claims are
        excluded, draft operator nodes are skipped as sources, and the BFS
        expansion never hops through draft claims — a draft-connected
        operator must change NO live claim's posterior.

        Membership contract (#5566): every claim admitted here is a
        FACTOR PARTICIPANT. Operator-mediated admissions are operator
        INPUTS — the targets of an operator's outgoing `IMPL`/`NAND` edge,
        the direction `_affected_factors` reads; direct-edge admissions are
        the endpoints of an operator-less `IMPL`/`NAND` edge (#888 W5). A
        structural predicate (`related`, `aboutSubject`, `memberOf`, the
        §3.9 set) or a reverse-only `IMPL` such as the mitigation back-link
        `(m)-[:IMPL]->(op)` forms no factor, so it must never admit a claim:
        otherwise `_update_claim_posterior` would recompute that node from
        empty natural parameters as `Beta(1,1)` and discard its persisted
        prior. This is the neutral-link rule (#5025, ONTOLOGY §3.9/§8).
        KNOWN EXCEPTION, not fixed here: a factor with fewer than 2
        PARTICIPATING inputs (draft/terminal inputs are stripped before
        `_update_factor`, and a unidirectional operator whose source was
        stripped is skipped entirely) no-ops, so an admitted input can still
        reach `_update_claim_posterior` with no message and be recomputed as
        `Beta(1,1)` — #5566's second half, owner-reserved (belief model,
        DECISION-LEDGER §22).
        """
        live_c = _live_only("c.status", include_draft)
        live_o = _live_only("o.status", include_draft)
        live_a = _live_only("a.status", include_draft)
        live_b = _live_only("b.status", include_draft)
        affected: set[str] = set()
        # #7902: resolve the seeds' labels in ONE batched sweep per epistemic
        # label, then BATCH both expansion queries per label — a per-seed loop
        # costs ~3 queries per dirty root, which the #395 interactive-latency
        # budget cannot afford on a 40-claim zone.
        #
        # Every DISTINCT seed must expand. Its pre-BFS 1-hop neighbourhood
        # (operator-less direct edges + `_live_neighbors`) is reach that
        # consumes no BFS hop, so skipping a seed that an earlier seed merely
        # REACHED drops a level the `max_hops`-capped BFS cannot make up —
        # `affected` would become a strict subset of `_window_closure`
        # (#6597: coverage 0.75-0.875 on a direct chain). Batching, not
        # skipping, is what buys the budget back.
        seed_ids = list(dict.fromkeys(operator_ids))
        seed_meta: dict[str, tuple[str, bool, str | None]] = {}
        unresolved = list(seed_ids)
        for _label in EPISTEMIC_LABELS:
            if not unresolved:
                break
            # A node carries exactly one epistemic label, so the first label
            # in EPISTEMIC_LABELS order that resolves wins — the same
            # stop-at-first result as the per-seed sweep.
            # #6211: the whole SEED PHASE is hop 0 and is typed: these queries'
            # operand is the caller's full seed set (2,073 ids in the issue's
            # measurement), so they are the FIRST place a dense graph aborts on
            # the engine's query timeout — before the BFS loop makes a hop.
            rows = self._hop_query(
                f"MATCH (n:{_label}) WHERE n.id IN $ids "
                "RETURN n.id, (n.is_operator = true), n.status",
                {"ids": unresolved}, hop=0,
                phase="seed_lookup", frontier_size=len(unresolved),
            )
            for sid, is_op, status in rows:
                seed_meta[sid] = (_label, bool(is_op), status)
            unresolved = [s for s in unresolved if s not in seed_meta]
        # A seed that resolved to no epistemic label contributes nothing,
        # matching the pre-widening Point probe.

        op_seeds = [s for s in seed_ids if seed_meta.get(s, ("", False, None))[1]]
        plain_by_label: dict[str, list[str]] = {}
        for s in seed_ids:
            meta = seed_meta.get(s)
            if meta is None or meta[1]:
                continue
            plain_by_label.setdefault(meta[0], []).append(s)

        if op_seeds:
            # Operator seeds — legacy behavior: follow outgoing edges to the
            # operator's inputs (the operator's factor). Draft operators and
            # draft target claims are excluded (#780). #7853: the input may
            # be any epistemic label.
            conds = [epistemic_disjunction("c")]
            if live_c:
                conds.append(live_c)
            if live_o:
                conds.append(live_o)
            rows = self._hop_query(
                "MATCH (o:Point)-[r:IMPL|NAND]->(c) "
                "WHERE o.id IN $oids AND " + " AND ".join(conds) + " "
                "RETURN DISTINCT c.id",
                {"oids": op_seeds}, hop=0,
                phase="seed_operators", frontier_size=len(op_seeds),
            )
            affected.update(r[0] for r in rows)

        for _label, group in plain_by_label.items():
            # Plain seeds (#888 W5): direct edges in BOTH directions (an
            # operator-less edge is a factor shared by its endpoints), plus
            # each seed's operator-mediated neighborhood so a seed whose only
            # connections are operator-mediated still runs its incident
            # factors. Draft endpoints are excluded (#780) — a draft seed runs
            # nothing. #7902: the seed may be any epistemic label, so the
            # resolved label anchors the id index here and narrows the
            # traversal position below.
            conds = ["b.id <> a.id", epistemic_disjunction("b")]
            if live_b:
                conds.append(live_b)
            if live_a:
                conds.append(live_a)
            rows = self._hop_query(
                f"MATCH (a:{_label})-[r:IMPL|NAND]-(b) "
                "WHERE a.id IN $ids AND " + " AND ".join(conds) + " "
                # #3139/#3154: index-independent non-operator predicate
                # (a bare `= false` is emptied by a GRAPH.COPY'd index).
                "AND (b.is_operator IS NULL OR b.is_operator = false) "
                "AND b.op_type IS NULL "
                "RETURN DISTINCT b.id",
                {"ids": group}, hop=0,
                phase="seed_direct_edge", frontier_size=len(group),
            )
            affected.update(r[0] for r in rows)
            live_group = [
                s for s in group
                if not (seed_meta[s][2] == "draft" and not include_draft)
            ]
            if live_group:
                affected.update(self._live_neighbors_many(
                    live_group, include_draft, label=_label))

        if affected and (max_hops is None or max_hops > 0):
            frontier = list(affected)
            hops = 0
            while frontier:
                if max_hops is not None and hops >= max_hops:
                    break
                hops += 1
                # Degeneration guard (#395): in the UNBOUNDED regime, stop
                # expanding before the next hop when the closure is ≈ the
                # whole graph (size bound) or a single hop would explode the
                # frontier on an already-large graph (growth bound). The
                # guard NEVER aborts the run — it proceeds with the collected
                # (truncated) set; the diagnostic travels via _last_truncated.
                # Both bounds are gated on graph SIZE (P3 note): a legitimately
                # dense 10-50 claim zone in a small graph must stay EXACT — a
                # real closure is never truncated.
                if max_hops is None:
                    total = self._graph_claim_count()
                    if (total >= _EP_GUARD_MIN_GRAPH_CLAIMS
                            and len(affected) >= _EP_GUARD_FULL_GRAPH_FRACTION * total):
                        self._last_truncated = True
                        logger.warning(
                            "EP affected-subgraph BFS: %d claims ≈ %.0f%% of "
                            "graph (%d claims) — degenerate full-graph closure, "
                            "truncating expansion (max_hops=None guard, #395).",
                            len(affected), 100.0 * len(affected) / total, total,
                        )
                        break
                # Strip drafts from the frontier BEFORE expanding: a draft
                # claim must never propagate to live claims (#780).
                if not include_draft:
                    draft_ids = self._filter_draft_ids(set(frontier), hop=hops)
                    if draft_ids:
                        affected -= draft_ids
                        frontier = [n for n in frontier if n not in draft_ids]
                        if not frontier:
                            break
                new_frontier: list[str] = []
                if frontier:
                    # Per-hop BATCH neighbor expansion (#395 delta A) — kills
                    # the BFS-time N+1 (2 queries per claim) with 2 queries
                    # per hop, preserving the per-claim semantics exactly:
                    # (1) operator-mediated bridges — operator detection and
                    # draft filters match _live_neighbors (#780: draft
                    # operators never bridge, draft targets never admitted;
                    # the include_draft hatch re-includes drafts — and the
                    # operator-detection predicate is IDENTICAL to
                    # _live_neighbors' under both modes so legacy
                    # op_type-only operators bridge consistently at every
                    # hop), and
                    # (2) operator-less direct edges (#888 W5) with the same
                    # live-endpoint filters.
                    # #5566: an operator-mediated bridge is factor-bearing
                    # only in the operator→input DIRECTION (`_affected_factors`
                    # reads `(op)-[:IMPL|NAND]->(input)`). Both hops are
                    # therefore typed AND directed: a reverse-only `IMPL`
                    # (e.g. the mitigation back-link `(m)-[:IMPL]->(op)`)
                    # forms no factor and must not admit its far endpoint.
                    nbr_rows: list = []
                    # #7853: n is the id-anchored frontier — sweep its labels;
                    # the far endpoint m is widened by traversal.
                    for _qn in epistemic_label_queries(
                        "MATCH (n:{label})<-[r:IMPL|NAND]-(op:Point)"
                        "-[r2:IMPL|NAND]->(m) "
                        "WHERE n.id IN $ids AND m.id <> n.id "
                        f"AND {epistemic_disjunction('m')} "
                        "AND (op.is_operator = true OR op.op_type IS NOT NULL) "
                        f"AND {_live_only('op.status', include_draft)} "
                        f"AND {_live_only('m.status', include_draft)} "
                        "RETURN DISTINCT n.id, m.id"
                    ):
                        nbr_rows.extend(self._hop_query(
                            _qn, {"ids": list(frontier)}, hop=hops,
                            phase="operator_bridge",
                            frontier_size=len(frontier)))
                    for _nid, mid in nbr_rows:
                        if mid not in affected:
                            affected.add(mid)
                            new_frontier.append(mid)
                    # Operator-less hops (#888 W5): direct IMPL/NAND edges
                    # (operator-mediated hops above). #7902/#7926: WIDENED —
                    # the direct-edge WRITE path admits the four epistemic
                    # labels, so this hop must too, and `_affected_factors`
                    # Batch 3 widens in lockstep so the admitted endpoint
                    # stays a factor participant. The hazard #7853 flagged is
                    # the HALF-widened state (no factor → recompute from empty
                    # natural parameters), never the both-sides widening.
                    # Draft endpoints never propagate (#780).
                    conds = ["a.id IN $ids", "b.id <> a.id",
                             epistemic_disjunction("b")]
                    if live_a:
                        conds.append(live_a)
                    if live_b:
                        conds.append(live_b)
                    dir_rows: list = []
                    # #7853: a is the id-anchored frontier — sweep its labels;
                    # b is widened by traversal.
                    for _qa in epistemic_label_queries(
                        "MATCH (a:{label})-[r:IMPL|NAND]-(b) "
                        "WHERE " + " AND ".join(conds) + " "
                        # #3139/#3154: index-independent non-operator form.
                        "AND (a.is_operator IS NULL OR a.is_operator = false) "
                        "AND a.op_type IS NULL "
                        "AND (b.is_operator IS NULL OR b.is_operator = false) "
                        "AND b.op_type IS NULL "
                        "RETURN DISTINCT a.id, b.id"
                    ):
                        dir_rows.extend(self._hop_query(
                            _qa, {"ids": list(frontier)}, hop=hops,
                            phase="direct_edge",
                            frontier_size=len(frontier)))
                    for _aid, bid in dir_rows:
                        if bid not in affected:
                            affected.add(bid)
                            new_frontier.append(bid)
                # Growth bound (max_hops=None only): a single hop producing
                # more than the frontier cap while the graph is already large
                # is explosive-blowup risk, not a legitimately dense zone
                # (a real 10-50 claim hub zone never reaches the cap).
                if (max_hops is None
                        and len(new_frontier) > _EP_GUARD_FRONTIER_CAP):
                    total_graph_claims = self._graph_claim_count()
                    if total_graph_claims >= _EP_GUARD_MIN_GRAPH_CLAIMS:
                        self._last_truncated = True
                        logger.warning(
                            "EP affected-subgraph BFS: frontier of %d new claims "
                            "exceeds cap %d on a %d-claim graph — truncating "
                            "expansion (max_hops=None guard, #395).",
                            len(new_frontier), _EP_GUARD_FRONTIER_CAP,
                            total_graph_claims,
                        )
                        break
                frontier = new_frontier
                if not frontier:
                    break
        # Final strip: FAIL-SAFE only — every admission point into `affected`
        # already excludes drafts (seed predicates, _live_neighbors, direct-edge
        # filters); the strip guards the nuclear-risk invariant against future
        # admission-point regressions (#943 review).
        if not include_draft and affected:
            affected -= self._filter_draft_ids(affected)
        return affected

    def _graph_claim_count(self) -> int:
        """Claim count in the graph (degeneration-guard sizing, #395).

        Counts CLAIMS only — epistemic nodes (:Point, :Subject, :Object,
        :Event — #7853) with is_operator != true AND op_type IS
        NULL (the projection layer's canonical operator test is
        bool(is_operator or op_type), projection/__init__.py). The guards
        compare this against ``len(affected)``, which holds CLAIM ids, so
        counting all epistemic nodes (claims + operators) made guard A fire at
        50% of nodes on operator-sparse graphs while the component was far
        from fully collected, truncating a genuine closure mid-BFS (#395 code
        review, PR #1273).

        The bound is graph-size-relative: it only gates once the graph has
        enough claims to make a whole-graph closure a cost problem
        (>= _EP_GUARD_MIN_GRAPH_CLAIMS), and a legitimately dense 10-50
        claim zone in a small graph always stays exact. One count query,
        called only in the max_hops=None regime.

        #7853 — this is DELIBERATELY the one epistemic read that is NOT an
        ``epistemic_label_queries`` sweep. It has no id anchor to index
        (``live.py``'s per-label rule is about id-anchored reads), and it must
        count each node ONCE: a node can carry BOTH ``:Point`` and ``:Object``
        (the entity/version-tracking shape — ``sdk.update_point`` probes
        ``MATCH (n:Point:Object {id:$id})``), so SUMMING per-label counts would
        count it twice. An inflated denominator makes the degeneration guard
        fire early and TRUNCATE a genuine closure — which changes the
        posteriors, not merely the cost. The single disjunction query is the
        only form that counts distinct nodes without shipping every id back,
        and one whole-graph count per unbounded-regime hop is the accepted
        price of the widening.

        #6211: typed like every other query of the pass. It counts the WHOLE
        graph, so on a large graph it is not cheap and can be the query the
        engine aborts — pass-level (``hop=None``): there is no hop frontier it
        walks.
        """
        rows = self._hop_query(
            "MATCH (n) "
            f"WHERE {epistemic_disjunction('n')} "
            "AND (n.is_operator IS NULL OR n.is_operator = false) "
            "AND n.op_type IS NULL "
            "RETURN count(n)",
            {}, hop=None, phase="claim_count", frontier_size=None,
        )
        return int(rows[0][0]) if rows and rows[0][0] is not None else 0

    def _filter_draft_ids(self, ids: set[str],
                          hop: int | None = None) -> set[str]:
        """Return the subset of ids whose nodes have status == 'draft' (#780).

        #6211: typed. ``hop`` is the hop that asked (the loop's frontier) or
        ``None`` for the pass-level strips (the seed phase and the final
        fail-safe sweep over the whole affected set).
        """
        if not ids:
            return set()
        rows: list = []
        # #7853: id-anchored — sweep the epistemic labels.
        for _q in epistemic_label_queries(
            "MATCH (n:{label}) WHERE n.id IN $ids AND n.status = 'draft' "
            "RETURN n.id"
        ):
            rows.extend(self._hop_query(
                _q, {"ids": list(ids)}, hop=hop, phase="draft_filter",
                frontier_size=len(ids),
            ))
        return {r[0] for r in rows}

    def _live_neighbors_many(self, node_ids: list[str], include_draft: bool,
                             label: str) -> list[str]:
        """Operator-mediated neighborhood hop that never crosses drafts (#780).

        Operator-mediated neighborhood hop for the affected-set expansion —
        the same typed-and-directed narrowing as `_affected_claims` (#5566) —
        with draft exclusion: it excludes hops THROUGH draft operator nodes
        and TO draft endpoints, because a draft-connected operator must change
        NO live claim's posterior, so the affected-set expansion must not
        reach live claims via a draft bridge.

        Only an operator's INPUTS are traversed (#5566): the hop is typed
        `IMPL|NAND` AND directed `(input)<-(op)->(input)`. A structural
        predicate onto an operator is not a belief path, and neither is a
        reverse-only `IMPL` (the mitigation back-link `(m)-[:IMPL]->(op)`) —
        neither forms a factor, so neither may admit its far endpoint into the
        affected set.

        ``node_ids`` (#7902 perf): the BATCHED form. The whole seed group is
        expanded in ONE query per label instead of one query per seed — a
        per-seed loop costs the #395 interactive-latency budget N queries on
        an N-dirty-root zone, and it is also why the expansions are batched
        rather than SKIPPED: skipping a seed an earlier seed merely REACHED
        would drop its neighbours from the pre-BFS set (#6597).

        ``label`` (#7902 perf): the nodes' already-resolved epistemic label —
        the hop runs ONCE on it. The caller resolves the label when it builds
        the seed group, so there is no four-label sweep here.
        """
        if not node_ids:
            return []
        # #7853/#7902: m (the far endpoint) may be any epistemic label —
        # widened by traversal. The id-anchored n uses the resolved label when
        # known, else sweeps the four labels.
        template = (
            "MATCH (n:{label})<-[r:IMPL|NAND]-(op:Point)"
            "-[r2:IMPL|NAND]->(m) "
            "WHERE n.id IN $ids AND m.id <> n.id "
            f"AND {epistemic_disjunction('m')} "
            "AND (op.is_operator = true OR op.op_type IS NOT NULL) "
            f"AND {_live_only('op.status', include_draft)} "
            f"AND {_live_only('m.status', include_draft)} "
            "RETURN DISTINCT m.id"
        )
        queries = [template.replace("{label}", label)]
        rows: list = []
        for _q in queries:
            # #6211: this is the SEED neighbourhood — the expansion that runs
            # before the BFS loop makes its first hop (hop index 0). It is the
            # same two-hop pattern, so it is the same failure: on a dense graph
            # it is the FIRST query that can exceed the engine's query timeout.
            rows.extend(self._hop_query(
                _q, {"ids": list(node_ids)}, hop=0,
                phase="seed_neighbourhood", frontier_size=len(node_ids)))
        return [r[0] for r in rows]

    def _affected_factors(self, affected_claims: set[str],
                          include_draft: bool = False,
                          skipped: list[dict] | None = None
                          ) -> list[tuple[str, str, list[str], float, str | None, str]]:
        """Extract EP factors from the affected claims subgraph.

        Three batch queries replace the original per-claim N+1 pattern (#400 follow-up):
        1. Single query for all operators connected to any affected claim.
        2. Single query for all inputs of those operators, ordered by idx.
        3. Single query for operator-less direct edges between plain Points
           (binary factors keyed on the source endpoint, #888 W5).

        Semantics are preserved: same affected set, same factor list, same
        ordering guarantees (source idx=0 first for directional IMPL).

        With include_draft=False (default, #780): draft operators never feed
        factors, draft ids are stripped from input_ids, and draft endpoints
        never form direct-edge factors. An operator whose live inputs drop
        below 2 becomes degenerate and is skipped — the SVBP-path convention.

        ``skipped`` (#6105) is an optional SINK: every factor this call drops
        (degenerate, or a directional operator whose idx-0 source was
        stripped) appends one structured record to it — ``operator_id``,
        ``op_type``, ``reason``, ``participating_inputs`` and every input with
        its status. ``run()`` passes ``self._last_skipped_factors`` so the run
        carries the diagnostic; the read-only scope callers pass nothing and
        stay side-effect free.
        """
        live_o = _live_only("o.status", include_draft)
        live_c = _live_only("c.status", include_draft)
        live_a = _live_only("a.status", include_draft)
        live_b = _live_only("b.status", include_draft)
        factors: list[tuple[str, str, list[str], float, str | None, str]] = []
        if not affected_claims:
            return factors

        from .weights import compute_operator_weight, NAND_BASE_WEIGHT  # noqa: I001

        # Batch 1: all operators connected to any affected claim. An operator
        # is a Point with is_operator=true OR an op_type (legacy nodes — the
        # projection layer treats bool(is_operator or op_type) as operator,
        # projection/__init__.py). Plain-point sources of IMPL/NAND edges are
        # operator-less direct edges (#888 W5) handled by Batch 3 below.
        # (Pre-#910 graphs are unaffected: plain points had no outgoing
        # IMPL/NAND edges, so the exclusion is behavior-neutral there.)
        # Draft operators are excluded (#780).
        op_info: dict[str, tuple[str, str | None, str | None]] = {}
        where_b1 = " AND ".join(
            ["(o.is_operator = true OR o.op_type IS NOT NULL)",
             "c.id IN $ids"] + ([live_o] if live_o else []) + ([live_c] if live_c else [])
        )
        rows: list = []
        # #7853: c is the affected claim (id-filtered) — id-anchored sweep.
        for _q in epistemic_label_queries(
            "MATCH (o:Point)-[r:IMPL|NAND]->(c:{label}) "
            f"WHERE {where_b1} "
            "RETURN DISTINCT o.id, o.op_type, o.label, o.direction"
        ):
            rows.extend(self._hop_query(
                _q, {"ids": list(affected_claims)}, hop=None,
                phase="factor_extraction", frontier_size=len(affected_claims),
            ))
        for op_id, op_type, label, direction in rows:
            if op_id not in op_info:
                op_info[op_id] = (op_type, label, direction)

        # Batch 3: operator-less direct edges (#888 W5, ONTOLOGY v3.5 §8
        # reification rule). A direct IMPL/NAND edge between two plain Points
        # (no is_operator, no op_type) is itself a binary factor: the source
        # endpoint plays the factor's role, messages live ON the edge
        # (forward: r.msg_alpha/r.msg_beta; back: r.back_msg_*), and
        # _update_factor computes the forward message from the source's belief
        # (its cavity, with no self-edge message) exactly as for an
        # operator-mediated factor. DIRECTION is read from the EDGE
        # (r.direction), defaulting to bidirectional when absent — the
        # operator node's direction applies only when an operator exists
        # (Batch 1 above). Weight parity with operator-mediated factors: an
        # explicit r.weight wins; otherwise the operator base weight applies
        # (NAND_BASE_WEIGHT — #855 — for NAND, 1.0 for IMPL). Parallel direct
        # edges between the same pair share one message slot (last-writer
        # wins) — unsupported; creation paths must not duplicate edges.
        dir_rows = self._hop_query(
            "MATCH (a)-[r:IMPL|NAND]->(b) "
            # #7902: both endpoints of a direct edge may carry any of the four
            # epistemic labels (the write path was widened with the same
            # helper). Traversal positions — the label disjunction goes in the
            # WHERE clause (FalkorDB rejects pattern-position disjunction).
            f"WHERE {epistemic_disjunction('a')} "
            f"AND {epistemic_disjunction('b')} "
            # #3139/#3154: index-independent non-operator form — a bare
            # `= false` drops every direct-edge factor on a GRAPH.COPY'd
            # graph (Batch 3 silently returns ∅).
            "AND (a.is_operator IS NULL OR a.is_operator = false) "
            "AND a.op_type IS NULL "
            "AND (b.is_operator IS NULL OR b.is_operator = false) "
            "AND b.op_type IS NULL "
            "AND (a.id IN $ids OR b.id IN $ids) "
            f"{('AND ' + live_a + ' AND ' + live_b + ' ') if live_a else ''}"
            "RETURN a.id, b.id, type(r), coalesce(r.direction, 'bidirectional'), "
            "       r.weight",
            {"ids": list(affected_claims)}, hop=None,
            phase="factor_extraction", frontier_size=len(affected_claims),
        )
        for src_id, tgt_id, rel, direction, weight in dir_rows:
            if weight is None:
                weight = (NAND_BASE_WEIGHT if rel == "NAND" else 1.0)
            factors.append((src_id, rel, [src_id, tgt_id], float(weight),
                            None, direction))

        if not op_info:
            return factors

        # Batch 2: all inputs for all discovered operators, ordered by idx.
        # ORDER BY r.idx ensures source (idx=0) comes first — required
        # for directional IMPL: id_a = source, id_b = target so that
        # back-messages are correctly skipped. Draft inputs are stripped
        # IN PYTHON (#780): a draft claim contributes NO belief to a live
        # factor, but the unfiltered list is needed to (a) detect when the
        # idx-0 SOURCE was a draft — renumbering a live target into the
        # source slot would invert directional semantics — and (b)
        # distinguish draft-caused degradation from genuinely unary
        # operators (pre-existing behavior: _update_factor no-ops <2 inputs).
        op_inputs: dict[str, list[str]] = {op_id: [] for op_id in op_info}
        op_input_live: dict[str, list[bool]] = {op_id: [] for op_id in op_info}
        # Raw input statuses for diagnostic surfacing (#992): when an operator
        # goes degenerate we name every input's status (incl. the legacy
        # outdated=true flag — #2422 review: a flag-dead input shows status
        # 'live' but is EP-dead, so the display must distinguish flag-dead)
        # so the silent-confidence-zero is traceable to the offending inputs.
        op_input_status: dict[str, list[str | None]] = {op_id: [] for op_id in op_info}
        # idx_known flags operators whose EVERY input edge carries an idx —
        # create_operator always writes idx (source=0 first). Legacy/migrated
        # operators may have idx-less edges (all coalesce to 0): position 0
        # is then NOT provably the source, so the directional source-slot
        # guard must not fire (it could check the wrong slot — #943 review).
        op_idx_known: dict[str, bool] = {op_id: True for op_id in op_info}
        rows = self._hop_query(
            "MATCH (o:Point)-[r:IMPL|NAND]->(c) "
            "WHERE o.id IN $ids "
            f"AND {epistemic_disjunction('c')} "
            "RETURN o.id, c.id, r.idx, c.status, coalesce(c.outdated, false) "
            "ORDER BY coalesce(r.idx, 0), c.id",
            {"ids": list(op_info.keys())}, hop=None,
            phase="factor_extraction", frontier_size=len(op_info),
        )
        for op_id, claim_id, idx, status, outdated in rows:
            op_inputs[op_id].append(claim_id)
            # Participation is UNCONDITIONAL on status (#2422): a terminal
            # input (retracted / superseded / outdated / archived /
            # deprecated status — the SAME vocabulary as the Cypher
            # predicate, derived from TERMINAL_EXCLUDED_STATUSES so the two
            # cannot drift) or the legacy outdated=true flag (which
            # invalidate_point writes without touching status) is DEAD for EP
            # and NEVER participates — include_draft=True re-includes DRAFTS
            # only (the #780 escape hatch), never terminal points. A None
            # status = legacy live node.
            is_terminal = status in TERMINAL_EXCLUDED_STATUSES or bool(outdated)
            participates = not is_terminal and (status != "draft" or include_draft)
            op_input_live[op_id].append(participates)
            # Display: status + flag marker so the #992 diagnostic names a
            # flag-dead input accurately (status 'live' + outdated flag shows
            # as "live(outdated)").
            op_input_status[op_id].append(
                f"{status or 'live'}(outdated)" if outdated else (status or 'live')
            )
            if idx is None:
                op_idx_known[op_id] = False

        # Assemble factors (single compute_operator_weight per operator).
        for op_id, (op_type, label, direction) in op_info.items():
            # Defensive: pre-migration operators without direction default to bidirectional
            if direction is None:
                direction = "bidirectional"
                logger.warning(
                    "Operator %s has no direction property — defaulting to 'bidirectional'. "
                    "Run graph-scripts/migrate_direction.py to backfill.",
                    op_id,
                )
            full_inputs = op_inputs.get(op_id, [])
            # Always filter to participating inputs. Drafts are stripped only
            # under the default (include_draft=False, #780); terminal inputs
            # are stripped under BOTH modes (#2422 — a terminal claim's ghost
            # must not vote even on the include_draft escape-hatch path).
            input_ids = [
                cid for cid, live in zip(full_inputs, op_input_live[op_id])  # noqa: B905
                if live
            ]
            stripped = len(full_inputs) - len(input_ids)
            if stripped:
                skip_reason = None
                if (not all(direction_flows(direction))
                        and op_idx_known[op_id]
                        and full_inputs
                        and not op_input_live[op_id][0]):
                    # The idx-0 SOURCE was stripped (a draft under the
                    # default, or a terminal point under either mode) —
                    # keeping this factor would renumber a live target into
                    # the source slot and invert directional semantics
                    # (#780 review-fix; #2422 extends to terminal sources).
                    logger.warning(
                        "Operator %s: source stripped — factor skipped "
                        "(non-bidirectional with stripped source, #780/#2422)",
                        op_id,
                    )
                    skip_reason = "source_stripped"
                elif len(input_ids) < 2 <= len(full_inputs):
                    # Degradation below 2 participating inputs — the factor
                    # must change NO live posterior (#780); matches the SVBP-
                    # path convention. Name every input's status so the
                    # silent zero-confidence is traceable (#992).
                    logger.warning(
                        "Operator %s: %d/%d inputs stripped — factor skipped "
                        "(degenerate, #780/#2422). Inputs: [%s]",
                        op_id, stripped, len(full_inputs),
                        ", ".join(
                            f"{cid.split('-')[-1]}={s}"
                            for cid, s in zip(full_inputs, op_input_status[op_id])  # noqa: B905
                        ),
                    )
                    skip_reason = "degenerate"
                if skip_reason is not None:
                    # #6105: the run's dropped factors are reported, not just
                    # logged — see _last_skipped_factors.
                    if skipped is not None:
                        skipped.append({
                            "operator_id": op_id,
                            "op_type": op_type,
                            "reason": skip_reason,
                            "participating_inputs": len(input_ids),
                            "inputs": [
                                {"id": cid, "status": st}
                                for cid, st in zip(
                                    full_inputs, op_input_status[op_id], strict=True)
                            ],
                        })
                    continue
            weight = compute_operator_weight(self.proj, op_id)
            factors.append((op_id, op_type, input_ids, weight, label, direction))
        return factors

    # ── Calibration ──────────────────────────────────────────────

    @staticmethod
    def confidence_to_prior(confidence: float, k: float = 2.0,
                            uniform_threshold: float = 0.05) -> tuple[float, float]:
        """Convert extractor confidence (0-1) to Beta(α, β) prior.

        Extractor confidence 0.8 → Beta(1+0.8k, 1+0.2k).
        Confidence near 0.5 → Beta(1,1) uniform (no information).

        Malformed confidence (None, bool, non-numeric, NaN/±inf) falls back
        to the uniform Beta(1,1) — never a NaN/degenerate prior that would
        silently zero downstream weights (#326). Out-of-range values are
        clamped to [0,1] before conversion.

        Args:
            confidence: extractor confidence in [0, 1]
            k: evidence strength multiplier (default 2 = mild prior)
            uniform_threshold: |c-0.5| < threshold → uniform prior
        """
        if confidence is None or isinstance(confidence, bool):
            return (1.0, 1.0)
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            return (1.0, 1.0)
        if not math.isfinite(conf):
            return (1.0, 1.0)
        conf = max(0.0, min(1.0, conf))
        if abs(conf - 0.5) < uniform_threshold:
            return (1.0, 1.0)
        return (1.0 + conf * k, 1.0 + (1.0 - conf) * k)

    # ── Public API ────────────────────────────────────────────────

    def compute_confidence(self, claim_id: str) -> dict:
        a, b = self._read_node(claim_id)
        total = a + b
        if total <= 0:
            # #330: degenerate stored params (0,0) — uniform Beta(1,1) fallback
            # instead of ZeroDivisionError.
            return {"mean": 0.5, "variance": 1/12,
                    "alpha": a, "beta": b, "effective_n": 0}
        return {
            "mean": a / total,
            "variance": (a * b) / (total * total * (total + 1)),
            "alpha": a, "beta": b, "effective_n": total,
        }

    def get_contested_claims(self, variance_threshold: float = 0.04) -> list[dict]:
        # #2490: terminal claims (status in the terminal vocab OR the legacy
        # outdated flag) are EXCLUDED — their posterior decays to vacuity at
        # the terminalizing write, so include-terminal surfaces must not list
        # them as contested.
        #
        # #3415: PRIOR-ONLY baselines are excluded too. A #2199 declared
        # baseline (``baseline_set=true``, no ``posterior_alpha``) is the
        # canonical 'baseline' state of ``ep_measurement_state`` — its
        # variance is the WIDTH OF THE DECLARED PRIOR, not a measured
        # contention, and it reads ``contested=False`` on every other
        # canonical surface (annotate_ep_batch / why / rankers /
        # ``sdk._review_prune`` — of which annotate_ep_batch and why are
        # themselves ``:Point``-anchored, so for an ENTITY the agreement below
        # is untestable; see the per-label gate note further down). Applying
        # the 0.04 posterior bar to a prior
        # width listed the same node as contested here while those four read
        # it unmeasured — the residual #2206/#3276-class disagreement THIS
        # gate closes (an explicitly declared prior wider than the bar, e.g.
        # ``set_point_baseline(2, 1)`` Beta(2,1) v=0.0556, used to surface;
        # the system default Beta(3,1) v=0.0375 stays under the bar either
        # way — and since #6151 an authored word is one weak body on top of
        # that default, so ``credibility='low'`` is now Beta(3.1,1)
        # v=0.03616, also under the bar).
        #
        # Deliberately STILL NO measured gate: a genuinely UNMEASURED live
        # claim (no persisted prior at all -> coalesce to Beta(1,1), v=1/12)
        # MUST list (test_agent_ops_supersede:169 pin — the post-supersede
        # unmeasured successor surfaces until a successful dream resolves
        # it). Only the declared-baseline state is removed; ``unmeasured``
        # keeps its deliberate listing. See ``sdk._review_prune`` for the
        # sibling scan that ALSO requires measurement.
        from .search_engine import ep_measured_cypher, ep_prior_only_cypher
        # #7927: ``n`` is the SUBJECT of this scan — contested claims are
        # enumerated GRAPH-WIDE (no id anchor to index), so the labels come
        # from the shared ``live.EPISTEMIC_LABELS`` declaration and one
        # label-anchored scan runs per label (each label keeps its own scan
        # instead of degenerating into a bare all-node walk). Never a
        # hand-spelled label literal.
        #
        # ``is_operator`` must admit UNSET as well as false — a
        # :Subject/:Object/:Event carries no ``is_operator`` at all, so a bare
        # ``= false`` drops every non-Point belief by NULL rather than by the
        # predicate it states.
        #
        # PER-LABEL PERSISTED-POSTERIOR GATE. An earlier version of this note
        # OVERCLAIMED it — re-measure before trusting any sentence here.
        # The "unmeasured live claim MUST list" rule (the :169 pin) is the CLAIM
        # rule and stays exactly as it was for ``:Point``. Entity labels differ
        # in ONE respect: ``create_entity`` rejects belief props, so a
        # STRUCTURALLY-ONLY entity has no persisted prior at all and its
        # Beta(1,1) fallback sits at the MAXIMUM possible variance
        # (1/12 = 0.0833) — every such node on the graph would list, forever, at
        # the top of a variance-descending report. So an entity lists only once
        # SOME posterior was persisted on it.
        #
        # WHAT THIS GATE IS, STATED BECAUSE AN EARLIER VERSION OF THIS COMMENT
        # OVERCLAIMED IT: it separates "wired into at least one factor" from
        # "structurally orphaned" — NOT "informative" from "uninformative".
        # The claim that "nothing would ever measure them" is FALSE since #7902
        # widened the EP factor extraction: a non-Point endpoint of a factor is
        # measured, which ``test_7902_write_path_label_widening::
        # test_operator_non_point_input_is_ep_live`` asserts by reading the
        # neighbour's posterior back — and ``_affected_claims`` resolves a
        # non-Point SEED too, so an entity reached only as a seed is measured as
        # well. A
        # measured-but-evidence-free entity reaches a SYMMETRIC posterior
        # (α=β, measured 1.26/1.26 → variance 0.071), which is over the 0.04 bar
        # and DOES list — consistent with every other surface, which reads such
        # a node as measured-with-a-wide-posterior rather than unmeasured.
        # Narrowing further would mean gating on posterior ASYMMETRY or on a
        # persisted measurement marker: a semantic change to this report's
        # contract that would silently reclassify ``:Point`` rows too, so it is
        # deliberately NOT taken inside a read-widening change.
        #
        # RECORDED ENTITY DIVERGENCES (so the "agreement" claimed above is not
        # read as covering them). A MEASURED :Subject/:Object/:Event belief
        # lists HERE and cannot list on: ``sdk._review_prune`` (``MATCH
        # (n:Point)``, noted in place), ``why.py``'s cyphers (``:Point``-only,
        # noted in place at the ranking read that depends on them), and
        # ``search_engine.annotate_ep_batch`` (``:Point``-anchored for both the
        # id anchor and the evidence reads, additionally gated at its only
        # search call site by ``entity_type == "point"``). Each is a separate
        # read surface outside #7927's six; do NOT "harmonize" one of them by
        # analogy without deciding which surface the entity belongs on.
        #
        # NON-OPERATOR predicate (review, P2 — two reviewers). ``is_operator``
        # ALONE now lies on the Point leg: the pre-#7927 form was a bare
        # ``= false``, so a legacy ``op_type``-only operator (#2205/#943 — a
        # Point carrying ``op_type`` and NO ``is_operator``) was excluded by
        # NULL. Admitting UNSET, as the entity labels need, silently re-admitted
        # exactly that class, and with NO measured gate on Point a legacy
        # operator coalesces to Beta(1,1) (v = 1/12) and sorts to the TOP of a
        # report about claims — an operator is not a claim. So this uses the
        # CANONICAL non-operator predicate (``sdk._operator_scope_clauses``,
        # #7108; ``_graph_claim_count`` in this file spells it the same way):
        # both halves are load-bearing, and the UNSET admission is only the
        # first.
        rows: list = []
        for _label in EPISTEMIC_LABELS:
            conds = [
                "(n.is_operator IS NULL OR n.is_operator = false)",
                "n.op_type IS NULL",
                _terminal_excluded("n.status"),
                f"NOT {ep_prior_only_cypher('n')}",
            ]
            if _label != EPISTEMIC_LABELS[0]:
                # The canonical measurement predicate (#3276), not a re-spelling:
                # composed with the prior-only conjunct above it is exactly
                # ``ep_measured_cypher``, and spelling it out here is how this
                # function's OWN subject — the #3415/#2206/#3276 "contested
                # here, unmeasured there" disagreement — gets reintroduced by
                # the next edit to either term.
                conds.append(ep_measured_cypher("n"))
            rows.extend(self.g.query(
                f"MATCH (n:{_label}) "
                "WHERE " + " AND ".join(conds) + " "
                "WITH n, coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS a, "
                "     coalesce(n.posterior_beta, n.ep_beta, 1.0) AS b "
                "WITH n, a, b, (a*b)/((a+b)*(a+b)*(a+b+1)) AS v "
                # Entity labels store their text in name/title — a Point's
                # ``content``-only read returned None for them.
                "WHERE v > $t "
                # ``:Event`` writers stamp ``id`` (``entities._upsert_event``
                # sets it "for edge matching") but key on ``eventId``, so the
                # coalesce keeps the row ADDRESSABLE (and the dedupe below
                # from collapsing every id-less Event onto a ``None`` key).
                f"RETURN {epistemic_identity('n')}, "
                "       coalesce(n.content, n.name, n.title, n.label, ''), "
                "       a, b, v",
                params={"t": variance_threshold},
            ).result_set)
        # A multi-labelled node (``:Point:Object`` — ``update_point``'s promote
        # arm mints one) is returned by MORE THAN ONE sweep: collapse on id,
        # first-wins, so it is listed once (the Point leg runs first and is the
        # label the claim rule applies to). The key is the row's identity
        # column (``coalesce(id, eventId)``), never a nullable property.
        by_id: dict[str, list] = {}
        for row in rows:
            by_id.setdefault(row[0], row)
        # A per-label ``ORDER BY`` cannot order the MERGED set, and Cypher row
        # order across four queries never decides an order — sort here,
        # variance-descending with an id tiebreak for determinism.
        ordered = sorted(by_id.values(),
                         key=lambda r: (-float(r[4]), str(r[0])))
        return [{"id": r[0], "content": r[1], "alpha": r[2],
                 "beta": r[3], "variance": r[4]} for r in ordered]

    def invalidate_messages(self, point_ids: list[str] | None = None) -> int:
        """Drop graph-persisted EP message state (epic 903-C4, #1242).

        Warm-start seeds from the edge ``msg_alpha/beta`` (+ ``back_msg_*``)
        properties — when the factor graph changes those seeds are stale and
        must be dropped so the next warm-started run recomputes instead of
        reusing them.

        ``point_ids=None`` drops messages on ALL IMPL|NAND edges (cheap full
        drop — used on evidence/baseline changes, where every prior can
        affect any factor). A point list drops only the edges touching those
        points (topology changes — create/delete operator/edge).

        Returns the number of edges whose messages were dropped.
        """
        if point_ids is None:
            rows = self.g.query(
                "MATCH ()-[r:IMPL|NAND]->() "
                "REMOVE r.msg_alpha, r.msg_beta, "
                "       r.back_msg_alpha, r.back_msg_beta "
                "RETURN count(r)"
            ).result_set
            count = int(rows[0][0]) if rows else 0
            # #5380: the drop is committed state — journal it, or a rebuild
            # resurrects the seeds the live graph just dropped.
            if count:
                self._emit_edge_message_clear("all")
            return count
        if not point_ids:
            return 0
        rows = self.g.query(
            "MATCH (p)-[r:IMPL|NAND]-() "
            f"WHERE {epistemic_disjunction('p')} AND p.id IN $ids "
            "REMOVE r.msg_alpha, r.msg_beta, "
            "       r.back_msg_alpha, r.back_msg_beta "
            "RETURN count(DISTINCT r)",
            params={"ids": list(point_ids)},
        ).result_set
        count = int(rows[0][0]) if rows else 0
        if count:
            self._emit_edge_message_clear("touching", point_ids)
        return count

    def invalidate_factor_messages(self, point_ids: list[str]) -> int:
        """Drop messages on ALL edges of every operator connected to a point.

        #2422: terminalization (retract / invalidate / supersede) must kill the
        dead claim's GHOST votes. ``invalidate_messages`` drops only edges
        touching the point — but a claim's influence reaches its neighbors
        through its operators' SIBLING edges (op1→B carries A's message into
        B; the edge does not touch A). After A is terminal, op1 is degenerate
        (skipped as a factor) so it never re-runs to zero op1→B — yet
        _update_claim_posterior consumes the stale persisted message and B
        stays boosted (the 0.5503 ghost in the #2422 E2E repro). Dropping
        every operator-connected edge's message makes warm-start recompute the
        whole neighborhood instead of reusing seeds computed under the dead
        claim's factor context.

        Returns the number of edges whose messages were dropped.
        """
        if not point_ids:
            return 0
        rows = self.g.query(
            "MATCH (op:Point)-[conn:IMPL|NAND]->(p) "
            f"WHERE {epistemic_disjunction('p')} AND p.id IN $ids "
            "AND (op.is_operator = true OR op.op_type IS NOT NULL) "
            "WITH DISTINCT op "
            "MATCH (op)-[r:IMPL|NAND]->(x) "
            f"WHERE {epistemic_disjunction('x')} "
            "REMOVE r.msg_alpha, r.msg_beta, "
            "       r.back_msg_alpha, r.back_msg_beta "
            "RETURN count(DISTINCT r)",
            params={"ids": list(point_ids)},
        ).result_set
        count = int(rows[0][0]) if rows else 0
        if count:
            self._emit_edge_message_clear("operator-neighbourhood", point_ids)
        return count

    def run(self, operator_ids: list[str], max_hops: int | None = 2,
            evidence: dict[str, tuple[float, float]] | None = None,
            include_draft: bool = False,
            warm_start: bool = False,
            gamma: float = 1e-3) -> tuple[int, bool]:
        """Run EP to convergence. Batch I/O avoids SQLite crashes (#6761).

        Args:
            operator_ids: operator node IDs to include — plain point IDs are
                also accepted as seeds: a plain seed runs the operator-less
                direct edges it participates in plus its operator-mediated
                neighborhood (#888 W5)
            max_hops: how far to extend the affected subgraph. int k = k BFS
                hops; None = the full connected subgraph from the seeds
                (#395 delta A — the unbounded regime carries a degeneration
                guard; when it fires, `_last_truncated` is set).
            evidence: optional {claim_id: (alpha, beta)} priors —
                merged with any evidence set at construction time.
                Evidence set at run() overrides per-claim.
            include_draft: when True, draft Points/operators participate in
                EP identically to live ones (#780 escape hatch — legacy
                behavior). Default False: drafts are excluded at ALL four
                factor-extraction call sites.
            warm_start (epic 903-C4, #1242): message censoring — the graph-
                persisted edge messages (msg_alpha/beta, back_msg_*) are the
                seed (loaded by _load_cache on every run); warm_start=True
                additionally SKIPS factor updates whose input-claim values
                changed by ≤ gamma since the factor last ran (bounded-error
                message censoring per Ihler et al., JMLR 2005 — the license
                for EFBP-style incremental reuse). The fast path
                (compute_confidence) never passes warm_start=True.
            gamma: the censoring threshold (message deltas ≤ gamma are
                skipped). Fixed, never recomputed per-iteration (EFBP
                pragmatic rule — exact bounds are not worth it).

        Returns the (iterations, converged) 2-tuple (unchanged contract —
        four destructuring callers). The run's affected-claim set is stashed
        on ``self._last_affected`` (assigned before the early returns so a
        vacuous run never leaves stale write-back state, #395 delta C); the
        degeneration guard's firing is stashed on ``self._last_truncated``;
        and every factor the run DROPPED (degenerate — a draft/terminal
        endpoint stripped it below 2 participating inputs — or a directional
        operator whose idx-0 source was stripped) is stashed on
        ``self._last_skipped_factors`` (#6105). A 2-tuple of (iterations,
        converged) cannot say "an arm of the graph contributed nothing", so a
        caller that must not read a healthy-looking run as evidence of
        coverage reads that list too.
        """
        # #395 (delta C): run-set diagnostics reset at entry, alongside the
        # cache lifecycle — a run that exits early must not report a previous
        # run's affected set / guard state.
        self._last_affected = set()
        self._last_truncated = False
        # #6105: dropped-factor diagnostics reset at entry, with the rest of
        # the run-set state — a vacuous run must never report a previous run's
        # skipped factors.
        self._last_skipped_factors = []
        # #1163: flush-guard diagnostics reset at entry (a vacuous run that
        # never loads a cache must not carry a previous run's skip flag).
        self._flush_skipped = False
        # Run-level evidence is CALL-SCOPED (#330): merge into a local dict so
        # self._evidence is never mutated by run() — otherwise a later run()
        # without evidence would re-apply (and re-write to the graph) stale
        # run-level priors. Constructor evidence still applies every run.
        run_evidence = dict(self._evidence)
        if evidence:
            run_evidence.update(evidence)

        # Cache lifecycle (#330): _node_cache/_msg_cache/_back_cache are a
        # per-run working set. Remove them at entry (before the evidence
        # pre-write and the early returns) so a run that exits early never
        # leaves stale cache behind for public reads (_read_node/_write_node
        # fall through to the graph when the attribute is absent).
        # Epic 903-C4 (#1242): censored-update counter reset per run (the
        # DE2E-6b cost metric — Δ factor updates warm vs from-scratch).
        self._warm_skipped_updates = 0
        for _attr in ("_node_cache", "_msg_cache", "_back_cache"):
            if hasattr(self, _attr):
                delattr(self, _attr)

        # Apply evidence priors to graph before EP iterations.
        # ponytail: direct graph write so evidence is visible even
        # when there are no operators / affected claims.
        if run_evidence:
            params_list = [
                {"id": cid, "a": a, "b": b}
                for cid, (a, b) in run_evidence.items()
            ]
            # #7853: id-anchored — sweep the epistemic labels (Point first).
            # Writes are idempotent, so a multi-labelled node written under two
            # labels is harmless.
            for _q in epistemic_label_queries(
                "UNWIND $params AS p "
                "MATCH (n:{label} {id: p.id}) "
                # #852 round-4: mirror _flush_cache's keep_prior semantics —
                # explicit baselines (baseline_set=true) are IMMUTABLE evidence
                # priors; run-level evidence must not clobber them. Also clear
                # any stale posterior so a changed prior is immediately
                # observable (a successful run re-flushes posteriors).
                "SET n.ep_alpha = CASE WHEN coalesce(n.baseline_set,false) "
                "                    THEN n.ep_alpha ELSE p.a END, "
                "    n.ep_beta  = CASE WHEN coalesce(n.baseline_set,false) "
                "                    THEN n.ep_beta  ELSE p.b END, "
                # #852 round-5: clear stale posterior ONLY when the prior
                # actually changed (baseline'd claims keep their posterior —
                # it is the observable EP result and not stale if the prior
                # was rejected). set_point_baseline already clears it on a
                # genuine baseline change.
                "    n.posterior_alpha = CASE WHEN coalesce(n.baseline_set,false) "
                "                           THEN n.posterior_alpha ELSE null END, "
                "    n.posterior_beta  = CASE WHEN coalesce(n.baseline_set,false) "
                "                           THEN n.posterior_beta  ELSE null END"
            ):
                self._hop_query(
                    _q, {"params": params_list}, hop=None,
                    phase="evidence_prewrite", frontier_size=len(params_list),
                )
            # #2884 D3: this pre-write CLEARS the posterior of every
            # NON-baseline evidence claim (baseline'd claims keep theirs — the
            # CASE above), and it is a committed write whenever the run then
            # early-returns (no affected claims → no flush to overwrite it).
            # Journal the clear, or a rebuild resurrects a posterior the live
            # graph no longer has. Read the non-baseline MATCHed ids rather
            # than reshaping the write statement (the write is unconditional
            # for every matched id). A later flush journals the real
            # posteriors, so replay order restores the final value.
            # SCOPE (honest, #2884 A4): the SAME statement also writes
            # `ep_alpha`/`ep_beta` (conditionally — the CASE keeps an explicit
            # baseline immutable), and the record carries only the posterior
            # clear. The prior is therefore not replayed (same residual as
            # `set_point_baseline`/the inheritance revert — see the note on
            # `set_point_baseline`): journaling it correctly requires the
            # statement's OUTCOME, not its input params.
            if self._emit is not None:
                # #7853: single disjunction query (not a per-label sweep) so a
                # multi-labelled node is read — and journaled — exactly once.
                rows = self._hop_query(
                    "MATCH (n) "
                    f"WHERE {epistemic_disjunction('n')} AND n.id IN $ids "
                    "AND coalesce(n.baseline_set, false) = false "
                    "RETURN n.id",
                    {"ids": list(run_evidence)}, hop=None,
                    phase="evidence_prewrite",
                    frontier_size=len(run_evidence),
                )
                for row in rows:
                    self._emit("ConfidenceChanged", id=row[0],
                               posterior_alpha=None, posterior_beta=None)

        affected = self._affected_claims(operator_ids, max_hops,
                                         include_draft=include_draft)
        # #395 (delta C): stash the run set BEFORE the early returns — the
        # write-back callers (sdk.compute_confidence, dream.py) consume
        # _last_affected so the write-back set == the run set by construction,
        # and a vacuous run (empty affected / empty factors) never leaves a
        # previous run's set behind for write-back.
        self._last_affected = affected
        if not affected:
            if not include_draft and operator_ids:
                logger.warning(
                    "EP run seeded with %d id(s) produced no affected claims — "
                    "draft Points/operators are excluded by default (#780); "
                    "pass include_draft=True to include them.",
                    len(operator_ids),
                )
            return 0, True

        factors = self._affected_factors(
            affected, include_draft=include_draft,
            skipped=self._last_skipped_factors)
        if not factors:
            return 0, True

        # Load once from graph, then run entirely in memory.
        # Only flush final results at the end — eliminates per-iteration I/O.
        self._load_cache(affected)

        # Epic 903-C4 (#1242) — warm-start censoring state: per-factor
        # last-seen input-claim snapshot. Keyed by factor identity (stable
        # across the per-iteration shuffle).
        warm_snapshot: dict[tuple, dict[str, tuple[float, float]]] | None = (
            {} if warm_start else None
        )

        for iteration in range(self.max_iter):
            # Re-load only on iteration 0 (already loaded above)
            # Subsequent iterations use in-memory cache updated by _write_node/_write_message

            prev = {cid: self._node_cache.get(cid, (1.0, 1.0))
                    for cid in affected}

            random.shuffle(factors)
            for op_id, op_type, input_ids, weight, label, direction in factors:
                if warm_snapshot is not None:
                    key = (op_id, op_type, tuple(sorted(input_ids)))
                    last = warm_snapshot.get(key)
                    if last is not None:
                        delta = 0.0
                        for cid in input_ids:
                            cur = self._node_cache.get(cid, (1.0, 1.0))
                            old = last.get(cid, cur)
                            delta = max(
                                delta,
                                abs(cur[0] - old[0]) / max(old[0], 1e-6),
                                abs(cur[1] - old[1]) / max(old[1], 1e-6),
                            )
                        if delta <= gamma:
                            # Censored (EFBP): the factor's inputs are
                            # sufficiently unchanged — reuse its previous
                            # message, do not re-update.
                            self._warm_skipped_updates += 1
                            continue
                    self._update_factor(op_id, op_type, input_ids, weight,
                                        label, direction)
                    warm_snapshot[key] = {
                        cid: self._node_cache.get(cid, (1.0, 1.0))
                        for cid in input_ids
                    }
                else:
                    self._update_factor(op_id, op_type, input_ids, weight,
                                        label, direction)

            for cid in affected:
                self._update_claim_posterior(cid, run_evidence)

            max_change = 0.0
            for cid in affected:
                new_a, new_b = self._node_cache.get(cid, (1.0, 1.0))
                old_a, old_b = prev.get(cid, (1.0, 1.0))
                change = max(
                    abs(new_a - old_a) / max(old_a, 1e-6),
                    abs(new_b - old_b) / max(old_b, 1e-6),
                )
                max_change = max(max_change, change)

            if max_change < self.tol:
                self._flush_cache()
                self._clear_caches()
                return iteration + 1, True

        self._flush_cache()
        self._clear_caches()
        return self.max_iter, False
