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
- Async decoupling: fast-path queries never block on dreaming (dreaming is
  triggered post-batch, lazy-on-read, or on-demand via SDK/MCP).

Concurrency contract (#85):
- Embedded mode: dreaming runs synchronously in-band after write batches
  (no daemon thread — avoids SQLite/redislite single-writer hazards, #6761,
  and the #176 process leak). Latency budget: ≤500ms on the dirty subgraph.
- Hosted mode: per-tenant async queue in hosted_api.py; cooperative asyncio
  tasks (server-mode FalkorDB handles concurrency natively).
"""
from __future__ import annotations

import logging
import threading

_log = logging.getLogger(__name__)

# Default EP subgraph expansion for incremental dreaming. _mark_dirty seeds
# 1-hop; the dream expands to max_hops for full propagation. DO NOT reduce
# below 2 without expanding _mark_dirty (contract, #85).
DEFAULT_MAX_HOPS = 2

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
              direction: str = "both") -> dict:
        """Run EP over the subgraph reachable from ``anchors``.

        Reuses the fast-path selector (analyze._bfs_select_operators, capped
        at 200 operators) and TortoiseEP.run with batch I/O. Returns
        {iterations, converged, affected_claims}.

        #780: draft Points/operators are EXCLUDED by default (EP only runs
        over live claims); there is no include_draft escape hatch on this
        surface — call TortoiseEP.run(include_draft=True) directly for
        legacy behavior.
        """
        if not anchors:
            return {"iterations": 0, "converged": True, "affected_claims": []}
        with self._lock:
            proj = self._sdk._get_proj()
            from .analyze import _bfs_select_operators
            operator_ids, factor_anchors = _bfs_select_operators(
                proj, anchors, max_hops=max_hops, rel_filter="IMPL|NAND",
                direction=direction,
            )
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
                return {"iterations": 0, "converged": True, "affected_claims": []}
            ep = self._sdk._get_ep()
            # #330: dream must honour the SDK's persistent evidence (baselines)
            # — hydrate graph-persisted baselines and pass a copy to ep.run.
            # run() is call-scoped, so the copy cannot leak into later runs.
            self._sdk._hydrate_evidence()
            iterations, converged = ep.run(
                seed_ids, max_hops=max_hops,
                evidence=dict(self._sdk._evidence),
            )
            # Persist mean confidence to node property (mirrors compute_confidence).
            # #395: the write-back set == the run set by construction — consume
            # ep._last_affected (stashed by run, assigned before its early
            # returns) instead of re-running the BFS, fixing the documented
            # dream.py:88 footgun (a second _affected_claims call with a
            # divergent default depth). Batch UNWIND write-back (confidence +
            # updatedAt stamp) mirrors compute_confidence.
            affected = set(ep._last_affected)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            confidences = {}
            for claim_id in affected:
                conf = ep.compute_confidence(claim_id)
                confidences[claim_id] = conf
            if confidences:
                proj.g.query(
                    "UNWIND $params AS p "
                    "MATCH (n:Point {id: p.id}) SET n.confidence = p.c, "
                    "n.updatedAt = $now",
                    params={"params": [{"id": cid, "c": conf["mean"]}
                                        for cid, conf in confidences.items()],
                            "now": now},
                )
            return {
                "iterations": iterations,
                "converged": converged,
                "affected_claims": sorted(affected),
            }

    # ── Whole-graph ────────────────────────────────────────────────

    def dream_all(self, max_hops: int = 2,
                  batch_size: int = 2000,
                  max_total_operators: int = 200_000) -> dict:
        """Full-graph EP stabilization from all non-operator Points.

        Memory + DoS guard (#85, security P1): batches the anchor set so the
        EP cache never loads the entire graph at once, AND caps the total
        operators processed across batches so a single request cannot trigger
        an unbounded whole-graph EP on the hosted deployment.

        Returns {batches, total_affected, converged_all}.
        """
        proj = self._sdk._get_proj()
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.is_operator = false "
            "RETURN n.id"
        ).result_set
        anchors = [r[0] for r in rows]
        if not anchors:
            return {"batches": 0, "total_affected": 0, "converged_all": True}

        total_affected: set[str] = set()
        converged_all = True
        batches = 0
        total_operators = 0
        for start in range(0, len(anchors), batch_size):
            chunk = anchors[start:start + batch_size]
            with self._lock:
                from .analyze import _bfs_select_operators
                chunk_ops, _chunk_anchors = _bfs_select_operators(
                    proj, chunk, max_hops=max_hops, rel_filter="IMPL|NAND",
                    direction="both",
                )
            total_operators += len(chunk_ops)
            if total_operators > max_total_operators:
                _log.warning(
                    "dream_all truncated at %d operators (cap %d) — "
                    "graph larger than budget",
                    total_operators, max_total_operators,
                )
                break
            result = self.dream(chunk, max_hops=max_hops)
            batches += 1
            total_affected.update(result.get("affected_claims", []))
            converged_all = converged_all and result.get("converged", False)
        return {
            "batches": batches,
            "total_affected": len(total_affected),
            "converged_all": converged_all,
        }
