# Archived sdk-method `create_point` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_create_point`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'create_point'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def create_point(self, kind: str, content: str, *, is_episodic: bool | None = None,
                      credibility: str | int | float | None = None,
                      claim_class: str | None = None,
                      **props) -> dict:
        # #1486 (code-review P1): is_episodic is a SERVER-MANAGED flag — bound
        # to this explicit param (never the props passthrough, which
        # _sanitize_props rejects). Internal capture/extractor callers pass it
        # here; tenant props cannot reach it (MCP boundary rejects + sanitize
        # backstop).
        """Create a new Point node. Raises ValueError if kind is invalid.

        Set dedup=True for idempotent creation. The dedup identity is
        ``(pointKind, content_hash)`` and — when a ``key`` is supplied —
        ``(pointKind, key)`` as well: a re-run under the same key FOLDS onto
        the point already holding it (within that pointKind) instead of
        minting a second point that shares the key (#6110). A key is therefore
        unique PER-KIND, never globally, and is never silently reassigned: a
        content-dedup hit on a point that already holds a DIFFERENT key keeps
        that key and warns.

        LIVE is the default (#1088): a point created WITHOUT an explicit
        ``status`` is live — not draft — whatever its kind. Draft is opt-in:
        pass ``status="draft"`` and it is honoured (no promotion clause,
        ingest fold, or operator edge will move it to live; the ONE scoped
        exception is a point the capture pass MINTS — it promotes the drafts
        it created itself, keyed on ``promotion_ids``, and never one it merely
        folded onto).

        Decision parts (#2199) — pointKind in {decision, option, criterion,
        evidence} created WITHOUT an explicit status — land LIVE (not draft)
        with an explicit, provenance-recorded starting belief: the default
        prior 'medium' = Beta(3,1) stamped ``system-default``. A passed
        ``credibility`` (plain-language ladder word gold/high/medium/low/
        unverified, T0-T4 form, or numeric 0-4) is a LABEL, stamped
        ``set-by-author``, that ADDS one weak evidence body on top of that
        baseline rather than replacing it (#398). Decision parts are
        human-authored judgment with no document Source to inherit belief
        default keeps the documented decide flow rankable on the first try
        with zero promote/calibrate chores and no silent Beta(1,1)-as-
        calibrated (the #7478 guard). Callers that explicitly pass a status
        (e.g. the capture/extraction paths, which pass status="draft" +
        extractedFrom) keep full manual control — no system default is applied.

        ``credibility`` is a VALIDATED LABEL, not a strength (#398 owner
        ruling 2026-10-12): an authored, unsourced assertion is ONE evidence
        body at the weakest registered weight, the same whoever wrote it. The
        word is a SOURCE that ADDS that weak body ON TOP OF whatever baseline
        the claim already has — never replacing it — so a decide part born live
        starts at Beta(3,1) + the weak body, a plain claim at the weak body
        alone, and a labelled claim can never score below the same claim
        unlabelled. The word no longer selects the ladder prior itself, but
        the ladder vocabulary is still validated (unknown words raise) and the
        word is persisted as the ``credibility`` Point property. Real sources
        attached later can carry the claim above the word through ordinary
        inheritance.

        PROVENANCE ADVISORY (#8171): a fresh claim written with NO source and
        NO provenance returns ``advisories: [{code: 'no_source', node_id,
        message, advisory: true}]`` — a transient key on the write RESULT (never
        a node property, never journaled), absent whenever the write is
        sourced. It is ADVISORY ONLY: the Point has already landed, nothing is
        refused, and stored state is unchanged. A claim is sourced here when it
        carries a declared or ``session_id``-inferred ``extractedFrom`` (this
        door materializes the ``:Source`` and the edge) or an authored
        ``credibility`` word (which #398 rules IS a source). The message names
        the cheap fix — ``extractedFrom='session:<id>'`` for an assertion the
        writer itself originates.

        ``claim_class`` (#2351, epic #2349 E-derived) declares whether this
        claim is AUTHORED (``asserted`` — the default, and what every claim in
        every existing graph already is) or COMPUTED from other beliefs
        (``derived``). ⛔ Only ``derived`` is STORED: the ``claimClass``
        property is written when — and only when — the claim is derived, so
        absence IS the asserted class, there is no migration and no re-stamp
        for existing claims, and passing ``claim_class="asserted"`` explicitly
        is accepted and stores NOTHING (two spellings of one class would invite
        a later reader to treat one as a third state). The class is a property
        of the WRITE THAT MINTS the claim: it rides the CREATE, and a dedup hit
        folds onto the existing claim WITHOUT re-stamping its provenance. A
        derived claim is second-class by construction — the write path refuses
        to wire it above an asserted claim and refuses to let it supersede one
        (see ``tortoise/claim_class.py``).
        """
        # #9287: a kind is an IDENTIFIER — refuse invisible padding/format
        # characters BEFORE `_validate_kind` would only WARN about them. The
        # warning is what let this shape through: a padded kind is not an
        # unrecognised spelling to register, it is one the calibration gate
        # classifies as a DIFFERENT kind, so EP consumed and stamped a node the
        # gate never demanded a prior for (measured; the read-path enumeration
        # that guards imported rows is documented on
        # `_KIND_WHITESPACE_CHARS`). First, so a caller whose value is not a
        # kind at all hears that, not an approval complaint about it.
        _refuse_invisible_kind("pointKind", kind, door="create_point")
        self._validate_kind(kind)
        # ── #9964: the kind's LAYER, reported to the caller ─────────────────
        # `_validate_kind` above warns `_logger` about an unrecognised spelling;
        # it never asks which LAYER declares the kind, and it never reaches the
        # caller or the result. So `create_point(kind='core:deployment')` — an
        # EVENT kind — minted a `:Point` (a claim EP prices) in silence, and the
        # Event/state fold never saw the occurrence (#9675's step-1 table).
        # This door cannot re-route losslessly (a Point and an Event are
        # different records, and §5 declares `decision`/`humanApproval` on BOTH
        # axes — the #7813 owner ruling), so the disagreement is REPORTED and
        # the write lands as asked (warn-not-block, #1934).
        # ⚠️ Computed AFTER `_coerce_props` and the props kind gates (further
        # down): the effective kind is the one the fold will STORE, and the
        # MCP-style nested `props={"pointKind": …}` shape only exists after the
        # coercion (read here, it missed the primary caller and reported nothing
        # — measured on `create_point("statement", "x", props={"pointKind":
        # "core:deployment"})`).
        # #5475: `pointKind` is one of the properties
        # `pack_state.graph_kind_namespaces` mines the approval set from, so the
        # point door widened it exactly as the entity doors did — measured at
        # HEAD: `create_point("totally:madeUpKind", ...)` on a `dev`-only graph
        # left `['dev', 'totally']` behind. `_validate_kind` above only WARNS,
        # so it is not a gate. Same rule, same ONE home, same refusal message
        # as the entity funnel (`_refuse_unapproved_kind`).
        self._refuse_unapproved_kind("pointKind", kind)
        _coerce_props(props)
        # #10000 / §9.1: the POINT door is closed too (#10389 review cycle 3).
        # The Point fold does not refuse `story_arc`, so this door persisted
        # §9.1's prose on a node (measured: `create_point("statement",
        # "a claim", story_arc=<prose>)` → `(:Point {story_arc: <the prose>})`).
        # `"Point"` is outside `_NARRATIVE_LABELS`, so this refuses on the label.
        _refuse_unpaired_narrative(props, door="create_point", label="Point",
                                   journal_armed=bool(self._event_log_path))
        # #9287: the props passthrough can carry `pointKind` itself and it WINS
        # over the positional kind (measured: `create_point("statement", …,
        # pointKind="\u2003statement\u2003")` stored the padded value), so the same
        # refusal has to run over the mined spellings — the same key set the
        # approval gate below reads, never a second hand-written list.
        self._refuse_invisible_point_kinds(props, door="create_point")
        # The props passthrough accepts the OTHER mined key names too
        # (`objectKind`, `eventKind`, ...), and each of those is persisted on
        # the Point and mined back into the approval set — measured:
        # `create_point("statement", "x", objectKind="marketing:campaign")`
        # widened a `dev`-only graph. Gate the whole mined key set, exactly as
        # the entity funnel does.
        self._refuse_unapproved_kinds_in_props(props)
        # #9964: NOW the effective kind is final — the positional `kind`, then
        # the props passthrough (which WINS over it, #5475/#9287), then the
        # nested MCP `props={…}` flatten. Report the layer disagreement from
        # THAT value. Fail-open (#1934): an advisory that raised would refuse a
        # write that landed before it existed — and the pack-manifest compile is
        # a real source of such a raise (see `kind_layers.section_of`).
        try:
            _layer_note = _first_layer_mismatch(
                [("point", "pointKind", str(props.get("pointKind") or kind))],
                "point")
        except Exception as _layer_exc:  # noqa: BLE001, RUF100 — advisory must never raise
            _logger.warning(
                "create_point: the kind<->layer advisory could not be computed "
                "(%s: %s) — the write proceeds without the notice (#1934: an "
                "advisory never refuses a write).",
                type(_layer_exc).__name__, _layer_exc)
            _layer_note = None
        # #9167: `content` is a REQUIRED str and nothing said so. A ``None`` or
        # any non-str reached ``_content_hash(content)`` below — i.e.
        # ``text.encode("utf-8")`` one frame inside a private helper — so the
        # caller got ``AttributeError: 'NoneType' object has no attribute
        # 'encode'``, a message naming neither this door nor ``content``: a
        # caller error that reads as an internal bug. The MCP
        # ``tortoise_create_point`` tool passes the caller's ``content``
        # straight through, so a client sending ``{"content": null}`` saw that
        # same internal-looking trace on the wire.
        #
        # Fail closed HERE, at the door, naming the argument — the same posture
        # as ``update_point`` (#5240) and ``checkpoint``'s item schema (#8554),
        # each at its OWN door. Placement is deliberate: after the kind gates
        # (so which error a doubly-invalid call sees is unchanged) and before
        # any prop processing, graph read or write, so a refused call leaves
        # nothing behind.
        #
        # ⛔ Deliberately NOT hardening ``_content_hash`` itself: that would
        # close every remaining surface at once but CANNOT name the caller's
        # parameter, and it would hide which doors still lack validation —
        # #8554's own reason for choosing the door over the helper.
        #
        # An EMPTY string stays valid: ``""`` hashes to a real digest, and
        # ``update_point`` already pins it as a real content edit (#5240).
        # Only a wrong TYPE is refused, never a wrong length.
        if not isinstance(content, str):
            raise TypeError(
                "create_point: content must be a str, got "
                f"{type(content).__name__} — `content` is required and "
                "cannot be None (every write path hashes it)"
            )
        # #329: server-managed fields rejected on the props passthrough
        # (the explicit-id path via props.pop("id") below is preserved for operators)
        props = _sanitize_props(props)
        # R2 (#1541) D3: search_keys is stored flat (see _flatten_search_keys_prop).
        _flatten_search_keys_prop(props)
        # #11239 (DM1's home): `mentions` is a list of RECORD dicts and no path
        # stores an array of maps (the projection denies it; a raw `SET n += $p`
        # gets it degraded to `null` by the driver, #7369), so the payload form is
        # encoded to
        # ONE compact JSON scalar here. ⛔ THIS call is the LOAD-BEARING one, and
        # that is measured rather than assumed (review P2, PR #11795):
        # commenting it out leaves `p.mentions` UNSET. ⛔ The replay door's call
        # (`_upsert_point_props`) is NOT free either — do not delete it: for a
        # journal this door wrote it is a no-op (canonical STRING, and the helper
        # no-ops for a `str`), but it is LOAD-BEARING for `EventAPI.add_point`,
        # a live door that journals the payload's LIST spelling and calls no
        # encoder of its own (neutering it leaves that live write storing
        # NOTHING), and it is the only arm that normalises a NON-canonical
        # record. Both are pinned behaviourally, by
        # `tests/test_mention_write_11239.py::test_the_event_api_door_carries_a_mention_to_the_point`
        # and `…::test_the_replay_door_normalises_a_non_canonical_record`.
        # Without it the live CREATE would hand the engine a list of maps and
        # the value would be SILENTLY DROPPED (`_persist_extra_props` skips it)
        # or degraded to `null` by the driver — neither is a refusal, and neither
        # stores anything: the silent-drop class #11239 exists to end. Encoding here
        # ALSO precedes the dedup fold's `self.update_point(pid, **props)`, so
        # that fold forwards the encoded value rather than a list.
        _encode_json_record_props(props)
        # #11239 (review R2 on PR #11795): a CREATE that did not restate the
        # record must not DELETE one. `update_point(mentions=None)` is the
        # documented clear — an addressed edit of an existing claim — but THIS
        # verb is "create": an explicit `mentions=None` reaches the dedup
        # fold's `self.update_point(pid, **props)` below, where a `None` in a
        # generic prop REMOVES the property. That is a silent deletion
        # triggered by a call that only asked to create, and it was measured:
        # `create → create_point(dedup=True, mentions=None)` left
        # `p.mentions` UNSET (the DM1 constraint "no held item silently
        # dropped", breached by the ordinary re-commit path). First-writer is
        # the rule every other prop here follows and the encoder already POPs
        # an empty list for exactly this reason, so a `None` is dropped rather
        # than forwarded.
        if props.get("mentions") is None:
            props.pop("mentions", None)
        # #13110 (review round 3): the SAME rule for the goal's achievement
        # state, and the same measured failure. A CREATE must not DELETE: an
        # explicit ``goalState=None`` reaches the dedup fold's
        # ``self.update_point(pid, **props)`` below, where a `None` in a generic
        # prop REMOVES the property — measured: `create_point(goalState=None,
        # dedup=True)` on an existing ``met`` goal left ``p.goalState`` UNSET,
        # while ``create_object(goalState=None)`` on the Object twin PRESERVED
        # it. ⚠️ WHAT THIS POP COVERS, exactly (round 4 review): the MCP ``props``
        # bag, a direct SDK call, and the nested-``props`` flatten — NOT the MCP
        # ``goalState=`` parameter, which the three tool bodies already guard
        # with ``if goalState is not None`` before it reaches here. The two REST
        # routes guard the same way. So this is the ONE place that covers every
        # channel the guards cannot see; it is not the only place a guard sits,
        # and it is not redundant — removing it re-opens the ``props`` bag and
        # the SDK call (measured: that leg of
        # ``test_no_create_channel_can_erase_a_stored_goal_state`` fails).
        # `update_knowledge(…, goalState=None)` is the documented clear.
        if props.get("goalState") is None:
            props.pop("goalState", None)
        # #10122: a `name` the index does not hold is a stored-but-unfindable
        # identifier — advise here, at the door that accepted it (after the
        # flatten, so the coverage check sees the stored `search_keys` form).
        # ⛔ Advisory only: it never refuses and never touches `props`.
        # E4 (#5007): the span is an ADDRESS — enforce the all-or-nothing /
        # ordered / in-range rule HERE as well as on the `Point` model. These
        # two props ride the generic passthrough, so without this a caller
        # could persist a half span, an inverted pair or a stringly-typed
        # offset — precisely the states `validate_span` declares impossible
        # (#5007 review P2: the invariant was commit-schema-only).
        if "span_start" in props or "span_end" in props:
            from tortoise.commit_schema import validate_span
            validate_span(props.get("span_start"), props.get("span_end"))
        # #5359: the validity window is a CLOSED interval. A caller-supplied
        # inverted pair (`validTo < validFrom`) used to persist verbatim, cover
        # NO instant, and make the point unreachable from every temporal query
        # (`get_historical_knowledge`) while every read reported honest absence —
        # bypassing the refusal #4021 put inside `supersede_point`. Delegate
        # the predicate to the ONE HOME (`commit_schema.
        # validate_validity_window`, shared with `invalidate_point`'s #5358
        # guard and the read path's `_covers`) instead of re-implementing it.
        # No `point_id`: a fresh CREATE has no stored opposite bound to merge.
        # #9202: this call also DECIDES the caller's `validTo` (shape or refuse)
        # and hands back the in-band receipt the reply echoes.
        _end_receipt = _refuse_inverted_point_window(self._get_proj().g, props)
        # #2350 (E-prec): the per-end precision declaration is validated by the
        # same fresh-create rule (no stored opposite to merge).
        _refuse_inconsistent_validity_precision(self._get_proj().g, props)
        # #10122 review P3: the advisory runs AFTER the refusal gates above —
        # a create that these refuse writes nothing, and advising about a write
        # that never happened is crying wolf (the same discipline
        # ``update_entity`` documents for its own door).
        _warn_point_name_not_lexically_indexed(props, door="create_point",
                                               content=content)
        # #3263: provenance is INFERRED from the write context, never demanded.
        # A write that carries a session context has a derivable Source — the
        # same `session:<id>` ref the capture path wires explicitly (#1350).
        # Without this, every session-context write that did not hand-supply
        # `extractedFrom` (the eval ingest paths, session_continuity, ...)
        # landed as an orphan Point, leaving source-tier calibration
        # unsatisfiable BY CONSTRUCTION (issues #3263/#3139). The inferred ref
        # flows through the SAME `extractedFrom` prop path as an explicit one,
        # so it is journaled on PointAdded and replayed by _upsert_point_edges
        # (live == rebuild). An explicit `extractedFrom` always wins.
        #
        # #3263 (review P2): only a non-empty SCALAR session id can name a
        # Source. A non-scalar would stringify into a bogus ref
        # (`session:['s1', 's2']`) and mint a corrupt Source — an invented
        # source is worse than none (design contract #3), so we skip and say so
        # rather than fabricate.
        _session_id = props.get("session_id")
        # #3263 (cycle-4 P2): validate the ref SHAPE before any write. A
        # non-string scalar (123) previously reached _link_source, whose
        # fan-out does list(source_ref) — raising TypeError AFTER the Point was
        # CREATEd but BEFORE PointAdded was journaled, so the graph gained an
        # orphan that a rebuild would drop (live != rebuild). Fail closed here,
        # pre-write, matching the bundle Phase-1 contract.
        if props.get("extractedFrom") is not None:
            # #8171 (review P2): the shape rules that used to be spelled out
            # here now live in ONE place, `_validate_extracted_from`, shared
            # with `create_entity`/`create_document`. Keeping a second copy
            # beside a helper that documents itself as the single home for the
            # contract is how the two silently drift — the helper's whole
            # reason to exist is that the doors routing a ref to
            # `projection._link_source` cannot disagree. Behaviour is
            # unchanged: this door's inline rules WERE that contract's
            # definition (#3263).
            _validate_extracted_from(props, door="create_point")
        if not props.get("extractedFrom"):
            # #3263: branch on TYPE, not truthiness. The contract is "a
            # non-empty string can name a Source", so anything else that is
            # present is a caller error worth surfacing — `0`, `0.0` and
            # `False` are neither strings nor absence, and keying on truthiness
            # silently skipped them (re-review P2). `None` alone means "no
            # session context" and stays silent.
            if isinstance(_session_id, str) and _session_id.strip():
                props["extractedFrom"] = f"session:{_session_id}"
            elif _session_id is not None:
                _logger.warning(
                    "create_point: session_id=%r is not a non-empty string — "
                    "provenance not inferred (pass extractedFrom explicitly); "
                    "refusing to mint an invented Source (#3263)",
                    _session_id,
                )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        proj = self._get_proj()

        # #49 Phase 2: context is REMOVED — raise TypeError if passed
        if "context" in props:
            raise TypeError(
                "create_point() got unexpected keyword argument 'context'. "
                "Context has been removed. Use pointKind for filtering, "
                "anchors for EP scoping, extractedFrom for provenance. See #49."
            )

        if is_episodic is not None:
            props["is_episodic"] = is_episodic  # server-managed (explicit param only)
        # Calibration: pop credibility before storing as node property. The
        # explicit ``credibility`` kwarg binds at the signature; a caller may
        # also reach it through a nested props dict (flattened by _coerce_props
        # above) — the explicit kwarg wins, the pop covers the nested path.
        # #6151 review P3: pop the nested form UNCONDITIONALLY. The pop used to
        # be conditional on the kwarg being absent, so a direct SDK caller
        # passing BOTH forms left `credibility` in `props`; the create path then
        # overwrote the key (`props["credibility"] = canonical_tier(...)`) and
        # hid it, but the DEDUP path forwards `props` to `update_point`, which
        # now HARD-REJECTS `credibility` as a generic property — a ValueError
        # from an input both surfaces document as valid.
        _nested_credibility = props.pop("credibility", None)
        if credibility is None:
            credibility = _nested_credibility
        # #2199: resolve + validate an author-stated belief BEFORE any graph
        # write — a bad ladder word fails loud with nothing created (never a
        # silent Beta(1,1) fallback, #7478; and never an uncalibrated live
        # orphan left behind by a mid-create raise). The resolved Beta is
        # applied below once the point id exists.
        cred_prior: tuple[float, float] | None = None
        if credibility is not None:
            from tortoise.source_credibility import credibility_prior
            cred_prior = credibility_prior(credibility)
            if cred_prior is None:
                raise ValueError(
                    f"Unknown credibility {credibility!r}. Ladder words: "
                    "gold / high / medium / low / unverified "
                    "(or the T0-T4 / numeric forms)."
                )
        # Always compute and store content hash — dedup flag only gates the
        # existing-point lookup, not hash persistence (fix #80).
        ch = _content_hash(content)
        props["content_hash"] = ch
        # Explicit id must be popped BEFORE the dedup branch: the dedup-hit
        # path calls update_point(pid, **props) and a residual 'id' in props
        # crashed with "multiple values for argument 'id'" (review fix, PR
        # #953 — the commit endpoint writes deterministic pt_<sha> ids with
        # dedup=True).
        explicit_id = props.pop("id", None)
        # Idempotency guard: dedup by content hash when requested
        dedup = props.pop("dedup", False)
        # #1088 / #6107: LIVE is the default for everything added to the
        # graph; an agent that deliberately asks for draft gets draft, and it
        # is honoured. "Was status given?" is decided BEFORE the pop so an
        # explicit status (the capture/extraction paths pass status="draft" +
        # extractedFrom, or an agent staging a draft deliberately) is explicit
        # BY CONSTRUCTION and keeps full manual control — with ONE scoped
        # exception: a point the command itself MINTS. The capture pass
        # promotes the drafts IT created (``promotion_ids``), never a folded
        # or pre-existing draft (#1088), so "explicit draft is never moved
        # live" is true of a point this write did NOT mint, not of a capture's
        # own extracted claims. Precedent: #2199 did exactly this for the
        # decide-part kinds.
        #
        # OVERRIDES: the fail-closed create posture of #344/#1212 (a
        # non-decide kind defaulted to status='draft', so a plainly-created
        # point was born EP-inert) — `live` is the default for everything
        # added to the graph, and `draft` is the deliberate caller-chosen
        # exception instead. Owner ruling 2026-10-09, recorded with its
        # OVERRIDES marker on #6107. Reason: the fail-closed protection and
        # the invisibility were the SAME mechanism — a goal created plainly
        # and wired only as an operator TARGET was never promoted, so EP
        # discarded the whole arm while the run still reported converged=True.
        # ⛔ #13143 review (F1, P1): the renderer UNION, never the literal name —
        # a `str` subclass whose content and `__str__` differ is written as
        # `status` by the transport while a literal-name test misses it, so the
        # word went UNGRADED. Which failure that produced is engine-specific and
        # is recorded in full in :func:`_rehome_rendered_status_key`'s docstring
        # (graph lane: the caller's key duplicates the inline CREATE map's own
        # `status` field and the driver refuses the statement; embedded: the
        # caller's key won last-wins and the ungraded word read as CURRENT). The
        # helper also refuses a two-key bag this door would otherwise answer
        # differently from `create_object`.
        explicit_status = _rehome_rendered_status_key(
            props, door="create_point", label="a Point")
        # Status is popped+validated BEFORE the dedup branch (#1905): a dedup
        # hit must never forward the caller's status into update_point (which
        # rejects any non-'live' status — a gated re-ingest of the same draft
        # item raised a raw ValueError mid-batch, stranding earlier bundle
        # items as committed). Popping up front also keeps vocabulary
        # validation uniform for both paths: an invalid status raises even
        # when the point already exists (no silent-ignore asymmetry).
        # Merge of main's #7856 (goals born live) with #1088 (LIVE is the
        # default for EVERY kind): the universal default below subsumes
        # main's GOAL_KINDS rule — a goal is still born live, for exactly the
        # reason main gives (a draft goal that is only ever a TARGET is
        # EP-inert while converged=True is still reported), and so is every
        # other kind. An explicit status is honoured by the pop below.
        status = props.pop("status", "live")
        # Fail-closed vocabulary validation (mirrors update_point): the FOUR-word
        # lifecycle vocabulary is read through `lifecycle_vocabulary`,
        # the ONE home (#11824 S0) — so a re-spelling (`'LIVE'`, `' Live '`) is
        # normalised silently, a declared synonym (`archived` / `retracted`) is
        # stored as `deprecated`, and `outdated` or a position word
        # (`in_progress` / `completed` / `active` / `paused`) is REFUSED with a
        # performable repair instead of being stored verbatim.
        #
        # The hole this closes is measured: a non-canonical status used to be
        # stored and then treated as EP-LIVE by `_live_only` (which excludes
        # 'draft' and the terminal statuses/flag — #2422), so junk outside the
        # vocabulary was the residual EP-live surface (PR #1073 review P0/P1).
        # The door now also raises for unhashable values (list/dict), because the
        # guard's own first arm is a type test.
        status = _read_caller_status(status, kind=":Point")
        # #2351 (epic #2349 E-derived): the claim class. Validated HERE, before
        # any graph write — an unknown class fails loud with nothing created
        # (the same fail-closed posture as the status vocabulary above and the
        # #2199 credibility word). The nested ``claimClass`` prop form is POPPED
        # and validated exactly like ``credibility`` (a generic props passthrough
        # must not be able to persist an unvalidated third class), with the
        # explicit kwarg winning when both are given.
        #
        # It is deliberately kept OUT of ``props``: it is a property of the
        # write that MINTS the claim, so it rides the CREATE map below and
        # never the dedup-hit ``update_point`` forward — a re-run must not
        # re-stamp an existing claim's provenance in either direction.
        _nested_claim_class = props.pop(_claim_class.PROP, None)
        _claim_class_value = _claim_class.validate(
            claim_class if claim_class is not None else _nested_claim_class)
        if dedup:
            # P1 #49: dedup by content_hash + pointKind (NOT context, which is
            # no longer written). The A10 hash-less content+kind fallback now
            # rides the shared ``_find_point_by_content`` helper (#2892) so
            # this lookup, the v2 capture seam, and ``_content_exists`` can
            # never drift apart again — the fork is exactly what let the
            # fallback go missing from ``_content_exists``.
            #
            # #6110: a caller-supplied ``key`` is an IDENTITY, not a label. The
            # shared resolver treats a point already holding that ``key``
            # (within this pointKind) as the dedup hit WHATEVER its content, so
            # a re-run under the same key FOLDS instead of silently minting a
            # second point that shares the key. The writer and the ingest dedup
            # counter both call `_resolve_dedup_hit`, so a key-bearing item
            # cannot report a miss the writer takes as a hit.
            _supplied_key = props.get("key")
            _has_key = (isinstance(_supplied_key, str)
                        and bool(_supplied_key.strip()))
            existing_id, _key_hits = self._resolve_dedup_hit(
                content, pointKind=kind,
                key=_supplied_key if _has_key else None)
            if _key_hits and len(_key_hits) > 1:
                # A graph written before #6110 can already hold several points
                # under one key. Fold onto the first (deterministic query
                # order) but SAY SO — a silent fold is exactly how the
                # duplicated-key state stayed invisible.
                _logger.warning(
                    "create_point: %d points already share key %r (kind=%r): "
                    "%r — a pre-existing duplicate the write path cannot "
                    "repair; returning %s. Re-key the extras or delete them "
                    "(#6110)",
                    len(_key_hits), _supplied_key, kind, _key_hits, _key_hits[0])
            if existing_id:
                pid = existing_id
                # ── #7819 + #10368: a dedup hit must not WRITE onto a dead claim
                # The check sits at the TOP of this branch because the fold has
                # two write legs and only one of them is `update_point`: the
                # authored-word leg (`set_point_baseline` +
                # `_stamp_authored_word`) writes server-owned baseline props
                # directly.
                #
                # #7819 refused the TEMPORAL fold (a window / `expiredAt` /
                # `when`): re-stamping it changes what the dead claim asserts
                # about WHEN it held, which the temporal read surfaces answer
                # from. #10368 closed the residual that left open: a fold
                # carrying a GENERIC prop passed straight to the write leg and
                # rewrote a terminal node — an UNADDRESSED write on the content
                # leg (the caller asked to CREATE; a content coincidence picked
                # the target), so it changed the record of a settled belief
                # without ever naming it. The refusal covers BOTH resolution
                # routes: a `key`-resolved hit is a write onto a dead claim too
                # (the key decides IDENTITY, #6110 — it is not an instruction to
                # rewrite a settled record, and ingest's own #10377 guard already
                # refuses the same fold). Both refusals are all-or-nothing for the CALLER'S writes
                # — no prop and no authored word is applied (the #7704
                # provenance retrofit below is explicitly NOT one of them) —
                # the dedup HIT is still reported, and the drop rides the RESULT
                # as an advisory (`temporal_not_applied` / `terminal_fold_refused`)
                # rather than only the log.
                #
                # ⛔ WHAT IS *NOT* REFUSED, so this does not reverse #3305: an
                # ADDRESSED revision — `update_point(<id>, <generic prop>)` —
                # still applies to a terminal claim. That is the recorded
                # post-terminalization revision capability (a `PointRevised`
                # later than the terminalization is the newer same-id writer and
                # replay folds it by `skip_updated_at` via `max_inline_seq`,
                # pinned by `test_pointinvalidated_rebuild.py::test_revise_
                # after_invalidate_skip_updated_at` and `test_pointsuperseded_
                # rebuild.py::test_supersede_then_revision_does_not_end_at_the_
                # supersede_ts`). #10368 objects to this fold's IDENTITY
                # RESOLUTION — a content match is not a caller naming the point —
                # never to a caller who does address it.
                #
                # #7704's journaled provenance retrofit ALSO still folds onto a
                # terminal point (OWNER RULING 2026-10-25: "a dedup hit KEEPS
                # the new evidence" — one claim, N sources): evidence ABOUT a
                # claim is not a rewrite of what the claim says. It runs by
                # symbol below and is deliberately left alone.
                #
                # Why not "just let the guard raise": the hosted commit path
                # re-keys a successor onto an already-dead node when the
                # successor's content address exists as one, and a committed
                # `when` puts a window on that fold —
                # `hosted_api._execute_commit_writes` sets `validFrom` from
                # `pr.point.when` (cited by symbol: this branch's own census file
                # forbids `file:line` pointers, which drift) — so a raise
                # there is the deterministic fail-closed 500 #3366 exists to
                # prevent.
                #
                # A dropped fold WARNS **and ADVISES** (#5365's posture, never
                # a silent skip): the caller keeps the honest outcome — the
                # dedup HIT is still reported and the point is still terminal —
                # and is told the window was not written, in the RESULT (the
                # `temporal_not_applied` advisory, which the hosted commit
                # receipt folds into `warnings`) as well as the log. The
                # operation for "replace what this
                # claim said, from a different time" is the retroactive
                # correction, which is DESIGN ONLY and not shipped in this tree
                # (`supersede_retroactively` has no SDK method and no registry
                # row; #5362 waits on surface approval).
                # ── #10368: ONE terminal read serves every leg of this fold ──
                # `_lifecycle_terminal_marker` is the shared verdict over the
                # id's whole fan-out (#7822 — one statement, one classifier),
                # and the #10368 refusal needs the same verdict the #7819 one
                # does. Read it ONCE, above both, instead of adding a second
                # read path (#2892). Skipped when the call carries neither props
                # nor an authored word (a bare dedup hit), so that path costs no
                # extra query.
                #
                # ⛔ THE GATE IS WHAT THE FOLD COULD **WRITE**, not the `props`
                # dict as it happens to sit on this line. `content_hash` was put
                # into `props` UNCONDITIONALLY at the top of this door and is not
                # popped until after the #7704 provenance retrofit below, and
                # `extractedFrom` is that retrofit (deliberately NOT refused) —
                # so `if props` was ALWAYS true here, the "bare dedup hit costs
                # no extra query" arm was unreachable, and every dedup hit spent
                # a fan-out read on the NORMAL path ("the self-host surface
                # dedups by DEFAULT", see the #10368 note above).
                #
                # ⚠️ THIS DEFECT WAS FIXED TWICE, INDEPENDENTLY: `origin/main`
                # gates on `_caller_props = set(props) - {"content_hash"}` (#10841
                # review, P3) and this fold gates on `_fold_words`, one name
                # narrower. Both are the same repair; the difference is the
                # DIRECTION of the subset: `_fold_words` is a SUBSET of
                # `_caller_props`, so every hit that takes the read HERE would
                # also have taken it under main's gate, and the evidence-only hit
                # (an `extractedFrom` retrofit alone) is the only case that
                # differs — main reads a verdict there, this gate does NOT, and
                # there is nothing to decide: that hit writes no prop and no word.
                #
                # `_fold_words` is the PRE-POP image of what
                # `_terminal_fold_refused` reads after its own pops, so the two
                # can never disagree: `_TEMPORAL_RESTAMP_PROPS` shares no member
                # with the two excluded names, so `_temporal ⊆ _fold_words` and
                # the #7819 arm keeps its verdict; `key` stays in `_fold_words`,
                # so a KEY-resolved fold is still governed (#6110); and an
                # empty `_fold_words` with no authored word leaves the props leg
                # with nothing to write, so there is no refusal to decide.
                # Pinned by BOTH tests the two fixes brought, which is why the
                # merge keeps both suites' cases:
                # `tests/test_10368_terminal_create_fold.py::
                # test_a_bare_dedup_hit_costs_no_terminal_read` (this fold) and
                # `…::test_a_bare_dedup_hit_spends_no_terminal_read` (main).
                _fold_words = set(props) - {"content_hash", "extractedFrom"}
                _terminal = (self._lifecycle_terminal_marker(pid)
                             if (_fold_words or cred_prior is not None)
                             else None)
                _temporal = _TEMPORAL_RESTAMP_PROPS & set(props)
                if _temporal and _terminal is not None:
                    _logger.warning(
                        "create_point: dedup resolved to point %s, which "
                        "is already terminal (%s) — the fold would "
                        "re-stamp its temporal record (%s), so NOTHING "
                        "was applied (#7819).",
                        pid, _terminal, sorted(_temporal))
                    # ...and SAY it in the RESULT, not only in the log: a
                    # warning is not observable to a hosted commit caller
                    # or to an SDK caller reading the return value. The
                    # advisory is transient (#8171) — it rides a COPY, so
                    # neither `get_point` nor the journal ever holds it.
                    # ⚠️ BOTH advisories ride the one `advisories` key, so the
                    # merge is `_merge_advisories` (a second `**` splat would
                    # REPLACE the list and drop the kind↔layer note — the
                    # defect measured at the #9964 review: the re-run of this
                    # very call returned `temporal_not_applied` ONLY, while
                    # its first run reported `kind_other_layer` too).
                    # ⛔ AND NO END RECEIPT (#10079 review P2). Shaping was
                    # COMPUTED from the caller's props at the top of this
                    # door, but this branch applies NOTHING — the fold that
                    # would have stored the shaped end was refused by
                    # #7819 just above. `valid_to_shaped` is defined as the
                    # receipt of an end that WAS shaped INTO THE STORED
                    # record (config/surface-manifest.yml), so emitting it
                    # here told the caller their end was now the canonical
                    # un-datable one while the stored window still ended on
                    # its original date and read `known` — a receipt that
                    # reports an action that did not happen is the exact
                    # betrayal the ruling's §0 orders refused (#9202).
                    # `temporal_not_applied` is the honest signal here, and
                    # it rides this same reply.
                    return _with_end_receipt({
                        **self.get_point(pid),
                        **_merge_advisories(
                            self._temporal_not_applied_advisory(pid),
                            _kind_layer_advisory(_layer_note, pid)),
                    }, None)
                if _has_key:
                    # One read serves BOTH #6110 audits below.
                    _existing = self.get_point(pid)
                    if _key_hits:
                        # The KEY resolved the dedup. If the content differs, the
                        # incoming content is NOT stored (the key is the
                        # identity, and content is first-writer-wins, like the
                        # calibration baseline, #7980) — say so, or the
                        # collision stays silent and the caller believes their
                        # content landed.
                        if _existing.get("content") != content:
                            _logger.warning(
                                "create_point: key %r is already held by point "
                                "%s with DIFFERENT content — folding onto it "
                                "and keeping the existing content. The key is "
                                "the identity; pass a fresh key with "
                                "dedup=False to mint a second point (#6110)",
                                _supplied_key, pid)
                        # The supplied spelling can differ from the one this
                        # point was MINTED with, in BOTH directions — and only
                        # one of them is #10481's:
                        #   * PADDED supplied → TRIMMED holder is #10481's new
                        #     reach (the seek is trim-aware now, so "  PK  "
                        #     hits a holder of "PK");
                        #   * TRIMMED supplied → PADDED holder was ALREADY
                        #     reachable pre-fix, because the raw term matched
                        #     the engine's trimmed index entry (#10481's own
                        #     measurement). That half closes a PRE-EXISTING
                        #     silent reassignment, not a #10481 consequence.
                        # Either way the key IS the identity and the
                        # FIRST-WRITER spelling is what the graph holds (#6110:
                        # a dedup hit never silently reassigns an existing
                        # key) — so the incoming spelling is NOT applied.
                        # Relaying it would rewrite the stored value on a pure
                        # dedup hit, bump ``updatedAt`` on every alternating
                        # re-run (#490 churn), and be LIVE-ONLY:
                        # ``PointRevised`` folds no ``key`` arm, so a rebuild
                        # reverts the spelling (#9732).
                        _existing_key = _existing.get("key")
                        if (isinstance(_existing_key, str) and _existing_key
                                and _existing_key != _supplied_key):
                            props.pop("key", None)
                            # …and SAY it (#10481 review, P3): the CONTENT-hit
                            # branch below already warns when it refuses to
                            # reassign a key, so a caller whose spelling was not
                            # applied is told there. Popping silently here made
                            # the same refusal invisible on the KEY-hit path —
                            # the caller writes `"  PK  "`, gets a correct fold,
                            # and never learns the graph kept `"PK"`. Same
                            # posture as #6110's "say so, or the collision stays
                            # silent", and for the same reason: the spelling the
                            # graph holds is a fact about THEIR identity.
                            _logger.warning(
                                "create_point: key %r not applied — the key "
                                "resolved point %s, which already holds key %r. "
                                "A dedup hit never reassigns an existing key "
                                "(#6110 first-writer-wins); the stored spelling "
                                "is kept verbatim",
                                _supplied_key, pid, _existing_key)
                    else:
                        # The hit came from CONTENT, not from the key: never
                        # silently REASSIGN an existing point's key. A key
                        # already held by a point is that point's identity —
                        # the same first-writer-wins posture the calibration
                        # baseline carries (#7980). Overwriting it would make
                        # `query(key=…)` return a point its original owner named
                        # and lose the first key.
                        _existing_key = _existing.get("key")
                        if (isinstance(_existing_key, str) and _existing_key
                                and _existing_key != _supplied_key):
                            props.pop("key", None)
                            _logger.warning(
                                "create_point: key %r not applied — content "
                                "matched point %s, which already holds key %r. "
                                "A dedup hit never reassigns an existing key; "
                                "pass a fresh key with dedup=False, or update "
                                "the point explicitly (#6110)",
                                _supplied_key, pid, _existing_key)
                # Existing point already stores content_hash — don't re-write it
                # (would make the `if props:` guard always truthy and bump
                # updatedAt on every dedup hit, #80 review).
                props.pop("content_hash", None)
                # ── #2782 (review #11836 Finding 1): a CREATE never DELETES. ──
                # An explicit ``None`` for a money field is a NO-OP on the create
                # leg — the extra-props passthrough drops ``None`` values
                # (``projection/entities.py::_persist_extra_props``) — but this
                # fold forwards the caller's bag to ``update_point``, whose write
                # is a bare ``SET n += $props`` where the same ``None`` REMOVES
                # the property. Forwarding it made the retry of an idempotent
                # create (a) RAISE for ``amountMinor``/``currency``/
                # ``minorUnitExponent`` (the update door refuses a null-delete)
                # and (b) silently DELETE the field for the sibling spellings the
                # update door leaves alone — either way a behaviour the first run
                # never had. Dropped here rather than refused, so the retry
                # neither raises nor deletes: that is what "idempotent creation"
                # means for this verb. Same class, same remedy as the
                # ``mentions=None`` pop above ("a CREATE must not DELETE").
                for _money_field in MONEY_VALUE_FIELDS:
                    if _money_field in props and props[_money_field] is None:
                        props.pop(_money_field, None)
                # ── #7704 (OWNER RULING 2026-10-25): a dedup hit KEEPS the new
                # evidence. The owner ruled the W4-b "twin" case a DUPLICATION
                # defect — "The dedup is the key here to solve the problem you
                # had raised" — with the intended state "one claim, N sources"
                # (docs/ONTOLOGY.md, `extractedFrom` many→many, #3263).
                #
                # `extractedFrom` must NOT ride the `update_point` prop path
                # below: that writer sets the node property but never calls
                # `_link_source`, so the Point would carry `extractedFrom` with
                # no `:Source` edge — a provenance `list_drafts` reports and the
                # graph does not hold. It is not replayed either, so live and
                # rebuild would disagree.
                #
                # It also must NOT be dropped, which is what this branch did
                # until #7704: it popped the ref and WARNED, because
                # "retrofitting provenance onto an existing Point needs a
                # journaled path, which does not exist yet (#3292)". #3292
                # closed on its first-write INFERENCE half (#3295) and left that
                # residual unowned, so every re-write that carried a NEW source
                # lost it — the duplicate's evidence vanished into the fold,
                # which is exactly the failure the ruling sends us to fix.
                #
                # `_fold_source_evidence` IS that journaled path (see its
                # docstring): it wires the missing refs, writes the accumulated
                # property, and appends the `PointAdded` record that makes the
                # state replayable. Decide per REF, not per call — the refs
                # already held are a no-op, so an idempotent re-write neither
                # writes nor journals (#3263 re-review P2).
                _incoming_ef = props.pop("extractedFrom", None)
                if _incoming_ef is not None:
                    # Normalise to the supplied non-empty refs: an explicit
                    # ``[]`` means "no provenance" (the bundle contract), and a
                    # scalar is ONE ref, never iterated character-wise.
                    _supplied = (
                        [_incoming_ef] if isinstance(_incoming_ef, str)
                        else [r for r in _incoming_ef if r]
                    )
                    if _supplied:
                        # The EDGES are authoritative (#3263): read them, not
                        # the convenience prop, so the fold is measured against
                        # the provenance the graph actually holds. Ordered, so
                        # the accumulated property is byte-stable across runs.
                        _present = [
                            row[0] for row in proj.g.query(
                                "MATCH (p:Point {id:$id})-[:extractedFrom]->"
                                "(s:Source) RETURN DISTINCT s.url ORDER BY s.url",
                                params={"id": pid}).result_set
                        ]
                        # `_present` holds the RESOLVED `s.url` of each
                        # `:Source` the edges address, while `_supplied` is the
                        # caller's RAW spelling. Comparing them directly makes a
                        # URL VARIANT of a ref the Point already holds look
                        # permanently absent — so the fold re-journals on every
                        # idempotent retry and writes a prop ref that has no
                        # Source and no edge (S0b/#5012: `resolve_source_key` is
                        # the ONE resolver every source writer uses to map a raw
                        # spelling onto the node the write will address, and
                        # `_mint_source_stub` — i.e. `_link_source` — routes
                        # through it). Resolve here so "present" is decided on
                        # the identity the edge will address. It does not MINT
                        # (an unregistered ref resolves to itself) and its only
                        # write is the declared derived-recompute adopt-on-touch
                        # — the same pre-append control-plane write the CREATE
                        # path already makes via `resolve_source_versions`. The
                        # RAW spelling is what stays in the ref lists: it is the
                        # journal-stable key `sourceVersionTransit` is keyed by
                        # (`resolve_source_versions`), never a live-time
                        # resolution.
                        from .source_identity import resolve_source_key
                        _present_set = set(_present)
                        _missing: list[str] = []
                        _missing_keys: set[str] = set()
                        for _ref in _supplied:
                            _key = resolve_source_key(proj.g, _ref)
                            if _key in _present_set or _key in _missing_keys:
                                continue
                            _missing_keys.add(_key)
                            _missing.append(_ref)
                        if _missing:
                            self._fold_source_evidence(
                                pid, _present, _missing)
                # #7980 + #398: an authored ``credibility`` is a WRITE, never a
                # silent drop, and it obeys the SAME rule as the create path —
                # ONE weak evidence body (the declared word is a validated
                # LABEL, not the claim's strength), added ON TOP OF whatever
                # baseline the claim already legitimately has.
                #
                # The persisted `credibility` label is what marks this baseline
                # as an AUTHORED ASSERTION: `_apply_source_inheritance` then
                # recomputes it, so a real source attached later displaces the
                # weak body through ordinary aggregation. An EXPLICIT
                # `set_point_baseline` baseline with no such label stays
                # IMMUTABLE evidence (#844): a dedup hit — which matches by
                # CONTENT HASH, and so may be an unrelated writer stating a
                # different belief — never rewrites it, and SAYS SO (the
                # self-host surface dedups by DEFAULT, which makes the
                # same-word retry the NORMAL path, so that one stays quiet). An
                # INHERITED (`inherited-from-source`) baseline is DERIVED, not
                # explicit, so the word is ADDED on top of it (#6151 review
                # P2).
                _word_applied = False
                # ── #10368: the authored word is NOT written onto a dead claim
                # This leg (server-owned baseline + label) runs BEFORE the props
                # leg, so gating only `update_point` would leak it — the same
                # "all-or-nothing" reason the #7819 comment above states. The
                # drop is reported by the one `terminal_fold_refused` advisory
                # below, not by a second code.
                if cred_prior is not None and _terminal is None:
                    from tortoise.source_credibility import (
                        AUTHOR_ASSERTION_WEAK_PC,
                        canonical_tier,
                    )
                    _existing = self.get_point(pid)
                    _word_label = canonical_tier(credibility)
                    _existing_label = _existing.get("credibility")
                    _bl_src = _existing.get("baseline_source")
                    _apply_word = False
                    _record_default = False
                    _base_a, _base_b = 1.0, 1.0
                    _drop_note = None
                    if _existing_label and _existing.get("baseline_set"):
                        # Already an authored assertion WITH its recorded
                        # baseline — FIRST-WRITER-WINS. A retry stating the SAME
                        # word loses nothing; a DIFFERENT word is dropped and
                        # must be audible.
                        #
                        # #6151 review P2(b): the label ALONE is NOT proof the
                        # baseline survived. `credibility` is a DECLARED Point
                        # prop and replays (`_POINT_DECLARED_PROPS`), while
                        # `ep_alpha`/`baseline_set`/`baseline_source` sit in
                        # `_POINT_DENY` and do NOT — so after a `rebuild_all` a
                        # word-bearing point holds the label with NO baseline.
                        # Keying first-writer-wins on the label alone made the
                        # dedup retry a silent no-op there (no `_drop_note`, no
                        # `_word_applied`): the point stayed uncalibrated
                        # forever and `compute_confidence(require_calibration=
                        # True)` raised. The baseline must actually be present
                        # for the claim to be "already" written; otherwise fall
                        # through to the apply branch below, which is the
                        # documented healing write (main re-baselined this
                        # shape, because `credibility` was not persisted there).
                        if _existing_label != _word_label:
                            _drop_note = (
                                "already carries the authored assertion "
                                f"{_existing_label!r}")
                    elif not _existing.get("baseline_set"):
                        # No baseline recorded — and `baseline_set` false is PROOF
                        # the #2199 default was never granted (#6151 review P2):
                        # the ONLY shape #2199 ever provisions is a decide part
                        # BORN live, and that part always carries
                        # `baseline_set=true` from its create
                        # (`_baseline_create_fields`, :187). So this branch is an
                        # explicit-STATUS decide part or a pre-#2199 row, and the
                        # default must NOT be granted from `kind`/`explicit_status`
                        # HERE — that is the current CALL's context, not the
                        # existing point's birth. Measured before the fix: a
                        # born-draft option dedup-hit with credibility=gold landed
                        # at 3.1/0.7561 (an unearned +2.0 alpha); after, the word
                        # is its own weak body on neutral -> 1.1/0.5238.
                        #
                        # This branch ALSO carries the post-rebuild healing write
                        # (label present, baseline gone) described above: the
                        # #2199 birth marker DOES replay (it rides the original
                        # `PointAdded` snapshot), so re-assert it here — a
                        # born-live decide part must keep its recorded provision
                        # and the next recompute re-injects the +2.0 on top of
                        # the word's weak body.
                        _record_default = bool(
                            _existing.get("baseline_system_default"))
                        _apply_word = True
                    elif _existing.get("baseline_system_default"):
                        # The #2199 provision is this claim's recorded base; the
                        # author's word ADDS the weak body ON TOP of it and
                        # never deletes it (#398: a labelled claim is never
                        # weaker than the same claim unlabelled). The marker is
                        # the ONLY legitimate signal: it is written by the birth
                        # path (`create_point`) and the mitigation grant — never
                        # inferred from the caller-supplied `baseline_source`
                        # token (#6151 review P1(b)). The token is writeable
                        # through `set_point_baseline(source=…)`, so trusting it
                        # let a caller stamp the marker and collect the +2.0
                        # default unearned (measured 1.2 -> 3.2).
                        _base_a = _existing.get("ep_alpha") or 1.0
                        _base_b = _existing.get("ep_beta") or 1.0
                        _record_default = True
                        _apply_word = True
                    elif _bl_src == BASELINE_SOURCE_INHERITED:
                        # #6151 review P2: an `inherited-from-source` baseline
                        # is DERIVED evidence, not an authored belief — the word
                        # is one more weak body ON TOP of it, exactly as on the
                        # other dedup cases. This shape used to fall to the
                        # drop below, so a documented word produced no body and
                        # no label (measured: an inherited 5.0 stayed 5.0 with
                        # `credibility` None). The next recompute re-derives it
                        # from the sources PLUS the word.
                        _base_a = _existing.get("ep_alpha") or 1.0
                        _base_b = _existing.get("ep_beta") or 1.0
                        _apply_word = True
                    else:
                        _drop_note = (
                            f"already exists with a {_bl_src!r} baseline")
                    if _apply_word:
                        self.set_point_baseline(
                            pid, _base_a + AUTHOR_ASSERTION_WEAK_PC,
                            _base_b, source=BASELINE_SOURCE_SET_BY_AUTHOR)
                        # #7951: the word LABEL and the #2199 birth marker are
                        # written through the server-owned internal route, NOT
                        # the generic props bag — `_sanitize_props` rejects a
                        # caller-supplied `credibility` on those surfaces, so
                        # this is the one legitimate writer here.
                        self._stamp_authored_word(
                            pid, label=_word_label,
                            system_default=(True if _record_default else None))
                        _word_applied = True
                    if _drop_note is not None:
                        _logger.warning(
                            "credibility=%r not applied — point %s %s, and a "
                            "dedup hit never overwrites the baseline a point "
                            "already carries (#7980, #844)",
                            credibility, pid, _drop_note)
                # ── #10368: the fold's props leg is refused on a dead claim ──
                # Computed HERE, after the pops above, so the flag states
                # exactly what the old write leg would have applied. Two of
                # those pops are NOT this guard's business: `content_hash` is
                # never re-written (#80 review) and `extractedFrom` is the
                # #7704 provenance fold, deliberately left applying. `key` MAY
                # still be in `props` here — on a KEY-resolved hit the identity
                # write is governed like any other, because a dedup HIT never
                # rewrites a settled record (#10368).
                _terminal_fold_refused = bool(
                    _terminal is not None
                    and (props or cred_prior is not None))
                if _terminal_fold_refused:
                    _logger.warning(
                        "create_point: dedup resolved to point %s, which is "
                        "already terminal (%s) — a dedup hit of either kind "
                        "never writes onto a settled claim (#10368), so the "
                        "CALLER'S props and authored word were NOT applied. "
                        "Revise it deliberately with "
                        "update_point(%r, ...), the addressed door.",
                        pid, _terminal, pid)
                if (props and not _terminal_fold_refused) or _word_applied:
                    # Only touch the existing point when the caller passed
                    # other props (or an applied word) — a pure dedup hit (no
                    # props) must not bump updatedAt or re-trigger EP
                    # dirty-marking (#490 review P2-1: re-capture would churn
                    # confidence for every point).
                    props["updatedAt"] = now
                    # #6140 / #9202: the CREATE leg of this call already warned
                    # about an unorderable caller `validFrom` / `validTo` if it
                    # had one, and this update leg writes the same value — so
                    # suppress the repeat for the duration of THIS write only.
                    _vf = props.get("validFrom")
                    _vt = props.get("validTo")
                    _tok = _WARN_SUPPRESS.set(
                        frozenset({repr(_vf), repr(_vt)})
                        if (_vf is not None or _vt is not None) else None)
                    # #2782: the money grade needs no flag here — this re-entry
                    # is graded on the merged state like any addressed edit, so a
                    # lone sibling on a node with no value stays the no-op the
                    # create leg gave it (idempotency) and a lone sibling on a
                    # MONEY node is refused as the mis-scale it would be
                    # (tortoise#11863). `None` money members were dropped above,
                    # because a CREATE never deletes.
                    try:
                        self.update_point(pid, **props)
                    finally:
                        _WARN_SUPPRESS.reset(_tok)
                return _with_end_receipt(
                    {**self.get_point(pid),
                     # `_merge_advisories`, never a second `**` splat: BOTH the
                     # kind↔layer note and the #10368 refusal ride the one
                     # `advisories` key, and a splat would REPLACE the list and
                     # drop one (#9964).
                     **_merge_advisories(
                         _kind_layer_advisory(_layer_note, pid),
                         (self._terminal_fold_refused_advisory(pid)
                          if _terminal_fold_refused else {}))},
                    _end_receipt)

        # #13035 (review P1, PR #13065): THIS door can be an UPDATE in disguise
        # when the caller supplies an `id`, so it grades the state it LEAVES the
        # node in — the #2782 posture the sibling gates already apply.
        #
        # `create_point` preserves a caller-supplied `id` for operators
        # (`explicit_id` above) and the statement it issues is a plain `CREATE`,
        # so a colliding id mints a SECOND `:Point` and the journal's
        # `PointAdded` is folded back onto the ORIGINAL node by replay's
        # `_upsert_point_props` (`projection/entities.py`) MERGE — the
        # defect class this repo already recorded for the entity fold
        # (`_update_entity`'s #9649/#10192 note: "the fold behind it MERGEs, so
        # an ON-MATCH re-create reaches an EXISTING node too — a guard on
        # `update_entity` alone is bypassable in one call"). Without the probe,
        # `create_point(..., id=<goal>, status="archived")` leaves
        # `status="archived"` beside a stored `goalState="met"`: exactly the
        # pair BOTH update doors refuse, in the one direction that silences
        # `derived_invalidation`'s retirement arm while every other reader serves
        # the goal as ended.
        #
        # ⛔ Graded on `{**props, "status": status}`, not on the bag, because the
        # CREATE always LANDS a `status` (the default is `"live"`, popped above).
        # A bag naming only `goalState` therefore leaves a pair the bag never
        # spelled, and asking the stored side about the ABSENT status would
        # grade a state the write never leaves.
        #
        # ⛔⚠️ REVIEW ROUND 3, FINDING 4 SUPERSEDED THE COST NOTE THAT USED TO SIT
        # HERE ("with the resolved status in the bag the probe is not even read
        # for it"). It IS read on this leg, for EVERY explicit-id create,
        # because the pair alone cannot answer whether the node it lands on is
        # ALREADY ENDED — and an ending is final
        # (`create_point(id=<ended goal>, goalState="active")` used to write a
        # coherent LIVE pair over the ending and is now REFUSED). A genuinely
        # fresh explicit id costs one read that answers `{}`, and an id-less
        # create pays nothing (there is no node it could reach).
        #
        # ⛔ PLACED AFTER the dedup block, and that placement is load-bearing: a
        # dedup HIT does not land `status` (it is popped before the fold, #1905)
        # and forwards `props` to `update_point`, whose own guard grades THAT
        # merged state — so grading the resolved `status` there would refuse a
        # write that never writes it. Past the block, a status provably lands.
        #
        # ⛔ `may_target_existing_node=True` (review P2, #13065): the resolved
        # status is a STATEMENT, not a no-op — the CREATE always writes one — so
        # the stored `goalState` is part of the state this write LEAVES for a
        # NON-terminal status too. Without the flag the guard only asked the
        # store when the status it resolved was terminal, and
        # `create_point(id=<retired goal>)` with NO `status` (resolved `"live"`)
        # folded `status="live"` beside a stored `goalState="abandoned"` — the
        # exact pair `test_no_WRITABLE_pair_leaves_the_two_surfaces_disagreeing`
        # asserts cannot exist, through the one door that test does not mint
        # from. A genuinely fresh explicit id still answers `{}` and refuses
        # nothing.
        if explicit_id is not None:
            _refuse_inconsistent_goal_ending(
                {**props, "status": status},
                surface="create_point",
                goal_ending=lambda: _stored_goal_ending_view(
                    proj, explicit_id, label="Point"),
                may_target_existing_node=True,
            )

        # Issue #52 — warn when caller passes an explicit non-ULID id
        if explicit_id is not None:
            if not _is_ulid(explicit_id):
                _logger.warning(
                    "create_point received non-ULID id=%r — canonical format is "
                    "<timestamp-hex>-<uuid12>. This will override the auto-generated ULID. "
                    "Prefer omitting 'id' to use auto-generated ULID.",
                    explicit_id,
                )
            pid = explicit_id
        else:
            pid = ulid()

        # ── #11739: the DETERMINISTIC composed embedding text (gate OFF) ──
        # DELIVERED HERE: the `quote` VERBATIM — the vector half of #11682 that
        # #11706's lexical change did not take, and the half a created point
        # actually has (see the neighbour note below). The names half
        # (`embed_compose`'s ordering / fan-out ceiling / word budget /
        # redaction) is built and tested but NOT wired at this door; it attaches
        # at the recompose seam, once the point's `about*` edges exist.
        # Composed here because this is the seam the door mints a Point's vector
        # through (`encode_for_row` below, #5008's gated submission step). ⛔ The boundary is stated in
        # `embed_compose`'s decision 5 and is not "one seam in the tree":
        # `update_point` is a second `:Point` mint door that does NOT recompose
        # (composition is CREATE-only, so a `quote`/`about*` revise leaves the
        # existing composed text and vector in place — the disclosed
        # stale-composition residual), and `api.EventAPI.add_point` mints from
        # raw `content` and is not routed through here at all.
        #
        # The composed string is written to `embeddingText` — the schema A′
        # field (#8932 §1) `stamp_journal_embedding` ALREADY attests — so
        # #4194's rule ("the vector can never describe different text than the
        # node holds") stays true as written, with no new attestation path: the
        # CREATE map carries the text as an ordinary property,
        # `_journal_snapshot_from_create_map` journals it, and
        # `_upsert_point_props` restores it on replay (`n.embeddingText=$et`).
        # `derived = replay(journal)` therefore RESTORES the composed text
        # rather than recomposing it — nothing is amended.
        #
        # ⛔ TWO conditions, both load-bearing, and both were review findings:
        #  * the composed text must DIFFER from `content` (an unquoted, unbound
        #    point stays byte-identical to the pre-#11739 write: no
        #    `embeddingText` property appears on it at all), and
        #  * `embeddingText` is attached ONLY once a derived vector actually
        #    exists for it — never when the caller supplied the vector
        #    (`"embedding" in props`: that vector describes the caller's own
        #    text) and never when the embedder was unavailable (nothing then
        #    describes the composed text, and a later `backfill_embeddings`
        #    re-encode USED TO be from `content` — since #12489 it reads
        #    `embeddingText or content`, so the backfill is no longer a reason
        #    this rule is safe; it stands on the #4194 identity alone). Attaching
        #    it anyway is exactly the #4194 lie `update_point`'s content arm
        #    clears.
        # ⛔ THE NEIGHBOUR HALF IS DELIBERATELY NOT WIRED HERE, and the reason is
        # measured rather than assumed (#11739 review round 4): a Point has NO
        # resolved neighbours at its own creation. `create_point` mints NO
        # `about*` edge (its only structural writes are `extractedFrom` and
        # `TAGGED`); its `about*` EDGES are written
        # by later passes (`backfill_about_entities`, the capture's entity link),
        # and the `about*` values it does receive are stored as plain string
        # properties — an asserted relation the graph does not hold. Composing
        # names from THOSE would put a neighbour name into the attested
        # `embeddingText` of a point with no such neighbour, which is the
        # fabrication the whole change must not make. So this door composes the
        # half a created point actually HAS: its verbatim quote. The names half
        # (ordering, fan-out ceiling, word budget, redaction — all built and
        # tested in `embed_compose`) attaches at the recompose seam that runs once
        # the edges exist, which is #11739's remaining scope.
        _embedding_text: str | None = None
        # ⛔ FAIL-SOFT, and the shape is #5148's rule rather than a habit: this
        # door must survive an embedder/dependency that cannot be imported
        # (`tests/test_write_path_unreachable_seam_5148.py` replaces the seam
        # with a raising module, and the `try/except` around `encode_for_row`
        # below is the same posture). `embed_compose` imports
        # `tortoise.embeddings` for its word budget, so an UNGUARDED import here
        # made `create_point` raise with the gate OFF — review round 5 measured
        # exactly that. A failed import means "no composition", never "no write".
        try:
            from . import embed_compose as _embed_compose
            if _embed_compose.compose_enabled():
                _composed = _embed_compose.compose_embedding_text(
                    props.get("quote"), content=content)
                if _composed != content:
                    _embedding_text = _composed
        except Exception:  # noqa: BLE001, RUF100 — composition is optional
            _embedding_text = None

        # Compute embedding (Phase 1A, #7698) — stored as Point property.
        # #4280: the store declares the width it can hold; on the embedded
        # lane that is None (no vector index — brute-force scan), so a
        # self-consistent injected encoder is stored instead of silently NULLed.
        embedding = None
        try:
            from .embeddings import encode_for_row
            embedding = encode_for_row(
                _embedding_text if _embedding_text is not None else content,
                self._get_proj().required_embedding_dim)
        except Exception:
            pass  # Graceful — embedding is optional
        if (_embedding_text is not None and embedding is not None
                and "embedding" not in props):
            props["embeddingText"] = _embedding_text

        # #2199 baseline — resolved BEFORE the CREATE so the ep_* provenance
        # fields land in the node's FIRST write. Embedded (FalkorDB Lite)
        # fulltext reindexes on a post-CREATE SET: a later baseline write
        # delete+re-adds the doc and silently drops its indexed content terms
        # (score -> 0, ranking collapse — the #2206/issue-insight E2E caught
        # it: the seeded decision fell below the observation hits). One write,
        # one index entry, on every engine.
        _create_baseline: tuple[float, float, str] | None = None
        # #2199 system default: a decide part BORN live (no explicit status in
        # the call) gets Beta(3,1). Computed FIRST because #398's declared word
        # is a SOURCE that ADDS one weak evidence body to whatever baseline the
        # claim already has — it must never delete this recorded provision.
        _system_baseline: tuple[float, float] | None = None
        if not explicit_status and kind in DECIDE_PART_KINDS:
            from tortoise.source_credibility import credibility_prior
            _system_baseline = credibility_prior(DECIDE_DEFAULT_CREDIBILITY)
            # RECORD the #2199 provision ON the point (`baseline_system_default`,
            # a declared Point prop). The recompute must re-inject the default
            # ONLY where #2199 actually granted one (#6151 defect 2): a decide
            # part BORN live has it, a decide part created with an EXPLICIT
            # status is documented as keeping full manual control (no default).
            # The `baseline_source` token alone cannot tell the two apart once a
            # word re-stamps the baseline `set-by-author`, so the birth fact is
            # persisted here rather than inferred later.
            props["baseline_system_default"] = True
        if cred_prior is not None:
            # #398 (owner ruling 2026-10-12): the declared word is a validated
            # LABEL and ONE weak evidence body — a SOURCE that ADDS to the
            # baseline the claim already has, never replacing it. A decide part
            # born live therefore starts at Beta(3,1) + weak body; a plain
            # claim (no #2199 default) starts at the weak body alone. Either
            # way a labelled claim can never score BELOW the same claim
            # unlabelled — typing a word is never punished. Real sources
            # attached later raise the claim further through ordinary
            # inheritance (`_apply_source_inheritance` folds the weak body back
            # in as one supporter).
            # The persisted `credibility` property is that LABEL (and the marker
            # that this baseline is an authored assertion — an EXPLICIT
            # `set_point_baseline` carries no such label and stays frozen).
            from tortoise.source_credibility import (
                AUTHOR_ASSERTION_WEAK_PC,
                canonical_tier,
            )
            _base_a, _base_b = (
                _system_baseline if _system_baseline is not None else (1.0, 1.0))
            _create_baseline = (_base_a + AUTHOR_ASSERTION_WEAK_PC, _base_b,
                                BASELINE_SOURCE_SET_BY_AUTHOR)
            props["credibility"] = canonical_tier(credibility)
        elif _system_baseline is not None:
            # #2199: system default applies ONLY to decide parts that were
            # BORN live (no explicit status in the call) — see the tail
            # comment block below for the full rationale.
            _create_baseline = (*_system_baseline, BASELINE_SOURCE_SYSTEM_DEFAULT)

        # #2952: EVERY property of a new Point is written in the CREATE's
        # property map — ONE graph write per point, no post-CREATE clause. A
        # post-CREATE write (the `SET n.embedding = ...` clause and the
        # per-prop `SET n += $props` loop that used to live here) makes the
        # engine delete+re-add the document in its fulltext index; the re-add
        # double-counts the index's collection statistics (document count /
        # total field length — the idf and fieldNorm terms of the fulltext
        # ``score``), and only an ASYNC engine pass reconciles them tens of
        # seconds later. Net effect: the same unchanged graph returns
        # different fulltext scores — hence a different RRF order and a
        # different top-k — purely as a function of elapsed wall-clock time.
        # Measured: a point created with zero post-CREATE writes scores
        # identically at t+0 and after settle (8.0); adding ONE post-CREATE
        # SET drops it to 6.0 and it drifts back ~30-60s later. This is the
        # same embedded-fulltext delete/re-add hazard #2199 fixed for the
        # baseline fields — extended here to the embedding and every prop.
        # #2952: the EP dirty stamp rides the SAME write. `_mark_dirty`'s
        # post-CREATE `SET n.ep_dirty = ...` was the last remaining second
        # write on the new node; folding it here makes the whole fresh-create
        # path ONE graph write. The epoch is advanced NOW so the stamp and
        # the persist agree on one value (see _advance_ep_version).
        #
        # #2422 (born-terminal exception): a point whose BORN status is
        # terminal must NOT carry that stamp. `status` is a write-path
        # argument, so the only place the terminal exclusion is read is
        # `_mark_dirty`'s persist query's WHERE — and that query is skipped
        # entirely when every dirty id arrives `pre_stamped` (`persisted |=
        # _stamped & set(dirty_ids)` below), which is precisely what an
        # inline stamp would do. The result is the #2422 ghost class: a
        # terminal point can never enter an EP affected set, so
        # `_sweep_dirty_roots` can never clear its flag — a NEVER-clearable
        # dirty root that pins `_auto_dream_mode` to 'local' forever (the
        # comment in `_mark_dirty` forbids exactly this). So a born-terminal
        # point gets no `ep_dirty`/`ep_dirty_at` in the CREATE map, and is
        # NOT reported as pre-stamped: it flows through the persist WHERE
        # (matches nothing → no write) into the terminal classification
        # (`terminal_ids`), which clears any stale flag and keeps the id out
        # of the in-memory mirror. `_sanitize_props` already rejects the
        # legacy `outdated` flag prop (#2491), so `status` is the complete
        # born-terminal surface.
        _born_terminal = status in TERMINAL_EXCLUDED_STATUSES
        # THE FIRST WRITE in this method is `_advance_ep_version`, which stamps the
        # epoch the CREATE below reuses — so an aborted write fails THERE, and
        # wrapping only the CREATE would leave this path unretried, making the
        # retry dead code in the failure mode it targets (#7405 review P0). Both
        # writes therefore go through the helper, on the same (stateless) handle.
        _epv = self._advance_ep_version()
        _create_params: dict = {"id": pid, "c": content, "k": kind, "st": status,
                                "now": now, "embedding": embedding,
                                "_epv": _epv}
        # Server-owned EP-dirty flags are never caller props — drop any that
        # slipped in so the CREATE map keeps exactly one key of each name.
        props.pop("ep_dirty", None)
        props.pop("ep_dirty_at", None)
        # #2952: a point born from a Source is inherit-eligible by definition
        # (#398) — the inheritance gate must start invalid. The generic helper
        # expresses that as `REMOVE n.inherited_at` AFTER the prop write, but
        # a fresh point has no stamp of its own and the caller's (if any) is
        # invalidated anyway, so the prop is dropped HERE: the CREATE map
        # never writes it and no post-CREATE REMOVE is needed (final node
        # state identical, ONE write instead of two).
        if props.get("extractedFrom"):
            props.pop("inherited_at", None)
        # CREATE-map (key -> expression), in the order the CREATE has always
        # written the fields. Built as a MAPPING because a caller prop whose
        # key collides with a field here must REPLACE it, not duplicate it:
        # the pre-#2952 code applied props in a post-CREATE `SET n += $props`
        # (so props won), and `createdAt` is a normal caller prop — api.py,
        # ingest.py and the source-inheritance path all pass one.
        #
        # #5256: the extractedFrom read-version is resolved from the :Source
        # HERE, on the LIVE path only, and carried in the CREATE map — i.e. in
        # the Point's OWN journaled snapshot (`_journal_snapshot_from_create_map`
        # copies it into the PointAdded payload below — the payload is the write
        # intent, NOT the read-back; the later `get_point` is the write-landed
        # proof only). The replay must never re-read the Source:
        # `_upsert_source`'s in-place contentHash bump is unjournalled (#5024),
        # so a Source read at rebuild time may hold a NEWER hash than the one
        # this Point was actually read from. A Source that does not exist yet
        # (or has an empty hash) contributes nothing — honest-absent, and the
        # stub `_link_source` mints below has no hash either way.
        _source_versions: dict[str, str] = {}
        _source_version_sv = None
        if props.get("extractedFrom"):
            from .projection.edges import _source_version_transit, resolve_source_versions
            _source_versions = resolve_source_versions(
                proj.g, props["extractedFrom"])
            _source_version_sv = _source_version_transit(_source_versions)
        # #5516: the write-time notice that the Source being written against has
        # changed since it was last read. Computed HERE, BEFORE the new Point's
        # `extractedFrom` edge is stamped below, so the comparison reads the
        # PRIOR recorded read version (`r.sourceVersion`, #5256) rather than the
        # version this very write is about to record. It never blocks or fails
        # the write — a broken notice read is logged and dropped (the advisory
        # contract). It is a READ over the recorded links; the only side effect
        # is `resolve_source_key`'s idempotent adopt-on-touch, and
        # `resolve_source_versions` above already ran the identical resolution
        # for these refs, so no NEW write occurs here (see the helper docstring).
        # The notice rides the write RESULT only; it is never a node property and
        # is never journaled.
        _source_change_notices: list[dict] = []
        if props.get("extractedFrom"):
            try:
                from .projection.edges import source_change_notices
                _source_change_notices = source_change_notices(
                    proj.g, props["extractedFrom"], _source_versions)
            except Exception:  # noqa: BLE001, RUF100 — advisory only
                _logger.warning(
                    "create_point: source-change notice read failed — "
                    "continuing without a notice (#5516)", exc_info=True)
                _source_change_notices = []
        _create_map: dict[str, str] = {
            "id": "$id", "content": "$c", "pointKind": "$k",
            "is_operator": "false", "status": "$st",
            "createdAt": "$now", "updatedAt": "$now",
        }
        if not _born_terminal:
            _create_map["ep_dirty"] = "true"
            _create_map["ep_dirty_at"] = "$_epv"
        if _source_version_sv is not None:
            _create_map["sourceVersionTransit"] = "$sv"
            _create_params["sv"] = _source_version_sv
        if _create_baseline is not None:
            _create_params.update(_baseline_create_params(_create_baseline))
            _create_map.update(_baseline_create_fields())
        # ``vecf32()`` is accepted inline in the property map on both engines
        # (embedded FalkorDBLite and server 4.x), so the embedding is no
        # longer a trailing SET clause. $embedding is None when the embedder
        # is unavailable — identical to the old `SET ... = vecf32(null)`.
        # A caller-supplied `embedding` prop is the embedding itself (the old
        # ordering applied the prop loop after the embedding SET, so props
        # won) — popped here so the map carries exactly one `embedding` key.
        # TYPE CHANGE GUARD (P2, PR #3018 review): the pre-#2952 props loop
        # stored a caller-supplied `embedding` RAW (it merely won the
        # assignment race against `SET n.embedding = vecf32($embedding)`), so
        # it must NOT be coerced here either — a silent float64-list →
        # vecf32/float32 rewrite of an explicitly caller-owned value would
        # change what a vector read returns. The server-computed embedding
        # keeps the original `vecf32()` coercion (unchanged).
        _embedding_expr = "vecf32($embedding)"
        _verbatim_embedding = False
        if "embedding" in props:
            embedding = props.pop("embedding")
            # #5004 round-6: the caller's vector is subject to the SAME
            # store-width guard the server-computed one already passes through
            # (`encode_for_store` -> `_degrade_to_width`). Without it the live
            # node stores a width the HNSW index cannot hold, while the REPLAY
            # refuses that same width and leaves the node vectorless — so the
            # rebuilt graph loses a vector live holds, i.e. `derived =
            # replay(journal)` is FALSE and the guard is one-sided. Degrading
            # BOTH sides to the store's declared width is the only consistent
            # arm; a wrong-width value is never written by either.
            _dim = self._get_proj().required_embedding_dim
            if embedding is not None and _dim is not None:
                try:
                    _w = len(embedding)
                except TypeError:  # not sized — cannot be a vector
                    _w = None
                if _w != _dim:
                    _logger.warning(
                        "create_point: a caller-supplied embedding of width "
                        "%s does not match this store's %d — storing the point "
                        "WITHOUT a vector rather than one the index cannot "
                        "hold or the replay would refuse (#5004)",
                        _w if _w is not None else "?", _dim)
                    embedding = None
            _embedding_expr = "$embedding"  # caller value stored verbatim
            _verbatim_embedding = embedding is not None
        _create_params["embedding"] = embedding
        for _i, (_key, _val) in enumerate(props.items()):
            _pname = f"_cp{_i}"
            _create_params[_pname] = _val
            _create_map[_key] = f"${_pname}"
        # The embedding is written LAST (and `embedding` was popped from props
        # above), so the map carries exactly one `embedding` key.
        _create_map["embedding"] = _embedding_expr
        # #2351: the claim class rides the CREATE map (one graph write, #2952)
        # and only for `derived` — absence IS `asserted`, which is what makes
        # the marker migration-free for every claim already in the graph.
        # "asserted" passed explicitly is validated and stored as NOTHING.
        if _claim_class_value == _claim_class.DERIVED:
            _create_params["claimcls"] = _claim_class.DERIVED
            _create_map[_claim_class.PROP] = "$claimcls"
        # #5004 round-3: a CALLER-owned vector is stored VERBATIM (the recorded
        # PR #3018 decision) and the replay must restore it in that same FORM.
        # The noun that carries that fact has to be the NODE, not the journal
        # payload: every later re-emit (`promote_point`, `merge_points`,
        # `mitigate_operator`, annotate) reads this point back through
        # `get_point`, and a payload-only flag was LOST there — the replay then
        # took the `vecf32` branch and narrowed the vector
        # (`0.1` -> `0.10000000149011612`), making `derived = replay(journal)`
        # false on every promote of a caller-vectored point. As a declared,
        # server-managed node property it rides `get_point` for free and is
        # restored by its own SET clause (`_upsert_point_props`).
        if _verbatim_embedding:
            _create_map["embedding_verbatim"] = "true"
        _create_fields = "".join(
            f", `{str(_k).replace('`', '``')}`: {_v}"
            for _k, _v in _create_map.items())
        # ── #7951 §17.1 — THE OUTBOX ORDERING (docs/architecture/STORAGE-ARCHITECTURE.md) ──
        # THE APPEND IS THE COMMIT POINT. The `PointAdded` record is built from
        # the SAME `_create_map`/`_create_params` the CREATE below writes and is
        # appended BEFORE any `:Point` cache write (`CREATE`/`MERGE` on `:Point`
        # and its edges), so the derived store cannot lead the truth for the
        # Point statements. NOTE the scope — the pre-append control-plane writes
        # (`_advance_ep_version`'s `:EpMeta`, `_emit_event`'s `:GraphEvent`, the
        # `:Source` adopt-on-touch) are the declared residuals of plan §3/§6.5,
        # and they DO precede the append. This is the outbox form of §17.1's mechanism (1): the
        # journal append is the transaction, and the cache write that follows
        # materialises exactly the record's own properties. The old post-CREATE
        # `get_point` read-back could not be journalled first — it does not
        # exist until the cache has already been written, which is how
        # `create_point` came to be the recorded apply-first deviation.
        # If the append fails the cache is NOT written (the append's own
        # returned outcome — the `_doc_write` template used a counter delta;
        # this door reads the per-call signal, see F1): the record could not be
        # committed, so the derived store must not be written. ⚠️ `_emit_event` writes the
        # `:GraphEvent` store BEFORE the JSONL append, so this arm leaves a
        # `:GraphEvent` for a Point that was never written — a DECLARED residual
        # (plan §6.7), not a committed rebuild record: the JSONL journal (the
        # rebuild source) holds nothing.
        _intended = _journal_snapshot_from_create_map(_create_map, _create_params)
        # ── #8933 Stage 2, WRITE HALF — at CAPTURE ──
        # The ruling says "at capture, write a normalised copy of each identifier
        # into the indexed text", and `create_point` is the door the mint uses
        # (`_capture_session_impl` -> `self.create_point`). It used to reach only
        # `_flatten_search_keys_prop`, so no SDK-created or captured Point ever
        # held the alias while a REPLAY of the same `PointAdded` did
        # (`apply` -> `_upsert_point_props`) — `derived = replay(journal)` was
        # false on `search_keys`, and a query typed with no separator
        # (`err4032`, the ruling's "dashes, spaces, or none") matched nothing.
        #
        # ⛔ It is DERIVED into the node and never RECORDED: `_intended` is
        # snapshotted on the line above, so the alias reaches the CREATE map and
        # nothing else. The journal keeps E3's spelling and a rebuild re-derives
        # the same value from `content` + the payload's own `search_keys` — the
        # SAME `point_identifier_aliases`/`append_identifier_aliases` pair
        # `_upsert_point_props` calls, never a second normaliser (the ruling's
        # criterion 2, and the Elastic `normalizer`-on-the-field shape).
        _alias_view = {"content": content}
        if isinstance(props.get("search_keys"), str):
            _alias_view["search_keys"] = props["search_keys"]
        if append_identifier_aliases(
                _alias_view, point_identifier_aliases(_alias_view)):
            # Rebuilt here (not earlier) so the un-patched path builds the field
            # list byte-identically to before this change.
            _create_params["_cp_alias"] = _alias_view["search_keys"]
            _create_map["search_keys"] = "$_cp_alias"
            _create_fields = "".join(
                f", `{str(_k).replace('`', '``')}`: {_v}"
                for _k, _v in _create_map.items())
        # The append's OWN outcome is the commit signal — a per-call value, not
        # a delta of the shared `_journal_write_failures` counter: a concurrent
        # writer on the same instance can increment that counter between the
        # read and the check, which would report THIS call's committed record as
        # failed and invite a duplicate re-issue (#5612; the sibling doors still
        # use the delta — plan §6.10).
        _record_committed = self._emit_event(
            "PointAdded", {"id": pid, "kind": kind, "content_hash": ch},
            point=_intended)
        if _record_committed is False:
            from .exceptions import WriteNotLandedError
            raise WriteNotLandedError(pid, getattr(proj, "graph_name", None))
        # #7951 §17.1: the cache write runs inside the live-write scope — the
        # door declares itself to the guard, and every statement below runs
        # strictly AFTER the append above. Since #9124 the declaration is
        # LOAD-BEARING rather than documentary: the §17.1 guard now covers the
        # raw guarded handle too (`proj.g`), which is the handle this `CREATE`
        # uses, so without the scope an armed handle refuses the create hot
        # path. The declaration is a lane, not an exemption — the append above
        # is the commit point and every write here follows it.
        with _live_write_scope("create_point"):
            self._graph_write_with_retry(
                lambda: proj.g.query(
                    "CREATE (n:Point {" + _create_fields.lstrip(", ") + "})",
                    params=_create_params,
                ),
                what="create_point CREATE(:Point)",
            )
            # No post-CREATE re-resolve: the `falkordb` Graph is STATELESS (it
            # re-issues `GRAPH.QUERY <name>` per call), so a replaced-graph abort is
            # a per-query race and `proj` is as valid after a retry as before it
            # (#7405 review P1-2). `_sync_tags` / `_link_source` below reuse it.
            # Tag handling: create :Tag nodes + TAGGED edges (#215, #485)
            #
            # #7209: the follow-up writes of a fresh create ride the SAME bounded
            # retry as the CREATE above. Before this they were bare `proj.g.query`
            # calls, so a rebuild's per-query abort landing here aborted the write
            # AFTER the Point had already landed — leaving a half-materialized
            # point (no TAGGED / no extractedFrom edges), which is a finding that
            # provenance and tag reads cannot reach: the same "the map loses a
            # finding" outcome by a narrower route. Every statement in these two
            # helpers is a MERGE (plus a read-guarded canonicalUrl adopt, itself a
            # documented no-op on re-run), so re-issuing one after an engine ABORT
            # cannot duplicate anything — the abort means the statement did not
            # land, exactly the contract `retryable_aborted_write` is built on.
            tags = props.get("tags") or []
            if isinstance(tags, (list, tuple)):
                self._graph_write_with_retry(
                    lambda: self._sync_tags(proj, pid, tags),
                    what="create_point TAGGED edges",
                )
            # P1-1: Ontology v2.1 — link Point → Source via extractedFrom
            if props.get("extractedFrom"):
                self._graph_write_with_retry(
                    lambda: proj._link_source(pid, props["extractedFrom"],
                                              source_versions=_source_versions),
                    what="create_point extractedFrom edges",
                )
                # Inheritance gate dirty-mark: a freshly-sourced point is always
                # inherit-eligible on the next EP run (no interval wait, #398).
                # #2952: a born point carries no `inherited_at` stamp, so the
                # `REMOVE n.inherited_at` the generic helper
                # (`_invalidate_inheritance_gate`) issues is a no-op on the final
                # state — and a no-op write on the node still costs a fulltext
                # re-add. The `extractedFrom` invalidation therefore happens in
                # the PROPS (see the up-front pop of `inherited_at` above): a
                # caller-supplied stamp is dropped BEFORE the CREATE map is built
                # instead of being written and then taken back out, so this path
                # is ONE graph write too. The EP dirty-marking half is issued once
                # below.

        # Apply the starting belief (only on new creation, not dedup).
        #
        # #2199: decision parts (kind in DECIDE_PART_KINDS) that were born
        # live get an EXPLICIT, provenance-recorded prior so the documented
        # decide flow ranks on the first try with zero undocumented
        # promote/calibrate chores. A passed ``credibility`` (plain-language
        # ladder word gold/high/medium/low/unverified, T0-T4 form, or numeric
        # 0-4) is a validated LABEL that ADDS one weak evidence body on top of
        # that baseline (#398) — it never replaces it — and stamps provenance
        # 'set-by-author'. No word → the standard starting belief 'medium' =
        # Beta(3,1), stamped 'system-default' — an EXPLICIT default with
        # labeled provenance, never a silent Beta(1,1)-as-calibrated uniform
        # (the #7478 guard).
        # Author-set credibility on ANY kind/status ADDS to the baseline the
        # same way.
        #
        # The graph write already happened inside the CREATE (above) — this
        # block only records the in-memory evidence cache + EP message
        # invalidation, so the fresh-create path never issues the separate
        # post-CREATE SET (embedded fulltext reindex hazard, above).
        # System-default note: it applies ONLY to decide parts that were BORN
        # live (no explicit status in the call) — a caller that explicitly
        # passed a status keeps full manual control per the docstring: no
        # auto default. This matters for sourced decide parts filed live from
        # a capture/commit receiver: they must stay inherit-eligible
        # (baseline_source IS NULL) so a tiered source's belief is applied at
        # EP time instead of being frozen at the flat medium default (which
        # would silently overwrite source belief — never-write-source-
        # inheritance class).
        if _create_baseline is not None:
            _b_a, _b_b, _b_src = _create_baseline
            self._evidence[pid] = (_b_a, _b_b)
            # Epic 903-C4 (#1242): a prior change alters factor behavior
            # WITHOUT a topology change — warm-start seeds computed under the
            # OLD prior are stale. Full drop (cheap) per the plan's W3 rule.
            self._get_ep().invalidate_messages()
        # Dreaming (#85): a new point can carry confidence-affecting props;
        # mark it dirty so the next dream/lazy-read stabilizes it.
        # #2952: the graph stamp was written by the CREATE above — this call
        # only fills the in-memory mirror (no second write on the node).
        # #2422: a born-terminal point was NOT stamped by its CREATE — it is
        # not pre-stamped, so `_mark_dirty` runs the persist query (whose
        # terminal WHERE matches nothing: no write) and then classifies the
        # id as terminal, keeping it out of `_dirty_roots` (the exact
        # observable the P1 regression test pins).
        self._mark_dirty([pid], ep_version=_epv,
                         pre_stamped=set() if _born_terminal else {pid})
        # #432+#548 unified: domain payload + full point snapshot for both
        # the :GraphEvent store (subscriptions/poll) and JSONL (rebuild_all).
        #
        # #7209: this read-back is the SDK's own proof that the write LANDED,
        # so it must not be a silent one. `get_point` answers `{}` for a Point
        # it cannot see — indistinguishable, to the caller, from a successful
        # create whose result happens not to carry an id. Returning that `{}`
        # is how a discarded write read as success (#7209); the retry above
        # closes the abort path, and this guard closes the RESIDUAL the retry
        # documents but cannot: a state-gated re-issue applied into a rebuilt
        # graph that the rebuild's own replay then replaces again (#7685's
        # escalation line — closing it fully needs an engine identity token).
        # #7951: the record was ALREADY appended ABOVE (the commit point),
        # before the cache write — see the outbox block at the CREATE. The
        # read-back below is the write-landed PROOF only; it is no longer the
        # journal payload. A read-back miss AFTER a committed append means the
        # TRUTH holds a record the cache has not materialised — a replay heals
        # it (the ordering contract's replay-healable window) — so this raise
        # must NEVER be read as "the write is gone": the record stands.
        _snapshot = self.get_point(pid)
        if not _snapshot:
            from .exceptions import WriteNotLandedError
            raise WriteNotLandedError(pid, getattr(proj, "graph_name", None),
                                      record_committed=bool(_record_committed))
        # ── #8171: the write-time provenance advisory, CLAIM door ────────
        # The ruling: an agent asserting something without research is a WEAK
        # source, and the source is the agent-session transcript. `audit`'s
        # `missing_sourceKind` check is the SAME predicate, evaluated POST HOC
        # (it can never change what the writer does next), and #8171 wired it
        # at write time on the entity doors — but the door that actually
        # creates CLAIMS (Points) reported nothing, so "a claim created with
        # no source and no provenance" was never told to its writer. MEASURED
        # before this: `create_point('statement', text)` returned the plain
        # snapshot with no `advisories` key at all.
        #
        # ADVISORY ONLY — it rides the write result on a COPY, exactly like the
        # #5516 `source_change_notices` below: the journaled snapshot and every
        # later `get_point` stay free of it, the write has already LANDED, and
        # nothing here raises, refuses or changes what was stored.
        #
        # What counts as SOURCED, and why each silence is justified:
        #   - `extractedFrom` (declared, or inferred from `session_id` by the
        #     #3263 path above) — materialized by THIS door into a `:Source`
        #     and an `extractedFrom` edge. That is the remedy the advisory
        #     names, so it must silence the note.
        #   - an authored WORD (`credibility=`) — #398 rules the author's word
        #     IS a source (the weak evidence body `_stamp_authored_word`
        #     records). Advising a writer who declared their basis would be
        #     wrong, not merely noisy.
        # A bare point-level `sourceKind` does NOT silence it: it is a legacy
        # annotation with no link behind it (the same measured finding that
        # removed the `sourceKind` bypass from `_provenance_advisory` —
        # `extracted_from_inert` exists because a declared-but-unwired ref is
        # worse than none), and `audit`'s own check treats "no sourceKind AND
        # no extractedFrom edge" as the gap, so neither field is provenance on
        # its own.
        _provenance_notes: dict = {}
        if not props.get("extractedFrom") and credibility is None:
            # A `session_id` that could not name a Source is the one case where
            # the plain message would be misleading: say why it did not help.
            _point_sid_hint = (
                " (A `session_id` was supplied but is not a non-empty string, "
                "so no source could be derived from it — pass a string "
                "`session_id`, or `extractedFrom='session:<session_id>'`.)"
                if _session_id is not None else ""
            )
            _provenance_notes = self._no_source_advisory(
                pid, hint=_point_sid_hint)
        _layer_advice = _kind_layer_advisory(_layer_note, pid)
        if _source_change_notices or _provenance_notes or _layer_advice:
            # #5516 / #8171 / #9964: all three are TRANSIENT — they ride the
            # write result the writer sees, on a COPY, so the journaled snapshot
            # above and every later `get_point` stay free of them. A write that
            # carries none returns byte-identical to before these features.
            _extra = dict(_merge_advisories(_provenance_notes, _layer_advice))
            if _source_change_notices:
                _extra["source_change_notices"] = _source_change_notices
            return _with_end_receipt({**_snapshot, **_extra}, _end_receipt)
        return _with_end_receipt(_snapshot, _end_receipt)
