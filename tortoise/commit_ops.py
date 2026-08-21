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
