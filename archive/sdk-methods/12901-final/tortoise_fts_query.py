# Archived sdk-method `tortoise_fts_query` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: tortoise_fts_query left the public SDK surface — privatised (body survives as `_tortoise_fts_query`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'tortoise_fts_query'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    @_metered_query
    def tortoise_fts_query(
        self,
        query: str | None = None,
        kind: str | None = None,
        *,
        entity_type: str = "point",
        structural_kind: str | None = None,
        structural_hops: int = 0,
        min_confidence: float = 0.0,
        order_by: str = "relevance",
        graph_ranker=None,
        limit: int = 10,
        threshold: float = 0.0,
        relationship_filter: str | None = None,
        traversal_path: str | None = None,
        exclude_status: list[str] | None = None,
        # #2351 (epic #2349 E-derived): filter — and therefore annotate — by the
        # claim's asserted|derived class. A read-side OPT-IN: ``None`` (the
        # default) costs no query and emits no key, so every existing caller is
        # byte-identical. When set, the filter is applied at the SAME
        # pre-truncation seam as ``exclude_status`` (#898) and every returned
        # Point carries ``claim_class`` — so a consumer can tell "this was told
        # to us" from "this was computed from other beliefs" without a new tool
        # (+0 MCP tools).
        claim_class: str | None = None,
        # #4509: the OPT-IN pre-truncation turn-echo exclusion. A caller that
        # names the capture session it is extracting for (the S3
        # link-before-create prior lookup) drops that session's own turn
        # echoes from the candidate set BEFORE ``limit`` — same seam as
        # ``exclude_status``. Default None = byte-identical to pre-#4509.
        exclude_turn_echo_session: str | None = None,
        include_terminal: bool = False,
        # #13302: the ENDED SCOPE — the read whose QUESTION is "what ended, and
        # how?". Default False = byte-identical (every existing caller keeps the
        # current-picture read it has). When True the population is the OTHER
        # half of the one determinant (design §6): the records
        # ``live._terminal_expression`` treats as ended, served with the ending
        # the record itself carries (its lifecycle word, the successor it names,
        # its terminal stage + outcome) and the time it ended. See
        # ``tortoise/ended.py`` — no new state, no visibility field, no second
        # vocabulary, and no per-call guess about which knob a surface accepts.
        ended_scope: bool = False,
        _elevated_timeout_ms: int | None = None,
        # #7953 (§17.3): PRIVATE — the residency COORDINATE a NODE-naming read
        # asked about, as ``label:key`` (``cache_projection.declared_key``).
        # The read path passes it to the marker so a partial declaration can
        # tell "outside the known-hot set" from "inside it and genuinely
        # absent"; a SET read names no node and passes nothing, keeping
        # today's set-level polarity. Default None = byte-identical.
        _residency_key: str | None = None,
        # #7983: PRIVATE — an INTERNAL pool read (recall_state's candidate pool,
        # a review pool, a session search) is not a caller reading these keys.
        # The funnel records the rows it RETURNS, and for a pool caller those
        # rows are candidates the end caller never sees, so recording them marks
        # up to 3-30k keys read and biases `f` HIGH — the direction that removes
        # the saving the instrument measures. Default True = product reads.
        _record_reads: bool = True,
        # #8487: PRIVATE — the read-path VALID-TIME selection (valid time as a
        # selection signal, not just decoration). Default True = the product
        # behaviour below. Passed False by a caller that RE-OWNS the order of
        # these rows downstream: its ordering is what the caller sees, so the
        # only thing this selection can still do is move which ids survive the
        # ``limit`` cut — an unmeasured composition change. The lanes that do
        # that:
        #   * ``tools/longmem_eval/retrieve.py`` (the evals' last order-owner);
        #   * ``recall_state`` / ``recall_gaps`` (StateRanker / GapsRanker
        #     re-sort the whole pool before the cut) and ``issue_insight`` /
        #     ``suggest_entry_points`` (``_issue_insight_rank`` / an RRF-derived
        #     confidence band, same shape) — each passes False at its own call
        #     site with the reason recorded there;
        #   * ``_review_pool`` / ``_resolve_subgraph_seed`` (#8487 review round
        #     6, P2) — a review pool is consumed as a MEMBERSHIP filter and a
        #     subgraph seed set as an expansion START set, so neither keeps the
        #     order; only the pre-cut composition of their ids could move, which
        #     is an unmeasured change to two product surfaces;
        #   * ``tools/fusion_formula_ab.py`` / ``tools/ask_pool_admission_probe``
        #     (a controlled measurement that must stay comparable to its own
        #     recorded numbers);
        #   * ``tortoise/extractor_v2.py::_fts_rows`` (#8487 review round 7, P2)
        #     — S3's capture-time prior leg: ``search_graph`` accumulates the
        #     rows into id-keyed dicts (order discarded) and link-before-create
        #     / supersession resolution consumes them as a SET, at ``limit=3``.
        #     A pre-cut composition change there is unmeasured and lands on the
        #     WRITE path, so it opts out at its own call site.
        # The ASK lane and its mirrors (``tools/ask_recall_bench.py``,
        # ``tools/gen_ask_transcripts.py``) keep it ON: they measure/replay the
        # shipped reader, so they must include the selection.
        #
        # OVERRIDES (#1745, recorded 2026-10-18 on #8487): this retrieval lever
        # ships DEFAULT ON, where #1745's fail-safe convention keeps a new
        # lever OFF until a measured delta adopts it (the convention
        # ``entity_key_expansion`` and ``time_aware`` above follow). #8487 is
        # OWNER-filed and requires the read path itself to consume valid time,
        # so a default-OFF lever would ship the issue unimplemented. The delta
        # is on the record rather than assumed: the shipped ask lane's context
        # order moved on merge —
        # ``tests/fixtures/ask_llm_transcripts/all-superseded-stale-evidence.json``
        # was regenerated (the current session block now precedes the
        # superseded one; ``user_message_hash`` c14618… → 495593…), and the
        # three-claim measurement on the shipped read path is in the #8487
        # plan-doc amendment (D3). The record lives here, in that plan doc, and
        # on #8487 itself, so the next lane reading the #1745 convention finds
        # why this lever is the exception.
        _valid_time_rerank: bool = True,
        # #12297: PRIVATE — the FUSED-CANDIDATE rerank. Default True = the
        # product behaviour (a relevance read reranks when the gate is armed).
        # Passed False by a caller whose PAGE COMPOSITION must not move. FOUR
        # classes, not one — they are different harms, and the brief's §5 states
        # the same four, in the same order (keep the two lists in step):
        #   * ORDER RE-OWNERS — ``suggest_entry_points``, ``issue_insight``,
        #     ``recall_state``, ``recall_gaps``, ``volunteer_context`` (bound as a
        #     ``_search_fn`` seam) and ``search_sessions`` (which re-sorts its pool
        #     by the PRE-rerank ``rrf`` score, so a rerank there is a permutation
        #     its own sort discards). A pre-cut reorder moves which ids survive
        #     ``limit`` and their own sort then hides it.
        #   * CONSUMED AS A SET, never as a page — ``_review_pool`` (a
        #     ``{pid: …}`` membership map; ``_review_add`` orders its own picks) and
        #     ``_resolve_subgraph_seed`` (an expansion START set). ⛔ Neither keeps
        #     an order, which is what ``_valid_time_rerank``'s docstring says about
        #     them: do not file them as order re-owners.
        #   * RECORDED-BASELINE HARNESSES — ``tools/longmem_eval/retrieve.py``,
        #     ``tools/fusion_formula_ab.py``, ``tools/ask_pool_admission_probe.py``,
        #     ``benchmarks/run_report.py`` and ``benchmarks/load_test.py``; also the
        #     ``docs/runbook`` diagnostics whose output IS the measurement — for
        #     ``w6c``/``w7a`` the gold row's POSITION, for ``5534`` an id-set delta,
        #     for ``4107`` the gold turn's fused rank and rendered block — and
        #     ``docs/evidence``'s cost probes. They pin it for comparability
        #     against RECORDED numbers — and ⛔ only the first three of those also
        #     pin ``_valid_time_rerank``, so that comparison holds for the
        #     ``tools/`` harnesses and not for the ``docs/`` and ``benchmarks/``
        #     halves: the harm here is a baseline that silently stops being the
        #     baseline, not a moved page.
        #   * MEMBERSHIP PROBES that read a page SMALLER than the pool width and ask
        #     "is this row in it" — ``battery/runner/setup.py``'s seed-manifest
        #     guard (a false ``ConfigError`` reading "stale PRE-FIX graph"),
        #     ``tools/why_layer_rate.py``'s support-entry lookup (whose ``None`` is
        #     the documented FAIL shape) and ``mcp_server``'s
        #     ``tortoise_suggest_entry_points`` (the returned page IS the resolution
        #     result set, at ``limit=5``). None of the three passes
        #     ``_valid_time_rerank=False``, and a re-picked page turns each into a
        #     false diagnosis.
        # Composition can only move when ``limit < _pool_n``, so a large-pool
        # caller (the evals' ``pool_size`` reads) is safe by construction — but
        # "safe today because of an arithmetic accident" is not an opt-out. ⛔ The two
        # levers are SEPARATE on purpose: the capture path
        # (``extractor_v2._fts_rows``) deliberately KEEPS the rerank — it has a
        # measured recall@3 delta and its own ``OVERRIDES:`` record — while
        # opting out of the valid-time selection. One flag for both would force
        # that departure to choose.
        _rerank_candidates: bool = True,
        pool_size: int | None = None,
        leg_trace: list[dict] | None = None,
        # B6 (#3892): the read-path status sink — one of the four recorded terms
        # (tortoise.read_status). Caller-owned and opt-in, exactly like
        # ``leg_trace``; default None = not computed, byte-identical behavior.
        read_status_out: dict | None = None,
        recency_field: str | None = None,
        recency_boost: float = 0.0,
        keep_numeric: bool = False,
        search_keys_prf: bool = False,
        # #2518 (C2 #2513): entity/fact-augmented key expansion — a caller-
        # gated ADDITIVE sparse-leg pass (default False = byte-identical).
        # The eval arms it (TORTOISE_LME_ENTITY_KEY_EXPANSION); the product
        # ask/search lanes adopt it only after the sealed A/B delta lands
        # (the #1745 fail-safe default decision).
        entity_key_expansion: bool = False,
        fusion_weights: dict | None = None,
        fusion_k: int = 60,
        w4_enrich: bool = True,
        # C6 (#2520, #2513): time-aware query expansion — the query-side
        # date anchor. ``time_aware`` gates the C6 branch; ``query_date``
        # is the question's date (YYYY-MM-DD, or a timestamp truncated to
        # it). TWO consumers, and only one of them is behind ``time_aware``:
        # with ``time_aware=False`` the C6 dense-anchor branch is never
        # entered, but ``query_date`` is STILL the reference instant of the
        # #8487 rank-time valid-time selection (§5e), so passing it can change
        # the returned order without ``time_aware`` (the ask lane does exactly
        # that). ``query_date=None`` leaves both consumers on their default
        # (the wall-clock date for §5e) and is the byte-identical path.
        query_date: str | None = None,
        time_aware: bool = False,
        # #8447: PRIVATE — the TRUNCATION marker's caller-owned sink, the same
        # pattern ``read_status_out`` uses (and ``_record_reads`` /
        # ``_residency_key`` for the other private read switches). The read
        # path records whether it DROPPED rows at ``limit`` so the caller can
        # tell a complete result from a subset; ``tortoise/truncation.py`` is
        # the marker's ONE home and mints its own field rather than a fifth
        # read-status term. Default None = not computed, so every existing
        # caller is byte-identical (and pays nothing).
        _truncation_out: dict | None = None,
    ) -> list[dict]:
        """Hybrid search with RRF fusion + EP annotation.

        entity_type: 'point' (default), 'event', 'subject', 'document', 'object', 'operator', or 'source'.
        Full-scan mode: omit query, set kind → all Points of that kind.
        Best-match mode: provide query → RRF fusion of FTS + vector + structural.
        include_terminal (#1391, #3301): default False — terminal-status
        nodes are excluded from the BASE retrieval. For Points that is
        ``live.TERMINAL_EXCLUDED_STATUSES`` (retracted, superseded, outdated,
        archived, deprecated); for Objects it is the canonical OBJECT
        vocabulary (superseded, deprecated, archived, retracted — no
        ``outdated`` flag, which no Object writer sets). Pass
        True to surface them (audit/history queries); a prior/resolution leg
        that must still SEE a terminal node (link-before-create, anchor
        resolution) opts in here too.

        ended_scope (#13302): the OTHER question — default False, and the whole
        current-picture read above is unchanged. `include_terminal` answers "the
        same read, without the currency filter", which still needs a query that
        MATCHES; this answers "what has ENDED", which is a question about STATE
        and cannot be asked of a text match at all (a customer's
        "has anything we relied on been withdrawn?" shares no word with
        "the beach club confirmed our booking"). When True the population is the
        records `live._terminal_expression` treats as ended — the SAME shared
        predictor every read filters on, taken in the positive direction, so no
        status set, slot literal or per-family deny-list lives here — served
        newest ending first, each row carrying the ending the record itself
        holds: its lifecycle `status` (``deprecated`` = nothing replaced it,
        ``superseded`` = see the successor), the successor as the TWO facts the
        model records — `successorId`/`allSuccessorIds` from a Point's `CORRECTS`
        edge and `successorName` from the `supersededBy` carrier (an
        Object/Subject names its successor; the carrier holds a NAME, so it is
        never served under an `…Id` key) — the `stage` block (`slot` +
        `outcome`) for a record ended on the POSITION axis, and `endedAt`.
        `linkedEvents` carries the Events wired to the record of ANY kind — a
        mention as well as an occurrence — so it answers "planned and never
        happened" only together with the kind test (`planned_occurrence`,
        #13128), never by being empty. It also omits an id-less Event (#13558).
        ⛔ A `query` beside it is REFUSED, not ignored: a state enumeration that
        silently dropped the caller's words would serve a whole population as if
        it were their answer. The refusal is a CLOSED LIST, and it names EVERY
        retrieval keyword the normal path uses to shrink, expand, re-rank,
        re-weight, time-scope or enrich the row set: `query`, `threshold`,
        `min_confidence`, `order_by`, `relationship_filter`, `traversal_path`,
        `claim_class`, `exclude_status`, `exclude_turn_echo_session`, `graph_ranker`,
        `structural_kind`, `structural_hops`, `pool_size`, `fusion_weights`,
        `fusion_k`, `recency_field`, `recency_boost`, `query_date`, `time_aware`,
        `keep_numeric`, `search_keys_prf`, `entity_key_expansion` and `w4_enrich`
        (`exclude_turn_echo_session` would have returned rows the caller asked to
        exclude, in silence — the failure the sentence above cites). A new
        retrieval keyword must be classified in that list, because the list IS the
        contract. `include_terminal` is the one keyword accepted beside it and it
        is a NO-OP here rather than a drop: the ended population is already the
        terminal one, so that flag can neither add nor remove a row. `entity_type`
        (point/object/subject — the kinds with a lifecycle), `kind` and `limit`
        scope and bound it.
        `ended_scope` is not a visibility field on the record and adds no word
        to the model: it is design §6's "option on the call", and the default
        keeps every existing surface answering exactly what it answered before.
        The scan it issues is BOUNDED by `ended.ENDED_SCAN_BUDGET_MS`
        (10 000 ms — FalkorDB's own general-query default, not the 500 ms leg
        budget: #13302 review F1), overridable by the caller's own budget. A
        budget it overruns degrades — zero rows, `incomplete`, a `degraded`
        leg — **when the caller passes a sink that can carry that marking**
        (`leg_trace`, `read_status_out` or `_truncation_out`); with none there
        is nowhere for the marking to go, so it raises the store's error
        instead of returning a bare `[]` that would claim "nothing of that
        family ever ended" (#13302 review round 2).

        pool_size: EXACT per-strategy retrieval depth override (benchmark/tests).
        Precedence: pool_size > TORTOISE_POOL_FLOOR env > the baked floor
        (the product's DEFAULT_POOL_SIZE = 120, #1947/G2). pool_size/floor
        only raise the candidate window (str_limit); the RETURNED limit is
        unchanged — truncation at result_ids[:limit] precedes EP decoration,
        so a deeper pool has zero cost for the RETURNED page's EP decoration.
        ONE exception (#8487 review round 7, P3): a Point read with temporal
        intent runs the valid-time SELECTION before that cut, so it reads
        epistemic state over the pre-cut pool (up to pool_size) — one wider
        ``OPTIONAL MATCH``-joined state read, capped by
        ``search_engine._DECORATION_TIMEOUT_MS`` (200 ms). That read fails
        OPEN: past the budget the selection is skipped and the leg records
        ``reason="state_read_incomplete"`` with ``degraded=False``, so a very
        deep pool on a temporal query can silently DECLINE the selection
        rather than fail the read. pool_size
        is an EXACT override: a value below limit*2 LOWERS the pool to
        pool_size (the env floor only raises — max(limit*2, floor)).
        #1348. Resolution is the product's
        (tortoise/retrieval.py::resolve_pool_size).

        Point results annotated with EP breakdown. confidence_mean is THE
        point's confidence (issue #2206 contract), and the contract is stated
        ONCE — as R1/R2/R3 in ``tortoise/search_engine.py``, above
        :func:`ep_measured_cypher`, where :class:`EpBreakdown` points: a
        readable ``n.confidence`` MIRROR leads, else α/(α+β) of the persisted
        pair, else ``null`` + ``ep_state: "absent"``. Do NOT re-derive it from
        shapes here: this line used to say "else the persisted prior mean",
        which is the wrong answer for a declared Beta(3,1) beside a stored 0.9
        (the mirror serves 0.9 — and ``update``/``update_entity`` clear a
        stale mirror precisely so the two cannot disagree), and every list of
        shapes this comment has carried was later falsified by a shape beside
        it (#11702: with nothing to report the neutral Beta(1,1) 0.5 was
        indistinguishable from a measured symmetric coin flip, so it is no
        longer substituted on a read/rank surface — ``get_confidence`` remains
        the EP prior read and still answers 0.5 there). contention/evidence
        stay the structural edge-ratio family (a different quantity). Non-Point
        entities skip EP annotation. min_confidence filters on
        confidence_mean (belief); defaults to 0.0 (no filter).

        relationship_filter: 'predicate:target_id' — only return points connected to
            target_id via an operator with label=predicate (e.g., 'addresses:customerSegment-1').
        traversal_path: 'FromKind→ToKind' — only return points that participate in a
            pack-declared relation chain (e.g., 'Product→Feature'). Resolved via pack registry.
        exclude_status: Point status values to EXCLUDE from results, applied to the
            fused candidate set BEFORE the final limit truncation (so filtering cannot
            silently shrink the result count — epic #898 recall_state). Default None =
            no filtering (existing behavior unchanged; retracted is already excluded at
            the retrieval layer, #689). Points with no status property are kept.
        claim_class (#2351, epic #2349 E-derived): 'asserted' or 'derived' — the
            claim's provenance CLASS, filtered at the SAME pre-truncation seam as
            ``exclude_status`` and, because the classes are read there anyway,
            ANNOTATING every returned Point with its ``claim_class`` key (a
            consumer must be able to tell "this was told to us" from "this was
            computed from other beliefs"). The read side of the class marker: a
            claim with no stored ``claimClass`` property IS 'asserted' (every
            claim authored before the marker existed, and every claim an
            authoring path writes), so ``claim_class='asserted'`` returns the
            whole authored graph without a migration. Default None = no filter
            and no key, i.e. byte-identical to the pre-#2351 read.
        exclude_turn_echo_session (#4509): the OPT-IN turn-echo exclusion — the
            capture session whose OWN transcript echoes (``{session_id}_t{i}`` turn
            Points; ``retrieval.is_turn_echo_row``) must NOT be treated as memory
            priors. Applied to the fused Point candidate set at the SAME
            pre-truncation point as ``exclude_status``, so ``limit`` applies to the
            already-filtered set — a session whose echoes consume every slot can
            otherwise hide a real prior ranked below them (the defect #4509
            fixes; a caller-side drop after the cut cannot be made sound). The
            exclusion reaches only candidates already in the fused pool — the
            UNION of the legs (uncapped), not one leg's window — so the residual
            bound is that union, roughly ``DEFAULT_POOL_SIZE`` per live leg: about
            120 with the vector leg unavailable, about twice that in hybrid. It is
            NOT ``MAX_SESSION_TURNS`` (500); raise ``pool_size`` (or
            ``TORTOISE_POOL_FLOOR``) to widen it.
            Applies wherever the resolved graph label is ``Point`` — so the point
            and operator legs, not only ``entity_type == "point"``;
            default None = no exclusion, byte-identical to pre-#4509 output. Do NOT
            pass the retrieval-pool session when you WANT the session's transcript
            (audit/history reads) — this is a memory-prior seam.
        _elevated_timeout_ms: PRIVATE — benchmark-only (#316). Threads an elevated
            collective-cap override into degradation_chain to measure uncensored
            true-completion latency. Default None = production 500ms cap. Never
            passed by production callers.
        _residency_key (#7953, §17.3): PRIVATE — the residency coordinate this
            read named, as ``label:key`` (build it with
            ``cache_projection.declared_key``, the SAME function the cache
            projection declares hot keys with). It is handed to the
            completeness marker (#7952) so a PARTIAL declaration can separate
            "outside the known-hot set" (a residency miss → fetch the record)
            from "inside it and genuinely absent" (a real in-store finding).
            A SET read names no node and leaves this None, which is the
            set-level polarity the marker already had — so default None is
            byte-identical behavior.
        leg_trace (R3 #1542 D4): PRIVATE trace contract — when provided,
            appends per-leg entries (vector/fts/structural/fallback — the
            R2 #1541 shared shape {"leg", "ran", "degraded", "reason",
            "count"}) so the dense leg's contribution is recorded per call,
            never null (E2E-1 leg-mix precondition). ``no_embedder`` vs
            ``encode_failed`` are distinguished in the query_vec block; the
            ``fallback`` entry is appended last on every early-return branch
            (snapshot TF-IDF path, legacy fallback_tfidf path, non-point
            return []). Default None = no trace, byte-identical behavior.
        read_status_out (B6 #3892): the read-path status contract — when
            provided, the dict receives ``{"status": <term>}`` with ONE of the
            four recorded terms (``tortoise.status_vocabulary``, consumed by
            ``tortoise.read_status``): ``available`` (reached, hits), ``empty``
            (reached, nothing matched), ``degraded`` (the memory layer is
            impaired — the store is configured but could not be reached, or it
            answered while a leg did not run, e.g. the embedder is absent so
            the dense leg was skipped, the #2573/#2898 class), or
            ``unconfigured`` (NO store/endpoint was declared — returned as a
            status, NEVER as an empty result and never as an exception).
            Caller-owned opt-in sink, the same pattern as ``leg_trace``;
            default None = not computed, no probe, byte-identical behavior.
        structural_kind (R4 #1543): keyword-only, default None — the kind
            passed to the STRUCTURAL strategy only (kind-scan), WITHOUT
            triggering the post-retrieval kind filter. Deliberately distinct
            from ``kind``: the eval's pool mixes kinds (turn points + raw
            transcripts + extracted points — the R1 union design), so a
            top-level filter would drop the raw-chunk leg. Legacy callers
            pass kind → struct_kind + post-filter exactly as today.
        structural_hops (R4 #1543): keyword-only, default 0 (off) — after
            the degradation chain returns, expands the FTS+vector hit ids
            over [:IMPL|NAND*1..N] edges (hop-1 = 1.0, hop-2 = 0.5) and
            folds the neighbors into the structural strategy's ranked list
            (deduped) — the graph-as-recall-amplifier pass. Default 0 skips
            the expansion entirely (byte-identical to pre-R4).
        recency_field (R5 #1544): keyword-only, default None (off) — the
            Cypher property name whose value re-ranks the fused pool by
            date (e.g. ``createdAt`` on Points, ``startedAt`` on Events;
            must match the STORED key — ``createdAt``, not the ``created_at``
            API alias). One batch date fetch per call; the rank-based
            factor comes from ``_recency_factors`` (newest → 1.0, undated →
            0.0) and multiplies each doc's RRF score by
            ``(1 + recency_boost × factor)``. Applied AFTER the RRF fusion +
            threshold filter, BEFORE the kind filter — re-ranks the fused
            candidate set so truncation picks up the new order. Any graph
            error → fail-open to plain RRF (logged, never crashes).
        recency_boost (R5 #1544): multiplier strength, clamped 0.0–10.0.
            Default 0.0 = off (byte-identical output to pre-R5).
        keep_numeric (A1 #2070): ask-lane numeric-token policy — all-digit
            tokens (money/quantity amounts) survive the sparse tokenizer so
            SAME-VALUE dollar turns become retrievable. Default False = the
            search lane stays byte-identical (numeric tokens still dropped).
        search_keys_prf (A4 #2070): pseudo-relevance-feedback expansion — a
            bounded SECOND FTS pass whose OR-union is the original query's
            tokens (slots reserved) PLUS additive aliases harvested from the
            retrieved pool's top-5 hits' ``search_keys`` (never replacing
            original tokens; bounded raise to 12 + 8 terms). Default False =
            single pass, byte-identical. The ask lane resolves the lever from
            ``TORTOISE_ASK_SEARCH_KEYS_PRF`` — itself default OFF until the
            delta is measured (#4593), so this parameter default and the ask
            lane agree.
        entity_key_expansion (C2 #2518, #2513): entity/fact-augmented key
            expansion — the multi-session evidence-surface lever. The query's
            own entities are resolved THROUGH THE INDEX (bounded FTS over the
            Object-name spine); their linked points' E3 ``search_keys`` +
            entity anchors (ALL sessions, via the ``aboutObject`` edges) become
            ADDITIVE OR terms for a second sparse pass, so subject-matching
            points from sessions the one-shot top-k starves can join the pool.
            Fail-open (any fetch/query failure keeps the original legs — the
            harvest is SHARED with the dense half below, so a failure cancels
            whichever half was about to run),
            additive-only, bounded (3 anchors; the A4 expansion-term cap 8),
            deterministic. Default False = byte-identical (single pass).
            OFF by default in the product (the #1745 fail-safe decision — the
            sealed #2513 A/B gate decides adoption); the eval arms it via
            ``TORTOISE_LME_ENTITY_KEY_EXPANSION`` / its ``--entity-key-
            expansion`` flag.
            ⛔ This argument alone arms the SPARSE half. The DENSE half
            (indicator 2 of the same issue — the same harvested alias pool also
            re-embeds the query and re-runs the dense leg, additively and
            fail-open) is a SEPARATE switch, ``TORTOISE_ENTITY_KEY_DENSE``
            (default OFF, see ``.env.example``), so the measured sparse-only
            arm stays byte-identical and the two halves are separable in an A/B.
            Both must be on for the dense half to fire.
        fusion_weights (A3 #2070): explicit per-strategy RRF weights
            override. Default None = the shared global resolution
            (TORTOISE_FUSION_WEIGHTS env → the shipped ``{"vector": 1.5}``)
            unchanged. Ask lane passes its own TORTOISE_ASK_FUSION_WEIGHTS
            knob through this slot.
        fusion_k (A3 #2070): the RRF damping constant override (the
            historical Cormack k=60). Default 60 = unchanged. Ask lane
            threads its TORTOISE_ASK_FUSION_K knob through this slot.
        query_date / time_aware (C6 #2520, #2513): the query-side date
            anchor. When ``time_aware`` is on, ``query_date`` is a valid
            date, and ``tortoise.time_aware.detect_temporal_intent`` reads
            the query as PREFER-LATEST ("now", "currently", "did I switch
            X"), the DENSE-leg embedding is computed over
            ``inject_query_date(query, query_date)`` = "<query> (as of
            <date>)" — so the embedding can express "which version was
            current on X", which the bare question cannot. The FTS and
            structural legs keep the ORIGINAL query (date tokens would
            dilute the sparse OR-union). A DATE-PINNED query ("where did I
            live in 2024?") is deliberately NOT anchored and gets no fresh
            bias (the invert-recency guard). Default ``time_aware=False`` →
            byte-identical, zero extra work.

            RANK-TIME VALID-TIME SELECTION (#8487) is NOT behind
            ``time_aware``: a PREFER-LATEST query is answered from the
            currently-valid versions whatever the anchor arm is set to.
            Before the #8487 wiring the ranker was blind to validity — a
            measured three-claim knowledge-update graph returned the ENDED
            claim (``validTo`` past, ``status='live'``) and a
            NOT-YET-EFFECTIVE claim inside the page as current facts, because
            nothing in the read path consumed the window. On the
            pre-truncation fused candidate set (the same seam as
            ``exclude_status``, so ``limit`` applies AFTER the selection) the
            page is stably reordered live-first via
            ``tortoise.time_aware.prefer_latest_ids``: nothing is filtered
            (a historical read keeps its evidence in the fused pool), order
            within each group is stable, undated entries are never demoted, and
            an all-stale pool is returned untouched. Pool membership is what is
            preserved — ``limit`` still applies after the selection, so a
            demoted row leaves the RETURNED PAGE when the page is already full
            of current rows. The
            reorder fires only when the fused page actually carries a
            non-current entry, so an undated corpus reads byte-identically.
            The decision is recorded on ``leg_trace`` as a ``valid_time``
            entry (reason + demoted count) — that entry plus each hit's own
            window (``valid_from``/``valid_to``/``end_state``/
            ``superseded_by``) is the audit trail for "which version was
            selected, and why". An UNUSABLE state read (short map, a raised
            read, or the selection raising) records
            ``reason="state_read_incomplete"`` / ``"state_read_failed"`` /
            ``"selection_failed"`` with ``degraded=False``: the entry's
            ``degraded`` flag is the memory layer's impairment signal, which
            ``ask_lane``/``read_status`` fold into the caller-facing
            degradation status, so an ORDERING impairment that step 7.6's
            page-width re-read fully recovers must not claim it. When that
            page-width read ITSELF comes back empty for a non-empty page, an
            ``epistemic_state`` entry with ``degraded=True`` is recorded — a
            read that did not run is a degradation, an order that could not be
            improved is not.
            ``TORTOISE_VALID_TIME_RERANK=0`` is the
            operator kill switch; ``_valid_time_rerank=False`` is the
            in-process opt-out.
        """
        # #2351: ``claimClass`` is a Point property, so a class filter on a
        # non-Point read is a CALLER ERROR and must be refused — silently
        # dropping it returns the very rows the caller asked to exclude (a
        # document has no marker, i.e. it reads as asserted), which arrives as
        # an answer. Checked HERE, before the degraded / fallback tiers below,
        # because those tiers return early and would otherwise bypass it.
        if claim_class is not None and entity_type not in _CLAIM_CLASS_POINT_TYPES:
            raise ValueError(
                f"claim_class is a Point property (#2351): it cannot filter "
                f"entity_type={entity_type!r}. Query entity_type='point' (or "
                f"'operator'), or omit claim_class — ignoring it here would "
                f"return the rows the caller asked to exclude."
            )
        # #13302: an ENDED-SCOPE read is the STATE population, not a match set, so
        # every retrieval knob beside it is REFUSED rather than dropped. A silent
        # drop is the defect class this file names repeatedly ("a keyword the
        # chosen arm cannot RECORD is refused, never dropped"): a caller who
        # passed a query and got the whole ended population back would read a
        # state enumeration as an answer to their question, and a caller who
        # passed ``min_confidence`` and got rows below it would never learn the
        # floor was not applied. ``limit`` (the page bound), ``entity_type`` and
        # ``kind`` (the population's scope) DO apply and stay served.
        #
        # ⛔ THE REFUSED SET IS CLOSED AND EVERY MEMBER IS NAMED BELOW. A knob that
        # is not named here is NOT reached by this arm either, so it may only be
        # admitted when dropping it cannot change which rows a caller receives. A
        # knob the normal path uses to SHRINK, EXPAND, RE-RANK, RE-WEIGHT, TIME-SCOPE
        # or ENRICH the row set is refused: silently dropping it serves a state
        # population as if the caller's scope had been applied, which is the
        # failure the comment above cites for ``query``. That is why the whole
        # retrieval vocabulary is here and not just the eight filters first
        # listed (#13416 review F4: ``structural_hops``/``structural_kind``/
        # ``graph_ranker``/``pool_size``/``fusion_*``/``recency_*``/``query_date``/
        # ``time_aware``/``keep_numeric``/``search_keys_prf``/
        # ``entity_key_expansion``/``w4_enrich`` were dropped in silence).
        # ⛔ ``include_terminal`` is deliberately NOT refused and is the ONE
        # exception, by construction rather than by omission: the ended population
        # is already the terminal one, so the flag can neither add nor remove a
        # row here — refusing it would refuse a caller for asking for what they
        # are getting. ``limit``, ``entity_type`` and ``kind`` scope and bound the
        # read and stay served; the sink/plumbing keywords (``leg_trace``,
        # ``read_status_out``, ``_truncation_out``, ``_elevated_timeout_ms``) are
        # how the caller receives or times the answer, not what it contains.
        if ended_scope:
            _refused_ended = sorted(
                name for name, _supplied in (
                    ("query", query is not None),
                    ("threshold", threshold not in (0.0, 0)),
                    ("min_confidence", min_confidence not in (0.0, 0)),
                    ("order_by", order_by != "relevance"),
                    ("relationship_filter", relationship_filter is not None),
                    ("traversal_path", traversal_path is not None),
                    ("claim_class", claim_class is not None),
                    ("exclude_status", bool(exclude_status)),
                    ("exclude_turn_echo_session",
                     exclude_turn_echo_session is not None),
                    ("graph_ranker", graph_ranker is not None),
                    ("structural_kind", structural_kind is not None),
                    ("structural_hops", structural_hops not in (0, None)),
                    ("pool_size", pool_size is not None),
                    ("fusion_weights", fusion_weights is not None),
                    ("fusion_k", fusion_k != 60),
                    ("recency_field", recency_field is not None),
                    ("recency_boost", recency_boost not in (0.0, 0)),
                    ("query_date", query_date is not None),
                    ("time_aware", bool(time_aware)),
                    ("keep_numeric", bool(keep_numeric)),
                    ("search_keys_prf", bool(search_keys_prf)),
                    ("entity_key_expansion", bool(entity_key_expansion)),
                    ("w4_enrich", w4_enrich is not True),
                ) if _supplied
            )
            if _refused_ended:
                raise ValueError(
                    f"ended_scope is a STATE enumeration, not a relevance read, "
                    f"so it cannot apply {', '.join(_refused_ended)}. It answers "
                    f"\"what ended, and how\" from the records' own state: omit "
                    f"{', '.join(_refused_ended)} and read the returned rows' "
                    f"`status` / `successorId`+`successorName` / `stage` (with its "
                    f"`slot` and `outcome`) / `endedAt`. Narrow the population with "
                    f"entity_type= and kind=, and bound the page with limit=; "
                    f"for a relevance read of what is still CURRENT, drop "
                    f"ended_scope and search as before."
                )
        from .search_engine import (  # noqa: I001
            classify_query, degradation_chain, rrf_fusion,
            annotate_ep_batch, get_relationships_bounded,
            fetch_point_epistemic_state, fallback_tfidf,
            SearchResult, SearchScores,
            search_provenance_enabled,
            subject_binding_available, SUBJECT_BINDING_UNAVAILABLE,
            RELATIONSHIPS_UNAVAILABLE,
            filter_by_relationship, filter_by_traversal_predicate,
            expand_structural_hops,
            _recency_factors,
            _trace_entry,
            VECTOR_LEG_BUDGET_MS,
        )
        # W4 (#2101): the additive why-layer enrichment (shared assembly —
        # DM-1; lazy import — why.py pulls search_engine helpers only).
        from .why import (
            enrich_items as w4_enrich_items,
            w4_enrichment_enabled,
        )
        # B6 (#3892): the recorded vocabulary's ONE home is
        # ``tortoise/status_vocabulary.py``; ``tortoise/read_status.py`` is the
        # read-path consumer of it and this method consumes that — it mints no
        # terms of its own.
        from .read_status import (
            STATUS_DEGRADED as _STATUS_DEGRADED,
            classify_leg_trace as _classify_leg_trace,
        )
        # #7952 (§17.2 / §17.3): the COMPLETENESS MARKER's ONE home. The read
        # path CONSUMES it and mints no verdict of its own, exactly as it does
        # for the status vocabulary above — and `not-hot` is never a term of
        # that vocabulary. Lazily imported so a default deployment pays
        # nothing and the field stays off (the #3892 flag pattern).
        from .residency import (
            RESIDENCY_FIELD as _RESIDENCY_FIELD,
            ResidencyDeclaration as _ResidencyDeclaration,
            apply_residency as _apply_residency,
            mark_residency as _mark_residency,
            read_declaration as _read_declaration,
            residency_enabled as _residency_enabled,
        )

        if entity_type not in ("point", "event", "subject", "document", "object", "operator", "source"):
            raise ValueError(f"entity_type must be 'point', 'event', 'subject', 'document', 'object', 'operator', or 'source', got {entity_type!r}")
        if limit < 1 or limit > 10000:
            raise ValueError(f"limit must be 1-10000, got {limit}")
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"threshold must be 0.0-1.0, got {threshold}")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be 0.0-1.0, got {min_confidence}")
        if not (0.0 <= recency_boost <= 10.0):
            raise ValueError(
                f"recency_boost must be 0.0-10.0, got {recency_boost!r}")
        if order_by not in ("relevance", "confidence", "graph"):
            raise ValueError(f"order_by must be 'relevance', 'confidence', or 'graph', got {order_by!r}")
        # #2351: the claim class is a caller INPUT, so it fails loud BEFORE any
        # retrieval work — a typo'd class must not silently read as "no filter"
        # (which would return claims the caller asked to exclude). The validated
        # value gates the filter + annotation below; ``None`` stays None.
        if claim_class is not None:
            claim_class = _claim_class.validate(claim_class)

        # #8447: the TRUNCATION marker starts COMPLETE and is only ever flipped
        # by a seam that actually drops rows. Seeding it here (rather than at
        # the cut) is what makes every early return below a POSITIVE claim of
        # completeness instead of an un-marked response the caller must guess
        # about — the distinction the issue's contract turns on. A missing
        # marker at the consumer is therefore a producer bug, not "complete"
        # (``truncation.truncation_field`` refuses to guess).
        #
        # ⛔ The seeding goes THROUGH the instrument, never around it: a sink is
        # CALLER-OWNED and may be REUSED, so a hand-written ``truncated = False``
        # would leave a stale ``_exact`` (and a stale ``total_available``) from an
        # earlier call riding this verdict — the defect one level down, and
        # exactly the hygiene ``truncation.mark_truncation`` performs and
        # documents. One writer, one contract (#8447 review round 3, P2).
        if _truncation_out is not None:
            from .truncation import mark_truncation as _seed_mark
            _seed_mark(_truncation_out, returned=0, candidates=0, exact=False)

        # B6 (#3892): the status sink and the trace used to classify it. When
        # no status is requested this is exactly today's ``leg_trace`` (None
        # unless the caller passed one) — byte-identical behavior. When a
        # status IS requested the classification gets its OWN per-call trace,
        # so one leg's entries can never answer for another (recall_state
        # classifies its Point and Object legs separately) while the caller's
        # ``leg_trace`` still receives every entry it used to.
        status_trace: list[dict] | None = leg_trace
        if read_status_out is not None:
            status_trace = []
            _caller_trace = leg_trace
        else:
            _caller_trace = None
        # #8447 (review round 5, P2): the truncation marker's proof needs the
        # SAME per-leg verdicts — a leg that never answered drops rows exactly
        # as a cap does, and until this the marker could not see it. With a
        # sink and no other trace, record one here: every consumer of
        # `status_trace` only APPENDS audit entries (never changes the rows),
        # and `degradation_chain` gates its per-leg lists on `trace_active`, so
        # the cost is bounded and the answer is unchanged.
        if status_trace is None and _truncation_out is not None:
            status_trace = []

        # #7952 (§17.2): the COMPLETENESS MARKER. Declared only AFTER the
        # projection is obtained and the bounded probe has answered, so the
        # known-hot set is read from a store known to be reachable — and never
        # read at all when the flag is off (zero steady-state cost, and the
        # response is byte-identical). ``None`` means NOT ASSESSED: the marker
        # is a directive, so an unassessable read leaves the field absent and
        # lets ``status`` carry the impairment, rather than guessing `not-hot`
        # for a store that is merely down (§17.3's S5 failure mode).
        _residency: _ResidencyDeclaration | None = None

        # #7983 (§17.4.4): the READ LEDGER — the instrument the hot fraction `f`
        # has never had. ONE product read = ONE funnel (`tortoise_fts_query`),
        # so this is the one seam that can say WHICH keys a read returned.
        # OFF by default, and "off" means NOT IMPORTED: the flag is read FIRST,
        # so a default deployment pays one env lookup, never loads the module,
        # extracts no key and returns a byte-identical response (#4097's truthy
        # contract, the one every other boolean env read in the tree uses).
        _read_ledger = None
        _read_ledger_enabled = is_truthy(os.environ.get("TORTOISE_READ_LEDGER"))
        if _read_ledger_enabled:
            try:
                from . import read_ledger as _read_ledger
            except Exception:  # pragma: no cover — a broken install, not a read
                _read_ledger = None
                _read_ledger_enabled = False

        # #8447: the TRUNCATION marker's state, written by the seam that DROPS
        # rows and read by ``_with_read_status`` below — so the marker is
        # recorded at the ONE place every return passes through, with the rows
        # that actually SHIP. Before this, a cap applied downstream of the cut
        # (``min_confidence``, a hit whose entity fetch came back empty) made
        # the delivered count disagree with what the marker claimed: a 3-match
        # pool whose 3 rows ``min_confidence`` filtered out answered 0 rows and
        # ``truncated: False`` — a positive claim of completeness on an empty
        # answer. Seeding ``candidates=0`` means every early return (nothing was
        # cut) is positively marked COMPLETE rather than left un-marked.
        _trunc: dict = {"candidates": 0, "exact": False, "incomplete": False}

        def _with_read_status(rows: list[dict]) -> list[dict]:
            """Attach the recorded read status when the caller asked for it.

            With a sink, EVERY call happens after the projection was obtained
            AND the bounded reachability probe answered — a probe failure or a
            `_get_proj()` failure returns before any of these paths. So the
            store is known to be DECLARED and to have ANSWERED, and
            ``configured=True`` / ``reached=True`` are facts, not defaults.
            """
            if _truncation_out is not None:
                from .truncation import mark_truncation as _mark_truncation
                # ``returned`` is the rows about to SHIP, not the rows that
                # survived the cut — the difference is the downstream filters.
                _mark_truncation(
                    _truncation_out, returned=len(rows),
                    candidates=_trunc["candidates"], exact=_trunc["exact"],
                    incomplete=_trunc.get("incomplete", False))
            if read_status_out is not None:
                status = _classify_leg_trace(
                    status_trace or (), hit_count=len(rows),
                    configured=True,
                    reached=True)
                if _residency is not None:
                    # §17.3: a `not-hot` read may not be reported as `empty` —
                    # a partial store cannot establish "nothing matched". The
                    # truth store was NOT consulted on this path (the residency
                    # router is not wired here yet — #7970), so a zero-hit read
                    # of a declared-partial store is `degraded`, never `empty`.
                    marker = _mark_residency(
                        _residency, hits=len(rows), key=_residency_key)
                    status = _apply_residency(
                        status, marker, record_consulted=False)
                    read_status_out[_RESIDENCY_FIELD] = marker
                read_status_out["status"] = status
                if _caller_trace is not None and _caller_trace is not status_trace:
                    _caller_trace.extend(status_trace or ())
            if (
                _read_ledger is not None
                and _read_ledger_enabled
                and _record_reads
            ):
                # The ledger never changes the answer: it records the
                # `(label, key)` of every row this read returned, and a ledger
                # failure is swallowed inside the module (a measurement may not
                # break the thing it measures).
                if rows:
                    _read_ledger.record_rows(label, rows)
                # #13084: the CALL is recorded whether or not it returned rows —
                # the query mix counts questions asked, and an empty search is
                # still a search (and the one that paid for a cold miss).
                # The kind follows `classify_query`'s own rule below: the dense
                # leg fires exactly when a text query is present, and a call with
                # no query is the structural/full-scan read.
                _read_ledger.record_query(
                    "vector" if (query and str(query).strip()) else "lookup",
                    label=label,
                    returned=len(rows or ()),
                )
            return rows

        def _with_claim_class(rows: list[dict],
                              classes: dict[str, frozenset[str]]) -> list[dict]:
            """Annotate each returned Point with its asserted|derived class.

            #2351 (epic #2349 E-derived), read side indicator 3. Only reachable
            when the caller ASKED for the class (``claim_class is not None``):
            an unfiltered read emits no key at all, so every pre-#2351 caller
            stays byte-identical — the annotation is OPT-IN, never a widened
            result shape.

            The classes are the ones the FILTER already read (``classes``), so
            annotating costs no second round trip. An id carrying no node with
            a marker reads as ``asserted``: absence IS the default class, which
            is what lets an authored-only graph be queried without migrating it.
            """
            wanted = claim_class
            for row in rows:
                pid = row.get("id")
                if not isinstance(pid, str):
                    continue
                carried = classes.get(pid) or frozenset({_claim_class.DEFAULT})
                # A Point id may fan out to several nodes with different marks
                # (#7822) — report the class the caller asked for when the id
                # carries it, else the strongest fact we have.
                row["claim_class"] = (
                    wanted if wanted in carried
                    else _claim_class.DERIVED
                    if _claim_class.DERIVED in carried
                    else _claim_class.ASSERTED)
            return rows

        def _filter_and_annotate_claim_class(
                rows: list[dict]) -> list[dict] | None:
            """Filter + annotate DEGRADED hit rows by claim class (#2351).

            The two fallback tiers (``search_snapshot`` / ``fallback_tfidf``)
            return BEFORE the pre-truncation filter block, and they already
            replicate the ``exclude_status`` / ``exclude_turn_echo_session``
            parity by hand for exactly this reason. Without this, a
            class-filtered read on a degraded store silently returned claims
            the caller asked to EXCLUDE — the one failure mode a filter must
            never have. Classes are read from the graph by id, so an
            unmarked hit stays ``asserted`` (absence IS the class).

            ⛔ ``None`` means THE CLASS READ FAILED. The caller must then
            neither filter NOR annotate: this tier exists to serve a SICK
            store, so it may not raise — but a failed read must also not
            become an answer, and returning the rows unchanged would be
            indistinguishable from "every id is allowed" and would get every
            row stamped with a class that was never read (the primary path
            refuses to do exactly that). Saying nothing is the only honest
            degradation.
            """
            ids = [r.get("id") for r in rows if isinstance(r.get("id"), str)]
            try:
                classes = _claim_class.classes_for_ids(graph, ids)
            except Exception:
                _logger.warning(
                    "claim-class filter failed on the degraded fallback — "
                    "pass-through, without the class annotation", exc_info=True)
                return None
            kept: list[dict] = []
            for row in rows:
                carried = classes.get(row.get("id")) or frozenset(
                    {_claim_class.DEFAULT})
                if claim_class not in carried:
                    continue
                row["claim_class"] = claim_class
                kept.append(row)
            return kept

        try:
            proj = self._get_proj()
        except Exception:
            if read_status_out is not None:
                # A store that will not OPEN is `degraded` (off by OUTAGE):
                # `_auto_health_recover()` raises when a declared server is
                # unreachable or a declared embedded file cannot be read. This
                # SDK always resolves a store TARGET — a server URI, or the
                # canonical embedded path via `resolve_db_path()` and
                # `FalkorProjection`'s no-arg fallback — so there is no
                # "never declared" state here to report as `unconfigured`
                # (off by policy, a set-up gap the engine does not have).
                # Never an empty result, and never a raise.
                read_status_out["status"] = _STATUS_DEGRADED
                # #8447 (review round 2, P2): a store that never answered cannot
                # be reported COMPLETE. The marker's own three states forbid it —
                # 0 rows over an UNREAD store is the third state, not the first.
                # Only ``status`` disagrees today, and only when its flag is on.
                if _truncation_out is not None:
                    from .truncation import mark_truncation as _lost_mark
                    _lost_mark(_truncation_out, returned=0, candidates=0,
                               incomplete=True)
                return []
            raise
        graph = proj.g
        if read_status_out is not None:
            # The store is configured; prove it ANSWERS before claiming
            # anything about its contents. BOUNDED like every other read query
            # — an unbounded probe would let a wedged store stall the read it
            # annotates.
            try:
                graph.query("RETURN 1", timeout=int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS))
            except Exception as e:  # noqa: BLE001, RUF100
                # The store IS configured (the projection object was obtained
                # just above) but did not answer: off by OUTAGE — `degraded`,
                # never `unconfigured` (which is reserved for a store that was
                # never declared) and never an empty result.
                _logger.warning(
                    "read path cannot reach the configured store (%s) — "
                    "degraded", e)
                read_status_out["status"] = _STATUS_DEGRADED
                # #8447 (review round 2, P2): configured but silent is still an
                # unread store — never a complete empty answer.
                if _truncation_out is not None:
                    from .truncation import mark_truncation as _lost_mark2
                    _lost_mark2(_truncation_out, returned=0, candidates=0,
                                incomplete=True)
                return []
        # #7952: the store is reachable, so the known-hot set can be READ.
        if read_status_out is not None and _residency_enabled():
            _residency = _read_declaration(
                graph, timeout_ms=int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS))
        # D10 (ONTOLOGY v3.15 §4.4): a document IS a :Source — there is no
        # :Document graph label, so post-retrieval Cypher must never address
        # one. Mirrors tortoise/search_engine.py's legs (the caller-facing
        # entity_type stays "document").
        # The alias mapping lives in `read_ledger` (next to the key contract) so
        # the ledger's coordinate and this funnel cannot disagree: an OPERATOR is
        # a :Point and a document IS a :Source, so recording either under its
        # caller-facing name would be a key the stock can never join. The
        # fallback is the pre-existing expression, kept byte-identical for the
        # default (flag-off) deployment. The two are not pinned to each other by a
        # test (the fallback is unused once the ledger is on); what IS pinned is
        # the mapping itself, alias by alias.
        label = (
            _read_ledger.label_for_entity_type(entity_type)
            if _read_ledger is not None
            else ("Source" if entity_type == "document" else entity_type.capitalize())
        )
        # Operator: Point nodes with is_operator=true, kind=op_type
        # Source: Source nodes, kind=sourceKind
        kind_field = {"point": "pointKind", "event": "eventKind", "subject": "subjectKind", "document": "documentKind", "object": "objectKind", "operator": "op_type", "source": "sourceKind"}[entity_type]

        # 1. Classify query → determine active strategies
        strategies = classify_query(query, kind)
        is_full_scan = (query is None and kind is not None)

        # Expand kind early for pack-aware structural query + kind filter.
        # #11679: the KIND FILTER below feeds `n.{kind_field} IN [...]`, so it
        # takes the read-filter set (descendants, never ancestors).
        expanded_kinds = self._read_filter_kinds(kind) if kind else None

        # ── #13302: the ENDED SCOPE is served HERE ────────────────────────────
        # Placed after the projection is obtained and its reachability proven
        # (so `_with_read_status` records a status the store actually answered
        # for), and after `label`/`kind_field`/`expanded_kinds` are resolved (so
        # the enumeration reads each family by its OWN carriers and honours the
        # same kind EXPANSION every other read filter uses). It returns before
        # the retrieval machinery: this is a state enumeration, and running the
        # legs first would either waste them or — worse — let a degraded FTS
        # fallback fill an answer to a question that was never about words.
        if ended_scope:
            from .ended import ENDED_SCAN_BUDGET_MS, enumerate_ended

            # ONE row beyond the page, so the TRUNCATION marker is PROVEN rather
            # than guessed: a population wider than ``limit`` is the only way a
            # caller learns rows were cut, and asking for the extra row costs one
            # slot in a bounded scan. Without it the marker would have to report a
            # full page as complete (the #8447 defect) or an unfilled one as cut.
            _ended_bound = int(_elevated_timeout_ms or ENDED_SCAN_BUDGET_MS)
            try:
                _fatter = enumerate_ended(
                    graph, entity_type=entity_type, kind=kind,
                    kinds=expanded_kinds, limit=limit + 1,
                    # BOUNDED like every other statement this read issues: the
                    # caller's own budget (the capstone's `_elevated_timeout_ms`)
                    # or `ENDED_SCAN_BUDGET_MS`, never an unbounded query. ⛔ That
                    # budget is the SCAN's own, not `VECTOR_LEG_BUDGET_MS`
                    # (#13302 review F1): 500 ms is calibrated for the single-hop
                    # legs (the `RETURN 1` probe, `_read_declaration`, the vector
                    # legs), and a family-wide `MATCH … collect() … ORDER BY …
                    # LIMIT` is not one of them — under it this arm degraded to an
                    # empty answer on the population the read exists for.
                    timeout_ms=_ended_bound)
            except ValueError:
                # ⛔ NOT a store failure: the door REFUSES a family with no
                # lifecycle before it ever queries (`ended.py`'s `FAMILIES`
                # check), and that refusal is the answer — swallowing it here
                # would serve `ended_scope` on an Event as a degraded empty
                # population, i.e. "nothing of that family ended", which is the
                # claim the two must never share. Only the STORE's failure is
                # degradable; a contract refusal keeps rising.
                raise
            except Exception as e:  # noqa: BLE001, RUF100
                # ⛔ The degradation must be CARRIED, or it is a lie (#13302
                # review round 2, P2). The marking below reaches the caller only
                # through a sink: the leg entry through `status_trace`, the
                # `incomplete` flag through `_truncation_out` (which
                # `_with_read_status` publishes ONLY when that sink exists).
                # With NEITHER, `[]` would arrive bare, byte-identical to the
                # factual claim "nothing of that family ever ended" — the one
                # reading `ended.py`'s docstring and this arm's own comment
                # forbid. Measured before the guard: a no-sink caller got `[]`
                # and a log line, nothing else.
                #
                # The SIBLING store-failure arm above answers it the same way —
                # degrade when a sink exists, otherwise `raise` — so a caller
                # with no vocabulary for "could not answer" gets the store's
                # error, exactly as a direct `enumerate_ended` caller does.
                # `status_trace` IS the "can this be carried?" test, and it is
                # the only one needed: it is the caller's `leg_trace` when one
                # was asked for, and is normalised to `[]` whenever a truncation
                # sink exists without one (the #8447 round-5 seam just above).
                # So `status_trace is None` is exactly `no sink at all` — do NOT
                # key this on `read_status_out` alone the way the sibling arm can
                # afford to: a trace-only or truncation-only caller HAS a
                # vocabulary for "could not answer" and must keep degrading
                # (`test_a_truncation_only_caller_still_degrades`,
                # `test_a_trace_only_caller_sees_the_ended_leg_did_not_answer`).
                if status_trace is None:
                    raise
                # FAIL-SAFE (#13302 review F1). FalkorDB's `timeout` is a MAXIMUM
                # QUERY RUNTIME: the server aborts the statement and the client
                # RAISES. This is the most expensive statement the read issues,
                # so on a production-sized family it is the arm most likely to
                # hit the budget — and without this handler a store that could
                # not answer would throw OUT of a public read whose every
                # sibling bounded statement degrades (the reachability probe
                # above, the vector legs, `_read_declaration`). Degrade exactly
                # as they do: zero rows, a leg entry that says the leg ran and
                # did NOT answer, and a truncation marker that forbids reading
                # the empty answer as COMPLETE. Never an unmarked empty list
                # passed off as "nothing ever ended" — and, that being
                # impossible without a sink, never a raise either once one is
                # present (the guard above is what makes both true at once).
                _logger.warning(
                    "ended-scope enumeration did not answer within %s ms (%s) "
                    "— degraded", _ended_bound, e)
                # ⛔ APPEND IN PLACE, never rebind: `status_trace` IS the
                # caller's `leg_trace` whenever no `read_status_out` was asked
                # for (`45129`), and a rebind to a copy would send this entry to
                # a list nobody reads — the sibling vector arm above
                # (`45617`) appends in place for exactly that reason. A
                # trace-only caller must see that the leg ran and did not
                # answer, or the one record of the degradation is a throwaway.
                if status_trace is not None:
                    status_trace.append(_trace_entry(
                        "ended", ran=True, degraded=True,
                        reason="query_failed", count=0))
                _trunc["incomplete"] = True
                return _with_read_status([])
            _ended_rows = _fatter[:limit]
            # ``candidates`` is what this read could SEE and it may be BOUNDED
            # (exactly the hosted ``GET /v1/points`` ``limit+1`` probe shape), which
            # is why ``exact`` is the separate input: 3 here means "more than
            # limit", never "exactly 3".
            _trunc["candidates"] = len(_fatter)
            _trunc["exact"] = len(_fatter) <= limit
            return _with_read_status(_ended_rows)

        # C6 (#2520): resolve the freshness intent ONCE and derive the
        # DENSE-leg query. The anchor touches the embedding only — the
        # sparse/structural legs keep the original query. ``query=None``
        # (full-scan) is safe: the detector is None-safe. Off path: no
        # import, no scan, byte-identical.
        _dense_query = query
        if time_aware:
            from .time_aware import dense_query_for
            _dense_query = dense_query_for(query, time_aware=True,
                                           query_date=query_date)

        # 2. Get query vector if needed (all core entity types now have embeddings #7845)
        # R3 (#1542) D4: no_embedder vs encode_failed are distinguished —
        # both leave query_vec None (the vector strategy is never submitted),
        # but the trace records WHY: a run can never confuse "no embedder
        # installed" with "embedder present but broken" (present-but-broken
        # used to be conflated into query_vec=None silently).
        # #5275: ``EmbedderUnavailableError`` is the CONTRACT of
        # ``TORTOISE_EMBEDDING_MODEL_REQUIRED=1`` — a process that armed it
        # must not be handed a keyword-only read, and the exception carries
        # the ``failure_kind`` attribution (name/context/kind) that no trace
        # term could. It is caught BY NAME and re-raised AHEAD of the generic
        # handler below: the blanket ``except Exception`` converted the named
        # refusal into ``model = None`` → the designed-absence reason
        # ``no_embedder``, so the read returned FTS-only results with no
        # error and no signal — exactly the outcome #4861 exists to prevent
        # (#2985). Every OTHER exception keeps the historical degrade: an
        # encoder bug or a shape error is not the flag's contract.
        query_vec = None
        _vec_reason: str | None = None
        if strategies.get("vector") and query and query.strip():
            from .embeddings import READ_EMBED_BUDGET_S, EmbeddingModel
            from .exceptions import EmbedderUnavailableError
            # #10893: stage marks for the wait-bound breach. `mark` is a NO-OP
            # when no timeline is bound (every CLI/test/SDK-user process), so
            # this costs one ContextVar read per boundary — see
            # `tortoise/read_stages.py` for why the instrument must not be able
            # to look like the latency it measures.
            # ⛔ `embed_get` and `embed_encode` are SEPARATE stages on purpose: a
            # COLD LOAD (~57 s measured) is setup the #4055 exemption already
            # excuses, while an ENCODE is work this request owns. Collapsing
            # them is how the two got argued as one thing on #10893.
            _mark_read_stage(STAGE_EMBED_GET)
            model = None
            # ⛔ #13528 — THE ACQUIRE IS BOUNDED, AND IT IS BOUNDED HERE. This
            # used to be a bare ``EmbeddingModel.get()``, which blocks for the
            # WHOLE one-time load (13.5 s on a warm model cache, ~57 s cold) and
            # — when a load is already in flight — blocks on the class LOCK the
            # loading thread holds across ``__init__``, so the caller's own
            # ``load_timeout`` could not bound it. Production measured the result
            # as ``stage=embed_get`` with HTTP=504 at t=11.52 s against this
            # lane's 8 s bound.
            #
            # The cost was never one request's latency: the read lane is 8 daemon
            # workers (``monitoring.CONTROL_PLANE_READ_WORKERS``), so every
            # worker parked in that lock makes the reads behind it QUEUE — the
            # ``pre_handler: 9295.0`` in the same measurement, i.e. reads that
            # had not started yet paying the wait before their first mark.
            #
            # ⛔ THE DECLINE IS THE SAME SHAPE THE READ ALREADY HAS: with no
            # model the vector strategy is never submitted and the leg trace
            # records WHY (``ran=False``, ``reason=<the cause>``), which
            # ``search_engine.declared_degraded_read`` turns into the read's
            # declared single-leg marker. Nothing new is traded — a read inside
            # the cold window is keyword-only, exactly as it is on a host with no
            # embeddings extra, and the dense leg returns on the next read after
            # the load lands.
            _read_decline = EmbeddingModel.await_read_ready_reason(
                READ_EMBED_BUDGET_S)
            if _read_decline is not None:
                # Not warm and the read declined to wait. It still goes through
                # the ONE absent exit, so ``TORTOISE_EMBEDDING_MODEL_REQUIRED``
                # (#4861/#5275) keeps failing LOUD rather than degrading
                # silently, and the refusal names the cause this read actually
                # resolved (a load still IN FLIGHT has recorded no failure of its
                # own, so ``_last_failure_kind`` alone would answer the
                # designed-absence term).
                # ⛔ ``kind`` is a LEG-TRACE reason, and ``_unavailable``
                # translates it into the refusal's ``failure_kind`` vocabulary
                # (#13528 review P1-1). The rule, not a fixed pair: a reason
                # already in that vocabulary (``load_failed`` / ``load_timeout``)
                # IS the cause and passes unchanged; ``no_embedder`` /
                # ``load_in_flight`` are trace-only words and are translated to
                # the RECORDED kind, else ``model_unavailable``. So a recorded
                # kind outranks the in-flight term — reconcile a refusal against
                # ``_last_failure_kind``, never against a trace-word mapping.
                EmbeddingModel._unavailable(
                    context="read: embedder not warm within the read's budget",
                    kind=_read_decline)
                _vec_reason = _read_decline
            else:
                try:
                    model = EmbeddingModel.get()
                except EmbedderUnavailableError:
                    raise  # #5275: REQUIRED — fail loud, never an FTS-only read
                except Exception:  # noqa: BLE001, RUF100
                    model = None
            if model is None and _read_decline is None:
                # #5275: a present-but-BROKEN embedder (load_failed /
                # load_timeout) is not the designed absence the
                # ``no_embedder`` term names — carry the recorded cause.
                _vec_reason = EmbeddingModel.absent_reason()
            elif model is not None:
                _mark_read_stage(STAGE_EMBED_ENCODE)
                try:
                    query_vec = model.encode([_dense_query])[0].tolist()
                except Exception:  # noqa: BLE001, RUF100
                    _vec_reason = "encode_failed"
        if _vec_reason is not None and status_trace is not None:
            # Recorded at the source (the strategy is never submitted) —
            # BEFORE degradation_chain merges its own entries, so the
            # vector entry always precedes the fts/structural merge.
            status_trace.append(_trace_entry(
                "vector", ran=False, degraded=True,
                reason=_vec_reason, count=0))

        # 3. Run retrieval with degradation
        is_embedded = getattr(proj, '_is_embedded', True)
        # Full-scan mode: no truncation — return ALL Points in context (#7811 completeness)
        # #1947 (audit G2): the product OWNS the pool-depth number. The floor
        # now BAKES the product's ``DEFAULT_POOL_SIZE`` (120) instead of the
        # historical limit*2 (20 at limit=10): the LongMemEval depth finding
        # (66% of marked evidence never entered a 60-item pool) applies
        # equally to the product's default 20-item window, and the deepened
        # candidate window is the single highest-leverage retrieval change.
        # Resolution is delegated to ``tortoise.retrieval.resolve_pool_size``:
        # pool_size exact override > TORTOISE_POOL_FLOOR env > the baked
        # 120 floor (limit*2 stays the per-call lower bound). pool_size is
        # validated up front (any mode, incl. full-scan) for consistency
        # with the limit validation bound (code-review P2 fix).
        if pool_size is not None and (pool_size < 1 or pool_size > 10000):
            raise ValueError(f"pool_size must be 1-10000, got {pool_size}")
        if is_full_scan:
            str_limit = 100000
        else:
            str_limit = resolve_pool_size(
                limit * 2,
                pool_size=pool_size,
                env_name="TORTOISE_POOL_FLOOR",
                default=DEFAULT_POOL_SIZE,
                exact=True,
            )
        # R4 (#1543): the structural strategy's kind — an explicit override
        # that does NOT trigger the post-retrieval kind filter below (the
        # eval's pool mixes kinds: turn points + raw transcripts + extracted
        # points — a top-level filter would drop the raw-chunk leg). Legacy
        # callers keep kind → structural kind + post-filter exactly as today.
        struct_kind = structural_kind if structural_kind is not None else kind
        # #4028: the read surface's OPT-IN vector-leg relevance floor. Off by
        # default (None) — see _resolve_vector_min_similarity. The shipped
        # default's real failure was 17 test-residue Points holding the only
        # stored embeddings: the hybrid surface answered every query with
        # them. That is a DATA defect (tools/purge_test_residue.py); this
        # lever additionally lets an operator who has measured their corpus
        # drop sub-relevance near neighbours.
        _vector_floor = _resolve_vector_min_similarity()
        _floored_legs: set[str] = set()
        # ── #8488: the in-process Okapi BM25 lexical arm ────────────────────
        # ON by default, OPT-OUT via ``TORTOISE_BM25_LEXICAL=0`` (owner ruling
        # 2026-10-28, ``tortoise/bm25.py::BM25_DEFAULT_ENABLED``) — the arm was
        # fail-safe OFF only until it had its own mechanism-level evidence and
        # the #10121 tie fix it depends on.
        #
        # What it swaps. The graph-side lexical leg is scored by the graph's
        # fulltext scorer, which on the embedded lane gives a rare term exactly
        # the weight a ubiquitous one gets and ignores document length
        # (measured: tortoise/bm25.py's module docstring). BM25 supplies idf,
        # term-frequency saturation and length normalisation. The arm runs in
        # the LEXICAL SLOT: when it produces rows they REPLACE the shipped
        # leg's list for this read, so the lexical signal is fused once, never
        # twice (two lexical legs would outvote the dense leg by construction —
        # the #7704 shape).
        #
        # Bounds. (a) embedded lane only (the lane the measurement covers, and
        # the only one where a corpus scan is already the design);
        # (b) entity families the leg can actually serve (BM25_ENTITY_TYPES);
        # (c) an arm that finds nothing leaves the shipped leg's rows EXACTLY as
        # they were — never an empty lexical slot (fail-safe, tested);
        # (d) it stands down when the #4028 relevance floor emptied the vector
        # leg, because that empty answer is the read's declared result;
        # (e) its fusion weight goes through ``clamp_lexical_fusion_weight``.
        #
        # The shipped leg is NOT suppressed: it runs in the chain as it always
        # does, in parallel and inside the deadline, so the fallback costs no
        # extra latency and the lexical slot is metered exactly once.
        #
        # Trace shape when the arm runs: the chain's entries keep
        # ``degradation_chain``'s fixed order (fts, vector, structural), and the
        # arm appends ONE further entry for the same ``fts`` leg AFTER them —
        # because that is when it ran. The two entries are not a duplicate
        # claim: the chain's entry has no ``mechanism`` key (the shipped
        # scorer's rows), the arm's carries ``mechanism="bm25"`` — or, when it
        # declines because the corpus is wider than the cap, ``declined=True``
        # with no ``mechanism`` at all (it produced no rows to attribute).
        #
        # (f) the arm does NOT run when a later lexical pass will REPLACE the
        # slot it writes. ``_search_keys_prf_expansion`` (A4) and
        # ``_entity_key_expansion_pass`` (C2) each RE-RUN the shipped scorer
        # and overwrite ``raw_results["fts"]``, so an arm that ran before them
        # would leave its rows discarded while its trace entry still claimed
        # ``mechanism="bm25"`` — a measurement (starting with #8486) would
        # baseline a read whose lexical order is the shipped scorer's while
        # reading a trace that says BM25 produced it. Standing down is the
        # honest composition: same shape as the ``#4028`` floor stand-down
        # below (no arm, no arm trace entry, the shipped rows stand), and it
        # costs nothing because composing the two levers was never measured.
        # Reopening it means feeding the expansion's alias tokens INTO the arm
        # so the arm is the last writer — not running it earlier.
        _bm25_lexical = False
        # Where the ARM's own trace entries begin (``None`` = the arm did not
        # run). The completeness proof attributes a leg-trace entry by
        # PROVENANCE, never by the slot name both lexical scorers share.
        _bm25_trace_start: int | None = None
        _prf_supersedes_the_arm = bool(
            search_keys_prf
            or (entity_key_expansion and entity_type == "point"))
        if (query and query.strip() and is_embedded and not is_full_scan
                and strategies.get("fts")):
            from tortoise.search_engine import BM25_ENTITY_TYPES

            from .bm25 import bm25_enabled
            _bm25_enabled = bm25_enabled()
            _bm25_lexical = (entity_type in BM25_ENTITY_TYPES
                             and _bm25_enabled and not _prf_supersedes_the_arm)
            if _bm25_enabled and _prf_supersedes_the_arm:
                # Visible, not silent: the operator asked for the arm AND for an
                # expansion pass that would overwrite it. The read is CORRECT
                # (shipped rows, shipped scorer), but the lever they set is not
                # in effect — say so rather than let a measurement discover it.
                _logger.warning(
                    "#8488: BM25 lexical arm standing down — %s replaces the "
                    "lexical slot after the arm would have written it; the "
                    "shipped lexical scorer's rows are used",
                    "search_keys_prf" if search_keys_prf
                    else "entity_key_expansion")
        # #4199: the read's OWN kind scope, resolved ONCE. Only ever supplied
        # when a trace is being recorded (it decides a DECLARATION, never which
        # rows are returned), so a leg_trace=None production caller pays neither
        # the extra scope count nor any behavior change.
        _scope_kinds = (
            tuple(expanded_kinds)
            if leg_trace is not None and query_vec is not None
            and expanded_kinds else None)
        # #10893: ONE stage for all three retrieval legs plus the expansion
        # passes below that re-ask the store. The legs run in PARALLEL under a
        # per-leg deadline, so a per-leg sum would exceed the stage that
        # contains it; per-leg detail already has its own channel (`leg_trace`,
        # a different question).
        _mark_read_stage(STAGE_RETRIEVAL)
        # #11691: the SAME budget `degradation_chain` gives every leg's
        # `timeout_ms` below, so the RECORD dense leg's statement bound is
        # `min(record door, a share of this)` instead of the door's 15 s — the
        # share is `record_vector.record_statement_ms`, the read divided by the
        # ARM's statement count plus one (a third for a column-filtered label's
        # two statements, a quarter for a folded label's three), which reserves
        # the rest of
        # the read for the graph branch after a cancellation. Derived with the
        # identical expression degradation_chain uses (`int(elevated or
        # VECTOR_LEG_BUDGET_MS)`) rather than a second guess at it.
        _leg_budget_ms = int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS)
        raw_results = degradation_chain(
            graph, query, struct_kind, query_vec, strategies,
            entity_type=entity_type, limit=str_limit,
            is_embedded=is_embedded,
            elevated_timeout_ms=_elevated_timeout_ms,
            # #1359: the recorded vector-index API (procedure vs Cypher-native)
            # lets run_vector_query skip the failing signature attempt.
            vector_index_api=getattr(proj, "_vector_index_api", None),
            # #13108: the labels this STORE measured a VECTOR index for
            # (`_record_vector_index_inventory`). A label absent from it skips
            # two round trips that can only be REFUSED — measured at ≈58 % of an
            # unindexed `:Object` read's store time. `None` (the inventory's
            # not-measured lane) is forwarded as None, i.e. attempt as before.
            # ⛔ The BEHAVIOURAL half (`…_for_reads`), never
            # `_vector_indexed_labels`: the reporting half collapses "the
            # catalogue was returned but unparseable" into an empty set, and
            # skipping on that evidence would demote every dense read —
            # `:Point` included — to a full scan after an engine catalogue
            # reshape.
            vector_indexed_labels=getattr(
                proj, "_vector_indexed_labels_for_reads", None),
            excluded_statuses=() if include_terminal else None,
            leg_trace=status_trace,
            # A1 (#2070): the ask-lane numeric-token policy threads into the
            # sparse leg's OR-union (default False = search lane unchanged).
            keep_numeric=keep_numeric,
            min_vector_similarity=_vector_floor,
            # Only request the floored-leg report when a floor is active, so a
            # floor-off call keeps `trace_active` False (pre-#4028 shape).
            floored_legs=(_floored_legs if _vector_floor is not None else None),
            # #4199: the read's OWN kind scope. Only ever supplied when a
            # trace is being recorded (it decides a DECLARATION, never which
            # rows are returned), so a leg_trace=None production caller pays
            # neither the extra scope count nor any behavior change.
            # Hoisted to ``_scope_kinds`` so C2's dense expansion pass (#2518
            # indicator 2) measures the SAME scope: a second dense leg that
            # omitted it recorded a healthy UNSCOPED entry, and one healthy
            # vector entry is all ``declared_degraded_read`` needs to call the
            # read hybrid — so an untagged expansion entry would launder the
            # shipped leg's genuine ``no_embeddings`` into ``hybrid: True``
            # (the #4199/#5287 class via a new leg).
            scope_kinds=_scope_kinds,
            # #11642 / #4051 V1: the ANN leg from the POSTGRES RECORD.
            # ⛔ RESOLVED HERE, ON THIS THREAD, and passed in — `searcher()` reads
            # the request's tenant ContextVar, and a ThreadPoolExecutor worker
            # does not inherit ContextVars, so resolving it inside the worker
            # would find no tenant and take the graph branch on EVERY request
            # while reporting nothing. `None` (no record DSN, or no tenant
            # installed) keeps the graph branches byte-identically.
            pg_vector=_record_vector_searcher(self, timeout_ms=_leg_budget_ms),
        )

        if _bm25_lexical and "vector" not in _floored_legs:
            # #8488: re-score the lexical slot in-process. Bounded by
            # DEFAULT_BM25_CORPUS_CAP (see run_bm25_query), and it returns []
            # on ANY failure — in which case ``raw_results["fts"]`` keeps the
            # shipped leg's rows untouched, so enabling the arm can never leave
            # the read with a lexically weaker pool than it had before. The
            # same holds when the corpus is WIDER than the cap: the leg
            # declines its windowed ranking and returns [], because the shipped
            # leg answered over the whole label and a first-N-ids window can
            # only lose matches. The leg records ``declined=True`` + a warning,
            # and deliberately NOT ``degraded``: the read is complete, and that
            # flag is aggregated into the read's recorded status, so carrying a
            # leg-local decline there relabelled every read on a large store as
            # impaired memory (#8488 follow-up).
            # ``meter=False``: the shipped leg already counted the lexical
            # slot in the tally; this is the same slot scored differently, not
            # a second leg.
            from tortoise.search_engine import run_bm25_query
            # #8447 (review round 6, P2): WHERE the arm's trace entries start.
            # The arm records under the SAME ``leg="fts"`` slot (the trace's
            # vocabulary has three leg names and the lexical one is that name),
            # so the completeness proof below must attribute an entry by
            # PROVENANCE, not by slot name — otherwise the arm's own
            # ``breaker_open`` / ``query_failed`` verdicts are read as the
            # SHIPPED leg failing, and every read on a store where the arm trips
            # its breaker is marked ``truncated: true`` even though the shipped
            # leg answered and every row shipped. A marker that fires on a
            # healthy read is the false positive this module forbids.
            _bm25_trace_start = len(status_trace) if status_trace is not None else None
            _bm25_hits = run_bm25_query(
                graph, query, entity_type=entity_type, limit=str_limit,
                timeout_ms=int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS),
                excluded_statuses=() if include_terminal else None,
                leg_trace=status_trace,
                keep_numeric=keep_numeric,
                meter=False,
            )
            if _bm25_hits:
                # Ordering: this replaces the key IN PLACE when the shipped
                # leg contributed rows (dict assignment to an existing key
                # keeps its position), and appends it when the shipped leg
                # contributed nothing (degradation_chain only stores non-empty
                # legs) — so the #2952 fixed order is preserved in the first
                # case and is not in the second. Neither matters to the fused
                # result: ``rrf_fusion`` sums over legs keyed by
                # ``strategy_names`` built from the same dict, so leg order is
                # not load-bearing. Kept as a plain assignment for that
                # reason, not because order is guaranteed.
                raw_results["fts"] = _bm25_hits

        # #8447 (review round 5, P3): the PER-LEG window — how many rows the
        # widest single leg returned — taken HERE, before any additive pass
        # (``structural_hops``, PRF, entity-key expansion) widens a leg's list.
        # A leg asked for ``str_limit`` rows that returns that many FILLED ITS
        # OWN WINDOW, which proves matches beyond it were never read; saturation
        # is therefore the proof on its own and needs no filter on top. Taken
        # after the BM25 arm so a re-scored lexical slot counts as the leg it
        # replaced. (Not the fused union: two legs of 60 under a window of 120
        # reach ``str_limit`` together without either leg saturating.)
        _leg_peak = max((len(_rows) for _rows in raw_results.values()), default=0)

        # #8447 (review round 6): DID EVERY ASKED LEG ANSWER? The proof below only
        # ever looked at the rows the SURVIVING legs returned, so a leg that
        # raised, hit the 500 ms collective deadline or was refused by its own
        # circuit breaker was invisible — its rows were never read and are
        # unreachable, exactly like rows behind the cap. Ask the trace.
        #
        # ``_asked_legs`` mirrors ``degradation_chain``'s own submit predicate (a
        # leg is only asked when the strategy is enabled AND it has a question:
        # the sparse/structural legs need a query, the dense leg needs a query
        # VECTOR). A leg that was never asked is a DECLARED capability gap — no
        # embedder installed, say — not a drop from a set this read matched;
        # ``status: degraded`` is the field that reports it, and marking every
        # read on a keyword-only deployment truncated would be the false positive
        # that trains readers to ignore this marker.
        #
        # ⛔ COMPUTED HERE, BEFORE the first early return below. The fold used to
        # sit with the exactness proof, ~700 lines further down, so the two
        # returns that leave this block (the #4028 floored vector leg, and
        # ``no_fallback_applicable``) shipped ``truncated: false`` over a read
        # whose sparse leg never answered — 0 rows marked COMPLETE over a store
        # that was only half read. Every return after this point consults it.
        #
        # ``skip_from`` is the BM25 arm's provenance (see the call site): its
        # entries are appended after the chain and describe the ARM.
        _asked_legs = {
            "fts": bool(strategies.get("fts") and query),
            "vector": bool(strategies.get("vector") and query_vec),
            "structural": bool(strategies.get("structural")),
        }
        from .truncation import lost_leg_owners as _lost_leg_owners
        _lost_legs = _lost_leg_owners(
            _asked_legs, status_trace, skip_from=_bm25_trace_start)

        if not raw_results:
            # #4028: the vector leg RAN and the relevance floor removed every
            # near neighbour. That is an ANSWER (nothing relevant), not a leg
            # failure — so the in-memory TF-IDF fallback, which returns a hit
            # for almost any query, must NOT fire here.
            if "vector" in _floored_legs:
                if leg_trace is not None:
                    leg_trace.append(_trace_entry(
                        "fallback", ran=False, degraded=False,
                        reason="relevance_floor_empty", count=0))
                # A REACHED store that answered with nothing relevant is still
                # an ANSWER — it must report a term like every other
                # data-returning path here. The hosted route indexes
                # ``status_out["status"]`` unguarded, so a missing term is a
                # 500, not a null.
                #
                # #8447 (review round 6, P3): an ASKED leg that never answered
                # is the ONE reason this "nothing relevant" answer is not
                # complete — the floor emptied the dense leg while the sparse one
                # could have failed in the same read. The total is withheld
                # (``exact`` stays False), so the caller gets the third state.
                _trunc["incomplete"] = bool(_lost_legs)
                # #10893 review F2: this arm RETURNS with no graph work of its
                # own, so without a terminal mark a breach here publishes
                # ``retrieval`` — the stage that is still OPEN, whose legs have
                # already answered. The right name for "read finished, answer
                # late" is ``response``.
                _mark_read_stage(STAGE_RESPONSE)
                return _with_read_status([])
            # All strategies failed — fallback to in-memory TF-IDF (Point only).
            if query and entity_type == "point":
                # #1375: serve from the cached lean corpus snapshot when
                # available (kills the ~350ms full-payload re-fetch + per-call
                # TF-IDF re-fit). Falls back to the legacy path when the
                # snapshot is unavailable (too big / build failed).
                #
                # #10602: through ``get_or_build``, NOT ``build_snapshot`` — a
                # degraded read used to fetch the whole corpus per reader, so N
                # concurrent agents cost N corpus reads and each call's latency
                # grew with the fleet's concurrency (measured: 8 readers, 4.6 s
                # each vs 595 ms for one). Coalescing adds NO cache entry and NO
                # TTL — but it is NOT "no cache-policy change" and NOT
                # result-identical: an overtaken build is no longer cached (so a
                # write-heavy graph loses hits the old unconditional ``put`` had),
                # and a reader racing a write's commit→invalidate tail can be served
                # that cohort's pre-commit rows. See the COMMIT→INVALIDATE WINDOW
                # note on ``get_or_build``.
                from tortoise.fallback_snapshot import (  # noqa: I001
                    _store as _fb_store, snapshot_key, get_or_build,
                    search_snapshot,
                )
                _key = snapshot_key(proj, getattr(self, "_namespace", None))
                # #2351: the snapshot is a deliberately LEAN projection (it
                # carries no class column), so it cannot honour a claim-class
                # filter pre-truncation. When one is requested, DROP the (cached
                # or fresh) snapshot and take the legacy path below — which
                # filters the CORPUS before truncating. Correctness over the
                # ~350ms the snapshot saves, on a rare degraded AND
                # class-filtered read.
                if claim_class is not None:
                    _snap = None
                else:
                    _snap = get_or_build(proj, _key, store=_fb_store)
                if _snap is not None:
                    snap_hits = search_snapshot(
                        query, _snap, limit=limit, kind=kind,
                        exclude_status=exclude_status,
                        include_terminal=include_terminal,
                        # #4509: parity with the primary path's pre-truncation
                        # turn-echo exclusion — a degraded read must not leak a
                        # capture's own transcript echoes as S3 priors.
                        exclude_turn_echo_session=exclude_turn_echo_session,
                    )
                    if status_trace is not None:
                        status_trace.append(_trace_entry(
                            "fallback", ran=True, degraded=True,
                            reason="tfidf_snapshot", count=len(snap_hits)))
                    # #8447 (review P1): this arm DROPS rows — ``search_snapshot``
                    # caps at ``limit`` — and it skipped the marker entirely, so a
                    # 20-match store answered 5 rows marked COMPLETE. The pre-cap
                    # MATCH count is not exposed by the scorer, so a FULL page is
                    # read as what it is: proof that more MAY exist, never proof
                    # that it does not. The total is therefore WITHHELD (round 2
                    # caught a fabricated one here). Conservative in the safe
                    # direction: this arm already reports ``status: degraded``, so
                    # over-marking cannot turn a HEALTHY read into a partial. The
                    # residual imprecision is a degraded read whose match count is
                    # EXACTLY ``limit`` — marked incomplete when it is complete.
                    _trunc["candidates"] = len(snap_hits) + (
                        1 if len(snap_hits) >= limit else 0)
                    _trunc["exact"] = False
                    # #8447/#10560: the SAME fold the early return above and the
                    # ``no_fallback_applicable`` return below both carry, and for
                    # the same reason. This tier answers by RANKING the whole
                    # kind-scoped corpus instead of by asking the retrieval
                    # legs, so an ASKED leg that never answered
                    # (``fts``/``index_missing`` — the #8488 lexical half
                    # unreachable) contributes nothing to the rows this tier
                    # saw: ``candidates`` counts only what THIS tier ranked, so
                    # it can say nothing about the dead leg. The verdict this
                    # tier must not publish is therefore ``truncated: false``
                    # merely because the page came back short — the matches the
                    # dead leg would have supplied were never READ by it — and
                    # the tier's own recorded posture is "conservative in the
                    # safe direction — this arm already reports
                    # ``status: degraded``, so over-marking cannot turn a
                    # HEALTHY read into a partial".
                    #
                    # ⚠️ Reaching this tier means the arm did NOT SERVE the
                    # slot — if it had, ``raw_results`` would be non-empty and
                    # this block is unreachable. It does NOT mean the arm is
                    # disabled: a tripped breaker, a raised arm and a
                    # cap-driven ``declined`` all leave the slot unserved with
                    # ``TORTOISE_BM25_LEXICAL=1``.
                    _trunc["incomplete"] = bool(_lost_legs)
                    # #10893 review F2/F4: the decoration is a GRAPH round trip
                    # (`fetch_point_epistemic_state` + the `subject_binding_
                    # available` probe), so it must finish BEFORE the terminal
                    # mark. A return expression is evaluated after the preceding
                    # statement, so marking first charged that fetch to
                    # `response` — which claims "every stage finished, the answer
                    # is late" while the store is the blocking call. `retrieval`
                    # is still the open stage here and its contract already
                    # covers "whatever the graph answers".
                    _decorated = _decorate_fallback_hits(snap_hits, graph)
                    _mark_read_stage(STAGE_RESPONSE)
                    return _with_read_status(_decorated)
                points = self.query(kind=kind,
                                    include_retracted=include_terminal,
                                    # #7983: these hits are recorded ONCE, by
                                    # `_with_read_status` below — a second record
                                    # here inflates read_count for every fallback
                                    # hit — and the scan itself is not a caller read.
                                    _record_reads=False)
                if exclude_status and points:
                    # Same status exclusion as step 5d (#898 review round-2):
                    # the degraded fallback must not leak superseded/deprecated
                    # into the UC1 state view. self.query returns raw node
                    # dicts carrying the status property.
                    points = [p for p in points
                              if (p.get("status") or "") not in set(exclude_status)]
                if exclude_turn_echo_session and points:
                    # #4509 parity (same reasoning as the snapshot tier above):
                    # drop the session's own echoes BEFORE ``fallback_tfidf``
                    # truncates to ``limit``.
                    points = [p for p in points if not is_turn_echo_row(
                        exclude_turn_echo_session,
                        {"id": p.get("id"),
                         "point_kind": p.get("pointKind"),
                         "content": p.get("content")})]
                allowed_ids: set[str] | None = None
                if points and claim_class is not None:
                    # #2351: filter the CORPUS before truncation, the same
                    # reason ``exclude_status`` is threaded into the tiers
                    # above: a filter applied AFTER the limit cut silently
                    # under-fills the caller's ``limit``.
                    _allowed = _filter_and_annotate_claim_class(points)
                    if _allowed is not None:
                        allowed_ids = {p.get("id") for p in _allowed}
                        points = [p for p in points
                                  if p.get("id") in allowed_ids]
                legacy_hits = fallback_tfidf(query, points, limit=limit)
                # #8447 (review rounds 1+2): this arm DROPS rows at ``limit``, so
                # it must mark. The pre-cap MATCH count is not exposed by the
                # scorer (``search_points`` ranks the whole corpus and slices), so
                # a FULL page is read as what it is — proof that more MAY exist,
                # never proof that it does not — and the total is WITHHELD rather
                # than published as ``len(points)``, which is the whole kind-scoped
                # CORPUS, not the matches (round 2's finding: 5,000 Points with 20
                # matches answered ``total_available: 5000``).
                _trunc["candidates"] = len(legacy_hits) + (
                    1 if len(legacy_hits) >= limit else 0)
                _trunc["exact"] = False
                if allowed_ids is not None:
                    # The ANNOTATION has to be re-applied: ``fallback_tfidf``
                    # rebuilds every row through ``SearchResult.to_dict()``,
                    # which has no class field, so the stamp put on the CORPUS
                    # dicts above does not survive into the returned rows. The
                    # rows kept here are exactly ``allowed_ids`` by
                    # construction, so the class is known without re-reading.
                    for _row in legacy_hits:
                        if _row.get("id") in allowed_ids:
                            _row["claim_class"] = claim_class
                if status_trace is not None:
                    status_trace.append(_trace_entry(
                        "fallback", ran=True, degraded=True,
                        reason="tfidf_legacy", count=len(legacy_hits)))
                # #8447/#10560: fold the lost-leg verdict here too — the same
                # fold the floored return and the snapshot tier above carry, and
                # the one ``no_fallback_applicable`` below carries as well (it
                # is the third degraded return of the three; this is the second).
                # A read whose ASKED lexical leg never answered may not publish
                # ``truncated: false`` merely because the corpus fallback found
                # rows: those rows were ranked from the whole corpus, not from
                # the match set the dead leg owed this read.
                _trunc["incomplete"] = bool(_lost_legs)
                # #10893 F4: same as the snapshot tier above — decorate (graph
                # round trips, `retrieval`'s own contract) before the terminal
                # mark, never inside it.
                _decorated = _decorate_fallback_hits(legacy_hits, graph)
                _mark_read_stage(STAGE_RESPONSE)
                return _with_read_status(_decorated)
            if status_trace is not None:
                status_trace.append(_trace_entry(
                    "fallback", ran=True, degraded=True,
                    reason="no_fallback_applicable", count=0))
            # #8447 (review round 6, P3): same fold as the floored return above —
            # nothing was cut, but an asked leg that never answered means the
            # empty answer is not a complete one.
            _trunc["incomplete"] = bool(_lost_legs)
            # #10893 review F2: terminal mark — this arm does no graph work, so
            # the time from here to the answer being written out is response
            # assembly, not retrieval.
            _mark_read_stage(STAGE_RESPONSE)
            return _with_read_status([])

        # R4 (#1543): 1-2 hop IMPL/NAND expansion on TEXT hits (graph as
        # recall amplifier — graphiti episode-mentions pattern). Folds into
        # the structural strategy so RRF fusion, match_source, and the E2E-1
        # leg-mix keep the established leg vocabulary (fts/vector/structural/
        # tfidf). Seeds = FTS + vector hit ids only — never the structural
        # kind-scan hits (no circular amplification). Default structural_hops
        # = 0 skips the block entirely (byte-identical to pre-R4); the TF-IDF
        # fallback above returns before this block, so degraded runs are
        # unchanged too.
        if structural_hops > 0:
            text_hit_ids = [
                pid
                for _strat in ("fts", "vector")
                for pid, _score in raw_results.get(_strat, [])
            ]
            if text_hit_ids:
                expansion = expand_structural_hops(
                    graph, text_hit_ids, max_hops=structural_hops,
                    limit=str_limit,
                    excluded_statuses=() if include_terminal else None,
                    timeout_ms=int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS),
                )
                if expansion:
                    merged = list(dict(raw_results.get("structural", [])).items())
                    seen = {pid for pid, _score in merged}
                    merged.extend(
                        (pid, score) for pid, score in expansion
                        if pid not in seen)
                    raw_results["structural"] = merged

        # A4 (#2070): search_keys pseudo-relevance-feedback expansion (ask
        # lane, caller-gated). A bounded SECOND FTS pass whose OR-union is
        # the original query's tokens (slots reserved per build_or_query's
        # cap) PLUS additive aliases harvested from the retrieved pool's
        # top-5 hits — never replacing original tokens. Fail-open: any fetch/
        # query failure keeps the ORIGINAL fts leg (byte-identical); the
        # expansion is additive recall only.
        if search_keys_prf and raw_results.get("fts"):
            expanded_fts = self._search_keys_prf_expansion(
                query, raw_results["fts"],
                str_limit=str_limit,
                excluded_statuses=() if include_terminal else None,
                leg_trace=status_trace,
                # P1-fix (#2070): the second pass respects the caller's A1
                # keep_numeric — an operator who opted OUT of numeric tokens
                # must not have them silently re-introduced by the PRF pass
                # (the alias harvest stays numeric-aware; only the original
                # query's re-tokenization honors the opt-out).
                keep_numeric=keep_numeric,
            )
            if expanded_fts is not None:
                raw_results["fts"] = expanded_fts

        # C2 (#2518, #2513): entity/fact-augmented key expansion (caller-
        # gated, default OFF — the #1745 fail-safe posture). Additive-only
        # and fail-open exactly like A4: the query's entity anchors resolve
        # through the Object-name index and their linked points' E3
        # ``search_keys`` re-run the sparse OR leg so same-subject points
        # from OTHER sessions can surface; any failure keeps the ORIGINAL
        # fts leg (byte-identical). Runs after A4 so the two second passes
        # compose when both are on (each operates on the current fts leg).
        # Point-only by design: the harvest resolves :Point linked to the
        # anchors (search_keys live on points), so expanding an 'event' or
        # 'operator' arm would merge POINT ids into an event/operator top-k
        # and silently truncate the real hits (P1 #2557 review — the event
        # arm returned [] where OFF returned the event).
        #
        # #2518 indicator 2 (dense-leg parity): the same harvest also feeds a
        # SECOND dense pass (``_entity_key_dense_pass``) when the operator arms
        # ``TORTOISE_ENTITY_KEY_DENSE``. The harvest is taken ONCE and shared, so
        # the dense half costs no extra graph query WHEN THE SPARSE HALF ALREADY
        # NEEDED ONE; and the harvest itself runs whenever EITHER half can use it
        # — so a read whose sparse leg came back empty still expands its dense
        # leg (and vice versa), instead of the two halves being chained on one
        # another's success. ⛔ That second case is the one where arming the dense
        # half DOES add work: with an empty sparse leg the disarmed code stopped
        # at the old ``if not fts_hits`` guard, so the armed read pays the anchor
        # FTS plus the ``aboutObject`` join (whose server-side ``collect()`` has
        # no LIMIT — the per-anchor cap is applied client-side) on a read that
        # previously paid neither. Stated rather than left to be discovered. With the dense switch
        # OFF (the default, and the measured arm) the gate below reduces to the
        # sparse half's original ``raw_results.get("fts")`` condition, and the
        # code path is byte-identical to the shipped one.
        _eek_dense_on = _entity_key_dense_enabled()
        # additive to an EXISTING dense leg only: the pass never resurrects a
        # leg this read did not have, and never re-populates one the #4028
        # relevance floor emptied (that empty answer is the read's declared
        # result).
        _eek_dense_ok = bool(
            _eek_dense_on
            and raw_results.get("vector")
            and "vector" not in _floored_legs
            and query_vec is not None
        )
        if (entity_key_expansion and entity_type == "point"
                and (raw_results.get("fts") or _eek_dense_ok)):
            _eek_aliases = self._entity_key_alias_harvest(
                query, keep_numeric=keep_numeric)
            if _eek_aliases and _eek_dense_ok:
                expanded_vec = self._entity_key_dense_pass(
                    _dense_query, raw_results["vector"],
                    aliases=_eek_aliases,
                    str_limit=str_limit,
                    excluded_statuses=() if include_terminal else None,
                    leg_trace=status_trace,
                    min_similarity=_vector_floor,
                    # #4199/#5287: the SAME scope the shipped leg measured, so
                    # the expansion's entry is tagged (or re-labelled) exactly
                    # like it — see the pass docstring's declaration note.
                    scope_kinds=_scope_kinds,
                    # the shipped leg's own timeout, so on a slow store the
                    # expansion no-ops for the same reason rather than sooner.
                    timeout_ms=int(_elevated_timeout_ms or VECTOR_LEG_BUDGET_MS),
                )
                if expanded_vec is not None:
                    raw_results["vector"] = expanded_vec
            if _eek_aliases and raw_results.get("fts"):
                expanded_fts = self._entity_key_expansion_pass(
                    query, raw_results["fts"],
                    aliases=_eek_aliases,
                    str_limit=str_limit,
                    excluded_statuses=() if include_terminal else None,
                    leg_trace=status_trace,
                    # the anchor + alias tokenization honors the caller's A1
                    # numeric policy (same posture as the A4 P1-fix).
                    keep_numeric=keep_numeric,
                )
                if expanded_fts is not None:
                    raw_results["fts"] = expanded_fts

        # 4. Fuse via RRF (skip if single strategy or full-scan)
        if is_full_scan or len(raw_results) == 1:
            strat_name, ranked = next(iter(raw_results.items()))
            # Apply threshold filter (score floor)
            fused = {pid: score for pid, score in ranked if score >= threshold}
            match_source = strat_name
        else:
            ranked_lists = list(raw_results.values())
            # #1657 weighted RRF: per-strategy fusion weights. The measured
            # fix for fusion dilution — the burn showed equal-weight RRF
            # costs ~2 nDCG pts when a strong vector leg is diluted by weak
            # FTS/structural legs. PRODUCTION DEFAULT (owner decision
            # 2026-08-25): vector 1.5x — the bge-small vector leg is the
            # strongest retriever; the HNSW surface shows hybrid > vector-only
            # (additive value from FTS/structural), so a moderate 1.5x shifts
            # toward the stronger leg without abandoning the others. Env
            # override for tuning: TORTOISE_FUSION_WEIGHTS='{"vector": 2.0}'
            # (JSON; the LongMemEval-S re-burn later will refine this).
            # A3 (#2070): the ASK lane threads its own
            # TORTOISE_ASK_FUSION_WEIGHTS / TORTOISE_ASK_FUSION_K knobs via
            # the ``fusion_weights``/``fusion_k`` kwargs — when the caller
            # passes None/60 the shared resolution below is unchanged
            # (search lane byte-identical).
            if fusion_weights is None:
                fusion_weights = {"vector": 1.5}
                _fw = os.environ.get("TORTOISE_FUSION_WEIGHTS")
                if _fw:
                    import json as _json
                    try:
                        fusion_weights = _json.loads(_fw)
                    except (ValueError, TypeError):
                        fusion_weights = {"vector": 1.5}
            if _bm25_lexical:
                # #8488: the lexical leg's weight is BOUNDED below the dense
                # leg's (tortoise/bm25.py — #7704 / #1657). A no-op for the
                # recorded production default; it removes only an unbounded
                # override, which would let the lexical leg decide a fused
                # order by itself.
                from tortoise.search_engine import clamp_lexical_fusion_weight
                fusion_weights = clamp_lexical_fusion_weight(fusion_weights)
            _mark_read_stage(STAGE_FUSION)
            fused = rrf_fusion(
                ranked_lists,
                strategy_names=list(raw_results.keys()),
                weights=fusion_weights,
                k=fusion_k,
            )
            # Apply threshold filter to RRF scores
            if threshold > 0:
                fused = {pid: score for pid, score in fused.items() if score >= threshold}
            match_source = "rrf"

        # #10893 review F1: ``fusion`` is PURE CPU and must bracket only
        # ``rrf_fusion``. Everything below (recency weights, kind/status/claim
        # filters, echo fold, the rerank pool fetch) is a graph round trip, and
        # leaving ``fusion`` open across it made a breach inside, say, the
        # ``exclude_status`` query publish ``stage=fusion`` with ``retrieval``
        # already closed and healthy — the operator then rules out the graph and
        # blames the CPU. ``retrieval`` is documented as "all three legs PLUS
        # whatever the graph answers" (read_stages.py), so the graph bracketing
        # belongs there. Marked after the dispatch so the single-strategy
        # (keyword-only, no-embeddings) arm is bracketed identically.
        _mark_read_stage(STAGE_RETRIEVAL)

        # 5. Apply kind filter BEFORE truncating (skip if structural-only already filtered)
        result_ids = list(fused.keys())
        # D10 (ONTOLOGY v3.15 §4.4): a document is a :Source resolving by
        # `url` (#149's canonical key), not by `id` — the same three-way
        # id_field search_engine's legs use.
        if entity_type in ("source", "document"):
            id_field = "url"
        elif entity_type == "event":
            id_field = "eventId"
        else:
            id_field = "id"
        # Graph label for MATCH (operators are Point nodes with is_operator=true)
        graph_label = "Point" if entity_type == "operator" else label

        # 4b. Recency re-rank (#1544 R5): optional date weight on the fused
        #     pool. Rank-based factor from ``recency_field`` (newest → 1.0,
        #     undated → 0.0 via ``_recency_factors``); fail-open to plain
        #     RRF on any graph error (circuit-breaker pattern). Default off
        #     → byte-identical. ``id_field`` is the existing per-entity id
        #     (``id`` for Point, ``eventId`` for Event) and ``graph_label``
        #     likewise — the one batch Cypher per call stays label-correct.
        _recency_applied = False
        if recency_field and recency_boost > 0 and result_ids:
            try:
                rows = graph.query(
                    f"MATCH (n:{graph_label}) WHERE n.{id_field} IN $ids "
                    f"RETURN n.{id_field}, n.{recency_field}",
                    params={"ids": result_ids},
                ).result_set
                weights = _recency_factors([(row[0], row[1]) for row in rows])
                fused = {pid: s * (1.0 + recency_boost * weights.get(pid, 0.0))
                         for pid, s in fused.items()}
                # Secondary sort key = the recency factor. It only has to break
                # ties the multiplier cannot: rows whose raw leg score is 0
                # multiply to 0 whatever their recency weight, so the newer doc
                # must still rank first. Enabled branch only — default stays
                # byte-identical.
                fused = dict(sorted(fused.items(),
                                    key=lambda x: (x[1], weights.get(x[0], 0.0)),
                                    reverse=True))
                result_ids = list(fused.keys())
                # What the #12297 rerank below keys its skip on: whether the
                # caller's recency WEIGHTING could reorder anything. ⛔ Three
                # framings are all wrong, and each was shipped at some point:
                #
                # 1. "did the id order move" — a recency multiplier that already
                #    agrees with the fused order leaves the ids identical while the
                #    caller's order IS in force (the test's own fixture seeds
                #    newest-first, so 4b is a no-op permutation there and "no
                #    movement" inverted the page).
                # 2. "did any row carry a date" — ``_recency_factors`` returns an
                #    all-zero map below TWO dated rows (``if len(dated) < 2: return
                #    factors``, search_engine.py), because one date has nothing to
                #    rank against. A pool with a single dated row weighs every row 0
                #    and did nothing.
                # 3. "did at least two rows carry a date" — a page whose dates all
                #    fall in ONE group has many dated rows and still weighs every row
                #    the SAME factor (a tie group shares its highest factor), so the
                #    multiplier is an identity. Measured: five points created on one
                #    date — a bulk import or one capture burst — the read with
                #    recency byte-identical to the read without it, the rerank
                #    skipped, and the trace naming recency over a
                #    pool in plain fused order.
                #
                # The mechanical question is whether the EFFECTIVE WEIGHTS can
                # reorder anything: a SPREAD, not a sign and not a count.
                # Undated pools, one-dated-row pools and all-tied pages all collapse
                # to zero spread and let the lever run, which is what each of the
                # three must do — none of them applied an order to demote.
                _eff = {pid: weights.get(pid, 0.0) for pid in fused}
                _recency_applied = bool(_eff) and max(_eff.values()) > min(_eff.values())
            except Exception as e:
                _logger.warning("recency weighting failed (%s) — plain RRF", e)

        if kind and query is not None and result_ids:
            expanded = expanded_kinds
            kind_ids = set()
            extra_clause = "AND n.is_operator = true" if entity_type == "operator" else ""
            try:
                if len(expanded) == 1:
                    kind_rows = graph.query(
                        f"MATCH (n:{graph_label}) WHERE n.{kind_field} = $kind {extra_clause} AND n.{id_field} IN $ids RETURN n.{id_field}",
                        params={"kind": expanded[0], "ids": result_ids},
                    ).result_set
                else:
                    placeholders = [f"$kind_{i}" for i in range(len(expanded))]
                    params_dict: dict[str, Any] = {"ids": result_ids}
                    for i, k in enumerate(expanded):
                        params_dict[f"kind_{i}"] = k
                    kind_rows = graph.query(
                        f"MATCH (n:{graph_label}) WHERE n.{kind_field} IN [{', '.join(placeholders)}] {extra_clause} AND n.{id_field} IN $ids RETURN n.{id_field}",
                        params=params_dict,
                    ).result_set
                kind_ids = {row[0] for row in kind_rows}
            except Exception:
                kind_ids = set(result_ids)  # Pass-through on error
            result_ids = [pid for pid in result_ids if pid in kind_ids]

        # 5b. Apply relationship_filter (predicate:target_id format)
        if relationship_filter and result_ids:
            parts = relationship_filter.split(":", 1)
            if len(parts) == 2:
                pred, tid = parts[0].strip(), parts[1].strip()
                if pred and tid:
                    result_ids = filter_by_relationship(
                        graph, result_ids, pred, tid,
                        entity_type=entity_type, id_field=id_field,
                    )
                else:
                    _logger.warning("Invalid relationship_filter format: %s", relationship_filter)
            else:
                _logger.warning(
                    "relationship_filter must be 'predicate:target_id', got: %s",
                    relationship_filter,
                )

        # 5c. Apply traversal_path (e.g., 'Product→Feature') — resolve via pack registry
        if traversal_path and result_ids:
            resolved = self._resolve_traversal_path(traversal_path)
            if resolved:
                pred = resolved["predicate"]
                result_ids = filter_by_traversal_predicate(
                    graph, result_ids, pred,
                    entity_type=entity_type, id_field=id_field,
                )
            else:
                _logger.warning(
                    "traversal_path %r could not be resolved to a pack relation",
                    traversal_path,
                )

        # 5d. Apply exclude_status BEFORE truncation (#898): filtering after the
        #     limit cut would silently shrink results when superseded/deprecated
        #     points dominate the pool. Points with no status are kept; only
        #     Point-label entities have status (operators are Points too).
        if exclude_status and result_ids and graph_label == "Point":
            try:
                excluded = set(exclude_status)
                status_rows = graph.query(
                    "MATCH (n:Point) WHERE n.id IN $ids AND n.status IN $statuses "
                    "RETURN n.id",
                    params={"ids": result_ids, "statuses": sorted(excluded)},
                ).result_set
                status_excluded_ids = {row[0] for row in status_rows}
                if status_excluded_ids:
                    result_ids = [pid for pid in result_ids if pid not in status_excluded_ids]
            except Exception:
                _logger.warning("exclude_status filter failed — pass-through", exc_info=True)

        # 5d-ter (#2351). The claim-class filter, at the SAME pre-truncation
        #     seam as exclude_status above and for the same reason: a filter
        #     applied after the limit cut silently shrinks the result set
        #     whenever the other class dominates the pool.
        #
        #     ⛔ The filter is OPT-IN and the classes are read ONCE, here, then
        #     reused for the annotation below — so an unfiltered call performs
        #     no extra read and emits no extra key (byte-identical to
        #     pre-#2351). The read is the shared fan-out-complete
        #     ``claim_class.classes_for_ids``: an id carrying several nodes is
        #     kept when ANY of them carries the requested class, and an id with
        #     no marker AT ALL is 'asserted' — which is what makes filtering
        #     for the authored class return the whole pre-marker graph.
        claim_classes: dict[str, frozenset[str]] = {}
        # ``claimClass`` is a Point property, and the ANNOTATION below must apply
        # under exactly the same condition as this filter — otherwise a
        # class-filtered read of another entity type annotates every row with a
        # class it was never measured for.
        #
        # ⛔ The non-Point refusal happened at the TOP of this method (before
        # the fallback tiers, which return early); by here ``claim_class``
        # implies a ``:Point`` read, so the filter and the annotation share one
        # condition.
        _claim_class_applies = claim_class is not None
        # A FAILED class read must not become an answer: pass-through is the
        # right degradation for the FILTER (return what the caller would have
        # got before #2351), but stamping every row ``asserted`` would ASSERT a
        # class that was never read. So a failure clears the annotation too.
        _claim_classes_ok = True
        if _claim_class_applies and result_ids:
            _wanted = claim_class
            try:
                claim_classes = _claim_class.classes_for_ids(graph, result_ids)
                result_ids = [
                    pid for pid in result_ids
                    if _wanted in (claim_classes.get(pid)
                                   or frozenset({_claim_class.DEFAULT}))
                ]
            except Exception:
                _logger.warning(
                    "claim-class filter failed — pass-through, without the "
                    "class annotation", exc_info=True)
                claim_classes = {}
                _claim_classes_ok = False

        # 5d-bis (#4509). Apply the OPT-IN turn-echo exclusion at the SAME
        #     pre-truncation seam as exclude_status (#898): a caller that names
        #     its capture session drops that session's own transcript echoes
        #     from the fused Point candidate set BEFORE ``result_ids[:limit]``,
        #     so ``limit`` counts already-filtered candidates. Doing this
        #     AFTER the cut (the previous caller-side drop) could not be made
        #     sound — up to ``MAX_SESSION_TURNS`` (500) echoes could outnumber
        #     any caller-side refill window and starve a real prior ranked
        #     below them, ADDing a duplicate memory Point instead of folding.
        #     Opt-in by construction: the branch is not entered when the
        #     parameter is None, so every existing caller is byte-identical.
        if (exclude_turn_echo_session and result_ids
                and graph_label == "Point"):
            try:
                echo_rows = graph.query(
                    "MATCH (n:Point) WHERE n.id IN $ids "
                    "RETURN n.id, n.pointKind, n.content",
                    params={"ids": result_ids},
                ).result_set
                echo_ids = {
                    row[0] for row in echo_rows
                    if is_turn_echo_row(exclude_turn_echo_session, {
                        "id": row[0],
                        "point_kind": row[1] if len(row) > 1 else None,
                        "content": row[2] if len(row) > 2 else None,
                    })
                }
                if echo_ids:
                    result_ids = [pid for pid in result_ids if pid not in echo_ids]
            except Exception:
                _logger.warning(
                    "turn-echo exclusion failed — pass-through", exc_info=True)

        # ── #12297: the FUSED-CANDIDATE-LIST rerank, placed AHEAD of the cut.
        #
        # This is the ONE wiring point, so EVERY caller of this read is behind the
        # gate — the two the issue names, plus the ask lane (``ask_lane`` reaches
        # this method with the default ``order_by`` and no recency, so it runs the
        # rerank too; the brief's §5 table states that its cost is NOT calibrated):
        #   * user search — a caller's own ``limit`` (MCP ``tortoise_search`` /
        #     the SDK read);
        #   * the capture-time noun-matching prior — ``extractor_v2._fts_rows``
        #     calls THIS function at ``limit=3`` for every derived entity query,
        #     so the prior leg is reranked here without a second call site and
        #     without touching its window (``#11901`` measured the window lever
        #     and declined it standalone; this is the PAIRED shape).
        #     ⛔ That call site opts OUT of #8487's valid-time selection with a
        #     "no selection on the capture path" rationale, because the prior it
        #     persists is consumed as a SET. This rerank is a deliberate
        #     departure from that ruling and carries its own ``OVERRIDES:``
        #     record AT the call site (``extractor_v2._fts_rows``), naming the
        #     measured recall@3 delta and, explicitly, that only candidate
        #     composition is measured — not the downstream link/supersession
        #     outcomes. With the gate OFF (the default), none of this runs.
        #
        # ⛔ Placed BEFORE ``result_ids[:limit]`` on purpose: a reranker that
        # ran after the cut could only reorder rows that already survived, and
        # its entire value is choosing WHICH rows survive a fixed ``limit``.
        # ⛔ ``rerank_pool`` is a PERMUTATION, never a filter — see its contract.
        # ⛔ Ordered BEFORE step 5e (valid-time selection) ON PURPOSE, and this
        # is a CORRECTNESS ordering, not a preference. #8487 made the valid-time
        # demotion the read's last word on order so that a present-tense query
        # never serves a superseded claim as current; a relevance reorder placed
        # after it would silently re-promote exactly that row. Relevance may not
        # outrank correctness, so the rerank runs first and the demotion lands on
        # top of it. ``tests/test_rerank_pool.py`` pins the order.
        # ⛔ Default OFF (``TORTOISE_FUSED_RERANK``): the shipped order is
        # byte-identical when the flag is unset, which is also why the OFF path
        # costs no graph read — only the gate lookup below, resolved ONCE for
        # all four arms (it used to be four ``os.environ.get`` calls, which the
        # comment beside them described as "one env lookup").
        # ⛔ A CALLER-DECLARED RECENCY ORDER OUTRANKS A RELEVANCE REORDER. Step
        # 4b above already ordered the fused list by date for a caller that asked
        # for it (``recency_field`` + ``recency_boost``), and this block sorts on
        # ``(-score, index)`` alone — so running it here demotes that multiplier
        # to an exact-tie tie-break and INVERTS the page. Measured before this
        # guard, end to end (5 points, ``recency_field="createdAt"``,
        # ``recency_boost=10``, a positionally-descending scorer): gate off →
        # newest→oldest, gate on → exactly REVERSED, and the trace said "ok" —
        # no degrade, no disclosure. The skip is DISCLOSED for the same reason
        # the degrade arms below are: an opted-in lever that declined to run must
        # not be indistinguishable from one that was never switched on.
        # ⛔ KEYED ON WHAT STEP 4b DID, NOT ON WHAT THE CALLER ASKED FOR. 4b
        # fails OPEN — its ``except`` arm leaves plain RRF order — so a recency
        # REQUEST whose read broke reordered nothing; skipping the rerank then,
        # while reporting a reason naming recency, names a cause that never
        # applied and suppresses a lever that was free to run. ``_recency_applied``
        # is set inside 4b's ``try`` only. The emitted reason therefore says what
        # BITT (``recency_order_applied``), not what was ASKED FOR — the request
        # wording described the cause that did not fire.
        # ⛔ ORDER_BY IS A PRECONDITION, and it is the same defect as the recency
        # inversion in a different costume. The rerank runs BEFORE the ``limit``
        # cut, so it changes which rows SURVIVE the page — that is the whole
        # point on a relevance read, and it is a bug on every other ordering:
        # ``order_by="confidence"`` sorts the SURVIVORS by EP belief and
        # ``order_by="graph"`` by the graph ranker, both AFTER the cut, so a
        # rerank that re-picked the page would silently drop high-confidence
        # rows the un-reranked read returns. Step 5e's valid-time lever in this
        # same method scopes itself the same way (``order_by == "relevance"``);
        # this is that rule applied to the second pre-cut reorder. Not a silent
        # choice either: a read that opted in and was left alone DISCLOSES it.
        _rr_on = rerank_pool_enabled()
        _rr_order_by_ok = order_by == "relevance"
        _rr_text_ok = bool((query or "").strip())
        # ⛔ THE ENTRY CONDITION IS "ARMED", NOT "ARMED AND NON-EMPTY" (#12382).
        # Every arm below carries ``result_ids and ... and (query or "").strip()``
        # in its condition, so the TWO shapes with the least to rerank — a
        # query-less FULL SCAN (``tortoise_fts_query(None, kind=..., limit=N)``,
        # a documented caller-facing mode) and a candidate set that a filter
        # emptied before this point (the kind / claim-class / relationship /
        # traversal / status / turn-echo filters all narrow ``result_ids`` in
        # place) — left the block through its own ``not query``/empty-list exit
        # WITHOUT an entry. Measured at #12343's head ``93af561442``: with
        # ``TORTOISE_FUSED_RERANK=1`` a full-scan read returned 3 rows and
        # ``rerank entries: []`` — byte-identical to a caller that never armed the
        # gate, so "the lever declined" read as "the lever was never on".
        # That is the ONE rule this block exists to enforce (see the degrade arms
        # below and #8487's ``valid_time`` leg): an armed lever that did not run
        # MUST say so.
        # ⛔ NOT the read that found NOTHING AT ALL: that one returns from the
        # fallback path above, before this block, and already carries its own
        # verdict on every leg it asked (``empty_results``, or
        # ``below_relevance_floor`` / ``breaker_open`` where that is what
        # happened) — a rerank entry there would restate the same fact twice.
        # ⛔ THE REASONS ARE ``rerank_pool``'s OWN vocabulary, not a new one: it
        # already degrades on ``empty_query`` and ``empty_pool`` — terms the
        # wiring could never reach because these two exits fired first. Naming
        # them here makes the wiring agree with the function it calls.
        # ⛔ AND THE TWO ARE NOT THE SAME FACT, so they are not one reason: a
        # query-less read has nothing to SCORE WITH, an empty result set has
        # nothing to score — collapsing them (both ``empty_query``) would blame
        # the caller's query for a read whose candidate set was emptied, which is
        # the mis-direction ``pool_text_empty`` vs ``pool_text_read_failed`` below
        # is already careful about.
        # ⛔ AND WHEN BOTH ARE TRUE THE POOL WINS — ``rerank_pool``'s own order
        # (``tortoise/rerank.py``: the ``empty_pool`` return precedes the
        # ``empty_query`` one). A read that returned NO rows reads
        # ``empty_pool`` even when the query is also blank, because "you sent no
        # query" is not an instruction that would have helped: the caller's own
        # filter removed every candidate, and ``rerank_unscored`` is 0 either way.
        # The other order inverted the function's precedence, so "the wiring
        # agrees with the function it calls" was true only on the exclusive
        # shapes (review round 2 on #12382).
        # ⛔ GATE OFF STAYS SILENT — that is the one case where "no entry" is the
        # truthful signal (``test_the_off_path_writes_no_rerank_trace_entry``),
        # and it is why this arm keys on ``_rr_on`` rather than on the exits.
        # ⛔ A CALLER DECISION OUTRANKS AN EMPTY INPUT, in the arms' OWN precedence
        # (review round 1 on #12382): the four arms below are keyed on
        # ``result_ids`` too, so they cannot fire on a read this arm catches — and
        # naming ``empty_query`` for a caller that ALSO opted out (`recall_state`
        # passes ``_rerank_candidates=False`` with ``query=None`` by default,
        # ``sdk.py`` "1. Candidate pool") would send that reader to fix the query
        # when the lever is what declined it. Same rule the block states for
        # recency: the read's own reason wins over an incidental one.
        # ⛔ ALL THREE caller decisions, not just the two that are booleans here
        # (review round 4): ``recency_order_applied`` is the third — a read that
        # asked for a recency order and ALSO has an empty input used to be told
        # ``empty_query``, and supplying a query then reports
        # ``recency_order_applied``, so the advice was a loop.
        # ⛔ AND IT CAN COEXIST WITH AN EMPTIED POOL (review round 5):
        # ``_recency_applied`` needs a non-empty pool AT STEP 4b, not here — the
        # kind / relationship / traversal / status / claim-class / turn-echo
        # filters all narrow ``result_ids`` AFTER it, so ``recency_order_applied``
        # does shadow ``empty_pool`` on a filter-emptied pool (measured). That is
        # the same rule as the two decisions above, not an accident, and
        # ``test_the_recency_skip_CAN_coexist_with_an_emptied_pool`` pins it.
        _rr_recency_conflict = bool(_recency_applied)
        _rr_skip_reason = (
            "order_by_not_relevance" if not _rr_order_by_ok
            else "order_owned_downstream" if not _rerank_candidates
            else "recency_order_applied" if _rr_recency_conflict
            else "empty_pool" if not result_ids
            else "empty_query")
        if _rr_on and status_trace is not None and not (
                result_ids and _rr_text_ok):
            status_trace.append(dict(
                _trace_entry("rerank", ran=False, degraded=False,
                             reason=_rr_skip_reason, count=0),
                **{RERANK_POOL_KEY: fused_rerank_pool_width(),
                   RERANK_SCORED_KEY: 0,
                   RERANK_UNSCORED_KEY: len(result_ids)}))
        # ⛔ THE SECOND HALF OF THE SAME GUARD. ``order_by``/recency catch a
        # caller that ASKS for another order; they do not catch a caller that
        # re-owns the order DOWNSTREAM — or consumes it as a SET — and passes
        # neither.
        #   * ``_review_pool``, ``suggest_entry_points``, ``issue_insight``,
        #     ``recall_state``, ``recall_gaps``, ``_resolve_subgraph_seed`` —
        #     these six ALREADY pass ``_valid_time_rerank=False`` for the
        #     identical reason. Note that the last of them and ``_review_pool``
        #     are SET consumers rather than sorters (see the parameter's
        #     docstring): same flag, different harm;
        #   * ``search_sessions``, which re-sorts its pool by the PRE-rerank
        #     ``rrf`` score before slicing, and ``volunteer_context``, which
        #     binds this read as a ``_search_fn`` seam — ⛔ NEITHER opts out of
        #     ``_valid_time_rerank`` (that lever's ordering is what they sort
        #     on / feed), so do not "restore symmetry" by adding it here.
        # Without this arm the rerank re-picked their page INVISIBLY — the trace
        # said ``ok``, because nothing in the block knew the caller had opted
        # out.
        # (``_rr_recency_conflict`` is bound ABOVE, with the disclosure arm that
        # also needs it.)
        if (result_ids and _rr_on and _rr_text_ok
                and _rr_order_by_ok and not _rerank_candidates
                and status_trace is not None):
            status_trace.append(dict(
                _trace_entry("rerank", ran=False, degraded=False,
                             reason="order_owned_downstream", count=0),
                **{RERANK_POOL_KEY: fused_rerank_pool_width(),
                   RERANK_SCORED_KEY: 0,
                   RERANK_UNSCORED_KEY: len(result_ids)}))
        if (result_ids and _rr_on and _rr_text_ok
                and not _rr_order_by_ok and status_trace is not None):
            status_trace.append(dict(
                _trace_entry("rerank", ran=False, degraded=False,
                             reason="order_by_not_relevance", count=0),
                **{RERANK_POOL_KEY: fused_rerank_pool_width(),
                   RERANK_SCORED_KEY: 0,
                   RERANK_UNSCORED_KEY: len(result_ids)}))
        # ⛔ AND NOT THE OPT-OUT: the two arms are not exclusive by construction —
        # a caller that opted out AND has a live recency spread satisfied both and
        # emitted TWO ``leg="rerank"`` entries for ONE read with two different
        # reasons (measured: ``tools/longmem_eval/retrieve.py`` passes exactly that
        # combination). The first reason the read has is the one that wins: it
        # declined the rerank for its own sake, and recency never got a vote.
        if (result_ids and _rr_on and _rr_text_ok
                and _rr_order_by_ok and _rerank_candidates
                and _rr_recency_conflict and status_trace is not None):
            status_trace.append(dict(
                _trace_entry("rerank", ran=False, degraded=False,
                             reason="recency_order_applied", count=0),
                **{RERANK_POOL_KEY: fused_rerank_pool_width(),
                   RERANK_SCORED_KEY: 0,
                   RERANK_UNSCORED_KEY: len(result_ids)}))
        if (result_ids and _rr_on and _rr_text_ok
                and _rr_order_by_ok and _rerank_candidates
                and not _rr_recency_conflict):
            # ONE width for the batch read AND the scorer (#12297 review): the
            # knob used to be resolved only inside ``rerank_pool``, so a
            # configured width ABOVE the default was silently unreachable —
            # the text read had already dropped the ids past the constant.
            _pool_n = fused_rerank_pool_width()
            _pool_ids = result_ids[:_pool_n]
            _pool_texts = _fetch_rerank_pool_texts(
                graph, entity_type, graph_label, id_field, _pool_ids)
            # Defined for BOTH branches: the honest arm below asks "was anything
            # scored", which must be a value (not a NameError) when the read
            # returned nothing at all.
            _scored_rows: list[dict] = []
            if _pool_texts:
                # ⛔ A ROW THE READ DID NOT RETURN IS NOT SCORED. An id the read
                # excluded (a :Source without ``documentKind``, an operator on
                # the ``is_operator`` leg with no row) used to be handed to the
                # scorer as ``""`` — and an empty passage outranks an unrelated
                # real one, so an EXCLUDED row was PROMOTED over included ones.
                # Only ids that came back WITH text are scored; every other row
                # keeps the slot the fused order gave it, and the disclosed
                # ``unscored`` count counts them.
                # ⛔ MEMBERSHIP IS NOT ENOUGH — the text must be NON-EMPTY. The
                # read maps a null/absent property to ``""`` (``str(row[1] or
                # "")``), so a row that IS returned can still arrive with no text
                # (a stubbed Source with no title; any row whose property is null)
                # and a membership test would score it on ``""`` — the very
                # inversion this filter exists to remove. Truthiness, not
                # presence, is the test.
                _scored_ids = [pid for pid in _pool_ids
                               if (_pool_texts.get(pid) or "").strip()]
                _scored_rows = [{"id": pid, "content": _pool_texts[pid]}
                                for pid in _scored_ids]
                # ⛔ ONE ROW PER CANDIDATE, IN FUSED ORDER. A row with no usable
                # text cannot be scored, so it must keep the SLOT the fused order
                # gave it: ``rerank_pool`` writes the scored rows back into their
                # own slots and leaves the rest alone. Handing it a COMPACTED
                # list instead (scored head + everyone else appended) demoted an
                # unscored row below rows it outranked — measured, a textless row
                # FIRST in the fused order landed last with a zeroed scorer, and at
                # a fixed ``limit`` it dropped off the page while the trace said
                # ``count: 0``. Silent recall loss, and the mirror of the
                # promotion this block already refuses.
                _pool_rows = [{"id": pid, "content": _pool_texts.get(pid) or ""}
                              for pid in result_ids]
                # ⛔ DO NOT SPEND A MODEL CALL ON A POOL WITH NOTHING TO SCORE.
                # The REPORTING half of this case is the ``if not _scored_rows``
                # arm below (it overwrites ``_rr_stats``, so the trace is honest
                # whether or not this guard exists — measured, reverting this to
                # ``if True:`` leaves the trace IDENTICAL). What only this guard
                # prevents is the WORK: a read that returned rows but no USABLE
                # text (every property null) would reach ``rerank_pool``, LOAD the
                # cross-encoder and take its lock (~6.4 s cold, brief section 6)
                # for a result that cannot differ — the function then returns
                # ``no_scorable_text`` before ``score()``, so the lock is the whole
                # cost and nothing is scored. ⛔ Not a zero ``pool`` and not a
                # batch of empty passages: the caller passes the RESOLVED width
                # (``_pool_n``), and a textless row is never scored at all.
                # ``test_a_pool_with_no_usable_text_makes_no_scorer_call`` pins it.
                if _scored_rows:
                    # ⛔ ONE PASS, O(candidates). The shape this replaces rebuilt a
                    # membership set INSIDE a comprehension (O(width^2) on a path
                    # already priced at ~207 ms/mention, and
                    # ``TORTOISE_FUSED_RERANK_POOL`` clamps only the FLOOR). No
                    # set is needed at all now: ``_pool_rows`` is built once above
                    # over ``result_ids``, and ``rerank_pool`` scores only the
                    # scorable slots — so the cost cannot come back through this
                    # block even at an unbounded width.
                    # ⛔ EVERY CANDIDATE IS HANDED OVER, and by construction the
                    # count is true: ``unscored`` is every candidate the scorer did
                    # not see (textless inside the width, and every row past it).
                    # Handing ``rerank_pool`` only the scored head made its
                    # ``unscored`` structurally ZERO — the silent-cap defect
                    # reproduced INSIDE the disclosure.
                    _reranked, _rr_stats = rerank_pool(query, _pool_rows,
                                                       pool=_pool_n)
                    if _rr_stats.get("applied"):
                        # ``rerank_pool`` is a permutation of what it was handed —
                        # the scored slots reordered in place, every other slot
                        # (no usable text, or past the width) untouched — so the
                        # read's id set is unchanged AND an unscored row cannot be
                        # pushed off a fixed-limit page.
                        result_ids = [r["id"] for r in _reranked]
            if not _scored_rows:
                # ⛔ AN OPTED-IN LEVER THAT DID NOTHING MUST SAY SO. The pool
                # text read is best-effort (it returns {} on any graph error),
                # so without this arm a transient failure made the trace
                # IDENTICAL to the never-opted-in one — "the reranker silently
                # did nothing" indistinguishable from "the flag was never set".
                # #8487's ``valid_time`` leg records ``state_read_failed`` for
                # exactly this case; this is the same rule.
                # ``no_text_field`` is DEFENSIVE, not reachable today: every
                # ``entity_type`` this method accepts has a declared text
                # property, so an unmapped label can only arrive with a future
                # one. It is kept because the honest failure to report then is
                # "this read has no property to score", not "the read broke",
                # and a unit test exercises the arm so it cannot rot silently.
                # ⛔ THREE ways to arrive here, and they are NOT the same fact:
                #   no_text_field          the entity type has no text property
                #   pool_text_empty        the read SUCCEEDED and every pooled row
                #                          came back with empty/absent text — there
                #                          is nothing to score, and blaming the
                #                          read would send a reader to the graph
                #                          when the data is the problem
                #   pool_text_read_failed  the read itself returned nothing
                # Reporting the middle case as the last one is a mis-direction,
                # not a nicety: the counts below are identical either way, so the
                # REASON is the only thing that tells them apart.
                _rr_stats = {
                    "applied": False,
                    "degrade_reason": (
                        "no_text_field"
                        if _rerank_text_field(entity_type, graph_label) is None
                        # ⛔ TWO DIFFERENT FAILURES, TWO NAMES. ``read_failed`` is
                        # the batch read returning nothing (a graph error, an
                        # excluded id set); ``empty`` is the read SUCCEEDING and
                        # no row carrying usable text. Collapsing them blamed the
                        # read for a pool that was simply empty of text — the
                        # mislabel the ``source`` regression surfaced.
                        else ("pool_text_empty"
                              if _pool_texts else "pool_text_read_failed")),
                }
            # ⛔ THE CAP IS DISCLOSED, NEVER SILENT. ``rerank_pool`` bounds how
            # many leading candidates are SCORED; rows past that width keep
            # their retrieval order and can never be promoted, so a caller that
            # opted into the lever must be able to tell "the reranker decided"
            # from "the reranker never saw it". Before this, the stats were
            # written to ``_trunc["rerank"]`` and read by NOTHING
            # (``mark_truncation`` publishes only candidates/exact/incomplete),
            # i.e. the cap was undisclosed.
            # ``ran``/``reason``/``count`` follow the #8487 ``valid_time`` leg
            # precedent (``count`` = what the lever DID); the width rides as
            # ADDITIVE keys (#4999) and ``degraded`` stays False — in a leg
            # trace that flag means "a retrieval leg did not run".
            if status_trace is not None:
                _rr_applied = bool(_rr_stats.get("applied"))
                _rr_scored = int(_rr_stats.get("scored") or 0)
                _rr_unscored = int(_rr_stats.get("unscored") or 0)
                if not _rr_applied:
                    # A degraded rerank ORDERED NOTHING, so every candidate went
                    # unscored. Reporting the stats' absent keys as 0 would say
                    # "nothing was left unscored" on a read where nothing was
                    # scored — the same false all-clear one branch up.
                    _rr_scored, _rr_unscored = 0, len(result_ids)
                status_trace.append(dict(
                    _trace_entry(
                        "rerank",
                        ran=_rr_applied,
                        degraded=False,
                        reason=("ok" if _rr_applied else str(
                            _rr_stats.get("degrade_reason") or "not_applied")),
                        count=int(_rr_stats.get("moved") or 0)),
                    **{RERANK_POOL_KEY: _pool_n,
                       RERANK_SCORED_KEY: _rr_scored,
                       RERANK_UNSCORED_KEY: _rr_unscored}))

        # 5e. (#8487) VALID-TIME SELECTION — prefer the CURRENTLY-VALID version.
        #     Runs at the SAME pre-truncation seam as ``exclude_status`` (#898)
        #     so ``limit`` counts already-selected candidates: a stale claim
        #     demoted AFTER the cut has already consumed the page slot a live
        #     one deserved. Measured defect, re-measured 2026-10-18: a
        #     three-claim knowledge-update graph (ended / current / not-yet-
        #     effective) came back from this method in pure relevance order with
        #     the ended claim INSIDE the page as a current fact (rank 0 in one
        #     read shape, rank 1 in another), and on a present-tense query the
        #     2099-dated claim ranked first. Nothing filtered it: a claim can be
        #     ``live`` and carry a window (`hosted_api`'s create arm writes
        #     ``validFrom`` on live points, `mining` stamps it from the session
        #     date), so terminal-STATUS exclusion is orthogonal to valid TIME.
        #     The mechanism is ``time_aware.prefer_latest_order``'s rules — #2520
        #     wrote it and the eval lane applies it to its FINAL pool, but the
        #     product read path never did; ``prefer_latest_ids`` is the id-level
        #     adapter over the state map the decoration reads anyway.
        #     Never a FILTER: membership of the fused pool is preserved and an
        #     all-stale pool is left alone. Nothing is exempted from the
        #     ``limit`` cut either, so a demoted row keeps its pool membership
        #     but leaves the RETURNED PAGE when a full page of current rows
        #     precedes it (the ask lane reads with ``include_terminal=True`` for
        #     the ``[SUPERSEDED BY]`` marker, so it sees the marker on the rows
        #     that survive — not a promise that every demoted row is returned).
        #     Cost: one intent scan (two bounded regexes) evaluated BEFORE any
        #     graph read, so a query with no PREFER-LATEST intent pays no state
        #     read here at all; when the intent is present the read is
        #     POOL-width (the pre-truncation set — up to ``pool_size``, default
        #     120, not the page's ``limit``) because the selection must happen
        #     before the cut, and that read is reused by step 7.6 when it is
        #     complete (otherwise 7.6 does its own page-width read — the
        #     pre-#8487 behaviour).
        _valid_time_state: dict[str, dict] | None = None
        if (query and _valid_time_rerank and result_ids
                and entity_type == "point" and order_by == "relevance"):
            from . import time_aware as _valid_time_rules
            if _valid_time_rules.prefer_latest_enabled():
                # INTENT FIRST (#8487 review P2). ``prefer_latest_ids`` gates on
                # the intent internally, i.e. AFTER the state read, so the read
                # used to fire on every topical query and be discarded. Two
                # bounded regexes here decide whether the pool-width read is
                # worth issuing at all.
                _vt_intent = _valid_time_rules.detect_temporal_intent(query)
                if _vt_intent.kind != _valid_time_rules.PREFER_LATEST:
                    if status_trace is not None:
                        status_trace.append(_trace_entry(
                            "valid_time", ran=True, degraded=False,
                            reason="no-prefer-latest-intent", count=0))
                else:
                    _vt_covered = False
                    # The audit REASON for an unusable map, split by WHAT
                    # failed (#8487 review round 5, P3): "state_read_failed"
                    # when the state read raised, "state_read_incomplete" when
                    # it came back short, and the selection's OWN
                    # "selection_failed" when ``prefer_latest_ids`` raised. One
                    # shared handler could not tell the first from the last.
                    _vt_reason = "state_read_incomplete"
                    _vt_stats = {"reason": "state_read_incomplete",
                                 "demoted": []}
                    try:
                        _vt_state = fetch_point_epistemic_state(
                            graph, result_ids)
                    except Exception:
                        # Belt on top of braces: the helper swallows its own
                        # failures, so this is reachable only if the seam
                        # changes — and then the leg must say WHICH read died.
                        _logger.warning(
                            "valid-time state read failed — pass-through",
                            exc_info=True)
                        _vt_state = {}
                        _vt_reason = "state_read_failed"
                    # COMPLETENESS, not presence (#8487 review P1).
                    # ``fetch_point_epistemic_state`` NEVER raises — it
                    # swallows every failure and returns what it has (in
                    # practice ``{}``) — and ``{} is not None``, so
                    # caching on ``is not None`` let a failed read stand in
                    # for the page's state: step 7.6 skipped its own read
                    # and every hit lost ``status`` / ``superseded_by`` /
                    # the validity window, i.e. a superseded claim was
                    # served WITHOUT its ``[SUPERSEDED BY]`` marker while
                    # the trace below still reported a clean selection.
                    # Publish the map for reuse only when it covers every
                    # id it was asked for.
                    _vt_covered = bool(_vt_state) and all(
                        pid in _vt_state for pid in result_ids)
                    if _vt_state:
                        try:
                            result_ids, _vt_stats = (
                                _valid_time_rules.prefer_latest_ids(
                                    result_ids, _vt_state, query=query,
                                    question_date=(
                                        _valid_time_rules.resolve_question_date(
                                            query_date))))
                            _valid_time_state = (
                                _vt_state if _vt_covered else None)
                        except Exception:
                            # Fail-open, like every other decoration on this
                            # path: a validity read must never fail a read.
                            _logger.warning(
                                "valid-time selection failed — pass-through",
                                exc_info=True)
                            _valid_time_state = None
                            _vt_covered = False
                            _vt_stats = {"reason": "selection_failed",
                                         "demoted": []}
                            _vt_reason = "selection_failed"
                    if status_trace is not None:
                        # TWO different facts, TWO different fields (#8487
                        # review round 6, P2). ``count`` is what the selection
                        # DID — and it is the same question whether or not the
                        # map was complete enough to reuse at step 7.6: a
                        # SHORT (non-empty) map still demotes every id it has
                        # state for, so a `count=0` there would report "no
                        # selection" on a read whose page order demonstrably
                        # moved, and would discard ``demoted`` — the audit
                        # trail this leg exists for (#8487 scope 3).
                        # ``reason`` is WHY the leg ended where it did:
                        # the selection's OWN reason when the map was complete
                        # (``latest-first`` / ``already-latest-first`` /
                        # ``no-stale-entries``), else the impairment that made
                        # it unusable and which step 7.6's own page-width read
                        # had to cover — ``state_read_incomplete`` (short map),
                        # ``state_read_failed`` (it raised) or
                        # ``selection_failed`` (``prefer_latest_ids`` raised).
                        #
                        # ``degraded`` is DELIBERATELY False (#8487 review
                        # P1). In a leg trace that flag is not audit-only:
                        # ``ask_lane`` folds ANY degraded leg into the
                        # response's ``retrieval_degraded`` and
                        # ``read_status.classify_leg_trace`` turns it into
                        # STATUS_DEGRADED — and every other ``degraded=True``
                        # entry in this trace means "a retrieval leg did not
                        # run" (vector skipped, TF-IDF produced the rows).
                        # Here nothing was degraded: the hits are complete and
                        # step 7.6 re-reads the page state, so the hits keep
                        # ``status`` / ``superseded_by`` / the window. Claiming
                        # a degradation would redden a healthy read — for
                        # temporal intent only, which no caller could
                        # interpret. The REASON carries the impairment.
                        status_trace.append(_trace_entry(
                            "valid_time", ran=True, degraded=False,
                            reason=(_vt_stats["reason"] if _vt_covered
                                    else _vt_reason),
                            count=len(_vt_stats["demoted"])))

        # #8447: THIS is the seam where results are lost, and until now the read
        # said nothing about it — ``limit`` above the candidate count returned a
        # bare list, so "exactly 5 matches" and "20 matches, you got 5"
        # serialized identically. Record the population the cap is applied to;
        # ``_with_read_status`` publishes the verdict with the rows that ship.
        #
        # ``exact`` is a CLAIM, so it is only made on evidence: ``result_ids`` is
        # the union of the retrieval legs, each asked for ``str_limit`` rows, so a
        # set SHORTER than that window proves no leg filled its own limit. That
        # proof does NOT survive a post-retrieval filter, though — the filters at
        # the seams above (``exclude_status`` / ``claim_class`` /
        # ``exclude_turn_echo_session``) SHRINK the union after the legs filled
        # their windows, so a short set over a filtered read proves nothing. Nor
        # does it survive a leg that never ANSWERED at all (``_lost_legs``
        # below): its rows are absent from the union for a reason that has
        # nothing to do with how many matches exist. Every one of those
        # conditions must hold, or the count is withheld rather than published
        # as a floor wearing a total's clothes.
        _filters_shrank = bool(
            exclude_status or claim_class is not None
            or exclude_turn_echo_session
            # #8447 (review round 2, P1): the POST-window kinds. ``kind``,
            # ``relationship_filter`` and ``traversal_path`` all narrow
            # ``result_ids`` AFTER the legs filled ``str_limit`` — the legs are
            # ``entity_type``-scoped only, and the structural kind expansion is
            # post-retrieval — so each of them breaks the same proof the filters
            # above break.
            or (kind is not None and query is not None)
            or relationship_filter is not None
            or traversal_path is not None
            # #8447 (review P2): the SCORE FLOOR narrows the fused union after
            # the legs filled their windows — the rows it drops scored below it,
            # so the survivors are not provably the whole match population.
            or threshold > 0)
        # #8447 (review round 6, P2): WHY ``_lost_legs`` IS NOT COMPUTED HERE.
        # It is folded above, before this block's first early return, and asks the
        # trace with a ``(leg, reason)`` table instead of one flat reason list:
        #
        #   * ``fts`` + ``index_missing`` is a LOST leg, not an answer — the
        #     lexical leg RAISED and returned nothing while the rows it would
        #     have matched still exist, and that index is the only route to them
        #     (``tortoise/truncation.py::LEG_ANSWERED_REASONS``).
        #   * the #8488 BM25 arm writes into the SAME ``leg="fts"`` slot, so its
        #     own ``breaker_open`` / ``query_failed`` entries are attributed to
        #     the ARM by provenance (``skip_from``), never to the shipped leg
        #     whose rows survive untouched.
        #
        # Key ABSENCE from ``raw_results`` could never make this distinction — a
        # leg that ran and found nothing leaves no key either — which is why the
        # trace, not the result dict, is the evidence. A leg emptied by the #4028
        # relevance floor is likewise an answer (it recorded `degraded=False`),
        # so it never enters here.
        _trunc["candidates"] = len(result_ids)
        _trunc["exact"] = (
            (len(result_ids) < str_limit)
            and not _filters_shrank
            and not _lost_legs)
        # A read is INCOMPLETE even when nothing was cut from the visible set, in
        # two cases — both of them "matches this read never saw, reachable by no
        # follow-up call":
        #   * a leg filled its own window. Saturation IS the proof; it does not
        #     need a filter on top, and requiring one let a `pool_size` below
        #     `limit` (an EXACT override, `retrieve.resolve_pool_size`) launder a
        #     saturated window into COMPLETE;
        #   * an asked leg never answered (``_lost_legs``, folded above).
        _trunc["incomplete"] = bool(_leg_peak >= str_limit or _lost_legs)
        result_ids = result_ids[:limit]

        # 6. EP annotation (Point only)
        # #10893: the decoration read is the FIRST of the two graph round trips
        # on this path (step 7's batch content fetch is the second), and the one
        # a breach never used to be able to name. #13294: it is also the FIRST
        # and the SECOND of the two stages those reads are charged to — the
        # annotation to ``ep_annotation``, everything after it to ``retrieval``.
        _mark_read_stage(STAGE_EP_ANNOTATION)
        ep_breakdowns = annotate_ep_batch(graph, result_ids) if entity_type == "point" else {}
        # #13294: ``ep_annotation`` is the ANNOTATION READ and nothing else, so it
        # closes the moment that read returns. Step 7's batch content fetch below
        # and its epistemic-state read are graph round trips on the SAME path, and
        # they reopen ``retrieval`` — the stage documented as "all three legs PLUS
        # whatever the graph answers" (``read_stages.py``) — which ``ordering``
        # then closes at step 9. Leaving ``ep_annotation`` open across them
        # published the DOMINANT graph read of a search as "the annotation took
        # 12 s" with ``retrieval`` closed and healthy: the inference the
        # instrument exists to remove, in the shape ``fusion`` had before #10893
        # F1. The two stages that sit between a sub-read and a later graph read
        # obey ONE rule, and it is written on ``STAGE_ORDERING``.
        _mark_read_stage(STAGE_RETRIEVAL)

        # 7. Fetch entity content in BATCH (not N+1)
        entity_data: dict[str, dict] = {}
        try:
            if entity_type == "point":
                # D3 session identity: the point fetch carries the session
                # identity so ``SearchResult.session_id`` / the wire
                # ``sessionId`` is populated for points (it was hardcoded ""
                # — the capture-metadata columns were only read for
                # entity_type="document"). Three ordered graph-derived
                # sources, never a guess: the Point's own camel ``sessionId``
                # prop, ELSE the Point's own snake ``session_id`` prop, ELSE
                # the ``:Session`` ``CONTAINS`` edge the capture loop writes
                # for every turn Point (whose ``:Session`` id IS the session
                # identity for a captured session); a Point contained by
                # several ``:Session`` nodes resolves to the deterministic
                # minimum of them.
                #
                # #3804: the snake ``n.session_id`` prop IS read here. It is
                # the Point-level identity ``create_point(session_id=…`` and
                # the LongMemEval ingest write (``tools/longmem_eval/
                # ingest.py`` / ``ingest_v2.py`` — snake on POINTS; their camel
                # ``sessionId`` goes on Document/Event nodes), and before this
                # it was read by NO search/ask path — so a Point created
                # through the SDK's own documented path came back
                # ``"sessionId": ""`` and rendered ``[session ?]``. Both
                # spellings are EXPLICIT Point-level identity and either
                # outranks the derived ``CONTAINS`` edge; between the two the
                # camel ``sessionId`` wins because it is the WIRE spelling —
                # ``SearchResult.to_dict()`` emits ``sessionId`` and the
                # hosted/API contract is expressed in it — so a Point that
                # carries it explicitly is speaking the wire's own vocabulary.
                # The WRITE side is deliberately NOT unified into one spelling:
                # the snake prop is already persisted on existing rows (and
                # written by the eval ingest), so a read path must accept both
                # regardless — teaching the read side both spellings is the
                # narrower change than a prop migration.
                #
                # ⛔ KNOWN LIMITATION — this read is the PROJECT-search point
                # path only. The two degraded fallback tiers
                # (``fallback_snapshot.search_snapshot`` / ``fallback_tfidf``)
                # build their ``SearchResult``s and RETURN before this fetch
                # runs, and the snapshot keeps a deliberately LEAN projection
                # (``fallback_snapshot._SNAPSHOT_QUERY`` — no session column),
                # so a degraded hit still carries ``sessionId == ""`` and
                # renders ``[session ?]``. #8658's four verbatim-provenance
                # columns (``quote`` / ``source_turn_id`` / ``span_start`` /
                # ``span_end``) are read here, so a DEGRADED hit carries none of
                # them either and Slice A's own-source leg stays inert on that
                # tier (``retrieval._is_own_source`` returns False on its empty
                # quote). Widening that cached corpus is the
                # same separate, policy-governed change
                # ``search_provenance_enabled`` already declares for
                # provenance — a declared boundary, not an oversight of #3804
                # or of #8658.
                # ⚠️ #11682 widened it by ONE key: ``quote`` is now in
                # ``_SNAPSHOT_QUERY`` as INDEX TEXT for the lexical mirror
                # (parity with the real FTS index). That does NOT surface
                # anything — ``search_snapshot`` never sets ``quote`` on its
                # results — so a degraded hit still carries none of the four
                # columns and the boundary above is unchanged on the SURFACE.
                # The corpus-side retention is bounded by the existing caps
                # (``MAX_CORPUS_POINTS`` × ``MAX_SNAPSHOT_ENTRIES``).
                #
                # R17 (``tests/test_assembly_sdk.py``): naming these rows is a
                # DELIBERATE content change, so the assembly goldens were
                # re-captured and ``_FROZEN_CHUNKS`` updated under that
                # module's own "documented, reviewed content change" clause —
                # #3804 is that record.
                _prov = search_provenance_enabled()
                _prov_cols = ", n.extractedFrom, n.createdAt" if _prov else ""
                # #8658: the verbatim provenance columns Slice A (#2683) and the
                # #2684 span surface key on. Read UNCONDITIONALLY (they are
                # plain row properties of a Point — no extra join, no policy
                # flag: they are what a distilled claim's source anchor IS), and
                # folded into the hit PRESENT-ONLY, so an unprovenanced hit is
                # byte-identical. Placed AFTER ``_prov_cols`` so the #3837
                # provenance indices above never shift.
                _prov_q = 9 if _prov else 7   # first #8658 column index
                rows = graph.query(
                    "MATCH (n:Point) WHERE n.id IN $ids "
                    "OPTIONAL MATCH (sess:Session)-[:CONTAINS]->(n) "
                    "RETURN n.id, n.content, n.pointKind, "
                    "       coalesce(n.has_answer, false), n.sessionId, "
                    "       n.session_id, sess.id" + _prov_cols
                    + ", n.quote, n.source_turn_id, n.span_start, n.span_end",
                    params={"ids": result_ids},
                ).result_set
                # A Point contained by MORE THAN ONE :Session yields one row
                # per Session and the engine's row order is unspecified, so
                # the edge ids are collected and a deterministic minimum is
                # taken below — the wire identity must not depend on plan
                # order. (The previous lock-free form also served as the
                # last-resort id-prefix derivation; that inference is gone —
                # see ``retrieval.hit_session_id``.)
                edge_sids: dict[str, list[str]] = {}
                for row in rows:
                    pid = row[0]
                    entity_data[pid] = {
                        "content": row[1],
                        "kind": row[2],
                        # A5 (#2070): the stored evidence mark rides the hit
                        # payload (mirrors the eval's point_props_for_hits)
                        # so the lane's evidence boost has material.
                        "has_answer": bool(row[3]),
                        "sessionId": (row[4] or "") if len(row) > 4 else "",
                        # #3804: the snake spelling ``create_point(
                        # session_id=… )`` writes — read here as the second
                        # explicit identity source, never emitted raw (the
                        # wire key stays camel ``sessionId``).
                        "session_id": (row[5] or "") if len(row) > 5 else "",
                    }
                    # #8658: verbatim provenance — PRESENT-ONLY, exactly like
                    # the row's other optional fields. A falsy ``at``-style
                    # empty quote and a null ``source_turn_id`` (FalkorDB
                    # drops null props, #2949) mean the Point carries no
                    # verbatim anchor, so NO key is added and the hit renders
                    # / packages exactly as it did before. ``span_start == 0``
                    # is a real address — presence, never truthiness.
                    _quote = (row[_prov_q] or "") if len(row) > _prov_q else ""
                    if _quote:
                        entity_data[pid]["quote"] = _quote
                    _turn = (row[_prov_q + 1]
                             if len(row) > _prov_q + 1 else None)
                    if _turn is not None and _turn != "":
                        entity_data[pid]["source_turn_id"] = _turn
                    for _off, _key in ((2, "span_start"), (3, "span_end")):
                        if len(row) > _prov_q + _off:
                            _v = row[_prov_q + _off]
                            if _v is not None:
                                entity_data[pid][_key] = _v
                    if _prov:
                        # Provenance (#3837): the Source/document ref and the
                        # capture time — additive, read ONLY when the flag is
                        # set so the default query is unchanged.
                        entity_data[pid]["source_ref"] = (
                            row[7] if len(row) > 7 else None
                        )
                        entity_data[pid]["captured_at"] = (
                            (row[8] or "") if len(row) > 8 else ""
                        )
                    edge_sid = (row[6] or "") if len(row) > 6 else ""
                    if edge_sid:
                        edge_sids.setdefault(pid, []).append(edge_sid)
                if _prov and entity_data:
                    # #5581 (the #5038 read-path half): the POINT-LEVEL currency
                    # verdict, and the sourced disclosure that rides with it.
                    # ONE batch query for the hits' ``extractedFrom`` links —
                    # read ONLY under the flag, exactly like the two columns
                    # above, so an unflagged call pays nothing and returns the
                    # byte-identical row it always did.
                    #
                    # ⛔ The verdict is the §4.6 AGGREGATE, never one link's
                    # pair: a hit is a Point, and ``extractedFrom`` is
                    # many→many, so reporting a single link's verdict as the
                    # Point's says ``current`` for a Point another link makes
                    # stale — the false-current the note exists to prevent
                    # (see ``currency_status``'s ⚠ and
                    # ``tests/test_source_version_read_wiring_5199.py``'s
                    # ``test_search_hit_makes_no_version_claim``).
                    #
                    # ``outdated_sources`` carries the behind links' sources so
                    # the row DISCLOSES what moved rather than flagging a
                    # binary state — the owner's ruling (2026-09-26) is that the
                    # out-of-date fact is withheld as an answer and disclosed as
                    # an FYI **carrying its source**; a flag alone is the shape
                    # the plan's `OVERRIDES:` line rejects.
                    from .search_engine import aggregate_currency, read_currency_links
                    _currency_links = read_currency_links(graph, list(entity_data))
                    for pid, entry in entity_data.items():
                        links = _currency_links.get(pid, [])
                        entry["currency"] = aggregate_currency(
                            (lk["recorded"], lk["current"]) for lk in links)
                        entry["outdated_sources"] = [
                            lk for lk in links if lk["currency"] == "stale"]
                for pid, entry in entity_data.items():
                    # D3: no fabrication, and an EXPLICIT identity wins: the
                    # Point's own ``sessionId`` (camel) prop, else its own
                    # ``session_id`` (snake) prop — #3804 — else the
                    # deterministic minimum of the :Session CONTAINS edge ids
                    # (sanitized BEFORE the pick — a lexicographic minimum
                    # over raw ids could select an unrenderable one and blank
                    # a value that had a perfectly good sibling). CAMEL FIRST:
                    # it is the WIRE spelling (``to_dict()`` emits
                    # ``sessionId``), so it is the one a Point carrying both
                    # spellings intends. The wire is filtered through the SAME
                    # sanitizer the reader tag uses: ``/v1/search`` output is
                    # agent-consumed and may be re-embedded into a
                    # line-oriented prompt, so it must not hand back a
                    # structure-breaking id.
                    own = (_safe_session_tag(entry["sessionId"])
                           or _safe_session_tag(entry["session_id"]))
                    # the raw snake spelling is an INTERNAL read; only the
                    # camel wire key survives on the entry (it feeds
                    # ``SearchResult.session_id`` → ``to_dict()["sessionId"]``)
                    entry.pop("session_id", None)
                    if own:
                        entry["sessionId"] = own
                    else:
                        cands = [s for s in
                                 (_safe_session_tag(v) for v in
                                  (edge_sids.get(pid) or [])) if s]
                        entry["sessionId"] = min(cands) if cands else ""
            elif entity_type == "event":
                rows = graph.query(
                    "MATCH (n:Event) WHERE n.eventId IN $ids RETURN n.eventId, n.subject, n.eventKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    eid = row[0]
                    entity_data[eid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "subject":
                rows = graph.query(
                    "MATCH (n:Subject) WHERE n.id IN $ids RETURN n.id, n.name, n.subjectKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    sid = row[0]
                    entity_data[sid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
            elif entity_type == "document":
                rows = graph.query(
                    "MATCH (n:Source) WHERE n.url IN $ids "
                    "AND n.documentKind IS NOT NULL "
                    "RETURN n.id, n.title, n.documentKind, n.topics, n.summary, "
                    "n.sessionId, n.eventId, n.sourcePath",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    did = row[0]
                    entity_data[did] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                        "topics": row[3] or [],
                        "summary": row[4] or "",
                        # D3: the SAME sanitizer as the ask-path identity and
                        # the point branch — this branch is reachable from the
                        # agent-consumed MCP ``tortoise_search``
                        # (``entity_type='document'``) and ``sessionId`` is
                        # client/ingest-writable, so a structure-breaking value
                        # must not be handed back here either.
                        "sessionId": _safe_session_tag(row[5]),
                        "eventId": row[6] or "",
                        "sourcePath": row[7] or "" if len(row) > 7 else "",
                    }
            elif entity_type == "object":
                rows = graph.query(
                    "MATCH (n:Object) WHERE n.id IN $ids "
                    "RETURN n.id, n.name, n.objectKind, n.status, n.supersededBy",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    oid = row[0]
                    entity_data[oid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                        # #1350: projection-owned status + successor (Object
                        # status fold). #9974 R1: the field is a LIVENESS
                        # decoration, so an absent stored status renders live
                        # (the same `live.status_as_reported` the three other
                        # read surfaces use) rather than `""`/omitted — a
                        # consumer comparing `== "live"` must keep matching a
                        # live Object whose birth stored nothing.
                        "status": status_as_reported(row[3]),
                        "superseded_by": row[4] or "",
                    }
            elif entity_type == "operator":
                rows = graph.query(
                    "MATCH (n:Point {is_operator: true}) WHERE n.id IN $ids RETURN n.id, n.label, n.op_type",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    oid = row[0]
                    entity_data[oid] = {
                        "content": row[1] or "",  # label is searchable text
                        "kind": row[2] or "",    # op_type is kind
                    }
            elif entity_type == "source":
                rows = graph.query(
                    "MATCH (n:Source) WHERE n.url IN $ids RETURN n.url, n.title, n.sourceKind",
                    params={"ids": result_ids},
                ).result_set
                for row in rows:
                    sid = row[0]
                    entity_data[sid] = {
                        "content": row[1] or "",
                        "kind": row[2] or "",
                    }
        except Exception:
            _logger.warning("Batch content fetch failed — returning results with minimal metadata")
            for pid in result_ids:
                entity_data[pid] = {"content": "", "kind": ""}

        # 7.5. Fetch relationships for result Points (Point only) — bounded,
        #      state-centric decoration (#1353 D2/D3/D4; D12 keeps the shared
        #      unbounded get_relationships for topic_summarization).
        #      #6971: ``point_relationships`` is a ``RelationshipDecoration``
        #      whose ``decoration_error`` is set when the query body raised. Read
        #      it BEFORE building hits: the all-empty seed is then LABELLED below
        #      rather than read as "no relationships". ``getattr`` so a plain
        #      dict from a stand-in still degrades to "no failure recorded".
        point_relationships = get_relationships_bounded(graph, result_ids) if entity_type == "point" else {}
        decoration_failed = bool(getattr(point_relationships, "decoration_error", ""))
        relationships_unavailable = (
            f"{RELATIONSHIPS_UNAVAILABLE}; cause: "
            f"{getattr(point_relationships, 'decoration_error', '')}"
            if decoration_failed else ""
        )
        # 7.6. Promoted epistemic state (status/superseded_by/supersedes/subject) — #1353 D8/D10
        #      #8487: reuse the read step 5e already made ONLY when that map is
        #      known COMPLETE (step 5e leaves ``_valid_time_state`` None when
        #      the map came back empty or short — a failed read). The page is a
        #      SUBSET of the pool it read, so a complete map IS the page's map
        #      (one batch read per query, not two); an incomplete one is not,
        #      and the page then fetches its own state, exactly as it did before
        #      #8487 — a validity read must never be able to strip a page's
        #      epistemic decoration. When THIS read also comes back empty for a
        #      non-empty page (the helper swallows the failure), the leg trace
        #      says so (``epistemic_state``, ``degraded=True``): that entry means
        #      "a read did not run", and it is the honest home for the impairment
        #      the ordering leg must not claim (#8487 review round 5, P3).
        if entity_type == "point" and _valid_time_state is not None:
            point_state = _valid_time_state
        elif entity_type == "point":
            point_state = fetch_point_epistemic_state(graph, result_ids)
            # #8487 review round 5 (P3): the impairment step 5e no longer
            # claims lives HERE. ``fetch_point_epistemic_state`` swallows its
            # failure and returns ``{}``, and every id on this page came from a
            # Point match — so an empty map for a non-empty page means the
            # decoration read did not run. The silence predates #8487, but 5e
            # used to be its accidental witness; with the ordering leg
            # honestly reporting ``degraded=False`` (an ordering impairment is
            # not a degradation), the honest home for "the decoration did not
            # run" is this read, where ``degraded`` means exactly that.
            if (status_trace is not None and result_ids
                    and not any(pid in point_state for pid in result_ids)):
                status_trace.append(_trace_entry(
                    "epistemic_state", ran=False, degraded=True,
                    reason="state_read_failed", count=0))
        else:
            point_state = {}

        # 8. Build SearchResult objects, filter, and order
        results = []
        for pid in result_ids:
            pt = entity_data.get(pid)
            if not pt:
                continue
            content, pt_kind = pt["content"], pt["kind"]
            ep = ep_breakdowns.get(pid) if entity_type == "point" else None
            # #125 capture metadata (document entity_type)
            cap_topics = pt.get("topics", [])
            cap_summary = pt.get("summary", "")
            cap_session = pt.get("sessionId", "")
            cap_event = pt.get("eventId", "")
            cap_source_path = pt.get("sourcePath", "")  # #167

            # Apply min_confidence filter (Point only; non-Point always pass)
            # #11702: an ABSENT belief has no number to clear a numeric floor,
            # so it cannot satisfy one — but a floor of 0.0 is the shipped
            # "filter off" default and must keep admitting it. This used to
            # compare the fabricated neutral 0.5 against the floor, so an
            # unmeasured claim silently passed every cut-off below 0.5 as if it
            # had neutral evidence.
            if (entity_type == "point" and ep and min_confidence > 0.0
                    and (ep.confidence_mean is None
                         or ep.confidence_mean < min_confidence)):
                continue

            # Build scores
            scores = SearchScores(rrf=fused.get(pid, 0.0))
            if "fts" in raw_results:
                for fid, fscore in raw_results["fts"]:
                    if fid == pid:
                        scores.fts = fscore
                        break
            if "vector" in raw_results:
                for vid, vscore in raw_results["vector"]:
                    if vid == pid:
                        scores.vector = vscore
                        break
            if "structural" in raw_results:
                for sid, sscore in raw_results["structural"]:
                    if sid == pid:
                        scores.structural = sscore
                        break

            result = SearchResult(
                id=pid,
                content=content,
                point_kind=pt_kind,
                scores=scores,
                match_source=match_source,
                ep=ep,
                relationships=point_relationships.get(pid, []),
                # #6971: the emptiness of ``relationships`` above is a FAILED
                # read, not a sparse point — say so on every hit (the failure
                # is a property of the batch decoration).
                relationships_unavailable=relationships_unavailable,
                status=point_state.get(pid, {}).get("status", "")
                or pt.get("status", ""),
                superseded_by=point_state.get(pid, {}).get("superseded_by")
                or pt.get("superseded_by") or None,
                supersedes=point_state.get(pid, {}).get("supersedes") or [],
                # #2294: the legacy ``outdated=true`` half of the terminal
                # vocabulary. ``invalidate_point`` writes this flag WITHOUT
                # touching ``status``, so a status-only reader cannot see the
                # point is dead — carried here so every reader (the ask lane's
                # decoration gate included) can apply the shared
                # ``live.is_terminal_status``. Additive in ``to_dict``.
                outdated=bool(point_state.get(pid, {}).get("outdated")),
                # E6 (#1538) D7: promoted window fields (additive — empty
                # string when undated ⇒ no marker, byte-identical output).
                # #9741 review P3-2: PRESENT, not truthy — ``x or pt.get(k) or
                # ""`` collapsed a stored FALSY bound into "no bound".
                # ``validFrom=0`` is a legal epoch instant (the write boundary
                # orders it, ``tests/test_validity_windows.py`` pins it), and it
                # arrived here as ``""``, so the hit carried NO start at all
                # while ``valid_from_precision: "day"`` rode beside it — the
                # renderer said "no window recorded" next to a precision that
                # names one. That is the drift #9730's residual test was written
                # to detect, and the renderer half alone cannot close it.
                # ``_stored_bound`` falls through on ABSENCE (``None``) only.
                valid_from=_stored_bound(
                    point_state.get(pid, {}).get("valid_from"),
                    pt.get("validFrom")),
                valid_to=_stored_bound(
                    point_state.get(pid, {}).get("valid_to"),
                    pt.get("validTo")),
                expired_at=_stored_bound(
                    point_state.get(pid, {}).get("expired_at"),
                    pt.get("expiredAt")),
                # #2350 (E-prec): the precision block rides the SAME decoration
                # as the window fields above. ⚠️ ``end_state``/precision are
                # read from ``point_state`` ONLY — when the batch decoration
                # query fails or skips the id, ``point_state`` has no entry and
                # the whole window block (fields + precision) is absent on this
                # surface, not relabelled ``open`` (#9201 review P3: the old
                # "falls back to the raw props" comment described a fallback
                # that cannot fire — ``pt`` carries no window column).
                # #9201: each end's presence rides along — a present-but-
                # unorderable bound yields precision ``None``, so the block's
                # presence needs its own datum (see ``SearchResult.to_dict``).
                # #9244: NO ``or pt.get("validFromPrecision")`` fallback here.
                # The arm was ALREADY dead (``pt`` carries no window column, per
                # the note above — so it is not how #9244 was ever reachable;
                # that was ``effective_precision``'s early return). It is
                # removed rather than kept as a second resolution path: the
                # moment ``pt`` widened, a raw declared token could re-enter the
                # wire behind the ONE resolution home's back, which is the whole
                # defect class #9244 closes. What stays here is the resolved
                # ``point_state`` value and nothing else.
                valid_from_present=bool(
                    point_state.get(pid, {}).get("valid_from_present")),
                valid_to_present=bool(
                    point_state.get(pid, {}).get("valid_to_present")),
                valid_from_precision=(
                    point_state.get(pid, {}).get("valid_from_precision") or ""),
                valid_to_precision=(
                    point_state.get(pid, {}).get("valid_to_precision") or ""),
                end_state=(
                    point_state.get(pid, {}).get("end_state")
                    or _resolve_end_state(
                        point_state.get(pid, {}).get("valid_to")
                        or pt.get("validTo"),
                        point_state.get(pid, {}).get("valid_to_precision"))),
                subject=point_state.get(pid, {}).get("subject"),
                topics=cap_topics,
                summary=cap_summary,
                session_id=cap_session,
                event_id=cap_event,
                source_path=cap_source_path,
                # A5 (#2070): stored evidence mark (additive in to_dict).
                has_answer=pt.get("has_answer", False),
                # #8658: the Point's verbatim provenance when the fetch found
                # it — absent keys ⇒ the dataclass defaults ⇒ ``to_dict`` emits
                # nothing, so an unprovenanced hit is byte-identical.
                quote=pt.get("quote", ""),
                source_turn_id=pt.get("source_turn_id"),
                span_start=pt.get("span_start"),
                span_end=pt.get("span_end"),
                # Provenance (#3837): present only when the flag fetched it;
                # SearchResult.to_dict emits the block only when set.
                source_ref=pt.get("source_ref"),
                captured_at=pt.get("captured_at", ""),
                # #5581: the §4.6 Point-level verdict and the disclosure's
                # material — present only when the flag fetched them, so
                # ``to_dict``'s provenance block stays absent by default.
                currency=pt.get("currency", ""),
                outdated_sources=pt.get("outdated_sources") or [],
            )
            results.append(result)

        # #4889: make a structurally-empty Subject layer loud instead of
        # letting an absent ``subject`` key read as "these points have no
        # subject". One probe, and ONLY when the whole batch resolved no
        # subject — a mixed batch needs no marker (the field is working).
        # Fail-open by construction: ``subject_binding_available`` returns
        # True on any probe error, so a broken probe never fabricates an
        # unavailability claim. This sits in the advertising surface only;
        # ``fetch_point_epistemic_state`` itself stays single-query, so the
        # shared assembly's query budget is untouched.
        if (entity_type == "point" and results
                and not any(r.subject for r in results)
                and not subject_binding_available(graph)):
            for r in results:
                r.subject_unavailable = SUBJECT_BINDING_UNAVAILABLE

        # 9. Order results
        # #10893 review F1: the ordering pass is a READ of its own on the graph
        # branch (`GraphRanker.rerank` -> its own `_fetch_signals`, plus W4
        # enrichment), so it gets its own stage. Without it those round trips
        # were charged to `ep_annotation` AFTER that stage had returned — a
        # confident lie, which is the one thing this instrument must not do.
        _mark_read_stage(STAGE_ORDERING)
        if order_by == "graph":
            # #25: graph-informed rerank — weighted fusion of similarity +
            # persisted EP confidence + operator connectivity + recency decay.
            # Requires the caller to allow a large enough candidate pool (limit
            # here is the pool size; final length is capped below).
            from .ranking import GraphRanker
            ranker = graph_ranker or GraphRanker(proj)
            dicts = [r.to_dict() for r in results]
            # W4-b (#2102): thread the query for the contested-relevance boost
            # ONLY when the caller opted into W4 enrichment — w4_enrich=False
            # suppresses the why-layer keys AND the boost together (order and
            # keys must never disagree).
            ranked = ranker.rerank(
                dicts, entity_type=entity_type,
                query=(query if w4_enrich else None))[:limit]
            # W4 (#2101): additive why-layer enrichment (flag-gated) — search
            # surface; recall_state's pool and the ask lane inherit it through
            # their own calls. Zero-LLM, bounded reads, fail-open (items
            # unchanged on any assembly error).
            if entity_type == "point" and w4_enrich \
                    and w4_enrichment_enabled():
                ranked = w4_enrich_items(proj, ranked)
            if _claim_class_applies and _claim_classes_ok:
                ranked = _with_claim_class(ranked, claim_classes)
            from .ranking import annotate_belief_order as _belief_label
            from .ranking import belief_read_for as _belief_read
            # #10137 review rounds 3-4: the label describes the READ, not the
            # branch name and not the ranker's configuration, and it is scoped
            # PER ROW — a row the read did not answer was scored from a default
            # and makes no claim. A duck-typed `graph_ranker` whose read we
            # cannot observe makes no claim either (None ⇒ key absent).
            _consulted, _answered = _belief_read(ranker, ranked)
            # #10893 review F1: this branch RETURNS here, so without a mark of
            # its own it would never reach the terminal stage and a breach in
            # response assembly would be published as `ordering` — blaming the
            # rerank for time the answer took to be written out.
            _mark_read_stage(STAGE_RESPONSE)
            return _with_read_status(_belief_label(
                ranked, consulted=_consulted, answered=_answered))
        belief_consulted = False
        read = None
        answered: frozenset | None = None
        if order_by == "confidence":
            # #25/#2206/#2286: sort by the PERSISTED EP belief mean α/(α+β)
            # that ep.confidence_mean carries. GraphRanker reads the
            # n.confidence flush-mirror when present and falls back to the
            # SAME coalesce of posterior_alpha/ep_alpha that annotate_ep_batch
            # reads — so the sort key and the result-window annotation can
            # never disagree (notably for prior-only claims: an auto-baselined
            # decide part with no EP flush has no n.confidence but a persisted
            # 0.75 prior — the pre-fix read ranked it as unmeasured 0.5).
            from .ranking import GraphRanker
            ranker = graph_ranker or GraphRanker(proj)
            read = ranker._fetch_signals([r.id for r in results], entity_type)
            signals = read or {}

            def _confidence_key(r):
                """#11702: a row with NO belief has no position on this axis.

                `order_by="confidence"` sorts on a number; an absent claim has
                none, so ordering it at the old 0.5 default put it in the
                middle of a belief ranking it was never measured for. It is
                placed AFTER every row that carries a number (tier 1), keeping
                its RRF position among the other unmeasured rows — the sort
                stays total and deterministic, and no fabricated key is used.
                """
                conf = (signals.get(r.id) or {}).get("confidence")
                if conf is None:
                    return (1, 0.0)
                return (0, -float(conf))

            results.sort(key=_confidence_key)
            # #10137 review P1 / round 7 P1: the label describes the READ and
            # the read must have supplied a MEASUREMENT — a failed fetch answers
            # None (every key stays at the neutral 0.5, leaving the list in RRF
            # order), and a row with no persisted belief at all — no α/β AND no
            # `n.confidence` mirror — answered 0.5 too. Neither is a consulted
            # order. A mirror-only row IS a measurement on this read (it is the
            # coalesce's FIRST choice) and is labelled `consulted`; see
            # test_the_mirror_counts_only_where_the_read_consults_it.
            answered = frozenset(
                pid for pid, sig in (read or {}).items() if sig.get("has_prior"))
            belief_consulted = bool(answered)
        # Default: RRF relevance order (already in fused order) — with one
        # exception, and it is not cosmetic: when the #12297 rerank is ON (opt-in)
        # the ORDER below is the reranker's, while each hit's ``scores.rrf`` is
        # still ``fused.get(pid)``, the PRE-rerank fused score (set above, before
        # the rerank reassigns ``result_ids``). The cross-encoder produces a
        # RANKING, not a value on the RRF scale, so it cannot be folded in; what
        # would be dishonest is leaving the divergence unstated, because a caller
        # that sorts by ``scores.rrf`` then gets a different order than the one
        # returned. The trace's ``rerank`` block records that the order changed;
        # ``scores.rrf`` deliberately keeps the fused value it always meant.

        # #10137: `order_by="confidence"` above sorts on the belief mean — so
        # it DID consult the layer, unless its signal read failed (then every
        # key is the neutral 0.5 and the list is left in RRF order, which
        # `belief_consulted` records). The relevance order below never reads
        # it. The hits carry an `ep` block either way, so the distinction has
        # to be stated — see `annotate_belief_order`.
        out = [r.to_dict() for r in results[:limit]]
        # W4 (#2101): additive why-layer enrichment (flag-gated) — see the
        # order_by=graph branch above for the contract.
        if entity_type == "point" and w4_enrich \
                and w4_enrichment_enabled():
            out = w4_enrich_items(proj, out)
        if _claim_class_applies and _claim_classes_ok:
            out = _with_claim_class(out, claim_classes)
        from .ranking import annotate_belief_order as _belief_label
        # `answered` is only meaningful on the confidence path, which is the
        # only one that reads (`read` stays None on the relevance path).
        # #10893: TERMINAL stage. A breach that lands here says "the read is
        # DONE and the answer is late" — the one attribution that points at
        # response assembly or a blocked loop rather than at any retrieval step.
        _mark_read_stage(STAGE_RESPONSE)
        return _with_read_status(_belief_label(
            out, consulted=belief_consulted,
            answered=answered if order_by == "confidence" else None))
