# Archived sdk-method `ingest` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: ingest left the public SDK surface — privatised (body survives as `_ingest`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'ingest'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def ingest(self, bundle: dict, granularity: str = "bulk", *,
               promotion_policy: str = "gated") -> dict:
        """Heterogeneous bulk write (epic #888 W4, design ref PR #912).

        One call writes points + entities + sources + connections coherently:
        all nodes first, then the connections between them. Indexing many
        interconnected items is ONE operation, not N.

        bundle = {
          points:      [{kind, content, ref?, status?, **props}],
          entities:    [{type: subject|object|event|document, name, ref?, **props}],
          sources:     [{url, sourceKind, tier?, sourceDate?, ref?, **props}],
          connections: [{from, to, relation, ...} | {from, to, operator, label?, direction?}],
        }

        - ``ref``: optional local addressing label usable in any connection's
          from/to (and in entity authoredBy/ownedBy/managedBy + about* props,
          and point extractedFrom) instead of the created id/url. Must be
          unique within the bundle. Never stored as a node property.
        - Connections resolve from/to by local ref first, then pass through as
          raw ids/urls to the underlying primitive (create_operator /
          create_edge / _link_source).
        - Reification rule (ontology v3.5 §8, updated for A3 §8 routing #1256):
          a connection carrying ``operator`` reifies to an operator Point ONLY
          when it carries a reification anchor (``reify: true`` or
          ``mitigation``); a PLAIN IMPL/NAND ``operator`` connection is
          operator-less — routed to a DIRECT edge (no operator node, see
          INGEST_CONTRACT.md §8). A connection carrying ``relation`` stays a
          PLAIN structural edge (structural edges never reify).
        - Endpoint typing (#7925, superseding #2062's route split): an operator
          connection endpoint may carry ANY of the four epistemic labels —
          Point/Subject/Object/Event — on BOTH the operator route (reify:
          true/mitigation/part-whole → create_operator) and the DIRECT route
          (plain IMPL/NAND → create_direct_edge). #7902 widened both write
          primitives to the same ``live.EPISTEMIC_LABELS``, and
          ``_find_operator``'s dedup key already collects the four, so the
          validator now states the same contract the writers enforce instead
          of a narrower one. An endpoint must address the node BY ITS ``id``
          (the write key — an Event that resolves only by eventId, e.g. the
          ingest_corpus DocumentCreated shape, is rejected at validation,
          never a mid-write crash); a ``:Source`` (a document IS a ``:Source``)
          and an operator-shaped item (a Point, or a Subject/Object/Event
          entity item — top-level or ``props`` spelling) stay violations. A
          bundle-local entity ref is resolved the way step 3 writes it, not as
          one item: Subject/Object merge BY NAME in bundle order, so a
          same-name item can carry the terminal ``status`` that mints the node,
          or an ``is_operator`` the merge writes onto the shared node (a later
          PERSISTABLE ``is_operator: False`` clears it; ``null`` is dropped by
          the writer, so it clears nothing); an existing operator-shaped or
          terminal name holder is refused too. The server-managed ``outdated``
          flag is refused bundle-wide instead — on ANY point or entity item,
          referenced or not, on either route — because BOTH item writers
          refuse it on PRESENCE and steps 2-3 write every item. The ``:Subject``
          status vocabulary is refused bundle-wide for the same reason: its
          writer (``create_entity(type='subject')`` through
          ``create_subject``) validates every item's ``status`` against
          ``SUBJECT_STATUS_VALUES`` before the name merge, so an invalid value
          (outside that set, non-string, or an explicit ``null``) on ANY entity
          item is a Phase-1 violation, while ``:Object``/``:Event`` statuses
          are stored verbatim and stay accepted — EXCEPT a ``:Object`` status
          that is a PERSISTABLE NON-STRING, which #12869 refuses at Phase 1 for
          the same partial-mutation reason (the arm is ``api.py``'s call into
          ``_refuse_non_string_object_status``; a value every writer DISCARDS
          is still dropped rather than refused, #7369 / #7925 round 4 — note
          that #11824 S0's door refuses non-strings earlier still for the SDK
          routes, so this arm covers the ingest route, not the whole surface).
          ⛔ A ``:Event`` status is
          refused bundle-wide, and since #13100 (S7) that means ANY non-``None``
          ``status`` — the terminal half alone was #11825's interim boundary, and
          the non-terminal half is retired too because "will happen" is a PLAN's
          job (`tortoise/planned_occurrence.py`). It is refused here for exactly
          the reason the ``:Subject`` vocabulary is — the door
          (``create_event`` → ``create_entity(type='event')``) runs
          ``live.refuse_event_lifecycle_state``, so leaving it to step 3 means the
          point items commit and THEN the write raises (measured at #11825: a
          bundle with one point and one ``status='superseded'`` event returned
          ``[]`` here and raised a bare ``ValueError`` after ``Point 1`` was
          written — the #2422/#7925 partial-mutation class, review cycle 3 P1). Props are read at the depth
          the ITEM'S write door flattens (``_item_prop_at_depth``): TWICE for a
          ``subject``/``object``/``event`` item (step 3 relays
          ``create_subject``/``create_object``/``create_event`` →
          ``create_entity``, each running ``_coerce_props``), ONCE for a
          ``document`` item (``create_document`` folds once and relays to the
          low-level ``_create_entity``), and TWICE for a ``points`` item
          (``create_point`` folds once, and its DEDUP relay into
          ``update_point`` folds again) — so ``props: {"props": {...}}`` cannot
          hide a refused key from Phase 1, and a third level is visible to
          neither reader nor writer. A direct-route
          connection whose two endpoints resolve to the SAME name-merged node
          is a self-edge and is rejected.
          NOTE: Event entity items are append-only — re-ingesting a FULL bundle
          mints a new Event occurrence (new id) and thus a new operator; dedup
          applies to id-stable external Event endpoints (or re-ingesting
          connection-only against the resolved id).
        - ``mitigation: {reason, strength}`` on an operator connection is
          APPLIED, not just an anchor (#5244): after the operator is created /
          deduped / absorbed, the mitigation is written through
          :meth:`mitigate_operator` — a mitigation Point plus the
          ``(op)-[:mitigated_by]->(m)`` edge, stamped with the bundle's
          batch_id. Re-submission updates it IN PLACE (never a duplicate) and
          warns ``mitigation_strength_change`` when the reason/strength
          actually changed (INGEST_CONTRACT §3.2 key 6). On a ``relation``
          connection a mitigation is a Phase-1 violation: a structural edge
          has no operator bridge to damp.
        - granularity='bulk' (default): whole bundle in one coherent pass,
          returns aggregated {created, ids, nudges}. granularity='granular':
          additionally returns per-item ``results`` for agent step-by-step
          control (each item's primitive result + deduped flag).
        - promotion_policy: "gated" (DEFAULT, Q2) — points stay draft;
          connections never promote (operator path: promote_source=False via
          #780 — the operator node is created draft and the source is NOT
          auto-promoted). "auto" — #131 parity preserved: a source point
          promotes on wire when its FIRST operator edge is created
          (CYCLE-26 REVIEW FIX P2: NOT retroactive — re-ingest dedup of an
          existing operator does not retro-promote a previously-gated
          source). ORTHOGONAL to granularity: both modes honor the same
          policy.
        - Idempotent-ish: points dedup by (content_hash, pointKind) via
          create_point(dedup=True); sources merge by url; Subject/Object
          resolve-or-mint on the name (#3590: the MERGE key is `id`, so the
          second item naming a live name RESOLVES to it rather than minting
          a second carrier); operator connections dedup by (op_type, input
          set);
          structural edges MERGE. Document/Event entities are append-only
          occurrence records — re-ingest duplicates them by design.
        - EP-safe: under promotion_policy='gated' every no-status point is
          born draft and stays draft (the operator wiring passes
          promote_source=False). Under promotion_policy='auto' a no-status
          connection-source point is born live and a no-status non-source
          point draft; a no-status DECIDE-PART item is left to create_point's
          #2199 system-default baseline. Since #1088 create_point's own
          default is live, this path states each point's status by intent
          rather than inheriting that default. Under gated ANY effective
          status other than 'draft' on a point item is rejected (INGEST
          CONTRACT row 9 —
          no bypass of the gated contract; the sanctioned routes are
          promotion_policy='auto' or update_point(status='live') after ingest;
          case variants, nested props={...}, and canonical terminal statuses
          are rejected too — EP _live_only excludes 'draft' and terminal
          statuses/flag (#2422)).
          Connection-driven promotion (source → live on first edge) only
          happens under promotion_policy='auto', and only for null-status
          sources (retracted/deprecated terminal sources are never
          resurrected; an explicit or prior draft is never retro-promoted,
          #1088).
          Under auto the operator node is written 'live' (#5089 — it used to be
          written WITHOUT a status property, which excluded the live operator
          from the strict grounding selector while a rebuild produced 'live'; the
          EP factor-extraction path admits a NULL status either way. Gated still
          writes an explicit draft on the operator).

        Returns {granularity, batch_id, created: {points, entities, sources,
        connections}, deduped: {...}, ids: {points, entities, sources,
        connections, refs}, nudges: [...], warnings: [...]} (+ results for
        granularity='granular'). The key set is the canonical enumeration
        (INGEST_CONTRACT §2.2 — a docs/behavior conformance test pins it).
        """
        if granularity not in INGEST_GRANULARITIES:
            raise ValueError(
                f"ingest: granularity must be 'bulk' or 'granular', got {granularity!r}"
            )
        if promotion_policy not in INGEST_PROMOTION_POLICIES:
            raise ValueError(
                f"ingest: promotion_policy must be 'gated' or 'auto', got {promotion_policy!r}"
            )
        if not isinstance(bundle, dict):
            raise ValueError(
                f"ingest: bundle must be a dict with points/entities/sources/"
                f"connections sections, got {type(bundle).__name__}"
            )
        # Row 9 of INGEST_CONTRACT.md: under gated, an explicit status:'live'
        # on a point item is a violation — the Q2-lock must not be bypassable
        # via the bundle's own status field (the sanctioned routes are
        # promotion_policy='auto' or update_point(status='live') after ingest).
        # This is check 5's RETAINED piece (CYCLE-26) — enforced by the shared
        # _check_gated_status helper below (see _validate_bundle).

        # ── Phase 1 — shared check helpers (plan §5.2 checks 1-8): collect
        # ALL violations with ZERO mutation before any write. Fail-fast raise
        # carries the first violation's message (the shipped message contract);
        # the A2 failure contract upgrades this to BundleValidationError with
        # ALL violations (.violations, .as_dict()).
        violations = self._validate_bundle(bundle, promotion_policy=promotion_policy)
        if violations:
            # A2 failure contract: BundleValidationError carries ALL
            # violations (str() = first message for back-compat parity);
            # _safe maps it to {error, code: ERR_BUNDLE_INVALID, violations}.
            from .exceptions import BundleValidationError
            raise BundleValidationError(violations)

        proj = self._get_proj()
        # §4.2 (A4): deterministic content-derived batch_id over the canonical
        # serialization of the RESOLVED bundle (refs expanded, NFC-normalized,
        # int/float-collapsed, item/connection order normalized) — computed ONCE
        # here, stamped on every NEW point the commit creates and applied
        # stamp-when-absent on dedup hits. Clock-independent by construction
        # (no time component — CYCLE-21 pin).
        from .exceptions import Phase2Error
        batch_id = derive_batch_id(bundle)
        refs: dict[str, str] = {}          # ref → canonical id (or url for sources)
        source_refs: set[str] = set()      # refs that address Source nodes (url-keyed)
        ids = {"points": [], "entities": [], "sources": [],
               "connections": [], "refs": {}}
        created = {"points": 0, "entities": 0, "sources": 0, "connections": 0}
        deduped = {"points": 0, "entities": 0, "sources": 0, "connections": 0}
        results = [] if granularity == "granular" else None
        # A3 warnings contract (cycle-23): the THIRTEEN-key enumeration lives
        # in INGEST_CONTRACT §3.2 (extended from eleven by #8499, and from
        # twelve by #9750's `duplicate_target_deduped` — the set is CLOSED, so
        # a new key is a contract amendment, not a drive-by list)
        # E2E-6.2; this accumulator carries the operator-absorb / residue /
        # drift warnings appended during the connection loop.
        warnings: list[str] = []
        self._ingest_warnings = warnings

        def _register_ref(ref: str, cid: str, section: str) -> None:
            if ref in refs:
                # REVIEW-FIX P2 (cycle-26): the Phase2Error must carry the
                # computed batch_id (plan §6.4 — the agent audits the partial
                # commit) AND the message must interpolate it (the literal
                # "batch_id=batch_id" text was a bug).
                raise Phase2Error(
                    f"ingest: duplicate bundle ref {ref!r} "
                    f"({section}, batch_id={batch_id}) — refs must be unique "
                    f"across the bundle",
                    batch_id=batch_id,
                )
            refs[ref] = cid
            ids["refs"][ref] = cid

        # ── Pre-scan refs: source refs resolve to their url (the canonical
        # Source key, known BEFORE creation); point/entity refs resolve to ids
        # registered as nodes are created.
        for item in bundle.get("sources") or []:
            ref = item.get("ref") if isinstance(item, dict) else None
            if ref:
                if ref in refs:
                    raise Phase2Error(
                        f"ingest: duplicate bundle ref {ref!r} "
                        f"(sources, batch_id={batch_id})",
                        batch_id=batch_id,
                    )
                refs[ref] = item.get("url", "")
                ids["refs"][ref] = item.get("url", "")
                source_refs.add(ref)

        # ── 1. Sources (first: points may reference them via extractedFrom) ──
        # Phase-2 defense-in-depth: the shared shape helper re-checks the raw
        # item right before the write (the same helper Phase 1 ran — parity).
        for i, item in enumerate(bundle.get("sources") or []):
            viols: list[dict] = []
            self._check_item_shape("sources", i, item, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            item = dict(item)
            ref = item.pop("ref", None)
            url = item.pop("url", None)
            source_kind = item.pop("sourceKind", None)
            existed = proj.g.query(
                "MATCH (s:Source {url:$url}) RETURN count(s)",
                params={"url": url},
            ).result_set
            node = self._create_source(url, source_kind, **item)
            canonical = node.get("id") or node.get("url") or url
            if ref:
                # Pre-registered in the ref pre-scan (url known upfront) —
                # refresh the canonical value (id may differ from url).
                refs[ref] = canonical
                ids["refs"][ref] = canonical
                source_refs.add(ref)
            ids["sources"].append(canonical)
            if existed and existed[0][0]:
                deduped["sources"] += 1
            else:
                created["sources"] += 1
            if results is not None:
                results.append({"section": "sources", "index": i, "ref": ref,
                                "item": item, "result": node,
                                "deduped": bool(existed and existed[0][0])})

        # ── 2. Points (status stated EXPLICITLY, #1088) ─────────────────
        # #1088 / INGEST_CONTRACT.md §11: with live as the create_point
        # default, ingest states each point's status by INTENT instead of
        # inheriting a default. Under gated every no-status point is born
        # draft and stays draft (the operator wiring passes
        # promote_source=False). Under auto the edge SOURCE is the point the
        # #131 contract promotes live — and under the #1088 ruling that is
        # exactly what create_point's default supplies, so ingest injects
        # NOTHING for a source; the operator-connection TARGET keeps the
        # draft→live lifecycle. An explicit per-item status (top-level or
        # nested props) always wins — the gated row-9 gate above already
        # rejected any non-draft explicit status, and this default fills only
        # items that carry no status key anywhere (mirrors
        # _check_gated_status's has_status).
        # #1088: the operator-connection TARGETS, collected from the
        # operator-requiring connections. A target is NOT promoted — the #131
        # contract promotes only the source — so under auto a no-status
        # target keeps the draft→live lifecycle (tests/test_mcp_server.py,
        # "source-only"). A point that is neither source nor target is a
        # standalone point: no status is injected at all, so create_point's
        # (live) default supplies it.
        _auto_sources: set[str] = set()
        _auto_targets: set[str] = set()
        if promotion_policy == "auto":
            for _conn in bundle.get("connections") or []:
                # Only an OPERATOR-requiring connection has a promotion path
                # (create_operator / create_direct_edge, both keyed on
                # "operator"). A plain relation connection (extractedFrom /
                # aboutSubject / …) never promotes, so it contributes no
                # target — mirror the ``if "operator" in conn`` write branch
                # below.
                if not (isinstance(_conn, dict) and "operator" in _conn):
                    continue
                # #1088: SOURCE WINS. A dual-role point — the `to` of one
                # operator connection AND the `from` of another — must not
                # be classified as a target. The three promotion clauses were
                # narrowed by this PR to fill only a NEVER-SET status
                # (`WHERE s.status IS NULL`), so an injected draft on a
                # connection SOURCE is never overwritten by the later
                # promote: the point would stay draft forever and be
                # silently EP-inert. A source is born live by create_point's
                # default, so collect the sources and subtract them from the
                # targets below.
                _from = _conn.get("from")
                if isinstance(_from, str):
                    _auto_sources.add(_from)
                # #3263 parity: `to` is a LIST on a many-to-many operator
                # connection (the same shape _resolve_ref_field and the
                # extractedFrom branch above handle element-wise). Collecting
                # only the scalar form silently reclassified every list-`to`
                # endpoint as a STANDALONE point, which then inherited the
                # live default — main wrote those targets draft.
                _to = _conn.get("to")
                if isinstance(_to, str):
                    _auto_targets.add(_to)
                elif isinstance(_to, list):
                    _auto_targets.update(
                        t for t in _to if isinstance(t, str))
            # #1088 SOURCE WINS: a dual-role point is a source first, so it is
            # never treated as a target (see the rationale above).
            _auto_targets -= _auto_sources

        def _bundle_item_has_status(it: dict) -> bool:
            """True when a point item declares a status the MINT door will read.

            #7925 review round 4: this stays at the mint depth (ONE fold —
            `create_point`), NOT at `_item_flatten_depth`'s depth 2, because
            the two readers feed different decisions:

            * `_check_gated_status` asks "did the bundle DECLARE a status" and
              is eager (depth 2): a refused declaration must be caught even
              when the second fold only happens on the dedup relay. That is
              fail-closed and matches row 9's "nested `props={...}`".
            * this asks "will the writer SEE a status", so it must be
              writer-parity. At depth 2 a points item declaring
              `props:{"props":{"status":"draft"}}` reported a status, Phase 2
              skipped its explicit `status="draft"` default, `create_point`
              (one fold) saw none, and the point was born **live** under the
              gate that promises draft (measured: `'draft'` on `origin/main`,
              `'live'` here) — a fail-open.
            """
            return _item_declared_status(it, 1)[0]

        for i, item in enumerate(bundle.get("points") or []):
            viols = []
            self._check_item_shape("points", i, item, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            item = dict(item)
            ref = item.pop("ref", None)
            # CYCLE-25: kind-absent DEFAULTS to 'statement' (v3.8 canonical —
            # the extraction write kind). Legacy kinds are write-compat; the
            # event kind is rejected by the shared _check_kind helper (check 2).
            kind = item.pop("kind", None) or "statement"
            self._check_kind(kind, i, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            content = item.pop("content", None)
            # extractedFrom may address a bundle source by its local ref.
            # #3263: many-to-many — resolve a LIST element-wise, mirroring
            # canonical._resolve_ref_field (whose list branch also precedes its
            # str branch). Handling only the scalar case left a list of local
            # refs unresolved, so _link_source minted Sources named after the
            # LOCAL refs ('s1', 's2') and the real bundle Sources were never
            # linked — inventing exactly the provenance this fix forbids.
            # Already-resolved ids/urls stay literal (refs.get(x, x) semantics).
            _ef = item.get("extractedFrom")
            if isinstance(_ef, str) and _ef in source_refs:
                item["extractedFrom"] = refs[_ef]
            elif isinstance(_ef, list):
                item["extractedFrom"] = [
                    refs[r] if isinstance(r, str) and r in source_refs else r
                    for r in _ef
                ]
            # #2892: shared content+kind resolution — the SAME helper the writer
            # (create_point) uses, so the ingest dedup counter can never drift
            # from the write it predicts. The helper is hash-query-THEN-A10
            # hash-less fallback (the fallback main also carried inline here) and
            # carries the same non-operator predicate the writer uses, so the
            # counter matches the writer exactly.
            #
            # #6110: the resolution is `_resolve_dedup_hit` (key identity
            # included), the SAME helper the writer calls — otherwise a
            # key-bearing item would report a content miss that the writer
            # takes as a key hit, and the counter would say "created" for a
            # fold. #8400: the key is read with `_coerce_props`' EXACT
            # precedence through `_item_dedup_key` — a top-level `key: None`
            # SHADOWS a nested `props["key"]` (presence wins, not truthiness),
            # so the counter cannot consult a key the writer never uses and
            # report a fold for a write that mints.
            existed, _ = self._resolve_dedup_hit(
                content, pointKind=kind, key=_item_dedup_key(item))
            # #1088 / #2199: decide the status on the WRITE COPY only — never
            # on `item` itself. An explicit per-item status (top-level or
            # nested props) ALWAYS wins: the branches below fill only items
            # that carry no status key anywhere (mirrors _check_gated_status's
            # has_status). Under gated EVERY no-status ingest point is born
            # draft — that explicit draft IS the request the gate promises
            # (`_check_gated_status` only inspects an EXPLICIT status, so
            # without it a born-live kind would silently bypass the gate).
            write_item = item
            if not _bundle_item_has_status(item):
                if promotion_policy == "gated":
                    # gated IS the explicit draft request: every no-status
                    # ingest point is born draft and stays draft.
                    write_item = {**item, "status": "draft"}
                elif (ref in _auto_targets and not (
                        isinstance(kind, str)
                        and kind in DECIDE_PART_KINDS | GOAL_KINDS)):
                    # #131 "source-only": an operator-connection TARGET keeps
                    # the draft→live lifecycle. Everything else under auto is
                    # left with NO status, so create_point's default supplies
                    # it — live, per the #1088 ruling. A dual-role point was
                    # already subtracted from _auto_targets (SOURCE WINS), so
                    # nothing is injected for it and it is born live; no
                    # `live` branch is injected anywhere — the default is it.
                    # #2199 and #7856: the decide parts and `goal` are
                    # exempt because a draft Point that is only ever a TARGET
                    # is EP-inert while the graph still reports it (a goal
                    # additionally reports converged=True — docs/ONTOLOGY.md
                    # §5). Those kinds keep their born-live contract; only
                    # `promotion_policy='gated'` writes them draft.
                    write_item = {**item, "status": "draft"}
            # #7856: a COPY, never a mutation of `item`. Granular
            # `results[].item` echoes the item's remaining props
            # (docs/INGEST_CONTRACT.md — empty for a bare statement point),
            # so forcing the draft in place would report a status the caller
            # never passed.
            point = self.create_point(kind, content, dedup=True, **write_item)
            pid = point["id"]
            if ref:
                _register_ref(ref, pid, "points")
            ids["points"].append(pid)
            # §4.2 (A4): stamp the bundle's batch_id on EVERY point — created
            # points get the full stamp; dedup hits get stamp-when-absent
            # (a batch-less pre-existing point ACQUIRES the bundle's batch_id on
            # its first dedup-hit — E2E-10 row 14 — and a crash between
            # create_point and the stamp, position (f), is completed on retry).
            # _stamp_batch_id itself decides: full stamp vs (h) record-repair
            # vs no-op (a point already stamped with a DIFFERENT batch_id keeps
            # it — dedup never rewrites provenance).
            self._stamp_batch_id(pid, batch_id, content_hash=_content_hash(content))
            if existed:
                deduped["points"] += 1
            else:
                created["points"] += 1
            if results is not None:
                results.append({"section": "points", "index": i, "ref": ref,
                                "item": item, "result": point,
                                "deduped": bool(existed)})

        # ── 3. Entities (subject/object resolve-or-mint on the name;
        # event/document append) ─
        for i, item in enumerate(bundle.get("entities") or []):
            viols = []
            self._check_item_shape("entities", i, item, viols)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            item = dict(item)
            ref = item.pop("ref", None)
            etype = _type_text(item.pop("type", None))
            name = item.pop("name", None)
            # Entity props that wire edges may address earlier bundle items
            # by local ref (points + entities are registered by now).
            for key in ("authoredBy", "ownedBy", "managedBy",
                        "aboutSubject", "aboutObject", "aboutPoint",
                        "aboutDocument"):
                if isinstance(item.get(key), str) and item[key] in refs:
                    item[key] = refs[item[key]]
            if etype == "subject":
                # ── #9964 review: probe the label the WRITE lands on, not the one
                # the item declared (the door re-labels by the kind). See
                # `_existing_entity_ids`.
                existed = _existing_entity_ids(proj.g, etype, item, name)
                node = self.create_subject(name, **item)
                canonical = node.get("id") or name
            elif etype == "object":
                existed = _existing_entity_ids(proj.g, etype, item, name)
                node = self.create_object(name, **item)
                canonical = node.get("id") or name
            elif etype == "event":
                event_kind = item.pop("eventKind", None)
                node = self.create_event(name, event_kind, **item)
                canonical = node.get("eventId") or node.get("id") or name
                existed = []  # Event records are append-only — never deduped
            elif etype == "document":
                doc_kind = item.pop("documentKind", None)
                node = self.create_document(name, doc_kind, **item)
                canonical = node.get("id") or name
                existed = []  # Document records are append-only — never deduped
            else:
                raise Phase2Error(
                    f"ingest: entities[{i}] type must be "
                    f"{'|'.join(_ENTITY_ITEM_LABELS)}, got {etype!r}",
                    batch_id=batch_id,
                )
            if ref:
                _register_ref(ref, canonical, "entities")
            ids["entities"].append(canonical)
            if existed:
                deduped["entities"] += 1
            else:
                created["entities"] += 1
            if results is not None:
                results.append({"section": "entities", "index": i, "ref": ref,
                                "item": item, "result": node,
                                "deduped": bool(existed)})

        # ── 4. Connections (nodes exist — resolve refs, apply reification) ──
        # Phase-2 defense-in-depth: the shared contract helper + a live
        # endpoint re-verify run right before each write — a node deleted/
        # superseded between Phase-1 validation and this write is the race
        # class the re-check exists for (same helpers, same messages — parity).
        for i, conn in enumerate(bundle.get("connections") or []):
            viols = []
            self._check_item_shape("connections", i, conn, viols)
            # (#9173) Phase 2 has the resolved ids, so the multi-item arm is
            # judged by the route the write below will actually take — the
            # same answer Phase 1 read from `_effective_connection_routes`.
            # Passing the REQUEST here would make Phase 2 refuse a bundle
            # Phase 1 accepted (the parity break this parameter closes).
            # Resolved ONCE per connection and handed to both Phase-2 checks
            # and to the write branch below: `_effective_route` is a graph
            # read (`_point_bearing_ids`), and the three call sites ask the
            # same question about the same ids.
            _eff = None
            if isinstance(conn, dict) and "operator" in conn:
                _to_v = conn.get("to")
                _ids = [refs.get(x, x) if isinstance(x, str) else x
                        for x in [conn.get("from"),
                                  *(_to_v if isinstance(_to_v, list)
                                    else [_to_v])]]
                _eff = self._effective_route(conn, _ids)
            self._check_connection(i, conn, viols, _eff)
            self._check_endpoint_race(i, conn, refs, viols, _eff)
            if viols:
                raise Phase2Error(viols[0]["message"], batch_id=batch_id)
            src = refs.get(conn["from"], conn["from"])
            to_list = conn["to"] if isinstance(conn["to"], list) else [conn["to"]]
            dsts = [refs.get(x, x) for x in to_list]
            if "operator" in conn:
                # ── #9750: de-duplicate the RESOLVED targets, by id ─────────
                # Two different spellings can name ONE node (a bundle-local ref
                # and the id it minted, or two items `create_point`'s content
                # dedup folded onto one id), so this cannot be a Phase-1
                # ref-level comparison — Phase 1 has no ids (`_check_connection`
                # says so itself). The dedup therefore belongs HERE, at the seam
                # that has just turned the caller's spellings into identities,
                # and it must run BEFORE `_find_operator` below: that query's
                # dedup key is `size(targets) = size($inputs)`, so handing it
                # the caller's multiplicity computes the key over a list the
                # graph no longer holds — and a re-ingest would stop matching
                # its own run-1 operator and mint a duplicate.
                #
                # ⛔ Scoped to the OPERATOR route on purpose. The relation route
                # fans out over `dsts` (`extractedFrom`) and its response
                # `to` shape is a stable contract field; de-duplicating there
                # would change a documented shape, which this issue is not.
                _distinct_dsts = _distinct_preserving_order(dsts)
                # (#9750 review round 1, P2) THE WARNING IS EMITTED AFTER THE
                # ROUTE IS KNOWN, further down: on the operator-less DIRECT
                # route nothing is "collapsed" into a per-target edge (that
                # writer reads `dsts[0]` and writes ONE direct edge, no operator
                # and no `INPUT` mirror), so the operator wording would describe
                # a graph that route never produces.
                _collapsed = [
                    str(d) for d in _distinct_dsts if dsts.count(d) > 1]
                dsts = _distinct_dsts
                op_type = conn["operator"]
                label = conn.get("label")
                direction = conn.get("direction")
                # §8 connection-routing (A3, #1053): a PLAIN IMPL/NAND
                # connection (route 'direct' — no reify/mitigation) is
                # operator-less per §8 — route to create_direct_edge, carrying
                # label/direction/batch_id on the EDGE (no operator node). The
                # promotion SET fires only on created==True (the promotion-on-
                # created-only pin, §5.3/CYCLE-24) — the bare-MERGE dedup hit
                # applies NO promotion.
                # ── ENDPOINT-KIND GATE (owner ruling 2026-10-13, #8499) ──
                # The route selector reads only the connection ITEM; the
                # endpoint kinds are knowable only HERE, refs resolved. A
                # Point-involving pair is operator-mediated by contract, so a
                # caller who wrote the plain spelling gets the operator form
                # plus a warning naming what happened — never a silent
                # structure change, and never a silent operator-less edge.
                #
                # NOTE (why the 'direct' branch below is RETAINED and now
                # REACHABLE): #7925 aligned ingest's endpoint contract with the
                # write primitives it dispatches to, so a plain IMPL/NAND whose
                # endpoints carry NO plain :Point — the Object↔Object
                # carve-out, or any Subject/Object/Event pair — takes this
                # branch and is written operator-less by
                # `create_direct_edge`. The gate above is what keeps that
                # correct: it is False for those pairs (no Point-bearing
                # endpoint) and True for any Point-bearing one, so the
                # operator-mediated structure is chosen where the ruling
                # requires it.
                _request_route = self._connection_route(conn)
                _route = (_eff if _eff is not None
                          else self._effective_route(conn, [src, *dsts]))
                if _collapsed:
                    # Warn-not-block (#1934's ruling, this issue's §3.2 key 13):
                    # the caller asked for N list entries and the write produces
                    # one edge per DISTINCT target, so name what collapsed
                    # rather than silently answering a different question — and
                    # describe the graph THIS route actually writes (the direct
                    # route takes one target and has no operator and no INPUT
                    # mirror, so the operator sentence would be false there).
                    # (review round 2, P3) The WHOLE sentence is branched: the
                    # operator route's tail about a preserved positional `idx`
                    # is false on the direct route, which writes no `idx` at all
                    # — and a warning that contradicts itself is worse than no
                    # warning.
                    # (#10305, text P2) …and the edge-count promise must not
                    # READ as one-edge-per-target when the collapsed target IS
                    # the source: `{from: q1, to: ["q2", "q2"]}` where `q2`
                    # folds onto `q1` writes TWO typed + TWO INPUT edges over
                    # ONE id (`idx` 0 and 1 — the deliberate self-support slot,
                    # §5.5), so the sentence names that slot instead of leaving
                    # a reader to infer a count the graph does not hold.
                    _effect = (
                        "one connection writes ONE typed edge and ONE INPUT "
                        "edge per DISTINCT target (the operator's input set "
                        "is a set, #9750); the repeated spelling was collapsed "
                        "with the order preserved, so the positional idx is "
                        "unchanged. One exception: a target that resolves onto "
                        "the SOURCE keeps its OWN slot, so that self-support "
                        "shape writes two typed and two INPUT edges over that "
                        "one id (idx 0 and 1)"
                        if _route != "direct" else
                        "a direct edge takes ONE target, so the repeated "
                        "spelling was ignored and the write is identical to "
                        "the singular spelling (#9750)"
                    )
                    self._append_warning(
                        "duplicate_target_deduped:"
                        + ",".join(_collapsed)
                        + f":{op_type} — the connection's 'to' list named the "
                        f"same target more than once; {_effect}."
                    )
                if _request_route == "direct" and _route == "operator":
                    self._append_warning(
                        f"point_mediated_route:{src}->{dsts[0]}:{op_type} — a "
                        f"plain IMPL/NAND connection between :Point endpoints is "
                        f"operator-mediated (owner ruling 2026-10-13, #8499): an "
                        f"operator-less edge can be neither mitigated nor "
                        f"annotated, and cannot carry the epistemic weighting a "
                        f"Point's relations claim. Reified automatically; pass "
                        f"`reify: true` to make the intent explicit."
                    )
                if _route == "direct":
                    edge_res = self.create_direct_edge(
                        op_type, src, dsts[0],
                        label=label,
                        direction=direction,
                        # #9749: the §8 attributes this route is the ONLY home
                        # for (INGEST_CONTRACT §13 row 4) were validated here
                        # and then dropped — `create_direct_edge` has always
                        # accepted both and wrote them on the edge (and the
                        # `DirectEdgeCreated` journal descriptor replays them),
                        # so the omission in THIS call was the whole defect. A
                        # connection reaching this branch is non-Point-bearing,
                        # so nothing upstream refuses them.
                        confidence=conn.get("confidence"),
                        weight=conn.get("weight"),
                        batch_id=batch_id,
                        promote_source=(promotion_policy == "auto"),
                    )
                    created_flag = bool(edge_res.get("created"))
                    if created_flag:
                        created["connections"] += 1
                    else:
                        deduped["connections"] += 1
                    conn_result = {"direct_edge": op_type, "from": src,
                                   "to": dsts[0], "deduped": not created_flag}
                    # §6.1 descriptor: the ids["connections"] entry is the
                    # STABLE shape (no deduped — aggregated in
                    # deduped.connections) so identical-resubmission equality
                    # holds; deduped lives only in the granular conn_result
                    # (plan §5.5 route matrix, cycle-22).
                    ids["connections"].append(
                        {"direct_edge": op_type, "from": src, "to": dsts[0]})
                    if results is not None:
                        results.append({"section": "connections", "index": i,
                                        "ref": conn.get("ref"), "item": conn,
                                        "result": conn_result,
                                        "deduped": bool(conn_result.get("deduped"))})
                    continue
                # (#9750 review round 1, P2) the REQUEST is handed over as its
                # real SLOT list, `[src, *dsts]` — `_find_operator` normalizes
                # `$inputs` to the DISTINCT set itself (one place, every caller),
                # so both sides of `size(DISTINCT targets) = size($inputs)` count
                # the same thing even when a target resolved onto the source's
                # own node. ⛔ Do NOT pre-dedupe here: the repeat is exactly what
                # tells that query the request is INEXPRESSIBLE as an edge
                # completion (`written` is an id set), and pre-deduping hid it
                # into a partial-absorb that landed an `idx` GAP.
                # The EDGE SHAPE is untouched — `create_operator` writes its own
                # `inputs`, which keep the deliberate self-support slot.
                existing = self._find_operator(
                    op_type, [src, *dsts],
                    label=label, direction=direction)
                if existing is not None and existing.get("kind") == "exact":
                    oid = existing["id"]
                    # k=N boundary (cycle-15): re-apply promotion when the
                    # exact-hit is NULL-status under auto (a crash after the
                    # full input-edge loop, before the promotion SET).
                    if (promotion_policy == "auto"
                            and existing.get("status") is None):
                        try:  # noqa: SIM105
                            self._reapply_operator_promotion(oid)
                        except Exception:  # noqa: BLE001, RUF100
                            pass
                    deduped["connections"] += 1
                    conn_result = {"operator_id": oid, "deduped": True}
                elif existing is not None and existing.get("kind") == "partial":
                    # partial-absorb (cycle-11): complete the written input
                    # set to the full set via IDEMPOTENT bare-MERGE + SET per
                    # input (never the raw-CREATE loop), then proceed to
                    # promotion under auto.
                    oid = existing["id"]
                    written = set(existing.get("written") or [])
                    # #2351: the SAME override guard as ``create_operator``.
                    # This branch completes an existing operator's input set
                    # with a raw edge MERGE — a second door onto the exact edge
                    # shape ``create_operator`` refuses to write. Without the
                    # guard here a DERIVED source gets wired above an ASSERTED
                    # input and the caller is told the connection succeeded,
                    # while the module's stated invariant is that the write
                    # path refuses such an edge outright.
                    # ``classes_for_ids`` is :Point-anchored, so an id absent
                    # from it has NO class — it is not a claim (:Subject/
                    # :Object/:Event), and absence must not read as "asserted".
                    # This is the same parity the other two doors get from
                    # their label check: the guard applies only when the SOURCE
                    # is a claim and at least one TARGET is one.
                    _absorb_classes = _claim_class.classes_for_ids(
                        proj.g, [src] + dsts)  # noqa: RUF005
                    _absorb_targets = set().union(
                        *(_absorb_classes.get(t, set()) for t in dsts)
                    ) if dsts else set()
                    if (op_type in _MECHANISM_OP_TYPES
                            and src in _absorb_classes
                            and _absorb_targets
                            and _claim_class.direction_blocks_create(
                                # ⛔ CANONICALIZE FIRST. `direction` here may be
                                # ABSENT, and absent is NOT the mutual default
                                # for every operator: NAND's absent direction is
                                # `unidirectional` (`_canonical_direction`), so
                                # judging the raw token treats a
                                # direction-omitting NAND as bidirectional and
                                # REFUSES a back-leg that never flows — a
                                # wiring `create_operator` accepts for the same
                                # edge shape (#2351 review). `_find_operator`
                                # canonicalizes for the same reason; the two
                                # doors must agree.
                                self._canonical_direction(op_type, direction),
                                _absorb_classes.get(src),
                                _absorb_targets)):
                        raise ValueError(_claim_class.override_refusal(
                            source_id=src, target_ids=list(dsts),
                            surface=f"ingest({op_type}) partial-absorb"))
                    _completed = 0
                    for _i, _inp in enumerate([src] + dsts):  # noqa: RUF005
                        if _inp in written:
                            continue
                        # IDEMPOTENT edge-completion (P0 fix, review gate): the
                        # ONE-STEP property-filtered MERGE
                        #   MERGE (o:Point {id:$oid})-[r:REL]->(t:Point {id:$inp})
                        # CREATES DUPLICATE NODES when the edge is absent (the
                        # FalkorDB quirk create_direct_edge's cycle-26 comment
                        # documents) — the completion would land on a fresh
                        # bare Point instead of the operator. Use the TWO-STEP
                        # pattern: MATCH both endpoints, then edge-only MERGE
                        # (with idx parity for create_operator's edge shape).
                        # #1919 (P1, review gate): the completion endpoints
                        # may be any of the four epistemic labels (#7902) — a
                        # Point-only MATCH would silently drop the typed edge
                        # for an absorbed Subject/Object/Event input (never
                        # converging to the dedup key). The reverse INPUT edge
                        # mirrors create_operator + the replay convention
                        # (fold-parity).
                        # The mechanism → storage-rel-type map lives with the
                        # potentials it dispatches (`ep` imports this package's
                        # graph layer, so the import stays local).
                        from tortoise.ep import MECHANISM_REL_TYPES as _MECHANISM_REL_TYPES
                        proj.g.query(
                            f"MATCH (o:Point {{id:$oid}}), (t) "
                            f"WHERE {epistemic_disjunction('t')} AND t.id = $inp "
                            # ⛔ The STORAGE edge, mapped exactly as
                            # `create_operator` writes it (#12067 review round 3):
                            # a coherence leg written as `hasPart` is an edge the
                            # factor builder (`MATCH (o)-[r:IMPL|NAND]->(c)`) never
                            # sees — the operator reports the leg as landed and it
                            # is INERT, the same class as the two replay maps.
                            f"MERGE (o)-[r:{_MECHANISM_REL_TYPES.get(op_type, 'hasPart')}]->(t) "
                            f"SET r.idx = $idx "
                            f"MERGE (t)-[:INPUT {{idx:$idx}}]->(o)",
                            params={"oid": oid, "inp": _inp, "idx": _i},
                        )
                        _completed += 1
                    # (#9750 review round 2, P3) COUNT THE WRITES, do not derive
                    # them: `len([src] + dsts) - len(written)` subtracts an id SET
                    # from a SLOT count, so a request that repeats an id (a target
                    # that resolved onto the source's own node) over-reports the
                    # completions in the §3.2 key-8 payload — it claimed 2 while
                    # exactly one edge had been written.
                    completed = _completed
                    if promotion_policy == "auto" and existing.get("status") is None:
                        try:  # noqa: SIM105
                            self._reapply_operator_promotion(oid)
                        except Exception:  # noqa: BLE001, RUF100
                            pass
                    deduped["connections"] += 1
                    conn_result = {"operator_id": oid, "deduped": True}
                    self._append_warning(
                        f"operator_absorb_completed:{oid}:{completed}")
                elif existing is not None and existing.get("kind") == "decline":
                    # TWO+ candidates → decline + partial_operator_residue
                    # warning; the retry creates a fresh complete operator.
                    self._append_warning(
                        "partial_operator_residue:"
                        + ",".join(existing.get("candidates") or []))
                    op = self.create_operator(op_type, src, dsts,
                                              label=label, direction=direction,
                                              promote_source=(promotion_policy == "auto"))
                    oid = op["id"]
                    created["connections"] += 1
                    conn_result = {"operator_id": oid, "deduped": False}
                else:
                    op = self.create_operator(op_type, src, dsts,
                                              label=label, direction=direction,
                                              promote_source=(promotion_policy == "auto"))
                    oid = op["id"]
                    created["connections"] += 1
                    conn_result = {"operator_id": oid, "deduped": False}
                # §4.2 (A4): operator Points are stamped POST-WRITE keyed on the
                # returned id (create_operator accepts no props). Stamp-when-
                # absent covers the operator-path crash boundary too (position
                # (g)): a retry's _find_operator dedup hit on a batch-less
                # operator Point acquires the bundle's batch_id.
                self._stamp_batch_id(oid, batch_id)
                # #5244: APPLY the connection's `mitigation` to the operator
                # this connection resolved to — created, deduped OR absorbed.
                # Until this call existed the field was read for ROUTING
                # (`_connection_route`), accepted and validated by
                # `_check_connection`, and then dropped: the caller got a real
                # operator, a success status, no mitigation Point, no
                # `(op:Point {is_operator:true})-[:mitigated_by]->(m:Point)`
                # edge and no warning. Applied AFTER the operator write (the
                # target id is only known here) and on EVERY submission, so a
                # re-submission re-applies too — `mitigate_operator` is
                # idempotent: it UPDATES the existing mitigation in place,
                # never duplicates (INGEST_CONTRACT §3.2 key 6). #9962: a
                # mitigation the graph records as WITHDRAWN is neither
                # revived nor re-minted here — a redelivery is not a
                # re-assertion (see `_apply_ingest_mitigation`). The helper
                # stamps the mitigation Point with this bundle's batch_id.
                mitigation = conn.get("mitigation")
                if isinstance(mitigation, dict):
                    self._apply_ingest_mitigation(oid, mitigation, batch_id)
                ids["connections"].append(oid)
            else:
                rel = conn["relation"]
                if rel == "extractedFrom":
                    # (Point)-[:extractedFrom]->(Source) — MERGE-based, so
                    # re-ingest is safe. Source side resolves by url/ref.
                    # #3263 (re-review P2): `to` may be multi-valued — a claim
                    # extracted from several sources. Fan out over EVERY target;
                    # handling only dsts[0] silently dropped the rest and
                    # undercounted created/deduped, on the surface this change
                    # set amended to many→many.
                    first_existed = None
                    for dst in dsts:
                        existed = proj.g.query(
                            "MATCH (n:Point {id:$pid})-[:extractedFrom]->"
                            "(s:Source {url:$url}) RETURN count(*)",
                            params={"pid": src, "url": dst},
                        ).result_set
                        hit = bool(existed and existed[0][0])
                        if hit:
                            deduped["connections"] += 1
                        else:
                            proj._link_source(src, dst)
                            created["connections"] += 1
                        if first_existed is None:
                            first_existed = hit
                    # Preserve the historical shape for the scalar case so
                    # existing consumers/tests see an unchanged `to`.
                    conn_result = {
                        "relation": rel, "from": src,
                        "to": dsts[0] if len(dsts) == 1 else list(dsts),
                        "deduped": bool(first_existed),
                    }
                else:
                    existed = proj.g.query(
                        f"MATCH (a)-[r:{rel}]->(b) "
                        "WHERE (a.id = $f OR a.eventId = $f OR a.url = $f) "
                        "AND (b.id = $t OR b.eventId = $t OR b.url = $t) "
                        "RETURN count(r)",
                        params={"f": src, "t": dsts[0]},
                    ).result_set
                    try:
                        ok = proj.create_edge(src, dsts[0], rel)
                    except ValueError as e:
                        # Only `aboutDocument` has a target contract, so only
                        # that refusal is re-shaped — `create_edge` raises
                        # ValueError for other invalid requests too (an unknown
                        # predicate; the `ownedBy` circular-DAG guard), and
                        # those must keep propagating unchanged.
                        if rel != "aboutDocument":
                            raise
                        # #5206: the refusal must reach the caller as this
                        # contract's own Phase2Error, not as a projection
                        # primitive's ValueError. NB the bundle is NOT rolled
                        # back: Phase 2 has already written its points and
                        # sources — the same pre-existing behaviour as the
                        # `endpoints not found` branch below.
                        raise Phase2Error(
                            f"ingest: connections[{i}] could not create "
                            f"{rel!r} edge — {e}", batch_id=batch_id) from e
                    if not ok:
                        raise Phase2Error(
                            f"ingest: connections[{i}] could not create "
                            f"{rel!r} edge — endpoints not found"
                        , batch_id=batch_id)
                    if existed and existed[0][0]:
                        deduped["connections"] += 1
                    else:
                        created["connections"] += 1
                    conn_result = {"relation": rel, "from": src, "to": dsts[0],
                                   "deduped": bool(existed and existed[0][0])}
                ids["connections"].append(conn_result)
            if results is not None:
                results.append({"section": "connections", "index": i,
                                "ref": conn.get("ref"), "item": conn,
                                "result": conn_result,
                                "deduped": bool(conn_result.get("deduped"))})

        # ── Nudges (write nudges, PR #912): populated once W2's
        # _nudge_candidates lands — advisory only, never enforced.
        nudges: list[dict] = []
        if hasattr(self, "_nudge_candidates"):
            try:
                for pid in ids["points"]:
                    nudges.extend(self._nudge_candidates(
                        "related", exclude_ids=[pid])[:2])
            except Exception:
                pass  # nudges are advisory — never fail the ingest

        out = {
            "granularity": granularity,
            "batch_id": batch_id,
            "created": created,
            "deduped": deduped,
            "ids": ids,
            "nudges": nudges,
            "warnings": warnings,
        }
        if results is not None:
            out["results"] = results
        self._ingest_warnings = None
        return out
