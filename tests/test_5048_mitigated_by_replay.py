"""#5048 — a mitigation's ``mitigated_by`` edge, identity and strength must survive ``rebuild_all``.

Root: ``derived = replay(journal)`` (#5048, the consolidation parent; #5089 the
epic). ``mitigate_operator`` writes a mitigation **live** as a NON-operator
Point:

    CREATE (m:Point {is_operator: false, mitigation_strength: s, …})
    CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)

It journals ONE ``OperatorAdded`` whose payload carries ``mitigation_strength``
and an ``operator`` EDGE-descriptor ``{op_type: "IMPL", inputs: [op]}`` (the
descriptor exists so the fold can rebuild the IMPL half; a mitigation is never
an operator — commit_schema.Operator, #4937).

Three replay gaps on that one record, all ``derived = replay(journal)``:

1. **the edge** — ``mitigation_strength`` alone (a property) does not rebuild the
   reverse edge. ``compute_operator_weight`` reads ONLY ``mitigated_by``, so a
   rebuild silently reverted ``w_eff = w * (1 - strength)`` to the undecayed
   base.
2. **the identity** — deriving ``is_operator`` from the descriptor's presence
   re-typed the mitigation as an operator on replay (live ``false`` / no
   ``op_type``; rebuild ``true`` / ``'IMPL'``).
3. **the strength** — the idempotent re-mitigation branch calls
   ``update_point(mid, mitigation_strength=…)``, whose ``PointRevised`` carries
   the value; the revise fold ignored it, so a rebuild reverted to the FIRST
   strength while the edge was present.

The OBSERVABLE asserted here is the resolved operator weight
(``compute_operator_weight``), not edge existence. #3815 established why: an
edge-existence assertion stays green for a mitigation whose strength is never
applied. Edge counts are asserted too, as the MECHANISM, and are counted
GRAPH-WIDE — an outgoing-only count cannot see the wrong-direction edges a
widened predicate would create.

MUTATIONS THAT MUST RED:
- drop the ``mitigated_by`` MERGE in ``edges.py::_create_edges`` → post-rebuild
  weight reads the undecayed base;
- weaken the identity half of the gate to ``not p.get("is_operator")`` (true on
  an ABSENT key) → the EventAPI case reds on its IDENTITY assertion (that
  payload carries no ``is_operator`` at all, so the shared identity arm re-types
  the operator as a non-operator, live and on rebuild). It does NOT red the edge
  assertion — the strength half already excludes that payload — which is
  exactly why the identity is asserted separately. The SDK's generic operator
  stays green under that weakening because its payload states
  ``is_operator: True``;
- weaken the strength half of the gate (drop it, or accept a merely-present
  value) → a non-operator point carrying only an ``operator`` descriptor mints
  ``mitigated_by`` and dampens that operator by the fallback strength (the fold
  is the LIVE+replay edge writer, so this is a belief change, not just a replay
  one); see ``test_payload_without_a_usable_strength_gains_no_edge``;
- let the strength test raise instead of refusing → one hand-edited record
  aborts ``rebuild_all`` after the wipe and leaves the graph with NO edges (see
  the ``out-of-float-range`` parameter);
- drop the BAND from the strength test (keep only a type check) → a payload
  outside the writer's own ``0 <= strength <= 1`` band mints the edge and
  dampens at the read-clamped band edge nobody asked for (see the
  ``above-band`` / ``below-band`` / ``nan`` / ``out-of-float-range``
  parameters);
- make the refusal non-total (drop the ``try``/``except``) → a hostile
  ``int`` subclass whose comparisons raise THROWS out of the fold instead of
  being refused (see the ``hostile-subclass`` parameter);
- gate the MERGE on the origin (``AND s.is_operator = true``, ONTOLOGY §3.9's
  hard rule) → ``rebuild_all`` mints the edge for a `#329`-stub origin where
  ``apply()`` mints nothing, i.e. a live!=replay divergence, which reds
  ``test_non_operator_origin_still_mints_the_edge_for_parity`` (its sibling
  ``…from_an_operator_origin_mints_the_edge`` is the non-vacuity control). The
  rule stays with the SDK writer — see that test's docstring and #5048;
- drop the ``is_operator`` arm in ``entities.py::_upsert_point_props`` → the
  identity parity assertion fails;
- drop the ``mitigation_strength`` fold in ``_revise_point`` → the
  re-mitigation weight reverts to the first strength;
- re-anchor that fold on ``skip_content`` → the superseded-bare-creation case
  reverts to the first strength (the write, not the supersession, is the
  boundary — see ``test_strength_boundary_is_the_write``);
- replace the ``"mitigation_strength" in persisted_extra_keys`` anchor with a
  hand-spelled ``p.get("mitigation_strength") is not None`` → a creation the
  writer DROPPED (an undeclared list) counts as a write, over-suppresses the
  later ``PointRevised`` and reverts the node to its first strength; see
  ``test_dropped_strength_creation_still_folds_a_later_revision``;
- restore the unconditional ``op_type`` derivation in
  ``consistency._canonical_point_fields`` → ``check_consistency`` reports a
  false content divergence on a faithful rebuild (see
  ``test_check_consistency_agrees_after_rebuild``).

Runs on the ambient ``TORTOISE_DB_URI`` when set (the docker lane) and falls
back to an embedded db, so both the default and the carve-out lane cover it.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from tortoise.api import EventAPI, provenance
from tortoise.consistency import check_consistency
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK
from tortoise.weights import (
    compute_operator_weight,
    mitigation_dampening_factor,
)

# A plain IMPL operator's base weight is the generic 1.0 (weights.py: no NAND
# base, no operator-valued target, no dynamic multiplier). Pinned as a literal
# so the expected decay is unambiguous.
BASE_WEIGHT = 1.0
# The sanctioned mitigation band (weights.py: [0.10, 0.50]).
SANCTIONED_STRENGTHS = (0.10, 0.30, 0.50)


class _RaisingInt(int):
    """An ``int`` whose comparisons raise — hostile, but ``isinstance``-legal.

    ``0 <= value`` dispatches to the SUBCLASS's reflected method, so an
    unguarded band check raises instead of refusing. Only a TOTAL refusal
    (``except Exception``) can honour the predicate's "never raises" guarantee.
    """

    def __le__(self, other):  # pragma: no cover - must never be reached
        raise RuntimeError("hostile __le__")

    def __ge__(self, other):  # pragma: no cover - must never be reached
        raise RuntimeError("hostile __ge__")


def _fresh_sdk(tmp_path):
    """A journaled SDK on a test-named graph (rebuild_all refuses non-test)."""
    ns = f"test_5048_{uuid.uuid4().hex[:8]}"
    events = tmp_path / "events"
    events.mkdir()
    if os.environ.get("TORTOISE_DB_URI"):
        sdk = TortoiseSDK(db_path=None, namespace=ns,
                          event_log_path=str(events / "events.jsonl"))
    else:
        sdk = TortoiseSDK(db_path=str(tmp_path / "t5048.db"), namespace=ns,
                          event_log_path=str(events / "events.jsonl"))
    return sdk, events


def _impl_chain(sdk):
    """T0 source -[IMPL]-> downstream claim. Returns (source, claim, op_id)."""
    src = sdk.create_point("statement", "T0 source", status="live",
                           dedup=False)["id"]
    claim = sdk.create_point("statement", "downstream claim", status="live",
                             dedup=False)["id"]
    op_id = sdk.create_operator("IMPL", src, [claim])["id"]
    return src, claim, op_id


def _all_mitigated_by(sdk):
    """Every ``mitigated_by`` edge in the graph, by direction.

    Graph-wide on purpose: an outgoing-only read from the operator cannot see a
    wrong-direction edge a widened predicate would create.
    """
    rows = sdk._get_proj().g.query(
        "MATCH (a:Point)-[:mitigated_by]->(b:Point) RETURN a.id, b.id"
    ).result_set
    return sorted(tuple(r) for r in rows) if rows else []


def _point_identity(sdk, pid):
    """``(is_operator, op_type)`` of one node — the identity a replay must keep."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.is_operator, n.op_type",
        params={"id": pid},
    ).result_set
    return tuple(rows[0]) if rows else None


def _mitigation_row(sdk):
    """``(is_operator, op_type, mitigation_strength)`` of the mitigation Point."""
    rows = sdk._get_proj().g.query(
        "MATCH (m:Point) WHERE m.mitigation_strength IS NOT NULL "
        "RETURN m.is_operator, m.op_type, m.mitigation_strength"
    ).result_set
    return rows[0] if rows else None


def _journal(events) -> list[dict]:
    path = events / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _oracle_strength(tmp_path, records: list[dict], mid: str):
    """Replay ``records`` chronologically through the live ``apply()`` path.

    The oracle is an UNJOURNALED SDK, so replaying a hand-edited journal here
    cannot append to the file under test.
    """
    ns = f"test_5048o_{uuid.uuid4().hex[:8]}"
    if os.environ.get("TORTOISE_DB_URI"):
        oracle = TortoiseSDK(db_path=None, namespace=ns)
    else:
        oracle = TortoiseSDK(db_path=str(tmp_path / "t5048_oracle.db"),
                             namespace=ns)
    try:
        for r in records:
            oracle._get_proj().apply(r)
        return oracle.get_point(mid).get("mitigation_strength")
    finally:
        oracle.close()


class TestMitigatedBySurvivesRebuild:
    """The rebuild must restore the edge the weight reads."""

    @pytest.mark.parametrize("strength", SANCTIONED_STRENGTHS)
    def test_resolved_weight_is_identical_across_rebuild(self, tmp_path, strength):
        """``w_eff`` before the rebuild == ``w_eff`` after it.

        Failing state (pre-fix): post-rebuild weight reads the undecayed base
        (1.0) instead of ``base * (1 - strength)`` — 0.5 at strength=0.50 is
        the first parametrisation to RED.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            sdk.mitigate_operator(op_id, f"mitigation at {strength}", strength)

            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before == pytest.approx(
                BASE_WEIGHT * mitigation_dampening_factor(strength))
            assert _all_mitigated_by(sdk), "live write lost the edge"

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            after = compute_operator_weight(sdk._get_proj(), op_id)
            assert after == pytest.approx(before), (
                "a rebuild must not move the resolved weight: "
                f"before={before}, after={after}, strength={strength}"
            )
            # The edge is the MECHANISM the weight above reads; assert it is
            # the thing that came back, not merely that the number matched.
            assert _all_mitigated_by(sdk), (
                "rebuild dropped the (op)-[:mitigated_by]->(m) edge — the "
                "mitigation Point survived but its belief effect did not"
            )
        finally:
            sdk.close()

    def test_mitigation_identity_is_stable_across_rebuild(self, tmp_path):
        """The mitigation Point keeps its live identity on replay.

        A mitigation is a NON-operator Point (``is_operator: false``, no
        ``op_type``). Deriving identity from the payload's ``operator``
        descriptor re-typed it as an operator on every rebuild — a
        ``derived = replay(journal)`` divergence, and the reason the descriptor
        and the identity must be told apart.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            sdk.mitigate_operator(op_id, "identity parity", 0.50)

            live = _mitigation_row(sdk)
            assert live is not None, "no mitigation Point found"
            assert live[0] is False, f"live is_operator must be false, got {live!r}"
            assert live[1] is None, f"live op_type must be NULL, got {live!r}"

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            rebuilt = _mitigation_row(sdk)
            assert rebuilt == live, (
                "replay re-typed the mitigation Point: "
                f"live={live!r}, rebuilt={rebuilt!r}"
            )
        finally:
            sdk.close()

    def test_remitigation_strength_survives_rebuild(self, tmp_path):
        """A second ``mitigate_operator`` on the same operator folds its strength.

        Failing state (without the ``_revise_point`` fold): the ``PointRevised``
        payload carries the new strength but replay ignores it, so post-rebuild
        ``w_eff`` reverts to the FIRST strength — 0.9 instead of 0.5.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            sdk.mitigate_operator(op_id, "first", 0.10)
            sdk.mitigate_operator(op_id, "first", 0.50)  # idempotent update path

            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before == pytest.approx(
                BASE_WEIGHT * mitigation_dampening_factor(0.50)), (
                "live re-mitigation did not take; the test cannot measure replay"
            )

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            after = compute_operator_weight(sdk._get_proj(), op_id)
            assert after == pytest.approx(before), (
                "a rebuild reverted the revised mitigation strength: "
                f"before={before}, after={after}"
            )
        finally:
            sdk.close()

    def test_check_consistency_agrees_after_rebuild(self, tmp_path):
        """The durability GATE must not call a faithful rebuild diverged.

        ``consistency._canonical_point_fields`` reads a nested ``operator``
        descriptor back as a flat ``op_type``. For a mitigation — a
        NON-operator whose payload carries that descriptor only as an edge
        carrier — that re-typed the JOURNAL side as an operator while the
        faithfully replayed graph node has no ``op_type``, so
        ``check_consistency`` reported a false ``divergence="content"`` and
        advised a "replay the journal" repair that could never converge.
        The point's explicit ``is_operator: false`` must outrank the
        descriptor here, exactly as it does in ``_upsert_point_props``.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            sdk.mitigate_operator(op_id, "gate parity", 0.50)
            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            result = check_consistency(str(events / "events.jsonl"),
                                       sdk._get_proj())
            assert result["ok"], (
                "check_consistency flagged a faithful rebuild as diverged: "
                f"divergence={result.get('divergence')!r}, "
                f"points={result.get('divergent_points')!r}"
            )
        finally:
            sdk.close()

    def test_strength_boundary_is_the_write(self, tmp_path):
        """A later same-file creation that OMITS the prop is not a boundary.

        ``mitigation_strength`` is an open-set prop written CONDITIONALLY by
        ``_persist_extra_props`` (a ``None``/absent value is skipped), so a
        creation that merely omits it never cleared it live. The correct
        suppression boundary is therefore the ``skip_hash`` one — the creation
        that actually WROTE it (or a hard delete→recreate that cleared it) —
        never ``skip_content``'s unconditional supersession. Anchoring on
        ``skip_content`` reverts the node to its FIRST strength here, measured
        against the chronological ``apply()`` oracle.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            src, _claim, op_id = _impl_chain(sdk)
            mid = sdk.mitigate_operator(op_id, "revise me", 0.10)["id"]
            sdk.mitigate_operator(op_id, "revise me", 0.50)  # PointRevised

            # A LATER same-file creation for the same id that does NOT carry
            # ``mitigation_strength`` — a bare re-emit shape no SDK producer
            # emits, and exactly the case that tells the two anchors apart.
            records = _journal(events)
            records.append({
                "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:00+00:00",
                "type": "PointAdded", "initiated_by": "raw-producer",
                "projection_version": 2,
                "point": {"id": mid, "content": "[MITIGATION] revise me",
                          "pointKind": "statement", "status": "live",
                          "is_operator": False,
                          "operator": {"op_type": "IMPL", "inputs": [src]}},
            })
            (events / "events.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in records))

            applied = _oracle_strength(tmp_path, records, mid)
            assert applied == pytest.approx(0.50), (
                "oracle setup drifted — the live path must keep the revision's "
                f"strength, got {applied!r}"
            )

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            rebuilt = sdk.get_point(mid).get("mitigation_strength")
            assert rebuilt == pytest.approx(applied), (
                "a superseding creation that never wrote mitigation_strength "
                f"must not suppress the revision: live={applied!r}, "
                f"rebuilt={rebuilt!r}"
            )
            after = compute_operator_weight(sdk._get_proj(), op_id)
            assert after == pytest.approx(
                BASE_WEIGHT * mitigation_dampening_factor(0.50))
        finally:
            sdk.close()

    @pytest.mark.parametrize("strength", [
        # omitted entirely — the shape a low-level EventAPI caller produces
        pytest.param(None, id="omitted"),
        # dropped by ``_persist_extra_props`` (a map, and the Point layer's
        # list policy — ``_POINT_LIST_PROPS`` is EMPTY), so the node ends up
        # with NO strength and a fabricated edge would dampen at the fallback
        pytest.param({"not": "persistable"}, id="map"),
        pytest.param([0.5], id="undeclared-list"),
        # persistable, so it LANDS on the node and then makes
        # ``weights.compute_operator_weight`` raise on every read
        pytest.param("not-a-number", id="non-numeric"),
        # a value ``math.isfinite`` cannot even CONVERT: accepting it (or
        # letting the conversion raise) turned one hand-edited record into an
        # abort of the whole rebuild AFTER the wipe
        pytest.param(10 ** 400, id="out-of-float-range"),
        # out of the writer's band — ``mitigate_operator`` raises on these, so
        # admitting one would authorise a dampening at the read-clamped band
        # edge that no record ever asked for
        pytest.param(5.0, id="above-band"),
        pytest.param(-0.5, id="below-band"),
        pytest.param(float("nan"), id="nan"),
        # a hostile int SUBCLASS whose comparisons raise: ``isinstance`` admits
        # it, so a refusal that is not TOTAL throws instead of refusing — and
        # ``_create_edges`` is the live writer, where a caller controls the
        # payload (``EventAPI.add_point(**fields)``)
        pytest.param(_RaisingInt(1), id="hostile-subclass"),
    ])
    def test_payload_without_a_usable_strength_gains_no_edge(self, tmp_path,
                                                             strength):
        """Only a NON-operator point carrying a usable strength is a mitigation.

        ``EventAPI.add_point(content, prov, **fields)`` forwards arbitrary
        fields, and ``_create_edges`` is the SHARED live+replay edge writer, so
        a low-level producer can attach an ``operator`` descriptor to a
        non-operator point. That is not a record any live writer produced — and
        neither is one whose ``mitigation_strength`` is merely PRESENT but
        unusable (``mitigate_operator`` writes a finite real IN ITS BAND; the
        passthrough drops a map/list, a non-numeric value poisons every weight
        read, an out-of-band value is one the writer itself rejects, and a
        value past float range must not be allowed to abort the rebuild).
        Minting ``mitigated_by`` for any of them dampens the operator by the
        fallback strength (measured 1.0 -> 0.7) — a silent belief change.
        """
        sdk, _events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            victim = sdk.create_point("statement", "annotated rel",
                                      status="live", dedup=False)["id"]
            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before == pytest.approx(BASE_WEIGHT)

            payload = {
                "id": victim, "content": "annotated rel",
                "is_operator": False,
                "operator": {"op_type": "IMPL", "inputs": [op_id]},
            }
            if strength is not None:
                payload["mitigation_strength"] = strength
            sdk._get_proj()._upsert_point_edges(payload)

            assert _all_mitigated_by(sdk) == [], (
                "a non-operator descriptor with an unusable "
                f"mitigation_strength ({strength!r}) minted a mitigated_by edge"
            )
            assert compute_operator_weight(
                sdk._get_proj(), op_id) == pytest.approx(before), (
                "a non-mitigation payload moved the operator's resolved weight"
            )
        finally:
            sdk.close()

    def test_non_operator_origin_still_mints_the_edge_for_parity(self, tmp_path):
        """NOT gated on the origin — this GUARDS the revert, it is not a
        parity measurement.

        ONTOLOGY §3.9's hard rule (#2315) is that ``mitigated_by`` originates
        only from an ``is_operator:true`` Point, and ``mitigate_operator``
        enforces it. The fold must NOT re-enforce it: adding ``AND
        s.is_operator = true`` was measured to make ``rebuild_all`` mint the
        edge for a `#329`-stub origin (``is_operator=false``, created when a
        short ``src`` does not resolve yet) where ``apply()`` minted nothing —
        and for a long unresolved ``src`` the two engines disagree in the same
        direction, because ``_create_edges`` skips the whole typed block at
        apply time and pass-1a hoisting resolves the origin at rebuild time.
        That is a live!=replay divergence, the invariant this fold exists to
        establish, traded for an edge ONTOLOGY itself calls dead structure (no
        EP factor addresses a non-operator). Recorded on #5048 with both
        measurements, together with the root: those two shapes diverge for the
        PRE-EXISTING ``IMPL``/``INPUT`` MERGEs as well, so the fold inherits the
        divergence rather than creating it.

        What THIS test pins is only that the fold accepts the record, so a
        future re-addition of the origin predicate reds it: the sibling
        ``…from_an_operator_origin_mints_the_edge`` is the non-vacuity control.
        """
        sdk, _events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            mitigation = sdk.create_point("statement", "the mitigation",
                                          status="live", dedup=False)["id"]
            bystander = sdk.create_point("statement", "not an operator",
                                         status="live", dedup=False)["id"]
            assert _point_identity(sdk, bystander) == (False, None), (
                "the fixture must name a NON-operator as the origin"
            )

            sdk._get_proj()._upsert_point_edges({
                "id": mitigation, "content": "the mitigation",
                "is_operator": False, "mitigation_strength": 0.5,
                "operator": {"op_type": "IMPL", "inputs": [bystander]},
            })
            assert _all_mitigated_by(sdk) == [(bystander, mitigation)], (
                "the fold must rebuild the edge the record names, whatever the "
                "origin's flag is — see this test's docstring for why"
            )
            assert compute_operator_weight(
                sdk._get_proj(), op_id) == pytest.approx(BASE_WEIGHT), (
                "the edge must dampen only the operator it names"
            )
        finally:
            sdk.close()

    def test_mitigation_payload_from_an_operator_origin_mints_the_edge(
            self, tmp_path):
        """Non-vacuity control: an operator ``src`` mints the edge too.

        The sibling test asserts the edge for a non-operator origin; this one
        asserts the honest record is not refused in the process, so the
        acceptance is a property of the fold rather than of one fixture.
        """
        sdk, _events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            mitigation = sdk.create_point("statement", "the mitigation",
                                          status="live", dedup=False)["id"]
            assert _point_identity(sdk, op_id) == (True, "IMPL"), (
                "the fixture's origin must be an operator"
            )

            sdk._get_proj()._upsert_point_edges({
                "id": mitigation, "content": "the mitigation",
                "is_operator": False, "mitigation_strength": 0.5,
                "operator": {"op_type": "IMPL", "inputs": [op_id]},
            })
            assert _all_mitigated_by(sdk) == [(op_id, mitigation)], (
                "an operator origin must mint the mitigated_by edge"
            )
            # The weight MOVES (the minted edge is read) even though the node
            # itself never received ``mitigation_strength`` here — this call
            # writes only the EDGES, so the read uses the documented fallback.
            # The exact per-strength value is pinned by the rebuild tests.
            assert compute_operator_weight(
                sdk._get_proj(), op_id) < BASE_WEIGHT, (
                "the minted edge did not dampen the operator it names"
            )
        finally:
            sdk.close()

    def test_dropped_strength_creation_still_folds_a_later_revision(
            self, tmp_path):
        """The anchor is the WRITER'S OUTCOME, not a hand-spelled guess.

        ``_persist_extra_props`` also drops undeclared list/tuple values
        (``_POINT_LIST_PROPS`` is EMPTY), so a creation whose payload says
        ``mitigation_strength: [0.5]`` writes NOTHING to the node — while a
        hand-spelled ``p.get("mitigation_strength") is not None`` test would
        count it as a write and anchor the skip on it, over-suppressing the
        later ``PointRevised`` that follows. Only the writer's reported outcome
        (``"mitigation_strength" in persisted_extra_keys``) tells the two
        apart, so that is what this pins: the revision must still fold.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            src, _claim, op_id = _impl_chain(sdk)
            mid = sdk.mitigate_operator(op_id, "revise me", 0.10)["id"]
            sdk.mitigate_operator(op_id, "revise me", 0.50)  # PointRevised
            records = _journal(events)
            # A LATER same-file creation that NAMES the prop in a form the
            # writer drops. ``superseded`` is true for the revision because a
            # creation follows it, so the anchor decides whether the revision
            # still folds — and only the writer's outcome knows that this
            # creation wrote nothing.
            records.append({
                "event_id": sdk.ulid(), "ts": "2026-09-18T00:00:00+00:00",
                "type": "PointAdded", "initiated_by": "raw-producer",
                "projection_version": 2,
                "point": {"id": mid, "content": "[MITIGATION] revise me",
                          "pointKind": "statement", "status": "live",
                          "is_operator": False, "mitigation_strength": [0.5],
                          "operator": {"op_type": "IMPL", "inputs": [src]}},
            })
            (events / "events.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in records))

            applied = _oracle_strength(tmp_path, records, mid)
            assert applied == pytest.approx(0.50), (
                "oracle setup drifted — the live path must end at the "
                f"revision's strength, got {applied!r}"
            )

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            rebuilt = sdk.get_point(mid).get("mitigation_strength")
            assert rebuilt == pytest.approx(applied), (
                "a creation whose strength the writer DROPPED must not anchor "
                f"the skip: live={applied!r}, rebuilt={rebuilt!r}"
            )
        finally:
            sdk.close()

    @pytest.mark.parametrize("strength", [True, False])
    def test_bool_strength_still_rebuilds_the_edge(self, tmp_path, strength):
        """A ``bool`` strength is REAL to the writer, so it must not be refused.

        ``mitigate_operator`` accepts one (the range check passes for
        ``False``/``True``), ``weights.mitigation_dampening_factor`` clamps it
        to the band edge, and the node stores it. Refusing it in the gate would
        silently drop a real mitigation's edge — measured ``w_eff`` 0.9 live ->
        1.0 rebuilt for ``False``. Rejecting a bool belongs at the WRITER, as
        its own decision; the fold mirrors what the writer accepted.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            sdk.mitigate_operator(op_id, f"bool {strength}", strength)

            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before != pytest.approx(BASE_WEIGHT), (
                "the live bool strength was a no-op; the test cannot measure it"
            )
            assert _all_mitigated_by(sdk), "live write lost the edge"

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            assert _all_mitigated_by(sdk), (
                "a bool strength lost its mitigated_by edge on rebuild"
            )
            assert compute_operator_weight(
                sdk._get_proj(), op_id) == pytest.approx(before), (
                f"a bool strength moved w_eff: before={before}, "
                f"after={compute_operator_weight(sdk._get_proj(), op_id)}"
            )
        finally:
            sdk.close()

    def test_generic_impl_operator_gains_no_mitigated_by(self, tmp_path):
        """The gate holds: only a mitigation Point may own the edge.

        ``mitigated_by`` is canonical ONLY from a mitigation Point (#4937);
        ``compute_operator_weight`` reads any such edge. A fix that
        reconstructed the reverse edge for EVERY IMPL operator would silently
        dampen unrelated operators, so the graph-wide edge count is pinned at
        zero both live and after a rebuild.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            _src, _claim, op_id = _impl_chain(sdk)
            assert _all_mitigated_by(sdk) == []
            before = compute_operator_weight(sdk._get_proj(), op_id)
            assert before == pytest.approx(BASE_WEIGHT)

            sdk._get_proj().rebuild_all(events, confirm_destructive=True)

            assert _all_mitigated_by(sdk) == [], (
                "a generic IMPL operator must never acquire mitigated_by"
            )
            assert compute_operator_weight(sdk._get_proj(), op_id) == pytest.approx(
                before)
        finally:
            sdk.close()

    def test_eventapi_impl_operator_gains_no_mitigated_by(self, tmp_path):
        """The MAIN ingest producer's operator must not gain the edge.

        ``EventAPI._point`` builds the ``OperatorAdded`` payload WITHOUT an
        ``is_operator`` key, so a gate on the key's ABSENCE (`not p.get(...)`)
        fires for every IMPL operator the extractor/ingest path creates and
        dampens each of its inputs. The gate must require the explicit-False
        identity a mitigation carries.
        """
        sdk, events = _fresh_sdk(tmp_path)
        try:
            proj = sdk._get_proj()
            api = EventAPI(EventLog(str(events / "events.jsonl")),
                           initiated_by="extractor", projection=proj)
            prov = provenance("doc.txt", [0, 10], "quote", extracted_by="t@0")
            a = api.add_point("T0 source", prov)
            b = api.add_point("downstream claim", prov)
            op_id = api.add_operator("IMPL", [a, b], prov)

            # The EDGE assertion alone is NOT enough here: the strength half of
            # the gate already excludes this payload, so a predicate reading the
            # flag as merely FALSY (`not p.get("is_operator")`) still leaves the
            # edge absent — while silently re-typing the operator as a
            # non-operator through the shared identity arm. Assert the identity
            # too, live and after the rebuild.
            assert _point_identity(sdk, op_id) == (True, "IMPL"), (
                "a live EventAPI IMPL operator was re-typed: "
                f"{_point_identity(sdk, op_id)!r}"
            )
            assert _all_mitigated_by(sdk) == [], (
                "a live EventAPI IMPL operator gained mitigated_by edges"
            )
            proj.rebuild_all(events, confirm_destructive=True)
            assert _point_identity(sdk, op_id) == (True, "IMPL"), (
                "replay re-typed a generic EventAPI IMPL operator: "
                f"{_point_identity(sdk, op_id)!r}"
            )
            assert _all_mitigated_by(sdk) == [], (
                "a replayed EventAPI IMPL operator gained mitigated_by edges"
            )
        finally:
            sdk.close()

    def test_operator_payload_carrying_the_property_gains_no_edge(self, tmp_path):
        """An OPERATOR payload that carries the property is still not a mitigation.

        ``rebuild_all``'s #548 graph-only path synthesizes an ``OperatorAdded``
        for an operator that carries ``mitigation_strength`` as an open-set
        passthrough property (``update_point(op, mitigation_strength=…)``). A
        gate keyed on the property would fire and dampen the operator's inputs —
        points that were never mitigated.
        """
        sdk, _events = _fresh_sdk(tmp_path)
        try:
            src, _claim, op_id = _impl_chain(sdk)
            payload = {
                "id": op_id, "content": "operator", "is_operator": True,
                "op_type": "IMPL", "mitigation_strength": 0.5,
                "operator": {"op_type": "IMPL", "inputs": [src]},
            }
            sdk._get_proj()._upsert_point_edges(payload)
            assert _all_mitigated_by(sdk) == [], (
                "an operator payload must not create mitigated_by edges"
            )
        finally:
            sdk.close()
