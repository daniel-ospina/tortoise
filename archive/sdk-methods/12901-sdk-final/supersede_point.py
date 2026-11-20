# Archived sdk-method `supersede_point` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_supersede_point`; recorded destination `supersede_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'supersede_point'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    @_declares_live_write_lane("supersede_point")
    def supersede_point(self, old_id: str, new_id: str,
                        *, valid_from: str | None = None,
                        valid_to_precision: str | None = None) -> dict:
        """Atomically replace old Point with new — CORRECTS edge + outdated flag + edge transfer.

        §17.1 LANE (#9124): JOURNAL-FIRST, declared — with ONE stated residual
        that this declaration does NOT close. The `PointSuperseded` record is
        appended before any mutating statement (all ~15 of them follow it), so
        the method carries `@_declares_live_write_lane`. The residual is that
        the append's RETURN is not checked here, so a FAILED append still
        proceeds to the cache: the lane is honest about ORDER and says nothing
        about CONFIRMATION. The doors that gate on a per-call append outcome do
        so ARM BY ARM, not door by door: `create_point` and `update_point`'s
        NON-promote arm raise `WriteNotLandedError` on `_record_committed is
        False`, and `delete_point` raises on an append that does not confirm —
        AFTER a `DETACH DELETE` that already landed (#9207; the cache write is
        that door's commit point, so its append can only report, never refuse).
        `update_point`'s PROMOTE arm (its record
        is emitted after the write, return discarded), `invalidate_point` and
        `retract_point` discard the append's return exactly as this door does,
        so the same residual is carried by FOUR doors, not one. Closing it is
        part 2 of #9124 (it changes the failure contract, not the order).

        E6 (#1538) D2 — bi-temporal validity windows: the supersession ALSO
        stamps the old point's window END (``validTo`` = the successor's
        ``validFrom`` — contiguous Graphiti windows: the old fact stops
        being true exactly when the new fact became true) and its
        transaction-time expiry (``expiredAt`` = now). The successor's
        window START is its own ``validFrom`` (the create path, D3), or the
        optional ``valid_from`` kwarg when the caller knows it (else read
        the successor's ``validFrom``, fall back to its ``createdAt``, fall
        back to now — monotone, never a gap). Additive-only for callers that
        stay inside the predecessor's window: a successor whose resolved start
        sorts strictly BEFORE the predecessor's own ``validFrom`` is refused
        (``ValueError``, before any mutation — an inverted window is satisfiable
        by no query instant, so the old fact would be silently unreachable from
        every read surface; #4021). Equality (a zero-length predecessor window)
        is legal. The comparison needs an INSTANT on both sides: a resolved
        start that is itself unparseable is not compared (it names no instant,
        so a refusal would rest on a lexicographic accident rather than a
        comparison) — the orderability residual is closed on the WRITE path
        (#5360) for every route EXCEPT the deliberately-exempt numeric-epoch
        ``valid_from`` kwarg: an unorderable resolved start or end is otherwise
        normalised to an orderable instant rather than persisted. That kwarg is
        still ``str()``-ed and left unorderable (see ``_supersede_window_end``
        and ``docs/ONTOLOGY.md`` §4.7), so that ONE route can still write a
        bound ``_covers`` cannot order.

        The kwarg is a CLAIM about the successor's window start, so when the
        successor carries a stored ``validFrom`` the two must be parseable
        timestamps naming the SAME instant (compared by instant via
        ``_created_sort_key`` — the measure ``get_historical_knowledge``'s ``_covers``
        uses). A disagreement raises ``ValueError`` BEFORE any mutation: a
        predecessor ``validTo`` that disagrees either leaves a GAP (a query
        instant covered by neither window) or an OVERLAP (two covering
        candidates ⇒ ``ambiguous``). The kwarg is the SOLE source when the
        successor carries no stored ``validFrom`` (an undated successor)
        **unless it names no instant**: an unparseable STRING kwarg falls
        through to the successor's ``createdAt``, and that fallback itself
        requires an orderable value and otherwise lands on ``now`` (#5360). A
        numeric-epoch kwarg is deliberately exempt — see
        ``_supersede_window_end``. See docs/ONTOLOGY.md §4.7 (``validTo``).

        E-prec (#2350): ``valid_to_precision`` declares how precisely the
        predecessor's stamped END is known. ``'unknown'`` is the closed-unknown
        state — the window ENDED but at a time we cannot pin, so NO ``validTo``
        is fabricated (the 'half-honest dates are worse than none' rule) and
        the stored marker is what every read path reports as
        ``end_state='unknown'``. Otherwise the predecessor's end is the
        successor's RESOLVED start — the caller's ``valid_from`` kwarg when it is
        usable (it wins outright: the agreement guard forces instant equality
        whenever the successor has a stored start, and it is the SOLE source for
        an undated successor), else its own ``validFrom`` when that names an
        instant, else its ``createdAt``, else ``now`` — and it inherits the
        successor's ``validFromPrecision`` when, and only when, the resolved end
        names the successor's start instant: a declaration annotates a bound, it
        cannot summon one (#9244), so a fallback instant nobody declared a
        granularity for is stamped with no precision (#9729). A caller who wants
        a token on a fallback instant states ``valid_to_precision`` explicitly.

        Transfers all edges from the old point to the new point:
          - Operator edges (IMPL, NAND, hasPart) with idx, AND their reverse
            ``(support)-[:INPUT]->(operator)`` mirrors (#6180 — the mirror IS
            the operator's support source, so it must travel with its typed
            edge; see the 2a / 2a-INPUT blocks).
          - Plain structural edges (aboutSubject, aboutObject, aboutAction,
            aboutEvent, aboutPoint, aboutDocument, extractedFrom, etc.)
        Preserves edge type and idx (source vs target position). Every
        transfer MERGEs, so an edge the successor already carries (a caller
        wired the successor first) collapses onto the existing one instead of
        doubling. Leaves the old point outdated with only the CORRECTS edge
        from the new point. ``edges_transferred`` counts the PRIMARY edges
        (operator/direct/structural); the INPUT mirrors ride along uncounted.
        """
        from datetime import datetime, timezone
        proj = self._get_proj()
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        # ⛔ BOUND BEFORE THE GUARDS, NOT AFTER (#12914 re-review finding 3).
        # The driver renders a parameter through `quote_string` -> `v.replace(...)`
        # + a format, so the bytes it sends are NOT the operand's base content
        # when the operand is a `str` subclass. Rebinding here — above the source
        # guard, the successor comparison and the target guard — is what makes
        # "guard, probes and wire are the same bytes" true; sitting below the
        # source guard it left the split open, and measured: an `old_id` whose
        # base content is an already-`superseded` Point but whose `replace()`
        # renders a LIVE Point passed the terminal guard and appended a SECOND
        # `PointSuperseded` for a dead claim — the #2498 harm that guard exists
        # to prevent. (`str.__str__` on a plain `str` is identity, so nothing
        # about an ordinary call changes.)
        if issubclass(type(old_id), str):
            old_id = str.__str__(old_id)
        if issubclass(type(new_id), str):
            new_id = str.__str__(new_id)

        # #432/#2498: the SHARED transition guard — old point must exist, be
        # a statement (not an operator), and not already be terminal. The
        # pre-#2498 body hardcoded a 3-status subset, so an `outdated` /
        # `deprecated` status or the legacy `outdated=true` flag slipped
        # through and a dead claim could be re-superseded — transferring
        # edges off a terminal node.
        self._assert_lifecycle_guard(old_id, method="supersession",
                                     role="source")

        # P1 (Qwen review): validate the NEW point too — it must exist, be a
        # statement, not be terminal, and differ from the old point. A missing /
        # self / terminal successor would terminalize the old point with no valid
        # replacement (phantom PointSuperseded).
        #
        # ⛔ THE COMPARISON IS A CALLER READ TOO (#12914 review P2, cycle 9): `==`
        # on an operand that is not genuinely `str` dispatches to the caller —
        # ``str.__eq__`` answers ``NotImplemented`` for a GENUINE ``str``
        # SUBCLASS that overrides ``__eq__``, so the subclass's answer is the one
        # that lands. Measured: a str subclass whose ``__eq__`` returns False
        # walked ``supersede_knowledge(<live Point>, SubLyingEq(<the same id>))``
        # past this gate, and the write stored the row as its OWN successor —
        # row ``['superseded', True, None]`` with a ``CORRECTS`` edge
        # ``<id> -> <id>`` — the exact state this gate exists to refuse. The
        # rebind at the TOP of this method (re-review finding 3) already reduced
        # every genuinely-`str` operand to its base content, so this comparison
        # runs on base content for that pair without ever calling the caller's
        # `__eq__`; a non-``str`` pair keeps exactly the comparison it had, so no
        # non-``str`` id changes behaviour here.
        if old_id == new_id:
            raise ValueError("supersede_point: old_id and new_id must differ")
        # #2498: the SAME shared guard for the successor — a terminal (or
        # operator) target would terminalize the old point with no valid
        # replacement (phantom PointSuperseded).
        self._assert_lifecycle_guard(new_id, method="supersession",
                                     role="target")
        # #2351 (epic #2349 E-derived): the ASSERTED-vs-DERIVED override guard.
        # Supersession IS an override — `supersede_point` terminalises the
        # predecessor and transfers its edges and standing to the successor —
        # so a DERIVED successor must never replace an ASSERTED predecessor (a
        # computed claim out-ranking an authored belief is the failure mode the
        # marker exists to prevent). The refusal is BEFORE any mutation, so a
        # refused call leaves the graph untouched (the #329/#4021 ordering).
        #
        # The class is read through the same fan-out discipline as the
        # lifecycle guard (#7822 — Point ids are not unique, and a verdict read
        # off one row would be row-order dependent): the predecessor counts as
        # asserted when ANY node carrying its id is asserted, the successor
        # counts as derived when ANY node carrying the successor's id is. That
        # is the fail-closed direction for BOTH sides.
        _supersede_classes = _claim_class.classes_for_ids(
            proj.g, [old_id, new_id])
        if _claim_class.blocks_override(
                _supersede_classes.get(new_id),
                _supersede_classes.get(old_id)):
            raise ValueError(_claim_class.supersede_refusal(
                old_id=old_id, new_id=new_id))
        # 0. #329: collect + validate ALL edge types BEFORE any mutation.
        #    The edge types are interpolated into query structure (no params
        #    possible) — an unvalidated type (e.g. from a crafted edge) is a
        #    Cypher injection primitive AND would cause a partial transfer.
        #    Validation strictly precedes the outdated-flag/CORRECTS writes so
        #    a failure leaves the graph untouched.
        # ⛔ The operator POPULATION is the CANONICAL one, not ``is_operator``
        # alone (#10418 review P2). EP's Batch-1 predicate — the one the #2351
        # backstop runs under — is
        # ``(o.is_operator = true OR o.op_type IS NOT NULL)`` (``ep.py``: a
        # legacy / migrated operator carries ``op_type`` and may have lost the
        # boolean flag), and the repo spells that same disjunction at five other
        # sites (``_is_operator_point``, :27460, :27487, :28464, :28522).
        # A narrow predicate here is not an under-report — it is the fail-open:
        # 2a-DIRECT's "is the far endpoint an operator?" set was the same narrow
        # form, so the node this query missed was re-pointed THERE as an
        # operator-LESS direct edge (``idx`` dropped — it is not in
        # ``DIRECT_EDGE_ATTR_KEYS`` — plus a ``DirectEdgeRepoint`` for a factor
        # the loader builds as an OPERATOR factor). That is #10195's harm,
        # reached by REMOVING a property instead of writing a hostile one. So
        # this query, the slot query below, 2a-INPUT and 2a-DIRECT's ``op_set``
        # all read the ONE canonical form.
        edges_result = proj.g.query(
            "MATCH (op:Point)-[r]->(old:Point {id:$old_id}) "
            "WHERE (op.is_operator = true OR op.op_type IS NOT NULL) "
            "RETURN op.id, type(r), r.idx, op.label, ID(r)",
            params={"old_id": old_id},
        )
        from .security import validate_rel_type
        for row in edges_result.result_set:
            validate_rel_type(row[1])  # raises ValueError before any mutation

        # ── #10195: the 2a OPERATOR re-point's override pre-flight (#2351) ──
        # The 2a arm below re-points every canonically-operator
        # ``(op:Point)-[IMPL|NAND {idx}]->(old)`` edge onto the successor with
        # a raw edge
        # MERGE + delete, without ever consulting the claim classes of the
        # operator's OTHER inputs — so a supersede could mint the
        # ``derived --IMPL--> asserted`` factor ``create_operator`` refuses
        # (the same defect class as #9727's direct-edge arm, a distinct door).
        #
        # Operator SLOT POSITION is what the door reads: ``inputs[0]`` is the
        # factor's SOURCE slot and the rest are targets, so replacing an input
        # can move a claim ACROSS the class boundary in either direction — the
        # predecessor may be a target whose successor is authored, or the
        # predecessor may BE the source slot. So the question is asked of the
        # REBUILT layout (old substituted by new, slots in idx order), not of a
        # diff against the pre-supersede layout: a diff would let exactly that
        # pre-existing violation ride through into a fresh node, and the door's
        # job is "may the graph hold this wiring after the write".
        #
        # ⛔ ONE predicate: ``claim_class.direction_blocks_create``, the same
        # one ``create_operator`` / ``create_direct_edge`` / ``ingest``'s
        # partial-absorb branch use, fed the operator's stored direction
        # canonicalized with ``_canonical_direction`` (absent is NOT the mutual
        # default for NAND — the #2351-review lesson the ingest door records).
        # A second copy of "may this edge exist" would be a finding of its own.
        #
        # Skipped, deliberately (the issue's scope): ``hasPart`` and the other
        # non-``IMPL|NAND`` rel types are not epistemic cascades (no belief
        # transfers, so the override rule does not apply), and an operator
        # labelled ``alreadyDecided`` is NOT re-pointed at all (the #1080
        # carve-out in 2a), so its slots do not move.
        #
        # ORDER: collected + judged HERE, beside the #329 edge-type validation
        # and BEFORE the ``PointSuperseded`` append and every other mutation — a
        # refusal that lands after the transfer is the #329/#4021
        # partial-mutation defect no matter how good its message is.
        # ⛔ EVERY mechanism, not one per operator (#10418 review P2). An
        # earlier cut kept ONE rel type per operator id —
        # ``_repoint_ops.setdefault(op_id, type(r))`` over a statement with no
        # ``ORDER BY`` — so which mechanism got judged was SERVER ROW ORDER,
        # and the two picks are not equivalent: ``_canonical_direction``'s
        # absent default is ``bidirectional`` for IMPL (both legs) and
        # ``unidirectional`` for NAND (forward only), so a NAND pick could pass
        # a wiring the IMPL pick refuses. That is a verdict (and a refusal
        # message) that flips between runs — the one property the slot query's
        # ``ORDER BY op.id, inp.id`` was added to guarantee. 2a re-points BOTH
        # rel types, so BOTH mechanisms reach the rebuilt factor and both are
        # judged; a set makes the collection order-independent by construction.
        _repoint_rels: dict[str, set[str]] = {}
        for row in edges_result.result_set:
            if row[1] in ("IMPL", "NAND") and row[3] != "alreadyDecided":
                _repoint_rels.setdefault(row[0], set()).add(row[1])
        if _repoint_rels:
            # ONE statement for every affected operator's slots. ``op.op_type``
            # / ``op.direction`` are the node's own record; ``sr.idx`` is the
            # slot position EP reads (``ORDER BY coalesce(r.idx, 0)``).
            # ⛔ ``WITH op`` between the predicate and the following MATCH is
            # load-bearing, not style: on the canonical engine a variable
            # filtered in one clause and re-bound by a LATER clause has its
            # predicate DROPPED, so the slot rows would come back for OTHER
            # operators' points (#6976; ``test_no_query_loses_its_id_predicate``).
            _slot_rows = proj.g.query(
                "MATCH (op:Point) WHERE op.id IN $op_ids "
                "AND (op.is_operator = true OR op.op_type IS NOT NULL) "
                "WITH op "
                "MATCH (op)-[sr:IMPL|NAND]->(inp) "
                "RETURN op.id, op.op_type, op.direction, sr.idx, inp.id "
                # Deterministic row order, so a DUPLICATED operator id (raw-write
                # only — ids are ULIDs) cannot make the verdict, or the refusal
                # message, depend on server row order (#10195 review P3).
                # ⛔ ``sr.idx`` is deliberately NOT in this ORDER BY: it is the
                # one property that may hold MIXED types, and cross-type ordering
                # is engine-defined — the door orders slots in Python instead.
                "ORDER BY op.id, inp.id",
                params={"op_ids": sorted(_repoint_rels)},
            ).result_set
            _slot_map: dict[str, list] = {}
            _op_meta: dict[str, list] = {}
            for _srow in _slot_rows:
                _sid, _sop_type = _srow[0], _srow[1]
                _sdir, _sidx, _sinp = _srow[2], _srow[3], _srow[4]
                _op_meta.setdefault(_sid, []).append((_sop_type, _sdir))
                _slot_map.setdefault(_sid, []).append((_sidx, _sinp))
            for _oid in sorted(_repoint_rels):
                _srows = _slot_map.get(_oid) or []
                if not _srows:
                    # No slot rows at all: this operator has no ``IMPL|NAND``
                    # edge reaching a point, so there is no rebuilt factor to
                    # judge.
                    continue
                # ⛔ NO STAND-DOWN ON, OR FOR, A NULL ``idx`` (#10195 review P2;
                # #10364/#10404). An earlier cut skipped the whole operator
                # whenever a slot edge carried no ``idx``, justified by "``ep.py``
                # clears ``op_idx_known[op_id]`` under ``if idx is None`` and that
                # flag gates its #2351 backstop". That was true at this branch's
                # base and is FALSE on main: ``#10404`` removed the gate, so the
                # load side now runs ``factor_direction_over_claims``
                # UNCONDITIONALLY (``ep.py`` — "⛔ NOT gated on ``op_idx_known``
                # (#10364)"), and ``op_idx_known`` survives only for the
                # SOURCE-STRIP guard, which asks a different question and is
                # correctly gated. So the door skipping killed the supersede's
                # only verdict while the loader still judged the factor it built.
                # ⛔ And the layout was never even unorderable: ``ep.py`` orders
                # ``coalesce(r.idx, 0), c.id``, so a NULL slot IS position 0 — the
                # very shape the duplicated-``idx`` arm below already reads (one
                # candidate, tie-broken by claim id). NULL is therefore COERCED to
                # 0 here, exactly as ``coalesce`` does, and judged. The skip was
                # the ONE remaining fail-open of this arm: a supersede minting
                # ``derived --IMPL--> asserted`` with no verdict, no warning and no
                # record, reachable by REMOVING a property rather than by writing a
                # hostile one.
                # ⛔ NOTHING ELSE stands down either. A non-``int`` ``idx`` and one
                # ``idx`` carried by two inputs are JUDGED too — the load side
                # builds and judges those factors, so a door that skipped them
                # would be the same divergence in the FAIL-OPEN direction (#10195
                # review P2 — this arm's earlier "the same evidence silences both"
                # claim was false for two of its legs).
                # The rebuilt layout: ``old`` becomes ``new`` AT ITS SLOT, so what
                # is judged below is the wiring the graph holds AFTER the write.
                # A NULL ``idx`` enters the layout as the 0 ``coalesce`` reads it.
                _slots: list[tuple[object, str]] = [
                    (0 if _sidx is None else _sidx,
                     new_id if _sinp == old_id else _sinp)
                    for _sidx, _sinp in _srows]
                _slot_ids = [i for _, i in _slots]
                # ⛔ ORDERABILITY IS NOT A NUMERIC PROPERTY, and this was MEASURED
                # on the engine rather than assumed (#10195 review round 2).
                # ``ep.py`` reads the factor with
                # ``ORDER BY coalesce(r.idx, 0), c.id``, and on FalkorDB that
                # ordering GROUPS BY TYPE before comparing values: a STRING slot
                # sorts AHEAD of a numeric one (``'1'`` before ``0``), and a
                # BOOLEAN sorts neither as 0 nor as 1. A total numeric order over a
                # MIXED layout therefore names a DIFFERENT source than the loader:
                # measured fail-open in one direction (source DERIVED at ``'1'``,
                # predecessor at ``0`` — the door passed the supersede and the load
                # side immediately reported ``derived_over_asserted``) and
                # over-refusal in the mirror. So the layout is ordered ONLY when
                # EVERY slot idx is a real ``int`` (``bool`` excluded — it is an
                # ``int`` SUBCLASS in Python yet a separate type to the engine); every
                # other layout takes the candidate arm below, which is engine-agnostic
                # and fail-CLOSED. Modelling the engine's type-group order instead
                # would be a second copy of engine-defined behaviour, wrong the day
                # that engine changes.
                # ⚠️ WHAT THAT COSTS, recorded rather than left to be discovered: a
                # layout whose idx are ALL one NON-``int`` type (every slot written
                # ``'0'`` / ``'1'``) IS ordered by the engine — inside one type group
                # the comparison is type-local — yet this door takes the candidate arm
                # for it too, because it does not re-implement that per-type order. So
                # it can refuse a supersede the loader would keep. That is the SAFE
                # direction (a false refusal, never an exemption), the shape is
                # reachable only by a raw write (``create_operator`` stamps ints), and
                # the refusal names a real remedy — while a stand-down here is exactly
                # the #10195 fail-open one property of a schemaless graph defeated.
                # ⚠️ A NULL ``idx`` was coerced to 0 above, so it joins the ORDERED
                # arm rather than the candidate one — which is the loader's own
                # reading (``coalesce(r.idx, 0)``), not a leniency: an all-NULL
                # layout is exactly the duplicated-``idx`` shape handled below.
                # ⛔ ONE pass decides orderability AND collects the positions as
                # ``int``. ``_slots`` is typed ``tuple[object, str]`` ON PURPOSE
                # (a raw-written ``idx`` may hold any type), so the ordered arm
                # below needs a list it can prove orderable — ``min()`` over
                # ``object`` is a mypy ``type-var`` error (#10418 typecheck), and
                # a ``cast()`` after an ``all()`` would ASSERT exactly what this
                # loop PROVES: ``_ordered_slot_idxs`` is complete precisely when
                # ``_uniform_int`` holds, which is the only branch that reads it.
                _ordered_slot_idxs: list[int] = []
                _uniform_int = True
                # Whether the candidate arm's cause is a CROSS-TYPE layout or ONE
                # non-``int`` type group — the refusal states which, because they
                # are two different facts (#10418 review P3): the engine orders
                # within a type group (type-local comparison) and groups ACROSS
                # them, so only the mixed layout's slots are genuinely unordered.
                _slot_idx_types: set[type] = set()
                for _slot_idx, _ in _slots:
                    if isinstance(_slot_idx, int) and not isinstance(
                            _slot_idx, bool):
                        _ordered_slot_idxs.append(_slot_idx)
                        _slot_idx_types.add(int)
                    else:
                        _uniform_int = False
                        _slot_idx_types.add(type(_slot_idx))
                _slot_order = "proven" if _uniform_int else (
                    "mixed" if len(_slot_idx_types) > 1 else "uniform-non-int")
                if _uniform_int:
                    # The load side's own rule: the source is the input at the
                    # LOWEST slot idx, and a slot idx carried by TWO inputs is
                    # tie-broken there by the claim ID (``c.id``). The door takes
                    # the SAME single candidate rather than judging every tied one —
                    # judging them all would refuse a shape the load side keeps,
                    # the divergence `claim_class` warns against (#10195 review P3).
                    _low = min(_ordered_slot_idxs)
                    _candidate_sources = [min(
                        {i for _sidx, i in _slots if _sidx == _low}, key=str)]
                else:
                    # UNORDERABLE, not unjudgeable: the door can NAME no SOURCE
                    # position here (a mixed layout has no provable order, and a
                    # uniformly non-int one it declines to re-order — see above), so
                    # EVERY input is a candidate source and the wiring is refused if
                    # ANY candidate would override. Fail CLOSED, because
                    # the load side keeps judging this factor — the fallback belongs
                    # to the load side's strictness, never to a silent exemption.
                    _candidate_sources = [i for _, i in _slots]
                # The slots are judged AS WRITTEN — every input, in the row's id
                # order (position is decided above, never by this list) — which is
                # how ``create_operator`` reads a caller's request. EP's load side
                # additionally strips draft/terminal inputs and takes the first
                # SURVIVING one as the source, but that is a reading of a factor
                # after the fact, not the wiring the caller asked to write: a draft
                # input can be promoted later, and the factor is then exactly the one
                # this door refuses.
                # ⛔ ``classes_for_ids`` is ``:Point``-anchored: an id ABSENT
                # from it is not a claim at all (:Subject/:Object/:Event —
                # #7902), and its absence must not read as the ASSERTED
                # default. Same parity as the ingest partial-absorb door.
                _slot_classes = _claim_class.classes_for_ids(proj.g, _slot_ids)
                _meta_rows = _op_meta.get(_oid) or []
                # Conservative on BOTH axes when a duplicated operator id leaves
                # the stored record ambiguous (ids are not unique — #7822 — so
                # this is a raw-write shape, reachable the same way the
                # mixed-``idx`` arm is): judge EVERY stored ``op_type`` and EVERY
                # stored ``direction``. Collapsing the directions to ONE stored
                # row picks an arbitrary one, and collapsing them to the per-type
                # default is NOT the conservative reading — a NAND's absent
                # default is ``unidirectional`` (one leg), so a ``<-`` row's
                # BACKWARD leg would go unjudged and a derived target under an
                # asserted source could be re-pointed into a factor the load side
                # reports ``derived_over_asserted`` (#10195 — the same fail-open
                # this arm's mixed-``idx`` sibling closes, on the direction axis).
                # A node with no ``op_type`` is pre-v3.6 legacy; the rel type that
                # carried it into this set IS its mechanism then.
                # ⛔ A stored ``direction`` may be a RAW-WRITTEN non-scalar (list/
                # map — hand edit, a foreign/legacy writer, a bulk import) and
                # such a value is UNHASHABLE, so the bare set comprehension this
                # replaced raised ``TypeError: unhashable type`` BEFORE any
                # verdict — aborting every supersede that touched that operator,
                # not only override-shaped ones (#10559).
                # The reading mirrors #10552's 2a-DIRECT arm: a PRESENT
                # non-string is judged BOTH ways (``<->``, which
                # ``_canonical_direction`` returns verbatim for every type, so
                # the stand-in flows through the existing predicate unchanged —
                # fail-CLOSED, since a reader sees a present junk token as the
                # mutual default), while ABSENT (``None``) keeps the edge type's
                # own default exactly as before.
                _dirs = {
                    _d if isinstance(_d, str)
                    else ("<->" if _d is not None else None)
                    for _, _d in _meta_rows
                }
                # ⛔ A stored ``op_type`` OUTSIDE the pair is no exemption
                # (#10418 review P3). ``create_operator`` routes every other
                # ``op_type`` to a ``hasPart`` edge (``:20244``), so
                # ``op_type='hasPart'`` with ``IMPL`` edges into the
                # predecessor is a RAW-WRITE shape — and EP's Batch-1 predicate
                # filters on ``op_type IS NOT NULL``, not on its VALUE, so the
                # loader still builds this factor and runs the #2351 backstop on
                # it. A door that skipped it here was that backstop's only
                # absent verdict. The mechanism to judge is therefore the REL
                # TYPE that carried the operator into this set.
                # ⛔ And the REL TYPES are UNION'd in, not used as a fallback
                # (#10418 review P2): these are the mechanisms 2a will actually
                # re-point, so they are judged WHATEVER the stored record says.
                # The stored value can be the WEAKER of the two (``NAND``'s
                # absent default is forward-only, ``IMPL``'s is both ways), and
                # a fallback that trusts it would pass a wiring the edge it is
                # re-pointing refuses. Union is the conservative direction and
                # costs nothing: when a ``direction`` IS stored,
                # ``_canonical_direction`` returns it verbatim for every type,
                # so the extra mechanism adds no distinct leg.
                _op_types = sorted({
                    t for t, _ in _meta_rows
                    if t in ("IMPL", "NAND")} | _repoint_rels[_oid])
                for _slot_source in _candidate_sources:
                    _slot_targets = list(_slot_ids)
                    _slot_targets.remove(_slot_source)
                    if not _slot_targets:
                        continue  # a unary factor overrides nobody
                    _slot_target_classes = set().union(
                        *(_slot_classes.get(_t, set()) for _t in _slot_targets))
                    for _op_type in _op_types:
                        # ``_op_types`` is provably within ``IMPL|NAND`` at this
                        # point (the stored value is filtered above and the
                        # union member is the rel type this query matched).
                        if (_slot_source not in _slot_classes
                                or not _slot_target_classes):
                            continue
                        # Every stored direction, canonicalized per ``op_type``
                        # (absent ⇒ that type's own default). ``_meta_rows`` is
                        # non-empty whenever ``_srows`` is, so ``_dirs`` always
                        # holds at least one row's value; a ``None`` in it is
                        # canonicalized here, never skipped.
                        # ⛔ Sorted for a STABLE refusal message, and by a
                        # TYPE-AWARE key: ``_canonical_direction`` returns a
                        # stored value verbatim, so a raw-written non-string
                        # ``direction`` beside a token on another node makes a
                        # bare ``sorted({1, "->"})`` raise ``TypeError`` — a
                        # traceback aborting a supersede this guard is not even
                        # about (#10418 review P3). The key is total over the
                        # scalar types a property can hold, and the verdict
                        # never depends on it (the loop raises on the FIRST
                        # blocking direction).
                        _effective_dirs = sorted({
                            self._canonical_direction(_op_type, _d)
                            for _d in _dirs},
                            key=lambda _d: (type(_d).__name__, str(_d)))
                        for _effective_dir in _effective_dirs:
                            if _claim_class.direction_blocks_create(
                                    _effective_dir,
                                    _slot_classes.get(_slot_source),
                                    _slot_target_classes):
                                raise ValueError(_claim_class.repoint_refusal(
                                    operator_id=_oid, op_type=_op_type,
                                    direction=_effective_dir,
                                    slot_order=_slot_order,
                                    old_id=old_id, new_id=new_id,
                                    source_id=_slot_source,
                                    target_ids=_slot_targets))

        # ── #9727: the 2a-DIRECT re-point's override pre-flight (#2351) ──
        # 2a-DIRECT below re-points every OPERATOR-LESS ``IMPL|NAND`` edge
        # incident to the predecessor onto the successor with a raw edge MERGE,
        # and it never consulted the claim classes of the edge's OTHER
        # endpoint — so a supersede could mint the ``DERIVED -[IMPL]-> ASSERTED``
        # wiring ``create_direct_edge`` / ``create_operator`` refuse to write
        # (measured: ``create_direct_edge(IMPL, new, t)`` raises the #2351
        # refusal for exactly the pair the re-point had just created).
        #
        # The door the defect reaches through needs no raw write. A direct edge
        # is judged at CREATE time on the pair it is asked to wire, and that
        # pair is the PRE-SUPERSEDE one: ``old``(derived) -[IMPL]-> ``t``
        # (derived) is a LEGAL same-class edge, and so is a supersede whose
        # successor is AUTHORED (an asserted claim may replace a derived one —
        # the #2351 guard above only refuses the reverse). Composed, the two
        # legal acts mint ``new``(asserted) -[IMPL]-> ``t``(derived), whose
        # flows-back leg is exactly the ``derived -> asserted`` override. The
        # class of the endpoints was judged once, at creation, against a
        # predecessor that the supersede then REPLACED — so the wiring is
        # judged here, on the pair the graph will hold after the write.
        #
        # ⛔ ONE predicate: ``claim_class.direction_blocks_create``, fed the
        # edge's stored direction — a string verbatim, the edge TYPE's default
        # when ABSENT, and a present non-string VERBATIM TOO, which
        # ``direction_flows`` then reads as BOTH ways (see the per-row note) —
        # and, all three of them, through the same canonicalizer as
        # ``create_operator``, ``create_direct_edge`` and ``ingest``'s
        # partial-absorb branch. #10195's operator pre-flight above reads the
        # same predicate, but it screens a non-string to ``"<->"`` BEFORE
        # canonicalizing — a hashability screen, not a second policy (see the
        # per-row note). A
        # second copy of "may this edge exist" would be a finding of its own.
        # The REFUSAL message is that
        # predicate's own (``override_refusal``), with this surface named —
        # the caller's ways out are the same ones it states.
        #
        # ORDER: collected and judged HERE, beside the #329 edge-type
        # validation and the #10195 slot pre-flight, and BEFORE the
        # ``PointSuperseded`` append and every other mutation — a refusal that
        # lands inside 2a-DIRECT would land after the CORRECTS edge and the
        # operator transfers, i.e. the #329/#4021 partial-mutation defect no
        # matter how good its message is.
        #
        # Skipped, deliberately: ``tid == new_id`` (2a-DIRECT DELETES that edge
        # as a phantom self-edge and re-points nothing), and any edge the
        # transfer itself will not write (an unresolvable endpoint label — the
        # loop's own #8622 condition), and a non-``:Point`` far endpoint — not
        # a CLAIM, so nothing to override (#7902; the same ``:Point``-only rule
        # the creation doors apply by building their class sets from Point
        # rows).
        def _direct_incident_edges(_direction: str) -> list:
            """The 2a-DIRECT population for ONE direction (query + op filter).

            ONE collection IMPLEMENTATION — every caller reads its own CURRENT
            state. Two queries that drifted apart would be a fail-open nobody
            notices, so the query and its operator filter live here only. The
            pre-flight's up-front snapshot and each transfer pass's re-read are
            deliberately separate (see the transfer loop's note): the
            ``out`` pass mutates the graph the ``in`` pass must then read, so
            hoisting ONE capture into both passes is the measured
            double-re-point defect, not a de-duplication.
            """
            if _direction == "out":
                _dq = ("MATCH (old:Point {id:$old_id})-[r:IMPL|NAND]->(t) "
                       "RETURN type(r), r, t.id, ID(r), labels(t)")
            else:
                _dq = ("MATCH (t)-[r:IMPL|NAND]->(old:Point {id:$old_id}) "
                       "RETURN type(r), r, t.id, ID(r), labels(t)")
            _rows = proj.g.query(_dq, params={"old_id": old_id}).result_set
            if not _rows:
                return []
            # Filter: exclude edges whose OTHER endpoint is an operator Point
            # (REVIEW-FIX P2 — a mitigation-style (m)-[:IMPL]->(op) edge has
            # an operator target; 2a owns op->input edges and runs first;
            # repointing an operator target would create a topology
            # create_direct_edge itself rejects). Operators are the CANONICAL
            # set — ``is_operator = true OR op_type IS NOT NULL``, the same
            # population 2a and the #10195 pre-flight read (#10418 review P2):
            # with a narrower set here, an ``op_type``-only operator's edge
            # fell through to this leg as an operator-LESS direct edge, losing
            # its ``idx`` slot position and emitting a ``DirectEdgeRepoint``
            # for a factor the loader builds as an operator factor.
            _other_ids = [row[2] for row in _rows]
            _op_rows = proj.g.query(
                "MATCH (p:Point) WHERE p.id IN $ids AND "
                "(p.is_operator = true OR p.op_type IS NOT NULL) "
                "RETURN p.id",
                params={"ids": _other_ids},
            ).result_set
            _op_set = {r[0] for r in _op_rows}
            # ⛔ The operator filter must not hide a row that is DELETED rather
            # than re-pointed (#9727). ``tid == new_id`` only ever deletes (the
            # phantom self-edge branch), and a successor that is an operator by
            # the CANONICAL population but not by the lifecycle guard's
            # narrower ``is_operator`` test (an ``op_type``-only node —
            # ``live.LIFECYCLE_FANOUT_QUERY`` reads the flag alone) would
            # otherwise be filtered HERE, so the ``in`` pass never saw the
            # transient ``(new)-[T]->(old)`` edge the ``out`` pass had just
            # minted and left it stranded on the superseded point — with its
            # ``derived -> asserted`` leg flowing. MEASURED (raw self-loop +
            # an ``op_type``-only successor): the supersede returned
            # ``edges_transferred=1`` with no refusal and that edge survived.
            return [row for row in _rows
                    if row[2] not in _op_set or row[2] == new_id]

        def _epistemic_label(node_id: str) -> str | None:
            """Resolve one id's epistemic label, Point-first (#7853 sweep)."""
            for _label in EPISTEMIC_LABELS:
                found_row = proj.g.query(
                    f"MATCH (n:{_label} {{id:$id}}) RETURN ID(n) LIMIT 1",
                    params={"id": node_id},
                ).result_set
                if found_row:
                    return _label
            return None

        new_label = _epistemic_label(new_id) if new_id else None
        _direct_rows: dict[str, list] = {
            _direction: _direct_incident_edges(_direction)
            for _direction in ("out", "in")}
        # ⛔ ONE class reader for BOTH endpoints, and it mirrors the creation
        # doors exactly: an id that is an id-ADDRESSABLE string is resolved by
        # the fan-out probe (``claim_class.classes_for_ids``, #7822); any other
        # id — a raw-written NON-STRING one, which the transfer's
        # ``MATCH {id:$tid}`` may still RESOLVE (measured: an integer id) — is
        # resolved by a
        # ``:Point``-anchored marker probe instead, because handing it to
        # ``classes_for_ids`` would silently describe nothing (that helper
        # filters non-``str`` ids) and ``None`` reads as "asserted" downstream,
        # which is a fail-open on one axis and a false refusal on the other
        # (measured both: an int-id derived far endpoint, and an int-id derived
        # SUCCESSOR). An endpoint with NO ``:Point`` row is not a CLAIM
        # (:Subject/:Object/:Event, #7902) — ``None`` — and nothing can override
        # it, nor it anything. An ABSENT / empty id does not resolve in the
        # transfer at all (measured), so it is skipped for the same reason a
        # non-``:Point`` endpoint is: no edge is written.
        def _claim_classes_for(node_id) -> frozenset[str] | None:
            if isinstance(node_id, str) and node_id:
                return _direct_classes.get(node_id)
            if node_id is None or node_id == "":
                return None
            _rows = proj.g.query(
                "MATCH (n:Point {id:$id}) "
                "RETURN coalesce(n.claimClass, 'asserted')",
                params={"id": node_id},
            ).result_set
            return frozenset(
                _claim_class.normalize(_r[0]) for _r in _rows) or None

        # ⛔ The id list handed to ``classes_for_ids`` is STRINGS only, and
        # ``new_id`` is NOT unioned in: a non-string successor id would make
        # ``sorted`` raise ``TypeError`` on a mixed set — aborting a supersede
        # the guard is not about (#10195 review P3's lesson, one arm over).
        _direct_other_ids = sorted(
            {row[2] for _rows in _direct_rows.values() for row in _rows
             if row[2] != new_id
             and isinstance(row[2], str) and row[2]})
        _direct_classes = _claim_class.classes_for_ids(
            proj.g, _direct_other_ids + ([new_id]
                                         if isinstance(new_id, str) and new_id
                                         else []))
        _new_classes = _claim_classes_for(new_id)
        for _direction, _rows in _direct_rows.items():
            for _row in _rows:
                _rtype, _rel, _tid = _row[0], _row[1], _row[2]
                if _tid in (new_id, old_id):
                    # ``tid == new_id``: 2a-DIRECT DELETES this one (the
                    # phantom self-edge case) — nothing is re-pointed.
                    # ``tid == old_id``: a SELF-LOOP at the predecessor, which
                    # the out/in passes consume between them (see the
                    # ``_direct_incident_edges`` note) PROVIDED the transient
                    # row reaches the ``in`` pass — which is what that helper's
                    # ``or row[2] == new_id`` exception guarantees. Judging the
                    # ``in`` copy of it would refuse a supersede over an edge
                    # that ends up deleted.
                    continue
                _row_labels = set(_row[4] or ()) if len(_row) > 4 else set()
                # The transfer writes an edge only when BOTH endpoints resolve
                # to an epistemic label (the #8622 condition); a row it deletes
                # instead is nothing to judge.
                if not (_row_labels & set(EPISTEMIC_LABELS)) or not new_label:
                    continue
                # …and the #2351 rule is asked of CLAIMS only. ``Point``-first
                # label order means this is the label the doors resolve too, so
                # a :Subject/:Object/:Event endpoint skips on both sides.
                if new_label != "Point":
                    continue
                _tid_label = next(
                    (lbl for lbl in EPISTEMIC_LABELS if lbl in _row_labels),
                    None)
                if _tid_label != "Point":
                    continue
                _tid_classes = _claim_classes_for(_tid)
                if _new_classes is None or _tid_classes is None:
                    # No ``:Point`` row behind that id — not a claim; the guard
                    # has no verdict to give and must not invent one.
                    continue
                _rdict = (dict(_rel.properties)
                          if hasattr(_rel, "properties") else {})
                # ⛔ ONE reader — no door-local copy of the direction policy
                # (#10520 A4). The stored token goes straight to
                # ``_canonical_direction``, the same canonicalization
                # ``create_direct_edge`` applies, and the VERDICT then comes from
                # ``claim_class.direction_blocks_create`` ->
                # ``live.direction_flows``, the same normalizer the EP backstop
                # reads. A PRESENT value is returned VERBATIM (only ABSENT is
                # replaced by the edge TYPE's default — deliberately NOT
                # ``ep.py``'s ``coalesce(r.direction, 'bidirectional')``, which
                # would be the over-refusal the directionless-NAND pin exists to
                # prevent), and a present JUNK value is read as BOTH ways by
                # ``direction_flows`` — which is also the fail-closed reading the
                # EP backstop sees. Its ``isinstance(..., str)`` screen is what
                # keeps a NON-``str`` unhashable value (a list, a map) out of a
                # frozenset membership test; it is NOT a guarantee for an
                # unhashable ``str`` SUBCLASS, which passes that screen and would
                # still raise inside ``canonical_direction``'s own membership test
                # (unreachable from a graph read — neither FalkorDB nor a JSON
                # projection can yield one). ⛔ Do NOT reintroduce a per-value
                # branch here: a second copy of the policy is what let this door
                # publish its own token as ``judged`` in the refusal below, and it
                # is behaviour-invisible (it verdicts identically by design), so
                # the SHAPE is what is pinned —
                # ``tests/test_2351_claim_class.py::
                # test_the_repoint_door_carries_no_door_local_direction_filter``
                # is the mechanical guard a comment cannot be.
                # ⛔ Scope of "ONE reader": the 2a-OPERATOR pre-flight above DOES
                # still screen a stored direction (``_d if isinstance(_d, str)
                # else ("<->" if _d is not None else None)``) — that is a
                # HASHABILITY screen, not a copy of this policy: the value becomes
                # a SET KEY there, and an unhashable list/map aborted the whole
                # pre-flight (#10559). Its ``<->`` stand-in yields the same verdict
                # as this door's reading; what it does NOT do is name the stored
                # value in its refusal, publishing ``'<->'`` for a value the graph
                # does not carry — that residue is tracked as **#11072** (it is NOT
                # #11033, which is the ID axis of the same pre-flight), not this
                # door's.
                _raw_dir = _rdict.get("direction")
                _eff_dir = self._canonical_direction(_rtype, _raw_dir)
                # ⛔ SAY ONLY WHAT THE MESSAGE DOES NOT ALREADY SAY. The
                # canonicalizer substitutes ONLY for an ABSENT value (a present
                # one is returned VERBATIM), so rendering ``(judged X)`` beside
                # ``direction X`` is pure duplication — and deleting the
                # door-local filter is exactly what turned the parenthetical
                # from "the token this door substituted" into a copy of the
                # value beside it. Render it ONLY where the canonicalization
                # SUBSTITUTED: that is the one reading the caller cannot take
                # from the stored value itself. ⛔ The condition is "did this
                # substitute" (``_raw_dir is None``), NOT "is the result the same
                # object": those coincide only while every token is returned
                # by identity, and a normalizer that returned an EQUAL BUT
                # DISTINCT token would silently re-create the duplicate.
                _judged = ("" if _raw_dir is not None
                           else f" (judged {_eff_dir!r})")
                if _direction == "out":
                    _src_id, _tgt_id = new_id, _tid
                    _src_classes, _tgt_classes = _new_classes, _tid_classes
                else:
                    _src_id, _tgt_id = _tid, new_id
                    _src_classes, _tgt_classes = _tid_classes, _new_classes
                if _claim_class.direction_blocks_create(
                        _eff_dir, _src_classes, _tgt_classes):
                    # ⛔ It names the WIRING IT JUDGED, not ``old->new``: on the
                    # ``in`` arm the edge being re-pointed is ``tid -> old``
                    # becoming ``tid -> new``, so a hardcoded ``old->new``
                    # points the caller at an edge that does not exist.
                    raise ValueError(_claim_class.override_refusal(
                        source_id=_src_id, target_ids=[_tgt_id],
                        surface=(f"supersede_point: re-pointing the "
                                 f"{_rtype} edge {_src_id!r}->{_tgt_id!r} "
                                 f"(from {old_id!r} to {new_id!r}, "
                                 f"direction {_raw_dir!r}{_judged})")))

        # #432 Task 3: durable PointSuperseded event (append-before-mutation,
        # AFTER the guard + edge-type validation — P2 review fix: emitting
        # before validation produced phantoms on corrupt-edge data).
        # E6 (#1538) D2: resolve the successor's validFrom (window contiguity
        # source) BEFORE the emit so the event payload carries the same
        # values the stamp block writes (read-only — no ordering impact).
        #
        # The resolution ORDER below is the documented one and is unchanged
        # (ONTOLOGY.md §4.1/§4.7 `validTo` row: `valid_from` kwarg →
        # successor validFrom → successor createdAt → now). What IS new is a
        # PRECONDITION on the kwarg, now stated in that row too: trusting it
        # verbatim broke chain contiguity silently in BOTH directions — an
        # EARLIER kwarg left a GAP (a query instant covered by neither
        # window, so `get_historical_knowledge` reports honest absence for a period
        # that was in fact covered) and a LATER kwarg left an OVERLAP (two
        # covering candidates ⇒ every instant inside it reads `ambiguous`).
        # The successor's STORED validFrom is the value every read path
        # computes its window start from (`get_historical_knowledge` → `_covers`), so
        # a disagreeing kwarg can only ever make the chain wrong. Refuse it
        # BEFORE any mutation rather than pick a winner: picking the store
        # would invert the documented order, picking the kwarg re-creates the
        # defect.
        #
        # The comparison keys on ``str(valid_from)`` — the value the stamp
        # block PERSISTS — not the caller's object, and requires BOTH sides to
        # be parseable to the same instant. Both halves are load-bearing:
        #   * keying the caller's object blesses a value the write
        #     metamorphoses. A numeric-epoch kwarg parses as an instant, but
        #     the ``str()`` that lands in ``validTo`` is UNPARSEABLE to
        #     ``_created_sort_key`` (its ISO branch needs a ``-`` or ``T``),
        #     i.e. an unbounded predecessor window — the exact OVERLAP this
        #     guard exists to prevent.
        #   * an unparseable side cannot be shown to name the same instant,
        #     and ``_covers`` cannot order it either, so agreeing to write it
        #     would be the silent wrong answer the read path refuses.
        # ``_created_sort_key`` is the SAME measure ``_covers`` uses, so the
        # guard's agreement boundary IS the read path's contiguity boundary.
        # It normalizes a purely cosmetic encoding difference
        # ("…T00:00:00Z" vs "…T00:00:00+00:00") and that is therefore
        # accepted. Since #3982 it ANCHORS a DATE-ONLY value to UTC midnight
        # (ECMA-262 §21.4.3.2: with no offset, date-only forms are UTC). So a
        # date-only value and the SAME day stated as midnight UTC
        # ("…T00:00:00Z") compare EQUAL, and that pair is ACCEPTED on every
        # host — the equality does not depend on the reader's zone.
        # The equality is specific to the midnight-UTC ENCODING, not to the
        # calendar day: the same date at any other time of day names a
        # different instant and is still refused, as is any pair on different
        # days. Those verdicts are now host-INDEPENDENT rather than
        # host-dependent. The read path consumes the same primitive
        # (`_covers`), so the guard and the read path continue to agree. A
        # zone-less date-TIME is unaffected and still reads locally.
        #
        # The guard's PRESENCE predicate is the read path's, not the
        # resolution branch's. `_covers` gates its START bound on presence AND
        # orderability, so a falsey-but-PRESENT stored value is a REAL window
        # start there when it orders: `0` keys as the parseable epoch-0
        # instant. `""` names no instant, and since the #6140 ruling (owner,
        # 2026-10-11) that means NO START RECORDED — an OPEN start covering
        # every instant — so it is not a bound the resolution branch may take
        # (persisting it as an END would leave the predecessor unbounded). The resolution
        # branch below delegates to ``_supersede_window_start_source`` — PRESENT
        # (not truthy) AND ORDERABLE: #3985 for the falsey-but-orderable `0`,
        # #5360 for the truthy-but-unparseable stored start and the unparseable `createdAt`.
        # The guard follows
        # `_covers`: with a kwarg present it refuses rather than allow an
        # unchecked window end against a start the read path treats as real
        # (a `validFrom=0` successor's `[epoch0, inf)` window overlaps any
        # predecessor end the kwarg writes at or after epoch 0, and gaps
        # before it). The no-kwarg FALSEY-AND-UNORDERABLE case (`""`) keeps the
        # pre-existing fallback — which is exactly what an ABSENT start gets, so
        # the two paths AGREED on it all along once #6140 ruled that `""` means
        # NO START RECORDED: there is no divergence left to track. #3985 fixed
        # the falsey-but-ORDERABLE case (`0`), which the branch above now takes.
        vf_rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.validFrom, n.createdAt, "
            "n.validFromPrecision",
            params={"id": new_id},
        ).result_set
        stored_vf = vf_rows[0][0] if vf_rows else None
        if valid_from is not None and stored_vf is not None:
            from .search_engine import _created_sort_key
            k_kwarg = _created_sort_key(str(valid_from))
            k_stored = _created_sort_key(stored_vf)
            if not (k_kwarg[0] == 0 and k_stored[0] == 0
                    and k_kwarg[1] == k_stored[1]):
                raise ValueError(
                    f"supersede_point: valid_from {valid_from!r} disagrees "
                    f"with successor {new_id}'s stored validFrom "
                    f"{stored_vf!r} — both must be parseable timestamps "
                    f"naming the same instant, else the predecessor's "
                    f"validTo gaps or overlaps the chain (read paths use "
                    f"the stored window start)"
                )
        # #4021: the resolution above plus the INVERTED-window refusal live in
        # ONE home, shared with the MCP dry-run preview (parity by construction).
        # This read of the predecessor's own start is the one the defect was
        # missing: the stamp below used the successor's start without ever
        # comparing it to the window it was truncating.
        old_vf_rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.validFrom",
            params={"id": old_id},
        ).result_set
        # EVERY node carrying the id, not just the first: the stamp below
        # MATCHes and stamps them all, and row order is server-dependent
        # (the #5358 sibling loops over the same fan-out for this reason).
        old_vfs = [r[0] for r in old_vf_rows]
        # #2350 (E-prec): resolve the precision of the predecessor's stamped
        # END BEFORE the inverted-window guard. ``'unknown'`` is the
        # closed-unknown marker: the window ENDED but carries no datable
        # instant, so NO ``validTo`` is fabricated (the 'half-honest dates are
        # worse than none' rule) — and because no end is persisted, the
        # inversion guard's premise cannot hold, so it is SKIPPED, mirroring
        # ``invalidate_point``. (Pre-fix the guard ran first and refused a
        # request that writes no end at all: fail-closed, but wrong for its
        # stated reason and unreachable at the sibling door.) Otherwise the
        # predecessor's end is the successor's RESOLVED start (a USABLE
        # valid_from kwarg, which wins outright — agreed with the stored start
        # whenever one exists → its own validFrom → its createdAt → now), and it
        # inherits the successor's declared start precision ONLY when the
        # resolved end names the successor's start instant (#9729): a
        # declaration annotates a bound, it cannot summon one (#9244), so a
        # fabricated fallback instant carries no token.
        from .validity import PRECISION_UNKNOWN as _PREC_UNKNOWN
        from .validity import normalize_precision as _normalize_precision
        successor_created_at = vf_rows[0][1] if vf_rows else None
        end_precision = _normalize_precision(
            valid_to_precision, field="valid_to_precision")
        if (end_precision is None and vf_rows
                and _supersede_window_start_source(
                    stored_vf, successor_created_at) is stored_vf):
            # #9729: the declared token annotates the successor's START, so it
            # may ride onto the predecessor's END only when that END came FROM
            # that start. `_supersede_window_start_source` is the ONE HOME for
            # "which source does the end resolve from" — an unorderable start
            # falls through to `createdAt`/`now`, instants nobody declared a
            # granularity for, and stamping the declaration there is the #9244
            # lie one surface over: a declaration annotates a bound, it cannot
            # summon an instant. (Guarding the SOURCE, not `createdAt`'s
            # orderability, covers both fallback steps with the same predicate
            # the writer itself resolves through, so the two cannot drift. The
            # `is` test is exact because that helper RETURNS the argument it
            # accepted — a future normalization inside it must keep doing so,
            # or inheritance goes silent. The rung AHEAD of it, the caller's
            # `valid_from` kwarg, is pinned to the same instant by the
            # agreement guard above — relax that guard and this gate must be
            # re-derived.)
            end_precision = vf_rows[0][2] or None
        _end_is_unknown = end_precision == _PREC_UNKNOWN
        succ_vf = _supersede_window_end(
            old_id=old_id, new_id=new_id, old_vfs=old_vfs,
            valid_from=valid_from, stored_vf=stored_vf,
            successor_created_at=successor_created_at,
            now=now,
            skip_inversion_guard=_end_is_unknown,
        )
        stamp_valid_to = None if _end_is_unknown else succ_vf
        # #2423 (rebuild-parity fix): kwargs-style emission (id + extra keys)
        # so the FULL payload rides the JSONL line — the previous dict-style
        # emission only reached the :GraphEvent store (payload) while the
        # JSONL line carried bare id=old_id, dropping new_id/valid_from/
        # valid_to/expired_at. Rebuild's PointSuperseded fold (pass-1b)
        # needs new_id + the bi-temporal stamps to re-stamp the superseded
        # point verbatim (#2423 indicator 1) — a JSONL wipe+rebuild of a
        # dict-style journal folds status-only (new_id missing) and warns.
        self._emit_event(
            "PointSuperseded",
            id=old_id, new_id=new_id,
            valid_from=succ_vf, valid_to=stamp_valid_to,
            valid_to_precision=end_precision, expired_at=now,
            # #5048: `now` is ALSO the envelope instant, so the replay's
            # `updated_at = _journal_instant(ev)` reads the instant the live
            # SET below writes. Without it the envelope `ts` was a SECOND
            # clock read and every supersede drifted live vs rebuilt by
            # microseconds — the same defect #5048 fixes for retraction, and
            # the rule `invalidate_point` already documents ("ts=now MUST be
            # passed … a drift from the live SET clock breaks exact-stamp
            # rebuild parity"). `recorded_ts`, not `ts`: the latter would also
            # add the key to the :GraphEvent payload (see `_emit_event`).
            recorded_ts=now,
        )

        # CYCLE-26 REVIEW-FIX P1 (cycle-7 pin): the superseded-status write +
        # CORRECTS edge MOVE AFTER the transfers (2a / 2a-DIRECT / 2b). The
        # transfers read old's edges directly and don't depend on the status;
        # under the pinned order a crash mid-transfer leaves old NOT yet
        # terminal, and a RE-RUN converges (the terminal guard never fires
        # on the retry). The old order (status first) left a terminal point
        # with EP-active incident direct edges and no remediation short of
        # rebuild_all.

        # 2a. Transfer operator edges (IMPL, NAND, hasPart) — preserve provenance

        # #6180: collect the INPUT mirrors BEFORE any mutation, beside the
        # typed edges (the #329 collect-then-mutate discipline).
        # ``(support)-[:INPUT {idx}]->(operator)`` is the operator's SUPPORT
        # SOURCE: why.py::_SUPPORT_OP_CYPHER reads
        # ``(sup)-[INPUT {idx:0}]->(op)-[:IMPL]->(n)`` and EP's factor
        # extraction reads the same INPUTs. Leaving the mirror on the dead
        # point had both effects the issue reports — the replacement's
        # why/support chain went empty, and the superseded point kept
        # exerting influence through the operator. Driven by the INPUT edges
        # themselves (not by the typed-edge loop) so an operator whose
        # op_type carries no typed edge still transfers its support source,
        # and the rebuild pass-2b leg mirrors this query exactly — SAME
        # canonical operator population since #10418 review P3, which widened
        # the replay's two pass-2b predicates to this disjunction (a replay
        # reading the narrower ``is_operator:true`` would have left a flag-less
        # operator's INPUT mirror stranded on the superseded prior).
        input_rows = proj.g.query(
            "MATCH (old:Point {id:$old_id})-[ri:INPUT]->(op:Point) "
            "WHERE (op.is_operator = true OR op.op_type IS NOT NULL) "
            "RETURN op.id, ri.idx, ID(ri), op.label",
            params={"old_id": old_id},
        ).result_set

        transferred = 0
        for row in edges_result.result_set:
            op_id, edge_type, idx = row[0], row[1], row[2]
            op_label = row[3] if len(row) > 3 else None
            rid = row[4] if len(row) > 4 else None
            if op_label == "alreadyDecided":
                # #1080 review: dedup context edges must NOT be re-pointed at
                # the replacement — an alreadyDecided IMPL on the superseded
                # prior declares the OLD decision a duplicate, not the new one.
                _logger.info(
                    "supersede_point: keeping alreadyDecided operator %s "
                    "attached to the superseded point %s (dedup context edge)",
                    op_id, old_id,
                )
                continue
            # Create new edge: operator → new point (same idx preserves
            # source/target position). #6180: MERGE, not CREATE — a caller
            # that pre-wired the shared edge onto the successor (the
            # successor-first write ordering the #6180 report reproduces) got
            # a SECOND parallel edge here, inflating the operator's evidence
            # weight while passing every "is the edge there?" check. idx
            # rides the MERGE pattern, so the two edges
            # create_operator(op, old, [old]) mints (idx 0 and idx 1) stay
            # distinct and both transfer.
            #
            # #6180 null-idx leg: an operator edge may carry NO idx at all —
            # `mitigate_operator` writes `(m)-[:IMPL]->(op)` and
            # `(op)-[:mitigated_by]->(m)` with no idx property, so `r.idx` is
            # null here. `MERGE … {idx:null}` is REFUSED by FalkorDB ("Cannot
            # merge node using null property value") and a `{idx:null}` MATCH
            # never matches, so the null class must merge on the bare pattern
            # (type + endpoints), exactly as the 2a-DIRECT leg below does.
            if idx is None:
                proj.g.query(
                    f"MATCH (op:Point {{id:$op_id}}), (new:Point {{id:$new_id}}) "
                    f"MERGE (op)-[:{edge_type}]->(new)",
                    params={"op_id": op_id, "new_id": new_id},
                )
            else:
                proj.g.query(
                    f"MATCH (op:Point {{id:$op_id}}), (new:Point {{id:$new_id}}) "
                    f"MERGE (op)-[:{edge_type} {{idx:$idx}}]->(new)",
                    params={"op_id": op_id, "new_id": new_id, "idx": idx},
                )
            # Delete the old edge by IDENTITY, never by an `{idx:$idx}` pattern:
            # at idx null such a pattern matches nothing (the edge would be
            # stranded), and even at a known idx the identity is the precise
            # key. The 2a-INPUT leg and the rebuild replay already delete by
            # `ID(r)`; 2a now matches them.
            proj.g.query(
                f"MATCH (op:Point {{id:$op_id}})-[r:{edge_type}]->"
                f"(old:Point {{id:$old_id}}) "
                f"WHERE ID(r) = $rid DELETE r",
                params={"op_id": op_id, "old_id": old_id, "rid": rid},
            )
            transferred += 1

        # 2a-INPUT (#6180): the operator's reverse INPUT mirror follows its
        # support source onto the successor. MERGE, like 2a — a successor
        # that already carries the mirror must not get a second one. The
        # alreadyDecided carve-out mirrors 2a: an operator deliberately kept
        # attached to the dead prior keeps its INPUT there too.
        for row in input_rows:
            op_id, idx = row[0], row[1]
            rid = row[2]
            op_label = row[3] if len(row) > 3 else None
            if op_label == "alreadyDecided":
                continue
            # #6180 null-idx leg: the mirror may carry no idx (see 2a) — merge
            # on the bare pattern then, because `{idx:null}` is refused by
            # MERGE and never matched by MATCH.
            if idx is None:
                proj.g.query(
                    "MATCH (new:Point {id:$new_id}), (op:Point {id:$op_id}) "
                    "MERGE (new)-[:INPUT]->(op)",
                    params={"new_id": new_id, "op_id": op_id},
                )
            else:
                proj.g.query(
                    "MATCH (new:Point {id:$new_id}), (op:Point {id:$op_id}) "
                    "MERGE (new)-[:INPUT {idx:$idx}]->(op)",
                    params={"new_id": new_id, "op_id": op_id, "idx": idx},
                )
            proj.g.query(
                "MATCH (old:Point {id:$old_id})-[ri:INPUT]->"
                "(op:Point {id:$op_id}) "
                "WHERE ID(ri) = $rid DELETE ri",
                params={"old_id": old_id, "op_id": op_id, "rid": rid},
            )

        # 2a-DIRECT (epic #902 §8 / plan §5.3 review fix): transfer
        # OPERATOR-LESS direct IMPL/NAND edges incident to the superseded
        # point, BOTH directions:
        #   (old)-[:IMPL|NAND]->(x)  and  (x)-[:IMPL|NAND]->(old)
        # Repointed to new_id preserving type/direction/confidence/weight/
        # label/batch_id (E2E-11.6: zero direct edges remain incident to a
        # superseded point, live AND post-rebuild). The REPOINT descriptor is
        # emitted BEFORE the transfer (EMIT-BEFORE-TRANSFER, §4.4) so a
        # transfer×emission crash cannot leave the stale descriptor in the
        # JSONL while the live edge moves.
        # #8622: the OTHER endpoint may carry ANY of the four epistemic
        # labels (#7902 / owner ruling #7813 — a direct IMPL/NAND is explicitly
        # allowed to connect any two of :Point/:Subject/:Object/:Event). The
        # transfer legs therefore anchor each endpoint on the label that
        # actually resolved it; a hardcoded ``:Point`` on the far endpoint
        # matched NOTHING, so the MERGE was a constrained no-op and the
        # delete-leg below removed the ORIGINAL edge — the transfer silently
        # DESTROYED the edge live, and the replay (which received the same
        # endpoint contract) dropped it too. ``labels(t)`` rides the same
        # traversal, so no extra round trip is spent resolving the far node.
        # #10139: ONE vocabulary for the carried attributes — the two 2a-DIRECT
        # legs below, the 2b emission further down and the pass-2b replay that
        # re-applies them all read `DIRECT_EDGE_ATTR_KEYS` (`projection/edges.py`).
        # Spelling the same five keys out at each site is the drift that left the
        # REBUILD half bare while the live half carried the value.
        from .projection.edges import DIRECT_EDGE_ATTR_KEYS

        # ⛔ The population is re-read PER DIRECTION here, even though the
        # #9727 pre-flight above already collected it: that ORDER is
        # load-bearing, not redundant. A self-loop ``(old)-[T]->(old)`` belongs
        # to BOTH populations, and the ``out`` pass MUTATES it (MERGE
        # ``(new)-[T]->(old)``, delete the loop) — the ``in`` pass then sees
        # that new edge as its ``tid == new_id`` row and deletes it. Serving the
        # ``in`` pass a row set captured BEFORE the ``out`` pass re-pointed the
        # loop a second time (measured: two residual direct edges on the
        # superseded point plus a phantom ``DirectEdgeRepoint`` — the E2E-11.6
        # "zero direct edges remain incident" contract at the top of this
        # block, and ``tests/test_dry_run_preview.py``'s two self-loop cases).
        # So ``_direct_incident_edges`` is SHARED as one collection
        # IMPLEMENTATION (the door and the mutation cannot disagree about what
        # an edge set IS), while each pass reads its own current state.
        for direction in ("out", "in"):
            rows = _direct_incident_edges(direction)
            for row in rows:
                rtype, r, tid, rid = row[0], row[1], row[2], row[3]
                # #8622: the label that ACTUALLY carries the far endpoint, in
                # EPISTEMIC_LABELS order (Point-first) — the same first-wins
                # order ``create_direct_edge``'s probe uses, so the write and
                # the transfer can never disagree about a multi-labelled node.
                _row_labels = set(row[4] or ()) if len(row) > 4 else set()
                tid_label = next(
                    (lbl for lbl in EPISTEMIC_LABELS if lbl in _row_labels),
                    None)
                # FalkorDB returns an Edge object — read its properties.
                rdict = dict(r.properties) if hasattr(r, "properties") else {}
                if tid == new_id:
                    # REVIEW-FIX P1 (cycle-26): the edge already terminates at
                    # the successor — repointing would CREATE A PHANTOM
                    # SELF-EDGE (new)->(new) (the MERGE runs before the old
                    # delete, and no existing (new)->(new) edge collapses it).
                    # The old edge is DELETED and NO repoint descriptor is
                    # emitted (pass-2b must not recreate the self-edge
                    # post-rebuild). E2E-11.6: zero direct edges incident to
                    # a superseded point, live AND post-rebuild.
                    proj.g.query(
                        f"MATCH (a)-[r:{rtype}]->(b) WHERE ID(r) = $rid DELETE r",
                        params={"rid": rid},
                    )
                    transferred += 1
                    continue
                _attrs = {k: v for k, v in rdict.items()
                          if k in DIRECT_EDGE_ATTR_KEYS}
                # EMIT-BEFORE-TRANSFER: the REPOINT descriptor (plan §4.4 —
                # A10's pass-2b repoint apply consumes it post-rebuild).
                # Flat descriptor shape (REVIEW-FIX P2: match
                # DirectEdgeCreated's §4.4 canonical flat shape — A10's
                # pass-2b reads the same fields from both record types).
                self._emit_event(
                    "DirectEdgeRepoint",
                    id=f"{old_id}->{new_id}:{rtype}",
                    src=(tid if direction == "in" else new_id),
                    tgt=(new_id if direction == "in" else tid),
                    edge_type=rtype,
                    **{k: v for k, v in _attrs.items()},
                )
                # MERGE-collapse (count()==1 twin guard): the MERGE matches
                # the bare pattern; if the target edge already exists it
                # converges instead of duplicating. rtype is validated
                # IMPL/NAND (from the query) — interpolated into the MERGE
                # pattern (FalkorDB requires ONE relationship type per
                # MERGE pattern).
                # MERGE (bare) then SET separately — MERGE+SET in ONE
                # statement matches the full pattern (attrs included) and
                # creates a PARALLEL edge on attribute change; the two-
                # statement form collapses to one edge and last-writer-wins
                # on attrs (the plan's no-parallel-direct-edges contract).
                # Two-step MERGE (FalkorDB quirk — same as create_direct_edge):
                # MATCH the existing nodes, then MERGE the edge between them
                # (never creates duplicate nodes; collapses to one edge).
                # #8622: anchor BOTH endpoints on their resolved labels. An
                # unresolvable label means the endpoint is not an epistemic
                # node (a raw/legacy edge the write path itself refuses), so
                # the transfer cannot be performed — the delete-leg below still
                # runs, mirroring the pre-#7902 "no such endpoint" outcome
                # WITHOUT the silent hope that a :Point match existed.
                if new_label is not None and tid_label is not None:
                    if direction == "out":
                        mq = (f"MATCH (new:{new_label} {{id:$new_id}}), "
                              f"(t:{tid_label} {{id:$tid}}) "
                              f"MERGE (new)-[nr:{rtype}]->(t)")
                    else:
                        mq = (f"MATCH (t:{tid_label} {{id:$tid}}), "
                              f"(new:{new_label} {{id:$new_id}}) "
                              f"MERGE (t)-[nr:{rtype}]->(new)")
                    proj.g.query(mq, params={"new_id": new_id, "tid": tid})
                    _keep = {k: v for k, v in rdict.items()
                             if k in DIRECT_EDGE_ATTR_KEYS}
                    if direction == "out":
                        _a_label, _b_label = new_label, tid_label
                    else:
                        _a_label, _b_label = tid_label, new_label
                    proj.g.query(
                        f"MATCH (a:{_a_label} {{id:$a}})-[r:{rtype}]->"
                        f"(b:{_b_label} {{id:$b}}) SET r += $attrs",
                        params={"a": (tid if direction == "in" else new_id),
                                "b": (new_id if direction == "in" else tid),
                                "attrs": _keep},
                    )
                # Delete the OLD edge by its internal id (precision),
                # constrained to the old point's incident edges (REVIEW-FIX
                # P2: never graph-wide — internal ids may be reused).
                if direction == "out":
                    proj.g.query(
                        f"MATCH (old:Point {{id:$old_id}})-[r:{rtype}]->() "
                        f"WHERE ID(r) = $rid DELETE r",
                        params={"rid": rid, "old_id": old_id},
                    )
                else:
                    proj.g.query(
                        f"MATCH ()-[r:{rtype}]->(old:Point {{id:$old_id}}) "
                        f"WHERE ID(r) = $rid DELETE r",
                        params={"rid": rid, "old_id": old_id},
                    )
                transferred += 1

        # 2b. Transfer plain structural edges (#122) — about*, extractedFrom, wasDerivedFrom, etc.
        # These edges connect the Point to entities (Subject, Object, Source, etc.)
        #
        # #2489 (structural-edge transfer parity): the transfer is now JOURNALED
        # for BOTH structural families (see `ID_ADDRESSED_STRUCTURAL_RELS`,
        # #9836) as flat DirectEdgeRepoint descriptors {src=old_id, tgt=<replay
        # key>, target_label=<label>, edge_type=<rel>} emitted BEFORE the transfer
        # (EMIT-BEFORE-TRANSFER, §4.4 — same crash discipline as 2a-DIRECT):
        #
        #   * the SNAPSHOT-DERIVABLE family (extractedFrom +
        #     aboutSubject/Object/Event/Document/Point) keys its target by the
        #     node's NATURAL key (`stub_key`). Rebuild pass-2 resurrects these
        #     edges at OLD from the point's immutable snapshot (extractedFrom prop
        #     / aboutEntities list), so the pass-2b replay must move them to the
        #     successor THAT SUPERSEDE NAMED (`succ[src]`, ONE HOP per descriptor —
        #     NOT the terminal `_final(src)`, which is only the skip guard) AND
        #     delete the resurrection at old.
        #   * the ID-ADDRESSED family (`aboutAction`, `wasDerivedFrom`, #9836)
        #     keys its target by the node's OWN `id` — or by its `url` when the
        #     target is a `:Source` — and carries the label it observed live: the
        #     only addressing that resolves for them (a Point has no `name`;
        #     `wasDerivedFrom`'s target label is not a function of the rel).
        #     Nothing ever resurrects these at old, so the descriptor's whole job
        #     is the MOVE, which live already performs.
        # The journal carries the target's LOGICAL identity only (`tgt=<key>` or,
        # for the delete-only guard, `tgt=new_id`) — never the FalkorDB internal
        # ID (internal ids die at rebuild).
        from .projection.edges import (
            DERIVABLE_STRUCTURAL_RELS,
            ID_ADDRESSED_STRUCTURAL_RELS,
            STRUCTURAL_REL_LABELS,
            STRUCTURAL_REPOINT_RELS,
            id_addressed_target,
            stub_key,
        )
        structural_rels = SUPERSEDE_STRUCTURAL_RELS
        # Successor internal node ids — runtime-only (never journaled). The 2b
        # no-self-edge guard compares the structural target's NODE IDENTITY to
        # EVERY successor node (mirror 2a-DIRECT's tid==new_id guard at ~4407):
        # without it the MERGE below mints a phantom (new)-[:rel]->(new)
        # self-edge when old's edge terminates at a successor (cycle-26 class —
        # e.g. old X-[:aboutPoint]->Y live, supersede(X->Y)).
        #
        # #7822: the whole fan-out, not ``succ_rows[0][0]``. Point ids are not
        # unique, so the successor is not one node: the MERGE below binds EVERY
        # node carrying ``new_id`` and a first-row-only scalar protected only
        # the row the server happened to return first, minting the phantom at
        # the LATER successor nodes — the verdict was server-row-order
        # dependent.
        succ_rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN ID(n)",
            params={"id": new_id},
        ).result_set
        succ_internals = {row[0] for row in succ_rows}
        # #7822: the delete-only descriptor keys its replay delete-leg on the
        # LOGICAL successor id and the replay deletes EVERY (old)-[rel]->(t)
        # edge, so ONE descriptor per rel covers every successor node the
        # fan-out matched. Emitting one per matching node would journal N
        # identical descriptors for the same operation.
        emitted_self_deletes: set = set()
        for rel in structural_rels:
            derivable = rel in DERIVABLE_STRUCTURAL_RELS
            struct_rows = proj.g.query(
                f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(target) "
                # ⛔ #9417: the EDGE's properties are selected too. The transfer
                # below recreates the relationship on the successor, and a bare
                # MERGE mints it with NO properties — so an `about*` edge carrying
                # the subject binder's `confidence` (session_link.py:381,
                # subject_binding.py:481) lost it on supersede. Selecting
                # `properties(r)` here is what lets the transfer carry it across.
                f"RETURN id(target), target.id, labels(target), properties(target), "
                f"properties(r)",
                params={"old_id": old_id},
            ).result_set
            for row in struct_rows:
                # Row shape: [internal id, logical id, labels, properties,
                # edge properties] —
                # #2489 extends the SELECT with properties(target) so the shared
                # stub_key resolver (projection/edges.py) extracts the per-rel
                # replay key at emission: `name` for aboutSubject/Object/Event/
                # Point, `url` for aboutDocument and extractedFrom (D10 — a
                # document IS a :Source). The retired pre-D10 title fallback key
                # is NOT one: `stub_key` never returns it (a title-only target
                # yields None), so such a descriptor cannot be emitted at all.
                # ONE key map in this loop — the emission comment below states
                # the same, and the two must not drift apart (#5265).
                target_graph_id = row[0]  # FalkorDB internal node id — exact match
                target_props = row[3] or {}
                # #9417: the old relationship's own properties (row[4]) — carried
                # onto the successor edge below, never silently dropped. WHITELISTED
                # to the `RelationUpsert.attrs` vocabulary (the set the replay
                # contract understands) — copying `properties(r)` raw would carry
                # EP-managed / internal fields onto the successor.
                # ⛔ NOT `source_version`: it is the RECORD column name, and this
                # file's graph rels carry the anchor as `sourceVersion`
                # (`_anchor_on_create`, `projection/edges.py`) — nothing writes
                # `r.source_version`, so the entry could never match and only
                # implied a carry that never happened. It is also the one key the
                # record contract forbids re-applying on a re-link ("a re-link must
                # not ADVANCE a recorded version" — `_relation_upsert_statement`).
                # ⚠️ SCOPE: `extractedFrom`'s `sourceVersion` anchor is therefore
                # STILL dropped on transfer — a separate, pre-existing loss this
                # vocabulary does not own. Stated here rather than letting the
                # word "never" above imply otherwise.
                # ⛔ ONE vocabulary, imported: `DIRECT_EDGE_ATTR_KEYS`
                # (`projection/edges.py`) is the SAME tuple the pass-2b replay
                # branch reads a descriptor's payload through (#10139), so the
                # live carry and the rebuilt carry cannot disagree about which
                # keys a supersede moves.
                edge_props = {
                    k: v for k, v in (row[4] or {}).items()
                    if k in DIRECT_EDGE_ATTR_KEYS
                }
                if target_graph_id in succ_internals:
                    # #2489 no-self-edge guard: the structural target IS the
                    # successor node. Delete the old edge ONLY — no transfer (a
                    # transfer would MERGE the phantom (new)-[:rel]->(new)).
                    # A DELETE-ONLY descriptor is emitted for EVERY rel that
                    # carries a repoint descriptor (`STRUCTURAL_REPOINT_RELS`),
                    # because on the RECORD route that descriptor is not a no-op:
                    # it is the ONLY thing that removes the row the pre-supersede
                    # `DirectEdgeCreated` wrote at the old point.
                    # ⛔ #9836 REVIEW (P2) — this arm used to emit for the
                    # DERIVABLE family only, reasoning that nothing ever re-creates
                    # an ID-ADDRESSED old-side edge so a delete-leg there would be a
                    # "permanent no-op". True of the GRAPH leg (pass-2 does not
                    # resurrect these rels; the `DirectEdgeCreated` fold skips the
                    # creation outright), and FALSE of the RECORD leg: the record
                    # holds the flat `DirectEdgeCreated` row at `src=old` (written
                    # by `_record_structural_edge` when the edge was created), the
                    # record applies records in ORDER with no graph state, and the
                    # ONLY statement that removes a relation row at a retired point
                    # is a descriptor's delete leg (`record_projection:4132`
                    # honours `delete_only` for ANY rel). So a link from the old
                    # point to its own successor survived AT the superseded point
                    # on the record route — `live != record-rebuild`, on the exact
                    # residual #9836 exists to close.
                    # `emitted_self_deletes` keys on rel: ONE descriptor per rel
                    # covers every successor node the fan-out matched (#7822), and
                    # the replay deletes every (old)-[rel]->(t) edge.
                    if rel in STRUCTURAL_REPOINT_RELS and rel not in emitted_self_deletes:
                        emitted_self_deletes.add(rel)
                        self._emit_event(
                            "DirectEdgeRepoint",
                            id=f"{old_id}->{new_id}:{rel}:self",
                            src=old_id, tgt=new_id,
                            # The derivable family's label is a function of the
                            # rel; the ID-ADDRESSED family's is not, so it rides
                            # the label the transfer OBSERVED on the target — the
                            # same value the record files with. (Here the target IS
                            # the successor, so the observed label is `Point` in
                            # the ordinary case; reading it beats asserting it.)
                            target_label=(
                                STRUCTURAL_REL_LABELS.get(rel)
                                or (id_addressed_target(row[2], target_props) or (None,))[0]
                            ),
                            edge_type=rel, delete_only=True,
                        )
                    proj.g.query(
                        f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(t) "
                        f"WHERE id(t) = $tid DELETE r",
                        params={"old_id": old_id, "tid": target_graph_id},
                    )
                    transferred += 1
                    continue
                # #2489: per-rel key extraction via the SHARED resolver —
                # never target.id for the NAME-KEYED family (Subjects MERGE by
                # name, Sources by url — and D10: a
                # document IS a :Source, so aboutDocument and extractedFrom both
                # key by `url`, the SAME key the target label moved with; a
                # title-keyed aboutDocument descriptor is no longer resolvable and
                # is never emitted). SKIP unresolvable keys (name-less Point from
                # an id-targeted create_about_edge; a Source target lacking url):
                # a null-key descriptor is un-replayable — those edges die at
                # rebuild today anyway (zero regression).
                # ⛔ #9836: the ID-ADDRESSED family has its OWN arm
                # (`elif rel in ID_ADDRESSED_STRUCTURAL_RELS` below) because these
                # rels have no natural key to read — which is WHY they are a
                # separate family. Their target is keyed by its own `id`, or by its
                # `url` when it is a `:Source` (a Source's identity IS its url, and
                # its `url` is likewise the record's `node_key` for it). The two
                # branches are mutually exclusive and the rel set decides which one
                # runs; nothing here guesses per target.
                if derivable:
                    keyed = stub_key(rel, target_props)
                    if keyed is not None:
                        target_label, target_key = keyed
                        # EMIT-BEFORE-TRANSFER: the structural REPOINT descriptor
                        # (A10/pass-2b replay consumes it post-rebuild). Event id
                        # gets a target-key suffix — multi-target same-rel id
                        # hygiene (2a-DIRECT's id base is
                        # f"{old_id}->{new_id}:{rtype}").
                        # #10139: the descriptor carries the edge's
                        # ATTRIBUTES too, not only its topology. Without them the
                        # replay can only re-MERGE a BARE edge, so the property
                        # #9417's live transfer carries (`confidence` from the
                        # subject binder) survived a supersede and was dropped by
                        # the next `rebuild_all` — a `live != rebuild` divergence
                        # on a data-bearing property. FLAT keys, exactly as the
                        # 2a-DIRECT descriptor above spreads them and exactly as
                        # `record_projection._relation_coordinates`' consumer reads
                        # an attribute payload (`{k: payload[k] for k in
                        # rel.attrs}` — flat, by NAME); a nested `attrs` map would
                        # be a third shape nothing in the tree reads.
                        # ⛔ Emitted ONLY when the old edge actually had one of the
                        # vocabulary's keys: the replay must be able to tell "no
                        # attributes to carry" from "carry an empty map", and an
                        # empty map is not writable by FalkorDB (the projection
                        # degrades it to null with a `#7369` warning).
                        self._emit_event(
                            "DirectEdgeRepoint",
                            id=f"{old_id}->{new_id}:{rel}:{target_key}",
                            src=old_id, tgt=target_key,
                            target_label=target_label,
                            edge_type=rel,
                            **edge_props,
                        )
                elif rel in ID_ADDRESSED_STRUCTURAL_RELS:
                    # #9836 — the ID-ADDRESSED family (`aboutAction`,
                    # `wasDerivedFrom`): transferred by this leg, but NOT
                    # snapshot-derivable, so before this they moved LIVE and
                    # journaled NOTHING — the journal route then lost the edge
                    # (its `DirectEdgeCreated` is skipped by the superseded-
                    # endpoint gate, and pass-2 never resurrects them) while the
                    # record route kept the pre-supersede row. The `derivable`
                    # gate above was the whole of that loss.
                    # ⛔ The target is addressed the way the rest of the tree
                    # addresses it, and the scheme is a property of the OBSERVED
                    # LABEL (`ID_ADDRESSED_TARGET_ADDRESSING`): a Point/Subject/
                    # Object by its own `id`, an `Event` by its `eventId` (the
                    # meeting guard can give two distinct Events one `id`, so `id`
                    # names no Event identity; `eventId` is what the record files an
                    # Event under), Point-first via the fold's own `EPISTEMIC_LABELS`
                    # order — the SAME rule the 2a-DIRECT leg above applies to a far
                    # endpoint, #8622 — and a `:Source` by
                    # its `url` — a Source's identity IS its url, and BOTH replay
                    # routes already resolve one that way (`resolve_structural_target(
                    # by_url=True)`; `relation.tgt_key`). A name key is unavailable
                    # by construction: a Point has no `name`, and `wasDerivedFrom`'s
                    # target label is not a function of the rel (ONTOLOGY §3.2 says
                    # Object->Object; the writers type-check nothing). The label
                    # rides `target_label`, which is what that field means on the
                    # structural route.
                    # SKIP (never half-journal) a target with NO addressable label
                    # and no url — filing a descriptor for it would make the record
                    # RICHER than the graph. Same reason, same helper as the 2a
                    # leg's label resolution. ⚠️ An earlier revision of this arm
                    # skipped `:Source` on the claim that a source target "cannot be
                    # addressed"; it can — by url, on both routes — and the skip
                    # is what left the edge moved live and present on NEITHER
                    # rebuild (measured, review P1).
                    id_keyed = id_addressed_target(row[2], target_props)
                    if id_keyed is not None:
                        target_label, target_key = id_keyed
                        self._emit_event(
                            "DirectEdgeRepoint",
                            id=f"{old_id}->{new_id}:{rel}:{target_key}",
                            src=old_id, tgt=target_key,
                            target_label=target_label,
                            edge_type=rel,
                            **edge_props,
                        )
                # Create new edge: new point → same target (MERGE = idempotent,
                # no dupes).
                # ⛔ #9417: the old edge's properties are carried across, because a
                # bare MERGE recreates the `about*` edge with NO properties and the
                # binder's `confidence` is then DROPPED, not copied.
                # ⛔ `ON CREATE SET`, NOT a bare `SET`. `SET nr += $attrs` overwrites
                # PER KEY, so an unconditional carry made the RETIRED point's value
                # win over a confidence the SUCCESSOR already held on the same
                # target — measured: old 0.42 + successor's own live 0.91 → 0.42,
                # successor destroyed (target: #9417 review P1). The collision is
                # the COMMON case: `supersede_point` is the RESTATEMENT path, so a
                # restated claim about the same subject is bound to the same
                # Subject by `bind_point_subjects` and both edges exist. In a chain
                # (X→Y→Z) each transfer would re-stamp the newest successor with the
                # oldest ancestor's value, and at the time the descriptor carried no
                # `attrs`, so the overwritten value was journaled NOWHERE and could
                # not be rebuilt. ⛔ That is fixed since #10139 (the descriptor
                # carries the values flat) — the wording above is the HISTORY of why
                # `ON CREATE` is the form, not a live claim about the descriptor.
                # ⛔ PRECEDENCE, stated: the successor's own live value WINS. That is
                # what the pre-#9417 code did (a bare MERGE left a pre-existing edge
                # untouched), so this preserves it while still refusing to leave a
                # NEWLY created edge bare. A successor edge that exists but is
                # partially populated is deliberately NOT topped up — its own state
                # is authoritative.
                # ⛔ params carry `attrs` ONLY when there is something to carry — an
                # empty map is not writable by FalkorDB and the projection degrades
                # it to null with a #7369 warning, so the empty case must stay a
                # plain MERGE with no `attrs` key at all.
                _params = {"new_id": new_id, "tid": target_graph_id}
                _carry = ""
                if edge_props:
                    _carry = "ON CREATE SET nr += $attrs"
                    _params["attrs"] = edge_props
                proj.g.query(
                    f"MATCH (new:Point {{id:$new_id}}), (t) WHERE id(t) = $tid "
                    f"MERGE (new)-[nr:{rel}]->(t) {_carry}",
                    params=_params,
                )
                # Delete old edge (match by exact internal node id)
                proj.g.query(
                    f"MATCH (old:Point {{id:$old_id}})-[r:{rel}]->(t) WHERE id(t) = $tid "
                    f"DELETE r",
                    params={"old_id": old_id, "tid": target_graph_id},
                )
                transferred += 1

        # 1. Mark old superseded + outdated + create CORRECTS edge — AFTER the
        # transfers (CYCLE-26 REVIEW-FIX P1 / plan §5.3 cycle-7 pin: the crash
        # window between the status write and the transfers is closed by
        # re-run convergence; this block is the last mutation before dreaming).
        # #432: status='superseded' alongside the legacy outdated=true flag
        # (back-compat for consumers reading the flag; #690 will consolidate).
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'superseded', "
            "n.outdated = true, n.updatedAt = $now, "
            "n.validTo = $valid_to, n.validToPrecision = $valid_to_precision, "
            "n.expiredAt = $expired_at, "
            f"{decay_clause('n')}",
            params={"id": old_id, "now": now,
                    "valid_to": stamp_valid_to,
                    "valid_to_precision": end_precision,
                    "expired_at": now},
        )
        proj.g.query(
            "MATCH (a:Point {id:$new_id}), (b:Point {id:$old_id}) "
            "MERGE (a)-[:CORRECTS]->(b)",
            params={"new_id": new_id, "old_id": old_id},
        )

        # Dreaming (#85): supersede changes the propagation graph around both.
        # #2422 ORDERING: the message drops run BEFORE _mark_dirty's epoch
        # bump — a concurrent EP run that observes the new epoch must also
        # observe the dropped messages (a run loading its message cache after
        # the version bump but before the drops would hydrate the stale
        # sibling message and flush a ghost-contaminated posterior past the
        # stale-run guard).
        # Epic 903-C9 (#1247): edge TRANSFER — invalidate warm-start seeds on
        # both endpoints' edges (supersede transfers edges with their msg_*
        # properties; the seeds are computed under the old factor context).
        self._get_ep().invalidate_messages([old_id, new_id])
        # #2422 (ghost-vote leg): a superseded point's influence also rides
        # the SIBLING edges of its operators (op1→B carries A's message into
        # B on an edge touching neither old nor new) — and the #1080
        # alreadyDecided carve-out keeps some operators attached to the dead
        # old point permanently. A draft successor (legal) makes the operator
        # degenerate: it never re-runs to zero the stale sibling message. Drop
        # every operator-connected edge's message for both endpoints.
        self._get_ep().invalidate_factor_messages([old_id, new_id])
        self._mark_dirty([old_id, new_id])

        return {
            "invalidated": True,
            "id": old_id,
            "corrected_by": new_id,
            "edges_transferred": transferred,
        }
