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

#10137 — the manifest also answers "did the pass MOVE anything?". Arming
beliefs is necessary but not sufficient: a graph whose extraction minted no
IMPL/NAND is EP-inert for ever (``operators == 0``), and a graph of IMPL-only
operators over the no-source ``Beta(1,1)`` prior is a fixed point, so every
posterior is exactly ``0.50`` and the belief layer contributes nothing while
COVERAGE (``points_with_ep``) reads complete. ``points_off_neutral`` /
``operators`` / ``factors`` / ``no_op_reason`` are that half, and
``measurement_posture`` publishes the populations built from them. The
state's own bar is deliberately NOT re-cut here (that is #8486's recorded
disclosure rule and an open owner decision) — see
``measurement_posture.belief_layer_state``.

Hermetic by construction: the module imports only ``logging`` and ``time``;
``activate_beliefs`` takes an already-constructed SDK (or a stub) so it can
be unit-tested without a database.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/ep_activation.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

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

# ── #10137: the CONTRIBUTION census ──────────────────────────────────────
# ``points_with_ep`` answers "is there EP state on this claim". It does NOT
# answer "did EP move it": on a graph whose operators are IMPL over the
# no-source ``Beta(1,1)`` prior, the uniform prior IS the model's fixed point,
# so every posterior is exactly 0.50 and ``points_with_ep == points`` while
# the belief layer contributed nothing (#10137). These cells answer the second
# question and the two questions that explain a zero.

#: The neutral mean of the unmeasured ``Beta(1,1)`` prior (#2598).
NEUTRAL_MEAN = 0.5
#: Anything closer than this to :data:`NEUTRAL_MEAN` counts as neutral. An
#: IMPL over the uniform prior lands ~1.9e-15 away (floating-point noise); a
#: real move is >= 0.03 — measured on the #10137 reproduction
#: (`set_point_baseline(6.0, 2.0)` on the source moves three claims to
#: 0.75 / 0.5945 / 0.5352).
NEUTRAL_EPS = 1e-6

#: Graph census of OPERATOR nodes. ``0`` means the extraction minted no
#: IMPL/NAND, so the graph is EP-inert for ever and no arm can change that
#: (#10137). Mirrors the operator predicate `_SELECT_DRAFT_OPERATORS` uses.
_COUNT_OPERATORS = (
    "MATCH (o:Point {is_operator:true}) "
    "RETURN count(o)"
)

#: Every non-operator claim carrying ANY persisted EP state, with BOTH
#: candidate pairs. The off-neutral count is computed in PYTHON from
#: :func:`read_confidence` rather than as Cypher arithmetic: the census must
#: agree with the canonical read by construction, including its per-field
#: posterior-over-prior coalesce and its degenerate-state refusals (a second
#: implementation of that rule in Cypher is exactly how a census and its read
#: surface drift apart).
_SELECT_CLAIM_EP_STATES = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
    "AND (n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL) "
    "RETURN n.id, n.posterior_alpha, n.posterior_beta, "
    "n.ep_alpha, n.ep_beta"
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


def _optional_int(value) -> int | None:
    """A manifest cell that may be genuinely UNKNOWN (``None``).

    Distinct from :func:`_scalar` on purpose: a count that could not be read
    must not become a fabricated ``0``. ``0`` from :data:`_COUNT_OPERATORS`
    is a MEASUREMENT ("this graph mints no operators"); a stub or older SDK
    that never reported the cell is not.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def points_off_neutral(rows) -> int:
    """Count claims whose PERSISTED posterior is not the neutral 0.50.

    ``rows`` is :data:`_SELECT_CLAIM_EP_STATES`'s result set — one row per
    claim carrying EP state, ``(id, posterior_alpha, posterior_beta,
    ep_alpha, ep_beta)``. A row is off-neutral when
    :func:`read_confidence` (the ONE canonical posterior read) returns a mean
    at least :data:`NEUTRAL_EPS` away from :data:`NEUTRAL_MEAN`.

    A row whose state is absent/malformed (``read_confidence`` returns
    ``None``) is NOT counted: unmeasured is not a contribution, and it is
    already visible as ``points_unmeasured``.
    """
    count = 0
    for row in rows or ():
        if not row:
            continue
        cells = list(row)
        while len(cells) < 5:  # a projection may omit trailing null cells
            cells.append(None)
        _, pa, pb, ea, eb = cells[:5]
        mean = read_confidence({"posterior_alpha": pa, "posterior_beta": pb,
                                "ep_alpha": ea, "ep_beta": eb})
        if mean is None:
            continue
        if abs(mean - NEUTRAL_MEAN) > NEUTRAL_EPS:
            count += 1
    return count


def _read_no_op_reason(sdk) -> str | None:
    """``no_op_reason`` from the SDK's own health surface (#3139), guarded.

    UNKNOWN (``None``) — never a fabricated reason — when the SDK has no
    ``dream_health_check``, when the call raises, or when the surface carries
    no reason. A reason is only meaningful for a pass that RETURNED; a raised
    pass is reported through ``ep_ran``/``error`` instead, so the caller skips
    this read in that case.
    """
    checker = getattr(sdk, "dream_health_check", None)
    if not callable(checker):
        return None
    try:
        health = checker()
    except Exception as exc:  # noqa: BLE001, RUF100
        logger.debug("activate_beliefs: dream_health_check failed: %s", exc)
        return None
    if not isinstance(health, dict):
        return None
    reason = health.get("no_op_reason")
    return reason if isinstance(reason, str) and reason else None

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
        points_with_ep, points_unmeasured, operators, points_off_neutral,
        factors, no_op_reason, duration_ms}``

    ``points_with_ep`` / ``points_unmeasured`` count NON-operator points
    carrying / lacking persisted EP state after the pass — COVERAGE.

    #10137 adds the three cells that say whether the coverage MEANT anything,
    because coverage alone cannot:

    * ``points_off_neutral`` — the CONTRIBUTION census. Non-operator claims
      whose persisted posterior mean is not the neutral ``0.50`` of the
      unmeasured ``Beta(1,1)`` prior. On a graph of IMPL-only operators over
      uniform priors every posterior IS ``0.50`` (the uniform prior is that
      model's fixed point), so this reads ``0`` while ``points_with_ep``
      reads every claim — the shape that could otherwise publish a
      "belief-activated" number for a run in which the belief layer moved
      nothing.
    * ``operators`` — a graph census of operator nodes. ``0`` means the
      extraction minted no IMPL/NAND, so nothing EP does can ever reach a
      claim: EP-inert for ever, whatever the arm does.
    * ``factors`` — the operators THIS PASS selected, read from the dream
      result's ``budget_used`` (full mode). Deliberately not the health
      surface's ``operator_counts``: a full pass is a batch loop and the
      Dreamer's ``_last_operator_count`` is written PER BATCH, so the health
      cell holds the last batch's selection, not the pass's.
    * ``no_op_reason`` — the SDK's own diagnosis of a pass that returned with
      no belief write (#3139): ``no_ep_factors`` (no factor reaches the
      window), ``stale_run_guard`` (#1163), ``silent_no_op``, ... ``None``
      when the SDK is too old / a stub — UNKNOWN, never invented.

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
    dream_result = None
    try:
        dream_result = sdk.dream(
            mode="full", require_calibration=False, warm_start=False)
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
    # #10137: coverage is not contribution. Both are measured, every run.
    operators = _scalar(g.query(_COUNT_OPERATORS).result_set)
    off_neutral = points_off_neutral(
        g.query(_SELECT_CLAIM_EP_STATES).result_set)
    # The pass's OWN operator selection (full mode's accumulated count), not
    # the health surface's per-batch `_last_operator_count`.
    factors = (_optional_int(dream_result.get("budget_used"))
               if isinstance(dream_result, dict) else None)
    # Only a pass that RETURNED has a reason; a raised pass is reported by
    # `ep_ran`/`error`, and a stale reason from an earlier pass would be a lie.
    no_op_reason = _read_no_op_reason(sdk) if ep_ran else None

    return {
        "points_promoted": points_promoted,
        "operators_promoted": operators_promoted,
        "ep_ran": ep_ran,
        "ep_engine": EP_ENGINE,
        "points_with_ep": with_ep,
        "points_unmeasured": points_unmeasured,
        # #10137 contribution census — `points_with_ep == points` while
        # `points_off_neutral == 0` is a fully covered, zero-contribution graph.
        "operators": operators,
        "points_off_neutral": off_neutral,
        "factors": factors,
        "no_op_reason": no_op_reason,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    }
