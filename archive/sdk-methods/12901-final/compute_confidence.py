# Archived sdk-method `compute_confidence` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: compute_confidence left the public SDK surface — privatised (body survives as `_compute_confidence`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'compute_confidence'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def compute_confidence(self, factors=None, evidence=None,
                           anchors: list[str] | None = None,
                           max_hops: int = 1,
                           rel_filter: str = "IMPL|NAND",
                           direction: str = "both",
                           require_calibration: bool | None = None,
                           recency_decay: float | None = None) -> dict:
        """Compute confidence via EP belief propagation. Returns {iterations, converged, confidences}.

        FACTOR-SELECTION REPORT (#6211) — on every result THIS method builds:
        ``truncated``, ``operators_seen``, ``operators_used``.

          * ``truncated`` — True when this result was computed over a REDUCED
            topology: the BFS operator selection hit its cap (``operators_used``
            < ``operators_seen``) OR the affected-claim expansion was cut short
            by the #395 degeneration guard (the same condition that sets
            ``diagnostic: "truncated"``). False means neither bound bit — the
            selection was NOT capped. It is not a claim that every wired factor
            voted: ``skipped_factors`` is the SEPARATE, additive record of
            factors EP dropped before consuming them (#6105).
            A caller that must not rank on a partial topology reads THIS, not
            ``converged``: before #6211 a capped pass returned ``converged:
            true`` and nothing in the result said a cap had been hit, so a
            confident number computed over a truncated factor set was
            indistinguishable from a full-graph ranking.
          * ``operators_seen`` — operators the BFS selection COLLECTED. On the
            explicit-``factors`` path no selection runs at all, so it equals
            the caller's factor count (see below).
          * ``operators_used`` — operators the BFS selection RETURNED (what
            ``truncated`` is computed from). It is NOT the size of the run's
            seed set: the anchors path additionally seeds the direct-edge
            factor-anchor endpoints, which are CLAIMS, not operators, so the
            run can be seeded with ids this count excludes. And it is not the
            number of factors that voted — ``skipped_factors`` (#6105) records
            the factors EP dropped before consuming them.

        ``operators_seen > operators_used`` names the operator cap specifically
        (the default cap is 200 — the value is not re-stated here because a
        caller that owns a budget passes its own). The explicit-``factors``
        path runs NO BFS selection, so the cap cannot bind there:
        ``operators_seen == operators_used == len(factors)`` and ``truncated``
        is False. Only the early returns (nothing to run) report 0 / 0 — they
        are the paths with no factor set to count. The NO-ARG dirty-root path
        does NOT select operators itself —
        it runs ``ep.run`` over the roots — but its fail-safe ``dream`` pass
        does, over the same 200 default, and that pass's posteriors become this
        result's priors; the three fields therefore carry the DREAM's selection
        there, and ``truncated`` is true when the dream's selection was capped.
        Before #6211's review round 3 that path reported ``False`` while a
        capped 200-of-260 selection had shaped the answer.

        The report is on every RESULT this method builds. One transport sibling
        builds its own dict and is therefore NOT covered: the MCP handler's
        HTTP no-dirty-state short-circuit (``mcp_server``), which returns
        ``diagnostic: "no_dirty_state_http"`` without the three keys — a caller
        on the hosted surface must read that diagnostic first.

        DIRTY-FLAG CLEARING (#6211, mode 2) — a successful pass retires the
        flags it recomputed. `ep_dirty` is the graph's cross-process "this
        claim's belief is stale" marker; before #6211 the only path that
        retired a CONVERGED PASS's recomputed set was the dreaming adapters'
        `_sweep_dirty_roots` (the two other writers of the flag — `_mark_dirty`'s
        terminal-point clear and `_retry_cap_verdict`'s retry-cap clear — are
        lifecycle events, not "this pass recomputed it"). A writer following
        the documented "compute confidence for my claims" call (`anchors=` /
        `factors=`) therefore got posteriors and `converged: true` while its own
        claims stayed `ep_dirty` forever. The two TARGETED branches (`factors=` /
        `anchors=`) now clear the run's affected claims through
        `_clear_targeted_pass_dirty`. The NO-ARG branch is unchanged: it clears
        through the fail-safe `dream` it delegates to, so it inherits THAT
        pass's gates and nothing here.

        The targeted clear is scaled to what the pass actually did, and it is
        deliberately STRICTER than the dream adapters, which gate on
        `converged`, the #1163 stale-run guard and `_guard_dream_progress`
        (#3139 — an explicit raise on a zero-work pass, NOT a truncation gate)
        and nothing else: measured, a no-arg pass whose dream selection was
        capped at 200 of 260 operators still clears, reporting
        `truncated: true`. This path additionally refuses when the pass was
        TRUNCATED (a reduced topology's `affected` set is incomplete by
        construction, so every claim outside it is still stale) and when the
        run recomputed nothing at all — stated directly here rather than
        inherited from the Dreamer's raise. A pass that fails, is capped, is
        truncated, or did no work clears NOTHING (W4 retention, #1243): those
        roots are still stale and must be retried. A missing epoch snapshot
        refuses explicitly (the Dreamer cannot reach that state: its snapshot is
        always an int, `0` when the epoch read failed, which clears nothing).
        Nothing new is returned for this — the flag itself is the observable,
        and `ep_dirty_at` records
        when it was set.

        Two diagnostics can accompany the result, both ABSENT when they do not
        apply: ``diagnostic`` — one of ``truncated`` (#395 degeneration guard),
        ``no_factors`` / ``no_dirty_roots`` (nothing to run), ``factors_skipped``;
        and ``skipped_factors`` — one structured record per factor EP DROPPED
        before it could be consumed (#6105). ``reason`` is one of
        ``source_stripped`` (a non-bidirectional operator lost its idx-0
        source), ``degenerate`` (a draft/terminal endpoint stripped the operator
        below 2 participating inputs) or ``derived_over_asserted`` (#2351 — a
        DERIVED claim's edge into an ASSERTED one, which the class guard blocks;
        see ``tortoise/claim_class.py``). ``converged`` speaks only for the
        factors the run CONSUMED: read ``skipped_factors`` before treating a run
        as evidence that every wired operator voted.

        #780: draft Points/operators are EXCLUDED by default (EP only runs
        over live claims); there is no include_draft escape hatch on this
        surface — call TortoiseEP.run(include_draft=True) directly for
        legacy behavior.

        Args:
            factors: operator IDs (list[str]) or factor tuples. If None, BFS-
                selects from anchors or runs LOCAL EP over the dirty roots
                (#395 delta C — no-arg no longer scans the whole graph).
            evidence: optional {claim_id: (alpha, beta)} priors — merged into
                a CALL-SCOPED copy for the run (never mutated into the SDK's
                persistent evidence, #395).
            anchors: list of Point IDs for BFS subgraph selection.
            max_hops: BFS expansion depth when using anchors (default 1),
                threaded to the run so selection depth == run depth (#395
                AC8). The no-arg path always uses the exact affected closure
                (max_hops=None) over the dirty roots.
            rel_filter: edge types for BFS — "IMPL", "NAND", or "IMPL|NAND" (default).
            direction: IMPL edge traversal direction — "incoming", "outgoing", or "both" (default).
            require_calibration: fail-closed gate — raises CalibrationError
                when an uncalibrated evidence point the SELECTION can consume
                is found (anchors/factors: the run's consumed set; no-arg: the
                dirty roots and their closure, #7739) — not for an unrelated
                point elsewhere in the graph. None (default) resolves
                to the shared TORTOISE_EP_REQUIRE_CALIBRATION posture
                (default "1" → True fail-closed, post-#344). Pass False
                explicitly to opt out and run EP on topology alone (the #7478
                degenerate case — do not do this silently).
            recency_decay: optional recency decay factor (default 0.95 from TORTOISE_EP_RECENCY_DECAY).
                T0 sources exempt; lower tiers get gentle decay. 1.0 = no decay.

        Precedence: factors > anchors > no-arg local EP over the dirty roots.
        """
        proj = self._get_proj()
        ep = self._get_ep()
        # #6211: the FACTOR-SELECTION REPORT this call returns. `truncated` is
        # True when the result was computed over a reduced topology — the BFS
        # operator selection hit its cap, or the #395 affected-claim guard cut
        # the expansion short. Both are reported because both make the same
        # number untrustworthy, and a caller that must not rank on a partial
        # topology should not have to know which one happened to fire.
        selection: dict = {"operators_seen": 0, "operators_used": 0,
                           "truncated": False}

        def _report(result: dict, *, guard_truncated: bool = False) -> dict:
            """Attach the #6211 factor-selection report to a result dict."""
            result["truncated"] = bool(selection["truncated"] or guard_truncated)
            result["operators_seen"] = selection["operators_seen"]
            result["operators_used"] = selection["operators_used"]
            return result

        # Hydrate evidence from graph-persisted baselines (survives SDK restarts)
        self._hydrate_evidence()
        # Apply source-based credibility inheritance (with recency modulation #122)
        self._apply_source_inheritance(recency_decay=recency_decay)
        # #395: evidence is CALL-SCOPED — merge into a local copy so the SDK's
        # persistent _evidence is never mutated by a run (same pattern as
        # TortoiseEP.run's run_evidence).
        run_evidence = dict(self._evidence)
        if evidence:
            run_evidence.update(evidence)
        # Calibration gate (#1157: shared check — dream / get_confidence use
        # the same _ensure_calibrated helper; fail-closed by default (#344),
        # one knob TORTOISE_EP_REQUIRE_CALIBRATION).
        if require_calibration is None:
            require_calibration = _ep_require_calibration_default()
        # #7739: the gate runs AFTER the selection (below), against the SAME
        # selection the run consumes — but still fail-closed BEFORE ep.run().
        # The no-arg (dirty-roots) branch below is scoped the same way, to the
        # dirty roots and their closure.
        if factors is not None:
            operator_ids = [f if isinstance(f, str) else f[0] for f in factors]
            if not operator_ids:
                return _report({"iterations": 0, "converged": True,
                                "confidences": {}, "diagnostic": "no_factors"})
            # No BFS selection ran: the caller supplied the factor set, so the
            # 200-operator cap does not apply to it. seen == used, truncated
            # False — reported rather than omitted so the shape is uniform.
            selection["operators_seen"] = len(operator_ids)
            selection["operators_used"] = len(operator_ids)
            if require_calibration:
                self._ensure_calibrated(
                    "compute_confidence",
                    scope=lambda: self._ep_run_scope(operator_ids, max_hops))
            iterations, converged = ep.run(
                operator_ids, max_hops=max_hops, evidence=run_evidence)
        elif anchors is not None:
            # BFS subgraph selection from anchor points (A9, epic #902): the
            # selection carries the direct-edge factor anchors too — a
            # direct-edge-only subgraph yields ZERO operators but a non-empty
            # direct-factor selection; ep.run accepts plain-point seeds, so
            # the run seeds = operators + the anchor endpoints (§5.6).
            # #395 (AC8): selection depth == run depth — max_hops threads
            # through BOTH the selector and ep.run.
            operator_ids, _factor_anchors = self._select_subgraph(
                anchors, max_hops=max_hops, rel_filter=rel_filter,
                direction=direction, stats=selection)
            for (src, tgt, _t) in _factor_anchors:
                operator_ids.append(src)
                operator_ids.append(tgt)
            operator_ids = list(dict.fromkeys(operator_ids))
            if not operator_ids:
                return _report({"iterations": 0, "converged": True,
                                "confidences": {}, "diagnostic": "no_factors"})
            if require_calibration:
                # #7739: scope = the run's consumed set for the selection just
                # computed. Anchors the run consumes are already in it (they
                # are operator inputs / direct-edge factor endpoints); an
                # isolated anchor reaches no factor, so adding it would only
                # manufacture a false blocker — the gate matches the run.
                self._ensure_calibrated(
                    "compute_confidence",
                    scope=lambda: self._ep_run_scope(operator_ids, max_hops))
            iterations, converged = ep.run(
                operator_ids, max_hops=max_hops, evidence=run_evidence)
        else:
            # #7739: the no-arg path's selection is the dirty roots and their
            # closure, so the gate is scoped to exactly that — a foreign
            # uncalibrated point (another lane's) no longer freezes a caller
            # whose own run reaches nothing that is uncalibrated. Roots are
            # captured BEFORE the gate so the scope and the run below seed the
            # SAME set; they are also included in the scope explicitly because
            # a root is a point this run was asked to recompute, and
            # ``_affected_claims``'s factor-participant rule (#5566) drops a
            # root that forms no factor.
            #
            # Ordering (unchanged in effect): hydration runs first because it
            # supplies the roots; a graph with no dirty roots still returns
            # ``no_dirty_roots`` without gating — a run that consumes nothing
            # cannot be frozen by calibration. #1163 (multi-process EP): the
            # hydration is what lets a fresh request-scoped SDK run the same
            # local EP the writing process would (the HTTP no-arg acceptance).
            self._hydrate_dirty_roots()
            roots = list(self._dirty_roots)
            if not roots:
                return _report({"iterations": 0, "converged": True,
                                "confidences": {}, "diagnostic": "no_dirty_roots"})
            if require_calibration:
                self._ensure_calibrated(
                    "compute_confidence",
                    scope=lambda: set(roots)
                    | self._ep_run_scope(roots, None))
            # ── No-arg (#395 AC8 + delta C): LOCAL EP over the affected
            # subgraph — dirty roots seed a max_hops=None run over the exact
            # affected closure; NO global extract_svbp_factors() scan (AC2 —
            # the epic-903 refactor regressed this no-arg path to a
            # whole-graph extract+run; restored to the #395 local contract,
            # #1162). Clean graph → 'no_dirty_roots' (AC8).
            # Bounded fail-safe pass (dream, max_hops=2) FIRST — preserves the
            # dirty-clear/retry lifecycle (#85); then the exact local pass
            # (max_hops=None). Roots are captured BEFORE the dream so the
            # exact pass seeds the ORIGINAL dirty set (the dream clears
            # converged roots). Double-pass rationale (scoping P2): bounded-
            # then-exact is a fail-safe against dream-alone silently dropping
            # dirty roots reachable only through legacy op_type-only operators
            # (the dream selector is {is_operator:true}-only; _affected_claims
            # is op_type-aware).
            # Epic 903-C4 (#1242): the read-path fail-safe pass is
            # FROM-SCRATCH (warm_start=False) — reads never censor.
            # Epic 903-C2 (#1240): a READ never moves the freshness signal
            # that the 903-C4 stale-first scheduler ranks on.
            # #1315/#1314: propagate the caller's calibration posture — the
            # epic903 refactor re-added an unpropagated dream here (same
            # class as the #1315 fix; line 7050/7202 already propagate).
            # #6211: the Dreamer is bound BEFORE the call, so the selection
            # report read after it is provably the instance that ran THIS pass
            # (``self._dreamer`` is non-None from here on, so a second caller
            # cannot hand us a fresh, still-empty one).
            dreamer = self._get_dreamer()
            # The RESULT of this pass is deliberately not used here: the
            # deterministic pass below owns the answer, and the dream's only
            # contribution to it is its selection report, which lands on the
            # bound ``dreamer``.
            self.dream(dirty_only=True, stamp_dreamed_at=False,
                       warm_start=False,
                       require_calibration=require_calibration)
            # #6211: the fail-safe dream runs its OWN operator selection (the
            # same 200 default) and flushes its posteriors, which the exact
            # pass below loads as priors — so a capped dream shapes THIS
            # result. Folding its report in is what keeps `truncated` from
            # certifying a capped derivation as complete on the very path the
            # issue measured (the no-arg dirty-root pass). Read from the
            # DREAMER's own report (``_last_selection_stats``), not from keys
            # on the dream result: the I1 local key-set is pinned exact and
            # does not carry them (#6211 — see ``_dream_local``).
            _dream_stats = getattr(
                dreamer, "_last_selection_stats", None) or {}
            if _dream_stats.get("truncated"):
                selection["truncated"] = True
            selection["operators_seen"] = max(
                selection["operators_seen"],
                int(_dream_stats.get("operators_seen") or 0))
            selection["operators_used"] = max(
                selection["operators_used"],
                int(_dream_stats.get("operators_used") or 0))
            iterations, converged = ep.run(roots, max_hops=None,
                                           evidence=run_evidence)
            if not ep._last_affected and not ep._last_truncated:
                return _report({"iterations": iterations, "converged": converged,
                                "confidences": {}, "diagnostic": "no_factors"},
                               guard_truncated=ep._last_truncated)
        confidences = {}
        proj = self._get_proj()
        # #395 (delta C): the write-back set == the run set — consume
        # ep._last_affected (stashed by run, assigned before its early
        # returns) instead of re-running the BFS with a default depth.
        for claim_id in (ep._last_affected or set()):
            conf = ep.compute_confidence(claim_id)
            confidences[claim_id] = conf
        # Batch write-back via UNWIND (drops the per-claim SET loop; the
        # full-precision mean is the loop's only unique write — n.confidence
        # itself is also batch-written by _flush_cache).
        # #1915: a READ never moves the freshness signal — no updatedAt stamp
        # here (get_confidence passes stamp_dreamed_at=False for the same
        # reason); the dream write-back owns the write-path stamp.
        if confidences:
            params_list = [{"id": cid, "c": conf["mean"]}
                           for cid, conf in confidences.items()]
            # #7853: the run set spans all four epistemic labels, so sweep ONE
            # query per label to keep the id index-anchored (a label predicate
            # over the UNWIND drops the per-(label, property) anchor —
            # ``tortoise/live.py``). A multi-labelled node (``:Point:Object``)
            # is returned by more than one sweep, so collapse on id: the
            # journal set must equal the committed set (#395).
            written: list = []
            seen_ids: set = set()
            for _q in epistemic_label_queries(
                    "UNWIND $params AS p "
                    "MATCH (n:{label} {id: p.id}) SET n.confidence = p.c "
                    "RETURN n.id"):
                for row in proj.g.query(
                        _q, params={"params": params_list}).result_set:
                    if row[0] not in seen_ids:
                        seen_ids.add(row[0])
                        written.append(row)
            # #2884 D3: this full-precision mean is the last confidence
            # writer on the fast path (the EP flush already journaled its
            # 4-dp rounded mirror) — journal it too, or a rebuild loses the
            # exact value. ``RETURN n.id`` binds the journal to the rows the
            # statement actually committed.
            if self._event_log_path:
                for row in written:
                    cid = row[0]
                    if cid in confidences:
                        self._emit_event(
                            "ConfidenceChanged", id=cid,
                            confidence=confidences[cid]["mean"])
        result = _report({"iterations": iterations, "converged": converged,
                          "confidences": confidences},
                         guard_truncated=ep._last_truncated)
        # #395: the degeneration guard never aborts the interactive path — it
        # proceeds with the capped set and reports the diagnostic.
        if ep._last_truncated:
            result["diagnostic"] = "truncated"
        # #6105: a factor EP DROPPED is REPORTED, not just logged. `converged`
        # is a statement about the factors the run consumed — it cannot say
        # that an operator was never consumed at all, so a run whose arm was
        # discarded (the causality-map goal: a point wired in as a TARGET only,
        # left draft, every operator into it degenerate) would otherwise read
        # as healthy. The structured list is additive and lossless; the
        # single-valued ``diagnostic`` is only claimed when no earlier
        # diagnostic already owns it (``truncated`` stays truthful).
        if ep._last_skipped_factors:
            # Fresh records (and fresh nested input dicts) so a caller cannot
            # mutate the EP object's run-set stash through the response.
            result["skipped_factors"] = [
                {**s, "inputs": [dict(i) for i in s["inputs"]]}
                for s in ep._last_skipped_factors
            ]
            result.setdefault("diagnostic", "factors_skipped")
        # #6211 (mode 2): the targeted paths have persisted their posteriors and
        # their `n.confidence` write-back, so the claims they recomputed are no
        # longer stale — clear THEIR flags. Runs after the write-back on purpose:
        # a claim is recomputed when its belief is persisted, not when the factor
        # pass returns.
        #
        # Keyed on the BRANCH PREDICATE, not a hand-set flag: the same condition
        # that chose the branch chooses whether this method owns the sweep, so a
        # future branch (or a refactor that moves one) cannot silently strand the
        # flags again — the #6211 failure reproduced by omission. The no-arg case
        # is excluded because its fail-safe `dream` already swept, and sweeping a
        # second time here would restate that rule in a second place.
        if factors is not None or anchors is not None:
            self._clear_targeted_pass_dirty(ep, result)
        return result
