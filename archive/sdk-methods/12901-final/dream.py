# Archived sdk-method `dream` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: dream left the public SDK surface — privatised (body survives as `_dream`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'dream'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def dream(self, dirty_only: bool = True, full: bool = False,
              max_hops: int = 2,
              require_calibration: bool | None = None,
              stamp_dreamed_at: bool = True,
              mode: str | None = None,
              budget: int | None = None,
              warm_start: bool = True) -> dict:
        """Run EP stabilization (#85) with strategy auto-selection (epic 903).

        One call, three strategies (I1): ``local`` (write-triggered refresh
        of the dirty roots), ``stale-first`` (scheduler: staleness-ranked
        window ∪ dirty roots, bounded per-pass), ``full`` (whole-graph
        stabilization). Users never pick — the router picks, unless an
        explicit ``mode`` overrides.

        Args:
            dirty_only: sugar (default True) — DEPRECATED (epic 903-C6,
                  #1244): kept for backward compatibility, currently IGNORED
                  by the mode router (auto-select and explicit mode govern).
                  Historical semantics (dirty_only=False = suppress local
                  dreaming) are NOT preserved — with no dirty roots the
                  router may route small graphs to full. Callers wanting the
                  old suppression should pass mode="local" explicitly.
            full: sugar — whole-graph stabilization. Maps to
                  mode="full" ONLY when ``mode`` is None (I1 rule 2).
            max_hops: EP subgraph expansion (keep ≥2 — contract with
                      _mark_dirty).
            require_calibration: gate the EP run on calibration state.
                  ``mode="local"`` scopes the gate to the seeds the pass hands
                  ``ep.run`` plus the dirty roots (#7739) — an unrelated point
                  elsewhere in the graph does not refuse the drain.
                  ``full`` stays whole-graph by contract and ``stale-first``
                  keeps the whole-graph posture (its window is chosen inside
                  the pass), so those two still refuse a foreign point.
                  None (default) resolves to the shared posture,
                  TORTOISE_EP_REQUIRE_CALIBRATION (default True — fail-closed,
                  post-#344; set it to "0" to opt out). Dream WRITES
                  n.confidence, so it must never silently run uncalibrated EP
                  (#7478).
            stamp_dreamed_at: when False, the pass never writes
                  lastDreamedAt (epic 903-C2) — the read-triggered
                  lazy-consistency paths (get_confidence /
                  compute_confidence) pass False so a READ never
                  moves the freshness signal that the 903-C4
                  stale-first scheduler ranks on. Default True for
                  write-triggered/operator-initiated dreams.
            mode: explicit strategy override ∈ {"local", "stale-first",
                  "full"} — WINS over the full/dirty_only sugar (I1 rule 1);
                  unknown mode → ValueError. None → auto-select by context
                  (I1 rule 3): write context (dirty roots) → local;
                  small-graph/first-run → full; otherwise → local. The
                  scheduled path calls with mode="stale-first" explicitly
                  (903-C8 wiring) — auto never silently picks the window.
            warm_start (epic 903-C4, #1242): True (default) — dream passes
                  reuse the graph-persisted edge messages with gamma-skip
                  censoring. False → a from-scratch pass (the fast path /
                  oracle captures, e.g. F4's frozen ground truth, which must
                  be bit-reproducible without censoring).
            budget: per-pass operator cap — ALSO the claim-window size the
                  scheduler walks, which is what bounds the pass's cost.
                  None → the CONFIGURED default
                  (``TORTOISE_DREAM_WINDOW_BUDGET`` when set, else
                  ``dream.DEFAULT_WINDOW_BUDGET``); every pass is bounded by
                  construction. The shipped value is deliberately
                  conservative — #1338 measured a 5,000-claim window raising
                  the engine's query timeout on a 5k-claim graph while
                  windows <= 2,000 completed, so raising it is an operator
                  decision taken against their own deployment's numbers
                  (passes per coverage cycle = ceil(operators / budget):
                  25/2,500 at the default for 5k/500k operators). An
                  explicit budget overrides it; budget=0 → no-op result
                  (not an error — exact key-set, all zeros, no stamps).
                  An explicit budget a full pass cannot satisfy raises
                  BudgetExceededError (tortoise/exceptions.py) — full is
                  complete-in-one-pass by contract; stale-first never
                  raises (deferral is its design). A DEFAULT-budget full
                  pass whose graph exceeds the 200_000-operator DoS cap is
                  truncated and reports ``converged_all=False`` — an
                  incomplete pass, never a silent success (#1338; full mode
                  is non-viable above the cap: use mode="stale-first").

        Returns (per-mode key-sets, pinned per I1 — exact):
            local: {mode, iterations, converged, affected_claims,
                    budget_used, coverage} — converged retained for the
                    dirty-root logic (roots covered by the affected set are
                    cleared; roots outside stay dirty for a later dream —
                    except a NEUTRAL-LINK root, which no pass can ever cover
                    and is cleared instead, #7505).
            stale-first: {mode, batches, converged_all, converged,
                    affected_claims, budget_used, coverage} — converged =
                    this pass's window-level convergence (drives the
                    dirty-root logic: a failed pass clears nothing, W4);
                    budget_used = distinct operators processed after dedup.
            full: {mode, batches, total_affected, converged_all,
                    budget_used, coverage, scanned_count} — total_affected
                    = reachable claims only; scanned_count = operator-less
                    claims trivially stamped by the scan path (DE2E-1).

        Coverage semantics per mode (I1): local → affected /
        window-reachable (live non-operator claims in the anchors' BFS
        closure); stale-first → affected / remaining-stale-before-pass (the
        pre-pass window); full → total_affected / reachable non-operator
        claims (claims with ≥1 IMPL|NAND edge; operator-less claims are
        reported via scanned_count, not the denominator). Coverage is 0.0
        when the denominator is empty (no-op / zero-operator graph).
        """
        # #1157: gate BEFORE any work — dream is an EP write surface
        # (persists n.confidence); same posture as compute_confidence.
        if require_calibration is None:
            require_calibration = _ep_require_calibration_default()
        # #1163 (multi-process EP): merge graph-persisted dirty roots into
        # the in-memory set so a fresh SDK (HTTP request scope, hosted dream
        # worker) runs the same local EP the writing process would (the W1
        # write-context rule below then resolves to local). Best-effort and
        # skipped when the in-memory set already covers the state.
        #
        # #7739: hydration now runs BEFORE the gate, because the gate's scope
        # depends on the roots the resolved mode will seed (and the mode's
        # own auto-selection depends on them). Still fail-closed BEFORE any
        # EP work below.
        self._hydrate_dirty_roots()
        resolved = self._resolve_dream_mode(mode, full)
        if require_calibration and resolved != "local":
            # #7739 boundary, deliberate: ``full`` is whole-graph by contract,
            # and a ``stale-first`` window is selected INSIDE the pass
            # (Dreamer.dream_window), so deriving its scope here would be a
            # second copy of the scheduler's derivation — exactly the
            # gate/run drift #7739 removes. Both keep the pre-#7739 posture.
            #
            # ``local`` is gated INSIDE ``_dream_local`` instead: its scope is
            # the SEED SET the pass hands to ``ep.run``, known only after the
            # pass selects operators (``ep.run`` re-expands ``max_hops`` from
            # them — see ``Dreamer.dream(before_run=…)``).
            self._ensure_calibrated("dream")
        if budget is not None and budget <= 0:
            # I1: budget=0 → no-op result, not an error. No EP, no stamps.
            return self._dream_noop(resolved, max_hops)
        dreamer = self._get_dreamer()
        if resolved == "local":
            return self._dream_local(dreamer, max_hops, stamp_dreamed_at,
                                     budget, warm_start,
                                     require_calibration=require_calibration)
        if resolved == "stale-first":
            return self._dream_stale_first(dreamer, max_hops, budget,
                                           warm_start)
        return self._dream_full(dreamer, max_hops, stamp_dreamed_at, budget,
                                warm_start)
