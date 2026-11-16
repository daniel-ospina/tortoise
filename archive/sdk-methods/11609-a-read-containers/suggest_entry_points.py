# Archived sdk-method `suggest_entry_points` — REMOVED from the product surface (#11609).
#
# batch:   11609-a-read-containers
# reason:  retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container
# source:  tortoise/sdk.py @ 32011c4dad4b42364868cb0810ad8e7317a7dfb6
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'suggest_entry_points'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def suggest_entry_points(self, query: str, *, limit: int = 5,
                             kind_filter: str | None = None,
                             graph_ranker=None) -> list[dict]:
        """Entity resolution — NL query → matching entities from the graph.

        String match on content (Cypher CONTAINS) + embedding fallback.
        Returns [{id, name, kind, confidence}] sorted by confidence DESC.
        kind_filter filters by n.pointKind.
        graph_ranker: optional GraphRanker (tortoise.ranking) to rerank the
        results with graph signals (persisted EP confidence, connectivity,
        recency) — #25. Off by default for backward compatibility.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        q = query.strip()[:500]  # ponytail: bound to 500 chars; embedding APIs have token limits
        if not q:
            return []

        proj = self._get_proj()
        clauses = ["n.is_operator = false",
                   "toLower(n.content) CONTAINS toLower($q)"]
        params = {"q": q}
        if kind_filter:
            clauses.append("n.pointKind = $kf")
            params["kf"] = kind_filter

        where = " AND ".join(clauses)
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} "
            "RETURN n.id, n.content, n.pointKind",
            params=params,
        ).result_set

        results = []
        q_lower = q.lower()
        for pid, content, kind in rows:
            # ponytail: guard empty content (stub nodes may have '')
            if not content:
                continue
            # Confidence formula (#22): exact match → 1.0, partial match →
            # [0.5, 1.0) via length ratio, smoothed to avoid scale collapse.
            # len(q)/len(content) alone would give 0.001 for 1-char in 1000-char
            # doc and 0.5 for 5-char in 10-char — not comparable. The 0.5 offset
            # ensures all substring matches score ≥ 0.5, reserving [0, 0.5) for
            # the hybrid fallback path (which has no substring match at all).
            # The fallback band-normalizes its RRF scores into [0, 0.5) (#22).
            if content.lower() == q_lower:
                confidence = 1.0
            else:
                ratio = len(q) / len(content)
                confidence = round(0.5 + 0.5 * ratio, 4)
            results.append({"id": pid, "name": content, "kind": kind or "", "confidence": confidence})

        results.sort(key=lambda r: r["confidence"], reverse=True)
        results = results[:limit]

        # Hybrid fallback if no string matches (Phase 0, #7748).
        # Confidence contract (#22): fallback results must live in the [0, 0.5)
        # band reserved by the substring-match formula above. Raw RRF scores are
        # NOT comparable to that band — rank-based fusion caps near 0.016 per
        # ranked list (~0.05 with 3 fused lists) while embedded FTS raw scores
        # are unbounded above — so `rrf * 0.5` landed anywhere from ~0.008 to
        # >1.0, tripping downstream conf > 0.3 thresholds. Band-normalize:
        # scale each RRF score by the set's max so the strongest fallback hit
        # lands at 0.49 (just under the 0.5 boundary) and weaker hits scale
        # proportionally. Invariant to the number of fused ranked lists (no
        # hardcoded multiplier).
        if not results:
            # #7983: a CANDIDATE pool — the fallback's rows are re-scored, some
            # dropped by the max_rrf gate and the rest reranked below, so what
            # the caller receives is recorded at the return instead.
            fts_results = self.tortoise_fts_query(
                q, limit=limit, _record_reads=False,
                # #8487: the fallback's rows are re-scored into a confidence
                # band below, so this is another candidate pool whose order the
                # caller re-owns.
                _valid_time_rerank=False,
                _rerank_candidates=False)  # #12297: same re-owned order
            results = []
            max_rrf = max(
                (r.get("scores", {}).get("rrf", 0.0) for r in fts_results),
                default=0.0,
            )
            # No fusion signal at all (max_rrf == 0, e.g. TF-IDF fallback with
            # all-zero similarity) → return NOTHING: every result would carry
            # confidence 0.0, which is indistinguishable from 'no match' and
            # pollutes suggest_entry_points with decoys for garbage queries
            # (stale test #test_no_match_returns_empty).
            if max_rrf <= 0:
                return []
            for r in fts_results:
                rrf = r.get("scores", {}).get("rrf", 0.0)
                confidence = round(0.49 * rrf / max_rrf, 4)
                results.append({"id": r["id"], "name": r.get("content", ""),
                                "kind": r.get("point_kind", ""), "confidence": confidence})
            results.sort(key=lambda r: r["confidence"], reverse=True)

        # #25: optional graph-informed rerank (persisted EP confidence,
        # operator connectivity, recency). Off by default (backward compat).
        if graph_ranker is not None and results:
            results = graph_ranker.rerank(results, entity_type="point", query=q)

        _ledger_record("Point", results)  # #7983: the rows this call SERVES
        return results
