# Archived sdk-method `search_sessions` — REMOVED from the product surface (#11609).
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
ARCHIVED_SYMBOL = 'search_sessions'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def search_sessions(self, query: str, *, agent: str | None = None,
                        topics: list[str] | None = None,
                        after: "datetime | str | None" = None,  # noqa: F821, UP037
                        before: "datetime | str | None" = None,  # noqa: F821, UP037
                        limit: int = 10, offset: int = 0) -> list[dict]:
        """Search indexed agent sessions. Returns Events with metadata snippets.

        Routes through the hybrid engine (tortoise_fts_query entity_type='event'
        kind='AgentSession'): RRF fusion of FTS (Event subject+name index),
        vector (session embeddings computed at index time, #244) and structural
        (eventKind) strategies. Results are ordered by relevance with startedAt
        DESC as tiebreak.

        agent/topics post-filter the candidates. after/before bound the search
        to sessions whose ``startedAt`` falls in ``[after, before]`` (inclusive).
        Each may be a ``datetime`` or an ISO-8601 string (``Z`` or offset
        accepted); both are normalized to UTC ISO-8601 so the comparison
        against stored ``startedAt`` values is valid regardless of the caller's
        timezone. Sessions that lack a ``startedAt`` are EXCLUDED whenever a
        bound is set.

        When no semantic strategy contributed (FTS/vector unavailable — e.g.
        embedded FalkorDBLite without embeddings, prod before #160 deploys),
        falls back to the legacy keyword CONTAINS surface (name + keywords) so
        the previous behavior keeps working.
        """
        if not query or not query.strip():
            return []
        proj = self._get_proj()
        has_bound = after is not None or before is not None
        after_utc = _to_iso_utc(after) if after is not None else None
        before_utc = _to_iso_utc(before) if before is not None else None

        # ── Hybrid route: RRF fusion of FTS + vector + structural ──
        # Generous candidate pool — agent/topics/temporal filters drop rows
        # post-retrieval, so fetch beyond the caller's limit + offset.
        candidate_limit = max(limit * 5, 50) + offset
        hybrid = self.tortoise_fts_query(
            query, kind="AgentSession", entity_type="event",
            limit=candidate_limit,
            _record_reads=False,  # #7983: a candidate pool, sliced before return
            # #12297: this pool is RE-SORTED by ``scores["rrf"]`` below (the
            # PRE-rerank fused score) and then sliced — the same re-owned order
            # the ``_valid_time_rerank=False`` lanes opt out of. A rerank
            # here is a full permutation of the same list that the sort then
            # throws away: it cannot change the page, and it pays a model load
            # plus ~207 ms/mention to compute nothing. The trace said ``ok``
            # because nothing in the block knew the caller had opted out.
            _rerank_candidates=False,
        )
        has_semantic = any(
            r.get("scores")
            and (r["scores"].get("fts") is not None
                 or r["scores"].get("vector") is not None)
            for r in hybrid
        )
        if has_semantic and hybrid:
            # Precision gate (#244 review): rows with NO semantic signal — no
            # FTS score and no vector score (structural-only; the kind filter
            # matches ALL AgentSession events with score 1.0) are NOT results,
            # they only fed the RRF candidate pool. Note: vector-only rows are
            # deliberately kept — word-distinct semantic recall is the point
            # of #244 ("port migration" finds a session about changing the
            # FalkorDB default port); see docstring. The brute-force vector
            # strategy is threshold-less, so those rows are ranked nearest-
            # neighbors-first and precision drops when a query has no real
            # semantic match (documented behavior).
            ids = [r["id"] for r in hybrid]
            rows = proj.g.query(
                "MATCH (e:Event) WHERE e.eventId IN $ids RETURN properties(e)",
                params={"ids": ids},
            ).result_set
            props_by_id = {}
            for row in rows:
                props = dict(row[0])
                if props.get("eventId"):
                    props_by_id[props["eventId"]] = props
            ranked = []
            for r in hybrid:
                props = props_by_id.get(r["id"])
                if props is None:
                    continue
                if agent and props.get("agent") != agent:
                    continue
                if topics:
                    s_topics = set(props.get("topics") or [])
                    if not any(t in s_topics for t in topics):
                        continue
                if has_bound:
                    started = props.get("startedAt")
                    if not started:
                        continue  # sessions without startedAt excluded when a bound is set
                    if after_utc is not None and started < after_utc:
                        continue
                    if before_utc is not None and started > before_utc:
                        continue
                # Precision gate (#244 review): structural-only rows (no FTS,
                # no vector score) are NOT results — a session that neither
                # keyword-matches nor semantically matches must not appear.
                scores = r.get("scores") or {}
                if scores.get("fts") is None and scores.get("vector") is None:
                    continue
                ranked.append((scores.get("rrf") or 0.0, props))
            # Relevance (RRF) desc, startedAt DESC as tiebreak (missing = last)
            ranked.sort(
                key=lambda item: (item[0], item[1].get("startedAt") or ""),
                reverse=True,
            )
            out = [props for _, props in ranked[offset:offset + limit]]
            # #7983: the candidate pool opted out; the page handed back is a read.
            _ledger_record("Event", out)
            return out

        # ── Legacy keyword fallback (no FTS/vector contribution) ──
        # Preserves the pre-#244 CONTAINS surface (name + keywords) plus the
        # #243 temporal filters (after/before, ISO-8601 UTC normalization,
        # startedAt IS NOT NULL exclusion when a bound is set).
        clauses = ["e.eventKind = 'AgentSession'"]
        params: dict = {"limit": max(limit * 3, 30)}
        query_lower = query.strip().lower()
        clauses.append("(toLower(e.name) CONTAINS $q OR any(kw IN e.keywords WHERE toLower(kw) CONTAINS $q))")
        params["q"] = query_lower
        if agent:
            clauses.append("e.agent = $agent")
            params["agent"] = agent
        if topics:
            topic_clauses = []
            for i, t in enumerate(topics):
                pk = f"topic{i}"
                topic_clauses.append(f"${pk} IN e.topics")
                params[pk] = t
            clauses.append(f"({' OR '.join(topic_clauses)})")
        has_bound = after is not None or before is not None
        if has_bound:
            # Explicitly drop sessions without startedAt — same outcome as
            # null-comparison semantics, but self-documenting and robust.
            clauses.append("e.startedAt IS NOT NULL")
        if after is not None:
            clauses.append("e.startedAt >= $after")
            params["after"] = _to_iso_utc(after)
        if before is not None:
            clauses.append("e.startedAt <= $before")
            params["before"] = _to_iso_utc(before)
        where = " AND ".join(clauses)
        params["offset"] = offset
        # #5346: same total-order requirement as `paginated_query` — this
        # legacy fallback is reachable with an `offset` (e.g. an embedded
        # graph without embeddings, or one whose Event FTS index is absent,
        # both of which make `has_semantic` False), and `e.startedAt` is not
        # unique. Without a unique tiebreak the `SKIP`/`LIMIT` windows are not
        # adjacent ranges of one order: measured on this head, 3,000 sessions
        # sharing one `startedAt` paged at 500 returned only 2,105 distinct
        # ids — 895 never read. `id(e)` is unique per node, so
        # `(startedAt, id(e))` is total.
        rows = proj.g.query(
            f"MATCH (e:Event) WHERE {where} "
            "RETURN properties(e) "
            "ORDER BY e.startedAt DESC, id(e) DESC SKIP $offset LIMIT $limit",
            params=params,
        ).result_set
        # Fetch a bit extra for scoring headroom, but honor the caller's limit.
        out = [dict(r[0]) for r in rows[:limit]]
        _ledger_record("Event", out)  # #7983: a served page, not a pool
        return out
