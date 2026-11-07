"""#10137 — an operator minted over ALREADY-LIVE claims must still become a factor.

The bug this file pins is the belief layer contributing nothing on a question
that has BOTH evidence and an operator.

``activate_beliefs`` promoted draft operators only when they were incident to a
Point promoted in the SAME pass (``... c.id IN $ids``). An operator whose
endpoints were already live was therefore promoted by NOTHING: no point is
draft, so that query matched none, and ``sdk.promote_point`` cannot stand in —
it refuses an operator node (``{blocked: True, reason: 'is_operator'}``) rather
than sweeping its endpoints (``sdk.py::promote_point``).
``ep._live_only('o.status')`` then keeps the draft operator out of the factor
graph for ever.

That is not an exotic graph. ``ingest_v2`` mints every operator with
``promote_source=False`` (→ ``status='draft'``) and gates its POINT writes on an
``_existing_point_ids`` batch probe, so any RESUMED/partial ingest skips the
points an earlier pass already wrote while still writing the draft operator over
them. (The ``create_operator`` default is ``promote_source=True``, which writes
``status='live'`` — this is the INGEST's posture, not every writer's.)

Reproduced on an embedded graph before the fix (premise carries a real
``set_point_baseline(6.0, 2.0)``, conclusion is its IMPL target)::

    operators: 1, factors: 0, no_op_reason: 'no_ep_factors'
    premise   posterior (6.0, 2.0)   confidence 0.75     (its own baseline)
    conclusion posterior (None, None) confidence unmeasured

so the belief layer contributed nothing while the graph held both the evidence
and the operator that should carry it. After the fix::

    operators: 1, factors: 1, no_op_reason: None
    conclusion posterior (1.5714, 1.0717)  confidence 0.5945

The assertions below are on that OBSERVABLE — the conclusion claim's persisted
confidence — not on the query text, so a different route to the same wiring
still passes.

Hermetic embedded harness (``tests/test_dream_noop_3139.py`` pattern): a temp
``TortoiseSDK`` path, no server, no network, no dataset.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import ep_activation as epa  # noqa: E402, RUF100
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100

#: A real move is >= 0.03 (#10137's own calibration: an IMPL over the uniform
#: prior lands ~1.9e-15 from neutral, a genuine contribution 0.03+).
_REAL_MOVE = 0.03
#: The measured confidence the evidenced premise pushes its conclusion to. Pinned
#: so a silent change to the IMPL message math surfaces here rather than as a
#: shifted benchmark number.
_CONCLUSION_CONFIDENCE = 0.5945


@pytest.fixture
def sdk():
    d = tempfile.mkdtemp(prefix="tortoise_10137_promo_")
    s = TortoiseSDK(str(Path(d) / "test.db"))
    yield s
    s.close()
    shutil.rmtree(d, ignore_errors=True)


def _claim(sdk: TortoiseSDK, text: str, status: str) -> str:
    return sdk.create_point("statement", text, dedup=False, status=status)["id"]


def _operator_status(sdk: TortoiseSDK, oid: str) -> str | None:
    """The operator node's persisted ``status`` — the wiring, read back."""
    rows = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$oid}) RETURN o.status", params={"oid": oid}
    ).result_set
    return rows[0][0] if rows else None


def _confidence(sdk: TortoiseSDK, pid: str) -> float | None:
    return epa.read_confidence(sdk.get_point(pid) or {})


def _assert_contributed(sdk, manifest, conclusion, oid) -> None:
    """The observable: the operator is LIVE, it was selected as a factor, and
    its conclusion's persisted confidence moved off the neutral prior."""
    assert manifest["operators_promoted"] == 1, (
        "the draft operator was not promoted — operators_promoted="
        f"{manifest['operators_promoted']} (operators census "
        f"{manifest['operators']}). A draft operator is excluded from the "
        "factor graph by ep._live_only('o.status'), so the belief layer cannot "
        "reach its conclusion (#10137)."
    )
    status = _operator_status(sdk, oid)
    assert status == "live", f"operator {oid} status is {status!r}, not 'live'"
    assert manifest["factors"] == 1, (
        f"the pass selected {manifest['factors']} factor(s), expected 1 — "
        f"no_op_reason={manifest['no_op_reason']!r}"
    )
    assert manifest["no_op_reason"] is None, (
        "a pass that promoted a live factor still reports a no-op reason "
        f"{manifest['no_op_reason']!r}"
    )
    conf = _confidence(sdk, conclusion)
    assert conf is not None, (
        "the conclusion claim is still UNMEASURED after an operator over live "
        "claims was activated — the belief layer contributed nothing (#10137)"
    )
    assert conf > 0.5 + _REAL_MOVE, (
        f"the conclusion reads {conf!r}; its evidenced premise (0.75) should "
        "have pushed it above the neutral prior"
    )
    assert conf == pytest.approx(_CONCLUSION_CONFIDENCE, abs=5e-4), (
        f"conclusion confidence {conf!r} != the pinned "
        f"{_CONCLUSION_CONFIDENCE} — the IMPL message math moved"
    )


def test_an_operator_minted_over_live_claims_moves_its_conclusion(sdk):
    """The failure shape, smallest case: nothing is draft, so the incident-only
    promotion sweep had no candidate and the factor never existed."""
    premise = _claim(sdk, "premise", "live")
    conclusion = _claim(sdk, "conclusion", "live")
    sdk.set_point_baseline(premise, 6.0, 2.0)
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]

    manifest = epa.activate_beliefs(sdk)

    assert manifest["points_promoted"] == 0, (
        "both claims were already live, so no point should be promoted — a "
        "non-zero count means the fixture stopped testing the live-point path"
    )
    _assert_contributed(sdk, manifest, conclusion, oid)


def test_a_resumed_ingest_operator_is_promoted_on_the_next_pass(sdk):
    """The realistic path: pass 1 promotes the drafts and finds no factor; the
    resumed ingest then mints an operator over those now-live claims."""
    premise = _claim(sdk, "premise", "draft")
    conclusion = _claim(sdk, "conclusion", "draft")
    first = epa.activate_beliefs(sdk)
    assert first["operators"] == 0 and first["no_op_reason"] == "no_ep_factors", (
        "pass 1 runs over an operator-less graph, so it must report the "
        f"structural no-op; read {first['operators']} operators / "
        f"{first['no_op_reason']!r}"
    )

    # …the resumed ingest writes the operator over claims pass 1 already made live
    sdk.set_point_baseline(premise, 6.0, 2.0)
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]
    assert _operator_status(sdk, oid) == "draft", (
        "create_operator(promote_source=False) must mint a DRAFT operator — "
        "the precondition this test exists for"
    )

    manifest = epa.activate_beliefs(sdk)

    assert manifest["points_promoted"] == 0, (
        "pass 2 has nothing draft to promote, which is exactly why the "
        "incident-only sweep found no operator candidate"
    )
    _assert_contributed(sdk, manifest, conclusion, oid)


def test_an_all_draft_graph_still_promotes_its_operators(sdk):
    """No regression on the census path: the sweep is additive and a graph whose
    claims are all draft reaches the same state it reached before."""
    premise = _claim(sdk, "premise", "draft")
    conclusion = _claim(sdk, "conclusion", "draft")
    sdk.set_point_baseline(premise, 6.0, 2.0)
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]

    manifest = epa.activate_beliefs(sdk)

    assert manifest["points_promoted"] == 2
    _assert_contributed(sdk, manifest, conclusion, oid)


def test_the_gate_promotes_an_operator_once(sdk):
    """The CAS clause IS the guard, and it is pinned HERE, on a real graph.

    ``_PROMOTE_OPERATOR`` carries ``WHERE o.status = 'draft'`` so that a second
    caller — a concurrent pass, or a re-entrant one — cannot flip an operator
    that is already live, journal a second ``OperatorPromoted`` (which replay
    would apply again) or double-count ``operators_promoted``. Deleting that
    clause leaves the stub suite green, because ``tests/longmem_eval/
    test_ep_activation.py``'s ``cas_lost`` models the CAS RESULT rather than the
    query text. Measured without the clause on this engine: two ``True``
    returns and two events for one operator.

    Assertion is on the observable — one event, one count — not on the Cypher.
    """
    premise = _claim(sdk, "premise", "live")
    conclusion = _claim(sdk, "conclusion", "live")
    sdk.set_point_baseline(premise, 6.0, 2.0)
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]

    events: list[str] = []
    inputs: list[str] = []
    promoted: set[str] = set()
    g = sdk._get_proj().g
    real_emit = sdk._emit_event

    def _counting_emit(type_, **kwargs):
        # Count through the SDK's OWN emit surface: an event the gate never
        # emits cannot be counted here, so this cannot pass by accident.
        events.append(type_)
        return real_emit(type_, **kwargs)

    sdk._emit_event = _counting_emit
    try:
        first = epa._promote_operator_row(sdk, g, oid, promoted, inputs.append)
        # ⛔ The second call gets a FRESH ``promoted_ops`` set, because the
        # in-memory guard short-circuits before the query — passing the same set
        # twice would make this test pass with the CAS clause deleted. A
        # CONCURRENT writer is exactly the caller that has its own bookkeeping
        # and can only be stopped by the persistent state on the node.
        second = epa._promote_operator_row(sdk, g, oid, set(), inputs.append)
    finally:
        sdk._emit_event = real_emit

    assert first is True, "the first promotion of a draft operator must win"
    assert second is False, (
        "a second promotion of an ALREADY-LIVE operator returned True — the "
        "CAS clause (``WHERE o.status = 'draft'``) is gone, so a concurrent "
        "pass would double-count and journal a duplicate OperatorPromoted "
        "(#10137)"
    )
    assert _operator_status(sdk, oid) == "live"
    assert len(inputs) == 2, (
        f"the gate reported {inputs!r} endpoint id(s) across two calls; only "
        "the WINNING call may report them"
    )
    assert events.count("OperatorPromoted") == 1, (
        f"the gate journalled {events!r} — a lost CAS race must emit no second "
        "OperatorPromoted, or JSONL replay applies the promotion again (#10137)"
    )


def test_the_gate_still_refuses_an_operator_with_a_draft_endpoint(sdk):
    """R16 is not widened by the sweep. ``activate_beliefs`` promotes every
    draft Point in phase 1a, so the public flow cannot reach this state — the
    gate is pinned directly through ``_promote_operator_row``, because a future
    "simplification" of the gate is exactly the change that would let a live
    edge inherit a draft endpoint.

    The falsifier named in the assertion is the one this change actually
    shipped on its first pass: spelling the liveness predicate in CYPHER (as a
    ``WHERE`` over ``coalesce(x.status, 'live') <> 'draft'``). That spelling
    admits every TERMINAL status (retracted / superseded / outdated / archived,
    and the legacy ``deprecated``), which the SDK's ``(st or "live") ==
    "live"`` blocks — so the gate must stay the Python comparison, not a Cypher
    predicate.
    """
    premise = _claim(sdk, "premise", "live")
    conclusion = _claim(sdk, "conclusion", "draft")
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]

    # Drive the gate directly: a public ``activate_beliefs`` call would promote
    # the draft conclusion in phase 1a and then (correctly) promote the operator.
    marked: list[str] = []
    epa._promote_operator_row(
        sdk, sdk._get_proj().g, oid, set(), marked.append,
    )

    assert _operator_status(sdk, oid) == "draft", (
        "the operator went live while its conclusion endpoint is still draft — "
        "R16 says a draft endpoint blocks promotion (#10137)"
    )
    assert marked == [], (
        f"the gate reported {marked!r} as promoted while an endpoint was draft"
    )


def test_the_gate_refuses_a_terminal_endpoint(sdk):
    """The half-fixed gate, pinned. A draft-only predicate (``<> 'draft'``)
    admits a RETRACTED endpoint, goes live over it, journals an
    ``OperatorPromoted``, and then ``ep.py`` strips the terminal input so the
    manifest publishes ``operators_promoted: 1, factors: 0`` — the exact
    "promoted but no factor" reading this change exists to make impossible FOR A
    TERMINAL INPUT. The pair alone is not a defect signal: a correct part/whole
    promotion publishes it too (EP builds no factor from ``hasPart``), so read it
    with ``no_op_reason`` and the factor count, never alone.
    Reachable on the population this change targets: a resumed ingest writing an
    operator over a claim a human has since retracted.
    """
    premise = _claim(sdk, "premise", "live")
    conclusion = _claim(sdk, "conclusion", "live")
    sdk.retract_point(conclusion)
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]

    marked: list[str] = []
    epa._promote_operator_row(
        sdk, sdk._get_proj().g, oid, set(), marked.append,
    )

    assert _operator_status(sdk, oid) == "draft", (
        "the operator went live over a TERMINAL endpoint — the gate admits "
        "every non-draft status instead of requiring 'live' (#10137)"
    )
    assert marked == []


def test_the_gate_refuses_a_status_less_operator(sdk):
    """The sweep is explicit-``draft``-only, and a status-less operator must not flip.

    The graph-wide candidate read is ``o.status = 'draft'`` on purpose. It must
    NOT grow an ``OR o.status IS NULL`` arm: the canonical read model treats an
    ABSENT status as LIVE (``live.py:278`` — the legacy-node rule), so a
    status-less operator is already eligible for the FACTOR graph and there is
    nothing to promote.

    ⛔ Pinned here precisely because "restore parity with capture" IS re-adding
    that arm, so the cost model matters and "journal noise" is NOT it. Three
    consequences, only the first of which is bookkeeping: no factor is lost (the
    EP read already admitted it), the manifest/``operators_promoted``/journal
    lose the flip, and — the one that actually matters — the strict grounding
    selector reads ``MATCH (n:Point {status: 'live'})``
    (``projection/grounding.py:19``, edges at ``:46``), so a status-less operator
    and its ``IMPL``/``NAND`` edges are INVISIBLE to ``compute_grounding``
    (``sdk.py:22350-22353`` says so verbatim). MEASURED on an embedded graph,
    one IMPL operator over two live claims — and the two reads move differently,
    so name the right one: the NODE read (``:19``) goes 3 → 2 after
    ``REMOVE o.status`` (the claims stay live, so it cannot reach 0), while the
    EDGE read (``:46``) goes 2 → 0; ``ep._live_only`` still admits the node. So
    re-adding the arm silently changes the grounding values of the claims around
    every legacy status-less operator — see the candidate query's own block.

    The assertions here are the sweep/gate arms; the grounding consequence is
    argued in the module, not asserted, because this module never calls
    ``compute_grounding``.

    The stub suite cannot see this arm at all: its candidate answer comes from a
    hand-fed ``graph_operators`` list regardless of the query text.
    """
    premise = _claim(sdk, "premise", "live")
    conclusion = _claim(sdk, "conclusion", "live")
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]
    # The legacy shape: an operator node with NO status property at all.
    sdk._get_proj().g.query(
        "MATCH (o:Point {id:$oid}) REMOVE o.status", params={"oid": oid},
    )

    events: list[str] = []
    real_emit = sdk._emit_event

    def _counting_emit(type_, **kwargs):
        events.append(type_)
        return real_emit(type_, **kwargs)

    sdk._emit_event = _counting_emit
    try:
        candidates = [
            r[0] for r in sdk._get_proj().g.query(
                epa._SELECT_DRAFT_OPERATOR_IDS).result_set if r
        ]
        promoted = epa._promote_operator_row(
            sdk, sdk._get_proj().g, oid, set(), lambda _eid: None,
        )
    finally:
        sdk._emit_event = real_emit

    assert oid not in candidates, (
        "a status-less operator was offered as a draft candidate — the sweep "
        "grew an `OR o.status IS NULL` arm, so a legacy operator would be "
        "re-promoted and re-journalled on every sweep (#10137)"
    )
    assert promoted is False, (
        "the gate promoted a status-less operator; absent status is already "
        "LIVE under the canonical read model, so this journals a redundant "
        "OperatorPromoted and a JSONL replay applies it again"
    )
    assert events.count("OperatorPromoted") == 0
    assert "status" not in (sdk.get_point(oid) or {}), (
        "the sweep WROTE a status onto a legacy operator instead of leaving it"
    )


def test_the_gate_refuses_an_operator_with_no_endpoints(sdk):
    """The empty-endpoint guard is load-bearing: ``all([])`` is ``True``.

    The candidate sweep is a status predicate (``is_operator = true OR
    op_type IS NOT NULL`` AND ``status = 'draft'``), so it can offer a node with
    NO outgoing edge to an epistemic label at all — a hand-written or partially
    migrated operator, which ``create_operator`` cannot mint (it writes one edge
    per input slot, ``idx:0`` included) but a raw write can. Without the
    ``if not endpoints: return False`` guard the gate falls through to
    ``all((st or "live") == "live" for … in [])``, which is vacuously TRUE, and
    the CAS then flips it.

    MEASURED on this engine, on exactly the node this test builds: the endpoint
    read returns ``[]``, ``all(...)`` over it returns ``True``, and
    ``_PROMOTE_OPERATOR`` returns the id with the node's status ``'live'`` — so
    dropping the guard journals an ``OperatorPromoted`` for an operator with no
    endpoints and counts it in ``operators_promoted``.

    The guard is the ENGINE's too (``sdk.py:21344-21345`` — ``if not eps:
    continue  # no endpoints — nothing to gate on``), so this is parity, not a harness
    invention: a harness that promoted here would publish a promotion
    production cannot produce.

    Driven through ``_promote_operator_row`` directly, because ``activate_beliefs``
    classifies the same node through the point sweep as well — the state is
    reachable through the public flow but the gate call is what must refuse.
    """
    oid = "op-orphan-10137"
    g = sdk._get_proj().g
    g.query(
        "CREATE (o:Point {id:$oid, is_operator:true, status:'draft'})",
        params={"oid": oid},
    )

    # Preconditions, so a green here cannot be a green for the wrong reason:
    # the sweep DOES offer the node, and the gate's own read finds no endpoint.
    assert oid in [
        r[0] for r in g.query(epa._SELECT_DRAFT_OPERATOR_IDS).result_set if r
    ], (
        "the candidate sweep no longer offers an endpoint-less draft operator, "
        "so this test would pass without exercising the guard"
    )
    assert g.query(
        epa._SELECT_OPERATOR_ENDPOINTS, params={"oid": oid}
    ).result_set == [], (
        "the fixture grew an epistemic endpoint — the empty-endpoint arm is not "
        "reached and the guard is untested"
    )

    events: list[str] = []
    marked: list[str] = []
    real_emit = sdk._emit_event

    def _counting_emit(type_, **kwargs):
        events.append(type_)
        return real_emit(type_, **kwargs)

    sdk._emit_event = _counting_emit
    try:
        flipped = epa._promote_operator_row(sdk, g, oid, set(), marked.append)
    finally:
        sdk._emit_event = real_emit

    assert flipped is False, (
        "the gate promoted an operator with NO endpoints — the `if not "
        "endpoints` guard is gone, and `all([])` is True, so the CAS flips a "
        "node nothing can gate on"
    )
    assert _operator_status(sdk, oid) == "draft", (
        "an endpoint-less operator went live; the engine keeps it draft "
        "(sdk.py:21344-21345) and a promotion the engine cannot produce must not be "
        "published in operators_promoted (#10137)"
    )
    assert events.count("OperatorPromoted") == 0, (
        f"the gate journalled {events!r} for an operator with no endpoints — "
        "JSONL replay would re-apply a promotion with nothing behind it"
    )
    assert marked == [], (
        f"the gate reported {marked!r} as a promoted endpoint for an operator "
        "that has none"
    )


def test_the_gate_reads_every_operator_edge_not_just_impl(sdk):
    """A ``mitigated_by`` edge to a dead mitigation must block, as it does in the engine.

    ``mitigate_operator`` mints ``(op)-[:mitigated_by]->(m)`` alongside the
    ``IMPL`` edges (``sdk.py:22814``), and the engine's R16 gate reads EVERY
    outgoing edge to an epistemic label (``sdk.py:21338-21347``). So an IMPL
    operator over two live claims whose mitigation was RETRACTED has two live
    IMPL endpoints and one dead third edge — the engine keeps it ``draft``.

    Reading only ``-[:IMPL|NAND]->`` here promoted it (measured before the fix:
    engine 3 rows → refuse, narrow read 2 rows → ``True`` and the operator went
    ``live``). Narrowing the EDGE SET makes a gate WEAKER, not stricter, so the
    harness published an ``operators_promoted`` the engine cannot produce — the
    one invariant this gate exists to hold.
    """
    premise = _claim(sdk, "premise", "live")
    conclusion = _claim(sdk, "conclusion", "live")
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]
    result = sdk.mitigate_operator(oid, "a caveat a human has since withdrawn")
    mid = result.get("point_id") or result.get("id")
    sdk.retract_point(mid)

    marked: list[str] = []
    promoted = epa._promote_operator_row(
        sdk, sdk._get_proj().g, oid, set(), marked.append,
    )

    assert promoted is False, (
        "the gate promoted an operator whose MITIGATION is retracted — the "
        "endpoint read is narrower than the engine's, which makes it weaker, "
        "not stricter (#10137)"
    )
    assert _operator_status(sdk, oid) == "draft"
    assert marked == []


def test_on_input_marks_only_factor_point_endpoints(sdk):
    """``on_input`` reports IMPL/NAND ``:Point`` endpoints and nothing else.

    Two exclusions, both measured, both load-bearing:

    * a ``hasPart`` / ``mitigated_by`` endpoint GATES the promotion but is not
      something EP propagates through (`ep.py` has no arm for either edge), so
      dirty-marking it is churn;
    * a non-``:Point`` endpoint (``:Subject`` / ``:Object`` / ``:Event``) cannot
      be stamped at all — ``_mark_dirty`` persists ``MATCH (n:Point {id: pid})``
      only — so handing its id over files it in ``_dirty_roots`` as an unmatched
      root with no ``ep_dirty`` stamp. The label-wide endpoint read (#7853)
      makes such endpoints reachable, so the filter is what keeps 1c honest.
    """
    premise = _claim(sdk, "premise", "live")
    conclusion = _claim(sdk, "conclusion", "live")
    oid = sdk.create_operator(
        "IMPL", premise, [conclusion],
        direction="unidirectional", promote_source=False,
    )["id"]
    g = sdk._get_proj().g
    # A live :Event endpoint on the same operator — admitted by the engine's
    # label-wide read, never stampable as dirty.
    g.query(
        "MATCH (o:Point {id:$o}) CREATE (o)-[:IMPL {idx:2}]->"
        "(:Event {id:'evt-10137', status:'live'})", params={"o": oid},
    )
    # ...and a mitigation, so a non-factor EDGE TYPE is present too.
    result = sdk.mitigate_operator(oid, "a caveat")
    mid = result.get("point_id") or result.get("id")

    marked: list[str] = []
    promoted = epa._promote_operator_row(sdk, g, oid, set(), marked.append)

    assert promoted is True, (
        "the gate refused an operator whose endpoints are all live — the :Event "
        "endpoint is admitted by the engine's label-wide read (#7853)"
    )
    assert sorted(marked) == sorted([premise, conclusion]), (
        f"on_input reported {marked!r}; expected exactly the two IMPL :Point "
        f"endpoints — a mitigation id ({mid}) is not a factor input and the "
        ":Event id cannot be stamped by `_mark_dirty` (it would land in "
        "`_dirty_roots` as an unmatched root)"
    )
