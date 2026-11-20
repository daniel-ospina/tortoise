# Archived sdk-method `update_point` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_update_point`; recorded destination `update_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'update_point'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    @_declares_apply_first_write_lane("update_point")
    def update_point(self, id: str, *, is_episodic: bool | None = None, **props) -> dict:
        """Update properties on an existing Point. Returns updated point dict.

        Implementation behind update(id, ...) — the consolidated Point/entity
        update (epic #888 W2).

        For :Object-labeled nodes, version is auto-incremented on every update.
        Status changes are validated against POINT_STATUS_VALUES.

        ``credibility`` is refused here (#7951) — it is a documented word input
        of ``create_point``/``mitigate_operator``, not a generic property.

        ``content=None`` is NOT a content edit (#5240) — it is treated exactly
        like an absent key, so it neither re-derives the hash/vector nor
        touches ``n.content``. An EMPTY STRING is a real edit (it hashes and
        stores ``""``); only ``None`` means "nothing to set".
        """
        proj = self._get_proj()
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        # ⛔ #13143 review (F1, P1): FIRST, before any of this door's own
        # `'status' in props` arms — they test the LITERAL name while the
        # transport applies `str(key)`, so a renderer-named key was written as
        # `status` by `SET n += $props` and never graded (measured:
        # `update_point({<K content 'zzz', __str__ 'status'>: 'garbage'})`
        # STORED `status='garbage'`, where `update_knowledge` refused it).
        # Re-filing it under the literal spelling makes every arm below see the
        # same write the transport will apply, and refuses a two-key bag whose
        # landing word cannot be known (#12786).
        _rehome_rendered_status_key(
            props, door="update_point", label=f"point {id!r}")
        # #10000 / §9.1: `update_point` writes with a raw `SET n += $props` and
        # consults no point deny-set, so it accepted the narrative and journalled
        # it as `PointRevised` — measured at #10389 review cycle 3, which is also
        # why this cannot be left to the fold's `_POINT_DENY` alone: a rebuild
        # would then DELETE a property the live write accepted (live ≠ replay).
        _refuse_unpaired_narrative(props, door="update_point", label="Point",
                                   journal_armed=bool(self._event_log_path))
        # #9287: an UPDATE is a WRITE, and ``pointKind`` is writable through it
        # (measured: ``update_point(id, pointKind='\u2003observation\u2003')``
        # stored the padded value and the gate never demanded it) — the same
        # invisible-kind refusal the create door applies, at ITS own door.
        self._refuse_invisible_point_kinds(props, door="update_point")
        # ── #5475 (residual): an UPDATE is a WRITE, and it widened the set ──
        # The create-side gate is at the funnels every CREATE meets, but
        # ``pointKind`` is one of the properties ``graph_kind_namespaces``
        # MINES, so an update that persists an unvalidated value installs the
        # pack just as a create did — measured at HEAD: a graph recorded as
        # ``['dev']`` became ``['dev','marketing']`` through this door alone.
        # Gating creates and not updates left the closure property one verb
        # away from true, which is the defect #5475 is about.
        self._refuse_unapproved_kinds_in_props(props)
        # #329: id mutation + server-managed fields rejected
        # #2782: an UPDATE is graded on the money state it LEAVES, not on the
        # bag — `SET n += $props` DELETES a key whose value is null, so
        # `update_point(id, currency=None)` would otherwise strip the currency
        # from a well-formed value and make `total_minor` refuse the whole set
        # while passing a "carries no money" gate.
        props = _sanitize_props(props, reject_id=True,
                                reject_belief_props=True,
                                reject_claim_class=True,
                                money_views=_money_views_for_update(
                                    proj.g, props, node_id=id,
                                    label=":Point"),
                                value_shapes=_stored_value_shapes(
                                    proj.g, props, node_id=id,
                                    label=":Point"),
                                # #13035: the stored half of the goal-ending
                                # pair — read ONLY if this write names one
                                # endpoint and not the other.
                                goal_ending=lambda: _stored_goal_ending_view(
                                    proj, id, label="Point"))
        # ⛔ #11702 (review cycle 12, P2): THIS is the THIRD writer that moves a
        # belief pair, and it was the one that left the `n.confidence` mirror
        # behind. `reject_belief_props` refuses `credibility` and the baseline
        # quartet only — `posterior_alpha` / `posterior_beta` / `confidence`
        # ride through, so `update_point(id, posterior_alpha=10.0,
        # posterior_beta=10.0)` stored a new pair beside the OLD flush value.
        #
        # Pre-#11702 that disagreement was VISIBLE (the mirror-led surfaces said
        # 0.9, the `ep` block said 0.5); post-#11702 the same ~12 surfaces all
        # read the mirror FIRST, so a stale mirror is indistinguishable from a
        # correct one and the served belief contradicts the persisted pair that
        # is supposedly behind it. The other two members of the family were
        # closed at their writes (`sdk.set_point_baseline` refreshes the mirror,
        # the EP evidence pre-write clears it with the posterior); this is the
        # same treatment, so no path moves α/β and leaves the mirror.
        #
        # The clear — not a recomputed mean — because the mirror is a FLUSH of
        # the posterior, and inventing one here would claim an EP write that
        # never ran. With the mirror gone, every mirror-first read falls through
        # to the pair mean (the #2206 precedence), which is the honest value for
        # the pair the caller just wrote — including a HALF pair, which then
        # reads absent instead of inheriting the stale mirror's number.
        #
        # A caller-supplied `confidence` still WINS (it is caller-owned, like
        # `tags`), so mirror≠pair stays reachable through the public writes —
        # pinned by `test_the_mirror_wins_over_a_contradicting_pair`.
        if (("posterior_alpha" in props or "posterior_beta" in props)
                and "confidence" not in props):
            # `SET n += $props` with a None REMOVES the property, and `props` is
            # also `emit_props`' base below — so the clear rides the same
            # `PointRevised` record (`BELIEF_PROPS` is in its fold whitelist,
            # with `None` the journaled clear) and a rebuild reproduces it.
            props["confidence"] = None
        # R2 (#1541) D3: search_keys is stored flat (see _flatten_search_keys_prop).
        _flatten_search_keys_prop(props)
        # #11239: same rule at the UPDATE door. Idempotent — a value already in
        # the canonical scalar form is left untouched, which is what lets
        # `create_point`'s dedup fold hand these props straight through
        # (`self.update_point(pid, **props)`) without re-encoding or refusing.
        _encode_json_record_props(props)
        # E4 (#5007): same rule as `create_point` / the `Point` model — an
        # update may not leave a half span, an inverted pair or an
        # out-of-range offset on the node. The pair is all-or-nothing, so an
        # update that moves one edge of the span passes BOTH edges.
        if "span_start" in props or "span_end" in props:
            from tortoise.commit_schema import validate_span
            validate_span(props.get("span_start"), props.get("span_end"))
        # #5359: refuse an inverted validity window BEFORE the write. An update
        # may move only ONE edge, so the caller's bound is merged with the
        # STORED opposite bound first (the declaration's own scope note,
        # `commit_schema.validate_validity_window`) — without that merge,
        # `update_point(id, validTo=EARLY)` on a future-`validFrom` point would
        # be checked as (None, EARLY) and pass, persisting the inversion. The
        # predicate itself is the ONE HOME, never a second copy.
        # #9202: this call also DECIDES the caller's `validTo` (shape or refuse)
        # and hands back the in-band receipt the reply echoes.
        _end_receipt = _refuse_inverted_point_window(proj.g, props, point_id=id)
        # #2350 (E-prec): precision is validated against the STORED pair after
        # the same merge, so a one-sided update cannot leave a marker beside a
        # bound it contradicts.
        _refuse_inconsistent_validity_precision(proj.g, props, point_id=id)
        # #5240: `content`'s None boundary is decided ONCE, here, before any
        # consumer reads the key — because the consumers disagreed. The
        # derived-hash line below tested KEY PRESENCE (``"content" in props``)
        # and so reached ``None.encode("utf-8")``, raising a bare
        # ``AttributeError`` from a private helper two frames from the caller's
        # call — naming neither ``content`` nor this verb — while the #4208
        # embedding guard further down already tested the VALUE
        # (``_new_content is not None``), the boundary ``_revise_point``
        # implements (``if new_content is not None:`` folding
        # ``n.content = coalesce($c, n.content)``). A guard on the hash ALONE
        # would not close it: ``SET n += $props`` carrying a None REMOVES the
        # property (measured), so the live node would LOSE its content while
        # the replay's ``coalesce`` restored it — live != rebuild, strictly
        # worse than the crash. Normalising to "absent" makes the hash, the
        # derived vector, the write and the journal agree with the replay: no
        # content edit at all, exactly like ``is_episodic=None`` below. A
        # GENERIC prop is the opposite by design — ``SET n += $props`` with a
        # None clears it, and the replay deliberately matches
        # (``_is_persistable_prop_value`` in ``tortoise/projection``: "a live
        # ``update_point(x=None)`` clears the property, and replay must match
        # by clearing it too"). ``content`` is not generic because its clear
        # has NO replay: ``coalesce($c, n.content)`` cannot express one, so a
        # live clear would be unrecordable and a rebuild would undo it. A
        # non-str is refused here for the same reason ``None`` was
        # diagnosable only from a helper — it would die at ``.encode`` a frame
        # away too.
        if "content" in props:
            _c = props["content"]
            if _c is None:
                props.pop("content")  # "nothing to set", never a clear
            elif not isinstance(_c, str):
                raise TypeError(
                    "update_point: content must be a str (or None for no "
                    f"content edit), got {type(_c).__name__}"
                )
        # #1904 (bug-hunt 2026-08-28 P1-3): a content edit MUST recompute
        # content_hash in the same round trip — every dedup surface matches
        # on the stored hash (create_point dedup, ingest, _content_exists),
        # so a stale hash after update_point(content=...) silently breaks
        # dedup and diverges the live graph from JSONL replay (which derives
        # the hash from PointRevised.new_content).
        #
        # #8933 Stage 2 (review round 8, F1): tracks whether the ``search_keys``
        # this door writes was DERIVED, so it can be kept out of the record the
        # way ``content_hash`` and the derived vector already are. Declared here
        # (not inside the ``if`` below) because the emit is far below and reads
        # it unconditionally.
        _derived_search_keys = False
        if "content" in props:
            props["content_hash"] = _content_hash(props["content"])
            # #8933 Stage 2 / #10203 (measured P0): a content edit is the THIRD
            # derived value this door recomputes in the same round trip, for the
            # same reason as the hash (#1904) and the vector (#4208) — the live
            # node must equal what a replay of `PointRevised` produces, and the
            # ruling writes the alias AT CAPTURE, so a revision has to re-derive
            # it. Leaving it alone meant an edit that introduced an identifier
            # never indexed it, an edit that removed one kept a stale alias that
            # still matched the old code, and `check_consistency` reported an
            # unclearable `divergence="content"` on `search_keys` for a faithful
            # graph (its reference view derived the NEW content's alias while the
            # node could only hold the creation's).
            #
            # The node's previous value says which tokens are STALE and is not in
            # the event, so it is read here; `revise_identifier_aliases` then
            # keeps every non-alias key and swaps the derived half. A
            # caller-supplied `search_keys` owns the list and wins as the base.
            #
            # ⛔ DERIVED, so NOT RECORDED (the #1904/#4208 rule, applied to the
            # third derived field on this door). `_revise_point` re-derives the
            # value from `new_content` and the node's own previous keys, so a
            # journalled copy is dead weight at best and a producer claim the
            # replay does not honour at worst (#5004/#5046). Left in the record,
            # `search_keys` is a COLUMN (`public.point`, record_projection
            # patches columns from the flat `NodePatch`), so a DERIVED value
            # would become authoritative in the record lane — which that
            # module's own doctrine forbids, and `chk_*_props_no_derived`
            # cannot catch it for a name that has a column.
            #
            # A CALLER-supplied key is different: it is owned by the call, the
            # replay cannot recompute it, and it rode `emit_props` before
            # #8933 too — so that case keeps its record, with the #5046
            # caller-owned parity gap unchanged (not widened here).
            # ⛔ AN EXPLICIT `search_keys=None` IS A CLEAR, NOT AN ABSENT KEY
            # (#10203 review round 12, P3). ``props.get(...)`` cannot tell the
            # two apart, so a call that edits content AND clears the vocabulary
            # — ``update_point(id, content=…, search_keys=None)``, the same
            # documented ``None``-clears shape ``tags``/``embedding`` use in
            # this very function — used to be REPLACED by the recomputed
            # derived value, which keeps the node's previous non-alias keys:
            # the caller was told the write succeeded while the vocabulary it
            # asked to drop survived on the node and in the FTS index (the
            # stale-index-value class, reached through argument-default
            # ambiguity rather than a bad clause).
            #
            # So the gate is on the KEY, not the value. The ``None`` then rides
            # ``SET n += $props`` exactly as it did before #8933 (a null REMOVES
            # the property) and rides the record too: a clear is CALLER intent,
            # not a derived value, so it keeps the same caller-owned record
            # parity a caller-supplied list has (and the same #5046 gap — the
            # replay fold has no clear clause — neither widened nor narrowed
            # here). When no content is edited the block below is never even
            # reached, which is why the defect only ever bit the combined call.
            if _identifier_tokens_enabled():
                if "search_keys" in props and props["search_keys"] is None:
                    _derived_search_keys = False  # caller-owned CLEAR
                else:
                    _prev = proj.g.query(
                        "MATCH (n:Point {id:$id}) RETURN n.content, n.search_keys",
                        params={"id": id},
                    ).result_set
                    _old_content, _old_keys = _prev[0] if _prev else (None, None)
                    _caller_keys = props.get("search_keys")
                    props["search_keys"] = _revise_identifier_aliases(
                        new_content=props["content"],
                        old_content=_old_content,
                        old_search_keys=_old_keys,
                        caller_search_keys=_caller_keys,
                    )
                    _derived_search_keys = _caller_keys is None
        if is_episodic is not None:
            props["is_episodic"] = is_episodic  # server-managed (explicit param only)
        # #5004 round-4: a caller-supplied vector is stored VERBATIM here
        # (`n += $props`, never `vecf32`) — the same recorded PR #3018 decision
        # `create_point` implements. Mark it on the NODE (so a LATER re-emit —
        # `promote_point`, `merge_points`, annotate — carries it and the replay
        # does not narrow the vector through `vecf32`: `0.3` ->
        # `0.30000001192092896`). Set BEFORE the writes below, because the
        # marker has to ride `get_point`; `_sanitize_props` has already run, so
        # this server-managed key cannot have come from the caller, and the
        # `is not None` gate leaves a "clear the vector" call unmarked.
        if props.get("embedding") is not None:
            # #5004 round-6: same store-width guard as `create_point` — a
            # wrong-width vector is written live but REFUSED on replay, so the
            # rebuilt graph would lose a vector live holds.
            _dim = proj.required_embedding_dim
            if _dim is not None:
                try:
                    _w = len(props["embedding"])
                except TypeError:
                    _w = None
                if _w != _dim:
                    _logger.warning(
                        "update_point: a caller-supplied embedding of width "
                        "%s does not match this store's %d — clearing it "
                        "rather than storing one the index cannot hold or the "
                        "replay would refuse (#5004)",
                        _w if _w is not None else "?", _dim)
                    props["embedding"] = None
        # #5004 round-8: the marker follows the vector — SET when the caller
        # supplies one, CLEARED when the caller clears it. `update_point(
        # embedding=None)` used to remove the vector but leave the marker, so
        # the node claimed "caller-owned, stored verbatim" with no vector, and
        # a rebuild then either dropped the marker (diverging from live) or put
        # it back beside a RECOMPUTED vector (the false-verbatim class round 6
        # closed). Gated on the KEY, so an unrelated update does not touch it.
        if props.get("embedding") is not None:
            props["embedding_verbatim"] = True
        elif "embedding" in props:
            props["embedding_verbatim"] = None
        # #8488 scope 1 (review round 3, P3): BOTH of those arms REPLACE or CLEAR
        # the vector, so neither may leave the writer-minted ``embeddingText``
        # behind — it describes the vector this write just replaced, which is the
        # #4194 lie under a different door. Round 2 closed the branch that
        # RE-ENCODES from `content` (`_derived_embedding`, below); a caller vector
        # (`embedding=[…]`) and a caller clear (`embedding=None`) reach the node by
        # the same bare `SET n += $props` and never touch the text. `None` REMOVES
        # the property. Gated on the KEY, so an unrelated update leaves a
        # writer-minted value alone — which is what makes it survive one.
        if "embedding" in props:
            props["embeddingText"] = None

        # #49 Phase 2: context is REMOVED — raise TypeError if passed
        if "context" in props:
            raise TypeError(
                "update_point() got unexpected keyword argument 'context'. "
                "Context has been removed. See #49."
            )

        # Validate status if present — through the ONE lifecycle door, so a
        # re-spelling is normalised (and stored normalised) and a synonym lands
        # as `deprecated` (#11824 S0). The promote-only arm below then decides
        # whether this door may write the resulting word at all.
        if 'status' in props:
            props['status'] = _read_caller_status(props['status'], kind=":Point")

        # #2351 (review P0): ``claimClass`` is a CREATE-time marker, NOT a
        # revision prop, and this door refuses it — through the SAME #7951
        # chokepoint as every other generic write bag, not with a second
        # per-door copy of the rule (``update_entity`` is the other documented
        # ``:Point`` update door and takes its refusal from the same place, so
        # patching one and forgetting the next cannot happen again). The
        # rejection itself, and why replay makes the marker create-only, are
        # documented on ``_sanitize_props(reject_claim_class=True)``; widening
        # replay beyond ``PointAdded`` is tracked as #9732.

        # #432 plan-review P1: update_point is non-status except the draft→live
        # promote (matches the create_operator promote). Any other status value
        # is rejected BEFORE the query — lifecycle transitions go through
        # retract_point()/supersede_point() (which emit events). This keeps
        # every claim transition observable via an emit hook.
        if 'status' in props:  # noqa: SIM102
            if props['status'] != 'live':
                raise ValueError(
                    "update_point only promotes draft→live — use "
                    "retract_point()/supersede_point() for lifecycle transitions"
                )

        # #10122: the advisory sits here, after the refusals that run BEFORE
        # the write (kinds, sanitisation, span, validity window, precision, the
        # draft→live status ValueError above). Named residuals it does NOT
        # cover, all measured: `_assert_lifecycle_guard` (further down) and the
        # promote arm's guarded-SET miss (`_raise_update_point_status_error`)
        # can still raise after this line, so a refused transition can advise.
        # Moving it past those is its own change — they are inside the write
        # arm, not before it (#10122 review rounds 3-4).
        # #10122: the advisory, AFTER the refusal gates above (a refused update
        # writes nothing, so advising about it is crying wolf — the discipline
        # `create_point` and `update_entity` already follow) and reading the
        # STORED text, because the verdict must be about the index, not about
        # the payload a name-only update does not carry (round-1 + round-2 P2).
        # Fires only when the update carries a string `name`, so a plain update
        # pays nothing.
        if isinstance(props.get("name"), str):
            # Aggregations ONLY, so this is one row on a hit AND on a miss —
            # a mixed ``count(n), n.content`` projection groups implicitly and
            # returned an EMPTY result set on the miss (the round-2 pin caught
            # it as an IndexError).
            _name_row = proj.g.query(
                "OPTIONAL MATCH (n:Point {id:$id}) "
                "RETURN count(n), "
                "coalesce(head(collect(n.content)), ''), "
                "coalesce(head(collect(n.search_keys)), ''), "
                "coalesce(head(collect(n.quote)), '')",
                params={"id": id},
            ).result_set[0]
            if _name_row[0]:
                _warn_point_name_not_lexically_indexed(
                    props, door="update_point",
                    stored={"content": _name_row[1],
                            "search_keys": _name_row[2],
                            # #11682: quote is an INDEXED field now, so the
                            # stored half of the verdict must read it —
                            # otherwise a name whose tokens appear only in the
                            # point's verbatim quote would be called
                            # unfindable while the index can answer it.
                            "quote": _name_row[3]})

        # #1088: with LIVE as the create default, an ordinary flow reaches
        # here with status='live' on a point that is ALREADY live. That is an
        # idempotent success, not the illegal transition the draft-born guard
        # was written to refuse — promote_point already answers 'already_live'
        # as a no-op (DE2E-N9), and this mirrors it. The probe runs only when
        # a status was supplied, and a genuinely illegal transition (a
        # terminal status resurrected) still falls through to the guarded
        # WHERE below and raises.
        if 'status' in props:
            _cur_status = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.status", params={"id": id},
            ).result_set
            if _cur_status and _cur_status[0][0] == 'live':
                if len(props) == 1:
                    # status-only: a true no-op — no write, no event.
                    return _with_end_receipt(self.get_point(id), _end_receipt)
                # Other props still belong to the caller; drop only the
                # already-satisfied status and let the non-status write path
                # below apply them (never silently discard caller data).
                props.pop('status')

        # ── #7819: a TERMINAL claim's temporal record is refused here ───────────
        # Measured on the pre-change tree: `retract_point(p)` followed by
        # `update_point(p, validTo="2026-06-10")` was accepted and stored the
        # window, as it was on a point carrying the legacy `outdated=true` flag.
        # `supersede_point` / `invalidate_point` / `retract_point` consult the
        # shared guard for their lifecycle writes; this verb did not, and it
        # writes the same window the temporal read surfaces answer from.
        #
        # THE TERMINAL GATE IS SCOPED to `_TEMPORAL_RESTAMP_PROPS` (see that
        # constant for why a blanket refusal is wrong here: #3305's
        # post-terminalization revisions are a recorded capability). The GATE
        # ITSELF now runs on every call that reaches the write path — the #1088
        # status-only-on-live no-op returns above it, taking no work — and what
        # is scoped is its terminal RAISE, passed as `refuse_terminal`, so:
        #   * a call carrying a temporal prop is refused on a terminal node
        #     exactly as before — including a STATUS-carrying one, which keeps
        #     #7819 whole: `update_point(terminal, status='live', validTo=…)`
        #     still raises `already terminal`, it does NOT reach the #432
        #     promote message (that message is for the status-only shape);
        #   * a call with NO temporal prop asks the terminality question but is
        #     not refused on it, so `update_point(terminal, status='live')`
        #     still raises the #432 "Illegal status transition", and an
        #     already-LIVE node is still the #1088 idempotent no-op;
        #   * `missing_ok=True` makes the ABSENT verdict a `None` rather than a
        #     raise, and the caller turns that into the documented `{}` reply
        #     with NO side effects (#10119). A status-carrying call is the one
        #     absent shape that keeps its pre-existing behaviour — it falls
        #     through to the guarded promote write and raises `No point <id>`
        #     (no side effects either way; the asymmetry is the promote
        #     contract's, #432);
        #   * `allow_operator=True` keeps operator points writable: this verb is
        #     what `annotate_operator` delegates to, and a caller may pass a
        #     window for an operator. A TERMINAL operator is still refused, pinned
        #     by `test_update_point_refuses_a_terminal_operator_record` (without
        #     the flag the operator verdict would mask the terminal one).
        # It runs after the inverted-window and validity-precision refusals (so
        # those are reported as themselves, not as terminal), and before the
        # journal append and every write, so a refused call journals nothing and
        # writes nothing.
        #
        # CLOSED by #12586 (was: "Residual, unchanged by this fix"). The
        # promote arm's guard now ANDs the SHARED terminal predicate's flag half
        # into the statement (`_PROMOTABLE_CLAUSE`), so a point that is dead by
        # the legacy `outdated=true` flag with a NULL status is REFUSED instead
        # of being given `status='live'` beside the flag. The instrument is
        # `live._terminal_excluded` in the WHERE and `live.is_terminal_status`
        # in the diagnostic — one predicate, the same one `update_entity`'s
        # guard already applied, so the two Point doors now agree on this row.
        # ⚠️ The "CLI backfill" is NOT this arm, and this change does not touch
        # it: `_cmd_backfill` and the repair sweep backfill with RAW Cypher
        # (`MATCH (p:Point) WHERE p.status IS NULL SET p.status = 'live'`, no
        # flag conjunct), so ONTOLOGY §5's "CLI backfill promotes NULL-status
        # legacy points to `live`" holds because its statement is untouched —
        # not because this arm behaves like it. What this arm must keep is the
        # narrow version of the same property: a NULL-status point WITHOUT the
        # flag still promotes (the flag conjunct is the only condition added),
        # pinned by `test_the_cli_backfill_shape_still_promotes`. The bulk
        # migration doors are a SEPARATE instance of this same root (#12586's
        # thread carries the measurement: they lift flag-dead NULL-status rows
        # in bulk, and changing them would rewrite a recorded §5 statement).
        # History: the residual was recorded in `docs/ONTOLOGY-CHANGELOG.md`
        # v3.35 when it came out of the model file.
        # ── #10119: the ABSENT arm takes no receipt, and now no WORK ──────────
        # Measured on the pre-fix tree: `update_point("no-such-point-9202",
        # confidence=0.5)` returned `{}` — the documented absent-id contract —
        # and yet appended a `PointRevised` journal row, advanced
        # `:EpMeta.ep_version` (0 -> 1 from cold), and put the ABSENT id into
        # `_dirty_roots`, where it SURVIVED `_sweep_dirty_roots(all real ids)`
        # and held `_auto_dream_mode()` at `'local'` for the life of the
        # instance — a typo'd id pinning the instance out of its dream cycle
        # (#2422 / #7505 / #9269). The reply was honest; the side effects were
        # not.
        #
        # THE GATE IS THE EXISTING INSTRUMENT, NOT A SECOND ONE. #8854 round 9
        # closed this exact shape on the PROMOTE no-op arm by returning before
        # `_mark_dirty`; the ABSENT arm had no such exit even though this door
        # already performed existence reads it could gate on. The predicate
        # lives in `_assert_lifecycle_guard` (the #2498/#7819 instrument: one
        # statement, one classifier, over the whole fan-out — #7822), so this
        # door now consults it on every call that reaches the write path (the
        # #1088 status-only-on-live no-op returns above it) instead of only when
        # a temporal prop is present. It must run HERE — after the refusals above (so a
        # refused call is reported as itself, not as absent) and BEFORE the
        # journal append and every write (the append is the commit point,
        # #7951 §17.1; an append for a node that is not there is a record of a
        # revision that cannot exist).
        #
        # `refuse_terminal` is FALSE unless the call carries a temporal prop:
        # #7819 scoped THIS door's terminal refusal deliberately — a generic
        # revision of a dead claim remains writable through the ADDRESSED door
        # (the caller names the point), which is the #3305 capability. The
        # UNADDRESSED create fold is the opposite case and refuses every write
        # onto a terminal claim (#10368), so the two doors no longer share a
        # scope; this one is unchanged.
        #
        # ⛔ THE STATUS-CARRYING CALL IS *NOT* SKIPPED. A first cut of this fix
        # read `if 'status' not in props:` around the whole guard and thereby
        # DELETED an existing refusal (#7819 review): measured on a flag-dead
        # point (`status=null, outdated=true`), `update_point(p,
        # status='live', validTo=…)` LANDED — status flipped to `live` and the
        # window stored — where the pre-fix tree raised `already terminal
        # (outdated)`. Only the ABSENT early return is status-gated (the
        # promote arm owns the absent shape, raising `No point <id>`).
        # ⚠️ THE TRADE THIS MAKES, STATED (#7405). During a rebuild the engine
        # aborts writes while the target is legitimately absent from the LIVE
        # graph but present in the journal. Pre-fix the non-promote arm appended
        # `PointRevised` first (the §17.1 commit point), so such a revision was
        # durable and a replay could pick it up, and the write rode the 7-14 s
        # `_graph_write_with_retry` window. Post-fix, a target absent at THIS
        # read gets no retry and no record: the typo and the mid-rebuild
        # absence are the SAME read, and the typo harm (a stranded dirty root,
        # #10119) is the one that persists, so this is the intended trade — but
        # the #7405 window no longer covers that shape. Do not "restore"
        # journal-first for absent ids to get the window back.
        _temporal_restamp = bool(_TEMPORAL_RESTAMP_PROPS & set(props))
        _verdict = self._assert_lifecycle_guard(
            id, method="update", missing_ok=True, allow_operator=True,
            refuse_terminal=_temporal_restamp)
        if _verdict is None and 'status' not in props:
            # ABSENT. Zero side effects, and the same `{}` the documented
            # contract already returns — no journal row, no epoch advance,
            # no dirty root, so a typo cannot strand the instance.
            # #10515: the trade above keeps the TYPO quiet, but it also made a
            # LEGITIMATE revision refused during a rebuild leave no trace at all
            # (no row, no epoch delta, no dirty root, no line, no counter). The
            # reply stays `{}`; the loss is now REPORTED — a warning plus
            # `metrics()["silent_revision_losses"]` — when the graph key cannot be
            # confirmed or the journal still HOLDS the id, and stays silent for
            # an id absent from both. It reports the loss; it does not prevent it.
            # "Zero side effects" still holds for the GRAPH and the journal (no
            # row, no epoch, no dirty root); the report READS the journal file,
            # and it is deliberately a chunked read of `os.stat` + a byte scan,
            # never `EventLog.position()` — that one takes the append flock and
            # can cost a typo 5 s (review B2).
            try:
                self._report_absent_revision(id)
            except Exception:  # noqa: BLE001, RUF100 — the report is DIAGNOSTIC
                # A diagnostic must never turn the documented `{}` absent reply
                # into a raise: the reply contract is the caller's, the report is
                # ours. Recorded at debug so the swallow is findable (review
                # round 4, non-blocking 1).
                _logger.debug(
                    "the #10515 absent-revision report failed for %r", id,
                    exc_info=True)
            return {}
        # ⛔ ORDERING (#10495 review P3). This block runs AFTER the ABSENT
        # early return above, not before it. `encode_for_row` is a real
        # inference (a model round-trip, or a timeout on a wedged one), so
        # running it above the guard made a write that CANNOT land pay for
        # one and then throw the vector away — measured on the pre-fix tree
        # with a spy on `tortoise.embeddings.encode_for_row`:
        # `update_point("typo-4", content="…")` -> `embedder called for an
        # ABSENT id: 1`. The status-carrying absent shape still reaches this
        # block (it does not take the early return; the promote arm owns it),
        # so nothing is skipped that used to run.
        # #4208: a content edit must recompute the DERIVED vector in the SAME
        # write, exactly as it already recomputes the derived `content_hash`
        # (#1904). Before this, `update_point(content=B)` left the node holding
        # content B with `vec(A)` — the dense leg then ranked it by text no
        # longer on the node, and live != rebuild, because the replay's
        # `_revise_point` DOES re-encode. Same rule as the hash, and the same
        # rule #19 set for the revise fold: recompute from the new content, or
        # WIPE it when the embedder is unavailable (`None`) — never preserve a
        # vector the new content did not produce (a vector for different text
        # is worse than none). #4194/#4280: route through `encode_for_store`
        # (the store-width-declaring seam) so the stored width matches the
        # Point HNSW index this store created; the seam is still
        # `compute_embedding` (module global), so installed doubles intercept.
        #
        # A CALLER-supplied vector is deliberately NOT recomputed: the caller
        # owns that write, the verbatim marker above already describes it, and
        # the record/replay treatment of a caller vector on a revise is the
        # separate declared gap #5046. Gated on the KEY, so this fires only
        # when the caller supplied none.
        _derived_embedding = False
        _new_content = props.get("content")
        if ("content" in props and "embedding" not in props
                and _new_content is not None):
            # `_new_content is not None` mirrors `_revise_point`'s own boundary
            # — a `None` new_content is "no content edit" there, so it must not
            # derive (or wipe) a vector either.
            try:
                from .embeddings import encode_for_row
                props["embedding"] = (
                    encode_for_row(_new_content, proj.required_embedding_dim)
                    if _new_content else None)
            except Exception:
                props["embedding"] = None
            _derived_embedding = True
            # #5238 review P3: the vector just derived is NOT caller-owned, so
            # a node that previously held a CALLER-supplied vector (and still
            # carries `embedding_verbatim=true`) must have that marker cleared —
            # exactly as the `embedding=None` path above does. Left set, the
            # node advertises a SERVER-derived vector as caller-owned: the
            # marker selects raw-vs-`vecf32` storage on every later re-emit and
            # flips the consistency comparison to an exact-float compare, while
            # a rebuild drops it (`_revise_point` re-encodes and never reads
            # the marker) — live != rebuild, the round-6 false-verbatim class.
            props["embedding_verbatim"] = None
            # #8488 scope 1 (review round 2, P1): the enriched text described the
            # OLD vector. This branch re-encodes from `content`, so the node must
            # not keep an `embeddingText` that no vector was computed from — the
            # exact #4194 lie the capture path's `prior_et` arm exists to prevent,
            # reached through the other write door. `None` REMOVES the property;
            # the replay's `_revise_point` clears it under the same condition.
            props["embeddingText"] = None

        # ── #11739: composition is CREATE-only, and this door says so ──────
        # `create_point` composes a Point's embedding text from its verbatim
        # `quote` (the names half is not wired at that door — a created point
        # has no resolved `about*` neighbours yet; see `embed_compose`'s wiring
        # note). THIS door is a second `:Point` vector mint, and it does NOT
        # recompose: recomposition is the seam that will also own neighbour
        # resolution, and duplicating it here is the "many seams" #11739 rules
        # out.
        #
        # ⛔ A `quote` / `about*` revise therefore leaves the existing composed
        # text and its vector in place — they still describe the text as it was
        # COMPOSED, which is a stale-composition window on a text the node no
        # longer carries. It is a STATED residual, not an oversight: an earlier
        # revision of this change cleared BOTH here, and review round 2 measured
        # that the clear diverged live from rebuild (`_revise_point` writes the
        # vector only under `new_content is not None`, and `emit_props` strips a
        # derived `embedding`), i.e. it traded a stale vector for
        # `derived = replay(journal)` being FALSE — a strictly worse defect. The
        # close for it is the recompose path (delete-time redaction sweep +
        # recompose on revise), which is #11739's remaining scope and must land
        # BEFORE the flag is armed.
        #
        # ⛔ Deliberately NOT "clear on key presence" either: `create_point`'s
        # dedup fold forwards the caller's props to this door
        # (`create_point(..., dedup=True)`), so a presence-triggered clear would
        # strip the composed text and the dense-leg vector from every UNCHANGED
        # point on an idempotent re-ingest (review round 2, measured).

        # Check if node carries :Object label (entity node with version tracking)
        has_object = proj.g.query(
            "MATCH (n:Point:Object {id:$id}) RETURN count(n) > 0",
            params={"id": id},
        ).result_set[0][0]

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017

        # ── #4520 residual: the DERIVED vector goes through the shared seam ──
        # `update_point(content=…)` re-encodes the embedding (#4208) and the
        # merge below would store it as a plain Cypher `List` — no `vecf32`,
        # no clear. `typeof` then flips `Vectorf32` → `List` on ONE content
        # edit: a bare `List` node is not in the HNSW index at all, so it is
        # silently MISSING from the index-served Point dense leg (#8429) — the
        # write seam exists so a server-owned vector is always the typed,
        # indexable shape. (#8316 has since normalised a right-width `List` at
        # the READ boundary, and #8430 drops a stored value the read cannot
        # consume — a wrong width or a non-numeric value — instead of aborting
        # the whole MATCH; a wrong-width TYPED row is its declared residual.)
        # That is the #4521 class on the LIVE path
        # (#4521 / PR #7740 fixed only the replay's `_revise_point`). The
        # derived vector is SERVER-owned, so it is cast and CLEARED first,
        # exactly like `_revise_point`: `vecf32()` alone is not enough,
        # because a small-Δ `vecf32` overwrite is silently DISCARDED on the
        # embedded engine (#4520's own, previously-fixed defect).
        # `preserve_on_null=False` keeps this branch's wipe-on-None semantics
        # (the embedder was unavailable, or the content became empty —
        # #19/#4208): a vector for different text is worse than none.
        #
        # ⛔ The CALLER-supplied vector is deliberately NOT routed here. It is
        # stored VERBATIM by a recorded decision (PR #3018's TYPE CHANGE
        # GUARD — a float64 list must not be silently narrowed through
        # `vecf32`), and the `embedding_verbatim` marker above describes that
        # form. Routing it through `set_embedding_clause` would narrow the
        # caller's value, reversing that decision rather than fixing a defect;
        # its remaining gap belongs to the #5046/#5048 reopen, not this change.
        _embed_clear = ""
        _embed_set = ""
        _props_param = props
        _embed_params: dict = {}
        if _derived_embedding:
            _props_param = {
                k: v for k, v in props.items() if k != EMBEDDING_PROPERTY}
            _embed_clear = clear_then_set_prefix("n")
            _embed_set = ", " + set_embedding_clause(
                "n", preserve_on_null=False)
            _embed_params[EMBEDDING_PROPERTY] = props.get(EMBEDDING_PROPERTY)

        # ── #7951 §17.1 — THE OUTBOX ORDERING ON `update_point` ──────────────
        # THE APPEND IS THE COMMIT POINT (§17.1 mechanism (1) in its OUTBOX
        # form; the same ordering `create_point` took in increment 1 —
        # `docs/plans/2026-10-14-7951-atomic-write-outbox.md`). The record is
        # built from the WRITE INTENT (`props`, the very dict the `SET` below
        # writes — never a post-write read-back) and appended BEFORE the cache
        # write, so the derived store cannot lead the truth.
        #
        # WHY THIS DOOR. Until now this door wrote the node and only then
        # emitted `PointRevised` — apply-first. `_emit_event` is deliberately
        # best-effort (#4240: a journal failure must never crash a write that
        # DID persist), so a failed append left the CACHE AHEAD OF THE JOURNAL
        # with no repair path: `rebuild_all` replays the OLD value and the
        # revision is gone. That is window (a) of the ordering contract at the
        # top of this module — the one a replay cannot undo — on the SDK's most
        # common mutation door.
        #
        # ⛔ THE PROMOTE ARM (`status='live'`) IS EXEMPT AND STAYS APPLY-FIRST.
        # Its `SET` is GUARDED by a `WHERE` that can legitimately match nothing
        # (an illegal transition, or a node that vanished) and the door then
        # RAISES. Journalling first would record a promotion the graph REFUSED,
        # and a replay would apply it — truth claiming a state the live write
        # never reached, which is strictly worse than the window this fixes.
        # That is the contract's exception 2 shape (the record can only be
        # stated AFTER the write) and it is pinned by
        # `tests/test_7951_update_point_outbox.py`:
        # `test_the_promote_arm_stays_apply_first_a_recorded_deviation` and
        # `test_a_refused_promote_journals_no_revision`.
        #
        # ⛔ DECLARED APPLY-FIRST (#9206), and the non-promote arm is the
        # reason the claim is the weaker one. The writes below go through the
        # raw guarded handle, whose §17.1 door DETECTS an undeclared write
        # (#9124 part 1) and REFUSES it once armed (part 2). This door could not
        # declare `_live_write_scope`: the SAME write region serves the PROMOTE
        # arm, whose record may only be stated AFTER its guarded write, so a
        # journal-first declaration here would authorize it falsely. The door
        # therefore carries `@_declares_apply_first_write_lane("update_point")`
        # — "a write precedes its record, by decision" — which is TRUE of the
        # promote arm and merely WEAKER than the non-promote arm's own
        # behaviour (that arm appends first and aborts with
        # `WriteNotLandedError` when its append fails; the lane says nothing
        # against it, and this comment is where the asymmetry is recorded).
        # Pinned by `tests/test_9124_declared_live_doors.py::
        # test_every_apply_first_declared_door_states_an_order_and_a_record`
        # (the door is genuinely apply-first) and by
        # `tests/test_7951_update_point_outbox.py`'s promote-arm pins.
        # #1904: content_hash is derived from content — keep it out of the
        # event record (mirrors create_point's snapshot strip at emit).
        # #4208: the vector this path just DERIVED from the content is stripped
        # for the same reason — it is not owned by this record. The replay's
        # `_revise_point` re-encodes from `new_content`, so a journalled copy
        # would be dead weight, and #5004's presence-is-ownership rule would
        # read the key as a producer claim the replay does not honour (the
        # #5046 shape). A CALLER-supplied vector stays in the record, as before.
        # #8933 (review round 8, F1): `search_keys` is the THIRD of these — the
        # alias `_revise_point` re-derives on a content edit — and it was riding
        # this record, making "derived, never recorded" (the PR body,
        # `.env.example`, `point_identifier_aliases`, and the create door's own
        # snapshot) false from this door onward. Stripped only when DERIVED: a
        # caller-supplied `search_keys` is caller-owned and stays, exactly like
        # the vector above.
        emit_props = {
            k: v for k, v in props.items()
            if k != "content_hash"
            and not (_derived_embedding and k == "embedding")
            and not (_derived_search_keys and k == "search_keys")
        }
        _promote_arm = 'status' in props

        def _record_promote() -> None:
            """Append the promote's record where the promote LANDS (#7951 2b).

            The promote arm is APPLY-FIRST by decision (`#8830`): its guarded
            ``SET`` can legitimately match nothing, so the record may not
            precede the write. It must not follow the arms that run AFTER it
            either. The tag sync and ``_mark_dirty`` come later and can raise —
            a witnessed tag sync that cannot land aborts the whole call — which
            left a landed promotion with NO record: measured on `main`, a
            ``update_point(status='live', tags=[...])`` whose TAGGED merge could
            not land left the node ``status='live'``, told the caller the call
            had failed, and appended ZERO ``PointRevised`` — a promotion that
            APPLIED with no record of it at all.

            ⚠️ WHAT THIS CLOSES, AND WHAT IT DOES NOT. The record now exists,
            and it carries the promotion (``status`` rides ``emit_props``), so
            the RECORD lane applies it: ``PointRevised`` is a flat
            ``NodePatch`` in ``record_projection.RECORD_MUTATIONS`` and
            ``Point.status`` is a primary column, so replaying the record
            restores ``live``. The GRAPH lane does NOT: ``_revise_point`` has
            no ``status`` clause (nor does ``_apply_one``'s ``PointRevised``
            arm), so ``rebuild_all`` over the same journal still replays such a
            promote as ``draft``. That is a PRE-EXISTING graph live-vs-replay
            parity gap, listed with ``:Object`` ``version``/``updatedAt`` in
            ``docs/plans/2026-10-17-7951-update-point-outbox.md`` §6.5 and
            filed as #9255 — this increment moves *when* the record is
            appended, it adds no fold. §6.5 also carries the TAG half: the
            record carries ``tags``, which the fold DOES apply (property +
            ``_sync_tags``), so a graph rebuild mints the ``TAGGED`` edges this
            window's aborted sync never wrote — a divergence in the opposite
            direction, and the reason #9255 needs a decision, not just a
            clause. Do not restate this docstring as "the promotion survives a
            rebuild" until that fold lands.

            So the commit point is "after the guarded write, before anything
            that can strand it" — the closest this arm takes to its siblings'
            journal-first policy (§17.1, #9124) without the decision §6 residual
            3 of the plan names (a guarded write whose outcome is known before
            the record is stated). The guarded `SET` can legitimately match
            nothing (`#8830`), so a record ahead of it could describe a
            transition the call did not perform. The two halves are pinned for
            BOTH arms (`:Point:Object` and `:Point`):
            ``test_the_promote_arm_stays_apply_first_a_recorded_deviation``
            (the write precedes the record) and
            ``test_a_landed_promote_is_journaled_before_a_later_arm_can_abort``
            (the record survives a tag arm that aborts after the promote
            landed). Recording is reached only with a NON-EMPTY
            ``result_set``: every zero-row promote has already returned (the
            #1088 no-op, which journals nothing) or raised (the diagnostic
            above).

            Best-effort, as at the old tail: ``_emit_event`` never raises, and
            the write it describes has already landed, so a failed append is
            counted in ``_journal_write_failures`` rather than mis-reported as
            ``WriteNotLandedError`` — which would claim the opposite of what
            happened.
            """
            self._emit_event("PointRevised", id=id,
                             new_content=props.get("content"), **emit_props)

        if not _promote_arm:
            # The append's OWN per-call outcome is the commit signal — a
            # per-call value, not a delta of the shared
            # `_journal_write_failures` counter, which a CONCURRENT writer on
            # the same instance can move (#5612).
            _record_committed = self._emit_event(
                "PointRevised", id=id,
                new_content=props.get("content"), **emit_props)
            if _record_committed is False:
                # No record ⇒ NO cache write: the revision is not applied, and
                # the caller is told so rather than handed a silent success.
                from .exceptions import WriteNotLandedError
                raise WriteNotLandedError(
                    id, getattr(proj, "graph_name", None),
                    operation="update_point")

        # #7405: the property writes below ride the bounded write retry —
        # `update_point` is the SDK's most common mutation door
        # (`annotate_operator` delegates here), so a rebuild's per-query abort
        # used to reject the revision outright while reads kept serving: the
        # finding is revised in the caller's head and lost from the map. Every
        # statement here is admissible on `retryable_aborted_write` ALONE — its
        # arms prove the statement did NOT land — and every value is computed
        # once above (``_props_param``, ``now``, ``_embed_params`` are all fixed
        # at the call), so a re-issue converges on exactly the state a single
        # successful issue would produce. That is also why the one relative
        # write inside a statement (``n.version = coalesce(n.version, 0) + 1``)
        # cannot double-bump: it is safe for the did-not-land reason, NOT
        # because it is an absolute SET. The promote arms carry their guard INSIDE
        # the statement, so a re-issue re-evaluates the same `_PROMOTABLE_CLAUSE`
        # (the #432 vocabulary AND #12586's `outdated` conjunct) against the graph — it
        # cannot promote twice, and a legitimately-refused promote still yields
        # an empty result set (the caller's
        # ``_raise_update_point_status_error`` path gains a ``status_only``
        # argument, so a live node is an idempotent success only for a
        # status-only promote and stays loud when other props went unwritten).
        def _promote_witness(result) -> bool:
            """A zero-row promote re-issue, classified by the node's STATUS.

            PRESENCE is not PROMOTABILITY (#8854 review round 5 P1): the replay
            can land the node as ``draft`` between the abort and the re-issue,
            and calling ``_raise_update_point_status_error`` on it would report
            an illegal transition for a node the very next re-issue promotes —
            with a ``ValueError``, which is predicate-FALSE, so it would also
            abandon the 7-14 s window. The read below decides:

            - absent → False: keep retrying, the abort surfaces on exhaustion
              (``ValueError('No point …')`` here would abandon the window).
            - ``None`` / ``draft`` → False: re-issuing matches — UNLESS the
              legacy ``outdated`` flag is set, which is terminal on its own
              (#12586): a re-issue can never match that row either, so it takes
              the documented ``ValueError`` instead of retrying out the window.
            - ``live`` → the #1088 idempotent success for a STATUS-ONLY promote;
              with other props nothing was applied, so the caller gets the SAME
              documented refusal the first-attempt route raises.
            - terminal → the documented ``ValueError``.
            """
            if result.result_set:
                return True
            rows = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
                params={"id": id},
            ).result_set
            if not rows:
                return False
            status, outdated = rows[0]
            if status == 'live':
                # #1088: already-live is an idempotent success for a
                # STATUS-ONLY promote (the arm's post-check completes the
                # no-op).
                if len(_props_param) == 1:
                    return True
                # With other props nothing was applied, so the caller must get
                # the SAME refusal the first-attempt route gives. Returning
                # False here would raise `_WriteNotLanded` instead, and its
                # exhausted path re-raises the GRAPH abort — an error telling
                # the caller to retry a promote that can never match while the
                # node is live, arriving after 7-14 s and withholding the
                # remedy ("re-issue without `status`"). This is the read the
                # `_WriteNotLanded` docstring promises a refused promote takes.
                _raise_update_point_status_error(
                    proj, id, status_only=False, present=True)
            # #12586: `None`/`draft` is re-issuable ONLY while the row is not
            # dead by the legacy flag — the WHERE column the re-issue evaluates
            # now carries the same conjunct, so a flag-dead draft would retry
            # a row that can never match and surface the graph abort instead of
            # the refusal. The predicate is the shared mirror
            # (`live.is_terminal_status`), never a second `outdated` test.
            if (status is None or status == 'draft') and not is_terminal_status(
                    status, bool(outdated)):
                return False
            if _raise_update_point_status_error(
                    proj, id, status_only=len(_props_param) == 1,
                    present=True):
                # A terminal→live flip between THIS read and the diagnostic's
                # own read on a status-only promote: the same idempotent no-op
                # the arm's post-check completes, so do not manufacture a
                # non-landing for it.
                return len(_props_param) == 1
            return False  # pragma: no cover — every other outcome of the
            # diagnostic raises, so only the flip above reaches here.

        # ── #10473 — DID THE CONDITIONAL WRITE LAND? ────────────────────────
        # "Was the target present?" is answered by `has_object` ABOVE, i.e. by
        # a READ that the write does not share a lock with, so its answer can
        # be stale by the time the write runs. The statements below are each a
        # CONDITIONAL write in the one form this store offers: a single
        # `MATCH (n:Point {id:$id}) … SET … RETURN count(n)` re-reads presence
        # and mutates under the same statement, so a zero count is POSITIVE
        # evidence the precondition failed AT WRITE TIME — not a stale answer
        # from a second read. That evidence used to be discarded on the
        # non-abort path: `_graph_write_with_retry`'s ``witness`` is consulted
        # only after a GRAPH-REPLACED abort latches (its own docstring), so a
        # plain zero-row match was swallowed and the door fell through to
        # ``_mark_dirty``. Measured on a target deleted between the guard read
        # and the write (a proxy that removes the node on the first matching
        # write statement): reply ``{}``, ``:EpMeta.ep_version`` 1 -> 2, and
        # the vanished id left in ``_dirty_roots`` where no sweep can ever
        # converge it (``_prune_nonexistent_dirty_roots`` runs only inside a
        # dream, and the stranded root keeps ``_auto_dream_mode`` on 'local') —
        # the #2422/#7505/#9269 pinning harm, one race window away from
        # #10119's absent-at-entry arm.
        #
        # ⛔ A RE-READ WOULD NOT CLOSE IT. Re-checking presence just before the
        # write narrows the window to a narrower one; the delete can land in
        # the gap again. Only the statement that would MUTATE can answer
        # "did I mutate", because its read and its write are one operation.
        #
        # ⛔ THE JOURNAL ROW IS DELIBERATELY NOT SUPPRESSED. The append above is
        # the commit point (§17.1), and the SAME zero-row shape covers two
        # different realities this door cannot tell apart without the journal
        # read #10473 reserves as its own decision: a target DELETED
        # concurrently (whose revision row is inert — a replay's
        # `_revise_point` re-matches nothing) and a target not yet REPLAYED
        # mid-rebuild, where that row is the only copy of the caller's revision
        # and #7405's window exists precisely to preserve it. Dropping the
        # append here would trade a stranded EP root for a LOST revision.
        _target_landed = True
        if has_object:
            if 'status' in props:
                # Promote guard folded INTO the WHERE clause (plan-review P2:
                # single round trip, no widened write window).
                # #2884 A2: carry the REST of `props` too. This arm used to
                # write ONLY status/updatedAt/version while the emit below
                # unconditionally journaled the whole `props` dict — so
                # `update_point(draft, status='live', confidence=0.5)` claimed
                # a write the live graph never made, and the replay fold then
                # applied it: a rebuilt graph held a confidence the pre-wipe
                # graph never had. The sibling no-status arm below already
                # writes `SET n += $props`, so this makes the two arms agree
                # and makes live match the durable record (write == read).
                # #7405: the property writes below ride the bounded write
                # retry — explained once above the arm split.
                # #12586: the promote arm's precondition is the SHARED clause —
                # promotable vocabulary AND the legacy `outdated` flag clear.
                res = self._graph_write_with_retry(
                    lambda: proj.g.query(
                        "MATCH (n:Point:Object {id:$id}) WHERE "
                        + _PROMOTABLE_CLAUSE + " "
                        + _embed_clear
                        + "SET n.status = 'live', n.updatedAt = $now, "
                        "n.version = coalesce(n.version, 0) + 1, n += $props"
                        + _embed_set + " "
                        "RETURN n",
                        params={"id": id, "props": _props_param, "now": now,
                                **_embed_params},
                    ),
                    what="update_point SET (Object promote)",
                    witness=_promote_witness,
                )
                if not res.result_set and _raise_update_point_status_error(
                        proj, id, status_only=len(_props_param) == 1):
                    # #1088: the caller asked for NOTHING BUT the status and
                    # the replay landed the node live, so this is the
                    # idempotent no-op the pre-write probe answers — and it
                    # must answer it the SAME way, with no writes. Falling
                    # through to the tail would run `_mark_dirty`, advancing
                    # the graph-wide EP epoch and persisting `ep_dirty` for a
                    # promote that matched nothing (#8854 review r9 P2).
                    return _with_end_receipt(self.get_point(id), _end_receipt)
                # LANDED: record it HERE, before the tag sync and `_mark_dirty`
                # below can strand it (#7951 2b — see `_record_promote`).
                _record_promote()
            else:
                # #10473: the returned ``RETURN count(n)`` IS the conditional
                # write's answer, so it is consumed instead of dropped — the
                # ``witness`` above only ever sees it after an abort.
                _target_landed = _landed_at_least_one(
                    self._graph_write_with_retry(
                        lambda: proj.g.query(
                            "MATCH (n:Point:Object {id:$id}) " + _embed_clear
                            + "SET n += $props, "
                            "n.version = coalesce(n.version, 0) + 1, "
                            "n.updatedAt = $now" + _embed_set
                            # #8854 review P1: a row to testify with. A bare SET
                            # returns nothing, so a re-issue that matched nothing
                            # (rebuild still replaying) was indistinguishable from
                            # a landed write.
                            + " RETURN count(n)",
                            params={"id": id, "props": _props_param, "now": now,
                                    **_embed_params},
                        ),
                        what="update_point SET (Object)",
                        witness=_landed_at_least_one,
                    ))
        else:
            if 'status' in props:
                # Promote guard folded INTO the WHERE clause (plan-review P2).
                # #2884 A2: see the :Object arm above — the rest of `props`
                # rides the same statement, so the journaled payload is the
                # whole truth of this write. #7405: retried, like its :Object
                # sibling above.
                # #12586: same shared clause as the :Object arm above.
                res = self._graph_write_with_retry(
                    lambda: proj.g.query(
                        "MATCH (n:Point {id:$id}) WHERE "
                        + _PROMOTABLE_CLAUSE + " "
                        + _embed_clear
                        + "SET n.status = 'live', n.updatedAt = $now, "
                        "n += $props" + _embed_set + " "
                        "RETURN n",
                        params={"id": id, "props": _props_param,
                                "now": now, **_embed_params},
                    ),
                    what="update_point SET (promote)",
                    witness=_promote_witness,
                )
                if not res.result_set and _raise_update_point_status_error(
                        proj, id, status_only=len(_props_param) == 1):
                    # #1088: see the :Object arm above — the same idempotent
                    # no-op, answered with no writes rather than through the
                    # tail's `_mark_dirty` EP advance.
                    return _with_end_receipt(self.get_point(id), _end_receipt)
                # LANDED: see the `:Object` arm above — same commit point, same
                # reason (#7951 2b).
                _record_promote()
            else:
                # #7405: one key per statement, each an absolute
                # `SET n += $props` — a re-issue writes the same value to
                # the same key, so it cannot double-apply or clobber a
                # sibling key the loop has not reached yet.
                #
                # ⛔ ONE retry budget for the WHOLE loop, not one per key
                # (#8854 review P2). The budget in
                # `_graph_write_with_retry` is per CALL (`retries=3` → one
                # 7-14 s window), so wrapping each key separately made the
                # caller-visible bound scale with the property count:
                # `annotate_operator` always passes FOUR keys
                # (`annotator_bias`/`_precision`/`_consistency`/`_directness`)
                # and would have blocked 4x the documented window before
                # raising — while buying nothing, because the #7405 window it
                # cannot outlast refuses EVERY key. Re-issuing the whole loop
                # is safe for the same reason each key is: an abort means the
                # statement did not land, and every statement is an absolute
                # `SET` of a value fixed before the first attempt, so the keys
                # that DID land in the aborted attempt are rewritten to the
                # same value rather than re-applied.
                def _write_props() -> bool:
                    if not _props_param:
                        return True
                    applied = True
                    for key, val in _props_param.items():
                        _key_rows = proj.g.query(
                            "MATCH (n:Point {id:$id}) SET n += $props "
                            "RETURN count(n)",
                            params={"id": id, "props": {key: val}},
                        ).result_set
                        # An EMPTY result set is not a number: `count()` is a
                        # single-row aggregate, so "no row" means the statement
                        # did not run (`_landed_at_least_one`'s own premise).
                        # Read it as "this key did not land" — indexing it blind
                        # raises `IndexError`, which is not a retryable abort, so
                        # it would escape as a STRUCTURAL failure and MASK the
                        # abort that authorised the retry (#8854 review P3).
                        matched = _key_rows[0][0] if _key_rows else 0
                        if not matched:
                            applied = False
                    return applied

                # #10473: ``_write_props`` already returns the per-key loop's
                # matched outcome, so the conditional write's answer is
                # CONSUMED here rather than dropped on the non-abort path.
                _target_landed = bool(self._graph_write_with_retry(
                    _write_props, what="update_point SET (per-key)",
                    # EVERY key must match. A sum would let one key matched
                    # by duplicate ids mask a sibling key that matched none
                    # (#8854 review P2: ids are not unique — the duplicate
                    # fan-out is a tested shape).
                    witness=bool))
                if _derived_embedding:
                    # The clear and the set are ONE atomic query, so a crash
                    # between them cannot leave the node vectorless. This arm
                    # is already per-key (not one statement), so the added
                    # embedding write introduces no new non-atomicity.
                    # #7405: absolute clear+set, converged by a re-issue.
                    # #10473: its `RETURN count(n)` is consumed for the same
                    # reason as the per-key loop's — a target that vanished
                    # between the property write and this one must not fall
                    # through to the EP side effects below.
                    _embed_landed = _landed_at_least_one(
                        self._graph_write_with_retry(
                        lambda: proj.g.query(
                            "MATCH (n:Point {id:$id}) " + _embed_clear
                            + "SET " + set_embedding_clause(
                                "n", preserve_on_null=False)
                            + " RETURN count(n)",
                            params={"id": id, **_embed_params},
                        ),
                            what="update_point SET (embedding)",
                            witness=_landed_at_least_one,
                        ))
                    if not _embed_landed:
                        _target_landed = False

        if not _target_landed:
            # ── #10473: THE CONDITIONAL WRITE PROVED THE TARGET GONE ────────
            # Zero rows matched and nothing raised: the node the guard read saw
            # is not there any more (a concurrent delete, or a rebuild whose
            # replay has not reached it). Both of this door's remaining effects
            # would be spent on a target that cannot receive them:
            #   * `_mark_dirty` advances the graph-wide `:EpMeta.ep_version` and
            #     "persists" a dirty flag that matches nothing, then classifies
            #     the id as a delete zombie and KEEPS it in `_dirty_roots` —
            #     where `_sweep_dirty_roots` can never converge it and
            #     `_prune_nonexistent_dirty_roots` only runs inside a dream, so
            #     `_auto_dream_mode` stays 'local' (#2422/#7505/#9269) while the
            #     point it names does not exist;
            #   * the tag sync would issue one `MATCH (p:Point {id}) MERGE
            #     (t:Tag) …` statement per tag against a Point that is not
            #     there. MEASURED: it mints nothing (the MERGE is anchored on
            #     the MATCH, so a missing Point creates no orphan `:Tag`), so
            #     skipping it is spent work avoided, not a leak closed.
            # The reply is whatever `get_point` answers, and — #10079 — with
            # NO end receipt: `{}` when the id no longer resolves at all, and
            # the STORED (un-revised) point when a duplicate-id twin still
            # carries it (`MATCH (n:Point {id})` fans out over every node with
            # that id — a tested shape, #8854 P2). On the fan-out the caller
            # gets a truthy dict whose props were NOT written, which is the same
            # answer the pre-fix door gave; the fix does not invent a refusal
            # for it, because the vanishing twin is not the caller's target.
            #
            # ⛔ NOT A REFUSAL. A legitimately-absent mid-rebuild target must not
            # be turned away (#7405's window exists for it), and the journal row
            # already appended above is what carries its revision to the replay.
            #
            # ⚠️ TWO RESIDUALS, stated rather than implied:
            #  * a call that issues NO property statement has no conditional
            #    write to consult — `_write_props` returns True early for an
            #    empty `_props_param`, so `update_point(<absent id>)` (and the
            #    `content=None` / `is_episodic=None` shapes that normalise to
            #    nothing) still reaches `_mark_dirty` and still strands a root.
            #    That shape has no write to prove anything with; it belongs to
            #    the absent-at-entry arm (#10119 / PR #10470), not to this one.
            #  * `_target_landed` is AND over the per-key loop, so a loop that
            #    landed keys 1..k and then lost the node reports False and the
            #    landed keys do not dirty EP. That can only matter if the node
            #    still exists (the marking would have matched), i.e. a replay
            #    landing mid-loop, and the alternative — marking dirty on a
            #    target that is gone — is the strand this closes.
            #  * the derived-embedding consumer below is the same rule applied to
            #    the door's second statement; the per-key arm's pin covers the
            #    mechanism, and the embedding arm is not separately pinned.
            # Pinned by `tests/test_7405_update_point_writes.py`'s #10473
            # section: `test_a_target_that_vanishes_at_the_write_strands_no_
            # dirty_root` (both arms), `test_the_vanish_shape_keeps_the_JOURNAL_
            # record_that_carries_the_revision`,
            # `test_a_vanished_tagged_target_issues_no_tag_sync` and the
            # negative control `test_a_LANDED_update_still_runs_the_ep_side_
            # effects`.
            return self.get_point(id)

        # Tag sync (#485): keep TAGGED edges consistent with the n.tags
        # property — update_point previously set the property but left edges
        # stale, so query_points_by_tag missed updated points. Falsy tag
        # values (None, "") normalize to [] like create_point, so the
        # "clear tags" idiom removes edges instead of leaving them stale.
        # #7405: `create_point` already rides the retry for this same helper
        # (`"create_point TAGGED edges"`); the update door did not, so the
        # same mutation surface behaved differently depending on which verb
        # the caller used. `_sync_tags` MERGEs each TAGGED edge and DELETEs the
        # dropped ones by an explicit id, so a re-issue after a non-landing
        # abort converges on the caller's tag list.
        if "tags" in props:
            # `sweep_orphans` is passed only from the SECOND attempt on
            # (#8854 review P3). The orphan-`:Tag` GC normally keys off the
            # edges THIS invocation deleted, and a retry re-invokes the whole
            # helper — after an abort that landed the `DELETE r` before the GC
            # statement, the re-read finds nothing stale and the GC would be
            # skipped, leaving an orphan `:Tag` a rebuilt graph (which never
            # had one) does not. That defect only EXISTS on a re-issue, so
            # forcing the graph-wide `:Tag` scan on every first attempt would
            # put a full-label scan on the SDK's hottest mutation door for
            # nothing — the one thing the sibling paths in `edges.py`
            # deliberately avoid. The counter is the attempt number: `fn` is
            # invoked once per attempt by `_graph_write_with_retry`.
            _sync_attempts = 0
            # WITNESS (#8854 review round 5, P1). This arm rode the retry with
            # `_sync_tags` returning None, so there was nothing to testify
            # with — and a re-issue that lands NOTHING reported success.
            # Measured pre-fix with the Point absent from the replayed graph:
            # `update_point(id, tags=[a, b])` returned normally while the graph
            # held ZERO TAGGED edges and no Point at all. The retry exists for
            # the rebuild window, and in that window the not-yet-replayed Point
            # is exactly the case it must NOT swallow: a MERGE against a
            # missing anchor matches nothing, succeeds, and looks like a write.
            #
            # The witness compares NAMES, not edges: `MATCH (p:Point {id:$pid})`
            # fans out over every node carrying that id (the duplicate-id shape
            # `_mark_dirty` also accounts for), so an edge count exceeds the
            # requested name count and reports a NON-landing on a sync that
            # landed. `_sync_witness` therefore returns a DISTINCT-name count.
            # "At least one" would call a half-landed sync good, and on an
            # absent Point it would still be zero — the defect itself.
            # The COUNT is read back from the graph rather than returned by
            # `_sync_tags`, because the reconciler is the projection's (shared
            # with replay, #2897) and its return value is not a landing proof:
            # a `MERGE` that matches nothing is not an error.
            #
            # The requested names are DEDUPED for the count only; the value
            # handed to `_sync_tags` is the caller's own, unchanged.
            _sync_raw_tags = props["tags"] or []
            # DEDUPE for the count only. A list is witnessed whenever its entries
            # are HASHABLE, which is the condition `dict.fromkeys` actually
            # needs: `tags=["a", 1]` is a legal tag list (the engine creates
            # both `:Tag` nodes and raises nothing), so disabling the witness for
            # it would re-open the very false success this arm closes — measured:
            # under an abort that wiped the Point, `tags=[1, 2]` returned a
            # success dict with no read-back at all.
            #
            # An UNHASHABLE entry (e.g. `tags=[{"name": "a"}]`) must NOT raise
            # here: a `TypeError` would re-bucket the engine's own
            # `ResponseError: Property values can only be of primitive types`
            # from the "db" family to "structural". Leave the witness vacuous
            # on that shape so the ENGINE keeps owning the error it raises.
            if isinstance(_sync_raw_tags, (list, tuple)):
                try:
                    _sync_wanted = list(dict.fromkeys(_sync_raw_tags))
                except TypeError:
                    _sync_wanted = None
            else:
                # A NON-ARRAY value (a number/dict, outside the public signature
                # but tolerated and pinned by `test_update_non_list_tags_no_crash`)
                # derives no edges BY DESIGN (#8333 owns that), so it must not be
                # witnessed against a count — `None` makes the witness vacuous
                # there instead of inventing a non-landing on a write that was
                # never asked to make edges.
                _sync_wanted = None

            def _sync() -> None:
                nonlocal _sync_attempts
                _sync_attempts += 1
                self._sync_tags(
                    proj, id, _sync_raw_tags,
                    sweep_orphans=_sync_attempts > 1)

            def _sync_witness(_result) -> bool:
                """Did the reconciler leave the Point carrying the WANTED names?

                LAZY on purpose: the wrapper consults a witness only once a
                graph-replaced abort has latched, so a tagged update that never
                retries issues NO read-back. Measuring this is the point of
                keeping it here rather than inside `_sync`.
                """
                if _sync_wanted is None or not _sync_wanted:
                    # A non-array value derives no edges by design (#8333), and
                    # an empty request asks for no edge: neither can be missing.
                    return True
                rows = proj.g.query(
                    "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) "
                    "WHERE t.name IN $names RETURN count(DISTINCT t.name)",
                    params={"pid": id, "names": _sync_wanted},
                ).result_set
                landed = int(rows[0][0]) if rows else 0
                return landed == len(_sync_wanted)

            self._graph_write_with_retry(
                _sync, what="update_point TAGGED edges",
                witness=_sync_witness)
        # Dreaming (#85): property mutations can affect confidence.
        self._mark_dirty([id])
        result = self.get_point(id)
        # The PROMOTE arm's record is NOT emitted here any more (#7951 2b): it
        # commits where the promote lands, inside the arm, so the tag sync and
        # `_mark_dirty` below it cannot strand a landed promotion. The unguarded
        # arms still record BEFORE their writes (the outbox ordering above); the
        # payload is the same dict either way — one record shape per call.
        #
        # ⛔ AN ABSENT ID TAKES NO RECEIPT (#10079 review round 2, P2). This
        # door returns `{}` for an id that does not exist — a documented
        # contract (`this verb returns {} for a missing id`, asserted at
        # tests/test_lifecycle_guards.py) — and that path writes NOTHING. Shaping
        # still ran from the caller's props, so `_end_receipt` is non-None, and
        # attaching it turned `{}` into truthy `{'valid_to_shaped': …}`: a caller
        # checking `if not sdk.update_knowledge(id, …)` to detect a missing id now
        # silently passes, and the receipt reports a shaping that was never
        # stored. Same class as the refused-fold branch above: a receipt may
        # only report an end that reached the STORED record.
        if not result:
            return result
        return _with_end_receipt(result, _end_receipt)
