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

So the target is addressed the way every other endpoint in this tree is: by its
OWN `id` for an epistemic node, and by its `url` for a `:Source`
(`ID_ADDRESSED_TARGET_ADDRESSING`), with the label the descriptor OBSERVED live
riding `target_label` (the field's own meaning on the structural route). Both
routes resolve `(label, key)` — the graph through `resolve_structural_target(...
by_id=True | by_url=True)`, the record by delegating to `_endpoint_resolution`,
the same resolution every other edge endpoint uses. ⚠️ The url arm is not
optional: "not addressable by `id`" is NOT "not addressable", and declining a
`:Source` target left the edge moved live and present on neither rebuild.

THE ACCEPTANCE BAR is `live == journal-rebuild == record-rebuild`, so this module
measures the LIVE and JOURNAL halves end to end (embedded graph, real journal,
real `rebuild_all`) and pins the RECORD half where it can be pinned without a
database: the PLAN the record emits for a descriptor. The row-count facts are
measured against real Postgres by `supabase/tests/pglite/validate.mjs` cases
13-17 (moves to the successor, filed under the descriptor's label — Point,
Object and **Source by its url**; the no-self-edge `delete_only` DELETE; and an
unknown label is a NO-MOVE, never delete-without-insert) — a pytest leg cannot
assert a row count, and a plan-text assertion cannot see one.
"""
from __future__ import annotations

import json

import pytest

from tortoise import record_projection as rp
from tortoise.projection import FalkorProjection
from tortoise.projection.edges import (
    DERIVABLE_STRUCTURAL_RELS,
    EPISTEMIC_LABEL_ORDER,
    ID_ADDRESSED_STRUCTURAL_RELS,
    ID_ADDRESSED_TARGET_ADDRESSING,
    RECORD_ADDRESSABLE_EDGE_LABELS,
    STRUCTURAL_REPOINT_RELS,
    id_addressed_target,
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
def test_a_SOURCE_targeted_edge_MOVES_and_both_routes_agree(edge_sdk, rel):
    """A `:Source` target is ADDRESSED — by its `url` — not "unaddressable".

    The P1 this pins (review, measured): `create_edge(rel, point, <source url>)`
    type-checks nothing, so the shape is producible; the emission then read "not
    addressable by `id`" as "not addressable" and declined the descriptor, so
    `supersede_point` moved the edge live and NOTHING journalled it. Before this
    leg, the census read:

        aboutAction    -> Source : LIVE [(new, url)]  JREBUILD []   AGREE False
        wasDerivedFrom -> Source : LIVE [(new, url)]  JREBUILD []   AGREE False

    Both routes DO address a `:Source`: the graph through
    `resolve_structural_target(..., by_url=True)` (an exact
    `MATCH (s:Source {url:$url})`), the record through `node_key`, which IS a
    Source's url — the same identity `extractedFrom`/`aboutDocument` already
    resolve by. So the descriptor carries `(target_label='Source', tgt=url)` and
    the rebuild agrees with live.
    """
    sdk, journal, tmp_path = edge_sdk
    ids = _world(sdk)
    url = sdk.create_source("https://9836.test/doc",
                            sourceKind="document").get("url")
    assert url, "the Source must carry the url the edge is keyed by"
    assert sdk.create_edge(rel, ids["old"], url)["created"] is True
    sdk.supersede_point(ids["old"], ids["new"])

    live = _edges(sdk._proj, rel)
    assert live == {(ids["new"], url)}, live
    reps = [r for r in _repoints(journal) if r.get("edge_type") == rel]
    assert len(reps) == 1, (
        f"{rel}: a `:Source`-targeted edge moved live ({live}) and journalled "
        f"{len(reps)} repoints — a Source is addressable by its url, so the "
        "emission must journal the move, not decline it")
    assert reps[0]["target_label"] == "Source", reps[0]
    assert reps[0]["tgt"] == url, reps[0]

    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert _edges(rebuilt, rel) == live, (
            f"{rel} -> Source: the journal rebuild disagrees with the live graph "
            "— the url-keyed descriptor is what carries the move")
    finally:
        rebuilt.close()


def test_an_unaddressable_target_is_not_half_journaled():
    """A target with NO addressable label and no url is NOT journalled.

    `id_addressed_target` returning ``None`` is what makes the emission SKIP such
    a target instead of filing a descriptor the record cannot apply (a record
    RICHER than the graph, the one direction Interface A forbids). ⚠️ Since the
    url arm landed this is DEFENSIVE rather than reachable: every label
    `create_edge` can resolve a target to is in
    `ID_ADDRESSED_TARGET_ADDRESSING` (`_RESOLVE_BRANCHES` = Point, Subject,
    Object, Event, Source) — so the guard is what keeps a future resolvable label
    from silently becoming an un-replayable descriptor.
    """
    assert id_addressed_target(["Tag"], {"id": "x"}) is None
    assert id_addressed_target(["Source"], {"id": "no-url-here"}) is None
    assert set(ID_ADDRESSED_TARGET_ADDRESSING) >= RECORD_ADDRESSABLE_EDGE_LABELS


def test_the_no_self_edge_guard_JOURNALS_the_delete_for_this_family(edge_sdk):
    """The transfer's existing guard, and the DELETE the RECORD needs from it.

    When the target IS the successor the leg DELETES the edge and transfers
    nothing. Live and journal-rebuild both hold NO edge (nothing re-creates one
    at the old point for this family), but the RECORD does not: its
    `DirectEdgeCreated` fold already wrote a relation row at the OLD point, and
    the record has no superseded-endpoint skip of its own — measured on real
    Postgres, `{src_key:'p1', tgt_key:'p2'}` survived while both graph routes
    held no edge (review P2). A `delete_only` descriptor is exactly that missing
    DELETE, and the record already applies it (`_relation_delete_statement`), so
    the emission carries one for this family too.
    """
    sdk, journal, tmp_path = edge_sdk
    ids = _world(sdk)
    assert sdk.create_edge("wasDerivedFrom", ids["old"], ids["new"])["created"]
    sdk.supersede_point(ids["old"], ids["new"])

    assert _edges(sdk._proj, "wasDerivedFrom") == set()
    reps = [r for r in _repoints(journal)
            if r.get("edge_type") == "wasDerivedFrom"]
    assert len(reps) == 1, reps
    assert reps[0]["delete_only"] is True, reps[0]
    assert (reps[0]["src"], reps[0]["tgt"]) == (ids["old"], ids["new"])
    # The target here IS the successor POINT, which is what makes the record's
    # `tgt_label = 'Point'` delete match the row the creation fold wrote.
    assert reps[0]["target_label"] == "Point", reps[0]

    # The record's half of the same fact: ONE statement, and it is the DELETE.
    statements = rp.mutations_for(reps[0], org_id="o", graph_id="g")
    assert len(statements) == 1, statements
    assert statements[0].sql.startswith("DELETE FROM public.relation"), \
        statements[0].sql
    assert "INSERT" not in statements[0].sql

    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert _edges(rebuilt, "wasDerivedFrom") == set(), (
            "a rebuild minted the no-self-edge phantom (E2E-11.6)")
    finally:
        rebuilt.close()


@pytest.mark.parametrize("rel", FAMILY)
def test_a_target_that_was_itself_superseded_is_NOT_followed(edge_sdk, rel):
    """The routing change is load-bearing: a target is moved ONCE, by ITS supersede.

    `p1 -[rel]-> t1`, then `supersede(t1 -> t2)`, then `supersede(p1 -> p2)`. The
    second supersede moves p1's OUTGOING side only — the edge was never p1's to
    retarget on the far end — so LIVE holds `p2 -> t1`, and t1 has meanwhile been
    superseded (its own outgoing edges moved; incoming ones never do).

    ⛔ WHY THIS PINS THE ROUTING (#9836 review P2): if a 2b structural descriptor
    were routed to the DIRECT-leg consumer, that leg resolves its `tgt` through
    `_final` — a ONE-HOP SUPERSEDE REDIRECT — and would rebuild `p2 -> t2`. That
    is the divergence the `STRUCTURAL_REPOINT_RELS` gate prevents, and it is NOT
    the "an Object target would be matched against `(b:Point {id:…})`" reason an
    earlier comment gave: the direct leg resolves endpoints through the #8622
    label sweep, so an Object target survives it. Reverting the gate to
    `DERIVABLE_STRUCTURAL_RELS` reds THIS test and no other.
    """
    sdk, journal, tmp_path = edge_sdk
    p1 = sdk.create_point("claim", "p1", pointKind="statement")["id"]
    p2 = sdk.create_point("claim", "p2", pointKind="statement")["id"]
    t1 = sdk.create_point("claim", "t1", pointKind="statement")["id"]
    t2 = sdk.create_point("claim", "t2", pointKind="statement")["id"]
    assert sdk.create_edge(rel, p1, t1)["created"] is True
    sdk.supersede_point(t1, t2)
    sdk.supersede_point(p1, p2)

    live = _edges(sdk._proj, rel)
    assert live == {(p2, t1)}, (
        f"live: a supersede moves the SOURCE side of an outgoing edge — the "
        f"target keeps its own identity ({live})")
    reps = [r for r in _repoints(journal) if r.get("edge_type") == rel]
    assert [(r["src"], r["tgt"]) for r in reps] == [(p1, t1)], reps

    rebuilt = FalkorProjection(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt.rebuild_all(str(tmp_path), confirm_destructive=True)
        assert _edges(rebuilt, rel) == live, (
            f"{rel}: the rebuild followed the TARGET's supersede chain "
            f"({_edges(rebuilt, rel)}) where live did not ({live}) — the "
            "descriptor was resolved through `_final` instead of at the target "
            "the transfer actually kept")
    finally:
        rebuilt.close()


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
    ("Source", "node_key"),
])
def test_the_target_is_resolved_by_the_labels_own_canonical_key(label, column):
    """The id-addressed arm delegates to `_endpoint_resolution` (one rule).

    `relation.tgt_key` is a composite FK into `node_identity`, so the endpoint
    must be filed by the label's CANONICAL key: `Point`/`Session`/`Event` →
    `node_key`, `Object`/`Subject` → `id` (#9881), `Source` → `node_key` (= its
    `url`). A restated resolution here would be a second rule for the same fact.
    ⛔ The Source case is the url arm: `node_key` ALONE, never the label's default
    `id OR node_key` union — a descriptor keyed by a url must not be able to
    resolve onto some Source's `id` (the `_endpoint_resolution` docstring's own
    extractedFrom hazard).
    """
    sql, params = rp._structural_target_resolution(
        "o", "g", label, "wasDerivedFrom", "tp-9836", "tgt", by_id=True)
    assert f"{column} = %s" in sql, sql
    assert params == ["o", "g", "tp-9836"], params
    # The graph's twin match — same label, same key — so one descriptor cannot
    # resolve onto two different nodes.
    assert sql.count("%s") == 3
    if label == "Source":
        # ONE key predicate, and it is `node_key` (org + graph + key = 3 binds).
        # The label's DEFAULT match is `id = %s OR node_key = %s` — 4 binds and an
        # `OR` — which would let a url-keyed descriptor resolve onto some
        # Source's `id` row.
        assert sql.count(" = %s") == 3, sql
        assert " OR " not in sql, sql
        assert "node_key = %s" in sql, sql


def test_an_unaddressable_label_is_a_no_move():
    """A label the arm cannot address moves NOTHING — never delete-without-insert.

    The graph's replay leg SKIPS such a descriptor; if the record acted on it,
    the DELETE half would run with no INSERT and the row would leave its endpoint
    and land nowhere (the atomicity the `moved` conjunct exists to protect).
    Returning `None` sends the descriptor back to the plain-upsert path, which is
    the same no-move the fold performs.
    """
    doc = _descriptor("wasDerivedFrom", tgt="https://x.test/d",
                      target_label="Tag")
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
    assert rp._ID_ADDRESSED_TARGET_ADDRESSING == ID_ADDRESSED_TARGET_ADDRESSING
    # The fold's id SWEEP is still the four epistemic labels — a `:Source` is
    # addressed on the repoint route by its url, not by the `DirectEdgeCreated`
    # sweep, so widening the repoint vocabulary must not widen this set.
    assert frozenset(
        {"Point", "Subject", "Object", "Event"}) == RECORD_ADDRESSABLE_EDGE_LABELS
    assert set(ID_ADDRESSED_TARGET_ADDRESSING) == (
        RECORD_ADDRESSABLE_EDGE_LABELS | {"Source"})


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
    # A `:Source` is addressed by its url — NOT declined (review P1: "not
    # addressable by id" is not "not addressable").
    assert id_addressed_target(["Source"], {"url": "u"}) == ("Source", "u")
    # An epistemic label wins over a co-resident Source label, in the fold's own
    # order — the same first-wins rule the 2a-DIRECT transfer applies.
    assert id_addressed_target(["Source", "Point"],
                               {"id": "x", "url": "u"}) == ("Point", "x")
    assert id_addressed_target(["Point"], {}) is None
    assert id_addressed_target([], {"id": "x"}) is None
