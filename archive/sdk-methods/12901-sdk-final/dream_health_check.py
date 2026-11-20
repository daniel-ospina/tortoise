# Archived sdk-method `dream_health_check` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_dream_health_check`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'dream_health_check'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def dream_health_check(self) -> dict:
        """Epic 903-C7 (#1245): the zero-output silent-death alarm +
        observability record (embedded evaluator — call-triggered, no
        daemon per #176; the hosted surface is /v1/dream/health).

        Alarm (COUNTER-based, never wall-clock — DE2E-8): stale backlog
        exists (dirty roots non-empty) AND the last dream pass produced
        zero output (affected claims == 0). A dead/silently-skipped dreamer
        looks identical to "nothing to do" — this is the A8 detection.
        Positive-control semantics: a backlog with REAL output is healthy —
        the alarm MUST NOT fire.
        """
        # #1163: hydrate graph-persisted dirty roots first so the hosted
        # health surface sees cross-process backlog (a fresh request-scoped
        # SDK would otherwise report a zero backlog over dirty graphs).
        self._hydrate_dirty_roots()
        backlog = len(self._dirty_roots)
        last_output = self._dream_metrics.get("last_pass_output", 0)
        last_pass_at = self._dream_metrics.get("last_pass_at")
        # #3139 (review P1): the previous alarm was
        #     backlog > 0 and last_output == 0 and last_pass_at is not None
        # which stayed FALSE in BOTH dishonest states the issue measured.
        #
        #  (a) A graph that had never run a pass has `last_pass_at is None`,
        #      so it reported `alarm_verdict: False, alarm_reason: 'ok'` with a
        #      live backlog and 0% coverage — literally the state the issue
        #      recorded.
        #  (b) A silent no-op still trivially stamps operator-less claims, so
        #      `affected_claims` (→ last_pass_output) is NON-zero even though no
        #      belief state was written; and `coverage_pct` counts STAMPS too,
        #      so it RISES. Keying the alarm on stamps cannot see a
        #      belief-state no-op — the health surface got *more* reassuring
        #      the worse the failure was.
        #
        # So the alarm now also keys on the two belief-state signals: the
        # proven silent no-op, and a live backlog with no coverage at all.
        #
        # But it must NOT contradict the guard's own classification of a
        # LEGITIMATE no-op. `_guard_dream_progress` says of the #1163
        # stale-run stand-down "Zero writes is correct here" — yet the
        # backlog terms below would page on it, because the stand-down
        # deliberately retains the dirty root. Excuse the reasons the guard
        # calls legitimate instead of false-paging on them.
        no_op_reason = self._dream_metrics.get("last_pass_no_op_reason")
        # #12116 review A-P2 (the WHOLE-read half): the live/stamped pair is read
        # ONCE per health call and its ratio is computed from that one value.
        # Before this, `coverage_pct` and the `propagation` block each called the
        # pair read of their own (twice + once = the same aggregate computed
        # THREE times, measured 5 graph queries per `dream_health_check`), and a
        # helper-level pin could not see it — R7 bounded only the new helper, so
        # the endpoint grew while the test stayed green. Both consumers below
        # take the pair (or its failure) from THIS read.
        # Failure posture is preserved per surface: `coverage_pct` keeps its
        # pre-existing 0.0 (it feeds the alarm), while the `propagation` block
        # reports `unavailable` with the reason (BOUNDED-OPERATION-CONTRACT §1a)
        # — so a failed read is still `0` on one surface and `unavailable` on the
        # other, never collapsed into a single meaning.
        stamp_pair, stamp_error = self._read_stamp_pair()
        coverage = (self._coverage_ratio(stamp_pair)
                    if stamp_pair is not None else 0.0)
        _LEGITIMATE_NO_OP = {
            "stale_run_guard", "budget_zero", "no_dirty_roots",
            "no_ep_factors",
        }
        if no_op_reason in _LEGITIMATE_NO_OP:
            alarm = False
        else:
            alarm = bool(
                no_op_reason == "silent_no_op"
                or (backlog > 0 and last_output == 0)
                or (backlog > 0 and coverage == 0.0)
            )
        # Derive the reason FROM the verdict: a reason without an alarm would
        # report a failure that `alarm_verdict` denies (and would leak a
        # backlog-terms reason on a deliberately excused legitimate no-op).
        if not alarm:
            alarm_reason = "ok"
        elif no_op_reason == "silent_no_op":
            alarm_reason = "silent_no_op"
        elif backlog > 0 and last_output == 0:
            alarm_reason = "zero_output_with_backlog"
        elif stamp_error is not None:
            # #12099 (the per-call half of #11823) — the "could not read it" pole
            # of `BOUNDED-OPERATION-CONTRACT.md` §1a (`0`, `"empty"` and "I
            # could not read it" must never become one answer). The pair read
            # FAILED, so `coverage` above is the `0.0` FALLBACK, not a reading,
            # and `zero_coverage_with_backlog` would assert a measurement that
            # never happened.
            #
            # SCOPE: this arm closes the FAILED-READ pole only, not §1a for this
            # field. Two residuals stay open on #12099 and are deliberately not
            # touched here: (a) an EMPTY denominator (`_coverage_ratio`'s `0/0`
            # → `0.0`) is still labelled as a measured zero — the "empty" pole;
            # and (b) this arm is unreachable while `last_pass_output == 0`, i.e.
            # on ANY instance that has served no pass — `GET /v1/dream/health`'s
            # per-request SDK, and an embedded SDK before its first pass — where
            # the `zero_output_with_backlog` arm ABOVE wins. (b) is a CONDITION,
            # not a surface: fixing only the hosted door would leave it intact on
            # embedded (measured: identical output before and after this change).
            #
            # The VERDICT is unchanged on purpose: an unreadable coverage over a
            # live backlog still alarms (fail-loud), and `coverage_pct` keeps its
            # pre-existing `0.0` because that number FEEDS this alarm — moving it
            # to `None` would disarm the term the alarm exists for. Only the
            # REASON stops claiming the zero was read; the WHY travels beside it
            # on `propagation.unavailable` in this same record (that field
            # conflates a failed stamp read with a failed census read where
            # coverage WAS measured, so it cannot answer "was the alarm's input
            # measured" — which is why the reason token is the right home).
            alarm_reason = "coverage_unavailable"
        else:
            alarm_reason = "zero_coverage_with_backlog"
        state = self.dream_health_state()
        stale_backlog = sum(  # noqa: F841
            1 for _ in self._dirty_roots)  # non-empty check below
        return {
            "alarm_verdict": alarm,
            "alarm_reason": alarm_reason,
            "stale_backlog": backlog,
            "last_pass_at": last_pass_at,
            "last_pass_output": last_output,
            # #3139 (review P1): belief state WRITTEN — as distinct from
            # `last_pass_output`, which counts stamps. Their divergence is
            # exactly what a silent no-op looks like on this surface.
            "last_belief_write_count": self._dream_metrics.get(
                "last_belief_write_count"),
            # #3139: legitimate-no-op vs silent-no-op is a difference the
            # caller must be able to see. The silent case raised
            # DreamNoOpError; this reports the proven-empty case.
            "no_op_reason": self._dream_metrics.get("last_pass_no_op_reason"),
            # …the SAME number the alarm above was computed from — one read, not
            # a second one that could disagree with the alarm's input (#12116 A-P2).
            "coverage_pct": coverage,
            "failure_rate": (
                self._dream_metrics["failure_count"]
                / max(self._dream_metrics["pass_count"], 1)),
            "operator_counts": getattr(
                self._get_dreamer(), "_last_operator_count", 0) or 0,
            "per_mode_counts": dict(
                self._dream_metrics.get("per_mode_counts", {})),
            "region_attempts": state["retry_attempts"],
            "warm_skipped_updates": state["warm_skipped_updates"],
            # #11823 — the propagation cap, made visible. `coverage_pct` above
            # is STAMP coverage over a population EP cannot reach; this block
            # is the graph-scoped answer to "how many claims have no belief",
            # plus the reach of the last pass THIS SDK instance served (not
            # "in this process" — the hosted door runs the pass on another
            # per-request SDK, see `_propagation_health`). It is an
            # additive field on a plain dict (no key-set is pinned here), and
            # it is on this surface for the same reason #3139 put
            # `last_belief_write_count` here: the pass result's key-set IS
            # pinned (I1, #6211), so belief observability lives on health.
            # "6 of 20, 14 uncovered": the graph-scoped half gives the counts;
            # this records the pass that ran. The pass half is per-instance, so
            # the per-pass keys are absent when this instance served no pass —
            # see `last_pass_reason`. The stamp pair (and its failure) is the ONE
            # read this call already made above, so the block does not repeat it
            # (#12116 A-P2, whole-read half).
            "propagation": self._propagation_health(stamp_pair, stamp_error),
        }
