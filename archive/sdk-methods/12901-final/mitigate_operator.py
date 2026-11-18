# Archived sdk-method `mitigate_operator` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: mitigate_operator left the public SDK surface — privatised (body survives as `_mitigate_operator`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'mitigate_operator'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def mitigate_operator(self, id: str, reason: str, strength: float = 0.5,
                          credibility: str | int | float | None = None) -> dict:
        """Create a mitigation Point that modulates an operator's edge strength.

        MITIGATION STRENGTH SEMANTICS — single source of truth is the
        tortoise/weights.py module docstring (#2315; product decision
        2026-09-07: mitigation is a GRADED DAMPENER, not a refutation).
        Sanctioned band: **[0.10, 0.50]** — 0.10 minor caveat (weakest),
        0.30 significant limitation, 0.50 major counter-evidence
        (STRONGEST; never >0.50 — that would invert the claim, use NAND).
        Formula: ``w_eff = w * (1 - strength)`` — EP reads
        ``mitigation_strength`` via ``compute_operator_weight`` and reduces
        the operator's effective weight (weights.py applies the multiply;
        a 0.50 mitigation keeps 50% of the weight, so the operator is
        dampened, never refuted). Strength is NOT how true the reason is
        and is NOT fused into the mitigation point's Beta prior (#2199
        knock-on decision 3 — the number modulates the OPERATOR's weight;
        the mitigation point's own prior stays a calibrated live evidence
        point). This SDK range-checks strength to 0-1 for legacy
        writers; the decide layer clamps to the band and weights.py clamps
        defensively on read.

        SCHEMA RULE (ontology §3.9): the ``mitigated_by`` edge can ONLY
        originate from an ``is_operator:true`` Point — this method is the
        single writer gate and enforces it below (raises for non-operators);
        generic create_edge cannot write the predicate (allowlist).

        Args:
            id: Operator Point ID to mitigate.
            reason: Why the edge is weaker than it appears.
            strength: dampening strength in [0.10, 0.50] (0.50 = strong;
                see weights.py module docstring) — clamped to the band by
                the decide layer and by EP's read path.
            credibility: optional plain-language starting belief for the
                mitigation reason itself (gold/high/medium/low/unverified,
                T0-T4 form, or numeric 0-4) — a VALIDATED LABEL, stamped
                'set-by-author'. Per the #398 owner ruling (2026-10-12) the
                word does NOT select the ladder prior: it is ONE weak evidence
                body that ADDS to the mitigation's baseline, so the mitigation
                starts at the #2199 system default (Beta(3,1)) plus the weak
                body and never scores below the same mitigation unlabelled.
                Default None → the #2199 system starting belief
                ('medium', Beta(3,1), provenance 'system-default') so the
                mitigation point is a calibrated live evidence point and the
                decide flow ranks on the first try (no CalibrationError).

        Raises ``ValueError`` if id not found, not an operator, strength out of
        [0, 1], or credibility outside the ladder — AND
        ``UnaddressableClaimError`` (a ``ValueError`` SUBCLASS, ``exceptions.py``,
        #9754) when the operator's existing mitigation Point carries no ``id``:
        the node this call would rewrite cannot be addressed at all, so refusing
        is the only honest answer (it used to return ``{}`` — a false success).
        The two are distinguishable BY CLASS, which is the point of the subclass:
        a caller that can skip one unrepairable node and continue catches the
        subclass (``commit_ops.apply_payload_operators`` does exactly that, per
        item), while a caller that must abort on a defect in its OWN record
        leaves the parent uncaught.
        Idempotent: a second call updates the existing CURRENT mitigation
        (reason + strength), does not create a duplicate. A mitigation the graph
        records as terminal (retracted/superseded/outdated/archived) is never
        revived in place — a re-assertion creates a NEW mitigation Point, since
        retraction is terminal (#432/#2422). A REDELIVERY through ``ingest()``
        is not a re-assertion, so it writes nothing in that state (see
        ``_apply_ingest_mitigation``). A ``draft`` mitigation is not TERMINAL —
        ``live.CURRENT_POINT_STATUS_VALUES`` includes draft, so it IS the node
        this lookup finds: the call updates it in place and does NOT promote it
        (#1088 — no retro-promotion), which means a draft-only operator accepts
        the call and does not dampen (a draft is CURRENT but EP-INERT:
        ``live._live_only`` excludes it from the dampener read). The reply
        carries its ``status`` so a caller can tell those two arms apart instead
        of reading the update as "this now dampens".

        The created mitigation Point is a CURRENT point (`status='live'`,
        #5089/#9962) — the `weights.py` dampener read prices `m.status`, so a
        terminal mitigation stops dampening and a retracted one restores the
        operator's undamped weight without deleting it. It is born `live` on
        EVERY route, `ingest()` under the default gated policy included: this
        door is the single-writer mitigation gate, not the bundle's point
        policy, and a mitigation that does not dampen is not an assertion.
        `docs/INGEST_CONTRACT.md` §2.1 records that exemption (#9962).
        """
        # #4647: guard before the range check — ``0 <= Decimal('NaN') <= 1``
        # raises ``decimal.InvalidOperation``, so the check would raise an
        # unnamed wrong-class error ahead of this guard (cycle-8 P2).
        _reject_unrepresentable_number("strength", strength)
        if not 0 <= strength <= 1:
            raise ValueError(f"strength must be 0-1, got {strength}")
        point = self.get_point(id)
        if not point:
            raise ValueError(f"Operator {id!r} not found")
        if not point.get("is_operator"):
            raise ValueError(f"Point {id!r} is not an operator")
        from tortoise.source_credibility import credibility_prior
        # Resolve the author-stated belief up front so a bad ladder word
        # fails before any write (never a silent Beta(1,1) fallback, #7478).
        author_prior = None
        if credibility is not None:
            # Validate the label (the ladder vocabulary is kept) but do NOT use
            # the ladder prior: #398 owner ruling 2026-10-12 makes an authored
            # assertion ONE weak evidence body, the same whoever wrote it. That
            # body ADDS to the mitigation's baseline (the #2199 system default
            # Beta(3,1)), so an explicit word can never score below the same
            # mitigation left unlabelled.
            from tortoise.source_credibility import (
                AUTHOR_ASSERTION_WEAK_PC,
                canonical_tier,
            )
            if credibility_prior(credibility) is None:
                raise ValueError(
                    f"Unknown credibility {credibility!r}. Ladder words: "
                    "gold / high / medium / low / unverified "
                    "(or the T0-T4 / numeric forms)."
                )
            _mit_default = credibility_prior(DECIDE_DEFAULT_CREDIBILITY)
            author_prior = (_mit_default[0] + AUTHOR_ASSERTION_WEAK_PC,
                            _mit_default[1])
        # Idempotency: check for an existing CURRENT mitigation.
        # #9962: a TERMINAL mitigation (retracted / superseded / outdated /
        # archived, or the legacy `outdated` flag) is EXCLUDED from this
        # lookup. The dampener read prices `m.status`, so updating a terminal
        # node in place would leave it dead: the call would report success and
        # dampen nothing — the same silent no-op this door's missing status
        # created in the first place (measured: mitigate → retract →
        # mitigate(strength=0.3) left the weight at 1.0000 with the node still
        # `retracted`). Retraction is TERMINAL (#432/#2422), so the node is NOT
        # revived in place: a re-assertion mints a NEW mitigation Point and the
        # retracted one keeps its state and its history. The read composes
        # several mitigations by max strength (#2315), so after the
        # re-assertion exactly the live one prices the operator.
        proj = self._get_proj()
        existing = proj.g.query(
            "MATCH (op:Point {id:$id})-[r:mitigated_by]->(m:Point) "
            f"WHERE {_terminal_excluded('m.status')} "
            "RETURN m.id",
            params={"id": id},
        ).result_set
        if existing:
            mid = existing[0][0]
            # #9754: an id-less mitigation Point (a hand-written / imported
            # shape) makes ``mid`` None, and EVERY write in this branch is
            # id-keyed — ``update_point``, the baseline writer and the word
            # stamp all match zero rows. #9262 guarded this branch to keep the
            # historical outcome, but that historical outcome WAS the defect:
            # ``return self.get_point(None)`` → ``{}``, an empty success the
            # caller cannot distinguish from "there is no mitigation" (the
            # reason/strength update did not land and no baseline was written,
            # so EP's calibration gate then refuses the operator while
            # ``calibrate_summary``'s advice — ``set_point_baseline(None, …)`` —
            # cannot be executed). Measured: the call returns ``{}``.
            # (An earlier version of this comment added "and leaves
            # ``_evidence[None] = (3.0, 1.0)``" — false on both trees: #9262
            # returned before ``set_point_baseline`` was ever reached, and
            # ``_evidence`` is written only by ``create_point``'s baseline,
            # ``set_point_baseline`` after its representability guard, and
            # ``_hydrate_evidence`` — none of which this path enters. Dropped
            # rather than carried forward: tests/test_calibration.py states the
            # correct reading in this same change.)
            #
            # The repair is option 1 of #9754: REFUSE with the real cause. The
            # message names the MITIGATION, never the operator the caller
            # passed — the operator is addressable and its id is actionable by
            # nobody here, so a refusal about it names a node the caller cannot
            # repair. ``UnaddressableClaimError`` is the existing shape for
            # "a node the run must price carries no usable id" (#9275), so
            # callers already have one word for this class.
            # ``apply_payload_operators`` classifies it per item (#9754): a
            # single unaddressable mitigation must not abort a commit's whole
            # write loop.
            if mid is None:
                from .exceptions import UnaddressableClaimError
                raise UnaddressableClaimError(
                    f"Operator {id!r} carries a mitigation Point with no id "
                    "(a hand-written or imported shape), so the "
                    "re-mitigation cannot be written: every write on this "
                    "path is keyed on the mitigation's id. No reason, "
                    "strength or baseline can be stored on it, and the "
                    "calibration gate will keep refusing the operator while "
                    "its advice (`set_point_baseline(<mitigation id>, …)`) "
                    "has no id to name. Repair the graph: give the existing "
                    "mitigation Point an id, or drop that unaddressable "
                    "mitigation and call mitigate_operator again to mint an "
                    "addressable one (#9754).")
            self.update_point(mid, content=f"[MITIGATION] {reason}",
                              mitigation_strength=strength)
            # An explicit belief on the idempotent-update path re-baselines
            # (never silently ignored). No credibility → keep whatever the
            # mitigation already carries — EXCEPT a legacy pre-#2199
            # mitigation with NO baseline at all: that is the exact
            # uncalibrated-live-evidence case the decide flow must not leave
            # behind (it would trip the fail-closed CalibrationError on the
            # documented first-run EP). Heal it with the system default so a
            # legacy mitigation becomes rankable on the next re-mitigation.
            if author_prior is not None:
                self.set_point_baseline(
                    mid, author_prior[0], author_prior[1],
                    source=BASELINE_SOURCE_SET_BY_AUTHOR)
                # #6151 review P2: persist the word LABEL and the recorded
                # #2199 birth marker. Without the label the recompute guard
                # (`_apply_source_inheritance`) skips this point forever, so a
                # word-bearing mitigation is a self-declaration NO refutation
                # can move (measured: a T1 source left ep_alpha at 3.1). The
                # mitigation's base ALWAYS includes the #2199 default
                # (author_prior is the default Beta(3,1) + the weak body), so
                # the marker is accurate.
                # (#6151 review P3) This is an EXISTING point, so unlike the
                # CREATE path below there is no PointAdded snapshot to carry
                # the two props — see `_stamp_authored_word`'s JOURNAL note:
                # they are lost on rebuild_all. Recorded, not silently widened.
                self._stamp_authored_word(
                    mid, label=canonical_tier(credibility),
                    system_default=True)
            elif not self.get_point(mid).get("baseline_set"):
                alpha, beta = credibility_prior(DECIDE_DEFAULT_CREDIBILITY)
                self.set_point_baseline(
                    mid, alpha, beta,
                    source=BASELINE_SOURCE_SYSTEM_DEFAULT)
                # The healed legacy mitigation carries the recorded #2199
                # default too (same birth fact `create_point` records).
                self._stamp_authored_word(mid, system_default=True)
            # #7505: mark the MITIGATION only — NOT the operator. What the
            # re-mitigation needs is the operator's INPUTS recomputed, and
            # `_reverse_bfs_neighbors` already returns them as claims through
            # the mitigation back-link branch `(m)-[:IMPL]->(op)` (#6599). The
            # operator id itself is not a factor participant, so no converged
            # pass can subtract it from `_dirty_roots` (that is #2422's class),
            # and marking it pinned `_auto_dream_mode()` to 'local' forever on
            # every mitigation. The in-process flow hid the missing mark behind
            # the operator's own creation-time ep_dirty flag; `ep_dirty` is
            # deliberately NOT journaled (#5166), so a rebuilt/replayed graph
            # has no such mask — the INPUT marks are what carry the
            # re-mitigation, and they are computed, not assumed (#5566 review
            # P2).
            self._mark_dirty([mid])
            return self.get_point(mid)
        # Create new mitigation Point
        mid = ulid()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        # #4647: this new-mitigation CREATE writes `strength` straight into
        # `mitigation_strength` (raw parameter), bypassing _sanitize_props —
        # the idempotent branch above routes through update_point and IS
        # guarded. EP weights read this value, so an inexact Decimal here
        # silently corrupts the operator weight. Guard before the write.
        _reject_unrepresentable_number("strength", strength)
        # #7405: `mid` is minted once above, every property is a fixed
        # parameter, and the abort means the statement did not land — so a
        # re-issue cannot mint a second mitigation Point. Bare CREATE ⇒
        # `retryable_aborted_write` only.
        # #9962 / #5089: the mitigation is born `status:'live'` — a CURRENT
        # point, which is what the dampener read prices (`weights.py` honours
        # `m.status`, so a status-less node would lean on the legacy NULL-is-
        # live rule forever) and what every other write path already emits
        # (`create_point` defaults to draft|live). Measured before the fix:
        # `get_point(mid)['status'] is None`, so the node was neither staged
        # nor current for any reader of `status` — the #6107 root, on this
        # door. It is `live`, not `draft`: the mitigation is created by an
        # explicit operator-mitigating call, exactly like the IMPL/NAND
        # operator it damps (this is NOT an extraction staging path).
        self._graph_write_with_retry(
            lambda: proj.g.query(
                "CREATE (m:Point {id:$id, content:$c, pointKind:'statement', "
                "mitigation_strength:$s, is_operator:false, status:'live', "
                "createdAt:$now, updatedAt:$now})",
                params={"id": mid, "c": f"[MITIGATION] {reason}", "s": strength, "now": now},
            ),
            what="mitigate_operator CREATE(:Point)",
        )
        # Bidirectional link: mitigation Point -[:IMPL]-> operator, operator <-[:mitigated_by]- mitigation
        # #7405: one statement, two fixed endpoints, abort ⇒ neither edge
        # landed, so a re-issue cannot double the pair.
        self._graph_write_with_retry(
            lambda: proj.g.query(
                "MATCH (m:Point {id:$mid}), (op:Point {id:$oid}) "
                "CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)",
                params={"mid": mid, "oid": id},
            ),
            what="mitigate_operator mitigation edges",
        )
        # #2199: mitigation reasons are human-authored judgment — give the
        # fresh live point its explicit, provenance-recorded starting belief
        # (author override or the documented system default) so the decide
        # flow's EP run never trips the fail-closed CalibrationError.
        if author_prior is not None:
            alpha, beta = author_prior
            source = BASELINE_SOURCE_SET_BY_AUTHOR
        else:
            alpha, beta = credibility_prior(DECIDE_DEFAULT_CREDIBILITY)
            source = BASELINE_SOURCE_SYSTEM_DEFAULT
        self.set_point_baseline(mid, alpha, beta, source=source)
        # #6151 review P2/P3: persist BOTH the word LABEL (so a later real
        # source displaces the weak body through the recompute instead of the
        # mitigation freezing) and the recorded #2199 birth marker (so the
        # recompute re-injects the default as the base rather than replacing
        # it). Server-owned write (`baseline_system_default` is rejected from
        # caller props); it runs BEFORE the PointAdded emit below so both props
        # ride the journal snapshot and survive a rebuild.
        if author_prior is not None:
            self._stamp_authored_word(
                mid, label=canonical_tier(credibility), system_default=True)
        else:
            self._stamp_authored_word(mid, system_default=True)
        # Dreaming (#85): new mitigation + IMPL edge change propagation.
        # #7505: `[mid]` only — same reasoning as the update path above. `mid`
        # reaches the operator's inputs through the reverse-BFS back-link
        # branch and is itself swept as a neutral link (it forms no factor);
        # the OPERATOR is deliberately not marked, because an operator root is
        # not sweepable and would pin the dream mode to 'local'.
        self._mark_dirty([mid])
        # #548: emit events for rebuild parity
        self._emit_event("PointAdded", point=self.get_point(mid))
        # Emit OperatorAdded so the IMPL edge (mitigation → operator) is
        # recreated on replay. The payload's `operator` descriptor is ALSO the
        # carrier `projection/edges.py::_create_edges` keys on to rebuild the
        # canonical reverse `(op)-[:mitigated_by]->(m)` (#5048): a mitigation is
        # the NON-operator Point carrying that descriptor, so this ONE record
        # reconstructs both halves. The mitigation Point's live identity
        # (`is_operator` false / no `op_type`) survives replay through
        # `_upsert_point_props`'s explicit-False arm, and a revised strength
        # folds through `_revise_point` / `_apply_one`.
        mit_point = self.get_point(mid)
        mit_point["operator"] = {"op_type": "IMPL", "inputs": [id]}
        self._emit_event("OperatorAdded", point=mit_point)
        return self.get_point(mid)
