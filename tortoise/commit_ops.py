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

from .live import is_terminal_status  # #2498 shared terminal vocabulary

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


def remap_operator_endpoint_refs(operators: list, id_map: dict) -> list:
    """#4716 Part 1 — rewrite payload operator endpoint refs through the
    capture commit's ``payload id -> resolved graph id`` map.

    Two id spaces meet at the commit: the payload's content-addressed
    ``pt_<sha>`` ids and the ids the graph actually holds. The capture point
    loop re-resolves each payload point by content hash and keeps the
    EXISTING node's id on a hit (``sdk.py`` — ``_find_point_by_content`` →
    ``resolved = hit_id``), but the operator refs used to travel verbatim to
    ``create_operator`` (called from ``apply_payload_operators``). A payload
    id that resolved to a different graph id then named nothing,
    ``create_operator`` raised, and ``apply_payload_operators`` swallowed it
    (``operator write skipped (inputs missing?)``) — the silent edge drop of
    #4654.

    This restores, on the v2 capture path, the invariant the v1 builder
    ``_stream_to_payload`` already enforces ("REMAP operator src/dst +
    MITIGATES target triples to the re-derived ids, else Layer-1 referential
    integrity fails", #1272).

    ⚠️ Scope (updated by **#4970**): BOTH write paths now wire this remap —
    the call site is no longer capture-only. The hosted commit
    (``hosted_api._execute_commit_writes`` §5/§7) used to pass the RAW payload
    ``Operator`` models while its ``create_point(..., dedup=True)`` re-keyed
    to an existing-content node, so the refs named nothing and the edge
    dropped silently (#4654). #4970 closed that residual: the hosted §5 point
    loop surfaces the id ``create_point`` actually RESOLVED to (its
    ``point_resolved_ids`` payload→graph map) and §7 passes the refs through
    THIS helper — the same graph-id precondition the capture commit satisfies.

    Pure and total: only refs present in ``id_map`` are rewritten, everything
    else (graph ids, event ids, empty refs) passes through untouched — so an
    operator referencing an Event, or an id already canonical, is
    byte-identical. Accepts raw payload dicts (the capture shape) and
    ``commit_schema`` Operator models; rewritten entries are copies, entries
    needing no change are returned as the original object, and the INPUTS are
    never mutated in place.

    ⛔ Caller contract — the map MUST be keyed by PAYLOAD ids, and ONLY by
    payload ids (ONE id space): a key the payload never named resolves
    nothing, and a server-recomputed id (``supersede_id``) mixed into the map
    could collide with another record's payload id (``point_content_id``
    hashes content only). On a ``supersede`` reconcile action the payload
    point id IS the PRIOR node's graph id, and the map keys it to the RESOLVED
    successor — deliberate, and consistent with ``supersede_point``'s own edge
    transfer (an operator edge on a superseded point belongs to its
    successor); pinned by
    ``tests/test_commit_endpoint.py::TestRekeyedPointResolvedIds::
    test_supersede_operator_ref_follows_the_successor``.
    """
    if not id_map:
        return list(operators or [])
    out: list = []
    for op in operators or []:
        updates: dict = {}
        src = _op_attr(op, "src")
        dst = _op_attr(op, "dst")
        if isinstance(src, str) and src in id_map:
            updates["src"] = id_map[src]
        if isinstance(dst, str) and dst in id_map:
            updates["dst"] = id_map[dst]
        target = _op_attr(op, "target")
        if target is not None:
            t_src = _target_attr(target, "src")
            t_dst = _target_attr(target, "dst")
            t_updates: dict = {}
            if isinstance(t_src, str) and t_src in id_map:
                t_updates["src"] = id_map[t_src]
            if isinstance(t_dst, str) and t_dst in id_map:
                t_updates["dst"] = id_map[t_dst]
            if t_updates:
                updates["target"] = (
                    {**target, **t_updates} if isinstance(target, dict)
                    else target.model_copy(update=t_updates))
        if not updates:
            out.append(op)
        elif isinstance(op, dict):
            out.append({**op, **updates})
        else:
            out.append(op.model_copy(update=updates))
    return out


def reverse_point_id_map(id_map: dict) -> dict:
    """Invert a payload-id → resolved-graph-id map for reason lookups.

    ``apply_payload_operators`` resolves a MITIGATES reason from the SAME ref
    it passes to ``sdk.mitigate_operator`` — but that ref has already been
    remapped into GRAPH-id space, while the caller's content lookup is keyed
    by PAYLOAD id. Handing the helper the naive resolver then degrades a
    re-keyed dampener's reason to its bare graph id (#4716 review P1,
    reproduced end-to-end). This builds the reverse lookup both write paths
    pass as ``point_content_by_id``.

    **FIRST payload id wins** when several ids resolved to one graph node
    (``setdefault`` over the map's insertion order). The rule is stated HERE,
    once, so the two call sites cannot drift on it. Both write paths key the
    map by PAYLOAD point id alone, so the winner is always a real
    ``payload.points`` entry and the reason resolves: two ids resolve to one
    node only when content+kind match (``_find_point_by_content``), so their
    normalized content is equal and either winner yields the same reason
    TEXT. ⛔ Do NOT add a non-payload key (e.g. the server-recomputed
    ``supersede_id``) to a caller's map: it has no ``payload.points`` entry,
    so if it won the lookup the reason would degrade to the bare graph id —
    exactly the #4716 review-P1 degradation this helper exists to prevent —
    and, being a second id space (``point_content_id`` hashes CONTENT only),
    it could collide with another record's payload id. See the §5 note in
    ``hosted_api._execute_commit_writes``.
    """
    reverse: dict[str, str] = {}
    for payload_id, resolved_id in (id_map or {}).items():
        reverse.setdefault(resolved_id, payload_id)
    return reverse


def remap_supersession_point_refs(records: list, id_map: dict) -> list:
    """#4716 Part 1 (adjacent hole) — same remap for the supersession
    reference that is a payload id BY CONSTRUCTION.

    ``supersessions`` records carry two point ids: ``superseded`` (resolved by
    the extractor against the S3 search, so ALREADY a real graph id) and
    ``supersedes_by`` (the NEW payload point's content-addressed ``pt_<sha>``
    id). A ``supersedes_by`` whose payload point resolved to an existing graph
    node under a different id is the SAME two-id-space mismatch the operators
    had: ``sdk.supersede(prior, '<payload id>')`` targets a node that does not
    exist, RAISES, and ``apply_supersessions`` swallows it as ``point
    supersede '<prior>' → '<payload id>' failed: …`` — so the CORRECTS fold
    would be lost. (It is NOT the ``point supersession ref '<payload id>' not
    found — skipped (fail-open)`` warning: that fires only when the
    already-graph-id ``superseded`` side is absent.)

    ``superseded`` is deliberately NOT remapped (code-review P2): it is the
    record's LANE DISCRIMINATOR downstream — ``apply_supersessions`` dispatches
    on ``ref.startswith("pt_")`` and ``_supersession_fold_order`` excludes
    ``pt_`` refs from its entity-lane pre-pass. Running it through the map
    would let a payload id that maps to a NON-``pt_`` graph id silently flip
    the record to the entity lane, degrading a CORRECTS fold into "successor is
    not an Object in the payload". It is already a graph id, so the map has
    nothing to do for it.

    ``about_entities`` does NOT share the hole either: its values are Object
    NAMES matched by name at attach time, never ids.
    """
    if not id_map:
        return list(records or [])
    out: list = []
    for record in records or []:
        updates: dict = {}
        for side in ("supersedes_by",):
            ref = _sr_attr(record, side)
            if isinstance(ref, str) and ref in id_map:
                updates[side] = id_map[ref]
        if not updates:
            out.append(record)
        elif isinstance(record, dict):
            out.append({**record, **updates})
        else:
            out.append(record.model_copy(update=updates))
    return out


def apply_payload_operators(proj, sdk, operators: list, *,
                            point_content_by_id=None) -> list[str]:
    """Apply Layer-1 payload operators with commit semantics (#1532 D3).

    IMPL/NAND first via ``sdk.create_operator`` (promote_source=False, #780);
    MITIGATES second via ``sdk.mitigate_operator`` — mitigation Point +
    (m)-[:IMPL]->(op) + (op)-[:mitigated_by]->(m), strength in [0.10, 0.50]
    (single source: tortoise/weights.py module docstring, #2315 — strength
    = the graded dampener of the operator's effective weight via
    w_eff = w * (1 - strength); NOT how true the reason is, NOT fused into
    the mitigation point's own Beta prior, #2199 knock-on decision 3).
    EP consumes mitigation_strength through compute_operator_weight.
    Deep-miss (target IMPL edge absent) -> logged warning, mitigation dropped
    (support-edge-first convention, DE2E-11 negative). Never raises on a
    missing target. ``point_content_by_id(pid) -> str`` supplies the
    mitigation reason's content fallback when provided.

    ⛔ #4937 (F1 ruling on #2552): a payload record with ``op_type ==
    "MITIGATES"`` is a BRIDGE-ATTACK record, not a peer operator kind — the
    ``target`` names the operator bridge it damps and ``strength`` keeps its
    ``w_eff = w × (1 − strength)`` meaning. It is therefore routed ONLY to
    ``mitigate_operator``; the first pass below skips it so it can never reach
    ``create_operator``. The wire spelling stays ``MITIGATES`` inside the
    ``operators`` array for backward compatibility with older clients and
    extractors; the *semantics* are mitigation, never a second operator kind.

    ⛔ ID-SPACE PRECONDITION (#4716 P1): every ``src``/``dst`` and MITIGATES
    ``target.{src,dst}`` ref MUST be a GRAPH id by the time it reaches here. A
    caller holding a payload-id space MUST pass the refs through
    ``remap_operator_endpoint_refs`` FIRST — and, because the MITIGATES reason
    is resolved from the SAME ref, it must hand a map-aware
    ``point_content_by_id`` that still resolves the PRE-remap payload id
    (otherwise a re-keyed dampener's reason degrades to its own graph id).
    Passing payload ids here is not an error the function can detect: the
    operator write is swallowed as ``operator write skipped (inputs
    missing?)``.

    Returns the ids of the IMPL/NAND operator Points THIS call CREATED, in
    creation order (#4936). A capture must stamp exactly those nodes with its
    ``sessionCaptured`` eventId: joining on the MINTED point set instead
    misses every operator whose endpoint RE-KEYED to a pre-existing graph
    node (#4716 Part 1) — a folded-only capture has no minted ids at all,
    so the operator topology was left unstamped and invisible to the
    eventId-keyed retrievable layer (``operator_counts == {}``). Only
    CREATED nodes are returned: the MITIGATES Cypher fallback can resolve an
    operator this call did not write, and a caller stamping provenance must
    never claim a node a prior session created. The derived-commit call site
    (``hosted_api._execute_commit_writes`` §7) ignores the value — it has no
    capture eventId to stamp.

    ⛔ The return is a plain LIST, never ``target_op_ids.values()``: that
    dict is the MITIGATES same-call lookup and is keyed on
    ``(src, dst, op_type)``; it must never double as the provenance set
    (#4936). Before #4971 the two could differ — ``create_operator`` mints
    unconditionally, so two payload records that re-keyed onto the SAME
    graph triple each created their own node, and a dict keyed on the triple
    would silently drop the earlier node's id from the provenance set,
    leaving it unstamped and invisible. #4971's guard (below) now makes the
    repeat a no-op, so a triple is unique within a call and the two agree
    again — but the LIST stays the caller's contract: it is the ordered set
    of ids this call CREATED, which is what a provenance stamp needs and what
    a caller must never widen by looking at the MITIGATES dict.

    ⛔ #4971 — IDEMPOTENCY GUARD, keyed on the RESOLVED ``(op_type, src,
    dst)`` triple. ``create_operator`` mints a fresh ULID on every call with
    no existence probe, so a commit (or a failed-capture retry) that reaches
    this pass twice mints a SECOND operator Point for one bridge — and each
    duplicate is a separately weighted edge (``weights.py::
    compute_operator_weight`` is per op id), so EP propagates the same link
    twice (silent belief inflation). The probe below is the eval lane's
    dup-edge probe (``tools/longmem_eval/ingest_v2.py``, #1369 review P2)
    hoisted here so the hosted (``hosted_api._execute_commit_writes`` §7) and
    capture (``sdk._extract_session_v2``) paths share one discipline — the
    eval lane keeps its own inline copy (it does NOT call this helper).

    ⚠️ The key is the RESOLVED triple, NEVER the payload id: on a re-keyed
    endpoint the payload id names nothing in the graph (it resolved to a
    pre-existing node under a DIFFERENT id), so a payload-keyed probe would
    never match and the duplicate would survive — the identical reasoning the
    event probe in ``ingest_v2.py`` records ("the key ... NEVER the payload
    ``id``: extractor_v2's prior-graph search REUSES a prior ... so the
    payload ``id`` is NOT a stable idempotency key"). It is exactly why the
    ID-SPACE PRECONDITION above is a precondition: this helper can only be
    idempotent if its caller handed it graph ids.

    ``direction`` is deliberately NOT part of the key — the acceptance is
    ONE node per ``(op_type, src, dst)`` triple, matching the eval lane's
    probe (so the FIRST write's direction wins; pinned by
    ``test_direction_is_not_part_of_the_key_first_write_wins``). On a repeat
    the guard ``continue``s (the eval lane's semantics), so the node is
    neither re-created NOR added to the returned list: the return contract is
    CREATED ids only, and a caller stamping provenance must never claim a node
    a prior commit created (the #4936 rule above). Because the guard probes
    the GRAPH, a duplicate triple WITHIN one payload collapses too, exactly as
    it does in the eval lane — the same belief inflation otherwise survives as
    a within-call duplicate (and that is why #4936's
    ``test_fold_lane_commit_leg_stamps_every_node_when_a_triple_repeats``
    now pins the collapsed count instead of the old two-node list).

    ⚠️ SCOPE — the probe is SINGLE-TARGET: ``idx:1`` is the only target it
    reads, because every caller here passes ``[dst]``. ``sdk.create_operator``
    accepts ``target_ids: list`` and IS called with several targets elsewhere
    (``sdk.py:9895``), so a future MULTI-TARGET caller would get the first
    target idempotent and the rest duplicated. Widen the probe to read the
    whole target set before routing such a caller here.

    ⚠️ The MITIGATES fallback below is IMPL-only (it hardcodes
    ``op_type:'IMPL'``), so ``continue``-ing a skipped NAND leaves a
    NAND-targeted mitigation with nothing to attach to and it is dropped with
    a warning. Not reachable today — ``OperatorTarget.op_type`` is
    ``Literal["IMPL"]`` (``commit_schema.py:464``) and the extractor emits
    ``op_type: "IMPL"`` (``extractor_v2.py:5868``) — but this guard is the
    first thing that makes that fallback load-bearing for a skipped same-call
    operator, so extend the fallback (``t_op_type`` + the mapped edge) in the
    same change that widens the schema.
    """
    target_op_ids: dict[tuple, str] = {}
    created_ids: list[str] = []
    for op in operators:
        op_type = _op_attr(op, "op_type")
        if op_type == "MITIGATES":
            continue
        src, dst = _op_attr(op, "src"), _op_attr(op, "dst")
        if not op_type or not src or not dst:
            _logger.warning(
                "operator write skipped (inputs missing?): %r", op)
            continue
        # #4971 — (op_type, src, dst) idempotency probe, on the RESOLVED
        # triple (see the docstring; a payload-keyed probe never fires on a
        # re-key). The relation type is mapped exactly as ``create_operator``
        # maps it for the edge it writes (part/whole ops use ``hasPart``), so
        # the probe reads the same edge the write would have created rather
        # than inlining a raw op_type that would make it miss on those ops.
        _edge_type = ("hasPart" if op_type not in ("IMPL", "NAND")
                      else op_type)
        _dup = proj.g.query(
            f"MATCH (o:Point {{is_operator:true, op_type:$t}})-"
            f"[:{_edge_type} {{idx:0}}]->(s) WHERE s.id = $src "
            f"MATCH (o)-[:{_edge_type} {{idx:1}}]->(d) WHERE d.id = $dst "
            "RETURN count(*) LIMIT 1",
            params={"t": op_type, "src": src, "dst": dst}).result_set
        if _dup and _dup[0][0]:
            # Already bridged by an earlier commit/retry — a no-op. Do NOT
            # record it in target_op_ids: the MITIGATES second pass falls
            # back to its own Cypher probe and finds the pre-existing operator
            # (whose ``mitigate_operator`` is itself idempotent).
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
        created_ids.append(result["id"])
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
            # ``mitigate_operator`` already wraps the reason in the
            # ``[MITIGATION] `` display prefix (sdk.py; ``why.py`` strips
            # exactly one), so the old ``f"[MITIGATION] {src}"`` fallback
            # stored a DOUBLE prefix whenever the reason could not be
            # resolved (#4716 review P2). Fall back to the raw ref.
            reason = str(src)
        sdk.mitigate_operator(op_id, reason=reason,
                              strength=_op_attr(op, "strength") or 0.5)
    return created_ids


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


#: #5654: cap on the per-record WARN emission from ``apply_supersessions``.
#: Its record loop is fail-open — ``warn(...)`` + ``continue`` per record — so
#: an over-cap batch turns malformed input into ONE WARN line per record.
#: #2243 proposes a Layer-1 cap on ``len(payload.supersessions)`` (PR #5648,
#: still OPEN at #5654's base). Until it lands the ``supersessions`` LIST is
#: Layer-1-ungated on every path — capture, hosted commit §6b and eval ingest
#: all hand this helper the uncapped records (the hosted endpoint does run
#: ``validate_layer1``, but it caps points/entities/operators only; eval splits
#: a payload into two kind-bucketed calls, so its budget is per call) — so this
#: emission bound is the only limit on the storm. It bounds the EMISSION,
#: never the writes: every record is still attempted, and the withheld count
#: is reported in ONE summary warn. 20 keeps every realistic batch (a handful
#: of records) fully verbose while capping a pathological one.
_MAX_SUPERSESSION_WARNS = 20


class _PerRecordWarnBudget:
    """Bound a per-record ``warn()`` emission to a fixed budget (#5654).

    Wraps the caller's warn callable: the first ``limit`` messages are
    emitted verbatim, later ones are counted but not emitted, so a caller
    that warns once per record cannot amplify a malformed batch into one
    WARN line per record. The bound is on the EMISSION only — the caller's
    control flow is untouched (every record is still attempted) — and
    ``suppressed`` lets the caller report the withheld count in ONE summary
    warn.
    """

    __slots__ = ("_emit", "_limit", "total")

    def __init__(self, emit, limit: int) -> None:
        self._emit = emit
        self._limit = limit
        self.total = 0

    def __call__(self, msg, *args, **kwargs) -> None:
        self.total += 1
        if self.total <= self._limit:
            self._emit(msg, *args, **kwargs)

    @property
    def suppressed(self) -> int:
        """Per-record messages counted but not emitted."""
        return max(0, self.total - self._limit)


def apply_supersessions(proj, sdk, records, *, session_id, warn=None,
                        skipped=None):
    """Apply canonical supersession records — the ONE consumer-side
    discipline (producer side: extractor_v2._supersession_records).
    pt_ records → supersede() CORRECTS (terminal-probed, idempotent);
    entity records → ObjectSuperseded event (id-style, journaled with
    full provenance) + _fold_object_superseded (count-verified).
    #2164/#2193: shared by capture (_extract_session_v2), eval ingest_v2,
    and the hosted commit endpoint (_execute_commit_writes §6b). warn()
    receives the skip/failure of every record UP TO
    ``_MAX_SUPERSESSION_WARNS`` per call; the per-record messages beyond that
    budget are COUNTED, not emitted, and the call then closes with ONE
    summary warn carrying the batch size, the true warning total and the
    withheld count (#5654). An over-cap batch therefore stays SIZED — the
    summary reports the batch size and the true warning total — but its
    records past the budget are not named individually, and the summary does
    not distinguish a withheld benign warning from a withheld malformed one.
    The bound is on the EMISSION only — every record is still
    attempted by the same gates below. The per-record guarantees recorded
    earlier (#2242's "exactly one warn" on a lost concurrent fold, #2164's
    "unresolved refs warn loudly") hold verbatim for any call raising at
    most ``_MAX_SUPERSESSION_WARNS`` per-record warnings; a call above the
    budget has
    its withheld warnings counted, sized and reported in the summary but not
    named individually — whatever their cause (a legitimate batch can exceed
    the budget, e.g. many concurrent-race losses, so exceeding it is not by
    itself evidence of malformed input). Caller-side comments that still
    promise a per-record "never a silent drop" (``sdk._extract_session_v2``'s
    docstring and call site, ``hosted_api`` §6b, ``tools/longmem_eval/ingest_v2``)
    describe the pre-#5654 contract; this bound NARROWS that promise, and the
    comment updates are tracked in #5723. With ONE explicit
    asymmetry (final-review
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

    ``skipped`` (#5365) is an OPTIONAL caller-supplied list. When given, one
    ``{"ref", "successor", "reason"}`` dict is appended for every record the
    function did NOT apply. The return stays an ``int`` so existing callers
    are untouched, but a caller that passes ``skipped`` can tell a clean
    batch from one that dropped records — which the callback warning alone
    does not give it, because a caller that does not read logs sees only the
    int. The fail-open posture is UNCHANGED and deliberate: a bad record
    still never aborts the ingest; it just stops being invisible.
    """
    if warn is None:
        warn = _logger.warning
    applied = 0

    def _skip(ref, successor, reason):
        """Record a record that did NOT apply, in the caller's ``skipped``.

        #5365: the fail-open gates below deliberately swallow a bad record
        rather than abort the ingest, but before this channel existed the
        ONLY trace was a callback warning — so a caller that did not read
        logs (the hosted commit endpoint, eval ingest) could not distinguish
        a fully-applied batch from one that silently dropped records. #4021
        made this acute: a retroactive supersession that previously
        SUCCEEDED (while writing an inverted predecessor window) now raises
        into the catch below and became a silent unapplied record.
        """
        if skipped is not None:
            skipped.append({"ref": ref, "successor": successor,
                            "reason": reason})
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
    # #5654: bound the EMISSION of the per-record warns in the loop below.
    # Write semantics are untouched — every record is still attempted by the
    # same fail-open gates — this only stops a malformed batch from producing
    # one WARN line per record. `warn` is caller-supplied (capture passes
    # ``warnings.append``, hosted passes a counting logger delegate, eval
    # passes ``logger.warning``), so the bound applies at this one seam shared
    # by every path. Deliberately a BLANKET bound, not a per-class exemption:
    # exempting the actionable classes would let a broken graph (every fold
    # failing) storm again. The cost is that a warning past the budget is not
    # emitted individually — the summary below reports the withheld count so
    # the malfunction stays visible.
    _raw_warn = warn
    warn = _PerRecordWarnBudget(warn, _MAX_SUPERSESSION_WARNS)
    for i in fold_order:
        record = records[i]
        ref = str(_sr_attr(record, "superseded") or "").strip()
        supersedes_by = str(_sr_attr(record, "supersedes_by") or "").strip()
        evidence = str(_sr_attr(record, "evidence") or "")
        if not ref or not supersedes_by:
            warn(f"supersession record skipped (missing superseded or "
                 f"supersedes_by): {record!r}")
            _skip(ref, supersedes_by, "missing superseded or supersedes_by")
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
            _skip(ref, supersedes_by, "self-supersession")
            continue
        if ref.startswith("pt_"):
            # Point-level → the canonical supersede() CORRECTS (outdated +
            # edge transfer). Terminal-probed first: supersede_point RAISES
            # on a terminal old point (sdk.py supersede_point guard), so a
            # re-ingested/overlapping terminal record is an idempotent
            # silent skip, never a warning and never a raised error.
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "RETURN n.id, n.status, coalesce(n.outdated, false)",
                params={"ids": [ref, supersedes_by]},
            ).result_set
            state_by_id = {r[0]: (r[1], bool(r[2])) for r in rows}
            if ref not in state_by_id:
                warn(f"point supersession ref {ref!r} not found — "
                     f"skipped (fail-open)")
                _skip(ref, supersedes_by, "ref not found")
                continue
            # #2498: the SHARED terminal vocabulary (status set + the legacy
            # `outdated=true` flag) — the pre-#2498 3-status tuple let an
            # `outdated` / `deprecated` / flag-dead ref fall through to
            # sdk.supersede, which now RAISES, turning this documented
            # idempotent no-op into a spurious warning.
            if is_terminal_status(*state_by_id[ref]):
                # already terminal — idempotent re-ingest no-op
                continue
            try:
                sdk.supersede(ref, supersedes_by)
                applied += 1
            except Exception as exc:
                warn(f"point supersede {ref!r} → {supersedes_by!r} failed: {exc}")
                # #5365: the fail-open skip this issue is about. #4021 made
                # an inverted successor RAISE where it previously succeeded
                # (while writing the bad window), so this pre-existing,
                # deliberate catch turned a REFUSAL into a silently
                # unapplied record. Record it for the caller.
                _skip(ref, supersedes_by, f"supersede refused: {exc}")
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
            # #1370: routing a subject-kind entity to `:Subject` means an
            # entity supersession record for it can no longer resolve to an
            # Object. Report that case ACCURATELY (the generic "dangling
            # successor" message would misdiagnose a correctly-typed node
            # as a missing one) and skip — entity supersession is Object-only.
            _subj = proj.g.query(
                "MATCH (s:Subject {name:$sb}) RETURN count(s)",
                params={"sb": supersedes_by},
            ).result_set
            if _subj and _subj[0][0]:
                warn(f"entity supersession {ref!r} skipped — successor "
                     f"{supersedes_by!r} is a :Subject (a declared §5 "
                     f"subject kind, #1370); entity supersession is "
                     f"Object-only")
                _skip(ref, supersedes_by, "successor is a :Subject")
            else:
                warn(f"entity supersession {ref!r} skipped — successor "
                     f"{supersedes_by!r} is not an Object in the payload "
                     f"entities or the graph (dangling successor)")
                _skip(ref, supersedes_by, "dangling successor")
            continue
        # NB: >1 successor rows are NOT skipped here — the alias scan below
        # (post ref-side resolution) decides. Duplicate names are only
        # harmful when a candidate is the target itself; otherwise the fold
        # is deterministic (display-string-only successor).
        # #5370: the fold stores supersededBy VERBATIM (the FULL successor
        # name) — it no longer truncates to 200 chars. That cap was the
        # fold's OWN behaviour, never a property of the general write path
        # (`create_entity`/`_upsert_object` store Object names verbatim), so
        # it was LOSSY: a >200-char successor was stored as a prefix that
        # names NO Object, so the ask-path name-keyed probe could not verify
        # it and the renderer reported "no successor record found" (fixed
        # here + in projection/entities.py + assembly._state_header_hit).
        # The DEDUP/keep-first probe below compares against the STORED form;
        # rows folded BEFORE #5370 still carry the old 200-char prefix, so
        # the compare accepts EITHER the full name (new rows) or its
        # 200-char prefix (legacy rows) — a same-successor re-ingest stays a
        # dedup on both. The FULL name was always kept for the journaled
        # event (round-2 review, ISSUE 2 — §11: the event log is the
        # reconstruction source), so for rows folded AFTER #5370 live and
        # replay agree byte-for-byte. A LEGACY row does not: live holds the
        # old prefix while the journal holds the full name, so a rebuild
        # REWRITES the prefix to the full name. That is a benign one-way
        # healing — the journal is the reconstruction source and its value
        # is the correct one — but it IS a real live↔replay difference on
        # pre-fix rows, pinned by
        # test_rebuild_all_rewrites_a_legacy_prefix_to_the_journaled_full_name.
        # No truncation happens here — only at the compare (legacy tolerance).
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
            # #5370: the fold now stores the FULL successor name, but rows
            # folded BEFORE the fix carry the old 200-char prefix (#2164's
            # fold cap). Accept EITHER form so a long same-successor
            # re-ingest stays a silent dedup on new AND legacy rows.
            stored = o_sb or ""
            same_successor = stored == supersedes_by or (
                len(supersedes_by) > 200 and stored == supersedes_by[:200])
            if same_successor:
                if stored != supersedes_by:
                    # Matched via the 200-char-prefix tolerance: the
                    # stored value is a 200-char name, so a DIVERGENT
                    # successor sharing those 200 chars is indistinguishable
                    # from an idempotent re-ingest. Keep-first outcome is
                    # identical (the fold never blind-overwrites), but be
                    # loud that identity beyond the prefix is unverified
                    # rather than silently absorbing a possible divergence.
                    #
                    # NB the message below deliberately does NOT call the
                    # stored value a "legacy fold": the same arithmetic is
                    # reached by a POST-#5370 row folded onto a successor
                    # whose name is EXACTLY 200 chars (a genuine full name),
                    # and the row alone cannot say which it is.
                    #
                    # The exact-equality branch (stored == supersedes_by)
                    # is deliberately SILENT, and cannot be otherwise:
                    # that equality is exactly what an idempotent
                    # same-successor re-ingest looks like on new rows AND
                    # on legacy rows, and a stored 200-char value that was
                    # really a legacy prefix is indistinguishable from a
                    # genuinely 200-char successor name. The ambiguity is
                    # inherent to the width of the old cap, not introduced
                    # here.
                    warn(f"supersession ref {ref!r} re-folded to a "
                         f"successor sharing a 200-char prefix with the "
                         f"stored fold — treated as idempotent "
                         f"(keep-first); identity beyond that prefix "
                         f"is not verified")
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
            _skip(ref, supersedes_by, "no visible successor")
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
                # concurrency signal (#2242 indicator 1). The CAS relies on
                # server-mode single-statement serialization (metering.py
                # doctrine); embedded self-host threads within one process
                # can still race — out of scope, see the plan.
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
            _skip(obj_name, supersedes_by, f"emit/fold failed: {exc}")
    if skipped:
        _raw_warn(
            f"supersession batch of {len(records)} record(s): "
            f"{applied} applied, {len(skipped)} SKIPPED (fail-open — the "
            f"caller's `skipped` list carries each one). First: "
            f"{skipped[0]['ref']!r} → {skipped[0]['successor']!r} "
            f"({skipped[0]['reason']})"
        )
    if warn.suppressed:
        _raw_warn(
            f"supersession batch of {len(records)} record(s): "
            f"{warn.total} per-record warning(s) raised — the first "
            f"{_MAX_SUPERSESSION_WARNS} were reported individually, "
            f"{warn.suppressed} withheld (not individually logged). "
            f"Every record was still applied or skipped by the same "
            f"fail-open gates."
        )
    return applied
