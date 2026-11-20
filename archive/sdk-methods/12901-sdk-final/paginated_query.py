# Archived sdk-method `paginated_query` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_paginated_query`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'paginated_query'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def paginated_query(self, kind: str | None = None,
                        skip: int = 0, limit: int = 20,
                        *, include_retracted: bool = False,
                        **filters) -> dict:
        """Query points with pagination. Returns {results, total, hasMore}.

        #5346: the order is TOTAL and stable — newest first, ties broken by
        the internal node id — so walking `skip = 0, limit, 2*limit, …` until
        `hasMore` is False visits every row exactly once and yields `total`
        distinct results. Before the tiebreak the order was not total, so such
        a walk silently dropped and repeated rows (measured: 390 of 21,281
        never read on the hosted graph).

        #432 Task 2: retracted points (status='retracted') are EXCLUDED by
        default — pass include_retracted=True, or an explicit status= filter,
        to surface tombstones. #1391: the default exclusion now covers ALL
        terminal statuses (``live.TERMINAL_EXCLUDED_STATUSES``: retracted,
        superseded, outdated, archived, deprecated).
        """
        # #1914: invalid pagination params fail cleanly instead of looping
        # (limit=0 → hasMore = skip + 0 < total always True on non-empty
        # graphs → infinite pagination loop) or passing raw negatives into
        # Cypher. Mirrors the guard in tortoise_query (mcp_server.py) and the same
        # `limit` guard on several other reads (e.g. belief_timeline,
        # list_dedup_candidates, suggest_entry_points). This is the only SDK read with
        # a `skip`, so the `skip < 0` half has no peer.
        if skip < 0:
            raise ValueError(f"skip must be >= 0, got {skip}")
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        proj = self._get_proj()
        clauses = self._operator_scope_clauses(kind)
        params: dict[str, Any] = {}
        # #1391: terminal-status exclusion — skipped when the caller explicitly
        # filters by status (their filter controls visibility). include_retracted
        # (name kept for compat) surfaces ALL terminal statuses for audit.
        if not include_retracted and "status" not in filters:
            from .search_engine import _exclude_status_clause
            clauses.append(_exclude_status_clause("n"))
        if kind:
            expanded = self._read_filter_kinds(kind)
            if len(expanded) == 1:
                clauses.append("n.pointKind = $kind")
                params["kind"] = expanded[0]
            else:
                placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                clauses.append(f"n.pointKind IN [{', '.join(placeholders)}]")
                for i, k in enumerate(expanded):
                    params[f"kind_{i}"] = k
        for key, val in filters.items():
            # #329: strict ASCII identifier + reserved-key rejection (the old
            # isalnum check allowed Unicode keys that broke Cypher params and
            # filter keys colliding with kind/kind_N/skip/limit silently
            # overwrote auto-generated parameters).
            from .security import validate_filter_key
            validate_filter_key(key)
            clauses.append(_property_filter_clause(key))
            params[key] = val
        where = " AND ".join(clauses) if clauses else "true"
        total = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN count(n)",
            params=params,
        ).result_set[0][0]
        # #5346: the ORDER BY must be a TOTAL order or paging is silently
        # lossy. `n.createdAt` is NOT unique — one capture stamps a whole
        # session of turns with a single timestamp — and with only a
        # non-unique key the tie group's row order is not stable across the
        # SEPARATE query each page is: consecutive `SKIP`/`LIMIT` windows are
        # then not adjacent ranges of ONE order, so a page walk DROPS rows and
        # repeats others, while `hasMore` (computed from the `count(n)` total)
        # still reports the enumeration complete. Measured on this head
        # (FalkorDB graph module 4.20.x *and* embedded falkordblite), 3,000
        # Points sharing one `createdAt`, page size 500: 3,000 rows returned
        # over 6 pages, only 1,505 distinct ids — 1,495 never read.
        #
        # `id(n)` (the internal node id) is unique per node, so
        # `(createdAt, id(n))` is total and the windows partition it exactly.
        # Deliberately NOT the more obvious `n.id`: Point ids are NOT unique
        # (colliding ids are a tested shape — see the every-matching-node scan
        # in `_find_terminal_dedup_hit`), so it can leave a residual tie and
        # with it a residual loss window. Same tiebreak, same reasoning, as
        # `audit.py`'s check-8 scan, which pages on the identical defect.
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN properties(n)"
            f" ORDER BY n.createdAt DESC, id(n) DESC SKIP $skip LIMIT $limit",
            params={**params, "skip": skip, "limit": limit},
        ).result_set
        results = [r[0] for r in rows]
        # #7983: a product READ of these Point keys — the ledger's instrument for
        # `f` must see the structural query path, not only the FTS funnel, or the
        # keys it serves are reported as never read (the flattering direction for
        # the split's own saving).
        _ledger_record("Point", results, kind="lookup")

        return {"results": results, "total": total, "hasMore": skip + limit < total}
