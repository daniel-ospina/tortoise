# Archived sdk-method `delete_point` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_delete_point`; recorded destination `delete_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'delete_point'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    @_declares_apply_first_write_lane("delete_point")
    def delete_point(self, id: str) -> bool:
        """Delete a Point and its relationships. Returns True if found.

        Implementation behind delete(id) — the consolidated Point/entity
        delete (epic #888 W2).

        §17.1 LANE (#9206): DECLARED — `@_declares_apply_first_write_lane`,
        the lane that states this door's order (APPLY-FIRST BY DECISION, #9207).
        It is deliberately NOT `@_declares_live_write_lane`: that lane claims
        "the record is appended, THEN the cache is written", which is false
        here (see the comment above the signature).

        THE CACHE WRITE IS THE COMMIT POINT (#9207). The `DETACH DELETE` is
        issued FIRST, and the `EntityMutated` op=delete record is appended only
        once it has RETURNED — so the journal cannot claim a destruction it is
        still serving the node through. That is the state #7951's append-first
        ordering left behind (measured with §17.1's write door armed, #9124) and
        the only one in which the record EXCEEDS the cache; repairing it needs a
        destructive `rebuild_all`, which every caller reads as "the delete
        happened" while `get_point` disagrees.

        Raises ``WriteNotLandedError`` (``operation="delete_point"``) in TWO
        windows, and they are distinguishable on the exception:

        * the destroy LANDED and the `EntityMutated` op=delete record did not
          confirm — ``destroy_verified=True``, ``record_committed=False``. The
          refusal says so
        (the record is UNCONFIRMED, never known-absent — a journal append can
        write and then raise: a partial `write(2)` stopped by ENOSPC/EIO, or a
        write whose close failed, #5612 having removed the buffered flush). In
        that state the TRUTH still holds the Point while the cache does not, and
        nothing repairs it by itself: `recover_from_log` rebuilds only an EMPTY
        graph, and `rebuild_all` is destructive — it RE-CREATES the Point from
        the journal (undoing the delete), after which the delete must be
        re-issued.
        * NEITHER store's state is known — ``destroy_verified=False``: the
          `DETACH DELETE` raised and the cache could not be read back (or its
          graph key was gone, so the read would have re-created it). No record is
          written and the refusal claims NOTHING about either store; the caller
          must establish the cache state with `get_point` before acting.

        A no-op id still returns ``False``; ``True`` still means the node was
        destroyed (or was already absent when the statement ran).
        """
        proj = self._get_proj()
        exists = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n) > 0",
            params={"id": id},
        ).result_set[0][0]
        if not exists:
            return False
        # A tag's edge count can only change for this point's own TAGGED
        # edges — scope the orphan scan to that case (skips the global tag
        # scan on every untagged delete; #485).
        has_tag_edges = proj.g.query(
            "MATCH (n:Point {id:$id})-[:TAGGED]->() RETURN count(*) > 0",
            params={"id": id},
        ).result_set[0][0]
        # #1916: capture the deleted point's 1-hop reverse-BFS neighbors
        # BEFORE the DETACH DELETE — the delete removes the node's edges, so
        # the post-delete _mark_dirty reverse-BFS can no longer see them and
        # no neighbor would be dirtied (stored confidences stay stale at
        # pre-delete values). Shared helper keeps this capture in lockstep
        # with _mark_dirty's own traversal.
        _op_ids, neighbor_claims = self._reverse_bfs_neighbors(proj, [id])
        # #2422/#5566: the OPERATOR ids are dropped here, exactly as
        # `_mark_dirty` drops them. Most operators are unsweepable as roots —
        # `_sweep_dirty_roots` subtracts only `affected`, and `_affected_claims`
        # admits an operator's INPUTS, so an operator that is nobody's input
        # keeps its `ep_dirty` forever, pinning `_auto_dream_mode` to 'local'
        # (measured: after a converged pass such an operator was the only
        # remaining root AND the only remaining graph flag). A NESTED operator —
        # one that is another operator's input — is reachable through the
        # per-hop expansion and does not have that problem, so this is a
        # narrowing of which ids become roots, not a claim that operators can
        # never be swept. Nothing is lost from #1916: the claims below are the
        # operator's OTHER inputs, and the hop in `TortoiseEP` walks
        # `(n)<-[:IMPL|NAND]-(op)-[:IMPL|NAND]->(m)` from them, so
        # `_affected_factors` still re-derives the operator's factor.
        neighbor_ids = [cid for cid in neighbor_claims if cid != id]
        # ── #9207 — THE CACHE WRITE IS THE COMMIT POINT ON THIS DOOR ──────
        # #9207 REVISES #7951's ordering here, deliberately and with the
        # measurement in hand (the OVERRIDES line is recorded on the issue).
        #
        # #7951 put the `EntityMutated` op=delete APPEND first, so a cache
        # write that cannot be guaranteed could never destroy a node the
        # journal had not recorded. That closed the "`rebuild_all` resurrects
        # it" window — and opened the mirror one: if the `DETACH DELETE` does
        # NOT land, `delete_point` raised with a destruction record already
        # committed, so `get_point` still served the Point while the truth
        # claimed it was gone. A retry cannot be re-issued out of that state —
        # over a still-present node it appends a SECOND destruction record for
        # one intent — and only a DESTRUCTIVE `rebuild_all` resolves it.
        #
        # ⛔ What that window is NOT is a systematic pre-write refusal, and the
        # reason is the GUARD'S ARMING STATE, not a gap in its coverage. §17.1's
        # write surface has three doors (#9124 part 1): the fold
        # (`FalkorProjection.apply`), the public raw-query lane
        # (`FalkorProjection.query`) and THIS one — the projection's write
        # handle (`proj.g`), through which `DETACH DELETE` is issued. The handle
        # is DETECTED on the same terms as the other two (shared
        # `_statement_writes` classifier): a mutating statement with no declared
        # lane is counted and logged. What is not yet live is its REFUSAL —
        # `_HANDLE_GUARD_ENFORCES` is `False` in the landed state (#9124 part 1,
        # owner decision option B), so nothing refuses this statement today.
        # This lane's own probe measured exactly that — `journal_backed (guard
        # armed): True`, then `RAW HANDLE LANE (proj.g.query): SUCCEEDED`
        # (docs/evidence/7951-delete-point-outbox/probes/delete_order_probe.py,
        # delete-order-probe.log). So the reachable trigger TODAY is a generic
        # engine/transport failure with the node still present: RARE. ⚠️ Once
        # part 2 arms the handle this door COMPLETES: it holds the DECLARED
        # apply-first lane (#9206 — `@_declares_apply_first_write_lane` above the
        # signature), which states "the cache write IS the commit point and the
        # record that describes it follows". What an armed handle still REFUSES
        # here is a raw UNDECLARED statement — the lane is on the door, not on
        # the statement. The ordering below is justified by the DIRECTION of the
        # divergence it leaves, not by its frequency — and the refusal test
        # injects the failure rather than claiming the door produces it.
        #
        # So the destroy is issued FIRST and the record is written only once it
        # has RETURNED. A cache write that raises appends NOTHING, so #9207's
        # invariant holds by construction: the journal cannot record a
        # destruction that the cache still holds the node through.
        #
        # What that trades for it, stated so it is not discovered later: if the
        # append fails AFTER a landed destroy, the cache is destroyed and the
        # record is not — the mirror window, where the cache LEADS the truth.
        # Whenever code runs it is reported, never silent (the refusal below),
        # and it heals in the TRUTH's direction: the truth still holds the
        # Point, so a replay RESTORES it and the caller re-issues the delete.
        # ⚠️ The window that is NOT reportable is a PROCESS DEATH between the
        # statement returning and the append (`kill -9`, OOM, power loss): no
        # code runs, so nothing raises and nothing is logged, and the next
        # `rebuild_all` re-creates the Point from the `PointAdded` line. Stated
        # rather than implied — it is the crash window this order accepts in
        # exchange for closing one that was systematic. #7951's window could
        # not be re-issued out of at all — a retry over a still-present node
        # appends a SECOND destruction record for one intent.
        #
        # The existence probe above still runs FIRST, so a delete of a
        # non-existent id journals nothing and returns False (unchanged).
        # #7405: the hard delete rides the bounded write retry. A DETACH DELETE
        # by an explicit id is idempotent — a re-issue after a non-landing ABORT
        # re-deletes a node that is (at worst) already gone, a no-op. The abort
        # MUST be proved not-landed, which is exactly what
        # `retryable_aborted_write` admits (a bare DELETE never rides
        # `retryable_transient`).
        #
        # #7951 review P1, PRESERVED IN THE NEW ORDER (#9207): a failure here
        # is CALLER-FACING, not just a crash window. Nothing has been recorded
        # yet, so the honest report is the engine's own error. But a raise does
        # NOT prove "nothing was destroyed": `_graph_write_with_retry` retries
        # only the aborted-write family, and `retryable_transient` deliberately
        # excludes the transport family (a timeout / dropped connection) because
        # there the server may have APPLIED the write and lost the reply. So the
        # cache is READ BACK before this door decides what to tell the caller —
        # otherwise a lost reply would destroy the Point with no record and no
        # report, which is the one direction this door must never produce.
        #
        # ⚠️ SCOPE: this wraps the `DETACH DELETE` ONLY, and that is the one
        # statement whose failure would misreport a LANDED destruction. The
        # statements after it stay bare deliberately (#7951 review round 2):
        #   * `_emit_event("PointRetracted", …)` is best-effort by construction
        #     (#4240) — it never raises, so there is nothing to wrap.
        #   * the orphan-tag GC is a RETRIED write that can still exhaust, but
        #     its failure leaves orphan `:Tag` rows, not a lost or an unapplied
        #     destruction: the node is already gone and the record already
        #     says so. A re-issue finds no node and returns False, so it
        #     cannot append a second record.
        #   * `_mark_dirty` / `invalidate_messages` are cache bookkeeping; a
        #     failure there is a stale-confidence report, not a state lie.
        # Widening this wrap would convert those into a "the delete did not
        # happen" claim that is FALSE after the DETACH DELETE landed — the
        # class of wrong answer this door exists to remove.
        #
        # No `RETURN count(n)` here, deliberately: this statement's result is
        # not used as a gate. A DETACH DELETE either removes every match or
        # raises, so a normal return — any row count — means the cache no longer
        # holds the Point: either this call removed it, or it was already absent
        # (a peer deleted it, or a rebuild's replacement graph has not replayed
        # it yet). Recording the destruction is TRUE of the cache in both cases,
        # and gating on a non-zero count would LOSE a delete performed inside a
        # rebuild window when the re-issue matches nothing (#7405's exemption,
        # pinned in `tests/test_7405_remaining_writes.py`).
        _reply_lost: BaseException | None = None
        try:
            self._graph_write_with_retry(
                lambda: proj.g.query(
                    "MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": id}),
                what="delete_point DETACH DELETE",
            )
        except Exception as exc:
            # READ the cache back before deciding: the raise may be a lost reply
            # over an APPLIED write (`_delete_landed_anyway` returns ``True``),
            # in which case the destruction must be recorded exactly as on the
            # happy path — the record follows the CACHE, so an applied destroy
            # with no record is the silent unrecorded destroy this door exists
            # to prevent. ``False`` is the clean refusal (#9207's indicator): a
            # delete refused before it wrote leaves NO destruction record.
            landed = self._delete_landed_anyway(proj, id, exc)
            if landed is False:
                raise
            if landed is None:
                # Cannot tell whether the destroy landed, so claim neither. No
                # record is written: the journal must not describe a cache state
                # nobody verified (that is the #9207 defect, mirrored).
                from .exceptions import WriteNotLandedError
                raise WriteNotLandedError(
                    id, getattr(proj, "graph_name", None),
                    f"the DETACH DELETE raised ({type(exc).__name__}: {exc}) "
                    "and the cache could not be re-read afterwards, so the "
                    "destruction is UNVERIFIED and no record was written",
                    operation="delete_point",
                    record="EntityMutated op=delete",
                    destroy_verified=False) from exc
            _reply_lost = exc
        if _reply_lost is not None:
            _logger.warning(
                "delete_point: the DETACH DELETE raised (%s: %s) and the cache "
                "no longer holds the Point — either this statement applied and "
                "the reply was lost, or the Point was already gone. The "
                "destruction record is written from the cache's own state, "
                "which is what this door can verify",
                type(_reply_lost).__name__, _reply_lost)
        # The destroy LANDED (or the Point was already absent). Only now may
        # the journal say so.
        if self._journal_entity_mutation("Point", id, "delete") is False:
            # ⚠️ RESIDUAL, declared not absorbed: this raise exits BEFORE
            # `_mark_dirty(neighbor_ids)` and `invalidate_messages()` below, so
            # the 1-hop neighbours of a destroy that DID land keep their
            # pre-delete stored confidences until something else dirties those
            # roots. Bounded, not silent: the caller is told the destroy landed
            # and that only a DESTRUCTIVE `rebuild_all` repairs the record, and
            # that rebuild recomputes every root. Named in the plan's §6 so a
            # later lane does not read this as full bookkeeping.
            from .exceptions import WriteNotLandedError
            raise WriteNotLandedError(
                id, getattr(proj, "graph_name", None),
                "the destroy landed in the cache, and the journal append for "
                "its record then did not confirm; if that record did not land "
                "the TRUTH still holds this Point",
                operation="delete_point",
                record="EntityMutated op=delete",
                destroy_verified=True)
        # #3300 residual: ONE event type must not mean two end-states.
        # `delete_point` hard-deletes; `retract_point` tombstones. The single
        # `PointRetracted` (id=) call emitted BOTH stores, and its fold has
        # exactly one meaning — tombstone — so a hard-deleted Point came back on
        # rebuild as `status='retracted'`.
        #
        # Two stores, one meaning each:
        #   * :GraphEvent ONLY (payload style, `id=` omitted) — the subscriber
        #     surface is UNCHANGED; `tortoise_events_poll` still sees the
        #     point-delete lifecycle row. #2353: the payload gains
        #     `deleted: true` (`_deleted_point_payload`) so the change feed can
        #     tell this hard delete from `retract_point`'s tombstone. The marker
        #     is UNCONDITIONAL — it records what this write did, and gating it on
        #     the reader's switch mislabelled every hard delete performed by an
        #     unswitched writer.
        #   * JSONL ONLY (`EntityMutated` is not in _GRAPH_EVENT_TYPES) — the
        #     replay journal carries the honest hard-delete record, whose fold is
        #     `_delete_entity_by_id`. No `state`: the recorded shape carries
        #     "nothing for a delete". (#9207: that record is appended ABOVE,
        #     AFTER the `DETACH DELETE` proves it landed — it follows the write
        #     it records, so it can never claim a destruction that did not
        #     happen.)
        #
        # `PointRetracted` therefore stays AFTER the write it reports: it is the
        # `:GraphEvent` store's statement of what this call DID (hence the
        # unconditional `deleted: true` marker), so stating it before the
        # `DETACH DELETE` would publish a destruction that may not happen. An
        # append that refuses (or that LANDS and then raises — the record is then
        # UNCONFIRMED, never known-absent, #5612) leaves this line unreached, so
        # no subscriber row is published for a destruction whose record this call
        # cannot confirm.
        self._emit_event("PointRetracted", _deleted_point_payload(id))
        # Tag GC (#485): delete orphaned :Tag nodes (no incoming TAGGED edges).
        # Idempotent — DETACH DELETE leaves count-0 tags behind that would
        # otherwise accumulate in list_tags.
        if has_tag_edges:
            # #7405: the orphan-tag GC is swept through the same retry. The
            # statement re-derives "orphan" from the graph at issue time and a
            # DELETE of an already-deleted tag is a no-op, so a re-issue after
            # a non-landing ABORT cannot double-delete or remove a live tag.
            self._graph_write_with_retry(
                lambda: proj.g.query(
                    "MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t"),
                what="delete_point orphan tag GC",
            )
        # Dreaming (#85): deletion changes the graph structure around
        # neighbors — mark the pre-captured neighbors dirty (#1916) so the
        # reverse-BFS has surviving edges to traverse (the deleted point's
        # own edges are already gone) and a dream fires.
        self._mark_dirty([id, *neighbor_ids])
        # Epic 903-C4 (#1242): a deleted operator/claim changes the factor
        # graph — surviving neighbors' edges carry messages computed under
        # the OLD graph. Full drop (delete is rare; cheap). P2-review: the
        # equivalence gate caught a 0.31 drift WITHOUT this (stale seeds
        # reused across the deleted factor's boundary).
        self._get_ep().invalidate_messages()
        return True
