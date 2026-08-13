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
from .live import _live_only

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
        # Operator-less direct-edge back-messages (#888 W5): the back-message
        # of a direct edge lives ON the edge (r.back_msg_alpha/beta) so each
        # edge has its own slot (fan-out sources don't collide).
        self._back_cache: dict[tuple[str, str, str], tuple[float, float]] = {}

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

        if affected_claims:
            back_rows = self.g.query(
                "MATCH (a:Point)-[r:IMPL|NAND]->(b:Point) "
                "WHERE coalesce(r.direction, 'bidirectional') = 'bidirectional' "
                "AND (a.id IN $ids OR b.id IN $ids) "
                "RETURN a.id, b.id, type(r), "
                "       coalesce(r.back_msg_alpha,0.0), coalesce(r.back_msg_beta,0.0)",
                params={"ids": list(affected_claims)},
            ).result_set
            for src, tgt, rel, ma, mb in back_rows:
                self._back_cache[(src, tgt, rel)] = (float(ma), float(mb))

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

        if getattr(self, "_back_cache", None):
            for rel in ("IMPL", "NAND"):
                params_list = [
                    {"src": src, "tgt": tgt, "a": ma, "b": mb}
                    for (src, tgt, r), (ma, mb) in self._back_cache.items()
                    if r == rel
                ]
                if params_list:
                    self.g.query(
                        f"UNWIND $params AS p "
                        f"MATCH (o:Point {{id:p.src}})-[r:{rel}]->(c:Point {{id:p.tgt}}) "
                        "SET r.back_msg_alpha = p.a, r.back_msg_beta = p.b",
                        params={"params": params_list},
                    )

    def _clear_caches(self) -> None:
        """Remove the batch caches so post-run reads hit the graph (#330)."""
        for _attr in ("_node_cache", "_msg_cache", "_back_cache"):
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
        rows = self.g.query(
            f"MATCH (o:Point {{id:$oid}})-[r:{rel_type}]->(c:Point {{id:$cid}}) "
            "RETURN coalesce(r.back_msg_alpha, 0.0), coalesce(r.back_msg_beta, 0.0)",
            params={"oid": src_id, "cid": tgt_id},
        ).result_set
        return (float(rows[0][0]), float(rows[0][1])) if rows else (0.0, 0.0)

    def _write_back_message(self, src_id: str, tgt_id: str,
                            msg_alpha: float, msg_beta: float,
                            rel_type: str = "IMPL") -> None:
        key = (src_id, tgt_id, rel_type)
        _cache = getattr(self, '_back_cache', None)
        if _cache is not None:
            _cache[key] = (msg_alpha, msg_beta)
            return
        self.g.query(
            f"MATCH (o:Point {{id:$oid}})-[r:{rel_type}]->(c:Point {{id:$cid}}) "
            "SET r.back_msg_alpha=$a, r.back_msg_beta=$b",
            params={"oid": src_id, "cid": tgt_id, "a": msg_alpha, "b": msg_beta},
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

        self._write_message(op_id, id_b, *damped_b, op_type)
        if bidirectional:
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
            # Operator-less direct edges (#888 W5): the back-message of the
            # claim's OWN outgoing direct edges also bears on its posterior
            # (it is the factor's message to the source endpoint).
            if hasattr(self, '_back_cache'):
                for (src, tgt, rel), (ma, mb) in self._back_cache.items():
                    if src == claim_id:
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
                rows = self.g.query(
                    f"MATCH (a:Point)-[r:{rel}]->(b:Point) "
                    "WHERE a.id = $cid "
                    "AND coalesce(r.direction, 'bidirectional') = 'bidirectional' "
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
                         max_hops: int = 2,
                         include_draft: bool = False) -> set[str]:
        """Affected claims for a run, seeded from operator and/or point ids.

        Seeds may be operator ids (legacy) or plain point ids (#888 W5): a
        plain-point seed discovers operator-less direct edges in both
        directions (an operator-less edge is a factor shared by BOTH of its
        endpoints) AND its operator-mediated neighborhood, so seeding a claim
        runs every factor it participates in.

        With include_draft=False (default, #780): draft target claims are
        excluded, draft operator nodes are skipped as sources, and the BFS
        expansion never hops through draft claims — a draft-connected
        operator must change NO live claim's posterior.
        """
        live_c = _live_only("c.status", include_draft)
        live_o = _live_only("o.status", include_draft)
        live_a = _live_only("a.status", include_draft)
        live_b = _live_only("b.status", include_draft)
        affected: set[str] = set()
        for seed_id in operator_ids:
            is_op = self.g.query(
                "MATCH (n:Point {id:$id}) RETURN (n.is_operator = true), n.status",
                params={"id": seed_id},
            ).result_set
            seed_is_draft = bool(
                is_op and is_op[0][1] == "draft" and not include_draft
            )
            if is_op and is_op[0][0]:
                # Operator seed — legacy behavior: follow outgoing edges to
                # the operator's inputs (the operator's factor). Draft
                # operators and draft target claims are excluded (#780).
                conds = []
                if live_c:
                    conds.append(live_c)
                if live_o:
                    conds.append(live_o)
                where = (" WHERE " + " AND ".join(conds)) if conds else ""
                rows = self.g.query(
                    "MATCH (o:Point {id:$oid})-[r:IMPL|NAND]->(c:Point) "
                    f"{where} "
                    "RETURN DISTINCT c.id",
                    params={"oid": seed_id},
                ).result_set
            else:
                # Plain-point seed (#888 W5): direct edges in BOTH directions
                # (an operator-less edge is a factor shared by its endpoints),
                # plus the seed's operator-mediated neighborhood via
                # _neighbors so a seed whose only connections are
                # operator-mediated still runs its incident factors. Draft
                # endpoints are excluded (#780) — a draft seed runs nothing.
                conds = ["b.id <> $id"]
                if live_b:
                    conds.append(live_b)
                if live_a:
                    conds.append(live_a)
                where = " WHERE " + " AND ".join(conds)
                rows = self.g.query(
                    "MATCH (a:Point {id:$id})-[r:IMPL|NAND]-(b:Point) "
                    f"{where} "
                    "AND (b.is_operator IS NULL OR b.is_operator = false) "
                    "AND b.op_type IS NULL "
                    "RETURN DISTINCT b.id",
                    params={"id": seed_id},
                ).result_set
                if not seed_is_draft:
                    for nid in self._live_neighbors(seed_id, include_draft):
                        affected.add(nid)
            affected.update(r[0] for r in rows)

        if max_hops > 0 and affected:
            frontier = list(affected)
            for _ in range(max_hops):
                # Strip drafts from the frontier BEFORE expanding: a draft
                # claim must never propagate to live claims (#780).
                if not include_draft:
                    draft_ids = self._filter_draft_ids(set(frontier))
                    if draft_ids:
                        affected -= draft_ids
                        frontier = [n for n in frontier if n not in draft_ids]
                        if not frontier:
                            break
                new_frontier: list[str] = []
                for claim_id in frontier:
                    for nid in self._live_neighbors(claim_id, include_draft):
                        if nid not in affected:
                            affected.add(nid)
                            new_frontier.append(nid)
                    # Operator-less hops (#888 W5): direct IMPL/NAND edges
                    # between plain Points (operator-mediated hops above).
                    # Draft endpoints never propagate (#780).
                    conds = ["b.id <> $id"]
                    if live_a:
                        conds.append(live_a)
                    if live_b:
                        conds.append(live_b)
                    where = " WHERE " + " AND ".join(conds)
                    dir_rows = self.g.query(
                        "MATCH (a:Point {id:$id})-[r:IMPL|NAND]-(b:Point) "
                        f"{where} "
                        "AND (a.is_operator IS NULL OR a.is_operator = false) "
                        "AND a.op_type IS NULL "
                        "AND (b.is_operator IS NULL OR b.is_operator = false) "
                        "AND b.op_type IS NULL "
                        "RETURN DISTINCT b.id",
                        params={"id": claim_id},
                    ).result_set
                    for (nid,) in dir_rows:
                        if nid not in affected:
                            affected.add(nid)
                            new_frontier.append(nid)
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

    def _filter_draft_ids(self, ids: set[str]) -> set[str]:
        """Return the subset of ids whose nodes have status == 'draft' (#780)."""
        if not ids:
            return set()
        rows = self.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids AND n.status = 'draft' "
            "RETURN n.id",
            params={"ids": list(ids)},
        ).result_set
        return {r[0] for r in rows}

    def _live_neighbors(self, node_id: str, include_draft: bool) -> list[str]:
        """Operator-mediated neighborhood hop that never crosses drafts (#780).

        Mirrors proj._neighbors (propagation.py) but excludes hops THROUGH
        draft operator nodes and TO draft endpoints — a draft-connected
        operator must change NO live claim's posterior, so the affected-set
        expansion must not reach live claims via a draft bridge.
        """
        if include_draft:
            return self.proj._neighbors(node_id)
        # Operator detection matches Batch 1 (_affected_factors): a Point is
        # an operator when is_operator=true OR op_type is set (legacy nodes —
        # projection/__init__.py treats bool(is_operator or op_type) as
        # operator). Matching only {is_operator:true} would leave legacy
        # operator bridges invisible to the draft exclusion (#943 review).
        rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r]-(op:Point)-[r2]-(m:Point) "
            "WHERE m.id <> $id "
            "AND (op.is_operator = true OR op.op_type IS NOT NULL) "
            "AND (op.status IS NULL OR op.status <> 'draft') "
            "AND (m.status IS NULL OR m.status <> 'draft') "
            "RETURN DISTINCT m.id",
            params={"id": node_id},
        ).result_set
        return [r[0] for r in rows]

    def _affected_factors(self, affected_claims: set[str],
                          include_draft: bool = False
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
        """
        live_o = _live_only("o.status", include_draft)
        live_c = _live_only("c.status", include_draft)
        live_a = _live_only("a.status", include_draft)
        live_b = _live_only("b.status", include_draft)
        factors: list[tuple[str, str, list[str], float, str | None, str]] = []
        if not affected_claims:
            return factors

        from .weights import compute_operator_weight, NAND_BASE_WEIGHT

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
        rows = self.g.query(
            "MATCH (o:Point)-[r:IMPL|NAND]->(c:Point) "
            f"WHERE {where_b1} "
            "RETURN DISTINCT o.id, o.op_type, o.label, o.direction",
            params={"ids": list(affected_claims)},
        ).result_set
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
        dir_rows = self.g.query(
            "MATCH (a:Point)-[r:IMPL|NAND]->(b:Point) "
            "WHERE (a.is_operator IS NULL OR a.is_operator = false) "
            "AND a.op_type IS NULL "
            "AND (b.is_operator IS NULL OR b.is_operator = false) "
            "AND b.op_type IS NULL "
            "AND (a.id IN $ids OR b.id IN $ids) "
            f"{('AND ' + live_a + ' AND ' + live_b + ' ') if live_a else ''}"
            "RETURN a.id, b.id, type(r), coalesce(r.direction, 'bidirectional'), "
            "       r.weight",
            params={"ids": list(affected_claims)},
        ).result_set
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
        # Raw input statuses (None/live/draft) for diagnostic surfacing (#992):
        # when an operator goes degenerate we name every input's status so the
        # silent-confidence-zero is traceable to the offending draft inputs.
        op_input_status: dict[str, list[str | None]] = {op_id: [] for op_id in op_info}
        # idx_known flags operators whose EVERY input edge carries an idx —
        # create_operator always writes idx (source=0 first). Legacy/migrated
        # operators may have idx-less edges (all coalesce to 0): position 0
        # is then NOT provably the source, so the directional source-slot
        # guard must not fire (it could check the wrong slot — #943 review).
        op_idx_known: dict[str, bool] = {op_id: True for op_id in op_info}
        rows = self.g.query(
            "MATCH (o:Point)-[r:IMPL|NAND]->(c:Point) "
            "WHERE o.id IN $ids "
            "RETURN o.id, c.id, r.idx, c.status "
            "ORDER BY coalesce(r.idx, 0), c.id",
            params={"ids": list(op_info.keys())},
        ).result_set
        for op_id, claim_id, idx, status in rows:
            op_inputs[op_id].append(claim_id)
            op_input_live[op_id].append(
                include_draft or status is None or status != "draft"
            )
            op_input_status[op_id].append(status)
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
            if not include_draft:
                input_ids = [
                    cid for cid, live in zip(full_inputs, op_input_live[op_id])
                    if live
                ]
                stripped = len(full_inputs) - len(input_ids)
                if stripped:
                    if (direction != "bidirectional"
                            and op_idx_known[op_id]
                            and full_inputs
                            and not op_input_live[op_id][0]):
                        # The idx-0 SOURCE was a draft — keeping this factor
                        # would renumber a live target into the source slot
                        # and invert directional semantics (#780 review-fix).
                        logger.warning(
                            "Operator %s: draft source stripped — factor skipped "
                            "(non-bidirectional with draft source, #780)",
                            op_id,
                        )
                        continue
                    if len(input_ids) < 2 <= len(full_inputs):
                        # Draft-caused degradation below 2 live inputs — a
                        # draft-connected operator must change NO live
                        # posterior (#780); matches the SVBP-path convention.
                        # Name the operator + every input's status so the
                        # silent zero-confidence is traceable (#992).
                        logger.warning(
                            "Operator %s: %d/%d inputs draft — factor skipped "
                            "(degenerate, #780). Inputs: [%s]",
                            op_id, stripped, len(full_inputs),
                            ", ".join(
                                f"{cid.split('-')[-1]}={s or 'live'}"
                                for cid, s in zip(full_inputs, op_input_status[op_id])
                            ),
                        )
                        continue
            else:
                input_ids = full_inputs
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
            evidence: dict[str, tuple[float, float]] | None = None,
            include_draft: bool = False) -> tuple[int, bool]:
        """Run EP to convergence. Batch I/O avoids SQLite crashes (#6761).

        Args:
            operator_ids: operator node IDs to include — plain point IDs are
                also accepted as seeds: a plain seed runs the operator-less
                direct edges it participates in plus its operator-mediated
                neighborhood (#888 W5)
            max_hops: how far to extend the affected subgraph
            evidence: optional {claim_id: (alpha, beta)} priors —
                merged with any evidence set at construction time.
                Evidence set at run() overrides per-claim.
            include_draft: when True, draft Points/operators participate in
                EP identically to live ones (#780 escape hatch — legacy
                behavior). Default False: drafts are excluded at ALL four
                factor-extraction call sites.
        """
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

        affected = self._affected_claims(operator_ids, max_hops,
                                         include_draft=include_draft)
        if not affected:
            if not include_draft and operator_ids:
                logger.warning(
                    "EP run seeded with %d id(s) produced no affected claims — "
                    "draft Points/operators are excluded by default (#780); "
                    "pass include_draft=True to include them.",
                    len(operator_ids),
                )
            return 0, True

        factors = self._affected_factors(affected, include_draft=include_draft)
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
