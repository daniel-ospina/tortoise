"""Dreaming — background EP stabilization for the Tortoise graph (#85).

Two-tier architecture (fast path + slow path):
  Fast path:  per-query impact-subgraph EP via compute_confidence(anchors=...)
  Slow path:  dreaming — whole-graph/incremental EP stabilization

The Dreamer reuses the landed fast-path selector (_bfs_select_operators) and
the batch-I/O EP engine (TortoiseEP). It is the Tortoise analogue of memory
consolidation / sleep consolidation: not answering a query, keeping the graph
honest after batch writes.

Design (Hybrid C, #85):
- ``dream(anchors, hops)`` — incremental: BFS from anchor points, run EP on
  the affected subgraph, write back confidences.
- ``dream_all()`` — full-graph stabilization from all non-operator points.
- ``dream_window(budget)`` (epic 903-C3, #1241) — stale-first scheduler:
  each pass refreshes the stalest window (staleness-ranked top-N ∪
  retained-dirty-roots), per-pass operator budget bounded by the existing
  200-op selector cap, operator-set dedup across windows (fixes the
  dream_all double-BFS), atomic UNWIND freshness write-back (#1240).
- Async decoupling: fast-path queries never block on dreaming (dreaming is
  triggered post-batch, lazy-on-read, or on-demand via SDK/MCP).

Concurrency contract (#85):
- Embedded mode: dreaming runs synchronously in-band after write batches
  (no daemon thread — avoids SQLite/redislite single-writer hazards, #6761,
  and the #176 process leak). Latency budget: ≤500ms on the dirty subgraph.
- Hosted mode: per-tenant async queue in hosted_api.py; cooperative asyncio
  tasks (server-mode FalkorDB handles concurrency natively).

Calibration posture (#1157): dreaming WRITES n.confidence, so it is an EP
write surface — the SDK-level ``dream()`` gate (require_calibration) refuses
uncalibrated runs before any EP work (CalibrationError), same posture as
compute_confidence. This class is internal; the gate lives on the public
``TortoiseSDK.dream`` surface so dream_all → dream chunks inherit it.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

from .live import epistemic_label_queries  # #7853: id-anchored label sweep

_log = logging.getLogger(__name__)

# Default EP subgraph expansion for incremental dreaming. _mark_dirty seeds
# 1-hop; the dream expands to max_hops for full propagation. DO NOT reduce
# below 2 without expanding _mark_dirty (contract, #85).
DEFAULT_MAX_HOPS = 2

# Default per-pass window budget for the stale-first scheduler (epic 903-C3,
# #1241; now CONFIGURABLE per #1338 — see resolve_window_budget).
#
# WHY IT STAYS 200. #1338 asked for a production-sane default (e.g. 10_000) on
# the grounds that 200 operators/pass needs 25-2,500 passes to cover the
# recorded production range (5k-500k operators, ``06-diagnostics.md``). The
# coverage arithmetic is right, but the MEASUREMENT does not support paying for
# it with a bigger window: ``dream_window`` passes the budget BOTH as the
# operator cap AND as the claim-window size, and the claim window is what
# ``_bfs_select_operators`` walks hop by hop. On a 5,001-claim synthetic graph
# (``graph-scripts/dream-scale-bench.py``) a 5,000-claim window reliably raised
# FalkorDB's ``Query timed out`` while windows <= 2,000 completed — so a 50x
# default is a per-pass RELIABILITY regression, not a free win. Raising the
# default is therefore left to the operator, who can read their own deployment's
# numbers, instead of being imposed on every deployment by this file.
#
# The coverage cycle is tunable WITHOUT a code change:
# ``TORTOISE_DREAM_WINDOW_BUDGET``. Passes per cycle =
# ceil(operators / budget): 25/2,500 at the default for 5k/500k operators,
# 5/500 at 1,000, 1/50 at 10,000. Raise it only with a measured window cost —
# the per-pass bound IS the only bound on a window pass (window passes do NOT
# consume the #329 hourly full-pass bucket; plan W7, re-evaluated at #1338),
# which is why the shipped value is the conservative one.
#
# budget=None resolves to the configured default (every pass bounded by
# construction); an explicit budget overrides it; budget=0 → no-op;
# budget >= graph → single pass (window = whole graph, degenerates to full).
DEFAULT_WINDOW_BUDGET = 200

#: Env override for the stale-first per-pass operator budget (#1338). Read on
#: EVERY resolution (never cached at import) so an operator can retune a
#: running deployment without a restart, and so tests stay order-independent.
DREAM_WINDOW_BUDGET_ENV = "TORTOISE_DREAM_WINDOW_BUDGET"


def resolve_window_budget(budget: int | None = None) -> int:
    """Effective per-pass window budget for the stale-first scheduler (#1338).

    Precedence: an explicit ``budget`` always wins (including ``0`` → the I1
    no-op contract) → ``TORTOISE_DREAM_WINDOW_BUDGET`` when set to a
    non-negative integer → :data:`DEFAULT_WINDOW_BUDGET`.

    A malformed or negative value is a WARNING and falls back to the default,
    never a silent behaviour change: ``-1`` would otherwise read as "unbounded"
    at every ``budget_used <= budget`` comparison and ``0`` as "no-op for every
    scheduled pass" — both are the opposite of what an operator typing a typo
    intended, and both fail silently. ``0`` is therefore only reachable by
    passing it EXPLICITLY.
    """
    if budget is not None:
        return budget
    raw = os.environ.get(DREAM_WINDOW_BUDGET_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_WINDOW_BUDGET
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        _log.warning(
            "#1338: %s=%r is not an integer — using the default window "
            "budget %d", DREAM_WINDOW_BUDGET_ENV, raw, DEFAULT_WINDOW_BUDGET,
        )
        return DEFAULT_WINDOW_BUDGET
    if value < 0:
        _log.warning(
            "#1338: %s=%d is negative — using the default window budget %d",
            DREAM_WINDOW_BUDGET_ENV, value, DEFAULT_WINDOW_BUDGET,
        )
        return DEFAULT_WINDOW_BUDGET
    return value


# Claim-anchor chunk size for windowed passes over large windows (mirrors
# dream_all's batch_size default) — bounds EP cache memory when the window
# degenerates to the whole graph (all-null / budget >= graph).
WINDOW_CLAIM_BATCH = 2000

# Embedded in-band latency budget (seconds). If a dream exceeds this, the
# caller falls back to lazy-read + the scheduled dream_all (#85).
EMBEDDED_LATENCY_BUDGET_S = 0.5


class Dreamer:
    """Incremental + whole-graph EP stabilization.

    Thread-safe: a lock serializes dream runs so concurrent triggers
    (post-batch, lazy-read, explicit) never overlap on the same SDK.
    """

    def __init__(self, sdk):
        self._sdk = sdk
        self._lock = threading.Lock()
        # #1163 (multi-process EP): per-pass observability for the SDK
        # adapters — _last_flush_skipped = the EP stale-run guard fired (a
        # concurrent write advanced the epoch mid-run → the pass's flush and
        # the dirty-root sweep must stand down); _last_run_ep_version = the
        # epoch snapshot the pass started from (the sweep only clears graph
        # flags stamped at or before it — never a newer process's marking).
        self._last_flush_skipped: bool = False
        self._last_run_ep_version: int = 0
        # #3139: belief-state writes performed by the last pass — the single
        # choke point is ``_ep_run_batch`` (the UNWIND confidence write-back).
        # The SDK adapters read this to refuse a success-shaped pass that
        # wrote zero belief state (the silent no-op).
        self._last_belief_write_count: int = 0
        # #6211: what the operator SELECTION of the last local pass actually
        # did — ``{truncated, operators_seen, operators_used, op_cap}`` from
        # ``_bfs_select_operators``' opt-in stats. ``_last_operator_count`` is
        # the POST-cap size, so a capped pass and a complete one were
        # indistinguishable to every caller; the SDK adapter reads this to
        # report the cap on the no-arg compute_confidence result, whose priors
        # come from this pass.
        self._last_selection_stats: dict = {}
        # #3139: the full pass's reachable-affected set. ``dream_all`` returns
        # a COUNT (``total_affected``) to keep the pinned I1 key-set, so the
        # set is stashed here for the caller's dirty-root sweep — without it
        # ``_dream_full`` swept an empty set and the backlog never cleared.
        self._last_affected_claims: set[str] = set()

    def _snapshot_ep_epoch(self) -> None:
        """Capture the graph's current EP epoch as this pass's world-view."""
        self._last_flush_skipped = False
        self._last_belief_write_count = 0
        try:
            self._last_run_ep_version = self._sdk._ep_epoch()
        except Exception:  # noqa: BLE001, RUF100
            self._last_run_ep_version = 0

    # ── Incremental ────────────────────────────────────────────────

    def dream(self, anchors: list[str], max_hops: int = DEFAULT_MAX_HOPS,
              direction: str = "both",
              stamp_dreamed_at: bool = True,
              trivial_stamp: bool = True,
              op_cap: int = 200,
              warm_start: bool = True,
              before_run: Callable[[list[str]], None] | None = None) -> dict:
        """Run EP over the subgraph reachable from ``anchors``.

        Reuses the fast-path selector (analyze._bfs_select_operators, capped
        at 200 operators) and TortoiseEP.run with batch I/O. Returns
        {iterations, converged, affected_claims}.

        ``before_run`` (#7739): called with the run's SEED IDS — the operators
        the selector chose plus the direct-edge factor endpoints — AFTER the
        selection and BEFORE ``ep.run``/any write (it fires even when the
        selection is EMPTY, so the caller can still gate the anchors it was
        asked to dream). The SDK uses it to evaluate the calibration gate
        against exactly what the pass is about to consume: ``ep.run``
        re-expands ``max_hops`` from those seeds, so a gate derived from the
        ANCHORS' own closure is narrower than the run
        (measured: one extra hop at ``max_hops=3`` on a chain) and would let an
        uncalibrated point inside the consumed set run ungated. Passing the
        seeds through this hook keeps one selection and one derivation, which
        is why the gate cannot drift from the pass.

        Epic 903-C2 (#1240) — freshness write-back:
        - The per-claim confidence write-back loop is replaced by a SINGLE
          UNWIND batch that SETs confidence + lastDreamedAt + updatedAt for
          all affected claims together (mirrors ``_flush_cache``'s shape) —
          atomic: both-or-neither, no N+1 writes, no partial writes (confidence reads are still per-claim — unchanged from pre-fix).
        - ``lastDreamedAt`` is stamped ONLY when the run converged (a failed
          run keeps old stamps — retention semantics, W4) AND
          ``stamp_dreamed_at`` is set. The fast path (compute_confidence)
          never passes this flag and never stamps.
        - Operator-less anchors (live claims with no IMPL/NAND edges — EP can
          never cover them) get a trivial stamp in the local pass when
          ``trivial_stamp=True`` (dream_all disables it and runs its own
          graph-wide scan instead, reporting ``scanned_count``).

        #780: draft Points/operators are EXCLUDED by default (EP only runs
        over live claims); there is no include_draft escape hatch on this
        surface — call TortoiseEP.run(include_draft=True) directly for
        legacy behavior.

        #1157 calibration: this is an EP WRITE surface (persists
        n.confidence). Callers must ensure the graph is gated before an EP
        write happens: the SDK passes ``before_run`` (gate evaluated on the
        seeds this pass will run, still fail-closed before any write), and
        the whole-graph modes are gated by the SDK before the call. Do not
        invoke Dreamer directly with uncalibrated graphs and no ``before_run``.

        ``op_cap`` (epic 903-C6, #1244): the BFS operator-selection cap for
        this pass. ``budget=None`` on the SDK surface resolves to the
        existing 200-op selector default (I1); an explicit budget overrides
        it. ``_last_operator_count`` is set to the selected operator-set
        size (after cap truncation) and ``_last_selection_stats`` to the
        selector's own report (``truncated`` / ``operators_seen`` /
        ``operators_used``, #6211) — read by ``compute_confidence``'s no-arg
        fold and by nothing else. It is NOT part of the pinned I1 result
        key-set: ``budget_used`` is the separate ``_last_operator_count``,
        and the local result carries no ``truncated`` of its own.
        """
        if not anchors:
            self._last_operator_count = 0
            self._last_belief_write_count = 0
            self._last_selection_stats = {}
            return {"iterations": 0, "converged": True, "affected_claims": []}
        with self._lock:
            self._snapshot_ep_epoch()
            proj = self._sdk._get_proj()
            from .analyze import _bfs_select_operators
            stats: dict = {}
            operator_ids, factor_anchors = _bfs_select_operators(
                proj, anchors, max_hops=max_hops, rel_filter="IMPL|NAND",
                direction=direction, op_cap=op_cap,
                stats=stats,
            )
            # A9 (epic #902 §5.6): the selection set ALSO carries direct-edge
            # factor anchors — a direct-edge-only subgraph yields ZERO
            # operator ids but a non-empty direct-factor selection. ep.run
            # accepts plain-point seeds (Batch 3 runs the operator-less
            # direct edges they participate in), so the run seeds = operators
            # + the anchor endpoints; both-empty is the ONLY vacuous case.
            seed_ids = list(operator_ids)
            for (src, tgt, _t) in factor_anchors:
                seed_ids.append(src)
                seed_ids.append(tgt)
            seed_ids = list(dict.fromkeys(seed_ids))  # dedup (order is not
            # deterministic — operator_ids is a set, hash-randomized across
            # processes; harmless: EP factors are order-independent and
            # affected_claims is a set)
            # #7739: the caller's gate (the SDK calibration gate) runs here —
            # on the seeds ``ep.run`` is about to receive, after the ONE
            # selection this pass performs, and BEFORE any EP work or write.
            # Fail-closed: raising here leaves the graph untouched. It fires
            # even for an EMPTY selection: a dream asked to recompute an
            # uncalibrated anchor must still refuse (the pre-#7739 posture),
            # even though the empty-seed branch below only trivially stamps.
            if before_run is not None:
                before_run(seed_ids)
            # Only a pass that got PAST the gate publishes its selection
            # report (#7739 review, #6211's hazard in reverse): a refused pass
            # runs no EP and writes no metrics, so leaving a count/stats here
            # would describe a pass that never ran.
            self._last_operator_count = len(operator_ids)
            self._last_selection_stats = stats
            if not seed_ids:
                # Epic 903-C2 (#1240): operator-less anchors (isolated claims
                # with no operators AND no direct edges). There is no EP to
                # run — but the local pass still trivially stamps them so a
                # freshly written isolated claim gets lastDreamedAt instead of
                # staying null forever (the pre-fix seed-empty early return
                # never stamped). Draft anchors are excluded by the scan's
                # liveness filter, so draft-only dirty roots stay unstamped
                # (the zero-affected retention path, #780).
                if stamp_dreamed_at and trivial_stamp:
                    stamped = self._trivial_stamp(proj, anchors)
                    return {"iterations": 0, "converged": True,
                            "affected_claims": sorted(stamped)}
                return {"iterations": 0, "converged": True, "affected_claims": []}
            ep = self._sdk._get_ep()  # noqa: F841
            # #330: dream must honour the SDK's persistent evidence (baselines)
            # — hydrate graph-persisted baselines and pass a copy to ep.run.
            # run() is call-scoped, so the copy cannot leak into later runs.
            iterations, converged, affected = self._ep_run_batch(
                proj, seed_ids, max_hops, stamp_dreamed_at,
                warm_start=warm_start)
            stamp = stamp_dreamed_at and converged
            # Epic 903-C2 (#1240): operator-less anchors among the dirty roots
            # are trivially stamped in the same local pass (the EP write-back
            # above can never cover them — they have no factors). Gated on the
            # same convergence rule: a failed run stamps nothing.
            if stamp and trivial_stamp:
                affected |= self._trivial_stamp(proj, anchors)
            return {
                "iterations": iterations,
                "converged": converged,
                "affected_claims": sorted(affected),
            }

    def _ep_run_batch(self, proj, seed_ids: list[str], max_hops: int,
                      stamp: bool, warm_start: bool = True
                      ) -> tuple[int, bool, set[str]]:
        """Run EP over ``seed_ids`` + the atomic UNWIND write-back (#1240).

        Shared by ``dream()`` and the stale-first scheduler
        (``dream_window``) so window passes reuse the EXACT
        confidence+lastDreamedAt+updatedAt single-UNWIND write-back
        (both-or-neither, no N+1 writes). ``lastDreamedAt`` is written ONLY
        when the run converged AND ``stamp`` is set — failed runs keep old
        stamps and re-enter the staleness ranking (W4 retention). Returns
        ``(iterations, converged, affected)``.
        """
        ep = self._sdk._get_ep()
        # #330: dream must honour the SDK's persistent evidence (baselines)
        # — hydrate graph-persisted baselines and pass a copy to ep.run.
        # run() is call-scoped, so the copy cannot leak into later runs.
        self._sdk._hydrate_evidence()
        # Epic 903-C4 (#1242): dream passes WARM-START — the graph-persisted
        # edge messages are the seed and the gamma-skip censors redundant
        # factor updates (bounded-error reuse). The fast path
        # (compute_confidence) never passes warm_start (default False).
        iterations, converged = ep.run(
            seed_ids, max_hops=max_hops,
            evidence=dict(self._sdk._evidence),
            warm_start=warm_start,
        )
        # #1163 (multi-process EP): the EP stale-run guard fired (a
        # concurrent write advanced the epoch mid-run) — this run's computed
        # values are stale. Stand the confidence write-back + stamping down
        # (a stale pass must not write confidence or move freshness) and
        # surface the flag for the SDK adapters (they must NOT sweep the
        # dirty roots either — the newer write's markings stay).
        self._last_flush_skipped = bool(getattr(ep, "_flush_skipped", False))
        if self._last_flush_skipped:
            self._last_belief_write_count = 0
            return iterations, converged, set(ep._last_affected)
        # Epic 903-C4 (#1242): DE2E-6b cost metric — the censored-update
        # counter is recorded per run (surfaced by the SDK adapters for the
        # 903-C7 health surface; never gated on wall-clock).
        self._last_warm_skipped = getattr(ep, "_warm_skipped_updates", 0)
        # #395: the write-back set == the run set by construction — consume
        # ep._last_affected (stashed by run, assigned before its early
        # returns) instead of re-running the BFS (the dream.py:88 footgun).
        # Epic 903-C2 (#1240): the batch UNWIND write-back SETs confidence +
        # lastDreamedAt + updatedAt in ONE statement (both-or-neither, no
        # N+1 writes). lastDreamedAt is written only when the run converged
        # AND stamp is set (failed runs keep old stamps — retention, W4).
        affected = set(ep._last_affected)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        stamp_now = stamp and converged
        params_list = [
            {"id": cid, "c": ep.compute_confidence(cid)["mean"]}
            for cid in affected
        ]
        # #3139: record the belief-state write count for this pass (0 when
        # EP found no factors) — the silent-no-op guard's primary signal.
        self._last_belief_write_count = len(params_list)
        if params_list:
            # #7853: the run set spans all four epistemic labels, so sweep ONE
            # query per label to keep the id index-anchored (a label predicate
            # over the UNWIND drops the per-(label, property) anchor —
            # ``tortoise/live.py``). The two SET shapes stay both-or-neither
            # (#1240); ``_journal_belief_writeback`` de-duplicates the rows a
            # multi-labelled node (``:Point:Object``) returns from >1 sweep.
            if stamp_now:
                _set = ("SET n.confidence = p.c, n.lastDreamedAt = $now, "
                        "    n.updatedAt = $now ")
            else:
                _set = "SET n.confidence = p.c, n.updatedAt = $now "
            written: list = []
            for _q in epistemic_label_queries(
                    "UNWIND $params AS p "
                    "MATCH (n:{label} {id: p.id}) " + _set +
                    "RETURN n.id"):
                written.extend(proj.g.query(
                    _q, params={"params": params_list, "now": now}).result_set)
            # #2884 D3: journal the write-back — one ConfidenceChanged per
            # committed row, with ``lastDreamedAt`` ONLY when the run
            # converged AND stamping is on (``stamp_now`` — the exact
            # condition the live SET above uses; a record claiming a stamp
            # that did not happen is a worse bug than the pre-fix loss).
            self._journal_belief_writeback(written, params_list, now, stamp_now)
        return iterations, converged, affected

    def _journal_belief_writeback(self, written, params_list: list[dict],
                                  now: str, stamp_now: bool) -> None:
        """#2884 D3: journal a committed dream confidence write-back.

        ``written`` is the ``RETURN n.id`` result of the write statement, so
        the journal set == the committed set by construction. ``lastDreamedAt``
        rides ONLY when ``stamp_now`` (converged + stamping on). Best-effort:
        the emitter is the SDK's ``_emit_event``, which no-ops without an
        event log and swallows log-write failures.
        """
        emit = getattr(self._sdk, "_emit_event", None)
        if emit is None:
            return
        by_id = {p["id"]: p["c"] for p in params_list}
        seen: set = set()
        for row in written:
            cid = row[0]
            # #7853: the per-label sweep returns a multi-labelled node once per
            # matching label — journal each committed id exactly once.
            if cid not in by_id or cid in seen:
                continue
            seen.add(cid)
            if stamp_now:
                emit("ConfidenceChanged", id=cid, confidence=by_id[cid],
                     lastDreamedAt=now)
            else:
                emit("ConfidenceChanged", id=cid, confidence=by_id[cid])

    # ── Stale-first window scheduler (epic 903-C3, #1241) ────────────

    def dream_window(self, budget: int | None = None,
                     max_hops: int = DEFAULT_MAX_HOPS,
                     warm_start: bool = True) -> dict:
        """Stale-first windowed pass (epic 903-C3, #1241).

        Refreshes the stalest chunk of the graph each pass:

            window = retained-dirty-roots ∪ staleness-ranked-top-N

        where the staleness ranking (analyze._stale_first_claims) orders
        live non-operator claims by ``lastDreamedAt ASC`` — NULL ranks
        STALEST (never dreamed → first; legacy / first-deploy / crash-mid-
        pass claims drain across passes), deterministic id tie-break — and
        the dirty-roots union guarantees non-converged regions (W4, 903-C5
        retention) reselect even when they rank outside the top-N.

        Per-pass cost is bounded by ``budget`` — distinct operators
        processed after operator-set dedup never exceeds it:
        - ``budget=None`` → ``resolve_window_budget(None)`` =
          ``TORTOISE_DREAM_WINDOW_BUDGET`` (when set) else
          :data:`DEFAULT_WINDOW_BUDGET` — every pass is bounded by
          construction.
        - ``budget=0`` → no-op result (no EP, no stamps).
        - ``budget >= graph`` → single pass (window = whole graph,
          degenerates to a full pass).
        - All-null graph (nothing ever dreamed — first deploy / legacy /
          crash recovery) → window = whole graph regardless of budget (W2).

        Operator-set dedup (scope item 4 — fixes the ``dream_all``
        double-BFS: the cap-check BFS and the inner ``dream()`` BFS
        recomputed the same subgraph): each batch's operator set is selected
        by exactly ONE ``_bfs_select_operators`` call (op_cap = budget) and
        reused for the EP run — no second BFS; sets are unioned across the
        pass's batches so overlapping windows never recompute the same
        operators.

        Write-back (#1240): every batch runs through the shared atomic
        UNWIND confidence+lastDreamedAt+updatedAt write-back with stamping
        on (scheduler passes are write-triggered); stamps land ONLY when the
        batch converged (W4 — failed runs keep old stamps and re-enter the
        ranking). Operator-less window claims are trivially stamped by a
        window-scoped scan, gated on the pass converging. Dirty roots are
        NOT cleared here — the SDK adapter (I6) maps ``operators_deduped``
        → ``budget_used`` and drives the dirty-root logic from ``converged``.

        Returns ``{mode: "stale-first", batches, converged, converged_all,
        operators_deduped, budget_used, affected_claims}`` (I6 — the SDK
        adapter adds ``coverage`` for the I1 shape).

        Side channel: ``self._last_window`` is set to the pre-pass window
        (the staleness-ranked claims + retained-dirty union) on every return
        path ([] for the no-op paths) — the SDK adapter (I6→I1 mapping)
        reads it to compute stale-first coverage WITHOUT recomputing the
        window post-pass (stamps written by the pass reorder the ranking,
        so a post-pass recomputation would be wrong — the
        all-null→whole-graph case would blow up entirely).
        """
        eff_budget = resolve_window_budget(budget)
        if eff_budget <= 0:
            self._last_window = []
            return {"mode": "stale-first", "batches": 0, "converged": True,
                    "converged_all": True, "operators_deduped": 0,
                    "budget_used": 0, "affected_claims": []}
        with self._lock:
            self._snapshot_ep_epoch()
            proj = self._sdk._get_proj()
            from .analyze import (_bfs_select_operators,  # noqa: I001
                                  _stale_first_claims,
                                  _stale_first_count_stamped)
            # All-null graph (nothing ever dreamed): window = whole graph —
            # no point windowing a never-dreamed graph; one full pass drains
            # it (W2 all-null degenerates to full).
            if _stale_first_count_stamped(proj) == 0:
                ranked = _stale_first_claims(proj, limit=None)
            else:
                ranked = _stale_first_claims(proj, limit=eff_budget)
            # Window = staleness-ranked-top-N ∪ retained-dirty-roots (the
            # union guarantees non-converged regions reselect even outside
            # the top-N, W2/W4). Dirty roots sorted for deterministic
            # chunking (BFS collection is order-independent — sets — but the
            # window slice is not).
            ranked_set = set(ranked)
            window = list(ranked) + [
                d for d in sorted(self._sdk._dirty_roots)
                if d not in ranked_set
            ]
            # Epic 903-C6 (#1244): record the pre-pass window for the SDK
            # adapter (stale-first coverage = affected / window closure —
            # I1 "remaining-stale-before-pass" = the stale claims this pass
            # could reach; the closure is computed from THIS pre-pass list
            # because stamps written by the pass reorder the ranking).
            self._last_window = window
            if not window:
                return {"mode": "stale-first", "batches": 0,
                        "converged": True, "converged_all": True,
                        "operators_deduped": 0, "budget_used": 0,
                        "affected_claims": []}
            seen_ops: set[str] = set()
            affected: set[str] = set()
            batches = 0
            converged_all = True
            # #3139-review: aggregate the per-batch signals across the window
            # (mirrors dream_all) — a later batch with no fresh factors must
            # not erase an earlier batch's writes from the guard's view.
            total_belief_writes = 0
            any_flush_skipped = False
            for start in range(0, len(window), WINDOW_CLAIM_BATCH):
                chunk = window[start:start + WINDOW_CLAIM_BATCH]
                batches += 1
                # ONE selection per batch — the cap-check and the run share
                # it (dream_all double-BFS fix); op_cap = budget enforces the
                # per-batch operator cap deterministically (sorted-by-id
                # truncation).
                ops, factor_anchors = _bfs_select_operators(
                    proj, chunk, max_hops=max_hops, rel_filter="IMPL|NAND",
                    direction="both", op_cap=eff_budget,
                )
                # Operator-set dedup: union across the pass's batches — an
                # operator already selected by an earlier window is never
                # re-run (overlapping windows share operator sets). The
                # pass-level remaining guard keeps budget_used ≤ B even when
                # the window spans multiple batches; truncated operators stay
                # stale and re-enter the ranking next pass.
                new_ops = ops - seen_ops
                remaining = eff_budget - len(seen_ops)
                if remaining <= 0:
                    # Pass budget exhausted — defer the rest of the window to
                    # the next pass (claims stay stale, re-enter ranking).
                    break
                if len(new_ops) > remaining:
                    new_ops = set(sorted(new_ops)[:remaining])
                seed_ids = list(new_ops)
                for (src, tgt, _t) in factor_anchors:
                    seed_ids.append(src)
                    seed_ids.append(tgt)
                seed_ids = list(dict.fromkeys(seed_ids))
                if not seed_ids:
                    # Chunk fully covered by an earlier window's operator set
                    # (or draft-only): nothing new to run.
                    continue
                seen_ops |= new_ops
                _iterations, converged, aff = self._ep_run_batch(
                    proj, seed_ids, max_hops, stamp=True,
                    warm_start=warm_start)
                affected |= aff
                total_belief_writes += getattr(
                    self, "_last_belief_write_count", 0) or 0
                any_flush_skipped = (
                    any_flush_skipped or self._last_flush_skipped)
                converged_all = converged_all and converged
            # #3139-review: surface the pass-total signals for the SDK
            # adapter's guard (the per-batch values are overwritten above).
            self._last_belief_write_count = total_belief_writes
            self._last_flush_skipped = any_flush_skipped
            # Operator-less window claims: trivial-stamp (window-scoped scan,
            # independent of the EP flush), gated on the pass converging — a
            # failed pass stamps nothing (W4 retention).
            if converged_all:
                affected |= self._trivial_stamp(proj, window)
            return {
                "mode": "stale-first",
                "batches": batches,
                "converged": converged_all,
                "converged_all": converged_all,
                "operators_deduped": len(seen_ops),
                "budget_used": len(seen_ops),
                "affected_claims": sorted(affected),
            }

    def _trivial_stamp(self, proj, claim_ids: list[str] | None = None) -> set[str]:
        """Trivial-stamp ``lastDreamedAt`` for operator-less claims (epic 903-C2).

        Operator-less = live non-operator Points with NO IMPL|NAND edges — EP
        can never cover them (``run()`` early-returns when it has no factors),
        so this dedicated scan stamps them directly, independent of the EP
        flush. ``claim_ids=None`` scans the whole graph (full passes); a list
        restricts the scan to the given anchors (local passes). Returns the
        stamped claim ids — the caller UNIONS them into the pass's affected
        set, which is what clears an operator-less backlog after a SUCCESSFUL
        pass (``_sweep_dirty_roots``).

        #3139 (review P1): deliberately does NOT clear ``ep_dirty`` itself.
        This runs BEFORE ``_guard_dream_progress``, so on a starved pass it
        would erase the durable graph backlog and then raise — the failed pass
        erasing its own backlog is precisely what the guard's docstring
        promises cannot happen, and a fresh SDK (/v1/dream/health constructs
        one per request) would then report ``alarm_verdict: False, 'ok'`` on a
        silent no-op: the #3139 symptom, reintroduced. It would also bypass the
        sweep's #1163 epoch guard and leave ``ep_dirty_at`` stale.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        if claim_ids is None:
            rows = proj.g.query(
                "MATCH (n:Point) "
                "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                "AND (n.status IS NULL OR n.status <> 'draft') "
                "AND NOT (n)-[:IMPL|NAND]-() "
                "SET n.lastDreamedAt = $now, n.updatedAt = $now "
                "RETURN n.id",
                params={"now": now},
            ).result_set
        else:
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "AND (n.is_operator IS NULL OR n.is_operator = false) "
                "AND (n.status IS NULL OR n.status <> 'draft') "
                "AND NOT (n)-[:IMPL|NAND]-() "
                "SET n.lastDreamedAt = $now, n.updatedAt = $now "
                "RETURN n.id",
                params={"ids": list(claim_ids), "now": now},
            ).result_set
        stamped = {r[0] for r in rows}
        # #2884 D3: journal the stamp for exactly the rows the statement
        # updated (the RETURN already names them) — a trivial stamp is a
        # committed lastDreamedAt write. The caller gates this method on the
        # pass converging, so the journal mirrors the live write condition.
        emit = getattr(self._sdk, "_emit_event", None)
        if emit is not None:
            for cid in stamped:
                emit("ConfidenceChanged", id=cid, lastDreamedAt=now)
        return stamped

    # ── Whole-graph ────────────────────────────────────────────────

    def dream_all(self, max_hops: int = 2,
                  batch_size: int = 2000,
                  max_total_operators: int = 200_000,
                  stamp_dreamed_at: bool = True,
                  budget: int | None = None,
                  warm_start: bool = True) -> dict:
        """Full-graph EP stabilization from all non-operator Points.

        Memory + DoS guard (#85, security P1): batches the anchor set so the
        EP cache never loads the entire graph at once, AND caps the total
        operators processed across batches so a single request cannot trigger
        an unbounded whole-graph EP on the hosted deployment.

        Epic 903-C2 (#1240): the full pass stamps every reachable claim via
        the atomic UNWIND write-back AND trivially stamps operator-less
        claims via a dedicated graph-wide scan (the per-chunk dream() calls
        disable their own trivial-stamp so operator-less claims are reported
        separately, per DE2E-1: ``total_affected`` = reachable only;
        ``scanned_count`` = operator-less stamped). The scan is gated on
        ``stamp_dreamed_at`` AND ``converged_all`` — a full pass that failed
        to converge anywhere is a failed run and stamps nothing (W4
        retention: failed runs do not rank fresh).

        Returns {batches, total_affected, converged_all, scanned_count,
        budget_used, truncated}.

        ``budget`` (epic 903-C6, #1244): an explicit operator cap for the
        pass. ``budget=None`` (default) keeps the existing ``
        max_total_operators`` DoS guard (warn + truncate, never raise). An
        explicit budget makes the pass fail loudly instead of truncating —
        a full pass is contractually complete-in-one-pass (J3), so when the
        graph requires more operators than the budget, ``
        BudgetExceededError`` is raised at the first chunk that would
        exceed it (earlier chunks' writes stay committed — the raise site
        surfaces the budget exhaustion; hosted wiring in 903-C8 maps it to
        the 429 quota response). ``budget_used`` = operators selected
        across the pass's chunks (accumulated pre-truncation).

        ``truncated`` (#1338) — DEFINED behaviour at the top of the
        production range. ``max_total_operators`` (200_000) is below the
        recorded top of the production range (500k operators,
        ``06-diagnostics.md``), so a default-budget ``dream_all`` on such a
        graph CANNOT complete. The pass then:

        - stops at the first chunk that would push ``total_operators`` past
          the cap (earlier chunks' EP writes stay committed),
        - returns ``truncated=True``,
        - reports ``converged_all=False`` — a truncated pass is NOT a
          completed one, and reporting success here is the silent-partial
          bug this flag exists to remove: the caller's dirty-root sweep is
          gated on ``converged_all``, so a partial pass must not clear the
          backlog it did not cover,
        - SKIPS the graph-wide operator-less trivial-stamp scan (that scan
          is gated on a complete converged pass; running it on a partial
          pass would stamp freshness onto regions the pass never looked at,
          poisoning the staleness ranking the production scheduler reads),
        - logs at WARNING with the required count and the remedy.

        Full mode is therefore documented as NON-VIABLE above
        ``max_total_operators``: use ``mode="stale-first"`` (the recorded
        production strategy — bounded per pass, ``TORTOISE_DREAM_WINDOW_BUDGET``
        operators each) or raise ``max_total_operators`` explicitly for an
        intentional one-shot full pass. The cap is deliberately NOT raised by
        default: for a direct SDK / self-host call it is the ONLY bound on a
        whole-graph EP, and weakening it silently would trade a data-integrity
        bug for a DoS one.
        """
        from .exceptions import BudgetExceededError
        self._snapshot_ep_epoch()
        # #3139-review: this is the PASS-START epoch. Each chunk's dream()
        # re-snapshots (dream.py), so the caller's dirty-root sweep must use
        # THIS value, not the last chunk's — a concurrent write landing
        # mid-pass (E1 in (E0, E_last)) must survive the sweep (#1163).
        pass_ep_version = self._last_run_ep_version
        any_flush_skipped = False
        proj = self._sdk._get_proj()
        rows = proj.g.query(
            # #3139/#3154: index-independent non-operator predicate. A bare
            # `n.is_operator = false` is served by the boolean range index,
            # and GRAPH.COPY drops the `false` entries from that index — so
            # on a restored/imported graph the selector returns ZERO anchors
            # while 1000+ claims are dirty, and this pass reported
            # `converged_all: True` over an empty scan (the silent no-op).
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id"
        ).result_set
        anchors = [r[0] for r in rows]
        if not anchors:
            self._last_belief_write_count = 0
            self._last_affected_claims = set()
            return {"batches": 0, "total_affected": 0, "converged_all": True,
                    "scanned_count": 0, "budget_used": 0,
                    "truncated": False}

        total_affected: set[str] = set()
        total_belief_writes = 0
        converged_all = True
        batches = 0
        total_operators = 0
        # #1338: the truncation flag — see the docstring. A partial pass must
        # never be reported as a completed one.
        truncated = False
        cap = budget if budget is not None else max_total_operators
        for start in range(0, len(anchors), batch_size):
            chunk = anchors[start:start + batch_size]
            with self._lock:
                from .analyze import _bfs_select_operators
                # Epic 903-C6 (#1244): when an explicit budget is set, LIFT
                # the selector's default 200-op cap (op_cap=None) so the
                # true per-chunk need is collected and the budget check
                # below can fire — the default cap would otherwise truncate
                # each chunk at 200 before total_operators > budget ever
                # triggers (silent partial full pass, P1 review #1244). The
                # BFS is still bounded by max_hops=2. budget=None keeps the
                # selector cap as the safety bound.
                chunk_ops, _chunk_anchors = _bfs_select_operators(
                    proj, chunk, max_hops=max_hops, rel_filter="IMPL|NAND",
                    direction="both",
                    op_cap=None if budget is not None else 200,
                )
            total_operators += len(chunk_ops)
            if total_operators > cap:
                if budget is not None:
                    raise BudgetExceededError(
                        f"full-mode dream budget {budget} exceeded — "
                        f"pass requires {total_operators} operators; "
                        f"raise the budget or use mode=\"stale-first\" "
                        f"for bounded windowed passes",
                        budget=budget, required=total_operators,
                    )
                _log.warning(
                    "dream_all truncated at %d operators (cap %d) — "
                    "graph larger than budget; the pass is INCOMPLETE "
                    "(converged_all=False, no freshness stamped). Use "
                    "mode='stale-first' (bounded per pass, "
                    "TORTOISE_DREAM_WINDOW_BUDGET) or raise "
                    "max_total_operators for an intentional full pass "
                    "(#1338).",
                    total_operators, max_total_operators,
                )
                truncated = True
                break
            result = self.dream(chunk, max_hops=max_hops,
                                stamp_dreamed_at=stamp_dreamed_at,
                                trivial_stamp=False,  # dedicated scan below
                                warm_start=warm_start)
            batches += 1
            total_affected.update(result.get("affected_claims", []))
            total_belief_writes += getattr(
                self, "_last_belief_write_count", 0) or 0
            any_flush_skipped = (
                any_flush_skipped or self._last_flush_skipped)
            converged_all = converged_all and result.get("converged", False)
        # #3139: surface the pass-total belief-state writes (the per-chunk
        # value is overwritten by each chunk's dream() call) and the
        # reachable-affected set for the caller's dirty-root sweep.
        self._last_belief_write_count = total_belief_writes
        self._last_affected_claims = set(total_affected)
        # #1338: a truncated pass is not a completed pass. Downstream, the
        # SDK's dirty-root sweep is gated on converged_all, so flipping it
        # here is what stops a partial pass from clearing a backlog it never
        # covered (W4 retention semantics).
        if truncated:
            converged_all = False
        # Restore the pass-start epoch + the ANY-chunk stale-run flag so the
        # caller's sweep is guarded against the whole pass, not just the last
        # chunk (#3139 review; #1163).
        self._last_run_ep_version = pass_ep_version
        self._last_flush_skipped = any_flush_skipped
        # Epic 903-C2 (#1240): dedicated graph-wide trivial-scan for
        # operator-less claims (independent of the EP flush; kept out of
        # total_affected per DE2E-1). Gated on BOTH stamp_dreamed_at AND
        # converged_all — a full pass where any chunk failed to converge is a
        # failed run and must not rank anything fresh (W4/DE2E-7 retention
        # semantics). The scan runs under the Dreamer lock like every other
        # dream-cycle write.
        scanned_count = 0
        if stamp_dreamed_at and converged_all:
            with self._lock:
                # #3139 (review P2): KEEP the stamped ids, not just their
                # count. They were scanned independently of `total_affected`
                # (per DE2E-1), so without this union the caller's sweep never
                # saw them and an operator-less claim stayed in the in-memory
                # dirty set forever — `stale_backlog` never reached zero after
                # a fully successful pass, even though `ep_dirty` was cleared
                # in the graph.
                _trivially_stamped = self._trivial_stamp(proj)
                scanned_count = len(_trivially_stamped)
                self._last_affected_claims = (
                    set(getattr(self, "_last_affected_claims", set()) or set())
                    | _trivially_stamped
                )
        return {
            "batches": batches,
            "total_affected": len(total_affected),
            "converged_all": converged_all,
            "scanned_count": scanned_count,
            "budget_used": total_operators,
            "truncated": truncated,
        }
