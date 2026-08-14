"""Dreaming — background EP stabilization for the Tortoise graph (#85).

Two-tier architecture (fast path + slow path):
  Fast path:  per-query impact-subgraph EP via compute_confidence(anchors=...)
  Slow path:  dreaming — whole-graph/incremental EP stabilization

The Dreamer reuses the landed fast-path selector (_bfs_select_operators) and
the batch-I/O EP engine (TortoiseEP). It is the Tortoise analogue of memory
consolidation / sleep consolidation: not answering a query, keeping the graph
honest after batch writes.

Design (Hybrid C, #85):
- ``dream(anchors, hops)`` — incremental: BFS from anchor points, run EP on
  the affected subgraph, write back confidences.
- ``dream_all()`` — full-graph stabilization from all non-operator points.
- ``dream_window(budget)`` (epic 903-C3, #1241) — stale-first scheduler:
  each pass refreshes the stalest window (staleness-ranked top-N ∪
  retained-dirty-roots), per-pass operator budget bounded by the existing
  200-op selector cap, operator-set dedup across windows (fixes the
  dream_all double-BFS), atomic UNWIND freshness write-back (#1240).
- Async decoupling: fast-path queries never block on dreaming (dreaming is
  triggered post-batch, lazy-on-read, or on-demand via SDK/MCP).

Concurrency contract (#85):
- Embedded mode: dreaming runs synchronously in-band after write batches
  (no daemon thread — avoids SQLite/redislite single-writer hazards, #6761,
  and the #176 process leak). Latency budget: ≤500ms on the dirty subgraph.
- Hosted mode: per-tenant async queue in hosted_api.py; cooperative asyncio
  tasks (server-mode FalkorDB handles concurrency natively).

Calibration posture (#1157): dreaming WRITES n.confidence, so it is an EP
write surface — the SDK-level ``dream()`` gate (require_calibration) refuses
uncalibrated runs before any EP work (CalibrationError), same posture as
compute_confidence. This class is internal; the gate lives on the public
``TortoiseSDK.dream`` surface so dream_all → dream chunks inherit it.
"""
from __future__ import annotations

import logging
import threading

_log = logging.getLogger(__name__)

# Default EP subgraph expansion for incremental dreaming. _mark_dirty seeds
# 1-hop; the dream expands to max_hops for full propagation. DO NOT reduce
# below 2 without expanding _mark_dirty (contract, #85).
DEFAULT_MAX_HOPS = 2

# Default per-pass window budget for the stale-first scheduler (epic 903-C3,
# #1241) — the existing _bfs_select_operators 200-operator selector cap.
# budget=None resolves to this (every pass bounded by construction); an
# explicit budget overrides it; budget=0 → no-op; budget >= graph → single
# pass (window = whole graph, degenerates to full).
DEFAULT_WINDOW_BUDGET = 200

# Claim-anchor chunk size for windowed passes over large windows (mirrors
# dream_all's batch_size default) — bounds EP cache memory when the window
# degenerates to the whole graph (all-null / budget >= graph).
WINDOW_CLAIM_BATCH = 2000

# Embedded in-band latency budget (seconds). If a dream exceeds this, the
# caller falls back to lazy-read + the scheduled dream_all (#85).
EMBEDDED_LATENCY_BUDGET_S = 0.5


class Dreamer:
    """Incremental + whole-graph EP stabilization.

    Thread-safe: a lock serializes dream runs so concurrent triggers
    (post-batch, lazy-read, explicit) never overlap on the same SDK.
    """

    def __init__(self, sdk):
        self._sdk = sdk
        self._lock = threading.Lock()

    # ── Incremental ────────────────────────────────────────────────

    def dream(self, anchors: list[str], max_hops: int = DEFAULT_MAX_HOPS,
              direction: str = "both",
              stamp_dreamed_at: bool = True,
              trivial_stamp: bool = True,
              op_cap: int = 200) -> dict:
        """Run EP over the subgraph reachable from ``anchors``.

        Reuses the fast-path selector (analyze._bfs_select_operators, capped
        at 200 operators) and TortoiseEP.run with batch I/O. Returns
        {iterations, converged, affected_claims}.

        Epic 903-C2 (#1240) — freshness write-back:
        - The per-claim confidence write-back loop is replaced by a SINGLE
          UNWIND batch that SETs confidence + lastDreamedAt + updatedAt for
          all affected claims together (mirrors ``_flush_cache``'s shape) —
          atomic: both-or-neither, no N+1 writes, no partial writes (confidence reads are still per-claim — unchanged from pre-fix).
        - ``lastDreamedAt`` is stamped ONLY when the run converged (a failed
          run keeps old stamps — retention semantics, W4) AND
          ``stamp_dreamed_at`` is set. The fast path (compute_confidence)
          never passes this flag and never stamps.
        - Operator-less anchors (live claims with no IMPL/NAND edges — EP can
          never cover them) get a trivial stamp in the local pass when
          ``trivial_stamp=True`` (dream_all disables it and runs its own
          graph-wide scan instead, reporting ``scanned_count``).

        #780: draft Points/operators are EXCLUDED by default (EP only runs
        over live claims); there is no include_draft escape hatch on this
        surface — call TortoiseEP.run(include_draft=True) directly for
        legacy behavior.

        #1157 calibration: this is an EP WRITE surface (persists
        n.confidence). Callers must gate on calibration state BEFORE calling
        (the SDK surface does via require_calibration → CalibrationError);
        do not invoke Dreamer directly with uncalibrated graphs.

        ``op_cap`` (epic 903-C6, #1244): the BFS operator-selection cap for
        this pass. ``budget=None`` on the SDK surface resolves to the
        existing 200-op selector default (I1); an explicit budget overrides
        it. ``_last_operator_count`` is set to the selected operator-set
        size (after cap truncation) — the SDK adapter reads it to report
        ``budget_used`` in the I1 local key-set.
        """
        if not anchors:
            self._last_operator_count = 0
            return {"iterations": 0, "converged": True, "affected_claims": []}
        with self._lock:
            proj = self._sdk._get_proj()
            from .analyze import _bfs_select_operators
            operator_ids, factor_anchors = _bfs_select_operators(
                proj, anchors, max_hops=max_hops, rel_filter="IMPL|NAND",
                direction=direction, op_cap=op_cap,
            )
            self._last_operator_count = len(operator_ids)
            # A9 (epic #902 §5.6): the selection set ALSO carries direct-edge
            # factor anchors — a direct-edge-only subgraph yields ZERO
            # operator ids but a non-empty direct-factor selection. ep.run
            # accepts plain-point seeds (Batch 3 runs the operator-less
            # direct edges they participate in), so the run seeds = operators
            # + the anchor endpoints; both-empty is the ONLY vacuous case.
            seed_ids = list(operator_ids)
            for (src, tgt, _t) in factor_anchors:
                seed_ids.append(src)
                seed_ids.append(tgt)
            seed_ids = list(dict.fromkeys(seed_ids))  # dedup (order is not
            # deterministic — operator_ids is a set, hash-randomized across
            # processes; harmless: EP factors are order-independent and
            # affected_claims is a set)
            if not seed_ids:
                # Epic 903-C2 (#1240): operator-less anchors (isolated claims
                # with no operators AND no direct edges). There is no EP to
                # run — but the local pass still trivially stamps them so a
                # freshly written isolated claim gets lastDreamedAt instead of
                # staying null forever (the pre-fix seed-empty early return
                # never stamped). Draft anchors are excluded by the scan's
                # liveness filter, so draft-only dirty roots stay unstamped
                # (the zero-affected retention path, #780).
                if stamp_dreamed_at and trivial_stamp:
                    stamped = self._trivial_stamp(proj, anchors)
                    return {"iterations": 0, "converged": True,
                            "affected_claims": sorted(stamped)}
                return {"iterations": 0, "converged": True, "affected_claims": []}
            ep = self._sdk._get_ep()
            # #330: dream must honour the SDK's persistent evidence (baselines)
            # — hydrate graph-persisted baselines and pass a copy to ep.run.
            # run() is call-scoped, so the copy cannot leak into later runs.
            iterations, converged, affected = self._ep_run_batch(
                proj, seed_ids, max_hops, stamp_dreamed_at)
            stamp = stamp_dreamed_at and converged
            # Epic 903-C2 (#1240): operator-less anchors among the dirty roots
            # are trivially stamped in the same local pass (the EP write-back
            # above can never cover them — they have no factors). Gated on the
            # same convergence rule: a failed run stamps nothing.
            if stamp and trivial_stamp:
                affected |= self._trivial_stamp(proj, anchors)
            return {
                "iterations": iterations,
                "converged": converged,
                "affected_claims": sorted(affected),
            }

    def _ep_run_batch(self, proj, seed_ids: list[str], max_hops: int,
                      stamp: bool) -> tuple[int, bool, set[str]]:
        """Run EP over ``seed_ids`` + the atomic UNWIND write-back (#1240).

        Shared by ``dream()`` and the stale-first scheduler
        (``dream_window``) so window passes reuse the EXACT
        confidence+lastDreamedAt+updatedAt single-UNWIND write-back
        (both-or-neither, no N+1 writes). ``lastDreamedAt`` is written ONLY
        when the run converged AND ``stamp`` is set — failed runs keep old
        stamps and re-enter the staleness ranking (W4 retention). Returns
        ``(iterations, converged, affected)``.
        """
        ep = self._sdk._get_ep()
        # #330: dream must honour the SDK's persistent evidence (baselines)
        # — hydrate graph-persisted baselines and pass a copy to ep.run.
        # run() is call-scoped, so the copy cannot leak into later runs.
        self._sdk._hydrate_evidence()
        iterations, converged = ep.run(
            seed_ids, max_hops=max_hops,
            evidence=dict(self._sdk._evidence),
        )
        # #395: the write-back set == the run set by construction — consume
        # ep._last_affected (stashed by run, assigned before its early
        # returns) instead of re-running the BFS (the dream.py:88 footgun).
        # Epic 903-C2 (#1240): the batch UNWIND write-back SETs confidence +
        # lastDreamedAt + updatedAt in ONE statement (both-or-neither, no
        # N+1 writes). lastDreamedAt is written only when the run converged
        # AND stamp is set (failed runs keep old stamps — retention, W4).
        affected = set(ep._last_affected)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        stamp_now = stamp and converged
        params_list = [
            {"id": cid, "c": ep.compute_confidence(cid)["mean"]}
            for cid in affected
        ]
        if params_list:
            if stamp_now:
                proj.g.query(
                    "UNWIND $params AS p "
                    "MATCH (n:Point {id: p.id}) "
                    "SET n.confidence = p.c, n.lastDreamedAt = $now, "
                    "    n.updatedAt = $now",
                    params={"params": params_list, "now": now},
                )
            else:
                proj.g.query(
                    "UNWIND $params AS p "
                    "MATCH (n:Point {id: p.id}) "
                    "SET n.confidence = p.c, n.updatedAt = $now",
                    params={"params": params_list, "now": now},
                )
        return iterations, converged, affected

    # ── Stale-first window scheduler (epic 903-C3, #1241) ────────────

    def dream_window(self, budget: int | None = None,
                     max_hops: int = DEFAULT_MAX_HOPS) -> dict:
        """Stale-first windowed pass (epic 903-C3, #1241).

        Refreshes the stalest chunk of the graph each pass:

            window = retained-dirty-roots ∪ staleness-ranked-top-N

        where the staleness ranking (analyze._stale_first_claims) orders
        live non-operator claims by ``lastDreamedAt ASC`` — NULL ranks
        STALEST (never dreamed → first; legacy / first-deploy / crash-mid-
        pass claims drain across passes), deterministic id tie-break — and
        the dirty-roots union guarantees non-converged regions (W4, 903-C5
        retention) reselect even when they rank outside the top-N.

        Per-pass cost is bounded by ``budget`` — distinct operators
        processed after operator-set dedup never exceeds it:
        - ``budget=None`` → DEFAULT_WINDOW_BUDGET (the existing 200-op
          ``_bfs_select_operators`` selector cap — every pass is bounded by
          construction).
        - ``budget=0`` → no-op result (no EP, no stamps).
        - ``budget >= graph`` → single pass (window = whole graph,
          degenerates to a full pass).
        - All-null graph (nothing ever dreamed — first deploy / legacy /
          crash recovery) → window = whole graph regardless of budget (W2).

        Operator-set dedup (scope item 4 — fixes the ``dream_all``
        double-BFS: the cap-check BFS and the inner ``dream()`` BFS
        recomputed the same subgraph): each batch's operator set is selected
        by exactly ONE ``_bfs_select_operators`` call (op_cap = budget) and
        reused for the EP run — no second BFS; sets are unioned across the
        pass's batches so overlapping windows never recompute the same
        operators.

        Write-back (#1240): every batch runs through the shared atomic
        UNWIND confidence+lastDreamedAt+updatedAt write-back with stamping
        on (scheduler passes are write-triggered); stamps land ONLY when the
        batch converged (W4 — failed runs keep old stamps and re-enter the
        ranking). Operator-less window claims are trivially stamped by a
        window-scoped scan, gated on the pass converging. Dirty roots are
        NOT cleared here — the SDK adapter (I6) maps ``operators_deduped``
        → ``budget_used`` and drives the dirty-root logic from ``converged``.

        Returns ``{mode: "stale-first", batches, converged, converged_all,
        operators_deduped, budget_used, affected_claims}`` (I6 — the SDK
        adapter adds ``coverage`` for the I1 shape).

        Side channel: ``self._last_window`` is set to the pre-pass window
        (the staleness-ranked claims + retained-dirty union) on every return
        path ([] for the no-op paths) — the SDK adapter (I6→I1 mapping)
        reads it to compute stale-first coverage WITHOUT recomputing the
        window post-pass (stamps written by the pass reorder the ranking,
        so a post-pass recomputation would be wrong — the
        all-null→whole-graph case would blow up entirely).
        """
        eff_budget = DEFAULT_WINDOW_BUDGET if budget is None else budget
        if eff_budget <= 0:
            self._last_window = []
            return {"mode": "stale-first", "batches": 0, "converged": True,
                    "converged_all": True, "operators_deduped": 0,
                    "budget_used": 0, "affected_claims": []}
        with self._lock:
            proj = self._sdk._get_proj()
            from .analyze import (_bfs_select_operators,
                                  _stale_first_claims,
                                  _stale_first_count_stamped)
            # All-null graph (nothing ever dreamed): window = whole graph —
            # no point windowing a never-dreamed graph; one full pass drains
            # it (W2 all-null degenerates to full).
            if _stale_first_count_stamped(proj) == 0:
                ranked = _stale_first_claims(proj, limit=None)
            else:
                ranked = _stale_first_claims(proj, limit=eff_budget)
            # Window = staleness-ranked-top-N ∪ retained-dirty-roots (the
            # union guarantees non-converged regions reselect even outside
            # the top-N, W2/W4). Dirty roots sorted for deterministic
            # chunking (BFS collection is order-independent — sets — but the
            # window slice is not).
            ranked_set = set(ranked)
            window = list(ranked) + [
                d for d in sorted(self._sdk._dirty_roots)
                if d not in ranked_set
            ]
            # Epic 903-C6 (#1244): record the pre-pass window for the SDK
            # adapter (stale-first coverage = affected / window closure —
            # I1 "remaining-stale-before-pass" = the stale claims this pass
            # could reach; the closure is computed from THIS pre-pass list
            # because stamps written by the pass reorder the ranking).
            self._last_window = window
            if not window:
                return {"mode": "stale-first", "batches": 0,
                        "converged": True, "converged_all": True,
                        "operators_deduped": 0, "budget_used": 0,
                        "affected_claims": []}
            seen_ops: set[str] = set()
            affected: set[str] = set()
            batches = 0
            converged_all = True
            for start in range(0, len(window), WINDOW_CLAIM_BATCH):
                chunk = window[start:start + WINDOW_CLAIM_BATCH]
                batches += 1
                # ONE selection per batch — the cap-check and the run share
                # it (dream_all double-BFS fix); op_cap = budget enforces the
                # per-batch operator cap deterministically (sorted-by-id
                # truncation).
                ops, factor_anchors = _bfs_select_operators(
                    proj, chunk, max_hops=max_hops, rel_filter="IMPL|NAND",
                    direction="both", op_cap=eff_budget,
                )
                # Operator-set dedup: union across the pass's batches — an
                # operator already selected by an earlier window is never
                # re-run (overlapping windows share operator sets). The
                # pass-level remaining guard keeps budget_used ≤ B even when
                # the window spans multiple batches; truncated operators stay
                # stale and re-enter the ranking next pass.
                new_ops = ops - seen_ops
                remaining = eff_budget - len(seen_ops)
                if remaining <= 0:
                    # Pass budget exhausted — defer the rest of the window to
                    # the next pass (claims stay stale, re-enter ranking).
                    break
                if len(new_ops) > remaining:
                    new_ops = set(sorted(new_ops)[:remaining])
                seed_ids = list(new_ops)
                for (src, tgt, _t) in factor_anchors:
                    seed_ids.append(src)
                    seed_ids.append(tgt)
                seed_ids = list(dict.fromkeys(seed_ids))
                if not seed_ids:
                    # Chunk fully covered by an earlier window's operator set
                    # (or draft-only): nothing new to run.
                    continue
                seen_ops |= new_ops
                _iterations, converged, aff = self._ep_run_batch(
                    proj, seed_ids, max_hops, stamp=True)
                affected |= aff
                converged_all = converged_all and converged
            # Operator-less window claims: trivial-stamp (window-scoped scan,
            # independent of the EP flush), gated on the pass converging — a
            # failed pass stamps nothing (W4 retention).
            if converged_all:
                affected |= self._trivial_stamp(proj, window)
            return {
                "mode": "stale-first",
                "batches": batches,
                "converged": converged_all,
                "converged_all": converged_all,
                "operators_deduped": len(seen_ops),
                "budget_used": len(seen_ops),
                "affected_claims": sorted(affected),
            }

    def _trivial_stamp(self, proj, claim_ids: list[str] | None = None) -> set[str]:
        """Trivial-stamp ``lastDreamedAt`` for operator-less claims (epic 903-C2).

        Operator-less = live non-operator Points with NO IMPL|NAND edges — EP
        can never cover them (``run()`` early-returns when it has no factors),
        so this dedicated scan stamps them directly, independent of the EP
        flush. ``claim_ids=None`` scans the whole graph (full passes); a list
        restricts the scan to the given anchors (local passes). Returns the
        stamped claim ids.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        if claim_ids is None:
            rows = proj.g.query(
                "MATCH (n:Point) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "AND (n.status IS NULL OR n.status <> 'draft') "
                "AND NOT (n)-[:IMPL|NAND]-() "
                "SET n.lastDreamedAt = $now, n.updatedAt = $now "
                "RETURN n.id",
                params={"now": now},
            ).result_set
        else:
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "AND (n.is_operator IS NULL OR n.is_operator = false) "
                "AND (n.status IS NULL OR n.status <> 'draft') "
                "AND NOT (n)-[:IMPL|NAND]-() "
                "SET n.lastDreamedAt = $now, n.updatedAt = $now "
                "RETURN n.id",
                params={"ids": list(claim_ids), "now": now},
            ).result_set
        return {r[0] for r in rows}

    # ── Whole-graph ────────────────────────────────────────────────

    def dream_all(self, max_hops: int = 2,
                  batch_size: int = 2000,
                  max_total_operators: int = 200_000,
                  stamp_dreamed_at: bool = True,
                  budget: int | None = None) -> dict:
        """Full-graph EP stabilization from all non-operator Points.

        Memory + DoS guard (#85, security P1): batches the anchor set so the
        EP cache never loads the entire graph at once, AND caps the total
        operators processed across batches so a single request cannot trigger
        an unbounded whole-graph EP on the hosted deployment.

        Epic 903-C2 (#1240): the full pass stamps every reachable claim via
        the atomic UNWIND write-back AND trivially stamps operator-less
        claims via a dedicated graph-wide scan (the per-chunk dream() calls
        disable their own trivial-stamp so operator-less claims are reported
        separately, per DE2E-1: ``total_affected`` = reachable only;
        ``scanned_count`` = operator-less stamped). The scan is gated on
        ``stamp_dreamed_at`` AND ``converged_all`` — a full pass that failed
        to converge anywhere is a failed run and stamps nothing (W4
        retention: failed runs do not rank fresh).

        Returns {batches, total_affected, converged_all, scanned_count,
        budget_used}.

        ``budget`` (epic 903-C6, #1244): an explicit operator cap for the
        pass. ``budget=None`` (default) keeps the existing ``
        max_total_operators`` DoS guard (warn + truncate, never raise). An
        explicit budget makes the pass fail loudly instead of truncating —
        a full pass is contractually complete-in-one-pass (J3), so when the
        graph requires more operators than the budget, ``
        BudgetExceededError`` is raised at the first chunk that would
        exceed it (earlier chunks' writes stay committed — the raise site
        surfaces the budget exhaustion; hosted wiring in 903-C8 maps it to
        the 429 quota response). ``budget_used`` = operators selected
        across the pass's chunks (accumulated pre-truncation).
        """
        from .exceptions import BudgetExceededError
        proj = self._sdk._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.is_operator = false "
            "RETURN n.id"
        ).result_set
        anchors = [r[0] for r in rows]
        if not anchors:
            return {"batches": 0, "total_affected": 0, "converged_all": True,
                    "scanned_count": 0, "budget_used": 0}

        total_affected: set[str] = set()
        converged_all = True
        batches = 0
        total_operators = 0
        cap = budget if budget is not None else max_total_operators
        for start in range(0, len(anchors), batch_size):
            chunk = anchors[start:start + batch_size]
            with self._lock:
                from .analyze import _bfs_select_operators
                # Epic 903-C6 (#1244): when an explicit budget is set, LIFT
                # the selector's default 200-op cap (op_cap=None) so the
                # true per-chunk need is collected and the budget check
                # below can fire — the default cap would otherwise truncate
                # each chunk at 200 before total_operators > budget ever
                # triggers (silent partial full pass, P1 review #1244). The
                # BFS is still bounded by max_hops=2. budget=None keeps the
                # selector cap as the safety bound.
                chunk_ops, _chunk_anchors = _bfs_select_operators(
                    proj, chunk, max_hops=max_hops, rel_filter="IMPL|NAND",
                    direction="both",
                    op_cap=None if budget is not None else 200,
                )
            total_operators += len(chunk_ops)
            if total_operators > cap:
                if budget is not None:
                    raise BudgetExceededError(
                        f"full-mode dream budget {budget} exceeded — "
                        f"pass requires {total_operators} operators; "
                        f"raise the budget or use mode=\"stale-first\" "
                        f"for bounded windowed passes",
                        budget=budget, required=total_operators,
                    )
                _log.warning(
                    "dream_all truncated at %d operators (cap %d) — "
                    "graph larger than budget",
                    total_operators, max_total_operators,
                )
                break
            result = self.dream(chunk, max_hops=max_hops,
                                stamp_dreamed_at=stamp_dreamed_at,
                                trivial_stamp=False)  # dedicated scan below
            batches += 1
            total_affected.update(result.get("affected_claims", []))
            converged_all = converged_all and result.get("converged", False)
        # Epic 903-C2 (#1240): dedicated graph-wide trivial-scan for
        # operator-less claims (independent of the EP flush; kept out of
        # total_affected per DE2E-1). Gated on BOTH stamp_dreamed_at AND
        # converged_all — a full pass where any chunk failed to converge is a
        # failed run and must not rank anything fresh (W4/DE2E-7 retention
        # semantics). The scan runs under the Dreamer lock like every other
        # dream-cycle write.
        scanned_count = 0
        if stamp_dreamed_at and converged_all:
            with self._lock:
                scanned_count = len(self._trivial_stamp(proj))
        return {
            "batches": batches,
            "total_affected": len(total_affected),
            "converged_all": converged_all,
            "scanned_count": scanned_count,
            "budget_used": total_operators,
        }
