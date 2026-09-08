"""Shared payload-operator application (#1532 D3).

Both the derived-commit endpoint (``hosted_api._execute_commit_writes`` §7)
and the capture path (``sdk._extract_session_v2``) write Layer-1 payload
operators with IDENTICAL commit semantics — IMPL/NAND first via
``sdk.create_operator`` (promote_source=False, #780), then MITIGATES via
``sdk.mitigate_operator`` with the same same-commit-map → Cypher-fallback →
deep-miss-drop resolution. Extracted here so the two write paths cannot
drift again (the commit endpoint used to apply them inline and the capture
path applied none — a parity hole the issue names).

The helper consumes RAW payload operator dicts (the exact
``extractor_v2.execute_embed`` shape: ``{src, dst, op_type, target,
strength}``) OR ``commit_schema`` Operator models (the commit reconcile
records) — field access is normalized for both.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# Statuses excluded from recall_state's default OBJECT view (the #1350 fold
# consumer). Mirrors the literal exclusion tuple in TortoiseSDK.recall_state
# (sdk.py — "(o.get('status') or '') not in (superseded, deprecated,
# archived, retracted)"). NOTE: this is NOT TortoiseSDK.STATE_EXCLUDED_STATUS
# (a class attr missing 'archived' and used for the POINT pool) and NOT
# search_engine.TERMINAL_EXCLUDED_STATUSES (adds 'outdated', which recall's
# object view DOES surface). Keep in sync with the recall_state filter — a
# supersession fold is only valid when a successor VISIBLE to that view
# remains.
_RECALL_OBJECT_EXCLUDED_STATUS = frozenset(
    {"superseded", "deprecated", "archived", "retracted"})


def _op_attr(op, name, default=None):
    """Field access for raw payload dicts OR commit_schema Operator models."""
    if isinstance(op, dict):
        return op.get(name, default)
    return getattr(op, name, default)


def _sr_attr(record, name, default=None):
    """Field access for raw supersession dicts OR commit_schema
    SupersessionRecord models (``.superseded`` / ``.supersedes_by`` /
    ``.evidence``) — same normalization as ``_op_attr``."""
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _target_attr(target, name, default=None):
    """Field access for an OperatorTarget model OR a raw target dict."""
    if isinstance(target, dict):
        return target.get(name, default)
    return getattr(target, name, default)


def _payload_point_content_by_id(payload: dict, pid: str) -> str:
    """Dict-payload twin of hosted_api._point_content_by_id — the capture
    payload is a raw dict (the commit endpoint's is a CommitPayload model)."""
    for pt in payload.get("points", []) or []:
        if pt.get("id") == pid:
            return str(pt.get("content", ""))
    return ""


def apply_payload_operators(proj, sdk, operators: list, *,
                            point_content_by_id=None) -> None:
    """Apply Layer-1 payload operators with commit semantics (#1532 D3).

    IMPL/NAND first via ``sdk.create_operator`` (promote_source=False, #780);
    MITIGATES second via ``sdk.mitigate_operator`` — mitigation Point +
    (m)-[:IMPL]->(op) + (op)-[:mitigated_by]->(m), strength in [0.10, 0.50]
    (advisory metadata: strength = how much the reason reduces the edge, NOT
    how true it is; EP does not read it — the mitigation point's own Beta
    prior is what EP consumes, #2199 knock-on decision 3).
    Deep-miss (target IMPL edge absent) -> logged warning, mitigation dropped
    (support-edge-first convention, DE2E-11 negative). Never raises on a
    missing target. ``point_content_by_id(pid) -> str`` supplies the
    mitigation reason's content fallback when provided.
    """
    target_op_ids: dict[tuple, str] = {}
    for op in operators:
        op_type = _op_attr(op, "op_type")
        if op_type == "MITIGATES":
            continue
        src, dst = _op_attr(op, "src"), _op_attr(op, "dst")
        if not op_type or not src or not dst:
            _logger.warning(
                "operator write skipped (inputs missing?): %r", op)
            continue
        try:
            result = sdk.create_operator(
                op_type, src, [dst],
                direction=_op_attr(op, "direction") or "unidirectional",
                promote_source=False,
            )
        except ValueError as e:
            _logger.warning(
                "operator write skipped (inputs missing?): %s", e)
            continue
        target_op_ids[(src, dst, op_type)] = result["id"]
    for op in operators:
        if _op_attr(op, "op_type") != "MITIGATES":
            continue
        t = _op_attr(op, "target")
        src = _op_attr(op, "src")
        if t is None:
            _logger.warning(
                "MITIGATES operator %r has no target — dropped", src)
            continue
        t_src, t_dst = _target_attr(t, "src"), _target_attr(t, "dst")
        t_op_type = _target_attr(t, "op_type") or "IMPL"
        op_id = target_op_ids.get((t_src, t_dst, t_op_type))
        if op_id is None:
            rows = proj.g.query(
                "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
                "MATCH (o)-[:IMPL {idx:0}]->(s) WHERE (s:Point OR s:Event) AND s.id = $src "
                "MATCH (o)-[:IMPL {idx:1}]->(d) WHERE (d:Point OR d:Event) AND d.id = $dst "
                "RETURN o.id LIMIT 1",
                params={"src": t_src, "dst": t_dst},
            ).result_set
            op_id = rows[0][0] if rows else None
        if op_id is None:
            # Deep-miss (DE2E-11 negative): the target IMPL edge is absent —
            # the mitigation must NOT attach (support-edge-first convention).
            _logger.warning(
                "MITIGATES target edge (%s,%s,IMPL) not found — "
                "mitigation dropped", t_src, t_dst)
            continue
        reason = point_content_by_id(src) if point_content_by_id else ""
        if not reason:
            reason = f"[MITIGATION] {src}"
        sdk.mitigate_operator(op_id, reason=reason,
                              strength=_op_attr(op, "strength") or 0.5)


# ── Supersession application (#2164 Task 3) ────────────────────────────
# Canonical supersession records flow from extractor_v2._supersession_records
# (``{"superseded", "supersedes_by", "evidence"}`` — refs are an entity id
# OR name; pt_<sha> refs are point content-addressed ids, dispatched by
# prefix) OR commit_schema.SupersessionRecord models (the commit reconcile
# records). Extracted here so capture (_extract_session_v2), eval ingest_v2,
# and the hosted commit endpoint (_execute_commit_writes §6b, migrated in
# #2193) share ONE consumer-side discipline.

def _supersession_fold_order(proj, records):
    """#2249: stable fold order for same-payload supersession chains.

    A same-payload chain (A→B and B→C in ONE payload) folds correctly only
    when A→B runs BEFORE B→C: B→C terminalizes B, and the fold-time
    visible-successor gate then skips A→B (its successor B is
    recall-excluded) leaving A live with its fold unjournaled. Payload
    emission order is NOT controllable (extractor embeds preserve LLM order;
    hosted §6b processes external client payloads verbatim) — reverse
    emission [B→C, A→B] is a natural outcome. This pre-pass returns an
    order in which every chain record folds while its successor is still
    live, making the end state order-INDEPENDENT.

    Mechanics: resolve, via TWO batched graph probes (never canonical-id
    math — legacy non-canonical-id carriers make obj-<sha26(name)> unsound;
    see test_apply_supersessions_chain_legacy_noncanonical_mid_node), the
    object each entity record's fold would terminalize (ref side, mirroring
    the loop's id-match-wins / single-name / never-guess discipline) and
    the id-carrying carriers under each successor name. Edge R→S when S's
    fold terminalizes an object in R's successor-candidate set. Stable Kahn
    (min-heap by original index) → fold order. Records that never fold
    (missing/self/pt_ lane), unresolved/ambiguous refs, and cycles + their
    transitive DEPENDENTS (records a cycle member points to) contribute no
    edges and keep PAYLOAD order (deterministic, reproduces pre-fix
    outcomes). Records that FEED a cycle (a cycle member is their
    successor) sort AHEAD of it — deterministic + monotonic (hoisting can
    only add folds; pinned by test_apply_supersessions_cycle_predecessor_hoisted).
    Self-referential successor names (a record's successor resolving back
    onto its OWN target via duplicate-name carriers) contribute no edges —
    the fold-time gate excludes the target itself (code-review P2-1). Edge
    creation excludes R's own target so a divergent same-target claim never
    hoists past its first-emitted sibling. Never raises on payload-content
    doubt — payloads with <2 entity records, unresolved/ambiguous refs, and
    cycles all fail soft to payload order; graph-QUERY failures propagate to
    the caller's try/except fallback (apply_supersessions), which preserves
    pre-fix per-record semantics. Id-less nodes can never be a VISIBLE
    successor (the fold-time gate requires an id), so chains through them as
    a middle/tail link are order-insensitive — but an id-less TARGET still
    participates as a needer of its own successor and sorts normally (pinned
    by test_apply_supersessions_chain_idless_target_converges). The main
    loop re-probes per record at fold time (its gates are STATUS-dependent —
    the pre-pass resolves structure only, so no TOCTOU: the Object graph is
    write-static inside apply_supersessions, entities precede every call).
    """
    records = list(records or [])
    n = len(records)
    if n < 2:
        return list(range(n))
    entity = []  # (original_index, ref, supersedes_by) — entity lane only
    for idx, record in enumerate(records):
        ref = str(_sr_attr(record, "superseded") or "").strip()
        supersedes_by = str(_sr_attr(record, "supersedes_by") or "").strip()
        if not ref or not supersedes_by or ref == supersedes_by \
                or ref.startswith("pt_"):
            # missing/self are warned + skipped by the loop; pt_ records
            # ride supersede() (separate lane) — none participate in edges
            continue
        entity.append((idx, ref, supersedes_by))
    if len(entity) < 2:
        return list(range(n))
    # Batch probe 1 — successor-name candidates (id-carrying rows only: an
    # id-less carrier can never be a VISIBLE successor — the loop's
    # has_visible_distinct requires an id).
    sb_names = sorted({sb for _, _, sb in entity})
    cand_rows = proj.g.query(
        "MATCH (o:Object) WHERE o.name IN $names RETURN o.name, o.id",
        params={"names": sb_names}).result_set
    cand_ids: dict[str, set] = {}
    for name, oid in cand_rows:
        if oid:
            cand_ids.setdefault(name, set()).add(oid)
    # Batch probe 2 — ref-side resolution, mirroring the loop's discipline
    # (apply_supersessions): an id-form ref wins (rows whose id == ref);
    # two ids claiming one ref = corruption never-guess; a name-form ref
    # resolves only via a SINGLE carrier (>1 = never-guess). Distilled to
    # the single object each fold would terminalize (its real id — None for
    # legacy id-less targets, which can never be an edge endpoint: the loop
    # folds them by name but they are id-less, and only id-carrying nodes
    # can be visible successors).
    refs_sorted = sorted({ref for _, ref, _ in entity})
    tgt_rows = proj.g.query(
        "MATCH (o:Object) WHERE o.id IN $ids OR o.name IN $names "
        "RETURN o.id, o.name",
        params={"ids": refs_sorted, "names": refs_sorted}).result_set
    by_id: dict[str, list] = {}
    by_name: dict[str, list] = {}
    for oid, name in tgt_rows:
        if oid and oid in refs_sorted:
            by_id.setdefault(oid, []).append((oid, name))
        if name in refs_sorted:
            by_name.setdefault(name, []).append((oid, name))
    target_id: dict[str, str] = {}  # ref -> real id its fold terminalizes
    for ref in refs_sorted:
        if by_id.get(ref):
            if len(by_id[ref]) > 1:  # duplicate id claim — loop never-guesses
                continue
            target_id[ref] = by_id[ref][0][0]
        elif len(by_name.get(ref, [])) == 1:
            target_id[ref] = by_name[ref][0][0] or None  # legacy id-less → None
        # else ambiguous (>1 name) or dangling → the loop skips → no edges
    # Edges over ORIGINAL record indices: R must fold before S when S's
    # fold terminalizes an object R's fold needs visible (S.target ∈ R's
    # successor-name candidates). Plan-review P0: the edge runs NEEDER→
    # TERMINALIZER — the producer record (whose visible-successor gate needs
    # its successor live) sorts BEFORE the record that would terminalize it.
    # Over-edging is harmless (fold-order cosmetics only); under-edging
    # fails soft to payload order. Index-space: entity carries ORIGINAL
    # record indices and edges/indeg are keyed by them, so a payload mixing
    # entity records with pt_/self/missing records (which contribute no
    # edges but occupy positions) wires constraints onto the RIGHT records.
    succ: dict[int, list[int]] = {}
    indeg = [0] * n
    for ridx, r_ref, sb in entity:                    # R — supersedes_by sb
        for sidx, s_ref, _s_sb in entity:             # S — terminalizes target_id[s_ref]
            if sidx == ridx:
                continue
            t = target_id.get(s_ref)
            # Exclude R's OWN target (code-review P2-1): the fold-time gate
            # has_visible_distinct excludes the target itself by construction,
            # so an edge keyed on it is spurious — it could hoist a divergent
            # same-target claim ahead of its first-emitted sibling and flip the
            # keep-first winner under duplicate-name carriers.
            if t and t in cand_ids.get(sb, set()) and t != target_id.get(r_ref):
                succ.setdefault(ridx, []).append(sidx)
                indeg[sidx] += 1
    # Stable Kahn over ALL n positions (entity members carry the edges;
    # non-entity records have indeg 0 and keep payload positions): pop the
    # lowest original index among ready nodes; leftover (cycles + their
    # transitive dependents) appends in original-index order == payload
    # order for that block (deterministic, pre-fix outcome).
    import heapq
    ready = [i for i in range(n) if indeg[i] == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for j in succ.get(i, []):
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(ready, j)
    if len(order) < n:  # cycle block — append leftovers in payload order
        remaining = sorted(set(range(n)) - set(order))
        order.extend(remaining)
    return order


def apply_supersessions(proj, sdk, records, *, session_id, warn=None):
    """Apply canonical supersession records — the ONE consumer-side
    discipline (producer side: extractor_v2._supersession_records).
    pt_ records → supersede() CORRECTS (terminal-probed, idempotent);
    entity records → ObjectSuperseded event (id-style, journaled with
    full provenance) + _fold_object_superseded (count-verified).
    #2164/#2193: shared by capture (_extract_session_v2), eval ingest_v2,
    and the hosted commit endpoint (_execute_commit_writes §6b). warn()
    receives every skip/failure —
    never a silent drop — with ONE explicit asymmetry (final-review
    P3): terminal pt_ olds are treated as idempotent re-ingests and
    skipped SILENTLY regardless of the claimed successor (no
    divergence probe — supersede_point would raise on a terminal old);
    terminal ENTITY olds warn keep-first when the claimed successor
    diverges from the stored one. The entity terminal branch is
    REACHABLE, not out-of-band-only: the extractor's S3 search_graph
    calls tortoise_fts_query(entity_type='object') directly, and that
    surface does NOT exclude terminal Objects (the terminal-status
    clause in search_engine applies to label == 'Point' only; recall's
    #1350 object filter runs after retrieval inside recall_state
    alone) — so overlapping capture (session 2 re-derives a
    supersession whose target session 1 already folded) routes a real
    entity record against a terminal target, and this branch is the
    idempotency mechanism (dedup same-successor / keep-first
    divergence). pt_ terminal olds remain unreachable via capture
    (S3 point exclusion + supersede_point's own guard); their silent
    idempotent skip guards out-of-band delivery. Same-commit supersession
    CHAINS (A→B and B→C in one payload) fold in DEPENDENCY order regardless
    of emission order (#2249): the pre-pass orders records so each fold
    runs while its successor is still live, and the visible-successor gate
    (which warns + skips a fold whose successor is recall-excluded) stays
    the same-payload-vs-cross-commit discriminator at fold time — a
    successor terminalized EARLIER IN THIS PAYLOAD folds after its
    producer; one terminal BEFORE the payload still skips (guard-(h)
    semantics). Returns the number of
    records applied.
    """
    if warn is None:
        warn = _logger.warning
    applied = 0
    # #2249: same-payload chains fold in DEPENDENCY order (a silent stable
    # pre-pass — payloads with <2 entity records or any resolution doubt
    # fall through to payload order). The per-record gates below re-run
    # unchanged at fold time on LIVE status — they remain the
    # same-payload/cross-commit discriminator (guard (h)). A pre-pass
    # failure (transient graph error) fails SOFT to payload order with one
    # warn — pre-fix partial-progress semantics are preserved exactly (the
    # hosted §6b caller runs this bare; capture wraps the whole call).
    records = list(records or [])
    try:
        fold_order = _supersession_fold_order(proj, records)
    except Exception as exc:  # pragma: no cover - transient graph failure
        warn(f"supersession fold-order pre-pass failed ({exc}) — "
             f"falling back to payload order")
        fold_order = list(range(len(records)))
    for i in fold_order:
        record = records[i]
        ref = str(_sr_attr(record, "superseded") or "").strip()
        supersedes_by = str(_sr_attr(record, "supersedes_by") or "").strip()
        evidence = str(_sr_attr(record, "evidence") or "")
        if not ref or not supersedes_by:
            warn(f"supersession record skipped (missing superseded or "
                 f"supersedes_by): {record!r}")
            continue
        if ref == supersedes_by:
            # self-supersession — meaningless, would fold an Object to
            # supersede itself (entity lane) or trip supersede_point's
            # old==new raise (pt_ lane). Every sibling path guards this
            # (the replaced eval inline loop's old_id == new_id → continue;
            # supersede_point raises on old==new; the producer-side
            # id-match short-circuits before its kind filter) — this
            # consumer-side guard is the defense-in-depth sink, placed
            # BEFORE the pt_/entity dispatch so it guards both lanes.
            warn(f"supersession record skipped (self-supersession): {record!r}")
            continue
        if ref.startswith("pt_"):
            # Point-level → the canonical supersede() CORRECTS (outdated +
            # edge transfer). Terminal-probed first: supersede_point RAISES
            # on a terminal old point (sdk.py supersede_point guard), so a
            # re-ingested/overlapping terminal record is an idempotent
            # silent skip, never a warning and never a raised error.
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.status",
                params={"ids": [ref, supersedes_by]},
            ).result_set
            status_by_id = {r[0]: r[1] for r in rows}
            if ref not in status_by_id:
                warn(f"point supersession ref {ref!r} not found — "
                     f"skipped (fail-open)")
                continue
            if (status_by_id[ref] or "") in ("superseded", "retracted",
                                              "archived"):
                # already terminal — idempotent re-ingest no-op
                continue
            try:
                sdk.supersede(ref, supersedes_by)
                applied += 1
            except Exception as exc:
                warn(f"point supersede {ref!r} → {supersedes_by!r} failed: {exc}")
            continue
        # Entity-level — successor FIRST: supersedes_by must be visible
        # (payload entities were already written when capture calls this;
        # eval/hosted write entities before supersessions too). A dangling
        # successor would be INVISIBLE — recall_state excludes superseded
        # Objects — so it is warned + skipped before any fold. Never-guess:
        # duplicate names (distinct ids) are ambiguous — the successor probe
        # must NOT pick one by LIMIT 1 (the same discipline the ref-side
        # probe below applies to ITS >1-name matches).
        sb_rows = proj.g.query(
            "MATCH (o:Object {name:$sb}) RETURN o.id, o.name, o.status",
            params={"sb": supersedes_by},
        ).result_set
        if not sb_rows:
            warn(f"entity supersession {ref!r} skipped — successor "
                 f"{supersedes_by!r} is not an Object in the payload "
                 f"entities or the graph (dangling successor)")
            continue
        # NB: >1 successor rows are NOT skipped here — the alias scan below
        # (post ref-side resolution) decides. Duplicate names are only
        # harmful when a candidate is the target itself; otherwise the fold
        # is deterministic (display-string-only successor).
        # #2164 review (P2, ISSUE C): the fold stores supersededBy truncated
        # to 200 chars (_fold_object_superseded: str(...)[:200] — mirrors the
        # write-path name cap, sdk.py name[:200]) — the DEDUP/keep-first
        # probe below compares against the STORED (truncated) form so a
        # long same-successor re-ingest dedups instead of warning. The FULL
        # name is kept for the journaled event (round-2 review, ISSUE 2 —
        # §11: the event log is truth; replay re-truncates identically at
        # the fold, so journal fidelity costs nothing at storage). No
        # truncation happens here — only at the compare and the fold.
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids OR o.name IN $names "
            "RETURN o.id, o.name, o.status, o.supersededBy",
            params={"ids": [ref], "names": [ref]},
        ).result_set
        if not rows:
            warn(f"supersession ref {ref!r} not found in the graph — "
                 f"skipped (fail-open)")
            continue
        # #2164 review (P2-2): rows[0] was backend-order-dependent — the OR
        # probe can return BOTH an id-match row (ref == an Object's id) AND a
        # DIFFERENT Object's name-match row for one ref (e.g. a legacy no-id
        # Object whose name happens to equal another Object's id). Deterministic
        # preference: when ref equals an Object's id, that row wins — an id
        # match is unambiguous, a same-string name match is the ambiguous side.
        # The >1-name-match never-guess below stays for name refs, where the
        # graph offers no tiebreak.
        by_id = [r for r in rows if r[0] == ref]
        if len(by_id) > 1:
            # two nodes claim the same id — raw-corruption artifact.
            warn(f"supersession ref {ref!r} matches {len(by_id)} Objects "
                 f"by id — skipped (never-guess)")
            continue
        if by_id:
            obj_id, obj_name, o_status, o_sb = by_id[0]
        else:
            by_name = [r for r in rows if r[1] == ref]
            if len(by_name) > 1:
                # never-guess: two Objects claim the same name, do not pick
                # one (a blind LIMIT 1 would fold an arbitrary carrier).
                warn(f"supersession ref {ref!r} matches {len(by_name)} Objects "
                     f"by name — skipped (never-guess)")
                continue
            if not by_name:  # pragma: no cover - probe WHERE clause guarantees
                warn(f"supersession ref {ref!r} matched a row via neither id "
                     f"nor name — skipped (never-guess)")
                continue
            obj_id, obj_name, o_status, o_sb = by_name[0]
        # REAL id captured BEFORE the legacy synthesis below: for a legacy
        # id-less target this stays None (the canonical id is synthesized
        # next for the journal — the graph node itself carries no id, and an
        # id-branch fold on a synthesized id would MISS on replay without
        # the name fallback). The alias + visible-successor scans need the
        # REAL id: a synthesized id could equal a canonical successor's id
        # only if a canonical Object with the same name coexists — impossible
        # (the ref probe's >1-name never-guess would have skipped it
        # earlier).
        real_obj_id = obj_id
        legacy_no_id = False
        if not obj_id:
            # #2164 review (P2-1): a legacy id-less Object (raw-Cypher-created
            # BEFORE canonical obj-<sha26(name)> minting — every supported write
            # path assigns the canonical id) probes back with o.id = None.
            # Emitting id=None was a SILENT DROP: the JSONL branch of
            # sdk._emit_event early-returns when id is None and the GraphEvent
            # payload became {} — the ObjectSuperseded journaled NOWHERE (the
            # M2 provenance gap). Never guess an id when the name is also gone.
            if not obj_name:
                warn(f"supersession ref {ref!r} resolved to an Object with "
                     f"neither id nor name — skipped (never-guess)")
                continue
            # Synthesize the canonical deterministic id (sdk._entity_name_id —
            # the exact id create_entity would have minted) so the journal
            # carries a real, auditable id.
            from tortoise.sdk import _entity_name_id
            legacy_no_id = True
            obj_id = _entity_name_id("Object", obj_name)
        # #2164 review (P1 advisory + round-2 ISSUE 4): the string-equality
        # self-guard above only catches ref == supersedes_by on the SAME
        # string. Two remaining hazards share one root — a successor
        # candidate that IS the ref-side Object:
        #   (a) MIXED id/name self-reference (superseded = the canonical id,
        #       supersedes_by = that same Object's name) would fold a LIVE
        #       Object onto ITSELF (status='superseded', supersededBy=<its
        #       own name>) → it vanishes from recall_state's default view
        #       (the ISSUE A harm class via a different spelling);
        #   (b) duplicate-named successors: a blind LIMIT 1 could pick the
        #       target as "the successor" — the same self-fold in disguise.
        # Scan ALL successor rows for id equality against the ref-side
        # resolution: canonical id equality is unambiguous proof that the
        # record's successor is the target itself → skip (never-guess). When
        # NO candidate aliases the target, duplicate-named successors are
        # NOT a blocker — the fold stores only the successor DISPLAY string
        # (supersedes_by) and never references a successor node, so the fold
        # result is deterministic across same-named candidates. (Pre-
        # round-2 the >1-name probe skipped the whole record, silently
        # unfolding legit supersessions whose ref side was an unambiguous
        # canonical id.) Legacy id-less self-spellings cannot reach this
        # scan: legacy refs resolve by name (obj_name == ref), so a
        # self-alias there requires supersedes_by == ref — already absorbed
        # by the string-equality guard above.
        # Self-alias ⇔ EVERY successor candidate is the target itself — i.e.
        # no DISTINCT successor exists under that name (a pure self-fold with
        # nothing left visible as the successor). Legacy id-less rows can
        # never make this True: a legacy target resolves by name (obj_name ==
        # ref), so a self-alias there requires supersedes_by == ref — already
        # absorbed by the string-equality guard above; any id-less row
        # reaching this scan is a DISTINCT successor (different name) and
        # correctly forces all() to False.
        all_candidates_are_target = bool(sb_rows) and all(
            sid is not None and real_obj_id is not None
            and sid == real_obj_id
            for sid, _sname, _sst in sb_rows)
        if all_candidates_are_target:
            warn(f"supersession record skipped (self-supersession via "
                 f"id/name aliasing): {record!r} resolves both sides to "
                 f"{obj_name!r} (id {real_obj_id!r}) — no distinct "
                 f"successor exists under that name")
            continue
        # round-4 review (P2-2): any target already in a recall-excluded
        # status (superseded/deprecated/archived/retracted — the #1350
        # object exclusion tuple, not just 'superseded') is terminal: a
        # re-record must dedup on same-successor or keep-first on divergence
        # — never re-fold. Pre-fix an archived/retracted/deprecated target
        # fell through to the fold, clobbering e.g. archived→superseded
        # (and for retracted, reversing the #689 leak-guard direction).
        if (o_status or "") in _RECALL_OBJECT_EXCLUDED_STATUS:
            # stored supersededBy is truncated to 200 by the fold — compare
            # against the truncated form (round-2 ISSUE C): a long
            # same-successor re-ingest dedups instead of spurious conflict.
            supersedes_by_stored = supersedes_by[:200]
            if (o_sb or "") == supersedes_by_stored:
                if len(o_sb or "") >= 200 and len(supersedes_by) > 200:
                    # round-2 review (ISSUE 1): the stored fold value was
                    # ITSELF truncated — a DIVERGENT successor sharing the
                    # same 200-char prefix is indistinguishable from an
                    # idempotent re-ingest. Keep-first outcome is identical
                    # (the fold never blind-overwrites), but be loud that
                    # identity beyond the cap is unverified rather than
                    # silently absorbing a possible divergence.
                    warn(f"supersession ref {ref!r} re-folded to a "
                         f"successor sharing a 200-char prefix with the "
                         f"stored fold — treated as idempotent (keep-first); "
                         f"identity beyond the name cap is not verified")
                # same successor already folded — idempotent dedup no-op
                continue
            warn(f"supersession ref {ref!r} already terminal "
                 f"(status={o_status!r}, supersededBy={o_sb!r}) — "
                 f"conflict with {supersedes_by!r} skipped "
                 f"(keep-first, the fold never blind-overwrites)")
            continue
        # round-4 review: the fold-through decision (target is LIVE) needs a
        # successor VISIBLE to recall_state's default Object view. That view
        # (sdk.py recall_state, #1350 filter) requires BOTH:
        #   (a) an id — SDK object reads (recall_state, fts_query, kind
        #       scan, get_entity) key retrieval on o.id and drop id-less
        #       rows before presenting results; a raw graph name probe
        #       returns an id-less node only as an id=None row (which the
        #       gate filters), never as a usable successor; and
        #   (b) status not in recall's object exclusion tuple
        #       {"superseded", "deprecated", "archived", "retracted"}
        #       (verified live: a deprecated Object enters the FTS pool but
        #       never the recall state view; "outdated" IS visible — it is
        #       not in the object exclusion, only in the POINT-terminal
        #       vocabulary set).
        # Folding a live target onto a display name whose remaining carriers
        # are all id-less or recall-excluded leaves NO visible successor =
        # the exact dangling-successor harm this lane exists to prevent.
        # Only a visible distinct candidate authorizes the fold; otherwise
        # the record's successor is effectively dangling → warn + skip.
        # (Runs AFTER the terminal check above — an idempotent re-ingest of
        # an already-folded target dedups silently even if its successor has
        # since gone terminal: the fold already happened, no new fold is
        # being made. Also: id-less legacy targets fold by name via the
        # journaled synthesized id + name fallback — their SUCCESSORS
        # carrying canonical ids are unaffected by the id-present rule.)
        has_visible_distinct = any(
            sid is not None and sid != real_obj_id
            and (sst or "") not in _RECALL_OBJECT_EXCLUDED_STATUS
            for sid, _sname, sst in sb_rows)
        if not has_visible_distinct:
            warn(f"entity supersession {ref!r} skipped — successor "
                 f"{supersedes_by!r} resolves only to id-less, "
                 f"target-identical, or recall-excluded Objects (no "
                 f"visible successor under that name)")
            continue
        try:
            # id-style emission: id + ALL extra kwargs ride the JSONL line
            # (event.update(extra), sdk._emit_event #548) AND ObjectSuperseded
            # ∈ _GRAPH_EVENT_TYPES (#432) synthesizes the GraphEvent payload
            # from the same kwargs — provenance reaches BOTH stores. The
            # emitted id is the synthesized canonical id for legacy nodes.
            sdk._emit_event(
                "ObjectSuperseded",
                id=obj_id, name=obj_name, supersedes_by=supersedes_by,
                session_id=session_id, evidence=evidence,
            )
            # _fold_object_superseded prefers the id branch when id is
            # truthy (entities.py: MATCH (o:Object {id:$id})) but FALLS BACK
            # to the name branch when the id branch matches nothing and a
            # name is present (#2164 ISSUE B) — a legacy node carries no id
            # property (or a pre-canonical one), so folding on the
            # synthesized id alone would MISS on replay. Fold by NAME — the
            # only key the legacy node actually has; the fold id branch is
            # kept (real id present) so canonical nodes fold by id. The
            # journaled event still carries the synthesized id for audit +
            # replay (a node canonicalized by a later create_entity gains
            # the id and replay folds by it; an id-less one is recovered by
            # the replay name fallback).
            fold_ev = {"name": obj_name, "supersedes_by": supersedes_by}
            if not legacy_no_id:
                fold_ev["id"] = obj_id
            folded, fold_matched = proj._fold_object_superseded(
                fold_ev, cas=True)  # #2242: the LIVE path opts into the CAS
            if folded == 0:
                # #2242 classified outcome: (0,0) = no node matched (the
                # fold-miss below — event stays journaled, rebuild retains
                # the truth); (0,N) = the node exists but is ALREADY
                # terminal — a concurrent commit folded it between this
                # record's gate probe and the fold (the keep-first loser).
                # The emitted event stays journaled; the fold is NOT applied
                # and NOT counted. Sequential paths can never reach here
                # (the terminal/visible gates precede the fold in the same
                # sync block) — this warn is exactly the cross-commit
                # concurrency signal (#2242 indicator 1).
                if fold_matched == 0:
                    warn(f"ObjectSuperseded emitted for {obj_name!r} but the "
                         f"fold matched no Object — event stays journaled "
                         f"(rebuild retains the truth)")
                else:
                    warn(f"ObjectSuperseded emitted for {obj_name!r} but the "
                         f"fold lost a concurrent race — the Object is "
                         f"already superseded (keep-first); event stays "
                         f"journaled (rebuild retains the truth)")
            else:
                applied += 1
        except Exception as exc:
            warn(f"ObjectSuperseded emit/fold failed for {obj_name!r}: {exc}")
    return applied
