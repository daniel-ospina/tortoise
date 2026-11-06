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
IMPL/NAND is EP-inert for ever (``operators == 0`` AND ``factor_edges == 0``
— a direct IMPL/NAND between two claims is still a factor, #888 W5), and a
graph of IMPL-only operators over the no-source ``Beta(1,1)`` prior is a fixed
point, so every posterior is exactly ``0.50`` and the belief layer contributes
nothing while COVERAGE (``points_with_ep``) reads complete.
``points_off_neutral`` / ``points_moved_off_neutral`` / ``operators`` /
``factor_edges`` / ``factors`` / ``no_op_reason`` are that half, and
``measurement_posture`` publishes the populations built from them. The
state's own bar is deliberately NOT re-cut here (that is #8486's recorded
disclosure rule and an open owner decision) — see
``measurement_posture.belief_layer_state``.

Hermetic by construction: the module imports only the standard library
(``logging``, ``time``, ``math``, ``sys``);
``activate_beliefs`` takes an already-constructed SDK (or a stub) so it can
be unit-tested without a database.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
# The preamble above is PINNED to exactly `import sys` by
# tests/test_entry_point_python_guard.py (`_guard_index`), so `math` — #10137's
# `math.isfinite` — sits BELOW the guard with the rest of the stdlib imports
# rather than in isort's alphabetical slot above it.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/ep_activation.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

import logging
import math
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

# ── #10137: the BELIEF-ELIGIBLE denominator ──────────────────────────────
# ``_COUNT_POINTS_TOTAL`` is every non-operator ``Point``, and on an eval graph
# that population also carries the EPISODIC SUBSTRATE: the v2 ingest writes one
# ``:Point`` per conversation turn (``ingest_v2.py:611-618``, kind ``event``
# with ``speaker`` set) and one per transcript chunk (``:637-643``, kind
# ``session-transcript``). No factor reaches those nodes, so EP can never write
# one, and counting them in the denominator makes ``points_with_ep / points`` a
# floor whose dilution is unbounded (measured 2,696 / 27,608 = 9.77% on the
# 24-question sample, ``docs/evidence/10137-corpus-ep-census/``).
#
# ⛔ ``is_episodic`` is NOT the discriminator, and using it is how this fix was
# wrong on its first pass (review P0): the ingest stamps ``is_episodic=True`` on
# the EXTRACTED CLAIMS TOO (``ingest_v2.py:275-290``, kind ``statement``). The
# flag is the QUOTA discriminator (``tortoise/quota.py:1024-1030`` counts only
# non-episodic points, which is why the eval ingest marks everything), not a
# belief/evidence one — filtering on it excluded every claim EP stamps: 3 claims
# + 2 IMPL read ``belief_eligible_total = 0`` against ``points_with_ep = 3``.
# The substrate is identified by the SHAPE its writer stamps, the one property
# pair the two substrate writers set and the claim writer does not.
#
# ⛔ The published point-level ratio is NOT renamed/"fixed" here — it is a real
# measurement of a real population, and the review that renamed the evidence key
# ``claim_coverage`` → ``point_coverage`` (#10137 review P2) is respected: that
# denominator cannot support a claim-coverage name. What was MISSING is a
# denominator that CAN. These cells are it, published ALONGSIDE the point-level
# pair so the two populations are never conflated again.
#
# A graph with no substrate nodes reads identically in both pairs, which is the
# correct answer, not a fallback.
# ``coalesce`` is not decoration: ``NOT (NULL = 'x')`` is NULL in Cypher, so a
# ``:Point`` written with no ``pointKind`` (reachable — the projection fold is a
# bare ``MERGE (n:Point {id})`` + conditional ``SET``) would fall out of BOTH
# populations, leaving a hole in the partition that no cell reports (review
# cycle 2, P2). Total-ising the predicate keeps
# ``points_substrate + belief_eligible_total == points_with_ep +
# points_unmeasured`` an invariant a test can hold.
#
# The event side requires ``is_episodic`` and NOT ``speaker``: ``speaker``
# arrived with delta 5 (#721), so pre-#721 turns carry ``pointKind='event'`` +
# ``is_episodic=true`` and no speaker, and `tortoise/session_reinjection.py:213-220`
# already ruled that requiring it "would silently exclude legacy turns for no
# additional exclusion power" — here it would do the opposite, absorbing legacy
# turn EVIDENCE into the belief denominator (review cycle 2, P1).
# Residual, named rather than absorbed: a kind-``event`` point with NEITHER flag
# (the demo seed, ``tortoise/hosted_api.py:12173``) is counted as belief-eligible.
# Excluding it needs the content-shape decision the repo owns in
# ``_TURN_SHAPE_FILTER``, not a second predicate here.
_SUBSTRATE_PREDICATE = (
    "(coalesce(n.pointKind, '') = 'session-transcript' "
    " OR (coalesce(n.pointKind, '') = 'event' "
    "     AND coalesce(n.is_episodic, false) = true)) "
)
_COUNT_POINTS_SUBSTRATE = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
    f"AND {_SUBSTRATE_PREDICATE}"
    "RETURN count(n)"
)
_COUNT_BELIEF_ELIGIBLE_TOTAL = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
    f"AND NOT {_SUBSTRATE_PREDICATE}"
    "RETURN count(n)"
)
_COUNT_BELIEF_ELIGIBLE_WITH_EP = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
    f"AND NOT {_SUBSTRATE_PREDICATE}"
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

#: Graph census of OPERATOR nodes (#10137).
#:
#: Widened to the ENGINE's operator predicate, NOT `_SELECT_DRAFT_OPERATORS`'s
#: narrower ``is_operator:true`` one (#10137 review, P1). `ep.py` treats a node
#: as an operator when ``is_operator = true OR op_type IS NOT NULL`` (its
#: operator-mediated selections), and the projection layer's canonical test is
#: ``bool(is_operator or op_type)``. Counting only the flag MISSES legacy
#: ``op_type``-only operators that EP consumes as factors, so it would publish
#: "this graph mints no operators" for a graph whose operators are merely
#: spelled differently.
#:
#: ⚠️ THE SAME PREDICATE ALSO ADMITS A HYBRID CLAIM — and that is the ENGINE's
#: own ambiguity, reproduced faithfully rather than resolved here. A hybrid
#: ``create_point(op_type=...)`` node is ``is_operator = false`` WITH
#: ``op_type``: the operator-side selections admit it (`ep.py:1456`, `:1804`,
#: `:1870`, `:2573` — all four spell this exact predicate), and
#: `ep._non_operator` admits the SAME node as a CLAIM on the participant side
#: (`ep.py:114-118`: "``is_operator = false`` — the modern plain Point AND the
#: hybrid … are CLAIMS"). So one node can appear in BOTH this census and
#: ``points_with_ep`` / ``points_unmeasured``.
#:
#: Consequences, stated so a reader does not over-read the cell:
#: * ``no_operators`` means "no operator-SHAPED node", not "no
#:   ``is_operator = true`` node". A graph whose only operator-shaped node is
#:   an edge-less hybrid reads ``operators: 1`` + ``factor_edges: 0`` and is
#:   diagnosed ``no_ep_factors`` ("operators exist, none reached the window") —
#:   truthful, and the NON-structural side, which is the safe direction for a
#:   verdict that routes a fix.
#: * The narrower ``is_operator IS NULL AND op_type IS NOT NULL`` spelling was
#:   REJECTED: it would make this census disagree with the engine's own
#:   operator selection, which is the definition the cell exists to report.
#: * Reachability is narrow and stated: no in-repo writer passes ``op_type`` to
#:   ``create_point`` (``ingest_v2`` mints real operators via
#:   ``create_operator``), so a benchmark run cannot hit the overlap; a graph
#:   built through the public ``create_point(op_type=…)`` shape can.
_COUNT_OPERATORS = (
    "MATCH (o:Point) "
    "WHERE (o.is_operator = true OR o.op_type IS NOT NULL) "
    "RETURN count(o)"
)

#: Direct factor edges: an ``IMPL``/``NAND`` whose BOTH endpoints are claims.
#:
#: An IMPL/NAND with NO operator node between its endpoints is still a factor
#: (#888 W5), so ``operators == 0`` alone does NOT prove the graph is
#: structurally EP-inert. The STRUCTURAL ``no_operators`` verdict therefore
#: requires THIS census to be ``0`` as well (#10137 review, P1).
#:
#: Deliberately restricted to CLAIM → CLAIM: an operator-mediated factor mints
#: premise→op and op→conclusion edges, and counting those would make the cell
#: include every ordinary factor while its name promises only the operator-less
#: ones (#10137 review 2, P2). The endpoint predicate mirrors `ep.py`'s direct-
#: edge arms (a non-operator claim is ``is_operator`` false/absent AND
#: ``op_type IS NULL``).
#:
#: ⚠️ It deliberately does NOT carry the other conjuncts those arms carry —
#: `epistemic_disjunction(a/b)` and `_live_only(a.status/b.status)`
#: (`ep.py:1939-1947`) — and the two omissions are safe for DIFFERENT reasons,
#: which the earlier wording wrongly merged into one. `_live_only` excludes
#: draft AND terminal endpoints (an `outdated` claim is not consumable today);
#: `epistemic_disjunction` restricts an endpoint to the EPISTEMIC_LABELS set and
#: says nothing about status. Neither omission can UNDER-count, and over-counting
#: is what keeps the verdict non-structural — which is the property this cell
#: needs. That is a SHAPE census, not a CONSUMABILITY census, for a reason
#: specific to the verdict it
#: feeds: ``no_operators`` claims the graph is structurally EP-inert — "no
#: factor reaches a claim, so nothing EP does can ever help" — a PERMANENT
#: claim. An edge whose endpoint is ``outdated`` is not consumable TODAY, but
#: reviving that claim makes it a factor again, so counting it keeps the reason
#: at ``no_ep_factors`` ("operators exist, none reached the window" — true)
#: instead of the structural verdict (false). Failing toward the NON-structural
#: reason is the same discipline the classifier applies to an UNKNOWN census
#: ("a fabricated structural cause is worse than an unclassified one"), and it
#: is why `_COUNT_OPERATORS` likewise counts draft/terminal operator nodes.
#:
#: Residual, tracked in #10223: a `stale_factor`-style reason ("a factor
#: exists but no endpoint is live") would need a LIVENESS-FILTERED census beside
#: this one. That is a new cell with its own vocabulary, not a predicate tweak
#: here — the shape cell must keep its over-count for `no_operators` to stay
#: truthful.
_COUNT_FACTOR_EDGES = (
    "MATCH (a)-[r:IMPL|NAND]->(b) "
    "WHERE (a.is_operator IS NULL OR a.is_operator = false) "
    "AND a.op_type IS NULL "
    "AND (b.is_operator IS NULL OR b.is_operator = false) "
    "AND b.op_type IS NULL "
    "RETURN count(r)"
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


def _scalar_or_none(result_set) -> int | None:
    """First cell of a single-row count query, ``None`` when it was not read.

    A lossy "absent result set → ``0``" twin deliberately does NOT exist here
    (#10137 review 3, P3): ``0`` is a MEASUREMENT of the operator census, where
    it is published as a named STRUCTURAL cause, so the two readings cannot
    share a number. A count query that returned no row failed; it did not
    measure a zero.
    """
    if not result_set or not result_set[0]:
        return None
    try:
        return max(int(result_set[0][0]), 0)
    except (TypeError, ValueError, IndexError):
        return None


def _census(g, query: str) -> int | None:
    """Run a single-cell census query, degrading a failure to UNKNOWN.

    #10137 review (P2): an unguarded census read propagated out of
    ``activate_beliefs``, so a MEASUREMENT query's failure discarded a pass
    that had already succeeded — the promotion writes and the posteriors were
    committed, yet the caller's fail-open handler turned the whole question
    into ``activation_failed``. A census that cannot be read is UNKNOWN; it is
    never a reason to throw away a completed measurement.
    """
    try:
        return _scalar_or_none(g.query(query).result_set)
    except Exception as exc:  # noqa: BLE001, RUF100
        logger.warning(
            "activate_beliefs: census query failed, reporting UNKNOWN: %s",
            exc, exc_info=True)
        return None


def _census_ids(g, query: str) -> tuple[set[str], int] | None:
    """:func:`_census` for the claim-state read — ``(ids, rows_read)``, or
    UNKNOWN.

    The ROW COUNT is returned beside the ids deliberately (#10137 review 3,
    P2): ``rows_read == 0`` while the coverage census reports claims carrying
    EP state is provably a BROKEN read — the two queries share one WHERE
    clause — whereas the id set alone cannot tell an all-neutral graph (a
    legitimate empty set) from a query that matched nothing. Without it a
    wrong predicate reports "the belief layer ran and moved nothing" on every
    graph, the silent zero this census exists to close.
    """
    try:
        rows = g.query(query).result_set or ()
        return off_neutral_ids(rows), len(rows)
    except Exception as exc:  # noqa: BLE001, RUF100
        logger.warning(
            "activate_beliefs: claim-state census failed, reporting UNKNOWN: "
            "%s", exc, exc_info=True)
        return None


def _optional_int(value) -> int | None:
    """A manifest cell that may be genuinely UNKNOWN (``None``).

    A count that could not be read must not become a fabricated ``0``. ``0``
    from :data:`_COUNT_OPERATORS`
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
    ``None``) is NOT counted: unmeasured is not a contribution. It is NOT
    counted in ``points_unmeasured`` either — every row of
    :data:`_SELECT_CLAIM_EP_STATES` satisfies the SAME predicate as
    :data:`_COUNT_POINTS_WITH_EP`, so such a claim is counted as COVERED by
    ``points_with_ep`` and appears in NEITHER population; the RESIDUAL below
    is the cell that would surface it. A NON-FINITE mean is excluded
    explicitly (#10137 review 3, P2): ``abs(nan - 0.5) > 1e-6`` is ``False``,
    so before the guard an undefined posterior was kept out of the
    CONTRIBUTION population only by floating-point accident — a silent
    dependency on the comparison, not on the value.

    ⚠️ RESIDUAL, deliberately not closed here: a claim whose persisted
    parameters are non-finite still counts as COVERED (``points_with_ep``
    counts EP state, not usable EP state), so its question still lands in
    ``neutral_questions`` — "the belief layer ran and moved nothing" — because
    that population is derived per QUESTION from ``points_off_neutral == 0``
    with ``points_with_ep > 0``, not from any per-row verdict. Closing it needs
    an "unusable persisted state" census cell feeding ``points_unmeasured``;
    that is a new cell with its own reconciliation, tracked in #10210 rather
    than smuggled in as a one-line predicate change.
    """
    return len(off_neutral_ids(rows))


def off_neutral_ids(rows) -> set[str]:
    """The claim IDS whose persisted posterior is not the neutral 0.50.

    Same read as :func:`points_off_neutral`, but keeping the id so a PRE-pass
    and POST-pass census can be DIFFERENCED (#10137 review). That difference
    is the only honest way to say "this pass moved these claims": a claim
    created with a non-uniform baseline (``set_point_baseline``) sits off the
    neutral mean with EP never having run, so a bare off-neutral COUNT cannot
    distinguish pre-existing prior from contribution.

    Rows that carry no usable id are dropped: an id-less row cannot be
    differenced, and a count that silently includes it would not reconcile
    with the set arithmetic.
    """
    ids: set[str] = set()
    for row in rows or ():
        if not row:
            continue
        cells = list(row)
        while len(cells) < 5:  # a projection may omit trailing null cells
            cells.append(None)
        pid, pa, pb, ea, eb = cells[:5]
        if not pid:
            continue
        mean = read_confidence({"posterior_alpha": pa, "posterior_beta": pb,
                                "ep_alpha": ea, "ep_beta": eb})
        if mean is None or not math.isfinite(mean):
            continue
        if abs(mean - NEUTRAL_MEAN) > NEUTRAL_EPS:
            ids.add(str(pid))
    return ids


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
        points_with_ep, points_unmeasured, points_substrate,
        belief_eligible_total, belief_eligible_with_ep,
        belief_eligible_unmeasured, operators, factor_edges,
        points_off_neutral, points_moved_off_neutral, factors, no_op_reason,
        duration_ms}``

    #10137 adds the cells that say whether the coverage MEANT anything,
    because coverage alone cannot. Every one of them is ``None`` (UNKNOWN)
    when it could not be MEASURED — a census that failed is never published
    as a ``0``, because ``0`` is itself a published finding here.

    * ``points_off_neutral`` — the STATE census: non-operator claims whose
      persisted posterior mean is not the neutral ``0.50`` of the unmeasured
      ``Beta(1,1)`` prior. On a graph of IMPL-only operators over uniform
      priors every posterior IS ``0.50`` (the uniform prior is that model's
      fixed point), so this reads ``0`` while ``points_with_ep`` reads every
      claim — the shape that could otherwise publish a "belief-activated"
      number for a run in which the belief layer moved nothing.
    * ``points_moved_off_neutral`` — the CONTRIBUTION proper: claims that were
      NOT off-neutral before this pass and are after it. ``points_off_neutral``
      alone cannot say this: a claim given a non-uniform baseline
      (``set_point_baseline``) sits off the neutral mean with EP never having
      run, so it would be counted as a contribution it is not. UNKNOWN when
      the pass did not run or either census read failed.
    * ``operators`` — a graph census of operator nodes, over the ENGINE's
      predicate (``is_operator`` OR ``op_type``).
    * ``factor_edges`` — a census of direct ``IMPL``/``NAND`` edges. An edge
      between two claims with no operator node between them is still a factor
      (#888 W5), so ``operators == 0`` on its own does NOT make a graph
      structurally EP-inert; a STRUCTURAL verdict needs BOTH to be a measured
      ``0``.
    * ``points_substrate`` / ``belief_eligible_*`` — the population split
      behind the coverage pair. ``points_with_ep`` counts every non-operator
      ``Point``, and an eval graph's population is dominated by the EPISODIC
      substrate the ingest writes as points (one per turn, kind ``event`` with
      ``speaker``; one per transcript chunk, kind ``session-transcript``) — evidence that no factor can ever reach
      and EP can never write. Folding it into the denominator makes the
      published ratio a floor whose dilution is unbounded (measured 9.77% on
      the 24-question sample, `docs/evidence/10137-corpus-ep-census/`).
      ``belief_eligible_total`` / ``_with_ep`` / ``_unmeasured`` are the SAME
      census over the non-substrate population (turn and transcript-chunk
      points excluded; the claims EP stamps DO carry ``is_episodic``, which is
      why that flag is not the discriminator), and
      ``points_substrate`` publishes the excluded count so nothing is hidden.
      These are ADDITIVE: ``points_with_ep`` / ``points_unmeasured`` keep
      their established meaning, because a key that silently changes
      denominator re-cuts every number ever published from it.
    * ``factors`` — the operators THIS PASS selected, read from the dream
      result's ``budget_used`` (full mode). Deliberately not the health
      surface's ``operator_counts``: a full pass is a batch loop and the
      Dreamer's ``_last_operator_count`` is written PER BATCH, so the health
      cell holds the last batch's selection, not the pass's.
      NOTE: ``factors``/``no_op_reason`` are only read for a pass that
      RETURNED — ``None`` here is UNKNOWN, never a claim that the pass wrote
      nothing (``ep_ran`` says whether it ran at all).

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

    # ── 1d. PRE-pass contribution baseline (#10137 review, P1) ────────
    # `points_off_neutral` alone cannot separate "this pass MOVED a claim"
    # from "this claim was ALREADY off the neutral mean": a point created
    # with a non-uniform baseline (`set_point_baseline`) sits off-neutral with
    # EP never having run. The CONTRIBUTION is the claims NEWLY off-neutral,
    # so the set is captured BEFORE the pass and differenced after it.
    off_neutral_before = _census_ids(g, _SELECT_CLAIM_EP_STATES)
    # The SAME "0 rows while claims carry EP state is a broken read" rule the
    # post-pass read applies (#10137 review 4, P3). `_census_ids` returns the
    # row count for exactly this, and the baseline is the read where a silent
    # zero is WORST: an empty baseline makes every off-neutral claim look NEWLY
    # off-neutral, so a broken pre-pass read credits THIS pass with a prior it
    # did not create — the defect `points_moved_off_neutral` exists to prevent,
    # in the opposite direction. The coverage count is issued only in the empty
    # case (the only case that needs it), against the same WHERE clause as the
    # row read, so a MEASURED empty graph (all-neutral, no EP state) stays a
    # legitimate baseline.
    before_cov = None
    if off_neutral_before is not None and off_neutral_before[1] == 0:
        before_cov = _census(g, _COUNT_POINTS_WITH_EP)
    if (off_neutral_before is not None and off_neutral_before[1] == 0
            and (before_cov is None or before_cov)):
        # ``before_cov is None`` takes the SAME verdict as a disagreement: the
        # census that would prove the baseline empty could not be read at all,
        # so an empty row set is UNPROVEN — and an unproven empty baseline is
        # precisely what credits this pass with a prior it did not create. Fail
        # closed (#10137 review 5, P3).
        logger.warning(
            "activate_beliefs: PRE-pass claim-state census returned 0 rows "
            "with no usable coverage count (%s) — broken read, publishing "
            "UNKNOWN contribution instead of crediting the pass with a prior",
            "unread" if before_cov is None else f"points_with_ep={before_cov}")
        off_neutral_before = None

    # ── 2. Run EP — whole-graph stabilization, posteriors persisted ───────
    ep_ran = False
    ep_error: str | None = None
    dream_result = None
    try:
        dream_result = sdk.dream(
            mode="full", require_calibration=False, warm_start=False)
        ep_ran = True
    except Exception as exc:  # noqa: BLE001, RUF100
        # #10137 review 14 (P2): the DIAGNOSIS goes on the manifest, not only
        # into the log. This fail-open return — the common failure shape, since
        # a raise inside `dream` is the expected #3154/#3139 breakage — used to
        # carry `ep_ran: false` and NO `error`, while `run.py`'s outer handler
        # (the rarer shape: `activate_beliefs` itself raising) writes both. The
        # summary's `failed`/`errors` cells keyed on `error`, so a run in which
        # EVERY pass raised published `failed: 0` and an empty `errors` list —
        # the one number a reader has for "did anything break" said nothing
        # did. `_classify_question` already resolved the question correctly (the
        # `ep_ran` arm), so only the published diagnosis was missing.
        ep_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "activate_beliefs: EP pass failed after promoting %d points / "
            "%d operators — claims remain unmeasured: %s",
            points_promoted, operators_promoted, exc, exc_info=True)

    # ── 3. EP-state census (post-pass) ────────────────────────────────
    # The coverage cells used to read a bare `result_set[0][0]` (0 on an absent
    # result set) while the contribution cells were already UNKNOWN-aware — the
    # same defect one layer down, and worse: `points_with_ep == 0` DRIVES the
    # per-question verdict and
    # the run's posture, so a failed read was published as "nothing was
    # measured" (#10137 review 2, P1). Every census read degrades to UNKNOWN.
    total = _census(g, _COUNT_POINTS_TOTAL)
    with_ep = _census(g, _COUNT_POINTS_WITH_EP)
    points_unmeasured = (max(total - with_ep, 0)
                         if total is not None and with_ep is not None
                         else None)
    # #10137: the belief-eligible population (claims + belief-bearing parts),
    # with the episodic substrate counted SEPARATELY rather than folded into
    # the denominator. See the query block for why this is additive and not a
    # redefinition of ``points_unmeasured``.
    points_substrate = _census(g, _COUNT_POINTS_SUBSTRATE)
    eligible_total = _census(g, _COUNT_BELIEF_ELIGIBLE_TOTAL)
    eligible_with_ep = _census(g, _COUNT_BELIEF_ELIGIBLE_WITH_EP)
    eligible_unmeasured = (
        max(eligible_total - eligible_with_ep, 0)
        if eligible_total is not None and eligible_with_ep is not None
        else None)
    # #10137: coverage is not contribution. Both are measured, every run.
    #
    # The GRAPH SHAPE (`operators`, `factor_edges`) is a property of the graph,
    # so it is read whether or not the pass ran. The CONTRIBUTION cells
    # describe what THIS pass did, so a pass that never ran publishes UNKNOWN
    # for them — publishing a contribution figure attributed to a pass that
    # raised is the false number this whole PR exists to stop
    # (#10137 review, P1).
    operators = _census(g, _COUNT_OPERATORS)
    factor_edges = _census(g, _COUNT_FACTOR_EDGES)
    after = _census_ids(g, _SELECT_CLAIM_EP_STATES)
    # A claim-state read that saw ZERO rows while the coverage census — the
    # SAME WHERE clause — counted claims carrying EP state is PROVABLY a
    # broken read, not a measured zero (#10137 review 3, P2). `_scalar_or_none`
    # closes this for the count cells; a row-set read needs its own check,
    # because an empty set is also the legitimate answer for an all-neutral
    # graph and those two must not be published as one number. An UNREAD
    # ``with_ep`` is the same verdict: the count that would prove the read saw
    # an empty population could not be read, so zero rows is unproven — the
    # cell is UNKNOWN, never a measured 0 (#10137 review 5, P3).
    if after is not None and after[1] == 0 and (with_ep is None or with_ep):
        logger.warning(
            "activate_beliefs: claim-state census returned 0 rows with no "
            "usable coverage count (%s) — broken read, reporting UNKNOWN "
            "instead of a fabricated off-neutral 0",
            "unread" if with_ep is None else f"points_with_ep={with_ep}")
        after = None
    off_neutral = (len(after[0]) if after is not None else None)
    # The CONTRIBUTION proper: claims NEWLY off the neutral mean. UNKNOWN when
    # the pass did not run or when EITHER census read failed — a baseline we
    # could not take cannot yield a contribution count.
    moved_off_neutral = (
        len(after[0] - off_neutral_before[0])
        if ep_ran and after is not None
        and off_neutral_before is not None else None)
    # The pass's OWN operator selection (full mode's accumulated count), not
    # the health surface's per-batch `_last_operator_count`.
    factors = (_optional_int(dream_result.get("budget_used"))
               if isinstance(dream_result, dict) else None)
    # Only a pass that RETURNED has a reason; a raised pass is reported by
    # `ep_ran`/`error` (and now really is — see `ep_error` above), and a stale
    # reason from an earlier pass would be a lie.
    no_op_reason = _read_no_op_reason(sdk) if ep_ran else None

    return {
        "points_promoted": points_promoted,
        "operators_promoted": operators_promoted,
        "ep_ran": ep_ran,
        #: ``None`` on success, else ``"<ExcType>: <message>"``. Same shape
        #: ``run.py::_activate_beliefs_for_question`` writes when the call
        #: itself raises, so the summary's failure cells see BOTH failure
        #: shapes through one key instead of only the rarer one.
        "error": ep_error,
        "ep_engine": EP_ENGINE,
        "points_with_ep": with_ep,
        "points_unmeasured": points_unmeasured,
        # #10137: the two populations the point-level pair conflates. The
        # episodic substrate is EVIDENCE (turn/chunk points)
        # and is never a belief, so it is excluded from the denominator and
        # reported here instead of silently inflating it.
        "points_substrate": points_substrate,
        "belief_eligible_total": eligible_total,
        "belief_eligible_with_ep": eligible_with_ep,
        "belief_eligible_unmeasured": eligible_unmeasured,
        # #10137 contribution census — `points_with_ep == points` while
        # `points_off_neutral == 0` is a fully covered, zero-contribution graph.
        "operators": operators,
        "factor_edges": factor_edges,
        "points_off_neutral": off_neutral,
        "points_moved_off_neutral": moved_off_neutral,
        "factors": factors,
        "no_op_reason": no_op_reason,
        "duration_ms": round((time.monotonic() - t0) * 1000),
    }
