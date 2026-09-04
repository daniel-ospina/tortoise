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
    (m)-[:IMPL]->(op) + (op)-[:mitigated_by]->(m), strength in [0.10, 0.50].
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
# and (phase-2) hosted §6b share ONE consumer-side discipline.


def apply_supersessions(proj, sdk, records, *, session_id, warn=None):
    """Apply canonical supersession records — the ONE consumer-side
    discipline (producer side: extractor_v2._supersession_records).
    pt_ records → supersede() CORRECTS (terminal-probed, idempotent);
    entity records → ObjectSuperseded event (id-style, journaled with
    full provenance) + _fold_object_superseded (count-verified).
    #2164: shared by capture (_extract_session_v2), eval ingest_v2,
    and (phase-2) hosted §6b. warn() receives every skip/failure —
    never a silent drop — with ONE explicit asymmetry (final-review
    P3): terminal pt_ olds are treated as idempotent re-ingests and
    skipped SILENTLY regardless of the claimed successor (no
    divergence probe — supersede_point would raise on a terminal old);
    terminal ENTITY olds warn keep-first when the claimed successor
    diverges from the stored one. Real capture cannot reach either
    branch (S3 terminal exclusion) — the out-of-band path is
    defense-in-depth. Returns the number of records applied.
    """
    if warn is None:
        warn = _logger.warning
    applied = 0
    for record in records or []:
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
        # Objects — so it is warned + skipped before any fold.
        sb_rows = proj.g.query(
            "MATCH (o:Object {name:$sb}) RETURN o.id LIMIT 1",
            params={"sb": supersedes_by},
        ).result_set
        if not sb_rows:
            warn(f"entity supersession {ref!r} skipped — successor "
                 f"{supersedes_by!r} is not an Object in the payload "
                 f"entities or the graph (dangling successor)")
            continue
        # #2164 review (P2): the fold stores supersededBy truncated to 200
        # chars (_fold_object_superseded: str(...)[:200]) — truncate ONCE
        # here, AFTER the successor-visibility probe above (which must see
        # the FULL successor name), so the keep-first probe, the journaled
        # event, and the stored fold value all agree. Pre-fix the conflict
        # probe compared the incoming UNtruncated value against the
        # fold-stored truncated one — long successor names produced spurious
        # "conflict … keep-first" warnings and same-successor dedup never
        # fired for the same successor.
        supersedes_by = supersedes_by[:200]
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
                # never-guess — deliberate divergence from hosted §6b's blind
                # LIMIT 1: two Objects claim the same name, do not pick one.
                warn(f"supersession ref {ref!r} matches {len(by_name)} Objects "
                     f"by name — skipped (never-guess)")
                continue
            if not by_name:  # pragma: no cover - probe WHERE clause guarantees
                warn(f"supersession ref {ref!r} matched a row via neither id "
                     f"nor name — skipped (never-guess)")
                continue
            obj_id, obj_name, o_status, o_sb = by_name[0]
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
        if (o_status or "") == "superseded":
            if (o_sb or "") == supersedes_by:
                # same successor already folded — idempotent dedup no-op
                continue
            warn(f"supersession ref {ref!r} already superseded by "
                 f"{o_sb!r} — conflict with {supersedes_by!r} skipped "
                 f"(keep-first, the fold never blind-overwrites)")
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
            matched = proj._fold_object_superseded(fold_ev)
            if matched == 0:
                # fold-miss: event stays journaled (rebuild retains the
                # truth), but the live status flip failed — make it visible.
                warn(f"ObjectSuperseded emitted for {obj_name!r} but the "
                     f"fold matched no Object — event stays journaled "
                     f"(rebuild retains the truth)")
            else:
                applied += 1
        except Exception as exc:
            warn(f"ObjectSuperseded emit/fold failed for {obj_name!r}: {exc}")
    return applied
