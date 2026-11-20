# Archived sdk-method `issue_insight` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_issue_insight`; recorded destination `search_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'issue_insight'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def issue_insight(self, title: str, body: str | None = None,
                      repo: str | None = None, limit: int = 2) -> dict:
        """Return a compact 'there's more in the graph' insight for a would-be issue.

        Surface (b) of #1196: called by the creating agent at issue-creation time
        BEFORE filing. Two stages:
          * Semantic (always): hybrid search on title+body for cross-session
            decisions / EP-tagged claims ('we already decided this'). Hits must
            clear a relevance gate (measured belief >= 0.5, or >= 2 shared tokens
            with the query) — otherwise the stage reports no matches instead of
            counting false positives. Gate-passing hits are then re-ranked
            (#3277) so an EP-confirmed claim WITH incoming evidence precedes
            unmeasured lexical matches BEFORE `[:limit]` — the shipped default
            limit=2 otherwise truncated the 'we already decided this' signal
            away (see `_issue_insight_measured_ep`'s #3276 caveats: the
            predicate does not prove measurement).
          * Repo (when repo= given): structural count of indexed GitHub
            observation points for that repo (source='github').
        Fail-closed: empty graph -> no_prior_knowledge; repo given + graph
        non-empty + zero points for repo -> repo_not_indexed. An out-of-domain
        `limit` (not a positive int) is REFUSED with `ValueError` before any
        work — the ONE input this method refuses ITSELF. Every other failure,
        including a graph-service fault, PROPAGATES: this body has no try/
        except. Conversion is the BOUNDARY's job (_safe → dict, REST → 500).
        data_points never exceeds `limit` rows (repo-stage append is capped).

        #8447 — every reply carries `truncated`, true when `data_points` are NOT
        the whole set this read matched. THREE seams make it true and the caller
        can reach none of them: (1) the `[:limit]` cut on the ranked hits, (2) a
        candidate pool that saturated ITS window, so matches past it were never
        read, and (3) an ASKED retrieval leg that never answered — a read where
        nothing was cut from the visible set and the missing rows are just as
        unreachable. `total_available` rides it ONLY when the count is EXACT, and
        "exact" is the RETRIEVAL path's own proof RELAYED (never re-derived
        here). That proof is withheld by a saturated window or a lost leg, and
        independently by any post-window filter that shrank the union and by the
        snapshot fallback arm — so this docstring does not try to enumerate it.
        What matters to a caller: `total_available` is either the true population
        or ABSENT (unknown, never zero); read `truncated` BEFORE `data_points`.
        ⛔ A pool at EXACTLY its own bound (`max(limit*5, 20)`) is exact ONLY while
        the STORE stays below the retrieval window (`max(120, 2*pool_bound)`) — not
        while `limit` is small: 120 matches withhold the total at `limit=2` too.
        """
        # #8447: `limit` is a CALLER INPUT, so an out-of-domain ask is REFUSED
        # before any work — never silently re-interpreted. Measured before this
        # guard: `limit=0` delivered no rows and answered "No graph matches"
        # beside `total_available: 6` (prose contradicting the field next to
        # it), and `limit=-1` sliced the tail off the list and served 5 of 6 —
        # a silent subset from a negative cap. Parity, not a new bound: the REST
        # route already validates `Query(2, ge=1, le=20)` and the sibling verb
        # `tortoise_fts_query` refuses `limit must be 1-10000` the same way.
        # `bool` is an `int` SUBCLASS, so `isinstance(True, int)` is True and a
        # bool ask would otherwise be served as `limit=1` — a re-interpreted
        # caller input, which is the defect this guard exists to refuse, and a
        # parity break with the REST mirror (`GET /v1/issue-insight?limit=true`
        # → 422 from `Query(2, ge=1, le=20)`). Same explicit bool test the
        # #8447 page-size seam uses (`bool` is checked FIRST: the sibling
        # comparisons are value tests and a bool passes them).
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(
                f"issue_insight: limit must be a positive int, got {limit!r}")
        proj = self._get_proj()
        count_rows = proj.g.query(
            "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN count(n)"
        ).result_set
        total_points = int(count_rows[0][0]) if count_rows else 0

        # #8447: the TRUNCATION marker's sink for THIS response. Seeded COMPLETE
        # so every return below is a POSITIVE claim rather than an un-marked
        # body the caller must guess about, and then handed to the retrieval
        # call so the marker's own proof (a lost leg, a saturated pool) is MADE
        # ONCE, by the code that drops the rows. Exactness is RELAYED from that
        # proof; only the "was the pool itself cut" gate is added here.
        from .truncation import EXACT_KEY as _EXACT_KEY
        from .truncation import mark_truncation as _mark_truncation
        from .truncation import truncation_field as _truncation_field
        _trunc_out: dict = {"truncated": False}

        def _with_truncation(response: dict) -> dict:
            """Carry the #8447 marker on EVERY response path of this read.

            `truncation.truncation_field` refuses to guess when the marker is
            absent, so a producer bug is a loud 500 at the handler rather than
            a silently-complete answer — the same posture `/v1/search` takes.
            """
            response.update(_truncation_field(_trunc_out))
            return response

        text = " ".join(p for p in (title or "", body or "") if p and p.strip()).strip()
        semantic_hits: list[dict] = []
        #: hits that cleared the relevance gate, BEFORE the `[:limit]` cut — the
        #: honest population the cut was applied to, and the number the
        #: `insight` sentence reports (post-cut it under-reported: a 17-hit
        #: read at the shipped `limit=2` said "2 graph hit(s) relate").
        relevant_hits = 0
        #: Is the count EXACT? RELAYED from the marker's own recorded proof
        #: (``truncation.EXACT_KEY``) AND gated on the pool not having been cut —
        #: ⛔ never re-derived from the pool's LENGTH. A local
        #: ``len(pool) >= pool_limit`` test marks a pool that returned exactly
        #: its bound as cut (the retrieval path keeps every row there and says
        #: COMPLETE), which withholds an exact total and trains callers to ignore
        #: the marker — the round-2 defect, and what this variable must not
        #: reintroduce (#8447 review round 5, P3).
        _exact = False
        if text:
            # Fetch a wider candidate pool than `limit` (review P3): the gate
            # below filters post-retrieval, so a relevant hit ranked just past
            # the limit would otherwise be invisible at small limits.
            # #8487: a CANDIDATE pool — ``_issue_insight_rank(...)[:limit]``
            # re-owns the order below, so the SDK-side validity selection could
            # only move which ids survive that cut. Same shape as
            # recall_state's opt-out.
            pool_limit = max(limit * 5, 20)
            semantic_hits = self.tortoise_fts_query(
                text, entity_type="point", limit=pool_limit,
                _record_reads=False,  # #7983: a candidate pool
                _valid_time_rerank=False,  # #8487: caller re-owns the order
                _rerank_candidates=False,  # #12297: same re-owned order
                _truncation_out=_trunc_out,  # #8447: the retrieval path's proof
            )
            # The retrieval path's verdict: it dropped rows at the pool bound,
            # or an ASKED leg never answered. Either way matches this read would
            # have seen were never read, so no exact total is available and the
            # answer is incomplete EVEN IF the gate below delivers everything it
            # kept (``truncation.mark_truncation``'s ``incomplete`` case).
            retrieval_incomplete = bool(_trunc_out.get("truncated"))
            # ⛔ ``_exact`` is RELAYED from the marker's own recorded proof, never
            # re-derived: ``truncated: False`` is NOT ``exact`` — it is also what
            # a read reports when it completed but had to WITHHOLD exactness. A
            # local ``len(pool) >= pool_limit`` test is worse still: it marks a
            # pool that came back at EXACTLY its bound as cut, which is a false
            # positive that withholds an exact total and trains callers to ignore
            # the marker (#8447 review round 2). ⚠️ NOR is that bound a guarantee:
            # below the window those rows are kept in full; AT it, the read saturated.
            #
            # ⛔ BOTH halves are required, and they answer different questions.
            # ``retrieval_incomplete``: the pool WAS cut, so the relevance gate
            # only saw part of the match population — `relevant_hits` is a FLOOR
            # even though the retrieval path's own candidate count was exact
            # (measured: 25 matches at limit=2, pool bound 20 → inner
            # `{truncated: True, total_available: 25}`; the 5 unseen rows could
            # clear the gate too, so publishing 20 as a total would be a lie).
            # ``_EXACT_KEY``: the retrieval path PROVED its candidate set is the
            # whole population; without it, a completed-but-exactness-withheld
            # read would publish its survivors as a total.
            _exact = (bool(_trunc_out.get(_EXACT_KEY))
                      and not retrieval_incomplete)
            # c70: drop hits that clear neither the EP-confidence floor nor a
            # shared-token floor (TF-IDF fallback hits carry no EP annotation;
            # FTS-mode hits are EP-annotated but unmeasured points must still
            # show real lexical overlap to count).
            semantic_hits = [
                h for h in semantic_hits
                if self._issue_insight_relevant(h, text)
            ]
            relevant_hits = len(semantic_hits)
            # #3277: re-rank BEFORE truncating — retrieval order is
            # mode-dependent (#3254/#2573), so two unmeasured lexical matches
            # can precede a measured EP-confirmed claim and [:limit] at the
            # shipped default (2) then drops the decision entirely. See
            # _issue_insight_rank; the [:limit] cap still holds (reorder, not
            # exempt), so `data_points <= limit` is preserved.
            semantic_hits = self._issue_insight_rank(semantic_hits)[:limit]
            _mark_truncation(
                _trunc_out, returned=len(semantic_hits),
                candidates=relevant_hits, exact=_exact,
                incomplete=retrieval_incomplete)

        repo_points: list[dict] = []
        if repo:
            repo_points = self.query(
                kind="observation", source="github", github_repo=repo,
                _record_reads=False,  # #7983: an internal population scan
            )

        if total_points == 0:
            return _with_truncation({
                "has_prior": False,
                "no_prior_knowledge": True,
                "repo_not_indexed": False,
                "data_points": [],
                "insight": "The graph has no prior knowledge yet — nothing to pull. "
                            "Run tortoise_manage_sources(action=\"enable\", "
                            "source=\"github_issues\") to seed it — that call "
                            "indexes EVERY connected repo, not one.",
                "more_in_graph": None,
                "repo_stats": None,
            })

        # #7983: the semantic pool opted out above; the hits that SURVIVE the
        # gate are what the caller receives, so record those (this is the read).
        _ledger_record("Point", semantic_hits, kind="vector")
        data_points: list[dict] = []
        for h in semantic_hits:
            dp = {"kind": h.get("point_kind"), "content": (h.get("content") or "")[:200]}
            if h.get("ep") and h["ep"].get("confidence_mean") is not None:
                dp["confidence_mean"] = round(h["ep"]["confidence_mean"], 3)
            data_points.append(dp)

        repo_stats = None
        if repo:
            repo_stats = {
                "repo": repo,
                "prior_issues": len(repo_points),
                "open": sum(1 for p in repo_points if p.get("github_state") == "open"),
            }
            # Cap the repo-stage append so data_points never exceeds `limit`
            # (semantic hits are already truncated to `limit` above).
            if repo_points and len(data_points) < limit:
                data_points.append({
                    "kind": "repo",
                    "content": f"{repo}: {len(repo_points)} prior issue(s) in the graph",
                })

        if repo and not repo_points:
            return _with_truncation({
                "has_prior": True,
                "no_prior_knowledge": False,
                "repo_not_indexed": True,
                "data_points": data_points,
                "insight": f"Graph is populated but '{repo}' has no indexed issues yet — "
                            "run tortoise_manage_sources(action=\"enable\", "
                            "source=\"github_issues\") to index it. That call has "
                            "no per-repo scope: it indexes EVERY connected repo, "
                            f"so '{repo}' is covered only if it is one of them.",
                "more_in_graph": None,
                "repo_stats": None,
            })

        # c70: nothing cleared the semantic gate -> "no matches", even when the
        # repo stage found points (repo context is still reported via repo_stats).
        if not semantic_hits:
            # ⛔ "No graph matches" is a POSITIVE claim of absence, and today it
            # can be produced by a read that dropped rows (a saturated pool, a
            # lost leg). The marker is what tells those two apart — the caller
            # may not assert absence over a read the store could not complete.
            return _with_truncation({
                "has_prior": False,
                "no_prior_knowledge": False,
                "repo_not_indexed": False,
                "data_points": [],
                # ⛔ Keyed on the MARKER's own verdict, never on `_exact`: a
                # read can be exactness-withheld (a post-window filter, the
                # snapshot fallback arm) while nothing was dropped, and telling
                # that caller "the read was TRUNCATED" would be prose
                # contradicting the field next to it — the same defect this
                # branch exists to remove, inverted (#8447 review round 4).
                "insight": (
                    "No graph matches for this issue title."
                    if not _trunc_out.get("truncated") else
                    "No graph matches among the matches this read could see — "
                    "the read was TRUNCATED (see `truncated`), so absence is "
                    "NOT proven."),
                "more_in_graph": None,
                "repo_stats": repo_stats,
            })

        top = semantic_hits[0]
        return _with_truncation({
            "has_prior": True,
            "no_prior_knowledge": False,
            "repo_not_indexed": False,
            "data_points": data_points,
            # `relevant_hits` (pre-cut), not `len(semantic_hits)` (post-cut):
            # a 17-hit read at the shipped `limit=2` used to say "2 graph hit(s)
            # relate" — a wrong statement, not merely an incomplete one.
            #
            # ⛔ And it is QUALIFIED when the count is not exact: a saturated
            # pool makes `relevant_hits` a LOWER BOUND (which is why
            # `total_available` is withheld on that read), so publishing it as
            # an unqualified count would reintroduce the same defect one level
            # down — a floor wearing a total's clothes, in prose this time.
            "insight": (
                f"{'' if _exact else 'at least '}{relevant_hits} graph hit(s) "
                "relate to this issue — check the graph before filing."),
            "more_in_graph": ((top.get("content") or "")[:80] if top else None),
            "repo_stats": repo_stats,
        })
