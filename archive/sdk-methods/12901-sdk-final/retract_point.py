# Archived sdk-method `retract_point` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_retract_point`; recorded destination `update_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'retract_point'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    @_declares_live_write_lane("retract_point")
    def retract_point(self, id: str, *, valid_to: str | None = None) -> dict:
        """Tombstone-retract a Point: status='retracted' (point stays in graph).

        §17.1 LANE (#9124): JOURNAL-FIRST, declared. `PointRetracted` is
        appended before the CAS `SET` ("append-before-mutation" below), and the
        method's every mutating statement follows it, so the method carries
        `@_declares_live_write_lane`.

        ⚠️ ORDER is what the declaration guarantees; CONFIRMATION is not. The
        append's return is discarded here, so a FAILED append still retracts the
        cache node and reports success — measured on this branch by forcing
        `_emit_event` to return False: the point came back `status='retracted'`.
        Only `create_point`, `update_point`'s non-promote arm and `delete_point`
        gate on their append's return — and on `delete_point` that gate can only
        REPORT (the destroy already landed, #9207), not refuse the write;
        `invalidate_point`, this door and
        `supersede_point` do not. That residual is part 2 of #9124 (it changes
        the failure contract).

        #432: retraction is a TERMINAL state transition, not a deletion — the
        projection keeps the point with status='retracted' and default query
        surfaces exclude it (opt-in via include_retracted). Single atomic
        conditional query on the happy path; diagnostic read only on the error
        path.

        Raises ValueError if the point is missing, is an operator node, or is
        already terminal (the shared terminal vocabulary: a status in
        live.TERMINAL_EXCLUDED_STATUSES OR the legacy outdated=true flag).

        ``valid_to=`` (#4282) is the caller's WINDOW END — the instant the claim
        stopped holding, which is what "retraction is a temporal field" means
        once the field is read rather than merely tested. It is stamped as the
        point's ``validTo`` in the same CAS ``SET`` (the node is still live here,
        so this is not the #7819 terminal re-stamp that verb refuses) and carried
        on the ``PointRetracted`` record, so a rebuild replays the caller's
        instant instead of losing it. ``updatedAt`` keeps its own meaning: the
        graph's clock for when it LEARNED of the retraction.
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        # P1 (code-review): validate FIRST, then emit, then mutate — the emit
        # before the guard produced phantom PointRetracted events on the
        # NORMAL invalid-input path (missing / operator / terminal), which
        # poll consumers would see as retractions that never happened.
        # #432/#2498: the SHARED transition guard (see
        # _assert_lifecycle_guard) — the pre-#2498 body hardcoded the same
        # 3-status subset as supersede_point.
        self._assert_lifecycle_guard(id, method="retraction")
        # #5048 (#4666): ONE clock read for both the record and the node.
        # `updatedAt` is RECORDED, not recomputed (docs/durability-posture.md
        # → `:Source.updatedAt`), so the producer must mint the instant once —
        # a second read here, and a third at replay, made live/rebuild/fold
        # hold three different stamps for one retraction.
        _now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        # #4282: refuse an INVERTED window BEFORE any mutation (the same shared
        # guard `invalidate_point` / `supersede_point` use, #5358) — a retraction
        # that names an end before the stored start would persist a window
        # covering no instant, which is exactly what that guard exists to stop.
        if valid_to is not None:
            self._assert_window_start_not_inverted(id, valid_to, door="retract_point")
        # #432 Task 3: durable PointRetracted event (append-before-mutation;
        # only after the input contract validates).
        #
        # ⛔ `valid_to` must ride the JOURNAL, not only the change-feed payload
        # (#10053 review, P1 — settled by executing the durability gate).
        # `_emit_event`'s POSITIONAL `payload=` reaches the `:GraphEvent` store
        # ONLY: the JSONL record is assembled from `point` / `id` / `**extra`.
        # So `_emit_event("PointRetracted", {"id": id, "valid_to": …}, id=id)`
        # journalled a record with NO window end — measured: the live node held
        # `validTo`, the rebuilt node had none, and `derived = replay(journal)`
        # was FALSE for every dated retraction. The kwargs form is what
        # `invalidate_point` already uses (`ts=…, valid_to=…`), and it emits the
        # SAME graph payload (`{"id": id, **extra}`) while also putting the key
        # on the record — which is what `record_projection`'s `from_payload` and
        # both replay folds read.
        #
        # An UNDATED retraction passes no key at all, so its record stays
        # byte-identical to before: presence is ownership, and the folds' shared
        # `_storable_bound` gate already treats "absent" and "null" alike (and
        # keeps an END_UNKNOWN spelling such as `""`, which the INSTANT
        # predicate `_usable_instant` would drop — #10053 review).
        _window = {} if valid_to is None else {"valid_to": valid_to}
        self._emit_event("PointRetracted", {"id": id, **_window},
                         id=id, recorded_ts=_now, **_window)
        # P1 (Qwen review): CAS the SET — the WHERE re-checks terminal state so
        # a concurrent retract/supersede/invalidate can't both pass validation
        # and have a terminal overwrite. #2498: the CAS now uses the SAME
        # shared vocabulary as the guard and the read path — the generic
        # `_terminal_excluded` predicate also rejects the legacy
        # `outdated=true` flag, not just a hardcoded status subset.
        # #7405: the CAS SET rides the bounded write retry — the same
        # terminal-transition shape as `invalidate_point`'s stamp. The
        # `_terminal_excluded` guard is INSIDE the statement and is re-read on
        # re-issue, and `_now` is minted once above, so a re-issued abort (which
        # by definition did not land) converges on the same stamp and cannot
        # re-retract or move `updatedAt`. An already-terminal point still yields
        # an empty result set on re-issue, so the `ValueError` contract is
        # unchanged — and the witness below makes that TRUE rather than
        # assumed (#8854 review round 3).
        def _retract_witness(result) -> bool:
            """Empty CAS result: tell "terminal" from "not replayed yet".

            ``PointRetracted`` is appended BEFORE the CAS, so a rebuild that
            replays that record terminalizes the node and the re-issued CAS
            can never match — a zero-row re-issue is therefore legitimately
            reachable for an already-terminal point. The diagnostic read (only
            on that empty path) decides which contract applies: the node is
            there and terminal → the documented ``ValueError``; absent → the
            write never landed, and the abort surfaces instead of a false
            "already terminal".
            """
            if result.result_set:
                return True
            rows = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
                params={"id": id},
            ).result_set
            if rows:
                status, outdated = rows[0]
                # PRESENCE alone is not terminality (#8854 review round 4 P2):
                # the rebuild's replay can land the node live between the CAS
                # and this read, and claiming "already terminal" for a live
                # point is the exact failure this PR closes.
                if status in TERMINAL_EXCLUDED_STATUSES or outdated:
                    raise ValueError(
                        f"Point {id!r} is already terminal — retraction is terminal")
            return False
        r = self._graph_write_with_retry(
            lambda: proj.g.query(
                "MATCH (n:Point {id:$id}) "
                f"WHERE {_terminal_excluded('n.status')} "
                "SET n.status = 'retracted', n.updatedAt = $now"
                + (", n.validTo = $valid_to" if valid_to is not None else "")
                + f", {decay_clause('n')} RETURN properties(n)",
                params={"id": id, "now": _now,
                        **({"valid_to": valid_to}
                           if valid_to is not None else {})}),
            what="retract_point CAS SET",
            # #8854 review P1: empty `result_set` is ambiguous — "matched a
            # terminal node" (the legitimate ValueError below) or "matched
            # nothing" (a rebuild still replaying). `_retract_witness`
            # disambiguates with one diagnostic read on that path only.
            witness=_retract_witness,
        )
        if not r.result_set:
            raise ValueError(
                f"Point {id!r} is already terminal — retraction is terminal")
        # #2422: retraction changes the propagation graph — the retracted
        # claim must stop voting, so its neighborhood must be recomputed.
        # (supersede_point / invalidate_point already mark dirty; retraction
        # was the missing write surface — without it, EP posteriors that the
        # retracted claim supported stayed stale.)
        # #2422 ORDERING: the message drops run BEFORE _mark_dirty's epoch
        # bump — a concurrent EP run that observes the new epoch must also
        # observe the dropped messages (see supersede_point for the full
        # stale-run-guard rationale). The drops kill the dead claim's ghost
        # votes: WITHOUT them, warm-start seeds reuse the pre-retraction
        # messages on the dead claim's IMPL|NAND edges — the claim's operator
        # factor is excluded (degenerate) and never re-runs to zero them, but
        # _update_claim_posterior still consumes the stale message on the
        # neighbor side (the 0.5503 ghost in the E2E repro). BOTH the claim's
        # own edges AND its operators' sibling edges carry its influence —
        # drop both (invalidate_messages covers the former,
        # invalidate_factor_messages the latter).
        self._get_ep().invalidate_messages([id])
        self._get_ep().invalidate_factor_messages([id])
        self._mark_dirty([id])
        return r.result_set[0][0]  # updated node props (no trailing get_point round trip)
