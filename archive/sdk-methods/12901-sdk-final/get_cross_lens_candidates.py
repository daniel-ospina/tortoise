# Archived sdk-method `get_cross_lens_candidates` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_get_cross_lens_candidates`; recorded destination `review_link_candidates` (#12901 final batch, #4282 Phase 2).
# source:  tortoise/sdk.py @ 1231060fd2be9205b9b3bba1fd315e9b19b3602e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'get_cross_lens_candidates'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def get_cross_lens_candidates(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        max_candidates: int = CROSS_LENS_CANDIDATE_CAP,
        routing: str = "truth",
        top_k: int = CROSS_LENS_ANN_TOP_K,
    ) -> dict:
        """Surface unverified cross-lens candidate pairs (#438, bring-your-own-agent).

        READ-ONLY — never writes to the graph and never decides operator
        semantics (the candidates-never-write/never-decide contract extends
        from cross_lens.py to this surface). Returns candidate pairs between
        Points from DIFFERENT sources (cross-stream discovery over the
        existing HNSW vector index) with lens pair, cosine similarity, point
        context, and dedup vs existing operators. The customer agent confirms
        candidates and writes operators through the normal API (no in-repo
        verifier — D7).

        Contract (2026-08-15 scoping, docs/scoping/
        2026-08-15-438-cross-domain-discovery-scoping.md):
          - D2: the payload carries a single ``routing`` field ("truth" |
            "relevance", #901 semantics). The surface stays NEUTRAL — no
            op_type / suggested_relation hint; the caller's declared routing
            context is echoed per candidate (deciding semantics is the
            customer agent's job).
          - D3: candidates are gated on a REGISTERED sourceKind (any tier) —
            Points whose Source has an unregistered sourceKind (or no Source
            at all) never appear, since unregistered kinds stay neutral
            (#398 / source_credibility.SOURCE_KIND_DEFAULTS).
          - D4: hard cap 200 candidates/cycle — ``max_candidates`` is clamped
            to CROSS_LENS_CANDIDATE_CAP (predictable per-cycle cost; local
            pre-filter tuning is #909's territory).
          - D8: empty results (not errors) when there is nothing to see.

        Discovery cost: bounded ANN pull over the existing Point.embedding
        vector index (HNSW on Docker/server FalkorDB; brute-force euclidean
        fallback on embedded — the documented degradation) with ``top_k``
        neighbors per pool point, then exact cosine recompute from stored
        embeddings (index scores are rank/euclidean, not the calibrated
        cosine). The scanned pool is capped at CROSS_LENS_POOL_CAP
        most-recently-updated eligible points — pair enumeration is
        pool-bounded (each pool point contributes at most ``top_k``
        neighbors, candidate space O(pool x top_k)), not O(n²) over the
        whole graph (indicator I3). Cost caveat: on the embedded backend
        run_vector_query is a brute-force scan over the ENTIRE Point index,
        so embedded recall is O(pool x total_points) — the pool cap bounds
        the pair space, not the per-query scan cost (the documented
        degradation).

        Recall caveat: the default top_k=20 favors dense recent clusters —
        same-source near-duplicates (D5-excluded territory) can crowd the
        top-k and crowd out genuine cross-lens pairs. Raise top_k (up to
        the hard cap) or lower ``threshold`` when cross-lens recall looks
        thin.

        Args:
            threshold: minimum cosine similarity (default 0.72 — the
                bge-small DEFAULT_THRESHOLD cross-vocabulary band from
                tortoise/embeddings.py; near-duplicates are 0.89+).
            max_candidates: requested result cap — HARD-clamped to 200 (D4).
            routing: the #901 routing context the caller is mining under
                ("truth" | "relevance") — echoed per candidate; the surface
                itself stays neutral.
            top_k: ANN neighbors pulled per pool point (recall bound) —
                HARD-clamped to CROSS_LENS_ANN_TOP_K_MAX (100) so an agent
                cannot inflate the per-cycle recall budget (D4 philosophy).

        Returns:
            {"candidates": [{src, dst, similarity, lenses, sourceKinds,
              src_content, dst_content, src_source, dst_source, routing}],
             "count": N, "cap": 200, "truncated": bool, "routing": str}.
            Candidates sorted by similarity descending.

        Raises ValueError on invalid routing/threshold/max_candidates/top_k.
        """
        if routing not in ("truth", "relevance"):
            raise ValueError(
                f"routing must be 'truth' or 'relevance' (#901), got {routing!r}"
            )
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"threshold must be 0.0-1.0, got {threshold!r}")
        if max_candidates < 1:
            raise ValueError(f"max_candidates must be >= 1, got {max_candidates!r}")
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k!r}")
        top_k = min(int(top_k), CROSS_LENS_ANN_TOP_K_MAX)  # hard cap (conf 75)

        limit = min(int(max_candidates), CROSS_LENS_CANDIDATE_CAP)  # D4
        pool = self._cross_lens_pool()
        if len(pool) < 2:
            return {"candidates": [], "count": 0,
                    "cap": CROSS_LENS_CANDIDATE_CAP,
                    "truncated": False, "routing": routing}

        connected = self._connected_pairs(list(pool))
        pairs = self._cross_lens_pairs(pool, threshold=threshold, top_k=top_k)

        all_candidates: list[dict] = []
        for (a, b), score in pairs:
            if frozenset((a, b)) in connected:
                continue  # dedup vs existing operators (Slice 1)
            pa, pb = pool[a], pool[b]
            all_candidates.append({
                "src": a,
                "dst": b,
                "similarity": round(score, 6),
                "lenses": [pa["source"], pb["source"]],
                "sourceKinds": [pa["sourceKind"], pb["sourceKind"]],
                "src_content": pa["content"],
                "dst_content": pb["content"],
                "src_source": pa["source_id"],
                "dst_source": pb["source_id"],
                "routing": routing,
            })

        truncated = len(all_candidates) > limit
        candidates = all_candidates[:limit]
        return {"candidates": candidates, "count": len(candidates),
                "cap": CROSS_LENS_CANDIDATE_CAP,
                "truncated": truncated, "routing": routing}
