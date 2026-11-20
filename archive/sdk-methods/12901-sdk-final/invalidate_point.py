# Archived sdk-method `invalidate_point` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_invalidate_point`; recorded destination `update_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'invalidate_point'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    @_declares_live_write_lane("invalidate_point")
    def invalidate_point(self, id: str, corrected_by_id: str,
                         *, valid_to_precision: str | None = None,
                         valid_to: str | None = None) -> dict:
        """Mark a Point outdated, linked to its replacement via CORRECTS edge.

        §17.1 LANE (#9124): JOURNAL-FIRST, declared. The `PointInvalidated`
        record is appended before the terminal stamp and the `CORRECTS` merge
        ("validated-emit-then-mutate" below), so every mutating statement in
        this method follows the append and the method carries
        `@_declares_live_write_lane`.

        Validation contract (#330/#2498) — all checks run BEFORE any write so
        a failure can never leave a partial graph state:
        - id == corrected_by_id → ValueError (a self-CORRECTS edge poisons
          traversal/credibility chains).
        - old point missing (never existed or already deleted) →
          {"invalidated": False} with no writes (retry-friendly).
        - old point is an OPERATOR or already terminal (shared terminal
          vocabulary: status in TERMINAL_EXCLUDED_STATUSES OR the legacy
          outdated=true flag) → ValueError via the single shared lifecycle
          guard, #2498 — an operator has no place in a CORRECTS chain, and a
          dead claim must not be re-stamped / re-edged.
        - corrected_by point missing, an OPERATOR, or already terminal →
          ValueError (structural failure: would orphan an outdated point, or
          wire a CORRECTS edge from an operator / a dead claim).
        - the predecessor's ``validFrom`` is AFTER ``now`` → ValueError
          (#5358). The stamp block writes ``validTo = now`` unconditionally,
          so a future-dated predecessor would persist an INVERTED window
          (``validTo < validFrom``); ``get_historical_knowledge``'s ``_covers`` then
          covers no instant and the point silently vanishes from every
          temporal query. Fail-closed refusal BEFORE any mutation (no partial
          write, no journal event), naming ``retract_point`` — the
          window-agnostic route — as the caller's way forward. Equality is
          well-formed (a zero-length ``[now, now]`` window is legal).
        Because ``outdated=true`` is itself terminal, repeating an invalidate
        now raises (#2498) instead of re-asserting: the old #330 "re-assert"
        contract let a dead claim's ``expiredAt`` move forward and minted one
        CORRECTS edge per distinct corrector onto a node every read surface
        already excludes.

        E-prec (#2350): ``valid_to_precision='unknown'`` closes the window
        WITHOUT stamping a fabricated ``validTo=now`` — the closed-unknown
        state ("this ended, but I cannot date it"). When that marker is given
        the #5358 inverted-window precondition is skipped: no end is written,
        so no inversion is possible.

        ``valid_to=`` (#4282) is the caller's WINDOW END: the instant the claim
        stopped holding. It replaces the ``now`` fallback in the stamp — and only
        there, since ``expiredAt``/``updatedAt`` record when the graph LEARNED of
        the end, not when the claim ended. It reaches the record through
        ``valid_to=`` on ``PointInvalidated`` (the §17.5 carrier), so a rebuild
        replays the caller's instant rather than its own clock, and the #5358
        inversion guard runs against the value that is actually stored.
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        if id == corrected_by_id:
            raise ValueError(
                f"invalidate_point: corrected_by cannot be the point itself ({id!r})"
            )
        # #2498: the shared lifecycle guard replaces the pre-#2498
        # existence-only probe. `missing_ok` preserves the retry-friendly
        # missing-id contract (#330) while rejecting operator/terminal input.
        if self._assert_lifecycle_guard(
                id, method="invalidation", role="source",
                missing_ok=True) is None:
            return {"invalidated": False, "id": id, "corrected_by": corrected_by_id}
        # #2498: guard the CORRECTOR leg too — `supersede_point` guards BOTH
        # endpoints and `supersede_knowledge(..., transfer_edges=False)` routes here, so
        # an unguarded corrector let `(operator|terminal)-[:CORRECTS]->(point)`
        # through on one leg and not the other. A missing corrector still
        # raises (structural failure — would orphan an outdated point).
        self._assert_lifecycle_guard(
            corrected_by_id, method="invalidation", role="corrector")
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        # #2350 (E-prec): 'unknown' closes the window WITHOUT a fabricated
        # validTo — the closed-unknown state (utopia's ``valid_to_precision``
        # pattern). Resolved before the #5358 inversion guard, which only
        # applies when a ``validTo`` is actually stamped.
        from .validity import PRECISION_UNKNOWN as _PREC_UNKNOWN
        from .validity import normalize_precision as _normalize_precision
        end_precision = _normalize_precision(
            valid_to_precision, field="valid_to_precision")
        # #4282: a caller-named instant IS the window end; `now` is the fallback
        # for a caller who named none. Resolved BEFORE the #5358 inversion guard,
        # which must measure the value that is about to be stored.
        end_instant = valid_to if valid_to is not None else now
        stamp_valid_to = None if end_precision == _PREC_UNKNOWN else end_instant
        # #5358: refuse an INVERTED predecessor window BEFORE any mutation.
        # The check is shared with the MCP dry-run preview
        # (`_preview_invalidate`) so the writer and its preview cannot drift.
        if stamp_valid_to is not None:
            self._assert_window_start_not_inverted(id, stamp_valid_to)

        # #2488 (rebuild-parity fix): kwargs-style PointInvalidated emission —
        # validated-emit-then-mutate (mirrors supersede_point's #432 anti-
        # phantom pattern). ts=now MUST be passed: the fold's updatedAt reads
        # ev.get("ts") (fallback clock), and a drift from the live SET clock
        # breaks exact-stamp rebuild parity. Crash after emit/before write is
        # convergent: re-run revalidates + re-emits; duplicate events fold
        # idempotently. #2498: double-invalidate is NO LONGER legal (the shared
        # lifecycle guard treats the outdated=true flag as terminal), so a
        # crash-replay after the write raises — the fold replay is what stays
        # idempotent.
        self._emit_event(
            "PointInvalidated",
            id=id, corrected_by=corrected_by_id,
            ts=now, valid_to=stamp_valid_to,
            valid_to_precision=end_precision, expired_at=now,
        )

        # #7405: both statements are retry-admissible on
        # `retryable_aborted_write` alone. The terminal stamp is an ABSOLUTE
        # `SET` whose every value (``now``, ``stamp_valid_to``,
        # ``end_precision``) was fixed before the first attempt, so a re-issue
        # writes the same end-state without moving the window end; the
        # `CORRECTS` edge is a MERGE on its endpoints, so it cannot double.
        # The abort means the statement did not land, so neither re-issue can
        # re-apply an effect — which is why this is safe even though the event
        # above is emitted BEFORE the mutation.
        self._graph_write_with_retry(
            lambda: proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.outdated = true, "
                "n.updatedAt = $now, n.validTo = $valid_to, "
                "n.validToPrecision = $valid_to_precision, "
                "n.expiredAt = $now, "
                f"{decay_clause('n')}"
                + " RETURN count(n)",
                params={"id": id, "now": now, "valid_to": stamp_valid_to,
                        "valid_to_precision": end_precision},
            ),
            what="invalidate_point terminal stamp SET",
            # #8854 review P1: a re-issue into a rebuilt-but-not-yet-replayed
            # graph matches nothing; that must stay loud, not read as success.
            witness=_landed_at_least_one,
        )
        self._graph_write_with_retry(
            lambda: proj.g.query(
                "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
                "MERGE (a)-[:CORRECTS]->(b) RETURN count(*)",
                params={"new_id": corrected_by_id, "old_id": id},
            ),
            what="invalidate_point CORRECTS edge",
            witness=_landed_at_least_one,
        )
        # Dreaming (#85): invalidation changes the propagation graph.
        # #2422 ORDERING: message drops BEFORE _mark_dirty's epoch bump (see
        # supersede_point — concurrent EP must see drops with the new epoch).
        # Epic 903-C9 (#1247): edge TRANSFER — invalidate warm-start seeds on
        # both endpoints' edges (a transfer is not a new/deleted edge; the
        # C4 topology hooks don't fire here).
        self._get_ep().invalidate_messages([id, corrected_by_id])
        # #2422 (ghost-vote leg): the invalidated (outdated=true) claim's
        # operators may go DEGENERATE (skipped as factors — never re-run to
        # zero their sibling-edge messages), so drop every operator-connected
        # edge's message too (op1→B carries the dead claim's influence into B
        # on an edge that does not touch the dead claim).
        self._get_ep().invalidate_factor_messages([id])
        self._mark_dirty([id, corrected_by_id])
        return {"invalidated": True, "id": id, "corrected_by": corrected_by_id}
