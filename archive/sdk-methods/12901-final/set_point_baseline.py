# Archived sdk-method `set_point_baseline` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: set_point_baseline left the public SDK surface — privatised (body survives as `_set_point_baseline`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'set_point_baseline'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def set_point_baseline(self, claim_id: str, alpha: float, beta: float, *,
                           source: str = BASELINE_SOURCE_SET_BY_AUTHOR) -> dict:
        """Set Beta prior evidence for a claim. Persists to graph immediately.

        ``source`` records the baseline's provenance (``baseline_source`` graph
        property) — the #2199 token family, ONE of:
          - "set-by-author" (default): belief stated directly by whoever filed
            the point (human OR agent — species is tracked in authoredBy).
            NEVER recomputed by ``_apply_source_inheritance``. This writer is
            the EXPLICIT belief path (#844), so it CLEARS the #398
            ``credibility`` label (#6151 review P1): an explicit baseline
            SUPERSEDES the authored word. A word-bearing baseline written by
            ``create_point(credibility=…)`` / ``mitigate_operator`` (whose
            label is stamped by ``_stamp_authored_word`` AFTER this call) is
            still recomputed so its weak assertion joins the aggregate as one
            supporter — but once the author sets the baseline HERE, the label
            is gone and the value is frozen against source recomputation.
          - "inherited-from-source": derived from Source evidence, recomputed
            per EP run subject to the per-point time gate (``n.inherited_at``).
          - "system-default": no belief was stated when filed; the system
            applied its standard starting belief (decide-part default, #2199).
            Never recomputed — same persistence class as set-by-author.

        Pre-#2199 spellings ('explicit' / 'inherited') are rejected with a
        migration pointer — run graph-scripts/2199_baseline_source_rename.py
        so old and new spellings never coexist. Set baselines are always
        distinguishable from legacy ``baseline_set=true`` rows with no source
        token (issue #398 2x2 mapping — calibrate_summary renders those as
        set-by-author legacy).

        REFUSES (nothing is written, and no prior is left in the in-memory
        evidence map — a refused write must never leave the SDK believing a
        prior the graph does not hold):
          - an unrepresentable ``alpha``/``beta`` (``_reject_unrepresentable_number``);
          - an id naming a node that cannot carry a ``:Point`` baseline — another
            epistemic label, a ``:Source``… (#9063); the error names the label
            it found;
          - an id naming NO node at all (#9262): the store statement matches
            zero rows, so reporting success would assert a prior that does not
            exist. Create the point first, or correct the id.
        NOT refused: an id the engine's LABELLED equality cannot bind while a
        ``:Point`` with it exists (an empty / whitespace-padded id). That path
        keeps its historical no-op — it returns the dict and holds the prior in
        memory only, writing nothing — because "no node with id" would be a
        false diagnosis there (#9063). That residual is #9751.
        """
        if source not in BASELINE_SOURCE_TOKENS:
            raise ValueError(
                f"Unknown baseline source {source!r}. Valid provenance tokens: "
                f"{sorted(BASELINE_SOURCE_TOKENS)}. Pre-#2199 spellings "
                "'explicit'/'inherited' were renamed — run "
                "graph-scripts/2199_baseline_source_rename.py against the graph "
                "before writing new baselines."
            )
        # #4647 (code-review P1): this writer reaches the store directly — the
        # `proj.g.query` below takes `alpha`/`beta` as raw Cypher parameters — so
        # it bypasses `_sanitize_props` entirely and must apply the
        # numeric-domain guard itself, exactly as `create_source` does. Without
        # it a caller silently corrupts the Beta prior this method exists to set:
        # `alpha=2**70` is CLAMPED to INT64 max and `alpha=Decimal('0.1')` is
        # stored as a different number, and those parameters feed EP confidence.
        # The guard runs BEFORE `self._evidence` is updated so a refused write
        # cannot leave the in-memory prior divergent from the graph. (The two
        # refusal paths BELOW land after the update — `written` is only known
        # after the store answers — so each one pops the seat it just planted,
        # #9262.)
        # #11702 cycle 21: a BOOLEAN is not a Beta count, and it slips every
        # guard below — `_reject_unrepresentable_number(True)` returns None (the
        # numeric domain does not treat a Boolean as an altered number) and
        # `True < 0` is False. Measured: `set_point_baseline(claim, True, 0.5)`
        # answered ok, stored `ep_alpha=true`, which every reader then reports
        # ABSENT (a Boolean is not a number) — while the same call set
        # `baseline_set=true` + `baseline_source="set-by-author"`, freezing the
        # claim against the source-inheritance recompute that would have given it
        # a belief. Refused here, like the cycle-20 guard on the generic door.
        for _name, _val in (("alpha", alpha), ("beta", beta)):
            if not _is_real_number(_val):
                raise ValueError(
                    f"Beta {_name} must be a number, not a "
                    f"{type(_val).__name__}: a Boolean/collection/string "
                    "operand is not a pseudo-count and cannot be read as a "
                    "belief (#11702)")
        _reject_unrepresentable_number("alpha", alpha)
        _reject_unrepresentable_number("beta", beta)
        # #11702 cycle 11: a Beta operand is a COUNT. The representability
        # guard accepts a negative float, and `(alpha + beta) > 0` passes for
        # α=-1, β=3 — which then wrote an out-of-range "mean" (-0.5) into the
        # SERVED `n.confidence` mirror (the value every read prefers) and
        # journaled it, so a rebuild reproduced it. Refuse before any write or
        # in-memory prior, exactly like the guards above.
        for _name, _val in (("alpha", alpha), ("beta", beta)):
            if _val < 0:
                raise ValueError(
                    f"Beta {_name} must be >= 0 (got {_val!r}): α and β are "
                    "pseudo-counts, and a negative operand yields a mean "
                    "outside [0, 1] and a negative variance")
        self._evidence[claim_id] = (alpha, beta)
        # Persist to graph so baselines survive SDK restarts
        proj = self._get_proj()
        # #398 (owner ruling 2026-10-12), #6151 review P1 — an EXPLICIT baseline
        # SUPERSEDES the authored word. The `credibility` label is the recompute
        # guard's ONLY exception (`_apply_source_inheritance`): a label-bearing
        # `set-by-author` baseline is recomputed so its weak authored body can
        # be displaced by a real source. That exception must NOT survive an
        # explicit write — the guard's own invariant is "a frozen
        # `set-by-author`/`system-default` baseline is label-less", and without
        # this clear an explicit write on a word-bearing point stayed
        # re-openable. MEASURED before the fix: `set_point_baseline(0.4)` + a T1
        # source recomputed to 5.1 and + an originating session to 1.1, with
        # `baseline_source` flipped to `inherited-from-source` — the author's
        # value silently overwritten in both cases.
        #
        # The clear rides the SAME statement as the baseline, so it shares the
        # baseline's journal class exactly: no new event, and `credibility`
        # joins `ep_alpha`/`baseline_*` in the documented live≠rebuild residual
        # below (this method emits no `PointAdded`/`PointRevised` at all) — the
        # gap is neither widened nor narrowed.
        #
        # `source=BASELINE_SOURCE_INHERITED` is the recompute's OWN write: it is
        # the derivation the label exists to enable, so it does NOT clear the
        # label. Clearing there would drop the word from every later recompute
        # and let the revert pass neutralise a word-bearing point whose sources
        # are removed. Only the two EXPLICIT (frozen) provenance classes
        # supersede the word.
        #
        # #6151 review P1(a): the SAME explicit write must also clear the #2199
        # `baseline_system_default` marker — the OTHER half of the same birth
        # fact. The marker is what the recompute's `+2.0` re-injection is gated
        # on, so leaving it behind re-injected the default OVER an explicit
        # baseline on the next word-bearing recompute (measured 0.4 -> 7.1). An
        # explicit baseline SUPERSEDES the provision exactly as it supersedes
        # the word: after this write the point is a frozen author belief, not a
        # born-live decide part that still carries the default.
        _set_clause = (
            "n.ep_alpha = $a, n.ep_beta = $b, n.baseline_set = true, "
            "n.baseline_source = $src, "
            # #11702: the `n.confidence` flush-mirror is the SERVED belief mean
            # (#2206) and every read/rank surface prefers it, so nulling the
            # posterior without touching it left the node reporting its OLD
            # posterior mean (0.9) beside a new prior mean (0.75) — the two
            # halves of one claim disagreeing on one response. The mirror is
            # refreshed to THIS prior's mean in the same write, so it can never
            # outlive the belief it summarises.
            "n.confidence = $c, "
            "n.posterior_alpha = null, n.posterior_beta = null"
        )
        if source != BASELINE_SOURCE_INHERITED:
            _set_clause += ", n.credibility = null, n.baseline_system_default = null"
        written = proj.g.query(
            f"MATCH (n:Point {{id: $id}}) SET {_set_clause} RETURN n.id",
            params={"id": claim_id, "a": alpha, "b": beta, "src": source,
                    # #11702: a degenerate (0,0) baseline writes NO mirror.
                    # Writing the #330 house 0.5 here would make the row
                    # PRESENT by the shared predicate (`n.confidence IS NOT
                    # NULL` wins over the zero-total pair), so one SDK call
                    # produced the exact `0.5` + belief state §6.3 forbids —
                    # and journaled it, so a rebuild reproduced it. `None`
                    # removes the property (Cypher `SET n.p = null`), leaving
                    # the row absent, which is what every read surface says.
                    # (The value comes from `belief_value` — the ONE owner of
                    # "mean of a Beta pair, or None" — so this call cannot
                    # re-derive the arithmetic the eight read surfaces use;
                    # it is also what keeps a bare `alpha / (alpha + beta)`
                    # from raising ZeroDivisionError BEFORE the write, which
                    # used to leave `self._evidence` holding a prior the graph
                    # never received.)
                    "c": belief_value(posterior_alpha=alpha,
                                      posterior_beta=beta, round_to=4)},
        ).result_set
        # #9063: a baseline the statement did NOT write must never be reported
        # as written when the caller's node EXISTS. ``set_point_baseline`` used
        # to return ``{'alpha': 3.0, 'beta': 1.0, 'source': 'set-by-author'}``
        # for a :Subject/:Object/:Event — the caller read success, the graph
        # kept ``baseline_set = NULL``, and the in-memory prior diverged from
        # the graph, so a caller could believe a node was calibratable when it
        # was not (measured: this issue's body, and the reviewer's #9057
        # sweep). Refuse loudly and name the label it found, and leave the
        # prior it already set in ``self._evidence`` NOT set: the same rule the
        # numeric-domain guard above follows (a refused write cannot leave the
        # in-memory prior divergent from the graph).
        #
        # SCOPE: this fires on a node that EXISTS and is not a :Point — an
        # epistemic entity label (the #9063 measured class) or any other
        # labelled node (a ``:Source`` addressed by its url, a ``:Tag``...),
        # since none of them can carry a :Point baseline.
        #
        # #9262 closes the remaining hole in the SAME contract: an id naming NO
        # node at all. The statement matched zero rows, yet the method returned
        # the success dict and planted the prior in ``self._evidence`` — which
        # ``_hydrate_evidence`` only reloads from ``baseline_set = true`` rows,
        # so the phantom died on restart and a run's belief state depended on
        # WHICH PROCESS set it (#1163's request-scoped SDK is that case). The
        # method's own contract is "Persists to graph immediately", so a write
        # that bound no row is refused loudly and leaves no in-memory seat.
        # ⛔ NOT universal, and stated as measured: the ONE zero-row write that is
        # still a no-op is the id-RESOLUTION case below (``_unbindable``), where
        # the probe DID find a ``:Point`` but the labelled equality cannot bind
        # its id — that call returns the dict and KEEPS the in-memory seat, which
        # is #9751's residual, deliberately outside this issue.
        if not written:
            labels: list[str] = []
            # #7853: id-anchored per-label probe — the indexes are
            # per-(label, property), so this is four index reads, not a scan.
            for _q in epistemic_label_queries(
                    "MATCH (n:{label}) WHERE n.id = $id RETURN labels(n)"):
                labels.extend(r[0][0] if r[0] else "?" for r in proj.g.query(
                    _q, params={"id": claim_id}).result_set)
            if not labels:
                # Non-epistemic label (Source/Tag/...): an id-keyed scan. This
                # runs ONLY on the failed-write path, so the success path
                # stays index-anchored; a ``:Source`` answers by its ``url``
                # only, which is why the four-label sweep above cannot see it.
                labels.extend(r[0][0] if r[0] else "?" for r in proj.g.query(
                    "MATCH (n) WHERE n.id = $id RETURN labels(n)",
                    params={"id": claim_id}).result_set)
            # …and a probe that finds the GATED label itself is NOT a label
            # mismatch: the write can miss for an id-RESOLUTION reason on a
            # hand-written id (measured: an empty / whitespace-only id, where
            # the engine's labelled equality misses and the unlabelled scan
            # hits). Claiming ":Point is not a :Point" is a false diagnosis that
            # no caller can act on, so that case keeps the historical no-op
            # behaviour of an id the write did not bind.
            #
            # ``_unbindable`` is the ONE thing that separates it from #9262's
            # case below (both leave ``labels`` empty): a node WAS found, so
            # "no node with id" would be a false diagnosis in the other
            # direction. It must be captured before the reset. Its phantom seat
            # (the call returns the dict, the graph stores nothing) is #9751.
            _unbindable = bool(labels) and labels[0] in _CALIBRATION_GATED_LABELS
            if _unbindable:
                labels = []
            if labels:
                self._evidence.pop(claim_id, None)
                raise ValueError(
                    f"set_point_baseline: {claim_id!r} is a :{labels[0]}, not a "
                    f":Point — nothing was written. Baselines are writable on "
                    f":Point only (epistemic-entity priors are the open "
                    f"belief-model decision in #9063), so the belief you "
                    f"intended was never stored."
                )
            if not _unbindable:
                # #9262: the probes found NOTHING by this id — a typo'd or
                # not-yet-created point. Refuse rather than report a prior the
                # graph does not hold, and drop the seat planted above so the
                # in-memory map stays a mirror of the graph (#9063's rule).
                self._evidence.pop(claim_id, None)
                raise ValueError(
                    f"set_point_baseline: no node with id {claim_id!r} — nothing "
                    f"was written. Baselines are persisted onto an existing "
                    f":Point only, and the prior is NOT kept in memory either "
                    f"(an unstored baseline must not be believed). Create the "
                    f"point first, or correct the id."
                )
        # #2884 D3/FIX-3: a baseline clears the posteriors LIVE (`null`), so a
        # rebuild must replay the CLEAR or it resurrects the stale posteriors
        # the live graph no longer holds. `RETURN n.id` binds the journal to
        # the rows the statement actually committed; a missing Point wrote
        # nothing and journals nothing.
        # SCOPE (honest, #2884 A4): the record carries ONLY the two posterior
        # clears. The SAME statement also writes `ep_alpha`, `ep_beta`,
        # `baseline_set` and `baseline_source`, and NO journaled event carries
        # those four — they are in `_POINT_DENY`, so the PointAdded passthrough
        # drops them, and `_REPLAY_GAP_PROPS` restores only synthetic
        # graph-only ids. An author-set baseline is therefore LOST on rebuild
        # (and `_hydrate_evidence` then finds no prior, refusing the next EP
        # run). That is a pre-existing gap this fix deliberately does not
        # widen into: journaling an EP prior is a design decision (the E3
        # pre-write writes `ep_alpha`/`ep_beta` CONDITIONALLY on the
        # pre-existing `baseline_set`, so the correct payload is the
        # statement's OUTCOME, not its input params) and is filed as a
        # residual for the controller.
        #
        # ⛔ #11702: the mirror IS journaled, because this statement now MOVES
        # it (`n.confidence = $c` above). Leaving it out made live and rebuilt
        # disagree — live `baseline`/0.75, rebuilt `absent`/None — because the
        # fold is presence-conditional and only writes the keys a record
        # carries. The payload is the value the statement itself committed, so
        # replay cannot differ from the write.
        # NOTE (#6151 review P3): `_stamp_authored_word` — the other writer in
        # this family — carries the SAME live≠rebuild gap on the
        # existing-point paths; its docstring records the mechanism, the
        # measured symptom, and why closing it is not a small change.
        if self._event_log_path:
            # #11702: the SAME value the statement committed — `None` for a
            # degenerate pair, so replay leaves the row absent exactly as the
            # live graph does (see the `c` param above). Computed by
            # `belief_value` for the same reason the `c` param is.
            _mirror = belief_value(posterior_alpha=alpha, posterior_beta=beta,
                                   round_to=4)
            for row in written:
                self._emit_event(
                    "ConfidenceChanged", id=row[0], confidence=_mirror,
                    posterior_alpha=None, posterior_beta=None)
        # Dreaming (#85, P1): a baseline change alters the prior — neighbors
        # whose confidence derived from this claim are now stale.
        self._mark_dirty([claim_id])
        # Epic 903-C4 (#1242): a prior change alters factor behavior WITHOUT
        # a topology change — warm-start seeds (edge messages) computed under
        # the OLD prior are stale. Full drop (cheap) per the plan's W3 rule.
        self._get_ep().invalidate_messages()
        return {"claim_id": claim_id, "alpha": alpha, "beta": beta, "source": source}
