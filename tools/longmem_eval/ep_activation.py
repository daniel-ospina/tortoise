"""Track C (#3011) — activate the belief layer (EP) for an eval namespace.

The eval ingest path (``tools/longmem_eval/ingest_v2.py``) writes every
extraction point with ``status="draft"`` (5 sites) and mints its operators
with ``promote_source=False`` (≈ L373). ``tortoise/ep.py`` filters the
factor graph with ``_live_only(...)``, which excludes ``draft`` — so EP
skips the entire eval graph and every claim's confidence reads the neutral
``0.50`` of a Beta(1,1) prior that was NEVER measured. Issue #2598: a
draft is EP-inert and must never display a fabricated ``0.5``.

This module closes that gap with two steps for a given eval graph:

1. **Promote** draft points → ``live`` and draft operators → ``live``
   (batch path, mirroring the capture auto-promotion block at
   ``tortoise/sdk.py:1270-1320`` — the ``PointPromoted`` / ``OperatorPromoted``
   events are emitted so a JSONL replay rebuilds the same live state).
2. **Run EP** so real posteriors (``posterior_alpha``/``posterior_beta``,
   falling back to ``ep_alpha``/``ep_beta``) are persisted.

EP entry point choice — ``dream(mode="full", ...)`` (NOT
``compute_confidence``): ``dream`` is the canonical whole-graph EP **write**
surface (its docstring: "dream WRITES n.confidence, so it must never
silently run uncalibrated EP") and full mode runs ``dreamer.dream_all`` —
batched (``batch_size=2000``), complete-in-one-pass, and it stamps/persists
the posteriors for every reachable claim. By contrast ``compute_confidence``
is a scoped **read** surface: its no-arg path only runs local EP over the
*already-dirty* roots (useless right after raw ``SET status='live'`` flips),
and its anchors/factors paths need caller-supplied seeds. A bulk post-ingest
pass has no seeds — full stabilization is exactly the right shape.

``read_confidence`` is the ONE canonical posterior read for a point property
dict. It returns ``None`` for a point with no persisted EP state — the
serializer then renders ``confidence: unmeasured`` (#2598). It NEVER
fabricates a ``0.5``.

Hermetic by construction: the module imports only ``logging`` and ``time``;
``activate_beliefs`` takes an already-constructed SDK (or a stub) so it can
be unit-tested without a database.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: EP engine label for the manifest — names the entry point actually used.
EP_ENGINE = "dream:full"

# ── Cypher (byte-mirrored from the capture auto-promotion block) ──────────
# Candidate draft points: non-operator, draft-or-unset status (the canonical
# read model treats unset status as live for most surfaces, but the ingest
# shape is explicit "draft"; matching both mirrors sdk.py:1277-1281).
_SELECT_DRAFT_POINT_IDS = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
    "AND (n.status IS NULL OR n.status = 'draft') "
    "RETURN n.id"
)
_PROMOTE_POINTS = "MATCH (n:Point) WHERE n.id IN $ids SET n.status = 'live'"
# Draft operators incident to the promoted claims (sdk.py:1296-1301).
_SELECT_DRAFT_OPERATORS = (
    "MATCH (o:Point {is_operator:true})-[:IMPL|NAND]->(c:Point) "
    "WHERE c.id IN $ids "
    "AND (o.status IS NULL OR o.status = 'draft') "
    "RETURN DISTINCT o.id"
)
_PROMOTE_OPERATOR = "MATCH (o:Point {id:$oid}) SET o.status = 'live'"

# ── EP-state census (non-operator claims only) ────────────────────────────
_COUNT_POINTS_TOTAL = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
    "RETURN count(n)"
)
_COUNT_POINTS_WITH_EP = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
    "AND (n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL) "
    "RETURN count(n)"
)


def read_confidence(p) -> float | None:
    """Canonical EP posterior read for a point property dict.

    Prefers the measured posterior (``posterior_alpha``/``posterior_beta``)
    over the immutable prior (``ep_alpha``/``ep_beta``), per-field. Returns
    ``None`` when the point carries NO usable EP state — an unmeasured point
    must render as ``confidence: unmeasured`` (#2598), never a fabricated
    ``0.5``.

    A *partial* state (only one of alpha/beta present) is also ``None``: EP
    always persists the pair together, so a lone parameter is malformed
    state, not a measurement. A degenerate ``alpha + beta <= 0`` is ``None``
    for the same reason (no defined mean).
    """
    if not p:
        return None
    # `is None` (not `or`) so a legitimate 0.0 posterior is never overridden
    # by the prior — "prefers posterior_*" must hold for falsy values too.
    alpha = p.get("posterior_alpha")
    if alpha is None:
        alpha = p.get("ep_alpha")
    beta = p.get("posterior_beta")
    if beta is None:
        beta = p.get("ep_beta")
    if alpha is None or beta is None:
        return None
    try:
        a = float(alpha)
        b = float(beta)
    except (TypeError, ValueError):
        return None
    if a + b <= 0:
        return None
    return a / (a + b)


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _scalar(result_set) -> int:
    """First cell of a single-row count query, 0 when absent."""
    if not result_set or not result_set[0]:
        return 0
    try:
        return int(result_set[0][0])
    except (TypeError, ValueError):
        return 0


def activate_beliefs(sdk, *, namespace: str | None = None,
                     batch_size: int = 500) -> dict:
    """Promote draft points/operators to live, then run EP.

    Args:
        sdk: a ``TortoiseSDK`` (or test stub) already scoped to the eval
            graph. ``sdk._get_proj().g`` is the query surface and
            ``sdk._emit_event`` / ``sdk._mark_dirty`` / ``sdk.dream`` are
            the write surfaces (all mirrored from the capture promotion
            block).
        namespace: the eval namespace the SDK is scoped to. When both the
            SDK and the caller name a namespace, they must match — activating
            the wrong graph would promote a peer's in-flight points. ``None``
            skips the guard.
        batch_size: points per promotion batch (thousands of eval points must
            not ride one Cypher statement).

    Returns a manifest:
        ``{points_promoted, operators_promoted, ep_ran, ep_engine,
        points_with_ep, points_unmeasured, duration_ms}``

    ``points_with_ep`` / ``points_unmeasured`` count NON-operator points
    carrying / lacking persisted EP state after the pass.

    Fail-open on the EP call (matching the capture block's non-fatal
    posture): a dream failure is logged and reported via ``ep_ran: False``
    so the run surfaces the failure in its manifest instead of crashing
    after the promotion writes are already committed. A failed EP pass
    leaves claims unmeasured — the serializer renders ``unmeasured``, so
    the failure can never masquerade as a measured ``0.5``.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    if namespace is not None:
        sdk_ns = getattr(sdk, "_namespace", None)
        if sdk_ns is not None and sdk_ns != namespace:
            raise ValueError(
                f"activate_beliefs namespace mismatch: SDK is scoped to "
                f"{sdk_ns!r} but caller asked for {namespace!r}")

    t0 = time.monotonic()
    proj = sdk._get_proj()
    g = proj.g

    # ── 1a. Promote EVERY draft point first ───────────────────────────
    # Two phases (points, then operators) so an operator never goes live
    # while one of its endpoints is still draft — the R16 invariant the
    # SDK's promote path enforces (_promote_incident_operators).
    rows = g.query(_SELECT_DRAFT_POINT_IDS).result_set
    draft_ids = [r[0] for r in rows if r and r[0]]
    points_promoted = 0
    for chunk in _chunks(draft_ids, batch_size):
        g.query(_PROMOTE_POINTS, params={"ids": chunk})
        # Snapshot AFTER the SET so the event carries the live state — the
        # projection's PointPromoted replay upserts the full snapshot (#548).
        for pid in chunk:
            sdk._emit_event("PointPromoted", point=sdk.get_point(pid))
        points_promoted += len(chunk)

    # ── 1b. Promote their draft operators (all endpoints now live) ────
    operators_promoted = 0
    promoted_ops: set[str] = set()
    for chunk in _chunks(draft_ids, batch_size):
        op_rows = g.query(_SELECT_DRAFT_OPERATORS,
                          params={"ids": chunk}).result_set
        for row in op_rows:
            oid = row[0] if row else None
            if not oid or oid in promoted_ops:
                continue
            promoted_ops.add(oid)
            g.query(_PROMOTE_OPERATOR, params={"oid": oid})
            sdk._emit_event("OperatorPromoted", id=oid,
                            point=sdk.get_point(oid))
            operators_promoted += 1

    # ── 1c. Write-trigger dirty-mark (claims + reverse-BFS neighbors) ─
    # The promotion is a raw SET (never a per-point promote_point call), so
    # EP would not otherwise know these claims are stale.
    if draft_ids:
        sdk._mark_dirty(draft_ids)

    # ── 2. Run EP — whole-graph stabilization, posteriors persisted ───
    ep_ran = False
    try:
        sdk.dream(mode="full", require_calibration=False, warm_start=False)
        ep_ran = True
    except Exception as exc:  # noqa: BLE001, RUF100
        logger.warning(
            "activate_beliefs: EP pass failed after promoting %d points / "
            "%d operators — claims remain unmeasured: %s",
            points_promoted, operators_promoted, exc, exc_info=True)

    # ── 3. EP-state census (post-pass) ────────────────────────────────
    total = _scalar(g.query(_COUNT_POINTS_TOTAL).result_set)
    with_ep = _scalar(g.query(_COUNT_POINTS_WITH_EP).result_set)
    points_unmeasured = max(total - with_ep, 0)

    return {
        "points_promoted": points_promoted,
        "operators_promoted": operators_promoted,
        "ep_ran": ep_ran,
        "ep_engine": EP_ENGINE,
        "points_with_ep": with_ep,
        "points_unmeasured": points_unmeasured,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    }
