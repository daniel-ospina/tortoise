# Archived sdk-method `create_source` — REMOVED from the product surface (#11609).
#
# batch:   11609-phase2-sources-containers
# reason:  name retired into register_source (#4282 Phase 2, #11609): the approved row 14 keeps this body under its own name, frozen here as the record of what the retired name did
# source:  tortoise/sdk.py @ 32011c4dad4b42364868cb0810ad8e7317a7dfb6
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'create_source'
ARCHIVED_BATCH = '11609-phase2-sources-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def create_source(self, url: str, sourceKind: str, *,
                      tier: str | None = None, sourceDate: str | None = None,
                      source_path: str | None = None,
                      raw_state: str | None = None,
                      is_episodic: bool | None = None,
                      _merge_run_id: str | None = None,
                      **props) -> dict:
        """Create (or merge) a Source node (issue #398 Task 6).

        Dual-write rule (ontology v3.1 §4.6 + code reader contract):
          - ``tier`` given → stored on ``credibilityTier`` (the property the
            inheritance adapter reads); ``sourceKind`` left untouched.
          - ``sourceKind`` is itself a tier-form (T0-T4) → mirrored to
            ``credibilityTier`` as well (canonical per ontology).
          - An EXISTING Source (URL collision) NEVER has its ``sourceKind``
            overwritten — tier lands on ``credibilityTier`` only.
        ``sourceDate`` is the evidence-age clock for decay (falls back to
        ``ingestedAt`` — the documented pipeline-arrival proxy). Invalid tier
        values raise ValueError.

        Epic #900 T3 extensions (plan §4.1/§5.1 pins b/d):
          - ``source_path=`` — the SANCTIONED route for the ``sourcePath``
            secondary prop (mirrors the ``api.add_document(source_path=)``
            precedent the sanitizer's docstring carves out): the path is
            pre-validated under the corpus root / TORTOISE_INGEST_BASE_DIR by
            the indexer, and props-passthrough of ``sourcePath``/``source_path``
            stays rejected by ``_sanitize_props`` (fail-closed, #329). The
            write path emits the ev key ``source_path`` → ``s.sourcePath`` on
            the node (camelCase; ``_SOURCE_HANDLED`` membership keeps the
            snake_case key from persisting verbatim).
          - the Source MERGE is CONDITIONAL (single statement, pin b): the ON
            MATCH version/updatedAt/contentHash/title/``_searchText`` bump
            fires ONLY when the stored contentHash differs (a stub Source with
            NULL contentHash is completed). The write's own QueryResult
            carries ``previousProps`` (the pre-write node) and is returned ON
            THIS CALL to ``create_source`` (#5024 P2-2) — never a shared
            projection slot. The index path's counter authority is a
            POST-MERGE re-read of ``s.__runId``/``s.version`` in
            ``_index_source_merge``, not a MERGE stats counter.
          - JOURNALING CONTRACT (a) (cycle-18): the write path emits a
            SourceCreated JSONL line on EVERY write (emit-on-every-write — a
            create-only cadence would revert updated Sources to create-time
            state post-rebuild); replay re-MERGEs by url and the hash-diff-gated
            bump lands at the live converged value.
            **#5024 (T6) supersedes the cadence, not the reason.** Emit-on-
            every-write was the right shape for a journal that had no record
            for a version transition, and it cost the two things §9.6 buys:
            a no-op re-check appended a line (the log grew on re-checks), and
            because the record said nothing about the transition, `updatedAt`
            and `ingestedAt` were re-read from the clock AT REPLAY TIME and
            diverged from the live node (measured `...57.016262` vs
            `...57.079810`). The cadence is now per-OUTCOME — `SourceCreated`
            (create / stub completion), `SourceVersioned` (a real hash
            transition, carrying `previousContentHash`),
            or NOTHING (a re-check that found what we already hold). The
            invariants the old contract protected are unchanged; see
            `docs/durability-posture.md` → *`:Source` — recorded vs
            recomputable, field by field* (R2) and
            `docs/architecture/STORAGE-ARCHITECTURE.md` §9.6.
          - ``raw_state=`` (#3998, D30) — the ABSENT-RAW state. A DELETION is
            an ACT and is recorded durably: one of ``present`` / ``deleted``.
            ``offline`` / ``access_revoked`` are CONDITIONS, observed live and
            NEVER stored (the owner's #3998 ruling, Option A, 2026-10-14) —
            passing one is refused with a message saying so; the observation is
            caller-supplied — a caller that has actually probed the raw routes
            it through ``raw_state.resolve_raw_availability`` (no shipped reader
            supplies one today, #8740). The
            deletion marker is BOUNDED by ``raw_state.RAW_DELETION_MARKER_TTL``
            (bounded metadata about the link back to the raw — never a
            retention period on user content, R1). Validated strictly (an
            unknown value raises ``ValueError``) and
            PRESERVED by a later upsert that carries none, so a re-ingest
            cannot resurrect a raw deleted between the two writes. Pass
            ``raw_state="present"`` to record that a raw came BACK — that is
            the only way to move a state off an absence, by design. The state
            rides the ``SourceCreated`` / ``SourceVersioned`` journal (a state
            change riding a version transition is journalled as
            ``SourceVersioned``; see ``docs/ONTOLOGY.md`` §4.6), so it survives
            ``rebuild_all``.
            ⚠️ This is the write half of the two-store model: the graph keeps
            identity + version + availability and NEVER the bytes, so props
            that would carry raw payload are refused (see ``_SOURCE_EXTRA_PROPS``
            in ``projection/entities.py`` for the declared :Source surface).
        """
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        if not url or not url.strip():
            raise ValueError("url must be a non-empty string")
        from tortoise.source_credibility import TIER_PRIORS, canonical_tier
        if tier is not None:
            _orig_tier = tier
            tier = canonical_tier(tier)
            if tier is None:
                raise ValueError(
                    f"Invalid tier {_orig_tier!r} — must be T0..T4 or a legacy alias "
                    f"(gold/high/medium/low/unverified)"
                )
        if sourceKind in TIER_PRIORS and tier is None:
            tier = sourceKind  # tier-form sourceKind mirrors to credibilityTier
        # #329: props passthrough of the server-managed sourcePath keys stays
        # fail-closed — ONLY the sanctioned source_path= keyword route carries
        # it (the sanitizer's docstring carve-out; §4.1). ``id`` overrides are
        # equally server-managed (node identity) — rejected here because
        # ``_create_entity``'s reject_id is bypassed for the sanctioned route.
        for _k in ("sourcePath", "source_path", "id", "is_episodic",
                   "rawState", "rawStateAt", "raw_state"):
            if _k in props:
                # #1501: name the actual sanctioned keyword per key (is_episodic
                # became a sanctioned create_source keyword in this change; #3998
                # adds raw_state for the absent-raw state, server-managed for the
                # same reason — a props payload must not be able to CLEAR a
                # recorded absence, which is the whole point of the state).
                sanctioned = ("source_path" if _k in ("sourcePath", "source_path")
                              else "raw_state" if _k in ("rawState", "rawStateAt", "raw_state")
                              else _k)
                raise ValueError(
                    f"{_k!r} is a server-managed field and cannot be set via "
                    f"props — use the sanctioned create_source({sanctioned}=) "
                    f"keyword (epic #900 §4.1)."
                )
        # #5196 round 5, P1: the token has TWO spellings, and this is the second.
        # `_merge_run_id` is the PARAMETER whose `run_clause` writes `s.__runId`,
        # so closing the property name alone left the same node property settable
        # through `props={...}` (the MCP `props` convention and the bundle splat
        # both land here). Measured before this: `create_source(...,
        # props={"_merge_run_id": "P"})` wrote `__runId == "P"` on the node.
        # NOTE the EXPLICIT `_merge_run_id=` keyword stays available: it is the
        # internal index-merge route (`_index_source_merge`), which is the only
        # legitimate writer and does not arrive through `props`.
        for _rk in ("__runId", "_merge_run_id"):
            if _rk in props:
                raise ValueError(
                    f"{_rk!r} is a server-managed field and cannot be set via "
                    f"props — the merge-run token is minted by the server "
                    f"(#5196)."
                )
        # Lane L5 (#5097 review P1): `_stub_completion` is the CAPTURE-
        # COMPLETION DISCRIMINATOR. `_upsert_source` reads it as a PARAMETER
        # (`$stub_completion`) and it SELECTS WRITE BEHAVIOUR twice: it
        # suppresses the ON CREATE arm's creator-only defaults
        # (`id`/`version`/`externalId`) and it widens the passthrough to
        # `_SOURCE_CAPTURE_REPLAY_PROPS`. Because this method builds `ev` as
        # `{..., **props}` and `_skip_sanitize=True`s the projection, an
        # unlisted key rode straight through — measured on the review head:
        # `create_source("session:x", "agentSession",
        # _stub_completion=True, sessionId="forged", capturedAt="…",
        # eventId="forged")` (a) PERSISTED all three provenance props the
        # record's own comment says are denied here (#3998), (b) created the
        # tenant's node with NO `id`/`version`/`externalId` — defeating this
        # method's own `id` reject — and (c) JOURNALLED the forged key + props,
        # so every rebuild restored the forgery. Same class as `_stub`
        # (#7936), `__runId` (#5196) and `contains_session` (#3947): a payload
        # key that decides replay behaviour is server-managed, not a prop. The
        # ONE producer is `_materialize_session_source`'s own `_emit_event`
        # call, which never re-enters this method.
        if "_stub_completion" in props:
            raise ValueError(
                "'_stub_completion' is a server-managed capture-completion "
                "discriminator and cannot be set via props — it is set only "
                "by the session-capture writer (#5097)."
            )
        # #9451: its sibling on the same record — `True` declares the record's
        # `ingestedAt` to be the instant the write STORED, which the fold's ON
        # MATCH arm then applies. Same class: a forged key moves a `:Source`'s
        # `ingestedAt` on every replay. Same hard-reject; the ONE producer is
        # the session-capture writer's own `_emit_event` call.
        if "_ingestedAtStored" in props:
            raise ValueError(
                "'_ingestedAtStored' is a server-managed journal discriminator "
                "and cannot be set via props — it is set only by the "
                "session-capture writer (#9451)."
            )
        # #3998 (D30): the LOUD half of the payload guard. The guarantee itself
        # is the CLOSED :Source property surface in `projection/entities.py`
        # (`_SOURCE_EXTRA_PROPS`), which is the only form that can hold — a
        # blocklist cannot enumerate the payload name space (measured: `text`,
        # `snippet`, `transcript`, `chunks`, `blob`, … all persisted a 2 KB body
        # on the open passthrough). This refusal exists so the COMMON mistake
        # gets an actionable error instead of a silent drop; an unlisted
        # spelling is still denied, one layer down.
        for _k in ("content", "body", "raw", "payload", "raw_content", "rawContent"):
            if _k in props:
                raise ValueError(
                    f"{_k!r} carries raw payload and cannot be stored on a "
                    f":Source — the graph INDEXES the raw, it is not the raw "
                    f"store (D30/#3919, #3998). Put the bytes in the raw store "
                    f"and pass contentHash= instead."
                )
        # #5256: the `extractedFrom` read-version anchor and its node carrier
        # are server-derived. `create_source` is the one writer that BYPASSES
        # `_sanitize_props` (`_create_entity(..., _skip_sanitize=True)`), so it
        # needs this reject itself — otherwise a caller-supplied `sourceVersion`
        # would persist on the Source node (unjournalled-value mismatch: the
        # value means nothing there, but it is a forged-looking provenance
        # field the tenant never owned). Same key family as the Point reject.
        for _svk in ("sourceVersion", "sourceVersions",
                     "sourceVersionTransit"):
            if _svk in props:
                raise ValueError(
                    f"{_svk!r} is a server-managed provenance field and cannot "
                    f"be set via props."
                )
        # #3644 review (P2b) + PR #8016 code-review ISSUE 1: the version
        # window and the version-TRANSITION keys are server-minted/journalled,
        # never caller-asserted. Before this guard the passthrough silently
        # DISCARDED a caller `validFrom` (the node got the minted instant while
        # the journal `SourceCreated` carried the caller's value — a wrong
        # window start reported OK), and `previousValidTo`/`previousContentHash`
        # collided with the explicit `previousValidTo=`/`previousContentHash=`
        # keywords on the `SourceVersioned` emit -> a swallowed `TypeError`
        # DROPPED the transition record while the live node had already moved
        # (fail-open, live != replay). Rejected loudly here, mirroring
        # `rawState`/`sourceVersion`: the sanctioned route is the server mint
        # (`create_source` mints `validFrom` once, on the `updatedAt` clock) and
        # the `SourceVersioned` record (`previousValidTo`/`previousContentHash`
        # are transition facts, `_SOURCE_HANDLED`).
        for _wk in ("validFrom", "validTo", "valid_to", "previousValidTo",
                    "previousContentHash"):
            if _wk in props:
                raise ValueError(
                    f"{_wk!r} is a server-managed version-window/transition "
                    f"field and cannot be set via props — `validFrom` is "
                    f"minted by the server on the `updatedAt` clock and the "
                    f"transition facts are journal-only (#3644)."
                )
        ev = {
            "url": url,
            "sourceKind": sourceKind,
            "ingestedAt": __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc).isoformat(),
            **props,
        }
        # #2296 review: this door bypasses `_sanitize_props`
        # (`_skip_sanitize=True`), so the shared intake strip cannot cover it —
        # and these are the names that are `_emit_event` PARAMETERS
        # (`recorded_ts` binds the record's `ts`; `point`/`payload` bind the
        # record's snapshot/payload; `type_` is the type parameter) or the
        # record's dispatch key (`type`), or overwrite the built envelope via
        # `event.update(extra)` (`ts`/`event_id`/`projection_version`), or ride
        # into the record as a forged payload key (`new_content`), plus the
        # reserved ACTOR keys (`initiated_by` and siblings), which the sanitizer
        # would also have stripped. Drop them all, as `_create_entity` drops
        # point/payload on its own route.
        for _emit_name in (*_RESERVED_ENVELOPE_PROPS, *_RESERVED_EMIT_CALL_PROPS,
                           *_RESERVED_ACTOR_PROPS):
            ev.pop(_emit_name, None)
        # #5140 P1: the documents METER KEY is not caller-writable IN THE
        # DEMOTION DIRECTION. Runs BEFORE the journal append below, so a
        # refused write leaves no record to replay.
        # ⛔ The PRESENCE test is hoisted out of the helper on purpose: the
        # stored-kind read is a graph round trip (plus `_resolve_source_url`'s
        # own), and `documentKind` is absent on essentially every producer
        # (`_index_source_merge` calls this per file), so an unconditional
        # lookup would put 2-3 queries on the hottest `:Source` write path and
        # a new pre-write failure mode — for a key nobody supplied.
        if "documentKind" in ev or "document_kind" in ev:
            _refuse_document_kind_demotion(
                self._stored_source_document_kinds(
                    self._get_proj().g, self._resolve_source_url(url)),
                ev, surface="create_source")
        if is_episodic is not None:
            # #1488: server-managed quota discriminator — explicit param only
            # (mirrors create_point). The props passthrough is rejected above.
            ev["is_episodic"] = is_episodic
        if source_path is not None:
            ev["source_path"] = str(source_path)
        if raw_state is not None:
            # #3998 (D30): the absent-raw state — the THIRD value on the source
            # record, after identity (url) and version (contentHash). Validated
            # HERE because the WRITE side is strict: silently dropping a
            # recorded absence is the exact failure this issue closes.
            # ``raw_state="present"`` is the "the raw came back" write.
            ev["rawState"] = validate_raw_state(raw_state)
            ev["rawStateAt"] = __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc).isoformat()
        if tier is not None:
            ev["credibilityTier"] = tier
        if sourceDate is not None:
            ev["sourceDate"] = sourceDate
        if _merge_run_id is not None:
            # internal run token: the race-safe CREATE discriminator for the
            # conditional-MERGE outcome (the embedded backend's nodes_created
            # stats are unreliable under concurrent same-key MERGEs). Rides
            # the ev dict to _upsert_source (popped by apply) and is stripped
            # from the journaled payload below.
            ev["_merge_run_id"] = _merge_run_id
        # #4647 (code-review P1): this writer bypasses `_sanitize_props`, so the
        # numeric-domain guard every other props surface gets must run here too.
        # It runs over the ASSEMBLED event dict rather than over `props`:
        # guarding `props` alone left the explicit keyword arguments reaching the
        # store unguarded, so `create_source(url, "web", sourceDate=2**70)` was
        # still silently clamped — the exact defect #4647 exists to fix, still
        # live on a public tenant write surface (and reachable through `ingest`,
        # which splats `**item` into this method). Running it here, after the
        # CALLER-facing assignments, covers every field a caller can reach: a
        # caller field added to `ev` above this point cannot bypass the guard.
        # (Two assignments do follow it — `updatedAt` and `validFrom`, minted
        # from this door's one clock — so the cover is over caller ingress, not
        # over literally every later line. Stated because the earlier wording
        # "after every `ev` assignment … cannot bypass a guard that runs after
        # all of them" was false as written, and #11858's guard below says why
        # that distinction matters.)
        for _key, _value in ev.items():
            _reject_unrepresentable_number(_key, _value)
        # #11858: the embedding MARKER family is not caller-writable on this
        # door either — and the placement follows the two gates above for the
        # same reason: this writer bypasses `_sanitize_props` entirely
        # (`_skip_sanitize=True`), it is the ONE door measured to accept them,
        # and it writes the node BEFORE it emits, so a refusal at `_emit_event`
        # would land too late and leave a live-only `:Source` (the #2296
        # divergence). Over the ASSEMBLED `ev`, so the guard follows every
        # CALLER ingress of this door (the `**props` splat and the explicit
        # keyword arguments) rather than a caller-argument list. It is NOT a
        # claim that no `ev` assignment follows — two server-minted ones do, and
        # the note below the guard names them.
        #
        # Measured at `7e6baffc5d`, one `create_source` call per key, none of
        # them carrying any vector — i.e. every marker describing NOTHING:
        #
        #     create_source(url, "web", embedding_width="f16")
        #       -> OK, journalled SourceCreated with `embedding_width: "f16"`
        #          and no `embedding` key anywhere (#11996 measured the same
        #          through this door); `embedding_verbatim` / `_preserved` /
        #          `_model` / `_revision` / `_text_hash` / `embeddingText`
        #          land identically.
        #
        # The other public props doors were driven the same way in the same
        # measurement — all TEN of them (the eleven `**props` write doors minus
        # this one), not just the four this note first listed (#11858 review
        # cycle 3, P3): `create_point`, `create_entity`,
        # `create_document`, `create_object`, `create_subject`, `create_event`,
        # `update_point` and `update_entity` refuse the name at intake; `update`
        # and `update_knowledge` refuse on an EXISTING node and silently no-op
        # on an unknown id (so a census row for those two must create its target
        # first — a row without one asserts nothing). This door was the only
        # leak, which is why the guard is here and why the test drives every
        # door rather than this row alone.
        #
        # ⚠️ Scope of "covered": this guard sits above every CALLER ingress
        # (`**props` is splatted into `ev` at the top of this method), so a
        # caller key cannot arrive below it. It is NOT a claim that no `ev`
        # assignment follows — two server-minted ones do (`updatedAt` /
        # `validFrom` from the one clock), and they are this door's own values,
        # not caller input. That distinction is pinned by the control test rather
        # than by this comment (a marker injected after the guard is caught
        # there; the guard itself never sees it).
        _caller_markers = sorted(set(ev) & _SERVER_MANAGED_EMBEDDING_MARKERS)
        if _caller_markers:
            raise ValueError(
                f"{_caller_markers} are server-managed embedding journal "
                "fields and cannot be set by hand. On this door the writer "
                "declares only the WIDTH, derived from the vector it stores — "
                "pass `embedding=` and it declares the truth; the rest are "
                "either inputs the writer READS (a supplied value forges that "
                "reading) or identity stamps it never mints on a `:Source`. A "
                "marker written here describes no values at all, which is "
                "exactly what a later width/base64 migration trusts (#11858)."
            )
        # #2782 (review Finding 3): the same reason, one field family over — and
        # the same placement discipline, over the ASSEMBLED ``ev`` so a later
        # keyword cannot bypass it. This door is the one props write that never
        # reaches `_sanitize_props` (`_skip_sanitize=True`), so without this the
        # money gate left it open: measured at `51deb8ec`, a malformed money bag
        # here was ACCEPTED (reported `ok`), silently DROPPED by the closed
        # `:Source` surface (`_SOURCE_EXTRA_PROPS` carries no money key, #3998 —
        # deny + log) and still JOURNALLED on the `SourceCreated` record, i.e. a
        # durable money value nothing ever validated. A write that reports
        # success while dropping the amount is exactly the strip-and-continue
        # this gate's sibling comment rejects.
        #
        # ⚠️ Stated bound: this refuses a MALFORMED bag. A WELL-FORMED money
        # value is still dropped from the NODE, because a `:Source` carries
        # provenance and has no declared money field at all — that is #3998's
        # closed-surface decision, not this gate's, and it is pinned by
        # `tests/test_money_value_2782.py::TestNonSanitizedSeams`
        # ::test_a_wellformed_money_value_on_a_source_is_still_dropped.
        # ⛔ That bound is about the NODE, and the prose above must not be read
        # as covering the RECORD: `ev` is what journals a `SourceCreated` row, so
        # a well-formed value still rides the journal (measured: node money
        # `[None, None]`, journal record carrying `amountMinor=7000000,
        # currency='BRL'`) and is dropped again when the replay folds it. The
        # graph therefore stays consistent, but the value is durable in the
        # record lane until a `:Source` surface decision says otherwise (#3998) —
        # recorded here rather than implied away, and pinned by the test's own
        # journal assertion (#11836 review, tortoise#11863).
        refuse_malformed_money(ev, door="create_source")
        # ── #5024 (T6): ONE clock for the transition ────────────────
        # `_upsert_source`'s ON MATCH sets `updatedAt = coalesce($updatedAt,
        # $now)` where `$now` is `_now_iso()` called INSIDE the fold. Reading
        # the clock TWICE (once here for the record, once in the fold for the
        # live node) is precisely how `updatedAt` diverged live vs replayed
        # before this change. So the instant is minted ONCE, rides the payload
        # as `updatedAt`, the fold's `coalesce` picks it up, and the record
        # carries the SAME string — live and replay cannot disagree.
        #
        # ── #3644 (§4.6 window half): the version's valid-time START ────
        # The SAME minted instant is the version's `validFrom`, and the two
        # fields are one clock read, not two (a second `_now_iso()` here is
        # exactly the live-vs-replay divergence the block above removes).
        #
        # WHY THE OBSERVATION INSTANT IS A LEGITIMATE `validFrom`.
        # §4.7 forbids borrowing the WRITE clock as a valid-time start for a
        # POINT (#3654): a session's occurrence date is a fact about the
        # content, so synthesizing one from the ingest clock asserts something
        # we do not know. A `:Source` VERSION has no such content-side date:
        # a version IS "the content as read at an instant", so the instant it
        # was read is definitional, not borrowed — and §4.6 requires the
        # transition to CLOSE the prior version's window, which can only be
        # closed at a boundary we observed (`validTo` of the old =
        # `validFrom` of the new, contiguity by construction).
        #
        # The instant is minted for EVERY call and lands on the node ONLY
        # through `_upsert_source`'s hash-diff gate (create, stub completion,
        # hash change) — so a no-op re-check moves nothing, live or replayed,
        # and `validFrom` is added to `_SOURCE_NOOP_IGNORED_KEYS` for the same
        # reason `updatedAt` is (minted per call ⇒ it can never be the fact
        # that makes an otherwise-identical re-check "different"). A producer
        # that KNOWS an earlier instant (a publication date) is a separate
        # additive input — deliberately not accepted here, because a
        # caller-asserted start needs the inversion guards the Point window
        # needed (#3980, #4021, #5358, #5360) and this lane has no such input.
        #
        # A SAME-HASH write that changes an extra still emits a `SourceCreated`
        # carrying this minted instant, exactly as it already carries the
        # minted `updatedAt` — the gate ignores both on that arm, so the record
        # over-states neither: reading the version timeline means reading the
        # FIRST `SourceCreated` plus the `SourceVersioned` records (there is one
        # node per `url` and only a create or a hash transition opens a
        # window). Stripping the key on that one arm would buy journal tidiness
        # the `updatedAt` precedent does not have, at the cost of two emission
        # variants that can drift.
        _version_instant = _mint_source_window_instant()
        ev["updatedAt"] = _version_instant
        ev["validFrom"] = _version_instant
        # #5024 P2-2: the pre-state rides THIS CALL's own return value, not a
        # projection-level slot. A slot is shared by every call on the SDK, so
        # two threads sharing one SDK on DIFFERENT urls (different per-url
        # locks, so they can interleave) could swap each other's
        # `previousProps` and lose a real transition.
        result, _written = self._create_entity(
            "Source", url, ev, "SourceCreated",
            _skip_sanitize=True, _return_apply_result=True)
        # ── #5024 P1-A: the pre-write state the RECORD DECISION needs ──────
        # Taken from the WRITE'S OWN statement (`_upsert_source` captures it
        # with `OPTIONAL MATCH`/`WITH` before its `MERGE` and returns it as
        # `previousProps`), so it is the state the write actually found. A
        # separate pre-read here was a DIFFERENT read from the one the write's
        # `ON MATCH` hash gate evaluated: a competing writer in that window
        # made the decision compare the incoming hash against a value the
        # node no longer had, and the branch below suppressed a real
        # transition (fail-OPEN toward suppression). `None` now means only
        # "the statement found no node" — never a swallowed read failure — so
        # the create arm is entered on real evidence. This also removes the
        # unconditional extra round trip (plus its internal `resolve_source_key`)
        # that P2-B flagged on the no-journal path.
        _stored_before = None
        if _written is not None and getattr(_written, "result_set", None):
            _stored_before = _written.result_set[0][0]
        # ── #5024 (T6): journal the write by WHAT IT CHANGED ────────────────
        # Pre-#5024 this emitted a plain `SourceCreated` on EVERY call, so a
        # re-check that found the same bytes still appended a journal line
        # (measured: 12 -> 13 lines on a no-op re-check). §9.6 bounds a version
        # at "three timestamps and a hash", so the re-check must be REPEAT-SAFE:
        #
        #   node did not exist, or a stub is being completed  -> SourceCreated
        #   a non-empty hash DIFFERS from the stored one       -> SourceVersioned
        #   nothing about the write would change the node      -> emit NOTHING
        #
        # The last arm is NOT "same hash": a hash-identical call may still
        # carry a new `summary`/`topics`, and those ride the payload into
        # `_persist_extra_props` on replay — suppressing THAT record would
        # trade log growth for a live!=replay divergence. So the no-op test is
        # over the payload's OWN keys, and anything it cannot prove unchanged
        # is RECORDED (fail-safe polarity: an unnecessary record is cheaper
        # than a lost change).
        try:
            payload = {k: v for k, v in ev.items()
                       if k not in ("_merge_run_id", "__runId")}
            _new_hash = payload.get("contentHash")
            _old_hash = (_stored_before or {}).get("contentHash")
            if _stored_before is None or (not _old_hash and _new_hash):
                # CREATE (no node), or a JOINT-E2E stub completion (#1032).
                #
                # The `_new_hash` guard is load-bearing, not decoration. A
                # hashless create stores `contentHash = coalesce($hash,'') = ''`,
                # so `not _old_hash` stays TRUE on that node forever — and an
                # incoming payload that ALSO carries no hash took this arm
                # unconditionally, pre-empting the no-op test below. Measured:
                # 5 identical `create_source(url, "document")` calls appended 5
                # records and changed nothing (version stayed 1). Production-
                # reachable: `hosted_api.py` re-ingests with
                # `contentHash=anchor` (None when no provenance ref carries a
                # hash) and with `contentHash=src.contentHash or ""`.
                #
                # A stub COMPLETED by a real hash still records (that is the
                # #1032 contract, pinned by
                # `test_a_stub_completion_still_emits_a_create`); a hashless
                # re-check now falls through to the no-op test, which suppresses
                # it only if the write really does leave the node unchanged.
                self._emit_event("SourceCreated", id=url, **payload)
            elif _new_hash and _new_hash != _old_hash:
                # The version transition. `previousContentHash` is the whole
                # point: it is what makes the PRIOR version addressable after
                # the node has moved on (`ONTOLOGY` §4.6 *Versioning*). The
                # transition instant is NOT a separate key — the payload's own
                # `updatedAt` (minted above, one clock) IS it, and the fold
                # reads exactly that. The record deliberately carries no
                # recomputed ordinal: the replay reproduces `version` through
                # the SAME hash-diff gate the live write used, so recording it
                # as well would be a second derivation that can disagree.
                self._emit_event(
                    "SourceVersioned", id=url,
                    previousContentHash=_old_hash,
                    # #3644: the PRIOR window's close, named as a recorded act.
                    # Equal to the payload's `validFrom` BY CONSTRUCTION (the
                    # same minted instant, above) — contiguity is the whole
                    # point of §4.6's "the journal record that opens a new
                    # version closes the old one in the same act". It is a
                    # JOURNAL-ONLY fact: the node holds the CURRENT version, so
                    # the fold must not write it (`_SOURCE_HANDLED` skips it,
                    # like `previousContentHash`).
                    previousValidTo=payload["validFrom"],
                    **payload,
                )
            elif self._source_payload_is_noop(payload, _stored_before):
                # Genuine re-check: the caller re-sent what we already hold.
                # Nothing is journalled, and nothing changed live either.
                pass
            else:
                # Same hash, but the payload carries something new (a retitled
                # summary, a new topic list). Record it so the extras survive a
                # rebuild — the pre-#5024 behaviour.
                self._emit_event("SourceCreated", id=url, **payload)
        except Exception:  # noqa: BLE001, RUF100
            _logger.warning("source journal emission failed for %s — continuing", url)
        # Write events invalidate the inheritance gate + reliability cache
        self._invalidate_inheritance_gate_for_source(url)
        self._clear_reliability_cache(url)
        return result
