# Archived sdk-method `recall_state` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: recall_state left the public SDK surface — privatised (body survives as `_recall_state`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'recall_state'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def recall_state(
        self,
        query: str | None = None,
        *,
        kind: str | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        min_confidence: float = 0.0,
        relevance_exp: float = 1.0,
        confidence_exp: float = 1.0,
        centrality_weight: float = 0.10,
        object_centric: bool = True,
        state_ranker=None,
        leg_trace: list[dict] | None = None,
        # B6 (#3892): the read-path status sink — the coalesced status of the
        # composite (Points + Objects) read. Same opt-in caller-owned pattern
        # as ``leg_trace``; default None = not computed, byte-identical.
        read_status_out: dict | None = None,
    ) -> list[dict]:
        """UC1 "state" recall (epic #898 Wave A) — what is true and
        high-confidence right now.

        Retrieves Points + Objects (hybrid search), then re-ranks the merged
        pool with the multiplicative confidence gate (StateRanker):

            base  = relevance_norm^a × confidence^b
            score = base × (1 + w_c × centrality_norm)

        State semantics:
        - Excludes status in (superseded, deprecated, retracted) by default;
          ``include_superseded=True`` brings superseded/deprecated back
          (retracted stays excluded — #689 leak guard).
        - Object-centric: Objects and the Points about them are ranked
          together; an Object's confidence is the mean EP posterior of the
          Points about it (``null`` when none of them has a belief — #11702).
        - Contested claims are SURFACED, never buried: ``contested:true`` +
          ``counter_evidence`` (NANDing point id/content) attached.
        - Most important arguments (operators, by annotator_precision) and
          high-contention NANDs / mitigations attached to top results.
        - Uncalibrated points (nothing that YIELDS a mean: no persisted
          belief, a half pair, a 0/0 pair, a negative operand, or an
          unreadable operand — with no readable mirror supplying one) carry
          ``confidence: null`` + ``ep_state: "absent"`` and are ordered AFTER
          every row that has a belief (#11702). This replaces the documented
          neutral-0.5 fallback: absence is still not scored as LOW support —
          the belief term does not participate in the order at all, and the
          row says so — but it is no longer ordered AS a measured coin flip
          either. `SEARCH-ARCHITECTURE.md` §6.3 is the decision.

        #2598 — drafts render honestly. A DRAFT is EP-inert (``live._live_only``
        excludes it at every factor-extraction call site), so when EP has not
        measured the claim its ``recall_ranking`` carries ``confidence: None``
        and ``confidence_source: "uncomputed"`` plus ``draft: True`` — "no
        belief computed", never the fabricated 0.5 that used to outrank
        measured live claims below 0.5. Drafts are still RECALLED (never
        hidden), and the ranker places them after every live row via an explicit
        status tiebreaker. The ``min_confidence`` floor therefore refuses an
        uncomputed draft for any positive floor (no measured belief to meet it)
        while the default 0.0 keeps it. Live rows are unchanged.

        Returns the same SearchResult shape as ``tortoise_fts_query`` (list
        of dicts), each annotated with ``entity_type``, ``recall_ranking``
        (score breakdown), and state-context keys (``contested``,
        ``counter_evidence``, ``arguments``, ``nands``, ``mitigations``,
        ``related_objects`` / ``related_points``).

        leg_trace (#2985): PRIVATE pass-through of the R3 #1542 D4 per-leg
            trace contract into the underlying ``tortoise_fts_query``
            call(s) — the same shape ``{"leg", "ran", "degraded", "reason",
            "count"}``. It exists so an evaluation lane can PROVE the vector
            leg was actually submitted before it records a score (a lane that
            ran FTS-only measured a different, keyword-only surface).
            Append-only, observational: passing it never changes retrieval —
            default None is byte-identical behavior. When ``object_centric``
            is True both the Point and Object reads append to the list (an
            entry per query), so consumers should test which entries RAN
            rather than assume one entry per leg. #2952: combine with
            :func:`tortoise.search_engine.declared_degraded_read` (declare the
            single-leg read) or :func:`tortoise.search_engine.require_hybrid_read`
            (fail loud). See also ``retrieval_legs`` — DELETED by #4282; the
            leg breakdown it returned is not on the public surface any more.
        read_status_out (B6 #3892): the read-path status contract — when
            provided, the dict receives ``{"status": <term>}`` with ONE of the
            four recorded terms (``tortoise.status_vocabulary``, consumed by
            ``tortoise.read_status``) for the WHOLE composite read:
            ``available`` / ``empty`` / ``degraded`` / ``unconfigured``. The
            Point and Object legs each classify their own read and the two are
            coalesced so that a leg that reached the store makes the composite
            ``degraded`` (incomplete) rather than ``unconfigured`` — a store's
            configuration is one global fact, so the reached leg proves it was
            declared; ``unconfigured`` wins only when NO leg reached the
            store. A read that returned rows can therefore never report
            ``unconfigured`` alongside them. Caller-owned opt-in sink; default
            None = not computed, byte-identical behavior.
        """
        from .ranking import StateRanker
        from .read_status import (
            STATUS_AVAILABLE,
            STATUS_DEGRADED,
            STATUS_EMPTY,
            combine_read_statuses,
        )

        # #7952: the completeness marker's ONE home — the composite read
        # coalesces its legs' markers rather than minting a verdict of its own.
        from .residency import (
            RESIDENCY_FIELD,
            combine_residency,
        )

        if limit < 1 or limit > 10000:
            raise ValueError(f"limit must be 1-10000, got {limit}")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be 0.0-1.0, got {min_confidence}")
        if relevance_exp <= 0 or confidence_exp <= 0:
            raise ValueError(
                f"relevance_exp/confidence_exp must be > 0, got {relevance_exp}/{confidence_exp}")
        if not 0.0 <= centrality_weight <= 1.0:
            raise ValueError(f"centrality_weight must be 0-1, got {centrality_weight}")

        try:
            proj = self._get_proj()
        except Exception:
            if read_status_out is not None:
                # A store that will not OPEN is `degraded` (off by outage);
                # `unconfigured` (off by policy) is not reachable from this
                # SDK, which always resolves a store target. Never a raise.
                read_status_out["status"] = STATUS_DEGRADED
                return []
            raise
        ranker = state_ranker or StateRanker(
            proj,
            relevance_exp=relevance_exp,
            confidence_exp=confidence_exp,
            centrality_weight=centrality_weight,
        )

        # 1. Candidate pool (Points + Objects), hybrid retrieval per entity.
        #    State filter applied INSIDE retrieval (exclude_status is enforced
        #    before pool truncation, so live claims ranked behind superseded
        #    ones are not dropped — #898 review P1). retracted is already
        #    hard-excluded at the retrieval layer (#689).
        pool = max(limit * 3, 30)
        # retracted stays hard-excluded at the retrieval layer (#689);
        # superseded/deprecated are excluded here unless include_superseded.
        exclude_status = None if include_superseded else sorted(
            self.STATE_EXCLUDED_STATUS - {"retracted"})
        # B6 (#3892): per-leg status sinks, combined into one after both reads.
        # None when no status was requested, so the calls stay byte-identical.
        _point_status: dict | None = None
        _object_status: dict | None = None
        if read_status_out is not None:
            _point_status = {}
            _object_status = {}
        # #8487: this is a CANDIDATE pool whose order the caller RE-OWNS —
        # StateRanker.rerank sorts the whole pool by relevance×confidence×
        # centrality and only then applies ``[:limit]`` (see "1. Candidate
        # pool" below). The SDK-side validity selection's ordering would
        # therefore be discarded, leaving only its pool-composition effect,
        # which is a behaviour change nothing measures. Same reasoning as the
        # eval lane's opt-out (#8487 review; ``_valid_time_rerank`` docstring).
        point_results = self.tortoise_fts_query(
            query, kind=kind, entity_type="point", limit=pool,
            _record_reads=False,  # #7983: a CANDIDATE pool, not a caller read
            _valid_time_rerank=False,  # #8487: caller re-owns the order
            _rerank_candidates=False,  # #12297: same re-owned order
            exclude_status=exclude_status,
            include_terminal=include_superseded,
            # Sec-1 (code-review gate): the pool can reach ~30k ids — W4
            # enrichment on the PRE-rerank pool would fold + project ~30k
            # candidates that StateRanker then discards ~2/3 of. Enrich
            # POST-rerank on the final top-limit list instead (see below).
            w4_enrich=False,
            # #2985: observational only — the call is byte-identical when
            # leg_trace is None (the product default).
            leg_trace=leg_trace,
            read_status_out=_point_status)
        object_results = (
            self.tortoise_fts_query(
                query, kind=kind, entity_type="object", limit=pool,
                _record_reads=False,  # #7983: a CANDIDATE pool, not a caller read
                _valid_time_rerank=False,  # #8487: caller re-owns the order
                _rerank_candidates=False,  # #12297: same re-owned order
                include_terminal=include_superseded,
                leg_trace=leg_trace,
                read_status_out=_object_status)
            if object_centric else []
        )
        # #1350: Object status filter (decision 2a — completed/in_progress
        # stay visible; superseded/deprecated/archived/retracted excluded
        # from the state view unless include_superseded brings them back).
        objects = [dict(r, entity_type="object") for r in object_results]
        if not include_superseded:
            # #3301: ONE canonical OBJECT vocabulary — the same set
            # ``search_engine._status_vocab_for("Object")`` feeds the four
            # search legs. Never re-literal it here.
            objects = [o for o in objects
                       if (o.get("status") or "")
                       not in _OBJECT_TERMINAL_STATUSES]

        # UC1 state view: hide mitigation bookkeeping points (they are
        # surfaced ATTACHED to results as context, not standalone claims —
        # review round-2 P3).
        points = [dict(r, entity_type="point") for r in point_results
                  if not (r.get("content") or "").startswith("[MITIGATION]")]
        # #1391: retracted stays hard-excluded even when include_superseded
        # brings the other terminal statuses back (#689 leak guard) — the
        # base retrieval opt-in (include_terminal) surfaces all terminal
        # statuses, so recall_state re-filters retracted from its own pool.
        if include_superseded:
            points = [p for p in points if p.get("status") != "retracted"]

        # 3. Multiplicative-gate ranking over the merged pool.
        merged = points + objects
        ranked = ranker.rerank(merged, entity_type="point", query=query)
        # #10137 (review round 3, P1): the POOL was read at order_by="relevance"
        # and is therefore labelled "not_consulted" — but THIS ranker's score is
        # `relevance ** a × confidence ** b` over the EP belief mean, so the
        # order the caller actually receives DID consult the layer. The label is
        # owned by the LAST re-order, so it is re-stated here from the ranker's
        # own read outcome (`belief_read_for`: None ⇒ no claim).
        from .ranking import annotate_belief_order as _belief_label
        from .ranking import belief_read_for as _belief_read
        _consulted, _answered = _belief_read(ranker, ranked)
        ranked = _belief_label(ranked, consulted=_consulted, answered=_answered)

        # 4. Explicit confidence floor (orthogonal to the multiplicative gate).
        # #2598: a DRAFT whose belief EP has not measured carries
        # ``confidence: None`` ("no belief computed" — never the fabricated
        # neutral 0.5). It cannot satisfy a POSITIVE belief floor, because there
        # is no measured belief to meet it; the default 0.0 floor means "no
        # floor" and therefore keeps drafts (they stay recallable, ranked after
        # every live row by the ranker's status tiebreaker). A bare
        # ``None >= min_confidence`` would raise TypeError, so the two cases are
        # decided explicitly rather than left to comparison semantics.
        def _passes_floor(row: dict) -> bool:
            conf = row["recall_ranking"]["confidence"]
            if conf is None:
                return min_confidence <= 0.0
            return conf >= min_confidence

        ranked = [r for r in ranked if _passes_floor(r)][:limit]

        # 5. State context surfacing (batched, not N+1).
        point_ids = [r["id"] for r in ranked if r.get("entity_type") == "point"]
        object_ids = [r["id"] for r in ranked if r.get("entity_type") == "object"]

        counter_evidence = self._state_counter_evidence(point_ids)
        arguments, nands = self._state_arguments(point_ids)
        mitigations = self._state_mitigations(arguments, nands)
        related_objects = self._state_related_objects(point_ids)
        related_points = self._state_related_points(object_ids)

        out: list[dict] = []
        for r in ranked:
            rid = r["id"]
            copy = dict(r)
            if rid in counter_evidence:
                copy["counter_evidence"] = counter_evidence[rid]
            if rid in arguments:
                copy["arguments"] = arguments[rid]
            if rid in nands:
                copy["nands"] = nands[rid]
            if rid in mitigations:
                copy["mitigations"] = mitigations[rid]
            if rid in related_objects:
                copy["related_objects"] = related_objects[rid]
            if rid in related_points:
                copy["related_points"] = related_points[rid]
            # Contestation surfaced at top level (ep carries variance/contested
            # for points; mirror it here so state consumers can flag without
            # digging into ep). Never a ranking demoter.
            ep = copy.get("ep")
            contested = bool(ep.get("contested")) if isinstance(ep, dict) else False
            copy["contested"] = contested
            out.append(copy)
        # Sec-1 (code-review gate): W4 enrichment POST-rerank on the final
        # top-limit list (the pool call above passed w4_enrich=False) — the
        # additive keys ride only the results the caller actually receives.
        # Flag-gated + fail-open (unchanged on any assembly error).
        try:
            from .why import enrich_items as _w4_enrich_items
            from .why import w4_enrichment_enabled as _w4_enabled
            if _w4_enabled():
                point_items = [i for i in out if i.get("entity_type") == "point"]
                enriched = _w4_enrich_items(proj, point_items)
                by_id = {i["id"]: i for i in enriched}
                out = [by_id.get(i["id"], i) for i in out]
        except Exception as e:  # noqa: BLE001, RUF100 — fail-open
            _logger.warning("W4 enrichment failed (recall_state): %s", e)
        # #7983: the CANDIDATE pool above was not recorded (`_record_reads=False`)
        # — these are the rows the caller actually receives, and they are what a
        # read of this path means for `f`.
        _ledger_record(
            "Point", [i for i in out if i.get("entity_type") == "point"],
            kind="traversal",
        )
        # NOT the Object leg — and since #9881 that is a WIRING choice (#8877),
        # not a shape limit. Before the move an Object's key was `name` and these
        # rows carry `id`, so recording them could only emit `unresolved`
        # ("rows with no key at all", which was false); the key IS the `id` now,
        # so those rows ARE addressable and hooking this leg is #8877's
        # follow-up. It is left unhooked here deliberately — this change is the
        # key move, and turning on a new read leg is a measurement decision for
        # that issue, not a silent side effect of it.
        if read_status_out is not None:
            # B6 (#3892): the composite is derived AFTER the pipeline, from the
            # rows the caller actually RECEIVES. The filters, the rerank and
            # the `min_confidence` floor can drop every candidate the legs
            # returned, and `available` ("reached AND returned content") must
            # never describe an empty result — the same rule
            # `tortoise_fts_query` applies to its final row list.
            _composite = combine_read_statuses(
                (_point_status or {}).get("status"),
                (_object_status or {}).get("status"))
            if _composite is None:
                # Defect guard, not a state: every leg's return path writes
                # its own term (see ``_with_read_status``), so a None
                # composite means one path escaped that contract. Never emit
                # ``null`` under the four-term vocabulary — describe the read
                # the caller RECEIVES instead.
                _composite = STATUS_AVAILABLE if out else STATUS_EMPTY
            if _composite == STATUS_AVAILABLE and not out:
                _composite = STATUS_EMPTY
            read_status_out["status"] = _composite
            # #7952: the legs' completeness markers coalesce the same way the
            # statuses do — the composite read is a residency miss if ANY leg
            # fell outside the known-hot set, because the answer the caller
            # receives is then incomplete. Absent when no leg was assessed
            # (the declaration is only read under its own flag).
            _markers = [(_point_status or {}).get(RESIDENCY_FIELD),
                        (_object_status or {}).get(RESIDENCY_FIELD)]
            _marker = combine_residency(*_markers)
            if _marker is not None:
                read_status_out[RESIDENCY_FIELD] = _marker
        return out
