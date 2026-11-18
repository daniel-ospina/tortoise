# Archived sdk-method `recall_gaps` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: recall_gaps left the public SDK surface — privatised (body survives as `_recall_gaps`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
# source:  tortoise/sdk.py @ 424368c05d8ee9b26aa2d77e4c5a93a4b207b58f
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'recall_gaps'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def recall_gaps(
        self,
        query: str | None = None,
        *,
        kind: str | None = None,
        limit: int = 20,
        min_load: int = 1,
        max_support: int = 2,
        include_superseded: bool = False,
        gaps_ranker=None,
    ) -> list[dict]:
        """UC2 "gaps" recall (epic #898 Wave B) — load-bearing claims that
        are themselves under-supported.

        Finds the weak links of a reasoning cycle: claims the graph leans on
        (they provide confidence to others via IMPL, or actively attack via a
        strong NAND) but that are poorly sourced/supported themselves (few
        incoming IMPL, no Source). This is a graph-STRUCTURE query (epistemic
        load vs epistemic support), NOT semantic similarity:

            load    = outgoing IMPL + outgoing NAND edge count
            support = incoming IMPL + extractedFrom→Source edge count
            score   = load / (1 + support)      # "load high AND support low"

        Reads IMPL/NAND edges whether operator-mediated or DIRECT (reification
        rule, ontology v3.5 §8) — see GapsRanker docstring for the edge
        semantics. Incoming NAND is surfaced as contention, never support.

        Args:
            query: optional topic scope (hybrid retrieval). One of query or
                kind must be provided — the population scan (kind) or the
                topic scope (query) defines the candidate pool.
            kind: pointKind to scan (full-scan mode, complete population).
            limit: max results (1-10000).
            min_load: only claims with load >= min_load are gaps (default 1
                — an isolated claim nothing leans on is NOT a gap).
            max_support: only claims with support <= max_support are gaps
                (default 2 — "few incoming IMPL, no Source" boundary).
            include_superseded: bring superseded/deprecated back (retracted
                stays hard-excluded, #689).
            gaps_ranker: injectable GapsRanker (tests / tuning).

        Returns the SearchResult shape (list of dicts), each annotated with
        ``entity_type`` and ``gaps_ranking`` (score breakdown).

        #2598: a DRAFT is not excluded from the candidate pool (a draft is
        CURRENT, not terminal), so a returned draft gap is rendered HONESTLY —
        ``gaps_ranking.draft: True``, ``confidence: None`` +
        ``confidence_source: "uncomputed"`` (never the fabricated neutral 0.5),
        and sorted after every live gap.
        """
        from .ranking import GapsRanker

        if query is None and kind is None:
            raise ValueError(
                "recall_gaps needs a topic query (topic scope) or a kind "
                "(population scan) to define the candidate pool")
        if limit < 1 or limit > 10000:
            raise ValueError(f"limit must be 1-10000, got {limit}")
        if min_load < 0:
            raise ValueError(f"min_load must be >= 0, got {min_load}")
        if max_support < 0:
            raise ValueError(f"max_support must be >= 0, got {max_support}")

        proj = self._get_proj()
        exclude_status = None if include_superseded else sorted(
            self.STATE_EXCLUDED_STATUS - {"retracted"})
        excluded_set = set(exclude_status or [])

        if query:
            # Topic scope: hybrid retrieval pool (operators can surface via
            # point retrieval — batch-filter them out below). Capped at the
            # retrieval layer's 10000 limit (P2: a valid large limit must
            # not blow the internal pool past it).
            pool = min(max(limit * 3, 50), 10000)
            results = self.tortoise_fts_query(
                query, kind=kind, entity_type="point", limit=pool,
                exclude_status=exclude_status,
                include_terminal=include_superseded,
                _record_reads=False,  # #7983: a candidate pool
                # #8487: GapsRanker.rerank re-sorts the pool and ``[:limit]``
                # is applied to THAT order, so this is the same
                # re-owned-order shape as recall_state.
                _valid_time_rerank=False,
                _rerank_candidates=False)  # #12297: same re-owned order
            pool_ids = [r["id"] for r in results if r.get("id")]
            op_ids = {
                row[0] for row in proj.g.query(
                    "MATCH (n:Point {is_operator:true}) WHERE n.id IN $ids "
                    "RETURN n.id", params={"ids": pool_ids}).result_set
            } if pool_ids else set()
            pool_results = [dict(r, entity_type="point") for r in results
                            if r["id"] not in op_ids]
            # #1391: retracted stays hard-excluded even when include_superseded
            # brings the other terminal statuses back (#689 leak guard).
            if include_superseded:
                pool_results = [p for p in pool_results
                                if p.get("status") != "retracted"]
        else:
            # Population scan: raw claim nodes (self.query already excludes
            # operators + terminal statuses; include_retracted surfaces
            # superseded/deprecated for the opt-in — retracted is then
            # re-filtered below by excluded_set).
            nodes = self.query(kind=kind,
                               include_retracted=include_superseded,
                               _record_reads=False)  # #7983: a population scan
            pool_results = []
            for n in nodes:
                if (n.get("status") or "") in excluded_set:
                    continue
                if (n.get("status") or "") == "retracted":
                    # #689 leak guard: retracted is never a gap candidate,
                    # even when include_superseded surfaces the others.
                    continue
                pool_results.append({
                    "id": n["id"],
                    "content": n.get("content") or "",
                    "point_kind": n.get("pointKind") or (kind or ""),
                    "status": n.get("status") or "",
                    "entity_type": "point",
                })

        # Mitigation bookkeeping points are surfaced attached to results, not
        # as standalone claims — same convention as recall_state.
        claims = [r for r in pool_results
                  if not (r.get("content") or "").startswith("[MITIGATION]")]

        ranker = gaps_ranker or GapsRanker(proj)
        ranked = ranker.rerank(claims)
        ranked = [
            r for r in ranked
            if r["gaps_ranking"]["load"] >= min_load
            and r["gaps_ranking"]["support"] <= max_support
        ][:limit]
        # #7983: the pool above opted out; the rows the caller RECEIVES are a read.
        _ledger_record("Point", ranked, kind="traversal")
        return ranked
