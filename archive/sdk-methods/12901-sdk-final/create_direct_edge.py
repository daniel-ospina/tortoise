# Archived sdk-method `create_direct_edge` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_create_direct_edge`; recorded destination `link_entities` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'create_direct_edge'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    @_declares_apply_first_write_lane("create_direct_edge")
    def create_direct_edge(self, op_type: str, source_id: str, target_id: str, *,
                           direction: str | None = None,
                           confidence: float | None = None,
                           weight: float | None = None,
                           label: str | None = None,
                           batch_id: str | None = None,
                           promote_source: bool = True) -> dict:
        """Create an OPERATOR-LESS direct IMPL/NAND epistemic edge (plan §5.3).

        Ontology v3.5 §8 / v3.8: plain IMPL/NAND connections are direct edges
        (edge-carried direction/confidence/weight/label/batch_id; NO operator
        node). The edge is a BARE-pattern MERGE + attribute SET — exactly one
        edge per (src,tgt,type), last-writer-wins on attribute change
        (MERGE-with-attributes would create a parallel edge on attribute
        change, violating EP's no-parallel-direct-edges contract).

        #7902: endpoints may carry ANY of the four epistemic labels —
        :Point/:Subject/:Object/:Event (owner ruling #7813; direct edges are
        the operator-less half of the same epistemic layer the operator path
        bridges, so the label set is identical). The resolved label anchors
        each id-filtered query so the per-(label, id) index is kept.

        #7925: ``ingest()``'s endpoint validation admits the same four labels
        on this route, so the two write surfaces agree about the same edge —
        before #7925 this primitive wrote a (Subject)-[:IMPL]->(Object) edge
        that ``ingest()`` then refused with ``must be a plain Point``.
        ``ingest()`` adds the terminal-endpoint guard below PLUS the
        Point-mediated route gate (#8499).

        Guards (shared with #901, E2E-11.7):
          - endpoints exist AND are plain epistemic nodes (a Source or an
            operator endpoint is a typed error);
          - terminal-endpoint guard: `status NOT IN {superseded, retracted}`
            (a direct edge incident to a terminal point would recreate the
            terminal-point propagation hazard).
        op_type ∈ {IMPL, NAND}.

        Promotion (CYCLE-24 promotion-on-created-only pin): `promote_source`
        fires ONLY when the MERGE CREATED the edge (`created==True`) — a
        bare-MERGE dedup hit applies NO promotion (dedup never rewrites).
        CYCLE-25: direction-absent NAND defaults to "unidirectional" on the
        edge (extraction default — new-claim-attacks-existing); direction-
        absent IMPL stays "bidirectional".

        Emits the dedicated JSONL edge descriptor for rebuild durability
        (#8499's pass-2b consumer) on EVERY write, NOT only on
        ``created==True``: a dedup hit re-emits, so the crash window between
        the MERGE and the emission is recovered by the retry's emission. (The
        ``promote_source`` pin above is a separate, created-only rule.)

        Returns {"direct_edge": op_type, "from": source_id, "to": target_id,
                 "created": bool, "deduped": bool}.
        """
        if op_type not in ("IMPL", "NAND"):
            raise ValueError(
                f"create_direct_edge: op_type must be IMPL or NAND, got {op_type!r}"
            )
        if source_id == target_id:
            raise ValueError("create_direct_edge: source_id and target_id must differ")
        if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
            raise ValueError(
                f"create_direct_edge: confidence must be in [0,1], got {confidence!r}"
            )
        # CYCLE-25: NAND extraction default — direction-absent NAND is
        # "unidirectional" (new-claim-attacks-existing); IMPL stays
        # bidirectional. An explicit value is preserved verbatim — the model's
        # four Layer-1 tokens (`->` / `<-` / `<->` / `-`) plus the two legacy
        # aliases (#7852). The edge stores what the caller declared; every
        # reader normalizes through `live.direction_flows`.
        if direction is None:
            direction = "unidirectional" if op_type == "NAND" else "bidirectional"
        if direction not in ACCEPTED_DIRECTION_VALUES:
            raise ValueError(
                f"create_direct_edge: direction must be one of "
                f"{', '.join(sorted(ACCEPTED_DIRECTION_VALUES))}, got {direction!r}"
            )

        # #4647: confidence/weight are narrowed through ``float()`` below, so an
        # inexact Decimal or an out-of-domain int is silently altered here just
        # as on the props surface. Guard the CALLER's values before the coercion.
        _reject_unrepresentable_number("confidence", confidence)
        _reject_unrepresentable_number("weight", weight)

        proj = self._get_proj()
        # Endpoint validation: exist, plain epistemic nodes, non-terminal.
        # #2422 (second-model gate): the terminal set derives from the shared
        # EP/read vocabulary AND the legacy outdated=true flag is rejected —
        # a flag-outdated point (status untouched by invalidate_point) must
        # equally refuse new direct edges.
        #
        # #7902: the endpoint may carry ANY of :Point/:Subject/:Object/:Event
        # (owner ruling #7813 — direct edges are the operator-less epistemic
        # edge shape, so the same four labels apply). The probe is id-anchored:
        # sweep the labels once each (index-preserving) and remember WHICH
        # label resolved each id so the MERGE below can anchor on it.
        found: dict[str, tuple[str, bool, str | None, bool]] = {}
        # #2351 (epic #2349 E-derived): the same probe carries each endpoint's
        # claim class, so the override guard costs NO extra round trip. Classes
        # are aggregated across every row an id resolves to (the #7822 fan-out
        # rule); an absent marker reads as "asserted".
        endpoint_classes: dict[str, set[str]] = {}
        for _label in EPISTEMIC_LABELS:
            rows = proj.g.query(
                f"MATCH (p:{_label}) WHERE p.id IN $ids "
                "RETURN p.id, coalesce(p.is_operator, false), p.status, "
                "coalesce(p.outdated, false), "
                "coalesce(p.claimClass, 'asserted')",
                params={"ids": [source_id, target_id]},
            ).result_set
            for r in rows:
                # First label wins for a (hypothetical) multi-labelled node;
                # the terminal/operator guards below are label-independent.
                found.setdefault(r[0], (_label, bool(r[1]), r[2], bool(r[3])))
                # ⛔ ``claimClass`` is a :Point property: a non-Point endpoint
                # is not a claim, so its absence must NOT read as "asserted"
                # (that refused a DERIVED point wired to an Object — a shape
                # #7902 made writable; #2351 review).
                if _label == "Point":
                    endpoint_classes.setdefault(r[0], set()).add(r[4])
        for pid in (source_id, target_id):
            if pid not in found:
                raise ValueError(
                    f"create_direct_edge: endpoint {pid!r} does not exist or "
                    f"is not an epistemic node (Point/Subject/Object/Event)"
                )
            _label, is_op, status, outdated = found[pid]
            if is_op:
                raise ValueError(
                    f"create_direct_edge: endpoint {pid!r} is an operator — "
                    f"direct edges connect plain epistemic nodes only"
                )
            # Mirror of `_endpoint_is_terminal` (the ingest guards' prediction
            # of THIS test): if either half of this composition moves, Phase 1
            # starts predicting a raise that never happens (under-refusal) or
            # keeps accepting an endpoint this raises on (mid-write). Keep the
            # two in step; `tests/test_ingest_event_endpoint.py` drives both
            # surfaces over one fixture.
            if status in TERMINAL_EXCLUDED_STATUSES or outdated:
                marker = "outdated" if outdated and (
                    status not in TERMINAL_EXCLUDED_STATUSES) else (status or "live")
                raise ValueError(
                    f"create_direct_edge: endpoint {pid!r} is terminal "
                    f"({marker}) — a direct edge incident to a terminal "
                    f"point is rejected (terminal-point propagation hazard, "
                    f"#2422)"
                )
        # The resolved labels anchor the id-filtered edge queries (#7902) so
        # the per-(label, id) index is kept — a bare `MATCH (a {id:$x})` would
        # full-scan. See live.epistemic_label_queries.
        label_src = found[source_id][0]
        label_tgt = found[target_id][0]

        # #2351: the override guard — a DERIVED source claim may not IMPL-cascade
        # above (or NAND-attack) an ASSERTED target. Raised AFTER the endpoint
        # guards above and BEFORE the MERGE, so a refused call writes nothing.
        # #2351: the guard applies only when BOTH endpoints are claims (:Point).
        # A non-Point endpoint carries no ``claimClass``, so it can neither be
        # overridden nor override — skipping it is what keeps #7902's typed
        # wirings writable.
        _tgt_classes = (endpoint_classes.get(target_id)
                        if found.get(target_id, (None,))[0] == "Point" else None)
        if (found.get(source_id, (None,))[0] == "Point" and _tgt_classes
                and _claim_class.direction_blocks_create(
                    direction,
                    endpoint_classes.get(source_id),
                    _tgt_classes)):
            raise ValueError(_claim_class.override_refusal(
                source_id=source_id, target_ids=[target_id],
                surface=f"create_direct_edge({op_type})"))

        # ── The SAME closed-set invariant, from the other door (#12067) ───────
        # `create_operator` refuses to put a second mechanism on a pair, and
        # this door writes the OPERATOR-LESS half of the same epistemic layer:
        # a bare `:IMPL` beside a NAND operator is two mechanisms on one pair
        # exactly as a NAND operator beside an IMPL one is. Without this arm the
        # invariant was enforceable in only ONE direction — measured: create an
        # IMPL operator, then `create_direct_edge("NAND", a, b)` was accepted,
        # producing the state the refusal exists to make unwritable (#12067
        # review, P1). `coherence` is included through its OWN spelling: it is
        # stored as `:IMPL` but it is a DIFFERENT mechanism, so an operator
        # declaring it conflicts with a bare IMPL edge.
        for _kind, _otype, _pair, _oid in _mechanism_pair_conflicts(
                proj, [(source_id, target_id)], op_type):
            if _kind != "operator":
                continue
            raise ValueError(
                f"create_direct_edge: the pair {[source_id, target_id]} already "
                f"carries the {_otype!r} mechanism (operator {_oid!r}), so it "
                f"cannot also carry a {op_type!r} direct edge (#12067 — the "
                f"mechanism set is CLOSED and a pair carries at most one). "
                f"Delete the operator first if the pair genuinely changed "
                f"meaning, or state the second intent against a different "
                f"claim."
            )

        # BARE-pattern MERGE + attribute SET (last-writer-wins; exactly one
        # edge per (src,tgt,type)). `created` is detected by a pre-count
        # BEFORE the MERGE (the MERGE's stats aren't reliably surfaced, and a
        # post-MERGE count always sees 1).
        rel_type = op_type  # IMPL / NAND
        pre = proj.g.query(
            f"MATCH (a:{label_src} {{id:$src}})-[r:{rel_type}]->"
            f"(b:{label_tgt} {{id:$tgt}}) "
            f"RETURN count(r)",
            params={"src": source_id, "tgt": target_id},
        ).result_set
        created = (pre[0][0] == 0)
        # Two-step MERGE (FalkorDB quirk fixed, cycle-26): a MERGE whose node
        # patterns carry property filters can CREATE DUPLICATE NODES when the
        # relationship is absent (the node match is ambiguous). MATCH the
        # existing nodes first, then MERGE the edge BETWEEN them — exactly one
        # edge per (src,tgt,type), no duplicate nodes.
        proj.g.query(
            f"MATCH (a:{label_src} {{id:$src}}), (b:{label_tgt} {{id:$tgt}}) "
            f"MERGE (a)-[r:{rel_type}]->(b)",
            params={"src": source_id, "tgt": target_id},
        )
        attrs = {"direction": direction}
        if confidence is not None:
            attrs["confidence"] = float(confidence)
        if weight is not None:
            attrs["weight"] = float(weight)
        if label is not None:
            attrs["label"] = label
        if batch_id is not None:
            attrs["batch_id"] = batch_id
        # SET r += $attrs (additive — never clobbers EP-managed msg_* fields)
        proj.g.query(
            f"MATCH (a:{label_src} {{id:$src}})-[r:{rel_type}]->"
            f"(b:{label_tgt} {{id:$tgt}}) "
            f"SET r += $attrs",
            params={"src": source_id, "tgt": target_id, "attrs": attrs},
        )

        # Promotion-on-created-only (CYCLE-24 pin): the guarded #131-style SET
        # fires ONLY when the MERGE created the edge.
        if created and promote_source:
            # #1088: only a never-set status is filled in with the live
            # default; an explicitly-drafted source is never moved to live
            # by this command.
            proj.g.query(
                "MATCH (s:Point {id:$id}) "
                "WHERE s.status IS NULL "
                "SET s.status = 'live', s.updatedAt = $now",
                params={"id": source_id,
                        "now": _now_iso()},
            )

        # JSONL edge descriptor (plan §4.4) — emitted on EVERY write (the
        # MERGE-keyed replay is idempotent, so dedup-hit emissions are
        # harmless, and the crash window closes: a crash between the MERGE
        # and the emission on attempt 1 is recovered by attempt 2's emission —
        # a created-only gate would lose the edge from the log forever on
        # crash-retry). REVIEW-FIX P1 (cycle-26): last-writer-wins attr
        # updates must replay the FINAL attrs post-rebuild.
        self._emit_event(
            "DirectEdgeCreated",
            id=f"{source_id}->{target_id}:{op_type}",
            src=source_id, tgt=target_id, edge_type=op_type,
            direction=direction,
            **({"confidence": float(confidence)} if confidence is not None else {}),
            **({"weight": float(weight)} if weight is not None else {}),
            **({"label": label} if label is not None else {}),
            **({"batch_id": batch_id} if batch_id is not None else {}),
        )

        self._mark_dirty([source_id, target_id])
        # Epic 903-C4 (#1242): a new direct edge changes the factor graph —
        # invalidate warm-start seeds on the endpoints' edges.
        self._get_ep().invalidate_messages([source_id, target_id])
        return {"direct_edge": op_type, "from": source_id, "to": target_id,
                "created": created, "deduped": not created}
