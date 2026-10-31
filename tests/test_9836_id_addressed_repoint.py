"""#9836 — the ID-ADDRESSED structural repoint family (`aboutAction`, `wasDerivedFrom`).

THE DEFECT THIS PINS. `supersede_point`'s 2b leg MOVES every rel in
`SUPERSEDE_STRUCTURAL_RELS` from the retired point to the successor live — but it
journalled a `DirectEdgeRepoint` descriptor for the SNAPSHOT-DERIVABLE subset
only (`if derivable:`). `aboutAction` (Action dissolved in ONTOLOGY v3.0) and
`wasDerivedFrom` (the A10 raw family) are transferred but are not
snapshot-derivable, so they moved live and journalled NOTHING. The two rebuild
routes then disagreed, each losing the edge a different way:

* **journal-rebuild** — the edge was GONE. Its `DirectEdgeCreated` is skipped by
  the superseded-endpoint gate (`_supersede_took_this_edge`, correct: replaying
  the creation at the old point would mint a phantom at a dead point, E2E-11.6)
  and nothing else re-creates it;
* **record-rebuild** — the `relation` row survived AT THE PRE-SUPERSEDE POINT,
  because the only record the move ever produced was the plain
  `DirectEdgeCreated` upsert at `src=old_id`.

Measured on `8e49b7d2a` (before the fix), per rel, one supersede:
``LIVE=[('NEW','TGT')] REPOINTS=[] JREBUILD=[]``.

WHAT IT IS FIXED BY, and why the family needs its OWN addressing rather than two
more rows in `STRUCTURAL_REL_LABELS`:

* a Point has no `name`, so `stub_key`'s natural-key arm cannot mint a key for
  `aboutAction`'s `Point -> Point` target (that is the `aboutPoint` defect
  #10313 owns); and
* `wasDerivedFrom`'s target label is not a function of the rel — ONTOLOGY §3.2
  declares `Object -> Object` while BOTH its writers (`create_derivation`,
  `create_edge`) type-check nothing, and the class-4 mint produces the Point
  shape.

So the target is addressed the way every other epistemic endpoint in this tree
is: by its OWN identity property — the label's own key
(`ID_ADDRESSED_TARGET_ID_PROPS`: `id`, or `eventId` for an `Event`, whose `id`
the meeting guard can share between two distinct nodes) — with the label the
descriptor OBSERVED live riding `target_label` (the field's own meaning on the
structural route). Both routes
resolve `(label, identity)` — the graph through `resolve_structural_target(..., by_id=
True)`, the record by delegating to `_endpoint_resolution`, the same resolution
every other edge endpoint uses.

THE ACCEPTANCE BAR is `live == journal-rebuild == record-rebuild`, so this module
measures the LIVE and JOURNAL halves end to end (embedded graph, real journal,
real `rebuild_all`) and pins the RECORD half where it can be pinned without a
database: the PLAN the record emits for a descriptor. The row-count facts are
measured against real Postgres by `supabase/tests/pglite/validate.mjs` cases
13-17 (moves to the successor, filed under the descriptor's label; an
unaddressable label is a NO-MOVE, never delete-without-insert; and the no-self-edge
guard's delete-only descriptor removes the pre-supersede row) — a pytest leg
cannot assert a row count, and a plan-text assertion cannot see one.
"""
from __future__ import annotations

import json
import logging

import pytest

from tortoise import record_projection as rp
from tortoise.projection import FalkorProjection
from tortoise.projection.edges import (
    DERIVABLE_STRUCTURAL_RELS,
    EPISTEMIC_LABEL_ORDER,
    ID_ADDRESSED_STRUCTURAL_RELS,
    ID_ADDRESSED_TARGET_ID_PROPS,
    RECORD_ADDRESSABLE_EDGE_LABELS,
    STRUCTURAL_REPOINT_RELS,
    id_addressed_target,
    resolve_structural_target,
)
from tortoise.sdk import SUPERSEDE_STRUCTURAL_RELS, TortoiseSDK

#: The two rels. Restated rather than imported into the assertions so a rel
#: silently dropped from the producer's set — the defect, inverted — reds here.
FAMILY = ("aboutAction", "wasDerivedFrom")


@pytest.fixture()
def edge_sdk(tmp_path):
    """A real SDK over an embedded graph, with a JSONL journal (so it emits)."""
    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "graph.db"), event_log_path=str(journal))
    try:
        yield sdk, journal, tmp_path
    finally:
        sdk.close()


def _records(journal) -> list[dict]:
    return [json.loads(line) for line in journal.read_text().splitlines()
            if line.strip()]


def _repoints(journal) -> list[dict]:
    return [r for r in _records(journal) if r.get("type") == "DirectEdgeRepoint"]


def _edges(proj, rel: str) -> set[tuple[str, str]]:
    return {(row[0], row[1]) for row in proj.g.query(
        f"MATCH (s)-[r:{rel}]->(t) RETURN s.id, t.id").result_set}


def _world(sdk) -> dict[str, str]:
    """Three Points: the claim, a target, and the successor."""
    return {
        "old": sdk.create_point("claim", "one", pointKind="statement")["id"],
        "target": sdk.create_point("claim", "two", pointKind="statement")["id"],
        "new": sdk.create_point("claim", "three", pointKind="statement")["id"],
    }


# ════════════════════════════════════════════════════════════════════════════
# 1. The census: live == journal-rebuild, per rel (the red-before/green-after leg)
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("rel", FAMILY)
def test_a_supersede_MOVES_the_edge_and_the_journal_rebuild_agrees(
        edge_sdk, rel):
    """`live == journal-rebuild` for a `Point -> Point` edge of the family.

    Before the fix this measured `LIVE=[('NEW','TGT')] JREBUILD=[]`: the edge was
    transferred live and the journal rebuild had nothing to replay, because the
    2b emission was gated on `derivable` and neither of these rels is.
    """
    sdk, journal, tmp_path = edge_sdk
    ids = _world(sdk)
    assert sdk.create_edge(rel, ids["old"], ids["target"])["created"] is True
    sdk.supersede_point(ids["old"], ids["new"])

    live = _edges(sdk._proj, rel)
    assert live == {(ids["new"], ids["target"])}, live
    # The descriptor exists, and it names the TARGET'S OWN ID under the label the
    # transfer observed — never the internal node id, never a natural key.
    reps = [r for r in _repoints(journal) if r.get("edge_type") == rel]
    assert len(reps) == 1, (
        f"{rel}: the 2b transfer MOVED the edge live ({live}) and journalled "
        f"NO repoint for it ({reps}) — the journal route has nothing to replay, "
        "which is the whole of #9836 (the emission was gated on `derivable`)")
    rep = reps[0]
    assert rep["src"] == ids["old"]
    assert rep["tgt"] == ids["target"]
    assert rep["target_label"] == "Point"
    assert rep.get("delete_only") is None

    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert _edges(rebuilt, rel) == live, (
            f"{rel}: the journal rebuild disagrees with the live graph — the "
            "descriptor the 2b emission used to omit is what carries the move")
    finally:
        rebuilt.close()


@pytest.mark.parametrize("rel", FAMILY)
def test_the_descriptor_is_NOT_emitted_when_the_target_cannot_be_addressed(
        edge_sdk, rel):
    """The RESIDUAL pin: a target this family cannot NAME is not half-journaled.

    A `:Source` endpoint is url-keyed and outside `RECORD_ADDRESSABLE_EDGE_LABELS`,
    so a descriptor for it is un-replayable on BOTH routes (the graph's by_id arm
    allowlists the label before it interpolates it; the record's id arm refuses a
    label outside `_ID_ADDRESSED_TARGET_LABELS`). The emission therefore skips it
    COMPLETELY — no partial descriptor — exactly as `stub_key` skips an
    unresolvable natural key for the derivable family. Filing one anyway is how
    the record would become RICHER than the graph, the one direction Interface A
    forbids.

    ⛔ #9836 REVIEW (P2): the consequent DIVERGENCE is asserted here rather than
    left implied, because the code before it is correct and the divergence is the
    thing a reader needs to know: live still MOVES the edge (the transfer is
    unconditional) while BOTH rebuild routes hold nothing. This is the residual
    the docs name (04-projection.md / 08-hosted-prerequisites.md / ONTOLOGY.md),
    and it is #9834's to close (making `:Source` addressable is a FOLD change).
    This leg is a RESIDUAL pin, not a claim of parity: when #9834 lands it must be
    UPDATED to require the descriptor, and it will red until someone does.
    """
    sdk, journal, tmp_path = edge_sdk
    ids = _world(sdk)
    src = sdk.create_source("https://9836.test/doc", sourceKind="document")
    url = src.get("url") or src.get("id")
    assert sdk.create_edge(rel, ids["old"], url)["created"] is True
    sdk.supersede_point(ids["old"], ids["new"])
    assert [r for r in _repoints(journal) if r.get("edge_type") == rel] == []

    # (1) live MOVED it — the skip is the producer's, not live's.
    live = {(row[0], row[1]) for row in sdk._proj.g.query(
        f"MATCH (s)-[r:{rel}]->(t) RETURN s.id, t.url").result_set}
    assert live == {(ids["new"], url)}, (
        f"live = {live} — the supersede transfer is unconditional; if live did NOT "
        "move this edge the residual below is a different (smaller) divergence "
        "and this pin is measuring nothing")
    # (2) …and BOTH rebuild routes lose it. That is the residual, stated.
    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert {(row[0], row[1]) for row in rebuilt.g.query(
            f"MATCH (s)-[r:{rel}]->(t) RETURN s.id, t.url").result_set} == set(), (
            "a `:Source`-targeted edge now SURVIVES the journal rebuild — that is "
            "the #9834 fix landing: update this residual pin (and the docs) to "
            "require the descriptor instead of its absence")
    finally:
        rebuilt.close()


@pytest.mark.parametrize("rel", FAMILY)
def test_the_no_self_edge_guard_JOURNALS_a_delete_only_descriptor(edge_sdk, rel, caplog):
    """⛔ #9836 review (P2): the guard arm must emit for THIS family too.

    When the target IS the successor the 2b leg DELETEs the edge and transfers
    nothing. This file used to assert that no descriptor is emitted here, on the
    reasoning that nothing ever re-creates an ID-ADDRESSED old-side edge, so a
    delete-leg would be a permanent no-op.

    That is true of the GRAPH route and FALSE of the RECORD route. The edge was
    created through `create_edge`, which journals a flat `DirectEdgeCreated` at
    `src=old` — so the record holds a row at the retired point BEFORE the
    supersede. The record applies records in ORDER with no graph state, and the
    only statement that removes a `relation` row at a retired point is a
    descriptor's delete leg (`record_projection` honours `delete_only` for ANY
    rel, `_relation_delete_statement`). With no descriptor the row survived at
    p1 while live had deleted the edge: `live != record-rebuild`, a structural
    edge incident to a superseded point, exactly what #9836 exists to close
    (E2E-11.6).

    Both routes must still hold NO edge afterwards — the delete-only descriptor
    exists to remove the record's row, never to mint anything.

    ⛔ The REPLAY half is the recognition, not a delete: nothing resurrects an
    ID-ADDRESSED old-side edge, so the graph's delete leg is a constrained no-op.
    What the widened `STRUCTURAL_REPOINT_RELS` gate buys there is that the
    descriptor is READ as this shape instead of falling through the
    "malformed delete_only" skip — so the assertion is the absence of that
    warning, which is the one observable difference on this route.
    """
    sdk, journal, tmp_path = edge_sdk
    ids = _world(sdk)
    assert sdk.create_edge(rel, ids["old"], ids["new"])["created"]
    sdk.supersede_point(ids["old"], ids["new"])

    assert _edges(sdk._proj, rel) == set()
    reps = [r for r in _repoints(journal) if r.get("edge_type") == rel]
    assert len(reps) == 1, (
        f"{rel}: the guard arm emitted {reps} — with no delete-only descriptor "
        "the record keeps the `DirectEdgeCreated` row at the superseded point "
        "while live deleted the edge")
    rep = reps[0]
    assert rep.get("delete_only") is True, rep
    assert rep["src"] == ids["old"]
    assert rep["tgt"] == ids["new"]
    # The record's delete keys `(Point, src) -> (Point, tgt)`; the label rides the
    # descriptor the same way the MOVE arm's does, so the two shapes cannot
    # disagree about the family's vocabulary.
    assert rep["target_label"] == "Point"

    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        with caplog.at_level(logging.WARNING):
            rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        skipped = [r.getMessage() for r in caplog.records
                   if "malformed delete_only" in r.getMessage()]
        assert skipped == [], (
            f"{rel}: the replay REFUSED the descriptor as malformed ({skipped}) — "
            "narrowing the delete_only gate to `DERIVABLE_STRUCTURAL_RELS` puts "
            "this family back on that skip while the producer keeps emitting it")
        assert _edges(rebuilt, rel) == set(), (
            "a rebuild minted the no-self-edge phantom (E2E-11.6)")
    finally:
        rebuilt.close()


def test_the_no_self_edge_guard_is_unchanged_for_the_DERIVABLE_family(edge_sdk):
    """Anchor: the widen is scoped, not a blanket.

    `aboutPoint` is snapshot-derivable, so its guard arm emits the delete-only
    descriptor for the ORIGINAL reason (pass-2 re-mints the phantom at old from
    the point's snapshot). Asserting it here means a future narrowing of the
    emission back to one family reds on the other, not silently.
    """
    sdk, journal, _tmp = edge_sdk
    ids = _world(sdk)
    assert sdk._proj.create_about_edge(ids["old"], ids["new"], "aboutPoint")
    sdk.supersede_point(ids["old"], ids["new"])
    reps = [r for r in _repoints(journal)
            if r.get("edge_type") == "aboutPoint"]
    assert len(reps) == 1 and reps[0].get("delete_only") is True, reps
    assert reps[0]["tgt"] == ids["new"]
    assert reps[0]["target_label"] == "Point"


@pytest.mark.parametrize("rel", FAMILY)
def test_a_supersede_CHAIN_lands_the_edge_on_the_terminal_point(edge_sdk, rel):
    """The shared MOVE machinery, exercised through the new addressing.

    One descriptor per hop, each landing at the successor ITS OWN supersede
    named, so the edge ends at the terminal point on both routes exactly as live
    left it — the property `_structural_repoint_statements`' "ONE HOP, not
    `_final(src)`" clause exists for. The family only changes how the TARGET is
    addressed, so this leg asserts the addressing did not disturb the hop rule.
    """
    sdk, journal, tmp_path = edge_sdk
    x = sdk.create_point("claim", "x", pointKind="statement")["id"]
    y = sdk.create_point("claim", "y", pointKind="statement")["id"]
    z = sdk.create_point("claim", "z", pointKind="statement")["id"]
    target = sdk.create_point("claim", "t", pointKind="statement")["id"]
    assert sdk.create_edge(rel, x, target)["created"] is True
    sdk.supersede_point(x, y)
    sdk.supersede_point(y, z)

    live = _edges(sdk._proj, rel)
    assert live == {(z, target)}, live
    srcs = [r["src"] for r in _repoints(journal) if r.get("edge_type") == rel]
    assert srcs == [x, y], (
        f"{rel}: a chain must journal ONE descriptor PER HOP ({srcs}) — "
        "collapsing onto the terminal point is the shape #10139 round 4 fixed")
    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert _edges(rebuilt, rel) == live
    finally:
        rebuilt.close()


@pytest.mark.parametrize("rel", FAMILY)
def test_the_TARGET_is_not_hopped_to_its_own_successor(edge_sdk, rel):
    """⛔ #9836 review (P2): the pass-2b ROUTING gate, given real coverage.

    Routing this family on `STRUCTURAL_REPOINT_RELS` instead of the derivable set
    was load-bearing and UNTESTED: with the gate reverted, this leg rebuilds the
    edge at the target's OWN SUCCESSOR while live holds it at the target.

    Why, exactly — the divergence the gate prevents is the DIRECT leg's hop, NOT a
    label mismatch (an earlier comment claimed the latter, and that claim is
    measurably false: the direct leg's endpoints go through the #8622
    label-agnostic sweep, so an Object- or Event-targeted `wasDerivedFrom`
    resolves there too). The direct leg resolves BOTH endpoints through
    `_final(...)` — the terminal node of the supersede chain — before it MERGEs.
    Live does NOT hop the target: 2b's transfer MERGEs onto the node the edge
    points at. So once the TARGET has itself been superseded, the direct leg
    rebuilds `new -> t2` where live holds `new -> t1`.
    """
    sdk, journal, tmp_path = edge_sdk
    ids = _world(sdk)
    t2 = sdk.create_point("claim", "target successor", pointKind="statement")["id"]
    assert sdk.create_edge(rel, ids["old"], ids["target"])["created"] is True
    # The TARGET retires FIRST, so its successor is exactly the node the direct
    # leg's `_final(tgt)` would hop to if this descriptor reached it.
    sdk.supersede_point(ids["target"], t2)
    sdk.supersede_point(ids["old"], ids["new"])

    live = _edges(sdk._proj, rel)
    assert live == {(ids["new"], ids["target"])}, (
        f"live = {live} — the transfer must land on the target AS IT IS, never on "
        "the target's own successor")
    reps = [r for r in _repoints(journal) if r.get("edge_type") == rel]
    assert len(reps) == 1 and reps[0]["tgt"] == ids["target"], reps

    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert _edges(rebuilt, rel) == live, (
            f"{rel}: journal-rebuild = {sorted(_edges(rebuilt, rel))} but live = "
            f"{sorted(live)} — the descriptor took the DIRECT leg, which hops the "
            "target to `_final(tgt)` and never lands where live landed")
    finally:
        rebuilt.close()


def test_a_rel_the_supersede_does_not_move_is_untouched(edge_sdk):
    """The family is a SUBSET of the transfer set — `dependsOn` is not in it.

    Anchor for the census: this is what "a rel supersede never moves" looks like
    (the edge stays at the old point on BOTH routes), so a fix that widened the
    emission to every structural rel would red here.
    """
    sdk, journal, tmp_path = edge_sdk
    ids = _world(sdk)
    assert sdk.create_edge("dependsOn", ids["old"], ids["target"])["created"]
    sdk.supersede_point(ids["old"], ids["new"])

    live = _edges(sdk._proj, "dependsOn")
    assert live == {(ids["old"], ids["target"])}, live
    assert [r for r in _repoints(journal)
            if r.get("edge_type") == "dependsOn"] == []
    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert _edges(rebuilt, "dependsOn") == live
    finally:
        rebuilt.close()


# ════════════════════════════════════════════════════════════════════════════
# 2. The record half: the descriptor is a MOVE, addressed by id
# ════════════════════════════════════════════════════════════════════════════


def _descriptor(rel="wasDerivedFrom", *, src="p1", tgt="tp-9836",
                target_label="Point", **extra):
    return {"type": "DirectEdgeRepoint", "ts": "T",
            "id": f"{src}->p2:{rel}:{tgt}",
            "src": src, "tgt": tgt, "target_label": target_label,
            "edge_type": rel, **extra}


@pytest.mark.parametrize("rel", FAMILY)
def test_the_record_applies_the_descriptor_as_a_MOVE(rel):
    """DELETE at the superseded point + INSERT at the successor — two halves.

    ONE mutation for the family, shared with the derivable set: the descriptor
    is a MOVE, and the only thing that differs is how the target is addressed.
    A single statement here is the plain-upsert defect #10244 retired (it would
    ADD the successor row and LEAVE the old one on a family whose old row the
    graph has already deleted).
    """
    statements = rp.mutations_for(_descriptor(rel), org_id="o", graph_id="g")
    assert len(statements) == 2, statements
    delete, insert = statements[0].sql, statements[1].sql
    assert "DELETE FROM public.relation" in delete
    assert "INSERT INTO public.relation" in insert
    assert "WITH RECURSIVE succ" in delete and "WITH RECURSIVE succ" in insert
    # The destination is the record's own successor chain — the descriptor
    # carries no successor, and must not start to.
    assert "superseded_by" in delete
    assert "new_id" not in delete + insert
    assert insert.rstrip().endswith("DO NOTHING")
    # ... and the target is resolved by the descriptor's LABEL against the node's
    # own id — the label is BOUND (never interpolated) and it is the value the
    # descriptor carried, so an Object target files as Object. A hardcoded
    # 'Point' would land NO row for an Object target while the graph moved the
    # edge.
    assert "Point" in statements[1].params, statements[1].params
    assert rel in statements[1].params, statements[1].params
    assert "target_label" not in insert, insert


@pytest.mark.parametrize("label,column", [
    ("Point", "node_key"),
    ("Object", "id"),
    ("Subject", "id"),
])
def test_the_target_is_resolved_by_the_labels_own_canonical_key(label, column):
    """The id-addressed arm delegates to `_endpoint_resolution` (one rule).

    `relation.tgt_key` is a composite FK into `node_identity`, so the endpoint
    must be filed by the label's CANONICAL key: `Point`/`Session`/`Event` →
    `node_key`, `Object`/`Subject` → `id` (#9881). A restated resolution here
    would be a second rule for the same fact.
    """
    sql, params = rp._structural_target_resolution(
        "o", "g", label, "wasDerivedFrom", "tp-9836", "tgt", by_id=True)
    assert f"{column} = %s" in sql, sql
    assert params == ["o", "g", "tp-9836"], params
    # The graph's twin match — same label, same id — so one descriptor cannot
    # resolve onto two different nodes.
    assert sql.count("%s") == 3


def test_an_unaddressable_label_is_a_no_move():
    """A label the arm cannot address moves NOTHING — never delete-without-insert.

    The graph's replay leg SKIPS such a descriptor; if the record acted on it,
    the DELETE half would run with no INSERT and the row would leave its endpoint
    and land nowhere (the atomicity the `moved` conjunct exists to protect).
    Returning `None` sends the descriptor back to the plain-upsert path, which is
    the same no-move the fold performs.
    """
    doc = _descriptor("wasDerivedFrom", tgt="https://x.test/d",
                      target_label="Source")
    assert rp._structural_repoint_statements("o", "g", doc) is None


def test_a_label_outside_the_addressable_set_is_a_REFUSAL_not_a_guess():
    """Reaching the resolver with a bad label fails LOUD (the vocabulary pin).

    The caller validates the label too, so this can only be reached by a
    vocabulary change that did not reach the arm; a silent fallback would file
    the row under a label the fold never resolved.
    """
    with pytest.raises(rp.RecordProjectionError):
        rp._structural_target_resolution(
            "o", "g", "DropTable", "wasDerivedFrom", "x", "", by_id=True)


# ════════════════════════════════════════════════════════════════════════════
# 3. The contracts: one vocabulary, mirrored and pinned (no drift either way)
# ════════════════════════════════════════════════════════════════════════════


def test_the_writer_still_transfers_exactly_the_families_we_claim():
    """`SUPERSEDE_STRUCTURAL_RELS` ⊇ the family, and the replay set is the split.

    A rel added to the writer's transfer set without being classified into one
    of the two families is the #9836 defect repeating one rel over.
    """
    assert set(FAMILY) <= set(SUPERSEDE_STRUCTURAL_RELS)
    assert set(FAMILY) == set(ID_ADDRESSED_STRUCTURAL_RELS)
    assert not (ID_ADDRESSED_STRUCTURAL_RELS & DERIVABLE_STRUCTURAL_RELS), (
        "a rel cannot be snapshot-derivable AND id-addressed: the derivable set "
        "is the resurrection set, and the replay's delete-leg reasons about it")
    assert STRUCTURAL_REPOINT_RELS == (
        DERIVABLE_STRUCTURAL_RELS | ID_ADDRESSED_STRUCTURAL_RELS)


def test_the_record_mirrors_the_folds_own_rel_set_and_labels():
    """Both directions, so neither side can drift silently.

    `record_projection` restates these constants for the STANDALONE reason every
    other constant in that block is (the PGlite harness loads the module without
    the package), so the pin is what keeps the restatement honest.
    """
    assert rp._ID_ADDRESSED_STRUCTURAL_RELS == ID_ADDRESSED_STRUCTURAL_RELS
    assert rp._ID_ADDRESSED_TARGET_LABELS == RECORD_ADDRESSABLE_EDGE_LABELS


def test_the_label_order_is_the_folds_sweep_order():
    """Point-first, and the SAME tuple the fold sweeps.

    A multi-labelled node must resolve to one label on the write, the transfer
    and both replay routes; two orders would let them disagree.
    """
    from tortoise.live import EPISTEMIC_LABELS

    assert EPISTEMIC_LABEL_ORDER == EPISTEMIC_LABELS
    assert set(EPISTEMIC_LABEL_ORDER) == RECORD_ADDRESSABLE_EDGE_LABELS


def test_the_label_order_is_the_WRITE_resolvers_order_too():
    """⛔ The emitted `target_label` must be the label the MINT already used.

    A node carrying more than one epistemic label is resolved by the FIRST
    branch that matches (`_resolve_entity`'s `LIMIT 1` UNION, one row per
    branch, deduped by identity), so the class-4 mint journals the edge with
    THAT branch's label — while the 2b repoint writes its own. If the two orders
    disagreed, the repoint's DELETE (keyed on the descriptor's `tgt_label`, part
    of `relation`'s primary key) would miss the row the creation landed and the
    INSERT would add a SECOND row at the successor: one edge, two record rows,
    and a `live != record-rebuild` divergence reachable only through this order.
    Pinned here because nothing else in the tree ties the two tuples together.
    """
    from tortoise.live import EPISTEMIC_LABELS
    from tortoise.projection import FalkorProjection

    # The resolver's epistemic branches, in ITS order, deduped (Source appears
    # twice but is not an epistemic label; nothing else repeats).
    resolved = [label for label, _prop in FalkorProjection._RESOLVE_BRANCHES
                if label in EPISTEMIC_LABELS]
    assert tuple(dict.fromkeys(resolved)) == EPISTEMIC_LABEL_ORDER, (
        f"the write resolver's label order {tuple(dict.fromkeys(resolved))} is "
        f"not the repoint's {EPISTEMIC_LABEL_ORDER} — a multi-labelled node "
        "would be journalled under one label and repointed under another")


def test_id_addressed_target_picks_the_labels_own_key():
    """`id_addressed_target` picks the label and the key THAT LABEL is filed by.

    Measured directly, because a label/key mismatch is the one failure mode this
    family can have that still lands a row: `#9972`-with-a-different-spelling.
    """
    subject = {"id": "sub-1", "name": "Alice"}
    assert id_addressed_target(["Subject"], subject) == ("Subject", "sub-1")
    assert id_addressed_target(["Point", "Event"], {"id": "x"}) == ("Point", "x")
    # ⛔ #9836 review (P2): an Event is keyed by its OWN `eventId`, never by `id`.
    # The meeting guard keeps `id = <candidate>` on the SUFFIXED Event too, so the
    # `id` names two nodes while `eventId` names one.
    guarded = {"id": "cand", "eventId": "cand-4a94ebe5"}
    assert id_addressed_target(["Event"], guarded) == ("Event", "cand-4a94ebe5")
    # An Event the journal never gave an `eventId` is unaddressable — SKIP, never
    # a descriptor keyed on the property that is ambiguous.
    assert id_addressed_target(["Event"], {"id": "cand"}) is None
    # A node the record cannot address → no descriptor at all.
    assert id_addressed_target(["Source"], {"url": "u"}) is None
    assert id_addressed_target(["Point"], {}) is None
    assert id_addressed_target([], {"id": "x"}) is None


def test_the_identity_property_is_the_labels_CANONICAL_one():
    """⛔ The key must be the identity BOTH routes already agree on.

    `ID_ADDRESSED_TARGET_ID_PROPS` decides two things at once — the property the
    graph's replay matches and the value the record resolves — so a row that
    disagreed with the tree's canonical identity tables would put one descriptor
    on two different nodes. Pinned to the two existing authorities rather than
    restated from memory: the fold's `_CANONICAL_ENTITY_ID_PROPS` (Point/Subject/
    Object → `id`, Event → `eventId`) and the record's own event key
    (`NODE_TABLES['event']` keys on `event_id`, which `node_key` mirrors under
    `chk_event_node_key_is_contract_key`, and `_ENTITY_MATCH_COLUMNS['event']`
    resolves by `node_key`).
    """
    from tortoise.projection import _CANONICAL_ENTITY_ID_PROPS

    canonical = dict(_CANONICAL_ENTITY_ID_PROPS)
    assert set(ID_ADDRESSED_TARGET_ID_PROPS) == set(RECORD_ADDRESSABLE_EDGE_LABELS)
    for label, prop in ID_ADDRESSED_TARGET_ID_PROPS.items():
        assert prop == canonical[label], (
            f"{label} is addressed by {prop!r} here and by {canonical[label]!r} in "
            "the fold's own identity table — one of the two replay routes would "
            "resolve a different node")
    # The record half of the same fact, for the one label where it is not `id`.
    assert ID_ADDRESSED_TARGET_ID_PROPS["Event"] == "eventId"
    assert rp.NODE_TABLES["event"] == ("Event", "event_id")
    assert rp._ENTITY_MATCH_COLUMNS["event"] == ("node_key",)


# ════════════════════════════════════════════════════════════════════════════
# 4. The `Event` target: its identity is `eventId`, and the tree can mint TWO
#    Events that share one `id` (review finding 3)
# ════════════════════════════════════════════════════════════════════════════


def _guarded_meeting_events(sdk, candidate: str, files) -> list[str]:
    """Build the shape with the tree's OWN producer, not a hand-written fixture.

    `entities._event_guarded_merge` is the only live writer that can give two
    distinct Events the SAME `id`: statement (1) MERGEs `{eventId: candidate}` and
    writes `id = candidate` into the props, and the SUFFIX branch (3) MERGEs
    `{eventId: <candidate>-<sha8>}` while those props still carry `id =
    candidate`. `_upsert_event(..., guard=True, guard_source_file=…)` is exactly
    how the meeting ingest path reaches it, so driving it keeps this fixture
    honest — a raw `MERGE` here would be this test inventing the shape it then
    asserts about.

    Returns the resolved `eventId`s, read back from the graph.
    """
    for sf in files:
        sdk._proj._upsert_event(
            {
                "eventId": candidate,
                "eventKind": "meeting",
                "source_file": sf,
                "name": f"meeting {sf}",
                "startedAt": "2026-10-25T03:00:00Z",
            },
            guard=True,
            guard_source_file=sf,
        )
    return sorted(
        row[0] for row in sdk._proj.g.query(
            "MATCH (e:Event) RETURN e.eventId").result_set)


def test_the_guarded_meeting_shape_really_does_share_one_id(edge_sdk):
    """The premise the old comment denied, measured — and what it broke.

    The review's finding 3: the id arm's refusal was justified as "a live
    producer cannot create that shape for these rels". It can, in this tree, and
    the consequence was not the refusal alone — the record files an Event under
    `node_key` (= `event_id` = `eventId`), so keying the descriptor on `id` also
    pointed the two routes at DIFFERENT Events. This asserts the shape exists and
    that keying on `eventId` names one node per descriptor.
    """
    sdk, _journal, _tmp = edge_sdk
    _world(sdk)  # materialises the projection
    ids = _guarded_meeting_events(sdk, "evt-cand-9836",
                                 ["/caps/a.jsonl", "/caps/b.jsonl"])
    assert len(ids) == 2, ids
    assert len(set(ids)) == 2
    shared = sdk._proj.g.query(
        "MATCH (e:Event) RETURN e.id, e.eventId").result_set
    assert {row[0] for row in shared} == {"evt-cand-9836"}, (
        "the guarded shape must give BOTH Events the same `id` — otherwise this "
        f"leg is not measuring the shape it claims to ({shared})")
    # `id` names two nodes: that is the ambiguity the old blanket match hit.
    assert len(sdk._proj.g.query(
        "MATCH (x:Event {id:$i}) RETURN ID(x)",
        params={"i": "evt-cand-9836"}).result_set) == 2
    # `eventId` names ONE per descriptor, and each resolves to its own node — the
    # fix, measured on the graph the live writer produced.
    internals = set()
    for eid in ids:
        resolved = resolve_structural_target(
            sdk._proj.g, "Event", eid, "wasDerivedFrom", by_id=True)
        assert resolved is not None, (
            f"{eid}: the id-addressed arm resolved NOTHING — keyed on `id` this "
            "is the ambiguity refusal, which drops the descriptor while live has "
            "already moved the edge")
        assert resolved["logical"] == eid
        internals.add(resolved["internal"])
    assert len(internals) == 2, internals


def test_an_event_target_keeps_its_OWN_node_across_the_rebuild(edge_sdk):
    """`live == journal-rebuild` when the target is an Event of that shape.

    Both Events are targets of one `wasDerivedFrom` edge; the transfer is
    expected to move BOTH edges to the successor and the journal to carry one
    descriptor per target, keyed by that target's own `eventId`.

    ⚠️ SCOPE, stated: the journal carries the two `EventRecorded` records with the
    RESOLVED ids (which is what the meeting path emits), so the REBUILT Events
    carry `id == eventId` — the guarded live shape (`id` fixed at the candidate)
    is a live-only artefact of the suffix MERGE and is not reproduced by a
    rebuild of the plain arm. The comparison below is therefore
    `(successor, target eventId)`, which is the identity this family is about;
    comparing the Events' `id` property would be measuring that pre-existing,
    unrelated divergence instead.
    """
    sdk, journal, tmp_path = edge_sdk
    ids = _world(sdk)
    resolved = _guarded_meeting_events(sdk, "evt-cand-9836",
                                      ["/caps/a.jsonl", "/caps/b.jsonl"])
    assert len(resolved) == 2, resolved
    for eid in resolved:
        # The meeting ingest path journals the RESOLVED id, so the rebuild has
        # both Events and the descriptors have something to resolve.
        sdk._emit_event("EventRecorded", id=eid, eventId=eid,
                        eventKind="meeting", name=f"meeting {eid}")
        assert sdk.create_edge("wasDerivedFrom", ids["old"], eid)["created"]

    def by_event(p):
        return {(row[0], row[1]) for row in p.g.query(
            "MATCH (s)-[r:wasDerivedFrom]->(t) RETURN s.id, t.eventId").result_set}

    before = by_event(sdk._proj)
    assert before == {(ids["old"], eid) for eid in resolved}, before

    sdk.supersede_point(ids["old"], ids["new"])
    live = by_event(sdk._proj)
    assert live == {(ids["new"], eid) for eid in resolved}, (
        f"live = {sorted(live)} — the transfer must move BOTH edges, one per "
        "Event target")

    reps = [r for r in _repoints(journal) if r.get("edge_type") == "wasDerivedFrom"]
    assert sorted(r["tgt"] for r in reps) == resolved, (
        f"the descriptors name {[r['tgt'] for r in reps]} but the targets are "
        f"{resolved} — keyed on `id` BOTH descriptors would name the candidate and "
        "one of the two Events would lose its edge at rebuild")
    assert {r["target_label"] for r in reps} == {"Event"}

    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert by_event(rebuilt) == live, (
            "the journal rebuild did not reproduce the move for EVERY target "
            "Event — the id-addressed arm resolved fewer nodes than live moved")
    finally:
        rebuilt.close()
