"""EP (Expectation Propagation) belief propagation for Tortoise.

Uses Beta messages in natural parameter space. Moment projection
via Gauss-Jacobi quadrature on [0,1]². Batch I/O eliminates
FalkorDBLite SQLite concurrent-write crashes (#6761).

See: tortoise/research/reflection-agentic-systems/ep-implementation-plan.md
"""
from __future__ import annotations

import logging
import math
import random

from .quadrature import tilted_moments, moments_to_beta, phi_nand, phi_impl

logger = logging.getLogger(__name__)


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
    """

    def __init__(self, projection, *, damping=0.5, n_quad=8,
                 max_iter=50, tol=1e-4, evidence=None):
        self.proj = projection
        self.g = projection.g
        self.damping = damping
        self.n_quad = n_quad
        self.max_iter = max_iter
        self.tol = tol
        # Fixed evidence priors: {claim_id: (alpha, beta)}
        self._evidence = dict(evidence) if evidence else {}

    # ── Batch I/O cache (#6761) ──────────────────────────────────

    def _load_cache(self, affected_claims: set[str]):
        """Load all node params and edge messages into Python dicts."""
        self._node_cache: dict[str, tuple[float, float]] = {}
        self._msg_cache: dict[tuple[str, str, str], tuple[float, float]] = {}

        self._immutable_priors: set[str] = set()
        if affected_claims:
            rows = self.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "RETURN n.id, coalesce(n.ep_alpha,1.0), coalesce(n.ep_beta,1.0), "
                "       coalesce(n.baseline_set, false)",
                params={"ids": list(affected_claims)},
            ).result_set
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
            rows = self.g.query(
                f"MATCH (o:Point)-[r:{rel}]->(c:Point) "
                "WHERE c.id IN $ids "
                "RETURN o.id, c.id, coalesce(r.msg_alpha,0.0), coalesce(r.msg_beta,0.0)",
                params={"ids": list(affected_claims)},
            ).result_set
            for oid, cid, ma, mb in rows:
                self._msg_cache[(oid, cid, rel)] = (float(ma), float(mb))

    def _flush_cache(self):
        """Write all cached data back to FalkorDB in batch.

        Attribute-safe (#330): caches may be absent (never loaded, or removed
        by the per-run lifecycle) — flush whatever is present.
        """
        if getattr(self, "_node_cache", None):
            immutable = getattr(self, "_immutable_priors", set())
            params_list = [
                {"id": cid, "a": a, "b": b,
                 "c": round(a/(a+b), 4) if (a + b) > 0 else 0.5,
                 "keep_prior": cid in immutable}
                for cid, (a, b) in self._node_cache.items()
            ]
            self.g.query(
                "UNWIND $params AS p "
                "MATCH (n:Point {id: p.id}) "
                # n.posterior_alpha/beta = the true EP posterior (preferred by
                # _read_node/compute_confidence — resolves observability for
                # baseline'd claims, #852 review P1). n.confidence = posterior
                # mean. n.ep_alpha/beta stay as IMMUTABLE priors for baseline'd
                # claims (re-run stability), posteriors for others (back-compat).
                "SET n.confidence = p.c, "
                "    n.posterior_alpha = p.a, n.posterior_beta = p.b, "
                "    n.ep_alpha = CASE WHEN p.keep_prior THEN n.ep_alpha ELSE p.a END, "
                "    n.ep_beta  = CASE WHEN p.keep_prior THEN n.ep_beta  ELSE p.b END",
                params={"params": params_list},
            )

        if getattr(self, "_msg_cache", None):
            for rel in ("IMPL", "NAND"):
                params_list = [
                    {"oid": oid, "cid": cid, "a": ma, "b": mb}
                    for (oid, cid, r), (ma, mb) in self._msg_cache.items()
                    if r == rel
                ]
                if params_list:
                    self.g.query(
                        f"UNWIND $params AS p "
                        f"MATCH (o:Point {{id: p.oid}})-[r:{rel}]->(c:Point {{id: p.cid}}) "
                        "SET r.msg_alpha = p.a, r.msg_beta = p.b",
                        params={"params": params_list},
                    )

    def _clear_caches(self) -> None:
        """Remove the batch caches so post-run reads hit the graph (#330)."""
        for _attr in ("_node_cache", "_msg_cache"):
            if hasattr(self, _attr):
                delattr(self, _attr)

    # ── Cached read/write ────────────────────────────────────────

    def _read_node(self, node_id: str) -> tuple[float, float]:
        # #330: capture once — a concurrent run() may delattr the cache between
        # the hasattr check and the read (TOCTOU); a local None falls through
        # to the graph instead of raising AttributeError.
        _cache = getattr(self, '_node_cache', None)
        if _cache is not None and node_id in _cache:
            return _cache[node_id]
        rows = self.g.query(
            "MATCH (n:Point {id:$id}) "
            "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
            "       coalesce(n.posterior_beta, n.ep_beta, 1.0)",
            params={"id": node_id},
        ).result_set
        return (float(rows[0][0]), float(rows[0][1])) if rows else (1.0, 1.0)

    def _write_node(self, node_id: str, alpha: float, beta: float) -> None:
        _cache = getattr(self, '_node_cache', None)
        if _cache is not None:
            _cache[node_id] = (alpha, beta)
            return
        # #330: guard degenerate (0,0) params — uniform fallback instead of ZDE
        mean = round(alpha / (alpha + beta), 4) if (alpha + beta) > 0 else 0.5
        # #852 round-6: mirror _flush_cache — baseline'd claims keep their
        # immutable prior; posterior written separately for observability.
        self.g.query(
            "MATCH (n:Point {id:$id}) "
            "SET n.confidence=$c, n.posterior_alpha=$a, n.posterior_beta=$b, "
            "    n.ep_alpha=CASE WHEN coalesce(n.baseline_set,false) "
            "                    THEN n.ep_alpha ELSE $a END, "
            "    n.ep_beta =CASE WHEN coalesce(n.baseline_set,false) "
            "                    THEN n.ep_beta  ELSE $b END",
            params={"id": node_id, "a": alpha, "b": beta, "c": mean},
        )

    def _read_message(self, op_id: str, claim_id: str,
                      rel_type: str = "IMPL") -> tuple[float, float]:
        key = (op_id, claim_id, rel_type)
        _cache = getattr(self, '_msg_cache', None)
        if _cache is not None and key in _cache:
            return _cache[key]
        rows = self.g.query(
            f"MATCH (o:Point {{id:$oid}})-[r:{rel_type}]->(c:Point {{id:$cid}}) "
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
        self.g.query(
            f"MATCH (o:Point {{id:$oid}})-[r:{rel_type}]->(c:Point {{id:$cid}}) "
            "SET r.msg_alpha=$a, r.msg_beta=$b",
            params={"oid": op_id, "cid": claim_id, "a": msg_alpha, "b": msg_beta},
        )

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
        rows = self.g.query(
            "MATCH (n:Point {id:$id}) "
            "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
            "       coalesce(n.posterior_beta, n.ep_beta, 1.0)",
            params={"id": claim_id},
        ).result_set
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

        cav_eta_a = self._cavity_natural(id_a, op_id, op_type)
        cav_eta_b = self._cavity_natural(id_b, op_id, op_type)
        alpha_a, beta_a = self._beta_from_natural(*cav_eta_a)
        alpha_b, beta_b = self._beta_from_natural(*cav_eta_b)

        # NAND uses the standard symmetric contradiction potential for the
        # target message. DIRECTION is enforced structurally by the
        # back-message guard below: for explicitly-'unidirectional' operators
        # the source/attacker receives NO factor message (the Dung-style
        # directed attack an agent opts into). Default is bidirectional
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

        # Determine if this operator is bidirectional.
        # Direction is an explicit operator flag (ONTOLOGY v3.1 §3.1, §8).
        # Default and missing → bidirectional.
        bidirectional = (direction == "bidirectional")

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

        self._write_message(op_id, id_b, *damped_b, op_type)
        if bidirectional:
            self._write_message(op_id, id_a, *damped_a, op_type)

    def _update_nary_factor(self, op_id: str, op_type: str,
                            input_ids: list[str], weight: float = 1.0,
                            label: str | None = None,
                            direction: str = "bidirectional") -> None:
        # input_ids are sorted by idx (source=0, targets=1..N).
        # IMPL: source→target pairs only (skip target↔target).
        # NAND bidirectional (default): all pairwise combinations (mutual
        # contradiction — at-most-one-true over all members).
        # NAND unidirectional (agent-chosen directed attack): source→each-target
        # attacks ONLY — target↔target pairwise edges would otherwise create
        # arbitrary directed attacks between targets (review P2, #795).
        n = len(input_ids)
        if n < 2:
            return
        if op_type == "IMPL":
            pairs = [(input_ids[0], input_ids[j]) for j in range(1, n)]
        elif op_type == "NAND" and direction != "bidirectional":
            # Directed NAND: the source attacks each target; targets do not
            # attack each other.
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
        bidirectional = (direction == "bidirectional")
        orig_damping = self.damping
        acc: dict[str, tuple[float, float]] = {c: (0.0, 0.0) for c in input_ids}
        touched: set[str] = set()
        try:
            self.damping = 1.0  # capture raw (undamped) pair messages
            for a, b in pairs:
                self._msg_cache = dict(overlay_base)  # clean per-pair state
                self._update_factor(op_id, op_type, [a, b], weight, label, direction)
                # _update_factor writes the target's message always, and the
                # source's only for bidirectional operators. Accumulate ONLY
                # freshly written claims — never the pre-factor state (a stale
                # source message must not be re-accumulated as if fresh, #326
                # directed-path corruption).
                for c in (a, b):
                    if c == a and not bidirectional:
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
        is_star = (op_type != "NAND") or not bidirectional
        scale = (1.0 / (n - 1)) if is_star else (2.0 / (n * (n - 1)))
        d = self.damping
        for c in input_ids:
            if c not in touched:
                # Directed-star source: no message by design — clear any stale
                # persisted source message so unidirectional semantics hold.
                # Cache mode: only when a stale entry exists (don't create a
                # zero entry for a never-messaged source). Graph mode: the
                # MATCH…SET is a no-op when no edge exists, so write always.
                if not bidirectional and c == input_ids[0]:
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
            for (oid, cid, rel), (ma, mb) in self._msg_cache.items():
                if cid == claim_id:
                    total_eta1 += ma
                    total_eta2 += mb
        else:
            for rel in ("IMPL", "NAND"):
                rows = self.g.query(
                    f"MATCH (o:Point)-[r:{rel}]->(c:Point {{id:$cid}}) "
                    "RETURN coalesce(r.msg_alpha,0.0), coalesce(r.msg_beta,0.0)",
                    params={"cid": claim_id},
                ).result_set
                for ma, mb in rows:
                    total_eta1 += float(ma)
                    total_eta2 += float(mb)
        alpha, beta = self._beta_from_natural(total_eta1, total_eta2)
        self._write_node(claim_id, alpha, beta)

    # ── Affected subgraph extraction ──────────────────────────────

    def _affected_claims(self, operator_ids: list[str],
                         max_hops: int = 2) -> set[str]:
        affected: set[str] = set()
        for op_id in operator_ids:
            rows = self.g.query(
                "MATCH (o:Point {id:$oid})-[r:IMPL|NAND]->(c:Point) "
                "RETURN DISTINCT c.id",
                params={"oid": op_id},
            ).result_set
            affected.update(r[0] for r in rows)

        if max_hops > 0 and affected:
            frontier = list(affected)
            for _ in range(max_hops):
                new_frontier: list[str] = []
                for claim_id in frontier:
                    for nid in self.proj._neighbors(claim_id):
                        if nid not in affected:
                            affected.add(nid)
                            new_frontier.append(nid)
                frontier = new_frontier
                if not frontier:
                    break
        return affected

    def _affected_factors(self, affected_claims: set[str]
                          ) -> list[tuple[str, str, list[str], float, str | None, str]]:
        """Extract EP factors from the affected claims subgraph.

        Two batch queries replace the original per-claim N+1 pattern (#400 follow-up):
        1. Single query for all operators connected to any affected claim.
        2. Single query for all inputs of those operators, ordered by idx.

        Semantics are preserved: same affected set, same factor list, same
        ordering guarantees (source idx=0 first for directional IMPL).
        """
        factors: list[tuple[str, str, list[str], float, str | None, str]] = []
        if not affected_claims:
            return factors

        # Batch 1: all operators connected to any affected claim.
        op_info: dict[str, tuple[str, str | None, str | None]] = {}
        rows = self.g.query(
            "MATCH (o:Point)-[r:IMPL|NAND]->(c:Point) "
            "WHERE c.id IN $ids "
            "RETURN DISTINCT o.id, o.op_type, o.label, o.direction",
            params={"ids": list(affected_claims)},
        ).result_set
        for op_id, op_type, label, direction in rows:
            if op_id not in op_info:
                op_info[op_id] = (op_type, label, direction)

        if not op_info:
            return factors

        # Batch 2: all inputs for all discovered operators, ordered by idx.
        # ORDER BY r.idx ensures source (idx=0) comes first — required
        # for directional IMPL: id_a = source, id_b = target so that
        # back-messages are correctly skipped.
        op_inputs: dict[str, list[str]] = {op_id: [] for op_id in op_info}
        rows = self.g.query(
            "MATCH (o:Point)-[r:IMPL|NAND]->(c:Point) "
            "WHERE o.id IN $ids "
            "RETURN o.id, c.id, coalesce(r.idx, 0) "
            "ORDER BY coalesce(r.idx, 0)",
            params={"ids": list(op_info.keys())},
        ).result_set
        for op_id, claim_id, _idx in rows:
            op_inputs[op_id].append(claim_id)

        # Assemble factors (single compute_operator_weight per operator).
        from .weights import compute_operator_weight
        for op_id, (op_type, label, direction) in op_info.items():
            # Defensive: pre-migration operators without direction default to bidirectional
            if direction is None:
                direction = "bidirectional"
                logger.warning(
                    "Operator %s has no direction property — defaulting to 'bidirectional'. "
                    "Run graph-scripts/migrate_direction.py to backfill.",
                    op_id,
                )
            input_ids = op_inputs.get(op_id, [])
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
        rows = self.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "WITH n, coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS a, "
            "     coalesce(n.posterior_beta, n.ep_beta, 1.0) AS b "
            "WITH n, a, b, (a*b)/((a+b)*(a+b)*(a+b+1)) AS v "
            "WHERE v > $t RETURN n.id, n.content, a, b, v ORDER BY v DESC",
            params={"t": variance_threshold},
        ).result_set
        return [{"id": r[0], "content": r[1], "alpha": r[2],
                 "beta": r[3], "variance": r[4]} for r in rows]

    def run(self, operator_ids: list[str], max_hops: int = 2,
            evidence: dict[str, tuple[float, float]] | None = None
            ) -> tuple[int, bool]:
        """Run EP to convergence. Batch I/O avoids SQLite crashes (#6761).

        Args:
            operator_ids: operator node IDs to include
            max_hops: how far to extend the affected subgraph
            evidence: optional {claim_id: (alpha, beta)} priors —
                merged with any evidence set at construction time.
                Evidence set at run() overrides per-claim.
        """
        # Run-level evidence is CALL-SCOPED (#330): merge into a local dict so
        # self._evidence is never mutated by run() — otherwise a later run()
        # without evidence would re-apply (and re-write to the graph) stale
        # run-level priors. Constructor evidence still applies every run.
        run_evidence = dict(self._evidence)
        if evidence:
            run_evidence.update(evidence)

        # Cache lifecycle (#330): _node_cache/_msg_cache are a per-run working
        # set. Remove them at entry (before the evidence pre-write and the
        # early returns) so a run that exits early never leaves stale cache
        # behind for public reads (_read_node/_write_node fall through to the
        # graph when the attribute is absent).
        for _attr in ("_node_cache", "_msg_cache"):
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
            self.g.query(
                "UNWIND $params AS p "
                "MATCH (n:Point {id: p.id}) "
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
                "                           THEN n.posterior_beta  ELSE null END",
                params={"params": params_list},
            )

        affected = self._affected_claims(operator_ids, max_hops)
        if not affected:
            return 0, True

        factors = self._affected_factors(affected)
        if not factors:
            return 0, True

        # Load once from graph, then run entirely in memory.
        # Only flush final results at the end — eliminates per-iteration I/O.
        self._load_cache(affected)

        for iteration in range(self.max_iter):
            # Re-load only on iteration 0 (already loaded above)
            # Subsequent iterations use in-memory cache updated by _write_node/_write_message

            prev = {cid: self._node_cache.get(cid, (1.0, 1.0))
                    for cid in affected}

            random.shuffle(factors)
            for op_id, op_type, input_ids, weight, label, direction in factors:
                self._update_factor(op_id, op_type, input_ids, weight, label, direction)

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
