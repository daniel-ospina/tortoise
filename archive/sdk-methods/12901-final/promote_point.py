# Archived sdk-method `promote_point` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: promote_point left the public SDK surface — privatised (body survives as `_promote_point`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'promote_point'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def promote_point(self, point_id: str) -> dict:
        """Reviewer-gated draft→live promotion (plan §6.1, J-5, DE2E-8).

        The ONLY path a draft extraction Point may go live — never via the
        SDK #131 edge auto-promotion for extraction paths
        (`create_operator(promote_source=False)`, #780).

        Semantics (all responses include id + status + promoted + blocked):
          - already live → NO-OP {promoted: False, blocked: False,
            reason: "already_live"} — DE2E-N9
          - operator node → blocked {blocked: True, reason: "is_operator"} —
            operators only go live via the R16 endpoint gate (matching
            retract_point's operator rejection)
          - not draft/live (terminal — retracted/superseded/outdated/archived,
            or the legacy `deprecated` — or an absent status) → blocked
            {blocked: True, reason: "not_draft"}
          - Point belongs to a QUARANTINED batch → blocked
            {blocked: True, reason: "batch_quarantined", batch_id} — quarantine
            is batch-level (plan §3): the batch's Points stay draft until the
            W-3 re-run passes (EpSafeCommit recovery loop)
          - otherwise → status draft→live, `reviewed: true` derived flag set,
            PointPromoted event emitted, and R16 zombie prevention:
            incident DRAFT operator nodes (status 'draft' — the post-#780
            extraction shape) are promoted to live ONCE ALL their endpoint
            Points are live, so a contradiction never stays a dead draft
            operator after its claims go live.

        NOTE (review #944/#990): the quarantine lock is a read-then-CAS —
        FalkorDBLite has no EXISTS subqueries, so the batch check cannot fold
        into the CAS. #990 added a post-CAS re-check that SURFACES a lost
        race instead of hiding it: when a quarantine lands between the read
        and the CAS, the promotion completes but the response carries
        ``race_detected: true`` + ``race_warning`` (the point is live while
        its batch is quarantined — an operator action is required).

        Raises ValueError if the Point does not exist.
        """
        proj = self._get_proj()
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN properties(n)",
            params={"id": point_id},
        ).result_set
        if not row:
            raise ValueError(f"No point {point_id!r}")
        point = row[0][0]
        status = point.get("status")

        # Operators go live ONLY via the R16 endpoint gate — promoting a
        # draft operator directly would create the live-operator-with-draft-
        # endpoints state R16 exists to prevent (review #944).
        if point.get("is_operator") or point.get("op_type"):
            return {"id": point_id, "status": status, "promoted": False,
                    "blocked": True, "reason": "is_operator"}
        # DE2E-N9: already-live promote → no-op, no error.
        if status == "live":
            return {"id": point_id, "status": "live", "promoted": False,
                    "blocked": False, "reason": "already_live"}
        # Terminal states cannot be promoted back to live.
        # ⚠️ RESIDUAL, MEASURED, DELIBERATELY NOT FIXED HERE (#12586): the test
        # below reads ``status`` ALONE, and the legacy ``outdated=true`` flag is
        # terminal on its own (§5: "Terminal on every read surface and for every
        # lifecycle transition"). A draft point invalidated before it was ever
        # promoted (``invalidate_point`` leaves ``status='draft'`` and sets the
        # flag; a pre-#9649 raw ``restatus`` after a supersede produces the same
        # row — see ``test_6239_promote_after_supersede.py``, whose fixture
        # writes exactly it. ``supersede_point`` itself writes
        # ``status='superseded'`` + the flag, which this guard DOES block) is
        # therefore promoted to
        # ``['live', outdated=true]`` through THIS door — the same
        # two-facts-disagree state #12586 closed on ``update_point``, measured on
        # the same tree. It is NOT fixed in that change on purpose: this verb is
        # a separate door with its own return contract (``blocked``, never a
        # raise) and its own cross-engine parity pin
        # (``test_6239_promote_after_supersede.py``, whose raw-restatus fixture
        # writes exactly this row), so the fix belongs to its own change with its
        # own evidence. #12586's thread carries the measurement.
        if status != "draft":
            return {"id": point_id, "status": status, "promoted": False,
                    "blocked": True, "reason": "not_draft"}

        # Quarantine lock (batch-level): a quarantined batch's Points stay
        # draft until re-review (W-3 recovery).
        batch_id = point.get("batch_id")
        if batch_id:
            from .mining import batch_status  # lazy: no module-level sdk↔mining cycle
            bs = batch_status(proj, batch_id)
            if bs is not None and bs["status"] == "quarantined":
                return {"id": point_id, "status": status, "promoted": False,
                        "blocked": True, "reason": "batch_quarantined",
                        "batch_id": batch_id}

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        # CAS-guarded SET (concurrent promote can't double-fire); reviewed is
        # the DERIVED reviewer flag (plan §3 — no new stored status).
        # #7405: the CAS promotion rides the bounded write retry. The abort is
        # a non-landing abort, so a re-issue re-evaluates the SAME
        # `WHERE n.status = 'draft'` predicate against unchanged state and lands
        # exactly once; if a concurrent promote won the CAS instead, the empty
        # result set is the already-existing "raced" branch below, unchanged.
        # The witness separates THOSE two from the third case — a re-issue that
        # lands nothing because the rebuild has not replayed the node yet —
        # which would otherwise be read as `not_draft` by that branch and
        # consumed as a real refusal (the ONE consumer that reads the answer as
        # a decision is `VerdictLayer.decide(..., VERDICT_KEEP)`, which rolls
        # its review stamps back on `blocked`).
        def _cas_witness(result) -> bool:
            """A zero-row CAS re-issue, classified by the node's STATUS.

            The CAS matches ONLY the literal ``'draft'`` (no ``IS NULL`` arm,
            unlike ``update_point``'s promote arms and unlike
            ``_promote_witness``'s table), so "which statuses explain a zero
            row" is exactly "not ``draft``":

            - absent → False: the rebuild has not replayed the node, so a
              re-issue cannot apply and the abort must surface on exhaustion.
              A `not_draft` verdict here would name a state nobody observed.
            - present, status ``draft`` → False: that does NOT explain a zero
              row, so the re-issue has not landed; keep retrying rather than
              manufacture a refusal for a point that is still promotable.
            - present, any other status (a concurrent promote, a terminal
              transition, or a NULL status — `null = 'draft'` is null, not
              true, so it too explains the zero row) → True: the `raced`
              branch below reads the real status and answers accurately.
            """
            if result.result_set:
                return True
            rows = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.status",
                params={"id": point_id},
            ).result_set
            if not rows:
                return False
            return rows[0][0] != 'draft'

        r = self._graph_write_with_retry(
            lambda: proj.g.query(
                "MATCH (n:Point {id:$id}) WHERE n.status = 'draft' "
                "SET n.status = 'live', n.reviewed = true, n.promotedAt = $now, "
                "n.updatedAt = $now RETURN n.id",
                params={"id": point_id, "now": now},
            ),
            what="promote_point CAS SET",
            witness=_cas_witness,
        )
        if not r.result_set:
            # Raced with a concurrent promote — re-read and report the outcome.
            re = self.get_point(point_id)
            if not re:
                # The node is GONE (deleted, or a second replacement between
                # this call's two reads). That is not a state verdict, and
                # `{}` would otherwise read as `status: None` → `not_draft`,
                # naming a state nobody observed. Report the absence the entry
                # contract already reports for the same condition.
                raise ValueError(f"No point {point_id!r}")
            if re.get("status") == "live":
                return {"id": point_id, "status": "live", "promoted": False,
                        "blocked": False, "reason": "already_live"}
            return {"id": point_id, "status": re.get("status"),
                    "promoted": False, "blocked": True,
                    "reason": "not_draft"}

        # Epic 903-C2 (#1240): a status→live transition invalidates the
        # promoted claim's EP-derived confidence — mark it dirty (plus its
        # operator neighborhood via the 1-hop reverse BFS) so the next dream
        # re-derives it. Verified gap: promote_point did NOT call _mark_dirty
        # (the W1 lifecycle contract says every status→live transition must),
        # which left a promoted claim permanently excluded from dreaming.
        self._mark_dirty([point_id])

        # Post-CAS race re-check (#990): the quarantine lock is a
        # read-then-CAS (two statements — FalkorDBLite has no EXISTS
        # subqueries), so a quarantine landing between the batch_status read
        # and the CAS can race a promotion through. Surface the race instead
        # of hiding it: the point is live, but the batch is quarantined.
        race_detected = False
        if batch_id:
            from .mining import batch_status
            bs = batch_status(proj, batch_id)
            if bs is not None and bs["status"] == "quarantined":
                race_detected = True
                _logger.warning(
                    "promote_point: batch %s quarantined concurrently with "
                    "promotion of %s (TOCTOU race, #990) — point is live but "
                    "batch is quarantined",
                    batch_id, point_id,
                )

        # Variant C (#784): a MERGED content-dedup candidate whose prior was
        # LIVE at approve time is wired NOW — the candidate is live, so the
        # "already decided" IMPL becomes a live→live link (exactly one).
        # The exists-guard is OPERATOR-MEDIATED (create_operator writes
        # (op)-[:IMPL]->(endpoint) edges — direct-edge counts are always 0,
        # #784 review) and the prior is validated BEFORE the CAS so a stale
        # dedup_target_id cannot produce a live point with no event.
        dedup_wired = False
        dedup_target_valid = True
        prior = point.get("dedup_target_id")
        if point.get("dedup_reviewed") == "merge" and prior:
            prow = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.id",
                params={"id": prior},
            ).result_set
            if not prow:
                dedup_target_valid = False
                _logger.warning(
                    "promote_point: dedup target %s for %s no longer exists — "
                    "skipping the alreadyDecided wire", prior, point_id)
        if point.get("dedup_reviewed") == "merge" and prior and dedup_target_valid:
            exists = proj.g.query(
                "MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->"
                "(:Point {id:$cand}), "
                "(op)-[:IMPL]->(:Point {id:$prior}) RETURN count(op)",
                params={"cand": point_id, "prior": prior},
            ).result_set
            if not exists or exists[0][0] == 0:
                self.create_operator(
                    "IMPL", point_id, [prior], label="alreadyDecided",
                    direction="unidirectional")
                dedup_wired = True

        # Temporal wiring at promotion (W-4, #786): a MERGED temporal
        # candidate whose prior was LIVE at extraction gets its NAND now
        # (live→live), or — for an explicit replacement — the prior is
        # superseded (CORRECTS + outdated:true). Targets are validated
        # BEFORE the CAS (mirroring the dedup block) so a stale/terminal
        # target can never produce a live point with no event (#1080 review).
        temporal_wired = False
        superseded = False
        # #5365: unapplied supersessions during this promotion — see the
        # temporal_replacement loop below.
        supersede_skipped: list[dict] = []
        # #12067: temporal NANDs the CLOSED MECHANISM SET refused — the pair
        # already carried another mechanism, so the wire was dropped. Named in
        # the result below rather than left in a log line (see the `except`).
        temporal_nand_refused: list[dict] = []
        temporal_targets = point.get("temporal_target_ids") or []
        if point.get("temporal_target_id") and point.get("temporal_target_id") not in temporal_targets:
            temporal_targets.append(point["temporal_target_id"])
        live_targets: list[str] = []
        if (point.get("temporal_candidate")
                and point.get("temporal_reviewed") == "merge"):
            for tgt in temporal_targets:
                # #7822: the SAME fan-out statement the lifecycle guard runs,
                # classified over EVERY node carrying the target id. The
                # pre-#7822 probe read ``trow[0]``: on a duplicate target id
                # a live sibling returned first let this path NAND-wire (or
                # hand to ``supersede_point``) a target the guard refuses on
                # its terminal sibling — and the verdict depended on server
                # row order. A target is live only when NO sibling is dead.
                trows = proj.g.query(
                    LIFECYCLE_FANOUT_QUERY, params={"id": tgt},
                ).result_set
                if not trows:
                    # MISSING node (stale target) — distinct from an unset
                    # status node, which is live by the canonical read model
                    # (#1080 round-2 review: missing targets crashed the
                    # NAND create AFTER the CAS).
                    _logger.warning(
                        "promote_point: temporal target %s for %s no longer "
                        "exists — skipping the wire", tgt, point_id)
                    continue
                # #12586: the picker is `live.first_terminal_row` — the ONE
                # home for "which row made this fan-out terminal?". This probe
                # had the third hand-rolled copy of that `min`-by-stable-key
                # (sorted(...)[0] selects the same element), which is how the
                # same row comes to be named two ways in two messages.
                hit = first_terminal_row(trows)
                if hit is None:
                    live_targets.append(tgt)
                else:
                    _logger.warning(
                        "promote_point: temporal target %s for %s is %s — "
                        "skipping the wire", tgt, point_id,
                        terminal_row_marker(hit))
        if live_targets and point.get("temporal_replacement"):
            for tgt in live_targets:
                try:
                    self.supersede_point(tgt, point_id)
                    superseded = True
                except ValueError as exc:
                    # #5365: the same fail-open skip as commit_ops — the
                    # promotion PROCEEDS without superseding, so the caller
                    # must be able to SEE that, not just find a log line.
                    # Reported in the result below.
                    supersede_skipped.append({"target": tgt,
                                              "reason": str(exc)})
                    _logger.warning(
                        "promote_point: supersede of %s failed for %s: %s",
                        tgt, point_id, exc)
        elif live_targets:
            for tgt in live_targets:
                exists = proj.g.query(
                    "MATCH (op:Point {is_operator:true})-[:NAND]->"
                    "(:Point {id:$cand}), "
                    "(op)-[:NAND]->(:Point {id:$prior}) RETURN count(op)",
                    params={"cand": point_id, "prior": tgt},
                ).result_set
                if exists and exists[0][0] > 0:
                    continue
                # Cross-guard: never NAND a pair already linked by an
                # alreadyDecided IMPL (dedup+temporal conflict, #1080).
                dup = proj.g.query(
                    "MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->"
                    "(:Point {id:$cand}), "
                    "(op)-[:IMPL]->(:Point {id:$prior}) RETURN count(op)",
                    params={"cand": point_id, "prior": tgt},
                ).result_set
                if dup and dup[0][0] > 0:
                    _logger.warning(
                        "promote_point: %s already linked to %s by an "
                        "alreadyDecided IMPL — skipping temporal NAND",
                        point_id, tgt)
                    continue
                try:
                    self.create_operator("NAND", point_id, [tgt])
                    temporal_wired = True
                except ValueError as exc:
                    # A racing delete between validation and create must
                    # never leave a live point without its event (#1080).
                    _logger.warning(
                        "promote_point: NAND wiring to %s failed for %s: %s",
                        tgt, point_id, exc)
                    # ⛔ EXCEPT THE CLOSED-SET REFUSAL, WHICH IS NOT A LOST
                    # RACE. The point is live and the temporal NAND was
                    # DROPPED, so a caller reading only `temporal_wired` cannot
                    # tell it from a race — the same fail-open SKIP the
                    # `supersede_skipped` arm eight lines above exists for
                    # (#5365: "the caller must be able to SEE that, not just
                    # find a log line"). Reported as a counted, listed field;
                    # the promotion still succeeds.
                    if "mechanism set is CLOSED" in str(exc):
                        temporal_nand_refused.append({"candidate": point_id,
                                                      "target": tgt})

        # R16 zombie-operator prevention: promote incident draft operators
        # once ALL their endpoint Points are live.
        promoted_ops = self._promote_incident_operators(proj, point_id, now)

        self._emit_event("PointPromoted", point=self.get_point(point_id))
        result = {"id": point_id, "status": "live", "promoted": True,
                  "reviewed": True,
                  "operator_nodes_promoted": promoted_ops}
        if dedup_wired:
            result["dedup_wired"] = True
        if temporal_wired:
            result["temporal_wired"] = True
        if superseded:
            result["superseded"] = True
        if supersede_skipped:
            # #5365: a promotion that could not supersede its temporal target
            # is a PARTIAL promotion. Surfaced as a counted, listed field so a
            # caller sees the skip without parsing logs (mirrors the
            # `race_detected` precedent above: a lost race is reported, not
            # hidden).
            result["supersede_skipped"] = supersede_skipped
            result["supersede_skipped_count"] = len(supersede_skipped)
        if temporal_nand_refused:
            # #12067: the temporal NAND the CLOSED MECHANISM SET refused (the
            # pair already carries another mechanism). Same contract as
            # `supersede_skipped` above and the same key name the mining report
            # uses, so a caller reading `temporal_wired` absent can tell a drop
            # from a race; the promotion itself stands (#1080).
            result["temporal_nand_refused"] = temporal_nand_refused
            result["temporal_nand_refused_count"] = len(temporal_nand_refused)
        if race_detected:
            result["race_detected"] = True
            result["race_warning"] = (
                "batch quarantined concurrently with promotion — point is live")
        return result
