"""EP (Expectation Propagation) belief propagation for Tortoise.

Uses Beta messages in natural parameter space. Moment projection
via Gauss-Jacobi quadrature on [0,1]². Batch I/O eliminates
FalkorDBLite SQLite concurrent-write crashes (#6761).

See: tortoise/research/reflection-agentic-systems/ep-implementation-plan.md
"""
from __future__ import annotations

import random

from .quadrature import tilted_moments, moments_to_beta, phi_nand, phi_impl


class TortoiseEP:
    """EP belief propagation on Tortoise factor graphs.

    Operates on the FalkorProjection's graph. Uses batch I/O:
    loads all affected data into Python dicts at the start of each
    iteration, computes factor updates in Python, flushes writes at end.

    Parameters:
        projection: FalkorProjection instance (provides .g and ._neighbors)
        damping: message damping factor, 0 < λ ≤ 1
        n_quad: Gauss-Jacobi points per dimension (8 = <0.001% error)
        max_iter: hard cap on EP outer iterations
        tol: convergence threshold (max relative change in α,β)
    """

    def __init__(self, projection, *, damping=0.5, n_quad=8,
                 max_iter=50, tol=1e-3, evidence=None, directed=False):
        self.proj = projection
        self.g = projection.g
        self.damping = damping
        self.n_quad = n_quad
        self.max_iter = max_iter
        self.tol = tol
        self.directed = directed
        # Fixed evidence priors: {claim_id: (alpha, beta)}
        self._evidence = dict(evidence) if evidence else {}

    # ── Batch I/O cache (#6761) ──────────────────────────────────

    def _load_cache(self, affected_claims: set[str]):
        """Load all node params and edge messages into Python dicts."""
        self._node_cache: dict[str, tuple[float, float]] = {}
        self._msg_cache: dict[tuple[str, str, str], tuple[float, float]] = {}

        if affected_claims:
            rows = self.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "RETURN n.id, coalesce(n.ep_alpha,1.0), coalesce(n.ep_beta,1.0)",
                params={"ids": list(affected_claims)},
            ).result_set
            for cid, a, b in rows:
                self._node_cache[cid] = (float(a), float(b))

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
        """Write all cached data back to FalkorDB in batch."""
        if self._node_cache:
            params_list = [
                {"id": cid, "a": a, "b": b, "c": round(a/(a+b), 4)}
                for cid, (a, b) in self._node_cache.items()
            ]
            self.g.query(
                "UNWIND $params AS p "
                "MATCH (n:Point {id: p.id}) "
                "SET n.ep_alpha = p.a, n.ep_beta = p.b, n.confidence = p.c",
                params={"params": params_list},
            )

        if self._msg_cache:
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

    # ── Cached read/write ────────────────────────────────────────

    def _read_node(self, node_id: str) -> tuple[float, float]:
        if hasattr(self, '_node_cache') and node_id in self._node_cache:
            return self._node_cache[node_id]
        rows = self.g.query(
            "MATCH (n:Point {id:$id}) "
            "RETURN coalesce(n.ep_alpha, 1.0), coalesce(n.ep_beta, 1.0)",
            params={"id": node_id},
        ).result_set
        return (float(rows[0][0]), float(rows[0][1])) if rows else (1.0, 1.0)

    def _write_node(self, node_id: str, alpha: float, beta: float) -> None:
        if hasattr(self, '_node_cache'):
            self._node_cache[node_id] = (alpha, beta)
            return
        mean = round(alpha / (alpha + beta), 4)
        self.g.query(
            "MATCH (n:Point {id:$id}) "
            "SET n.ep_alpha=$a, n.ep_beta=$b, n.confidence=$c",
            params={"id": node_id, "a": alpha, "b": beta, "c": mean},
        )

    def _read_message(self, op_id: str, claim_id: str,
                      rel_type: str = "IMPL") -> tuple[float, float]:
        key = (op_id, claim_id, rel_type)
        if hasattr(self, '_msg_cache') and key in self._msg_cache:
            return self._msg_cache[key]
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
        if hasattr(self, '_msg_cache'):
            self._msg_cache[key] = (msg_alpha, msg_beta)
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

    def _update_factor(self, op_id: str, op_type: str,
                       input_ids: list[str], weight: float = 1.0) -> None:
        if len(input_ids) < 2:
            return
        if len(input_ids) > 2:
            return self._update_nary_factor(op_id, op_type, input_ids, weight)

        id_a, id_b = input_ids

        cav_eta_a = self._cavity_natural(id_a, op_id, op_type)
        cav_eta_b = self._cavity_natural(id_b, op_id, op_type)
        alpha_a, beta_a = self._beta_from_natural(*cav_eta_a)
        alpha_b, beta_b = self._beta_from_natural(*cav_eta_b)

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

        # Minimal cavity boost: 2x for uniform Beta(1,1) to break EP symmetry.
        # Without this, single-IMPL edges converge to weak coupling even at w=8.
        cav_boost_a = 4.0 if abs(cav_eta_a[0]) < 0.01 and abs(cav_eta_a[1]) < 0.01 else 1.0
        cav_boost_b = 4.0 if abs(cav_eta_b[0]) < 0.01 and abs(cav_eta_b[1]) < 0.01 else 1.0
        raw_eta_a = (raw_eta_a[0] * cav_boost_a, raw_eta_a[1] * cav_boost_a)
        raw_eta_b = (raw_eta_b[0] * cav_boost_b, raw_eta_b[1] * cav_boost_b)

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
        if not self.directed:
            self._write_message(op_id, id_a, *damped_a, op_type)

    def _update_nary_factor(self, op_id: str, op_type: str,
                            input_ids: list[str], weight: float = 1.0) -> None:
        for i in range(len(input_ids)):
            for j in range(i + 1, len(input_ids)):
                self._update_factor(op_id, op_type,
                                    [input_ids[i], input_ids[j]], weight)

    def _update_claim_posterior(self, claim_id: str) -> None:
        # Start from evidence prior in natural parameter space
        ev = self._evidence.get(claim_id)
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
                          ) -> list[tuple[str, str, list[str], float]]:
        factors: list[tuple[str, str, list[str], float]] = []
        seen: set[str] = set()
        for claim_id in affected_claims:
            for rel in ("IMPL", "NAND"):
                rows = self.g.query(
                    f"MATCH (o:Point)-[r:{rel}]->(c:Point {{id:$cid}}) "
                    "RETURN o.id, o.op_type",
                    params={"cid": claim_id},
                ).result_set
                for op_id, op_type in rows:
                    if op_id not in seen:
                        seen.add(op_id)
                        input_rows = self.g.query(
                            "MATCH (o:Point {id:$oid})-[r:IMPL|NAND]->(c:Point) "
                            "RETURN c.id",
                            params={"oid": op_id},
                        ).result_set
                        input_ids = [r[0] for r in input_rows]
                        from .weights import compute_operator_weight
                        weight = compute_operator_weight(self.proj, op_id)
                        factors.append((op_id, op_type, input_ids, weight))
        return factors

    # ── Calibration ──────────────────────────────────────────────

    @staticmethod
    def confidence_to_prior(confidence: float, k: float = 2.0,
                            uniform_threshold: float = 0.05) -> tuple[float, float]:
        """Convert extractor confidence (0-1) to Beta(α, β) prior.

        Extractor confidence 0.8 → Beta(1+0.8k, 1+0.2k).
        Confidence near 0.5 → Beta(1,1) uniform (no information).

        Args:
            confidence: extractor confidence in [0, 1]
            k: evidence strength multiplier (default 2 = mild prior)
            uniform_threshold: |c-0.5| < threshold → uniform prior
        """
        if abs(confidence - 0.5) < uniform_threshold:
            return (1.0, 1.0)
        return (1.0 + confidence * k, 1.0 + (1.0 - confidence) * k)

    # ── Public API ────────────────────────────────────────────────

    def compute_confidence(self, claim_id: str) -> dict:
        a, b = self._read_node(claim_id)
        total = a + b
        return {
            "mean": a / total,
            "variance": (a * b) / (total * total * (total + 1)),
            "alpha": a, "beta": b, "effective_n": total,
        }

    def get_contested_claims(self, variance_threshold: float = 0.04) -> list[dict]:
        rows = self.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "WITH n, coalesce(n.ep_alpha,1.0) AS a, coalesce(n.ep_beta,1.0) AS b "
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
        if evidence:
            self._evidence.update(evidence)

        # Apply evidence priors to graph before EP iterations.
        # ponytail: direct graph write so evidence is visible even
        # when there are no operators / affected claims.
        if self._evidence:
            params_list = [
                {"id": cid, "a": a, "b": b}
                for cid, (a, b) in self._evidence.items()
            ]
            self.g.query(
                "UNWIND $params AS p "
                "MATCH (n:Point {id: p.id}) "
                "SET n.ep_alpha = p.a, n.ep_beta = p.b",
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
            for op_id, op_type, input_ids, weight in factors:
                self._update_factor(op_id, op_type, input_ids, weight)

            for cid in affected:
                self._update_claim_posterior(cid)

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
                return iteration + 1, True

        self._flush_cache()
        return self.max_iter, False
