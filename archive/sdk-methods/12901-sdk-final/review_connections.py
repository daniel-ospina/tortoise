# Archived sdk-method `review_connections` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_review_connections`; recorded destination `review_link_candidates` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'review_connections'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def review_connections(
        self,
        mode: str = "both",
        scope: str | None = None,
        *,
        # #1349 T14: derived from tortoise.embeddings DEFAULT_THRESHOLD
        # (0.72 for bge-small) — single source of threshold truth; a model
        # rotation recalibrates embeddings.py and this follows.
        similarity_threshold: float = DEFAULT_THRESHOLD,
        variance_threshold: float = 0.04,
        add_limit: int = 20,
        prune_limit: int = 50,
        similarity_fn=None,
    ) -> dict:
        """Review graph connections — the hygiene counterpart to connect
        (#913 W6, design: product/2026-08-11-tooling-surface-consolidation.md).

        READ-ONLY: surfaces suggestions and flags; never mutates the graph.
        The agent decides, then acts via create_operator / supersede / delete.

        mode=add: find relevant-but-MISSING connections. Pairs of Points that
            are semantically related (embedding cosine similarity above
            ``similarity_threshold``) but NOT yet connected (no shared
            operator, no direct edge — and not extracted from the same
            DOCUMENT Source: same-document pairs are near-duplicates by
            construction and are excluded, see _review_add) are surfaced as
            suggested connections: {from, to, suggested_relation: "IMPL",
            reason, similarity}. Suggestions only — nudge, don't enforce
            (design principle 4). A capture-session Source — the canonical
            ``session:<session_id>`` ref, or a pre-#4005 bare-filename
            ``agentSession`` Source — is capture provenance, not co-authorship
            of one document, so it does NOT exclude a pair; nor does a Source
            whose url is corrupt (a legacy list-valued url), which contributes
            nothing.
            Scope: ``scope`` (topic text or Point id) narrows the candidate
            pool via hybrid retrieval; None = whole graph, capped at
            REVIEW_ADD_POOL_CAP most-recently-updated non-terminal,
            non-operator Points (pairwise scoring is O(n²) — bound the work;
            pass a scope for larger graphs). Retrieval is nearest-match, not
            exact — see the scope note below.
        mode=prune: find ILLOGICAL/stale connections to fix or prune, using
            EP signals. Flags IMPL/NAND edges (operator-mediated OR direct
            per the reification rule) where:
              * stale         — edge incident to a retracted/superseded/
                                outdated/archived/deprecated Point (or the
                                legacy outdated=true flag). STALE_TERMINAL_STATUSES
                                is the canonical exclusion set, so this list
                                tracks it — the legacy `deprecated` member
                                included (#4058). suggested_action is
                                "re-point" when a CORRECTS successor exists,
                                else "prune".
              * contested     — edge incident to a claim with high posterior
                                variance (stored EP params only — an
                                unmeasured uniform prior is NOT contested)
                                or a claim with an incoming NAND operator
                                edge (the derived `challenged` condition,
                                ontology §5). suggested_action "review".
              * contradictory — the same pair is BOTH IMPL- and NAND-linked
                                (implication + mutual exclusion at once).
                                suggested_action "review".
        mode=both: run both, return {add: [...], prune: [...]}.

        Scope semantics (both modes) — ``scope`` is a FOCUS / work-bounding
        filter, NOT an exact-match or relevance gate. Hybrid retrieval returns
        the points NEAREST the scope, so near-but-not-exact is expected and
        intended: a scope naming something absent from the graph normally
        still yields its nearest retrievable neighbours — but in the degraded
        single-leg state every retrieved id can score 0 and be dropped, so "no
        exact match" does not by itself imply a non-empty pool (see below).

        When the pool is EMPTY (and with it the review): retrieval returned
        nothing (no hits, or it failed — fail quiet); or every retrieved id was
        dropped — its RRF score was <= 0 (the single-leg TF-IDF fallback carries
        no fusion signal), or it is an operator / terminal / outdated point / a
        ``[MITIGATION]`` bookkeeping row. mode=add also returns no suggestions
        when the pool holds fewer than 2 points, or when no pair clears
        ``similarity_threshold``.

        What IS guaranteed: a NON-EMPTY scope never silently falls back to the
        whole-graph pool (an empty or omitted scope means the whole graph by
        design). For a hard relevance bar, mode=add additionally filters pairs
        at ``similarity_threshold``; mode=prune applies no similarity gate by
        design — it reports the EP signals of the edges incident to the scoped
        pool (#2854).

        Returns {add: [{from, to, suggested_relation, reason, similarity}],
                 prune: [{from, to, relation, issue, suggested_action,
                          detail}]} — only the key(s) for the requested mode.

        Args:
            mode: "add", "prune" or "both" (default "both").
            scope: optional topic text or Point id — narrows the review to
                the retrieval-nearest candidates. A focus filter, not an
                exact-match or relevance gate (see Scope semantics above).
            similarity_threshold: minimum cosine similarity for mode=add
                (default 0.72 — the bge-small DEFAULT_THRESHOLD "semantically
                related" cross-vocabulary band, see tortoise/embeddings.py;
                near-duplicates are 0.89+).
            variance_threshold: posterior variance above which a claim is
                contested (default 0.04 — same signal as search_engine's
                has_ep-guarded contested flag; deliberately NOT
                TortoiseEP.get_contested_claims, which ALSO flags unmeasured
                uniform priors — an unmeasured claim is not contested. #3415:
                a declared #2199 baseline prior is prior-only and is no
                longer flagged there either — the two scans agree on every
                state except the unmeasured (Beta(1,1)) listing).
            add_limit: max suggestions (default 20).
            prune_limit: max flagged entries (default 50).
            similarity_fn: injectable pairwise-similarity function for
                mode=add (tests / tuning). Signature:
                similarity_fn(points: list[dict]) -> {(a_id, b_id): score}
                with a_id < b_id. Default: embedding cosine via
                tortoise.embeddings._encode (TF-IDF fallback when the model
                is unavailable).

        Raises ValueError on an invalid mode or out-of-range parameters.
        """
        if mode not in ("add", "prune", "both"):
            raise ValueError(f"mode must be 'add', 'prune' or 'both', got {mode!r}")
        if not (0.0 <= similarity_threshold <= 1.0):
            raise ValueError(
                f"similarity_threshold must be 0.0-1.0, got {similarity_threshold!r}")
        if variance_threshold < 0:
            raise ValueError(
                f"variance_threshold must be >= 0, got {variance_threshold!r}")
        if add_limit < 1 or prune_limit < 1:
            raise ValueError(
                f"add_limit and prune_limit must be >= 1, got {add_limit!r}/{prune_limit!r}")

        result: dict = {}
        if mode in ("add", "both"):
            result["add"] = self._review_add(
                scope, similarity_threshold, add_limit, similarity_fn)
        if mode in ("prune", "both"):
            result["prune"] = self._review_prune(
                scope, variance_threshold, prune_limit)
        return result
