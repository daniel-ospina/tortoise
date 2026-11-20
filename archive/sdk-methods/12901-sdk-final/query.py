# Archived sdk-method `query` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_query`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'query'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def query(self, kind: str | None = None,
              *, include_retracted: bool = False,
              _record_reads: bool = True,
              **filters) -> list[dict]:
        """Query points by pointKind and/or custom property filters.

        #432 Task 2: retracted points (status='retracted') are EXCLUDED by
        default — pass include_retracted=True, or an explicit status= filter
        (e.g. status='retracted'), to surface tombstones.

        For confidence-aware queries, use tortoise_fts_query() with query=None
        for full-scan mode with EP annotation.

        #1391: the default exclusion now covers ALL terminal statuses
        (``live.TERMINAL_EXCLUDED_STATUSES``: retracted, superseded, outdated,
        archived, deprecated) — stale claims are not served as current.
        include_retracted (name kept for compat) surfaces them for audit. Use
        a raw Cypher query via proj.g.query() to inspect tombstones.
        """
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
        rows = proj.g.query(
            f"MATCH (n:Point) WHERE {where} RETURN properties(n)",
            params=params,
        ).result_set
        points = [r[0] for r in rows]
        # #7983: `_record_reads=False` is for the SDK's OWN population scans —
        # `recall_gaps`, `issue_insight` and the FTS TF-IDF fallback. They are not
        # a caller reading these keys: recording them would mark the ENTIRE stock
        # read after one `recall_gaps()` call (f -> ~1) and bias `f` HIGH, the
        # direction that removes the saving the ledger exists to measure.
        if _record_reads:
            _ledger_record("Point", points, kind="lookup")
        return points
